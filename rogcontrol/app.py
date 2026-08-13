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

The application id is deliberately ``com.fadi.rogcontrol.dev``: the GTK3 app
still ships as ``com.fadi.rogcontrol``, and sharing an id means the running
old app claims the launch and silently gets presented instead of this one.
"""

import sys
import threading
import traceback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import config as config_mod  # noqa: E402
from . import hardware  # noqa: E402
from .pages.cpu import CpuPage  # noqa: E402
from .pages.overview import OverviewPage  # noqa: E402

APP_ID = "com.fadi.rogcontrol.dev"

# (id, sidebar label, icon). Order is the sidebar order.
PAGE_SPECS = (
    ("overview", "Overview", "speedometer-symbolic"),
    ("cpu", "CPU", "computer-chip-symbolic"),
    ("gpu", "GPU", "video-display-symbolic"),
    ("fans", "Fans", "weather-windy-symbolic"),
    ("battery", "Battery", "battery-good-symbolic"),
    ("keyboard", "Keyboard", "input-keyboard-symbolic"),
    ("system", "System", "emblem-system-symbolic"),
)

# Pages not built yet get a placeholder rather than being left out of the
# sidebar: the shape of the finished app should be visible from the first
# run, and a missing entry reads as a bug where "Coming next" reads as a plan.
PLACEHOLDERS = {
    "gpu": ("GPU", "Power limit, clock offsets, Dynamic Boost and the "
                   "temperature target move here."),
    "fans": ("Fans", "The three curve editors and the RPM calibration move "
                     "here."),
    "battery": ("Battery", "Charge limit and the AC/battery profile pickers "
                           "move here."),
    "keyboard": ("Keyboard", "Brightness, effect, colours and speed move "
                             "here."),
    "system": ("System", "GPU mode, power-mode sync, the log view and About "
                         "move here."),
}


class MainWindow(Adw.ApplicationWindow):
    """Split-view window: sidebar of pages, content pane per page.

    NavigationSplitView rather than the old notebook because it collapses to
    a single pane on a narrow window, which is what finally lets this window
    be resized freely -- the GTK3 version fought its own minimum width."""

    def __init__(self, app, config, caps):
        super().__init__(application=app)
        self.config = config
        self.caps = caps
        # Set while widgets are being populated from the config, so the
        # handlers that apply settings can tell "the user moved this" from
        # "we just loaded a profile into it". Without it, building the window
        # would fire an apply for every control on screen.
        self._loading = True

        self.set_title("ROG Control")
        width, height = 960, 720
        saved = config.get("window_size")
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            try:
                # Floor the saved size: the GTK3 window could be narrower than
                # this layout's natural width, and restoring that would open
                # with the sidebar already collapsed for no reason.
                width = max(880, int(saved[0]))
                height = max(600, int(saved[1]))
            except (TypeError, ValueError):
                pass
        self.set_default_size(width, height)

        self.pages = {}
        self._build_ui()
        self._loading = False

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
        header.pack_end(self._build_profile_switcher())

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        for page_id, label, _icon in PAGE_SPECS:
            page = self._build_page(page_id, label)
            self.pages[page_id] = page
            self.stack.add_named(page, page_id)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.stack)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self.toast_overlay)
        return Adw.NavigationPage(title="ROG Control", child=toolbar)

    def _build_page(self, page_id, label):
        if page_id == "overview":
            return OverviewPage(self)
        if page_id == "cpu":
            return CpuPage(self)
        title, description = PLACEHOLDERS[page_id]
        status = Adw.StatusPage(title=title, description=description)
        status.set_icon_name(dict(
            (pid, icon) for pid, _l, icon in PAGE_SPECS)[page_id])
        # Named so the placeholder is obviously a stage rather than a failure.
        button = Gtk.Button(label="Coming next")
        button.set_sensitive(False)
        button.set_halign(Gtk.Align.CENTER)
        button.add_css_class("pill")
        status.set_child(button)
        return status

    def _build_profile_switcher(self):
        """The profile list, in the header where it applies to every page."""
        self.profile_names = list(self.config.get("profiles", {}).keys())
        self.profile_drop = Gtk.DropDown.new_from_strings(
            self.profile_names or ["(no profiles)"])
        self.profile_drop.set_tooltip_text("Active profile")
        current = self.config.get("current_profile")
        if current in self.profile_names:
            self.profile_drop.set_selected(self.profile_names.index(current))
        # Connected after the initial selection is in place: set_selected
        # emits the same signal, and handling it here would write the config
        # every time the window opened.
        self.profile_drop.connect("notify::selected", self._on_profile_changed)
        return self.profile_drop

    # -- navigation ----------------------------------------------------------

    def _on_page_selected(self, _listbox, row):
        if row is None:
            return
        self.stack.set_visible_child_name(row.page_id)
        self.content_title.set_title(row.page_label)
        if self.split.get_collapsed():
            self.split.set_show_content(True)

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
        # Only the on-screen values follow the profile for now. Pushing the
        # whole profile at the hardware means a ~16 second fan curve write
        # (the EC drops curve writes fired less than 8 seconds apart), so
        # that belongs with the Fans page and its progress indicator rather
        # than hidden behind a dropdown.
        self.reload_pages()
        self.toast(f"Profile: {name}")

    def reload_pages(self):
        for page in self.pages.values():
            reload_fn = getattr(page, "reload", None)
            if reload_fn is not None:
                reload_fn()

    # -- services offered to pages -------------------------------------------

    def toast(self, text):
        """Show a transient message. Safe to call from any thread."""
        if threading.current_thread() is threading.main_thread():
            self.toast_overlay.add_toast(Adw.Toast.new(text))
        else:
            GLib.idle_add(self._toast_idle, text)

    def _toast_idle(self, text):
        self.toast_overlay.add_toast(Adw.Toast.new(text))
        return GLib.SOURCE_REMOVE

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

        threading.Thread(target=worker, daemon=True).start()

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
        self.select_page("cpu")
        self.select_page("overview")


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
        if self.win is None:
            config = config_mod.load_config()
            caps = hardware.detect_capabilities()
            self.win = MainWindow(self, config, caps)
            self.win.select_page("overview")
        return self.win

    def do_activate(self):
        self._ensure_window().present()

    def do_command_line(self, cmdline):
        args = list(cmdline.get_arguments()[1:])
        if "--self-test" in args:
            self.exit_code = self._do_self_test()
            return self.exit_code
        win = self._ensure_window()
        if "--toggle" in args:
            # Second launch of an already-visible window means "put it away".
            if win.get_visible():
                win.set_visible(False)
            else:
                win.present()
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
