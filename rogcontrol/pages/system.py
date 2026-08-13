"""System: graphics mode, power-mode sync, the log, and what was detected.

Three read-mostly things and one genuinely dangerous control.

The dangerous one is the graphics mode. Switching it tears down and rebuilds
the display stack, which on this machine means the session goes away and
anything unsaved goes with it. The GTK3 app fired the switch straight off a
radio button, so a stray click on the wrong row could end the session with no
warning. Here it asks first, and it only ever offers the modes supergfxctl
says this machine supports -- on the laptop this was written on that is a
list of one, and a picker offering the other two would be offering two ways
to fail.

The sync row exists because this app and the OS both think they own the
power mode. Selecting a profile sets power-profiles-daemon to match, but
GNOME's power menu can set it back, and until the enforcer notices, the
machine is running one thing and reporting another. Rather than hide that,
the page names both sides and says whether they agree.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from .. import APP_VERSION  # noqa: E402
from .. import hardware  # noqa: E402
from .. import profiles as profiles_mod  # noqa: E402

REFRESH_SECONDS = 5
DASH = "—"

# Lines of log kept in the view. Enough to cover a boot's worth of applies
# and the failure before it; more than this and the widget costs more to
# render than the page is worth.
LOG_LINES = 300

GPU_MODE_SUBTITLE = (
    "Integrated turns the NVIDIA card off entirely for battery life; hybrid "
    "leaves it available for games. Changing this restarts the display "
    "stack — you will be logged out."
)

SYNC_DESCRIPTION = (
    "This app and the OS both hold an opinion about the power mode. "
    "Selecting a profile sets the OS mode to match; anything else that "
    "changes it — GNOME's power menu, a keyboard key — leaves the two "
    "disagreeing until the background enforcer notices."
)


class SystemPage(Adw.PreferencesPage):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.caps = window.caps
        self._loading = True
        self._sampling = False
        self._timer_id = None
        self._switching = False
        self.modes = []

        self._build()
        self._reload_log()
        self.reload()
        self._loading = False
        self._refresh_now()
        self._timer_id = GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)
        self.connect("destroy", self._on_destroy)

    # -- construction --------------------------------------------------------

    def _build(self):
        self._build_gpu_mode()
        self._build_sync()
        self._build_log()
        self._build_about()

    def _build_gpu_mode(self):
        group = Adw.PreferencesGroup(title="Graphics mode")
        self.add(group)

        self.mode_row = Adw.ComboRow(title="GPU mode",
                                     subtitle=GPU_MODE_SUBTITLE)
        # Populated from the daemon in _refresh_now: what this machine
        # supports is a question only supergfxctl can answer, and asking it
        # costs a subprocess, so the row starts empty rather than showing a
        # guess that is then corrected.
        self.mode_row.set_model(Gtk.StringList.new([]))
        self.mode_row.connect("notify::selected", self._on_mode_changed)
        group.add(self.mode_row)

        if not self.caps.get("supergfxctl"):
            self.mode_row.set_sensitive(False)
            self.mode_row.set_tooltip_text(
                "Not available on this machine: supergfxctl is not installed")
            self.mode_row.set_subtitle(
                "supergfxctl is not installed — GPU mode switching is "
                "unavailable.")

    def _build_sync(self):
        group = Adw.PreferencesGroup(title="Power mode",
                                     description=SYNC_DESCRIPTION)
        self.add(group)
        self.profile_row, self.profile_value = self._value_row(
            group, "Active profile")
        self.osmode_row, self.osmode_value = self._value_row(
            group, "OS power mode", "power-profiles-daemon's active profile")
        self.sync_row, self.sync_value = self._value_row(group, "In sync")

    def _build_log(self):
        group = Adw.PreferencesGroup(
            title="Log",
            description=f"{hardware.LOG_PATH} — written by this app, the "
                        f"boot-apply service and the background enforcer.")
        self.add(group)

        refresh = Gtk.Button(label="Refresh")
        refresh.set_valign(Gtk.Align.CENTER)
        refresh.connect("clicked", lambda _b: self._reload_log())
        group.set_header_suffix(refresh)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_cursor_visible(False)
        self.log_view.set_monospace(True)
        # Wrapping, not scrolling sideways: a log line is long, and a
        # horizontal scrollbar inside a page that is meant to work at 360px
        # is exactly the failure this rewrite exists to remove.
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        for setter in (self.log_view.set_top_margin,
                       self.log_view.set_bottom_margin,
                       self.log_view.set_left_margin,
                       self.log_view.set_right_margin):
            setter(8)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Tall enough to be a log rather than a peephole, short enough that
        # the About group below it is still reachable without a long scroll.
        scroller.set_min_content_height(220)
        scroller.set_max_content_height(360)
        scroller.set_child(self.log_view)

        row = Adw.PreferencesRow()
        row.set_activatable(False)
        row.set_focusable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for setter in (box.set_margin_top, box.set_margin_bottom,
                       box.set_margin_start, box.set_margin_end):
            setter(8)
        box.append(scroller)
        row.set_child(box)
        group.add(row)

    def _build_about(self):
        group = Adw.PreferencesGroup(title="About")
        self.add(group)

        row = Adw.ActionRow(title="ROG Control",
                            subtitle="Power, fan and GPU control for ASUS ROG "
                                     "laptops")
        version = Gtk.Label(label=APP_VERSION)
        version.add_css_class("numeric")
        version.add_css_class("dim-label")
        row.add_suffix(version)
        group.add(row)

        hardware_row = Adw.ActionRow(title="Detected hardware")
        hardware_row.set_subtitle(self._hardware_summary())
        # The summary is several lines of detection results; without this it
        # is one long line that would push the window wide.
        hardware_row.set_subtitle_lines(0)
        group.add(hardware_row)

    def _value_row(self, group, title, subtitle=""):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        label = Gtk.Label(label=DASH)
        label.add_css_class("dim-label")
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_xalign(1.0)
        row.add_suffix(label)
        group.add(row)
        return row, label

    def _hardware_summary(self):
        """What detection actually found, in the order it matters.

        Named capabilities rather than a yes/no list: "no ryzenadj" is the
        answer to "why are the CPU sliders greyed out", and that is the only
        question this row exists to answer."""
        caps = self.caps
        limits = caps.get("gpu_limits") or hardware.default_gpu_limits()
        lines = []
        lines.append(f"GPU: {limits['name']}" if limits.get("name")
                     else "GPU: no NVIDIA card detected")
        if caps.get("nvidia"):
            lines.append(f"GPU power range: {limits['min_w']}–"
                         f"{limits['max_w']} W, clock ceiling up to "
                         f"{limits['clock_limit_max']} MHz")
        clock = caps.get("cpu_clock")
        if clock:
            lines.append(f"CPU clock range: {clock[0] / 1e6:.1f}–"
                         f"{clock[1] / 1e6:.1f} GHz")
        present = [name for name, ok in (
            ("custom fan curve", caps.get("fan_curve")),
            ("fan rpm", caps.get("fan_rpm")),
            ("ryzenadj", caps.get("ryzenadj")),
            ("nvidia-smi", caps.get("nvidia")),
            ("nvidia-settings", caps.get("nvidia_settings")),
            ("supergfxctl", caps.get("supergfxctl")),
            ("rogauracore", caps.get("rogauracore")),
            ("Dynamic Boost", caps.get("nv_dynamic_boost")),
            ("GPU temp target", caps.get("nv_temp_target")),
            ("charge limit", caps.get("charge_limit")),
            ("keyboard backlight", caps.get("kbd_backlight")),
        ) if ok]
        lines.append("Available: " + (", ".join(present) if present
                                      else "nothing detected"))
        return "\n".join(lines)

    # -- refresh -------------------------------------------------------------

    def _on_destroy(self, _widget):
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _tick(self):
        if self.get_mapped():
            self._refresh_now()
        return GLib.SOURCE_CONTINUE

    def _refresh_now(self):
        if self._sampling or self._switching:
            return
        self._sampling = True
        self.window.apply_async(self._sample, self._on_sample)

    def _sample(self):
        """Worker thread: three subprocesses, no widgets."""
        return {
            "mode": (hardware.read_gpu_mode()
                     if self.caps.get("supergfxctl") else None),
            "modes": (hardware.read_supported_gpu_modes()
                      if self.caps.get("supergfxctl") else []),
            "power_mode": hardware.read_power_mode(),
        }

    def _on_sample(self, data, error):
        self._sampling = False
        if error is None:
            self._render(data)

    def _render(self, data):
        self._render_modes(data.get("modes") or [], data.get("mode"))
        self._render_sync(data.get("power_mode"))

    def _render_modes(self, modes, active):
        # supergfxctl answering with nothing at all leaves the last known
        # list up rather than emptying the picker under the user's cursor.
        if not modes:
            modes = list(self.modes) or (
                list(hardware.GPU_MODES_FALLBACK)
                if self.caps.get("supergfxctl") else [])
        # The active mode always appears, even if -s did not list it: it is
        # what the machine is running, and a picker that cannot show it would
        # show something else as selected, which reads as a mode change.
        if active and active not in modes:
            modes = modes + [active]
        was_loading = self._loading
        self._loading = True
        try:
            if modes != self.modes:
                self.modes = modes
                self.mode_row.set_model(Gtk.StringList.new(modes))
            if active in modes:
                index = modes.index(active)
                if self.mode_row.get_selected() != index:
                    self.mode_row.set_selected(index)
        finally:
            self._loading = was_loading
        if not self.caps.get("supergfxctl"):
            return
        # Set both ways round, not just off: supergfxd can be restarted or
        # its mode list can change under a running window, and a row latched
        # insensitive on one sample would never come back.
        only_one = len(modes) <= 1
        self.mode_row.set_sensitive(not only_one)
        self.mode_row.set_tooltip_text(
            f"supergfxctl reports {modes[0]} as the only mode this machine "
            f"supports" if only_one else None)

    def _render_sync(self, power_mode):
        name = self.window.current_profile_name() or DASH
        self.profile_value.set_text(name)
        self.osmode_value.set_text(power_mode or DASH)
        expected = profiles_mod.expected_ppd_mode(name)
        agree = profiles_mod.ppd_modes_agree(name, power_mode)
        for css in ("success", "warning", "error"):
            self.sync_value.remove_css_class(css)
        if agree is None:
            self.sync_value.set_text("not comparable")
            self.sync_row.set_subtitle(
                "power-profiles-daemon is not answering"
                if power_mode is None else
                f"“{name}” is not one of the four stock profiles, so it maps "
                f"to no OS power mode")
        elif agree:
            self.sync_value.set_text("yes")
            self.sync_value.add_css_class("success")
            self.sync_row.set_subtitle(
                f"“{name}” and the OS both mean {power_mode}")
        else:
            self.sync_value.set_text("no")
            self.sync_value.add_css_class("warning")
            self.sync_row.set_subtitle(
                f"“{name}” expects {expected}, but the OS is on "
                f"{power_mode} — re-select the profile to push it back")

    def _reload_log(self):
        text = hardware.read_log_tail(LOG_LINES)
        buffer = self.log_view.get_buffer()
        if text is None:
            buffer.set_text(f"No log yet at {hardware.LOG_PATH}.")
            return
        buffer.set_text(text or "The log is empty.")
        # Sit at the newest line: the reason to open a log is what happened
        # last, and a view that opens at the oldest entry has to be scrolled
        # every single time.
        buffer.place_cursor(buffer.get_end_iter())
        self.log_view.scroll_to_mark(buffer.get_insert(), 0.0, True, 0.0, 1.0)

    # -- graphics mode -------------------------------------------------------

    def _on_mode_changed(self, row, _param):
        if self._loading or self._switching:
            return
        item = row.get_selected_item()
        if item is None:
            return
        mode = item.get_string()
        # Asked, never assumed: this ends the session. The picker is put
        # back first, so declining leaves the row showing what is actually
        # running rather than the mode that was not switched to.
        dialog = Adw.AlertDialog(
            heading=f"Switch graphics mode to {mode}?",
            body="This restarts the display stack. You will be logged out "
                 "and anything unsaved in any application will be lost.\n\n"
                 "Some machines need a full reboot before the new mode is "
                 "in force.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("switch", f"Switch to {mode}")
        dialog.set_response_appearance("switch",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_mode_response, mode)
        dialog.present(self)

    def _on_mode_response(self, _dialog, response, mode):
        if response != "switch":
            # Put the row back on the running mode.
            self._refresh_now()
            return
        self._switching = True
        self.mode_row.set_sensitive(False)
        self.window.toast(f"Switching graphics mode to {mode}…")
        self.window.apply_async(
            lambda: hardware.set_gpu_mode(mode),
            lambda result, error: self._on_mode_applied(mode, result, error))

    def _on_mode_applied(self, mode, result, error):
        self._switching = False
        self.mode_row.set_sensitive(True)
        ok, message = (False, str(error)) if error is not None else result
        if ok:
            self.window.toast(f"Graphics mode set to {mode}. "
                              f"Log out to finish switching.")
        else:
            self.window.toast(f"Graphics mode change failed: {message}")
        self._refresh_now()

    # -- shell hooks ---------------------------------------------------------

    def reload(self):
        """The active profile changed, so the sync verdict has too.

        Re-rendered against the last known OS mode rather than re-reading
        it: the profile is what changed, and a subprocess on the main loop
        for every profile switch would make the switch feel slow."""
        mode = self.osmode_value.get_text()
        self._render_sync(None if mode == DASH else mode)

    def self_test_tick(self):
        """One synchronous read-and-render of every section. No writes."""
        self._render(self._sample())
        self._reload_log()
