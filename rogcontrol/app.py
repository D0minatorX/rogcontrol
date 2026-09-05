"""The GTK4/libadwaita application: window, navigation and the apply plumbing.

This is the shell. It owns exactly three things every page depends on and
nothing else:

* ``window.config`` / ``window.caps`` -- the loaded config and the probed
  machine capabilities, read once and shared, so a page never re-reads the
  config file behind another page's back.
* ``window.toast(text)`` -- the single place anything reports success or
  failure. Pages never own a status label.
* ``window.apply_async(fn, on_done)`` -- every hardware call goes through
  here. ``run_helper`` shells out to sudo with a ten second timeout and
  nvidia-smi takes a couple of hundred milliseconds; either on the main loop
  freezes the window, and a frozen window during a fan-curve apply is what
  made the old version feel broken.

The application id must match the desktop entry's filename
(``org.rogcontrol.RogControl.desktop``). GNOME matches a window to its
launcher by application id, and when the two disagree the icon in the
applications grid starts the app but never attaches to the window it
opened -- which looks exactly like clicking the icon doing nothing at all.
"""

import concurrent.futures
import json
import os
import sys
import threading
import time
import traceback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import config as config_mod  # noqa: E402
from . import fancurve  # noqa: E402
from . import hardware  # noqa: E402
from .pages.battery import BatteryPage  # noqa: E402
from .pages.cpu import CpuPage  # noqa: E402
from .pages.fans import CHANNEL_GAP_S, FansPage  # noqa: E402
from .pages.gpu import GpuPage  # noqa: E402
from .pages.keyboard import KeyboardPage  # noqa: E402
from .pages.overview import OverviewPage  # noqa: E402
from .pages.system import SystemPage, UPDATE_AUTO_INTERVALS_S  # noqa: E402
from .widgets.ambient import ambient_available  # noqa: E402

APP_ID = "org.rogcontrol.RogControl"

# What to call each CPU apply step when one of them fails. Keyed by the step
# names in hardware.CPU_APPLY_STEPS.
STEP_LABELS = {"limits": "CPU limits", "boost": "CPU boost",
               "epp": "CPU energy preference", "clock": "CPU clock cap"}

# The smallest window this layout is meant to be usable at -- roughly a phone
# in portrait. Nothing here is allowed to demand more than this: a page that
# does shows up as a window that refuses to be dragged narrower, which is the
# GTK3 version's defining bug and the reason this shell exists.
MIN_WIDTH, MIN_HEIGHT = 360, 360

# (id, sidebar label, icon). Order is the sidebar order.
PAGE_SPECS = (
    ("overview", "Overview", "speedometer-symbolic"),
    ("cpu", "CPU", "computer-chip-symbolic"),
    ("gpu", "GPU", "video-display-symbolic"),
    ("fans", "Fans", "weather-windy-symbolic"),
    ("battery", "Battery", "battery-good-symbolic"),
    ("keyboard", "Keyboard", "keyboard-brightness-symbolic"),
    ("system", "System", "emblem-system-symbolic"),
)


class MainWindow(Adw.ApplicationWindow):
    """Split-view window: sidebar of pages, content pane per page.

    NavigationSplitView rather than the old notebook because it collapses to
    a single pane on a narrow window, which is what finally lets this window
    be resized freely -- the GTK3 version fought its own minimum width."""

    def __init__(self, app, config, caps, hardware_report_path=None):
        super().__init__(application=app)
        self.config = config
        self.caps = caps
        # Set by the caller when the application's first-launch check just
        # wrote one (see RogControlApp._ensure_window), before this window's
        # pages are built below, so the CPU page's own-vendor notice can name
        # the path. None means "nothing written this run" -- an existing
        # report from a previous launch is not re-announced, since the CPU
        # page's notice already tells the user --hardware-report exists.
        self.hardware_report_path = hardware_report_path
        # Set while widgets are being populated from the config, so the
        # handlers that apply settings can tell "the user moved this" from
        # "we just loaded a profile into it". Without it, building the window
        # would fire an apply for every control on screen.
        self._loading = True

        # The sidebar icons are looked up by name, and the system icon theme
        # is not guaranteed to have all seven -- Adwaita is missing
        # speedometer-symbolic and computer-chip-symbolic outright, and a
        # fresh install may not even have Adwaita active (KDE ships Breeze).
        # Bundling them here and adding this as a search path means the
        # sidebar looks the same regardless of what theme the desktop is
        # running, with the system theme still preferred if it has its own.
        icon_theme = Gtk.IconTheme.get_for_display(self.get_display())
        icons_dir = os.path.join(os.path.dirname(__file__), "icons")
        icon_theme.add_search_path(icons_dir)

        self.set_title("ROG Control")
        width, height = 960, 720
        saved = config.get("window_size")
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            try:
                # Floored at the layout's real minimum (MIN_WIDTH/MIN_HEIGHT,
                # enforced again just below by set_size_request either way),
                # not some larger number picked to avoid restoring the
                # occasional narrow size a GTK3 config could have. That
                # second floor did not distinguish "an old GTK3 leftover"
                # from "the user deliberately narrowed this GTK4 window and
                # saved it" -- it silently discarded both back up to the same
                # fixed width, which looked exactly like the size never
                # having saved at all.
                width = max(MIN_WIDTH, int(saved[0]))
                height = max(MIN_HEIGHT, int(saved[1]))
            except (TypeError, ValueError):
                pass
        self.set_default_size(width, height)
        # How narrow the window is *allowed* to be, as opposed to how narrow
        # it opens. Everything inside is built to reflow rather than scroll
        # sideways -- subtitles wrap, sliders shrink, the sidebar folds away
        # -- so there is no reason to stop the user at a phone-sized window.
        # GTK takes the larger of this and what the content genuinely needs,
        # so it is a floor and not a promise; see MIN_WIDTH below.
        self.set_size_request(MIN_WIDTH, MIN_HEIGHT)

        # Which slow hardware write owns the machine right now, or None. The
        # fan portion of a profile apply alone takes ~10 seconds (see
        # CHANNEL_GAP_S), so the window has to be able to say "still working"
        # rather than start a second, overlapping write. See claim_hardware.
        self._hw_owner = None
        # A page reload asked for while a write was in flight, run once it
        # ends. See reload_pages.
        self._reload_after_apply = False
        # One long-lived worker instead of a fresh threading.Thread per
        # apply_async call: every page's tick (2-5s, for as long as the
        # window is open) went through apply_async, so that was an OS
        # thread spun up and torn down forever. max_workers=1 matches every
        # caller's own busy-flag gating -- none of them issues a second
        # apply_async before the first has returned.
        self._worker_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1)

        self.pages = {}
        self._build_ui()
        self._loading = False

        # The config file has five writers; this window is one of them. See
        # check_external_config_change.
        self._last_config_mtime = config_mod.config_mtime()
        self._config_watch = GLib.timeout_add_seconds(
            config_mod.CONFIG_POLL_SECONDS, self.check_external_config_change)
        self.connect("destroy", self._on_destroy)
        self.connect("close-request", self._on_close_request)

    # -- construction --------------------------------------------------------

    def _build_ui(self):
        self.split = Adw.NavigationSplitView()
        self.split.set_min_sidebar_width(180)
        self.split.set_max_sidebar_width(240)
        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())
        self.set_content(self.split)

        # Below this width the two panes stop fitting side by side, so the
        # sidebar folds away behind the back button instead of squeezing the
        # content pane down to nothing.
        breakpoint_ = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 620sp"))
        breakpoint_.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint_)

    def _build_sidebar(self):
        self.sidebar_list = Gtk.ListBox()
        self.sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        # The style class is what gives the rows the flat, full-width look of
        # a sidebar rather than the boxed list look of a preferences page.
        self.sidebar_list.add_css_class("navigation-sidebar")

        for page_id, label, icon in PAGE_SPECS:
            row = Gtk.ListBoxRow()
            row.page_id = page_id
            row.page_label = label
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            box.append(Gtk.Image.new_from_icon_name(icon))
            box.append(Gtk.Label(label=label, xalign=0))
            row.set_child(box)
            self.sidebar_list.append(row)

        self.sidebar_list.connect("row-selected", self._on_page_selected)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self.sidebar_list)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(scroller)
        return Adw.NavigationPage(title="ROG Control", child=toolbar)

    def _build_content(self):
        self.content_title = Adw.WindowTitle(title="Overview", subtitle="")
        header = Adw.HeaderBar()
        header.set_title_widget(self.content_title)
        # Packed menu-first, so the menu button sits at the very end and the
        # profile drop-down to its left -- the order every GNOME app uses.
        header.pack_end(self._build_profile_menu())
        header.pack_end(self._build_profile_switcher())

        # The visible page's own actions -- Apply/Revert, or Calibrate/Apply
        # on the fans page -- sit at the start of the header, beside the page
        # title. Every page's box is packed once here and all but the current
        # one hidden, rather than re-parenting buttons on each navigation: a
        # widget has one parent, and moving it around on every page change is
        # a good way to end up with the CPU page's Apply button on the GPU
        # page. See widgets/action_buttons.py.
        self.page_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                    spacing=6)
        header.pack_start(self.page_actions)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        for page_id, label, _icon in PAGE_SPECS:
            page = self._build_page(page_id, label)
            self.pages[page_id] = page
            self.stack.add_named(page, page_id)
            # Pages that have header actions say so with an action_box; the
            # read-only ones (Overview, System) simply do not have one.
            box = getattr(page, "action_box", None)
            if box is not None:
                box.set_visible(False)
                self.page_actions.append(box)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.stack)

        # Applying a profile takes about twenty seconds, most of it spent
        # waiting between fan channels, and it happens on whichever page the
        # user is looking at. A toast would be gone long before the work is,
        # so the progress lives in a banner under the header where it stays
        # put until the apply really ends.
        self.apply_banner = Adw.Banner()
        self.apply_banner.set_revealed(False)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self.apply_banner)
        toolbar.set_content(self.toast_overlay)
        return Adw.NavigationPage(title="ROG Control", child=toolbar)

    def _build_page(self, page_id, _label):
        """Every id in PAGE_SPECS has a page; there is no placeholder tier.

        A KeyError here is the right failure: a sidebar entry with nothing
        behind it is a bug, and one that shows an apologetic empty page
        instead of raising is a bug that ships."""
        builders = {"overview": OverviewPage, "cpu": CpuPage, "gpu": GpuPage,
                    "fans": FansPage, "battery": BatteryPage,
                    "keyboard": KeyboardPage, "system": SystemPage}
        return builders[page_id](self)

    def _build_profile_switcher(self):
        """The profile list, in the header where it applies to every page."""
        self.profile_names = list(self.config.get("profiles", {}).keys())
        self.profile_drop = Gtk.DropDown.new_from_strings(
            self.profile_names or ["(no profiles)"])
        self.profile_drop.set_tooltip_text("Active profile")
        # A drop-down is as wide as the name it is showing, and profile names
        # are the user's to invent. Left alone, "Balanced Performance" sets
        # the window's minimum width from inside the header bar, where no
        # amount of reflowing below can help.
        self.profile_drop.set_factory(self._ellipsizing_factory())
        # The popup is a separate factory, and it must not ellipsize: the
        # button can afford to shorten a name because the tooltip and the
        # popup both still name it in full, but a list where "Balanced Power"
        # and "Balanced Performance" both read "Balanced P..." is a list you
        # cannot choose from. This is the GTK3 app's oldest bug.
        self.profile_drop.set_list_factory(self._full_name_factory())
        current = self.config.get("current_profile")
        if current in self.profile_names:
            self.profile_drop.set_selected(self.profile_names.index(current))
        # Connected after the initial selection is in place: set_selected
        # emits the same signal, and handling it here would write the config
        # every time the window opened.
        self.profile_drop.connect("notify::selected", self._on_profile_changed)
        return self.profile_drop

    def _build_profile_menu(self):
        """The menu next to the switcher: everything that changes *which*
        profiles exist, as opposed to which one is active.

        A menu rather than four buttons because the header has to survive a
        360px window, and because these are rare, deliberate acts -- the
        drop-down beside it is the control that gets used every day."""
        for name, handler in (
                ("new-profile", self._on_new_profile),
                ("delete-profile", self._on_delete_profile),
                ("import-profiles", self._on_import_profiles),
                ("export-profile", self._on_export_profile)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

        # Two sections: what this config holds, then moving profiles between
        # machines. The separator between them is the point of the split.
        edit = Gio.Menu()
        edit.append("New Profile…", "win.new-profile")
        edit.append("Delete Profile…", "win.delete-profile")
        transfer = Gio.Menu()
        transfer.append("Import…", "win.import-profiles")
        transfer.append("Export Backup…", "win.export-profile")
        menu = Gio.Menu()
        menu.append_section(None, edit)
        menu.append_section(None, transfer)

        self.profile_menu = Gtk.MenuButton()
        self.profile_menu.set_icon_name("open-menu-symbolic")
        self.profile_menu.set_tooltip_text("Manage profiles")
        self.profile_menu.set_menu_model(menu)
        return self.profile_menu

    @staticmethod
    def _ellipsizing_factory():
        """Factory for the button face, whose label shortens instead of
        pushing the header wide.

        Only the closed button uses this. The popup gets
        ``_full_name_factory`` instead, because a shortened name is a hint
        there and a dead end in the list."""
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item):
            label = Gtk.Label(xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(16)
            item.set_child(label)

        def bind(_factory, item):
            item.get_child().set_text(item.get_item().get_string())

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory

    @staticmethod
    def _full_name_factory():
        """Factory for the popup list: every name in full, however long.

        No ellipsizing and no width cap, so the popover sizes to its longest
        name. GtkDropDown gives the popover the button's width as a *minimum*
        only, so widening past the header costs the closed button nothing."""
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item):
            item.set_child(Gtk.Label(xalign=0))

        def bind(_factory, item):
            item.get_child().set_text(item.get_item().get_string())

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory

    # -- navigation ----------------------------------------------------------

    def _on_page_selected(self, _listbox, row):
        if row is None:
            return
        self.stack.set_visible_child_name(row.page_id)
        self.content_title.set_title(row.page_label)
        self._show_page_actions(row.page_id)
        if self.split.get_collapsed():
            self.split.set_show_content(True)

    def _show_page_actions(self, page_id):
        """Show the visible page's header buttons and hide the rest."""
        for other_id, page in self.pages.items():
            box = getattr(page, "action_box", None)
            if box is not None:
                box.set_visible(other_id == page_id)

    def select_page(self, page_id):
        for row in self.sidebar_list:
            if getattr(row, "page_id", None) == page_id:
                self.sidebar_list.select_row(row)
                return

    # -- profile -------------------------------------------------------------

    def current_profile_name(self):
        return self.config.get("current_profile")

    def current_profile(self):
        """The active profile's dict, always a real dict.

        Returns the live object out of the config rather than a copy, because
        callers edit it in place and then save -- and an empty dict for an
        unknown name, so a page never has to guard every lookup."""
        profiles = self.config.get("profiles", {})
        return profiles.get(self.current_profile_name()) or {}

    def _on_profile_changed(self, drop, _param):
        if self._loading:
            return
        index = drop.get_selected()
        if index < 0 or index >= len(self.profile_names):
            return
        name = self.profile_names[index]
        if name == self.config.get("current_profile"):
            return
        self.config["current_profile"] = name
        config_mod.save_config(self.config)
        self.reload_pages()
        # And then actually put it on the machine. Saving the name alone --
        # which is all this used to do -- left the CPU, GPU and fans running
        # the previous profile, and left power-profiles-daemon on the
        # previous mode, which the enforcer reads as the OS asking for the
        # old profile back. It duly switched back and re-pushed all three
        # fan curves to do it, so a profile switch cost two full curve
        # writes and ended where it started.
        self.apply_profile_async(name)

    def reload_pages(self):
        # Not underneath a write in flight: reload() repoints every row at the
        # profile as it stands now, while the worker is still writing values
        # captured before that, and the worker's own callback then saves and
        # restores against the older ones. The rows ended up showing neither
        # the write that had just happened nor the profile just reloaded.
        # Deferred rather than dropped -- they do have to catch up, just after
        # the write rather than during it. release_hardware runs it.
        if self._hw_owner is not None:
            self._reload_after_apply = True
            return
        for page in self.pages.values():
            reload_fn = getattr(page, "reload", None)
            if reload_fn is not None:
                reload_fn()

    # -- managing which profiles exist ---------------------------------------
    #
    # The rules all live in config.py, where they are pure and tested; what
    # is left here is asking the question and reporting the answer. Every one
    # of these ends in save_config + _refresh_profile_list, because the
    # switcher's model is a snapshot of the config and a stale one offers a
    # switch to a profile that is not there any more.

    def _refresh_profile_list(self, select=None):
        """Rebuild the switcher from the config, selecting ``select``.

        Held under ``_loading`` so that pointing the drop-down at a different
        profile does not read as the user choosing it -- which would fire a
        second, ~20 second hardware apply on top of whatever the caller is
        already doing."""
        was_loading = self._loading
        self._loading = True
        try:
            self._sync_profile_drop(select or self.config.get("current_profile"))
        finally:
            self._loading = was_loading

    def _on_new_profile(self, _action, _param):
        current = self.current_profile_name()
        dialog = Adw.AlertDialog(
            heading="New profile",
            body=f"It starts as a copy of “{current}”, so the machine keeps "
                 f"running exactly as it is now." if current else
                 "The new profile starts from the stock settings.")
        entry = Gtk.Entry(placeholder_text="Profile name")
        # Enter creates, which is the whole interaction for a dialog that is
        # one text field.
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create",
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")
        dialog.set_close_response("cancel")
        # Refused names are refused before the button is pressed rather than
        # after: an empty or duplicate name is the only way this can fail, and
        # a greyed-out Create says so without the user losing what they typed.
        dialog.set_response_enabled("create", False)
        entry.connect("changed", self._on_new_profile_typed, dialog)
        dialog.connect("response", self._on_new_profile_response, entry)
        dialog.present(self)

    def _on_new_profile_typed(self, entry, dialog):
        error = config_mod.profile_name_error(self.config, entry.get_text())
        dialog.set_response_enabled("create", error is None)
        # Red only once there is something to be wrong: an empty field is the
        # starting state, not a mistake.
        if error is not None and entry.get_text().strip():
            entry.add_css_class("error")
            entry.set_tooltip_text(error)
        else:
            entry.remove_css_class("error")
            entry.set_tooltip_text(None)

    def _on_new_profile_response(self, _dialog, response, entry):
        if response != "create":
            return
        try:
            name = config_mod.create_profile(self.config, entry.get_text())
        except ValueError as e:
            self.toast(str(e))
            return
        config_mod.save_config(self.config)
        self._refresh_profile_list(select=name)
        self.reload_pages()
        # No hardware apply: the new profile is a copy of the one already
        # running, so there is nothing to push, and pushing it would cost
        # ~20 seconds of fan writes to arrive back where the machine already is.
        self.toast(f"Profile “{name}” created — a copy of what is running.")

    def _on_delete_profile(self, _action, _param):
        name = self.current_profile_name()
        if not name:
            self.toast("There is no profile to delete.")
            return
        if len((self.config.get("profiles") or {})) <= 1:
            self.toast("This is the only profile left — there has to be one.")
            return
        # Named here rather than discovered afterwards: losing an auto-switch
        # target is a consequence the user should agree to, not find out about
        # the next time they unplug.
        also = [source for source, key in config_mod.AUTO_SWITCH_KEYS.items()
                if self.config.get(key) == name]
        body = "This cannot be undone."
        if also:
            body += (" It is also the profile used on "
                     + " and ".join(also)
                     + " power, so that auto-switch will be turned off.")
        dialog = Adw.AlertDialog(heading=f"Delete “{name}”?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_response, name)
        dialog.present(self)

    def _on_delete_response(self, _dialog, response, name):
        if response != "delete":
            return
        was_current = name == self.config.get("current_profile")
        try:
            current = config_mod.delete_profile(self.config, name)
        except ValueError as e:
            self.toast(str(e))
            return
        config_mod.save_config(self.config)
        self._refresh_profile_list(select=current)
        self.reload_pages()
        self.toast(f"Deleted “{name}”.")
        if was_current:
            # The machine is still running the settings of a profile that no
            # longer exists, and current_profile now names a different one.
            # Leaving those two disagreeing is what the enforcer would spend
            # the next minute correcting anyway.
            self.apply_profile_async(current)

    def _on_export_profile(self, _action, _param):
        # Everything, not the one profile on screen -- this is the backup,
        # so leaving anything out would make it a worse one than just
        # copying ~/.config/rogcontrol.json by hand.
        dialog = Gtk.FileDialog()
        dialog.set_title("Export Backup")
        dialog.set_initial_name("rogcontrol-backup.json")
        dialog.set_filters(self._json_filters())
        # Gtk.FileDialog, not Gtk.FileChooserDialog: the latter is deprecated
        # in GTK 4.10 and its .run() needs a nested main loop, which is the
        # thing this rewrite is built to avoid.
        dialog.save(self, None, self._on_export_chosen)

    def _on_export_chosen(self, dialog, result):
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return  # dismissed
        path = file.get_path() if file is not None else None
        if not path:
            return
        payload = config_mod.export_backup(self.config)
        try:
            with open(path, "w") as f:
                json.dump(payload, f, indent=2)
        except (OSError, TypeError, ValueError) as e:
            self.toast(f"Export failed: {e}")
            return
        self.toast(f"Backed up everything to {os.path.basename(path)}.")

    def _on_import_profiles(self, _action, _param):
        dialog = Gtk.FileDialog()
        dialog.set_title("Import")
        dialog.set_filters(self._json_filters())
        dialog.open(self, None, self._on_import_chosen)

    def _on_import_chosen(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return  # dismissed
        path = file.get_path() if file is not None else None
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            self.toast(f"Could not read that file: {e}")
            return
        # A full backup replaces everything and cannot be undone, so it
        # gets a confirmation the same way Delete does. An old-style file
        # that shares one or a few profiles has no backup marker and merges
        # in immediately, exactly as it always has -- that path is already
        # safe by construction (see import_profiles).
        if config_mod.is_backup_file(data):
            self._confirm_restore_backup(data)
            return
        try:
            # Validates the whole file before it touches the config: a file
            # that is half profiles and half junk must change nothing at all.
            names = config_mod.import_profiles(self.config, data)
        except ValueError as e:
            self.toast(f"Could not import: {e}")
            return
        config_mod.save_config(self.config)
        self._refresh_profile_list()
        self.reload_pages()
        # Imported, not applied: the file describes power limits and fan
        # curves for a machine that may not be this one, so it arrives as
        # something to look at and select, never as something now running.
        if len(names) == 1:
            self.toast(f"Imported “{names[0]}” — select it to apply.")
        else:
            self.toast(f"Imported {len(names)} profiles — "
                       f"select one to apply.")

    def _confirm_restore_backup(self, data):
        profiles = data.get("profiles")
        count = len(profiles) if isinstance(profiles, dict) else 0
        body = (f"This replaces every profile and setting you have now "
                f"with the {count} profile"
                f"{'s' if count != 1 else ''} and settings in this backup. "
                f"This cannot be undone.")
        dialog = Adw.AlertDialog(heading="Restore this backup?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore")
        dialog.set_response_appearance("restore",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_restore_response, data)
        dialog.present(self)

    def _on_restore_response(self, _dialog, response, data):
        if response != "restore":
            return
        try:
            # Raises before cfg.clear() runs, so a corrupt or truncated
            # backup leaves the current config untouched rather than
            # replacing it with half a file.
            config_mod.restore_backup(self.config, data)
        except ValueError as e:
            self.toast(f"Could not restore: {e}")
            return
        config_mod.save_config(self.config)
        self._refresh_profile_list()
        self.reload_pages()
        self.toast("Backup restored.")

    @staticmethod
    def _json_filters():
        """Profile files first, everything else still reachable -- an export
        the user renamed is still a perfectly good import."""
        filters = Gio.ListStore.new(Gtk.FileFilter)
        profile_filter = Gtk.FileFilter()
        profile_filter.set_name("Profile files")
        profile_filter.add_pattern("*.json")
        filters.append(profile_filter)
        everything = Gtk.FileFilter()
        everything.set_name("All files")
        everything.add_pattern("*")
        filters.append(everything)
        return filters

    # -- applying a whole profile --------------------------------------------

    def apply_profile_async(self, name):
        """Push everything in profile ``name`` at the hardware, off the main
        loop, reporting progress in the banner.

        Off the main loop is not optional: the fan channels need 8 seconds
        between them, so this takes about twenty seconds start to finish and
        doing it inline would freeze the window for all of it."""
        # Nothing else may write the hardware underneath this. The drop-down
        # is one way in, deleting the active profile is another, and every
        # page's own Apply is a third -- claim_hardware shuts all of them.
        if not self.claim_hardware(f"applying {name}"):
            return
        profile = (self.config.get("profiles") or {}).get(name) or {}
        self._set_apply_banner(f"Applying {name}…")
        self.apply_async(
            lambda: self._apply_profile_worker(name, profile),
            lambda result, error: self._on_profile_applied(name, result, error))

    def _set_apply_banner(self, text):
        """Show progress text. Safe to call from a worker via idle_add."""
        self.apply_banner.set_title(text)
        self.apply_banner.set_revealed(True)
        return GLib.SOURCE_REMOVE

    def _apply_profile_worker(self, name, profile):
        """Worker thread. Returns the list of things that failed.

        The order here is load-bearing twice over:

        * the OS power mode goes first, because changing it is what wipes the
          EC's custom fan curve on this hardware -- pushing the curves before
          the mode would hand them straight to a controller about to throw
          them away;
        * within the CPU section it is boost, then EPP, then the clock cap,
          because writing cpufreq's ``boost`` refreshes every policy and
          takes ``scaling_max_freq`` back to hardware maximum with it. A cap
          written first is silently undone."""
        failures = []

        def step(text):
            GLib.idle_add(self._set_apply_banner, text)

        def do(label, fn):
            ok, message = fn()
            if not ok:
                failures.append(f"{label}: {message}")

        step(f"Setting the OS power mode for {name}…")
        result = hardware.set_power_mode_for_profile(name)
        # None means this profile maps to no OS mode, which is not a failure
        # -- see hardware.set_power_mode_for_profile.
        if result is not None and not result[0]:
            failures.append(f"OS power mode: {result[1]}")

        # Beside the power mode, and for the same reason: both are things
        # that follow a profile becoming current rather than settings the
        # profile pushes at a subsystem. None means the user is on another
        # lighting mode (Ambient among them, which must not be painted over)
        # or this machine has no controllable keyboard colour.
        #
        # This is one of four callers -- the tray's apply, the hotkey cycler
        # and the enforcer are the others -- because a profile switch made
        # with this window closed has to repaint the keys too, and most of
        # them are.
        result = hardware.set_profile_kbd_color(self.config, name, self.caps)
        if result is not None and not result[0]:
            failures.append(f"Keyboard colour: {result[1]}")

        cpu = profile.get("cpu") or {}
        if cpu:
            step("Applying the CPU power limits…")
            # hardware.cpu_apply_plan, not a chain of ifs. This was the
            # fifth hand-maintained copy of the CPU apply in the tree, and
            # like three of the others it never learned about the clock
            # floor: switching profile from this window wrote every other
            # CPU setting and silently dropped min_freq.
            for step_name, args in hardware.cpu_apply_plan(cpu, self.caps):
                do(STEP_LABELS.get(step_name, step_name),
                   lambda a=args: hardware.run_helper(*a))

        gpu = profile.get("gpu") or {}
        if gpu:
            step("Applying the GPU settings…")
            if "watts" in gpu and self.caps.get("nvidia"):
                do("GPU power limit",
                   lambda: hardware.run_helper("gpu", gpu["watts"]))
            if "clock_limit" in gpu and self.caps.get("nvidia"):
                arg = hardware.gpu_clock_limit_arg(
                    gpu["clock_limit"],
                    (self.caps.get("gpu_limits")
                     or hardware.default_gpu_limits())["clock_limit_max"])
                do("GPU clock ceiling",
                   lambda: hardware.run_helper("gpuclocklimit", arg))
            if "dyn_boost" in gpu and self.caps.get("nv_dynamic_boost"):
                do("Dynamic Boost",
                   lambda: hardware.run_helper("nvboost", gpu["dyn_boost"]))
            if "temp_target" in gpu and self.caps.get("nv_temp_target"):
                do("GPU temperature target",
                   lambda: hardware.run_helper("nvtemp", gpu["temp_target"]))
            if self.caps.get("nvidia_settings"):
                if "clock_offset" in gpu:
                    do("GPU core clock offset",
                       lambda: hardware.set_nvidia_clock_offset(
                           "core", gpu["clock_offset"]))
                if "mem_clock_offset" in gpu:
                    do("GPU memory clock offset",
                       lambda: hardware.set_nvidia_clock_offset(
                           "memory", gpu["mem_clock_offset"]))

        fans = profile.get("fans") or {}
        if fans and self.caps.get("fan_curve"):
            # Only the channels whose curve is not already the one the
            # controller is running. Each write costs a CHANNEL_GAP_S gap
            # before the next, so a switch that used to take ~16 seconds
            # regardless now costs that gap per channel that actually
            # changed -- and nothing at all when the curves match, which is
            # every switch back to a profile whose fans are the same as the
            # current one's.
            #
            # Read from the driver rather than remembered: the EC drops
            # curves behind this app's back on every power-mode change, and a
            # channel it has thrown away has to be rewritten even though
            # nothing in the config moved.
            held = {ch: hardware.read_fan_curve_points(ch)
                    for ch in hardware.FAN_CHANNELS}
            enabled = hardware.read_fan_curve_enabled()
            channels = [
                ch for ch in hardware.FAN_CHANNELS
                if ch in fans
                and not (enabled.get(ch) is not False
                         and held.get(ch) is not None
                         and fancurve.curve_matches_hardware(fans[ch],
                                                             held[ch]))]
            for i, channel in enumerate(channels):
                if i > 0:
                    # Mandatory: the asus-wmi EC can silently drop curve
                    # writes fired too close together. First measured this
                    # as an 8s-only requirement (0.5s left channels stuck);
                    # a later retest found 0.5s-8s all held, so CHANNEL_GAP_S
                    # was lowered to 5s for margin over the retested floor.
                    step(f"Waiting {CHANNEL_GAP_S}s — the fan controller "
                         f"ignores curves written closer together…")
                    time.sleep(CHANNEL_GAP_S)
                label = hardware.FAN_LABELS[channel]
                step(f"Writing the {label} curve ({i + 1} of "
                     f"{len(channels)})…")
                flat = fancurve.curve_to_flat(fans[channel], 8)
                do(label, lambda: hardware.run_helper("fan", channel, *flat))
        return failures

    def _on_profile_applied(self, name, failures, error):
        self.release_hardware()
        self.apply_banner.set_revealed(False)
        if error is not None:
            self.toast(f"Applying {name} failed: {error}")
            return
        if failures:
            self.toast(f"{name} applied, except — " + "; ".join(failures))
        else:
            self.toast(f"Profile: {name} — applied.")
        # The fan page's banner decides from the driver's cached points, and
        # those have just moved.
        self.reload_pages()

    # -- following the config file -------------------------------------------

    def check_external_config_change(self):
        """Re-read the config when something else has written it.

        This window is one of five writers -- the enforcer switches profile
        on AC/battery and when the OS power mode changes, the tray and the
        hotkey cycler switch it too -- and every page save writes this
        window's whole in-memory copy back. Loading the file once at startup
        therefore meant that nudging any slider silently reverted whatever
        those had done in the meantime: unplug the laptop, watch it switch
        to Quiet, touch one control and it was back on Performance.

        The re-read is deliberately not treated as a user edit. ``_loading``
        is set around the profile switcher so that following the file does
        not fire an apply, which would turn every external switch into a
        second ~10 second fan write from this side."""
        mtime = config_mod.config_mtime()
        if not config_mod.config_file_moved_on(self._last_config_mtime, mtime):
            # First sample, or the file is gone: record and read nothing.
            if self._last_config_mtime is None:
                self._last_config_mtime = mtime
            return GLib.SOURCE_CONTINUE
        self._last_config_mtime = mtime
        try:
            with open(config_mod.CONFIG_PATH) as f:
                fresh = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError):
            # Half-written is impossible (every writer renames a temp file
            # over it), so this is a file we should not act on at all.
            return GLib.SOURCE_CONTINUE
        if not isinstance(fresh, dict):
            return GLib.SOURCE_CONTINUE

        profile_changed, contents_changed = config_mod.reload_decision(
            self.config, fresh)
        # Replaced in place rather than rebound: every page reaches this dict
        # through ``window.config``, and some hold the current profile's own
        # sub-dict, so swapping the object would leave them writing into a
        # copy nothing saves.
        self.config.clear()
        self.config.update(fresh)
        if not (profile_changed or contents_changed):
            # The common case, and it includes this window's own saves.
            return GLib.SOURCE_CONTINUE

        was_loading = self._loading
        self._loading = True
        try:
            self._sync_profile_drop(fresh.get("current_profile"))
        finally:
            self._loading = was_loading
        self.reload_pages()
        return GLib.SOURCE_CONTINUE

    def _sync_profile_drop(self, name):
        """Point the switcher at ``name`` without that looking like a user
        selection. Caller holds ``_loading``."""
        names = list((self.config.get("profiles") or {}).keys())
        if names != self.profile_names:
            # A profile was added, renamed or removed elsewhere; a stale list
            # offers a switch to something that no longer exists.
            self.profile_names = names
            self.profile_drop.set_model(
                Gtk.StringList.new(names or ["(no profiles)"]))
        if name in self.profile_names:
            index = self.profile_names.index(name)
            if self.profile_drop.get_selected() != index:
                self.profile_drop.set_selected(index)

    def _on_destroy(self, _widget):
        if self._config_watch is not None:
            GLib.source_remove(self._config_watch)
            self._config_watch = None

    def save_window_size(self):
        """Remember the window's size for next launch.

        Called from two places: closing the window with its own X
        ("close-request") and the tray's Quit item, which tears the
        application down through do_shutdown() instead -- close-request is
        never emitted on that path, so a resize followed by "Quit" from the
        tray used to be silently lost.

        get_default_size() rather than get_width()/get_height(): the two are
        not interchangeable here. This window is restored by handing a saved
        size straight back to set_default_size(), and on this GTK4 stack
        get_width()/get_height() report a size ~20px smaller in each
        dimension than what set_default_size() was actually given -- CSD
        resize-shadow accounting that the two calls don't agree on. Saving
        that shrunk figure and feeding it back through set_default_size()
        next launch shrinks it again, and again on the launch after that:
        every close-and-reopen quietly loses another ~20px until the window
        bottoms out at MIN_WIDTH/MIN_HEIGHT, which is what "the size doesn't
        stay the way I left it" actually was. get_default_size() is GTK's
        own recommended call for exactly this -- it stays in the same units
        set_default_size() was called with, live-tracking the window through
        any manual resize, so the round trip through config has nothing left
        to lose."""
        width, height = self.get_default_size()
        if width > 0 and height > 0:
            self.config["window_size"] = [width, height]
            config_mod.save_config(self.config)

    def _on_close_request(self, _widget):
        # Returning False lets the close proceed -- this only ever observes
        # it, never blocks it.
        self.save_window_size()
        return False

    # -- Ambient -----------------------------------------------------------

    def start_saved_ambient(self):
        """Bring the Ambient sampler back up if that is the saved mode."""
        page = self.pages.get("keyboard")
        if page is not None:
            page.start_saved_ambient()

    def stop_ambient(self):
        page = self.pages.get("keyboard")
        if page is not None:
            page.stop_ambient()

    # -- services offered to pages -------------------------------------------

    def toast(self, text):
        """Show a transient message. Safe to call from any thread."""
        if threading.current_thread() is threading.main_thread():
            self.toast_overlay.add_toast(self._make_toast(text))
        else:
            GLib.idle_add(self._toast_idle, text)

    def _toast_idle(self, text):
        self.toast_overlay.add_toast(self._make_toast(text))
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _make_toast(text):
        # use-markup defaults to True, so text built from live values --
        # a shell command, an exception message, a path -- breaks Pango
        # markup parsing the moment it contains a bare "&" or "<" (e.g. the
        # update terminal fallback's "cd X && ./install.sh") and the toast
        # never appears at all, silently. None of this app's toasts need
        # markup.
        toast = Adw.Toast.new(text)
        toast.set_use_markup(False)
        return toast

    def claim_hardware(self, owner):
        """Take the machine for one slow write, or refuse and say why.

        One writer at a time is a hardware requirement, not tidiness. The
        asus-wmi EC silently drops fan-curve writes fired too close together,
        which is why every fan write in this app is paced by CHANNEL_GAP_S --
        and two paced sequences running at once interleave and defeat the
        pacing that exists to stop exactly that. Before this, pressing Apply
        on the CPU, GPU or Fans page and then switching profile (or letting
        the enforcer switch it on AC/battery) ran both, and channels went
        missing.

        Main thread only: every caller is a signal handler or an idle
        callback, so the check and the set cannot interleave and no lock is
        needed. Returns True when the caller may go ahead; when it returns
        False it has already told the user."""
        if self._hw_owner is not None:
            self.toast(f"Still {self._hw_owner} — try again in a moment.")
            return False
        self._hw_owner = owner
        self._sync_hardware_busy()
        return True

    def release_hardware(self):
        """Hand the machine back. Safe to call when nothing is held."""
        if self._hw_owner is None:
            return
        self._hw_owner = None
        self._sync_hardware_busy()
        if self._reload_after_apply:
            self._reload_after_apply = False
            self.reload_pages()

    def hardware_busy(self):
        return self._hw_owner is not None

    def _sync_hardware_busy(self):
        """Grey out every other way into the hardware while one write runs.

        The drop-down and the profile menu are two ways in; each page's own
        Apply is another, which is what set_hardware_busy is for."""
        busy = self._hw_owner is not None
        self.profile_drop.set_sensitive(not busy)
        self.profile_menu.set_sensitive(not busy)
        for page in self.pages.values():
            hook = getattr(page, "set_hardware_busy", None)
            if hook is not None:
                hook(busy)

    def apply_async(self, fn, on_done=None):
        """Run ``fn()`` on a worker thread; call ``on_done(result, error)``
        back on the main loop.

        Exactly one of ``result``/``error`` is meaningful: a raised exception
        arrives as ``error`` rather than propagating out of a thread nobody
        is watching, where it would print a traceback and leave the caller's
        widget stuck in its "applying" state forever."""
        def worker():
            try:
                result, error = fn(), None
            except Exception as e:  # noqa: BLE001 - reported, not swallowed
                traceback.print_exc()
                result, error = None, e
            if on_done is not None:
                GLib.idle_add(deliver, result, error)

        def deliver(result, error):
            on_done(result, error)
            return GLib.SOURCE_REMOVE

        self._worker_pool.submit(worker)

    def apply_isolated(self, fn, on_done=None, timeout=None):
        """Like ``apply_async``, but on its own throwaway thread rather than
        the shared single-worker pool.

        For one-off calls (the update download) that must not be able to
        wedge that pool: it is also what every page's live-stat polling and
        Apply button run through, so a call here that blocks forever --
        stalled DNS, a connection that never gets past connect() -- would
        freeze the entire app for the rest of the session, not just this
        call. ``timeout`` reports back as an error instead of waiting
        forever; the thread itself is left running in the background since
        Python cannot cancel it, but it can no longer block anything else."""
        done = threading.Event()
        outcome = {}

        def worker():
            try:
                outcome["result"] = fn()
            except Exception as e:  # noqa: BLE001 - reported, not swallowed
                outcome["error"] = e
            finally:
                done.set()

        def deliver(result, error):
            on_done(result, error)
            return GLib.SOURCE_REMOVE

        def supervisor():
            if not done.wait(timeout):
                if on_done is not None:
                    GLib.idle_add(deliver, None, TimeoutError(
                        f"timed out after {timeout:g}s"))
                return
            if on_done is not None:
                GLib.idle_add(deliver, outcome.get("result"),
                              outcome.get("error"))

        threading.Thread(target=worker, daemon=True).start()
        threading.Thread(target=supervisor, daemon=True).start()

    # -- self test -----------------------------------------------------------

    def self_test(self):
        """Exercise every page once, without a main loop.

        GUI code is not unit-testable in this project (no display in CI, no
        test dependencies allowed), so this is the smoke test: it constructs
        every page, then runs each page's own one-shot refresh synchronously
        so that the read-and-render path is covered too, not just the
        widget tree. Anything that raises fails the run."""
        for page_id, _label, _icon in PAGE_SPECS:
            page = self.pages[page_id]
            tick = getattr(page, "self_test_tick", None)
            if tick is not None:
                tick()
        for page_id, _label, _icon in PAGE_SPECS:
            self.select_page(page_id)
        self.select_page("overview")
        # The header menu's items are strings pointing at actions by name, so
        # a renamed handler shows up as a menu entry that is simply dead
        # rather than as an error. Nothing else would catch it.
        for name in ("new-profile", "delete-profile", "import-profiles",
                     "export-profile"):
            if self.lookup_action(name) is None:
                raise RuntimeError(f"the profile menu's {name} action is "
                                   f"missing")
        # One writer at a time. Nothing else covers it: both ways in are
        # signal handlers, and the thing it prevents -- two paced fan-curve
        # sequences interleaving -- shows up as dropped channels on real
        # hardware rather than as an error anywhere.
        if not self.claim_hardware("a self test"):
            raise RuntimeError("claim_hardware refused an idle machine")
        if self.claim_hardware("a second self test"):
            raise RuntimeError("claim_hardware allowed a second writer in")
        if self.profile_drop.get_sensitive():
            raise RuntimeError("the profile switcher stayed live during a "
                               "hardware write")
        self.release_hardware()
        if self.hardware_busy() or not self.profile_drop.get_sensitive():
            raise RuntimeError("release_hardware left the window busy")


class RogControlApp(Adw.Application):
    def __init__(self, self_test=False):
        flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        if self_test:
            # A self test must not hand itself to a running instance, and
            # must not need a session bus at all.
            flags |= Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id=APP_ID, flags=flags)
        self.self_test = self_test
        self.exit_code = 0
        self.win = None

    def _ensure_window(self):
        # Opening the window puts the tray icon back. The tray's own Quit
        # stops its service for good -- systemd is told not to restart that
        # one exit code (see QUIT_EXIT_CODE in rogcontrol-tray) -- so
        # launching the app is the way back, and doing it here covers every
        # route that opens a window: the launcher, --show, --toggle. --quit
        # returns before this and so cannot resurrect the tray it just
        # dismissed.
        hardware.start_tray()
        if self.win is None:
            # Asked once, here, rather than by the GPU page: the System
            # page's About row needs the same answer, and two pages each
            # forking nvidia-smi at startup would cost half a second for one
            # fact. On a machine with no NVIDIA card the exec fails
            # immediately and the fallback ranges come back.
            #
            # Asked BEFORE load_config, and its answer handed in: the one
            # time this matters is a fresh install, where it is what lets
            # the stock profiles start out scaled to the card actually
            # fitted instead of the 140W one they were written against. An
            # existing config ignores it entirely -- migrate_config only
            # reads it while creating profiles from scratch.
            gpu_limits = hardware.detect_gpu_limits()
            config = config_mod.load_config(gpu_min_w=gpu_limits["min_w"],
                                            gpu_max_w=gpu_limits["max_w"])
            caps = hardware.detect_capabilities()
            caps["gpu_limits"] = gpu_limits
            # Asked here rather than inside detect_capabilities, which is
            # standard library only so the helper scripts and the tests can
            # import it: answering this needs GStreamer and a session bus.
            caps["kbd_ambient"] = ambient_available()
            hardware_report_path = None
            self.win = MainWindow(self, config, caps,
                                  hardware_report_path=hardware_report_path)
            self.win.select_page("overview")
            if not self.self_test:
                # Ambient is the only mode that needs a process behind it, so
                # a saved Ambient mode has to be restarted here; every other
                # mode is already live in the firmware from when it was
                # applied. Skipped under --self-test, which must not start a
                # screen capture or change what the keyboard is doing.
                self.win.start_saved_ambient()
                if (caps.get("fan_curve") and not config.get("fan_rpm_cal")
                        and not config.get("fan_calibration_prompted")):
                    # The RPM figures are the developer's own machine's until
                    # this runs once -- see fancurve.FAN_RPM_CAL -- so this
                    # puts the same Calibrate dialog the Fans page's own
                    # button opens in front of the user unasked, the one
                    # time it is likely to still be true that they have not
                    # seen it. idle_add so it appears after the window has
                    # actually mapped, not fighting the initial layout pass.
                    GLib.idle_add(self._prompt_first_run_calibration)
                self._maybe_auto_check_update()
        return self.win

    def _maybe_auto_check_update(self):
        """Run the System page's own update check unasked, if the user has
        opted into it -- see config.py's update_check and
        pages/system.py's UPDATE_AUTO_INTERVALS_S.

        Every mode but "launch" is throttled by a timestamp in the config
        rather than by anything time-based in this process, because the
        process does not live between launches: opening and closing the
        window five times in an hour must not mean five checks just because
        each one is a fresh "launch"."""
        config = self.win.config
        mode = config.get("update_check", "off")
        if mode == "off":
            return
        interval = UPDATE_AUTO_INTERVALS_S.get(mode)
        if interval is not None:
            last = config.get("last_update_check", 0) or 0
            if time.time() - last < interval:
                return
        config["last_update_check"] = time.time()
        config_mod.save_config(config)
        # idle_add: the window has not mapped yet at this point, and the
        # dialog this can open (see pages/system.py) needs a realized parent
        # to present against.
        GLib.idle_add(self._run_auto_update_check)

    def _run_auto_update_check(self):
        page = self.win.pages.get("system")
        if page is not None:
            page.check_for_update()
        return GLib.SOURCE_REMOVE

    def _prompt_first_run_calibration(self):
        # Marked BEFORE the dialog is even shown, not after a response: a
        # crash or a force-quit mid-dialog must not re-arm this every launch
        # forever, which is exactly the kind of nag this is trying not to
        # be. Cancelling the dialog is a real answer -- "not now" -- and the
        # tooltip on the Calibrate button (see pages/fans.py) is what is
        # left to remind them after this one showing.
        self.win.config["fan_calibration_prompted"] = True
        config_mod.save_config(self.win.config)
        self.win.select_page("fans")
        fans_page = self.win.pages.get("fans")
        if fans_page is not None:
            fans_page._on_calibrate_clicked(None)
        return GLib.SOURCE_REMOVE

    def do_shutdown(self):
        # Ambient holds a screen-capture session and a sampling thread open,
        # so it has to be closed deliberately -- nothing else here outlives
        # the process.
        #
        # save_window_size() runs here too: the tray's Quit item calls
        # self.quit(), which reaches do_shutdown() directly without ever
        # emitting the window's "close-request" -- the only other place size
        # is saved. Safe to call with the window already hidden or never
        # shown (--minimized): get_width()/get_height() still report the
        # last realized size, and the >0 guard inside skips it if not.
        if self.win is not None:
            self.win.stop_ambient()
            self.win.save_window_size()
            # cancel_futures=True: a stray tick queued behind an in-flight
            # sample must not delay exit. wait=False: ThreadPoolExecutor
            # otherwise registers an atexit join that would block process
            # exit on a slow in-flight sudo run_helper call -- the
            # daemon=True threads this replaced never had that problem.
            self.win._worker_pool.shutdown(wait=False, cancel_futures=True)
        Adw.Application.do_shutdown(self)

    def do_activate(self):
        self._ensure_window().present()

    def do_command_line(self, cmdline):
        args = list(cmdline.get_arguments()[1:])
        if "--self-test" in args:
            self.exit_code = self._do_self_test()
            return self.exit_code
        if "--quit" in args:
            # The tray's Quit item. The tray is a separate GTK3 process (it
            # has to be -- see rogcontrol-tray) so it cannot reach into this
            # one; sending a flag through the single-instance handoff is the
            # same route it uses to show the window. Handled before the
            # window is built, so quitting an app that is not running starts
            # nothing: this process becomes the instance, quits, and exits.
            self.quit()
            return 0
        win = self._ensure_window()
        if "--toggle" in args:
            # Second launch of an already-visible window means "put it away".
            if win.get_visible():
                win.set_visible(False)
            else:
                win.present()
        elif "--show" in args:
            # One-directional, unlike --toggle: a "show" shortcut should
            # always bring the window up, never hide an already-visible one.
            win.present()
        elif "--hide" in args:
            # The other direction of --show. Hides rather than closes: the
            # window stays alive, invisible, exactly as --toggle's hide
            # branch does, so a following --show is instant rather than a
            # fresh launch.
            win.set_visible(False)
        elif "--minimized" in args:
            # Built but never shown. The window still belongs to the
            # application, which is what keeps the process alive with nothing
            # on screen.
            pass
        else:
            win.present()
        return 0

    def _do_self_test(self):
        try:
            win = self._ensure_window()
            win.self_test()
        except Exception:  # noqa: BLE001 - the point of the flag
            traceback.print_exc()
            return 1
        finally:
            if self.win is not None:
                # Destroying the window is what lets the application exit; a
                # live window holds it open with no main loop running.
                #
                # realize() first, and it is not optional: GTK 4.22.4
                # segfaults inside gtk_window_destroy() on a window that was
                # never realized (gdk_surface_get_display on a NULL surface).
                # Realizing creates the surface without mapping it, so
                # nothing appears on screen and the teardown path is the
                # ordinary one.
                self.win.realize()
                self.win.destroy()
                self.win = None
        print("self-test: built and refreshed every page OK")
        return 0


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    app = RogControlApp(self_test="--self-test" in argv)
    status = app.run(argv)
    return app.exit_code or status
