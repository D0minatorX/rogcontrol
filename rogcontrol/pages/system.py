"""System: graphics mode, asusd, power-mode sync, the log, and detection.

Read-mostly things, one genuinely dangerous control, and one conflict worth
naming.

The dangerous one is the graphics mode. Switching it tears down and rebuilds
the display stack, which on this machine means the session goes away and
anything unsaved goes with it. The GTK3 app fired the switch straight off a
radio button, so a stray click on the wrong row could end the session with no
warning. Here it asks first -- but it offers all three modes, exactly as the
GTK3 app did.

It did briefly build the picker out of ``supergfxctl -s``, which sounds
right and is not. That command answers "what will the daemon accept in the
state it is in right now", and on a laptop whose hardware MUX has the
display wired to the discrete GPU the answer is the single mode it is
already in. A picker built from that holds one entry and switches nothing,
which is how the feature went missing. So -s is reported as information on
its own row, the mode actually in force is stated in full above it, and a
mode the daemon will not take comes back refused in supergfxd's own words
on a row below -- a refusal being a far better answer than an empty list.

That is also why the mode section is four rows rather than one. Those three
facts are status, so they stay in visible text: a greyed-out picker whose
reason is hidden in a tooltip reads as a missing feature -- "where is the
switch for the gpu mode" -- and on a touchpad there is no hover to find the
reason with. The picker itself is a control, so it is the one row here that
follows the rest of the app and keeps its explanation on hover.

The conflict is asusd. It is asusctl's daemon and it drives exactly the same
hardware as this app: the same asus-wmi platform knobs, the same three custom
fan curves, the same keyboard lighting. Two programs re-asserting different
fan curves at the same embedded controller is not a configuration, and the
fans are where it is audible. So the page says whether asusd is installed and
what it is doing, and can stop and disable it -- or put it back. What it does
not do is remove the package: that is a transaction the user should see, so
the exact command for the detected distro is shown instead.

The sync row exists because this app and the OS both think they own the
power mode. Selecting a profile sets power-profiles-daemon to match (the
window's profile switch pushes the mode before anything else, because
changing it is what wipes the EC's fan curve). GNOME's power menu can set
it back, and until the enforcer notices, the machine is running one thing
and reporting another. Rather than hide that, the page names both sides and
says whether they agree.

What the enforcer then does is adopt, not revert: an externally set mode is
treated as a request to switch profile, so the disagreement is resolved by
this app moving to the profile that mode maps to. The row used to tell the
user to "re-select the profile to push it back", which was never possible
-- selecting the profile that is already current is a no-op in the switcher,
by design, since it would otherwise cost a full ~20 second re-apply.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import APP_VERSION  # noqa: E402
from .. import hardware  # noqa: E402
from .. import profiles as profiles_mod  # noqa: E402

REFRESH_SECONDS = 5
DASH = "—"

# Lines of log kept in the view. Enough to cover a boot's worth of applies
# and the failure before it; more than this and the widget costs more to
# render than the page is worth.
LOG_LINES = 300

# The picker is a control, so it follows the tuning pages: the consequence
# stays on the row, the paragraph goes on hover. The reasoning the user asked
# to have visible is status -- what is running, what supergfxd will accept,
# what it answered -- and that stays on the rows around this one.
GPU_MODE_SUBTITLE = "Restarts the display stack — you will be logged out"

GPU_MODE_TOOLTIP = (
    "Integrated turns the NVIDIA card off entirely for battery life; hybrid "
    "leaves it available for the applications that ask for it; AsusMuxDgpu "
    "wires the display straight to it. All three are offered whatever "
    "supergfxd lists as supported — it is asked, and its answer is shown "
    "below. Switching restarts the display stack, which ends the session, so "
    "it asks for confirmation first."
)

# What each of supergfxctl's mode names means, in one line. The names are the
# daemon's own spelling and several of them say nothing to anyone who has not
# read its source, which is not a good state for the row that describes what
# your display is plugged into.
GPU_MODE_DESCRIPTIONS = {
    "Integrated": "The integrated GPU drives everything and the NVIDIA card "
                  "is powered down. Longest battery life, no dGPU for games.",
    "Hybrid": "The integrated GPU drives the screen; the NVIDIA card wakes "
              "for the applications that ask for it.",
    "NvidiaNoModeset": "The NVIDIA card is loaded without kernel modesetting.",
    "Vfio": "The NVIDIA card is bound to vfio, for passing through to a "
            "virtual machine.",
    "AsusEgpu": "An external GPU is driving the display.",
    "AsusMuxDgpu": "The display is wired straight to the NVIDIA card by the "
                   "hardware MUX. Fastest, and the integrated GPU's power "
                   "saving is bypassed.",
}

# The subtitle on the row that reports supergfxctl -s. Information, not a
# gate: every mode is offered whatever this row says. See
# hardware.gpu_mode_choices.
MODES_SUPPORTED_SUBTITLE = (
    "What supergfxd says it will accept in the state it is in right now. "
    "All the modes below are offered whatever this says."
)

# Added to it when the daemon is listing fewer modes than are on offer,
# which on this hardware is the normal state rather than a fault. Visible
# text, not a tooltip: on a touchpad there is no hover at all.
MODES_PARTIAL_SUBTITLE = (
    "It is listing fewer than are offered below, which is what it reports "
    "when the laptop's hardware MUX has the display wired to one GPU. "
    "Switching to a mode it has not listed may well be refused — try it and "
    "supergfxd's own answer appears below. Which GPU the MUX uses is chosen "
    "in the firmware setup screen (or in Armoury Crate under Windows), not "
    "from the OS."
)

# The row that repeats supergfxd's reply to a switch, word for word. A toast
# is gone in five seconds and a refusal is the thing you most want to still
# be able to read.
MODE_ANSWER_TITLE = "supergfxd's answer"

# When it said yes, or no, and said nothing else. Rare, but a blank row under
# a heading that promises an answer is worse than a sentence saying there
# wasn't one.
MODE_ANSWER_SILENT_OK = "It accepted the change without printing anything."
MODE_ANSWER_SILENT_FAIL = "It refused the change without saying why."

NO_DAEMON_SUBTITLE = (
    "supergfxctl is installed but supergfxd is not answering, so the current "
    "mode cannot be read and nothing can be switched. Check the service with "
    "systemctl status supergfxd."
)

ASUSD_DESCRIPTION = (
    "asusd is asusctl's background daemon, and it drives the same hardware "
    "as this app: the same asus-wmi platform knobs, the same three custom "
    "fan curves, the same keyboard lighting. With both running they "
    "re-assert different settings at each other — the fans surge and the "
    "lighting changes on its own. Run one of them, not both."
)

# The three asusd rows are actions, so they get the same treatment as the
# settings on the tuning pages: the command or the consequence stays on the
# row, the paragraph explaining it is on hover. What asusd *is* and why it
# conflicts stays in visible text (ASUSD_DESCRIPTION, ASUSD_STATE_SUBTITLE) --
# that is the page telling the user what is going on, not a control
# describing itself.
ASUSD_DISABLE_SUBTITLE = "systemctl disable --now asusd — reversible below"

ASUSD_DISABLE_TOOLTIP = (
    "Stops asusd and keeps it stopped across reboots. Needs root, so it goes "
    "through this app's privileged helper. Nothing is removed and it is "
    "reversible with the row below."
)

ASUSD_ENABLE_SUBTITLE = "Both will compete for the hardware again"

ASUSD_ENABLE_TOOLTIP = (
    "Puts asusd back exactly as it was, running and starting at boot. Expect "
    "the fans and the keyboard lighting to start disagreeing with this app "
    "again while both are running."
)

ASUSD_REMOVE_TOOLTIP = (
    "This app does not run your package manager. Removing a package is a "
    "transaction you should see, with its own confirmation and its own list "
    "of what else goes with it — so the command is printed here to run in a "
    "terminal yourself."
)

NO_PACKAGE_MANAGER_SUBTITLE = (
    "No package manager this app recognises was found — remove the asusctl "
    "package the way you installed it."
)

ASUSD_STATE_TEXT = {
    hardware.ASUSD_ABSENT: "not installed",
    hardware.ASUSD_RUNNING: "running",
    hardware.ASUSD_STOPPED_ENABLED: "stopped, but starts at boot",
    hardware.ASUSD_STOPPED_DISABLED: "stopped and disabled",
}

ASUSD_STATE_SUBTITLE = {
    hardware.ASUSD_ABSENT:
        "Nothing else is driving this hardware — this is the state this app "
        "wants to be in. There is nothing here to stop, disable or "
        "uninstall; those buttons appear only when asusd is installed.",
    hardware.ASUSD_RUNNING:
        "asusd is running right now and is competing with this app for the "
        "fans, the power profile and the keyboard lighting.",
    hardware.ASUSD_STOPPED_ENABLED:
        "Not running now, but it is still enabled — it comes back at the "
        "next boot and starts competing again.",
    hardware.ASUSD_STOPPED_DISABLED:
        "Installed but stopped and disabled, so it will not come back on its "
        "own. Nothing is competing for the hardware.",
}

SYNC_DESCRIPTION = (
    "This app and the OS both hold an opinion about the power mode. "
    "Selecting a profile here sets the OS mode to match. Changing it the "
    "other way — GNOME's power menu, a keyboard key — is taken as a request "
    "to switch profile: the background enforcer picks it up within a minute "
    "and moves this app to the profile that mode maps to."
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
        # True from the moment an asusd enable/disable is asked for until
        # systemd has been asked what actually happened, so the two buttons
        # cannot be pressed again mid-flight.
        self._asusd_busy = False
        self.asusd_state = {}
        # What is in the picker, and separately the last non-empty answer
        # supergfxctl -s gave. The two are deliberately not the same list.
        self.modes = []
        self.supported_modes = []

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
        self._build_asusd()
        self._build_sync()
        self._build_log()
        self._build_about()

    def _build_gpu_mode(self):
        group = Adw.PreferencesGroup(title="Graphics mode")
        self.add(group)

        # What the machine is running, first and in full. This is the answer
        # to "which GPU is my screen on", and it is worth having whether or
        # not anything can be switched.
        self.mode_now_row, self.mode_now_value = self._value_row(
            group, "Current mode", strong=True)
        self.modes_row, self.modes_value = self._value_row(
            group, "Modes supergfxd supports", MODES_SUPPORTED_SUBTITLE)

        # Why there is no picker, when there is no picker. A separate row and
        # not the ComboRow's own subtitle, because an insensitive row draws
        # its text dimmed -- and the one thing this text must be is readable.
        self.mode_blocked_row = Adw.ActionRow(title="Switch mode")
        self.mode_blocked_row.set_subtitle_lines(0)
        self.mode_blocked_row.set_visible(False)
        group.add(self.mode_blocked_row)

        self.mode_row = Adw.ComboRow(title="Switch mode",
                                     subtitle=GPU_MODE_SUBTITLE)
        self.mode_row.set_tooltip_text(GPU_MODE_TOOLTIP)
        # All three from the start, not a list built from supergfxctl -s.
        # The daemon's list says what it will take in the state it is in, not
        # what the machine can do, and filtering by it is what left this
        # picker with a single entry and no way to switch anything.
        self.modes = hardware.gpu_mode_choices()
        self.mode_row.set_model(Gtk.StringList.new(self.modes))
        # Unclipped, so the warning still reads in full at a narrow window.
        self.mode_row.set_subtitle_lines(0)
        self.mode_row.connect("notify::selected", self._on_mode_changed)
        group.add(self.mode_row)

        # Empty until something has actually been switched, then supergfxd's
        # reply verbatim -- an acceptance or, more usefully, its refusal.
        self.mode_answer_row, self.mode_answer_value = self._value_row(
            group, MODE_ANSWER_TITLE)
        self.mode_answer_row.set_visible(False)

        if not self.caps.get("supergfxctl"):
            self._block_switching(
                "supergfxctl is not installed, so the graphics mode cannot "
                "be read or changed from here. Install supergfxctl and its "
                "supergfxd service to switch between integrated and hybrid "
                "graphics.")
            self.mode_now_row.set_subtitle(
                "supergfxctl is not installed — the mode cannot be read.")

    def _block_switching(self, reason):
        """Replace the picker with the reason there is nothing to pick."""
        self.mode_row.set_visible(False)
        self.mode_blocked_row.set_subtitle(reason)
        self.mode_blocked_row.set_visible(True)

    def _allow_switching(self):
        self.mode_blocked_row.set_visible(False)
        self.mode_row.set_subtitle(GPU_MODE_SUBTITLE)
        self.mode_row.set_sensitive(True)
        self.mode_row.set_visible(True)

    def _build_asusd(self):
        """Whether the other daemon for this hardware is on the machine."""
        group = Adw.PreferencesGroup(title="asusctl / asusd",
                                     description=ASUSD_DESCRIPTION)
        self.add(group)

        check = Gtk.Button(label="Check")
        check.set_valign(Gtk.Align.CENTER)
        check.set_tooltip_text("Ask systemd again, right now")
        check.connect("clicked", self._on_check_asusd)
        group.set_header_suffix(check)

        self.asusd_row, self.asusd_value = self._value_row(
            group, "asusd service", strong=True)

        self.asusd_disable_row = Adw.ActionRow(
            title="Stop and disable asusd", subtitle=ASUSD_DISABLE_SUBTITLE)
        self.asusd_disable_row.set_subtitle_lines(0)
        self.asusd_disable_row.set_tooltip_text(ASUSD_DISABLE_TOOLTIP)
        self.asusd_disable_button = Gtk.Button(label="Stop and disable")
        self.asusd_disable_button.set_valign(Gtk.Align.CENTER)
        self.asusd_disable_button.add_css_class("destructive-action")
        self.asusd_disable_button.connect("clicked", self._on_asusd_disable)
        self.asusd_disable_row.add_suffix(self.asusd_disable_button)
        self.asusd_disable_row.set_activatable_widget(
            self.asusd_disable_button)
        group.add(self.asusd_disable_row)

        self.asusd_enable_row = Adw.ActionRow(
            title="Enable and start asusd", subtitle=ASUSD_ENABLE_SUBTITLE)
        self.asusd_enable_row.set_subtitle_lines(0)
        self.asusd_enable_row.set_tooltip_text(ASUSD_ENABLE_TOOLTIP)
        self.asusd_enable_button = Gtk.Button(label="Enable")
        self.asusd_enable_button.set_valign(Gtk.Align.CENTER)
        self.asusd_enable_button.connect("clicked", self._on_asusd_enable)
        self.asusd_enable_row.add_suffix(self.asusd_enable_button)
        self.asusd_enable_row.set_activatable_widget(self.asusd_enable_button)
        group.add(self.asusd_enable_row)

        # The command is worked out once: which package manager is on this
        # machine does not change while the window is open.
        self.uninstall_command = hardware.asusd_uninstall_command()
        self.asusd_remove_row = Adw.ActionRow(title="Uninstall asusctl")
        self.asusd_remove_row.set_subtitle_lines(0)
        # The command itself stays on the row -- it is the whole point of the
        # row, the Copy button beside it copies exactly that, and a command
        # you have to hover to read is a command you cannot type. Why this app
        # will not run it for you is on hover.
        self.asusd_remove_row.set_subtitle(self.uninstall_command
                                           or NO_PACKAGE_MANAGER_SUBTITLE)
        self.asusd_remove_row.set_tooltip_text(ASUSD_REMOVE_TOOLTIP)
        if self.uninstall_command:
            self.copy_button = Gtk.Button(label="Copy")
            self.copy_button.set_valign(Gtk.Align.CENTER)
            self.copy_button.set_tooltip_text("Copy the command to the "
                                              "clipboard")
            self.copy_button.connect("clicked", self._on_copy_command)
            self.asusd_remove_row.add_suffix(self.copy_button)
            self.asusd_remove_row.set_activatable_widget(self.copy_button)
        group.add(self.asusd_remove_row)

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

    def _value_row(self, group, title, subtitle="", strong=False):
        """A titled row whose suffix label carries the value.

        ``strong`` is for the one or two facts on this page that are the
        answer rather than a detail -- the graphics mode in force, what asusd
        is doing. They get the heading style instead of the dimmed one, so
        they are legible at a glance from across the page."""
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        # Several of these subtitles are a sentence or two of explanation.
        row.set_subtitle_lines(0)
        label = Gtk.Label(label=DASH)
        label.add_css_class("heading" if strong else "dim-label")
        label.set_wrap(True)
        # WORD, not WORD_CHAR. These values are single words as often as not
        # -- AsusMuxDgpu, balanced, yes -- and breaking inside one produced
        # "Asus-Mux-Dgpu" and "bala-nced" in a window with room to spare.
        # Word wrapping keeps the whole word and takes the width it needs;
        # a two-word value like "Balanced Power" still wraps cleanly.
        label.set_wrap_mode(Pango.WrapMode.WORD)
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
        """Worker thread: a handful of subprocesses, no widgets."""
        return {
            "mode": (hardware.read_gpu_mode()
                     if self.caps.get("supergfxctl") else None),
            "modes": (hardware.read_supported_gpu_modes()
                      if self.caps.get("supergfxctl") else []),
            "power_mode": hardware.read_power_mode(),
            # Read every cycle rather than once at startup: asusd can be
            # installed, started or stopped while this window is open, and a
            # page claiming it is absent while it is fighting over the fans
            # is the worst version of this row.
            "asusd": hardware.read_asusd_state(),
        }

    def _on_sample(self, data, error):
        self._sampling = False
        if error is None:
            self._render(data)

    def _render(self, data):
        self._render_modes(data.get("modes") or [], data.get("mode"))
        self._render_asusd(data.get("asusd") or {})
        self._render_sync(data.get("power_mode"))

    def _render_modes(self, supported, active):
        """Fill the picker, and report -s beside it without obeying it.

        ``supported`` is whatever ``supergfxctl -s`` just said. It goes on
        its own row as information; it does not decide what the picker
        holds. See hardware.gpu_mode_choices for why."""
        # Keep the last non-empty answer: supergfxd going quiet for one
        # sample should not blank the row that says what it supports.
        if supported:
            self.supported_modes = list(supported)
        modes = hardware.gpu_mode_choices(active, self.supported_modes)
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
        # The two facts, stated whether or not anything can be switched.
        self.mode_now_value.set_text(active or DASH)
        if active:
            self.mode_now_row.set_subtitle(GPU_MODE_DESCRIPTIONS.get(
                active, "supergfxd's own name for the mode this machine is "
                        "running."))
        # What -s reported, not what the picker holds -- those are different
        # lists on this machine, and conflating them is the bug this row now
        # exists to make visible.
        self.modes_value.set_text(", ".join(self.supported_modes)
                                  if self.supported_modes else DASH)
        subtitle = MODES_SUPPORTED_SUBTITLE
        if self.supported_modes and len(self.supported_modes) < len(modes):
            subtitle += " " + MODES_PARTIAL_SUBTITLE
        self.modes_row.set_subtitle(subtitle)

        if not self.caps.get("supergfxctl"):
            return
        # Set both ways round, not just off: supergfxd can be restarted under
        # a running window, and a row latched insensitive on one sample would
        # never come back. Nothing else blocks the picker -- a short list from
        # -s is not a reason to take the choice away, only to say so above.
        if active is None:
            self._block_switching(NO_DAEMON_SUBTITLE)
            self.mode_now_row.set_subtitle(
                "supergfxd is not answering, so the mode cannot be read.")
            return
        self._allow_switching()

    def _render_asusd(self, state):
        """Say what asusd is doing, and offer only what makes sense."""
        self.asusd_state = state
        name = state.get("state", hardware.ASUSD_ABSENT)
        self.asusd_value.set_text(ASUSD_STATE_TEXT.get(name, DASH))
        subtitle = ASUSD_STATE_SUBTITLE.get(name, "")
        if state.get("raw_active") == "failed":
            # Not running, but it tried and something went wrong -- worth
            # saying, because "stopped" alone reads as deliberate.
            subtitle += (" systemd reports the unit as failed rather than "
                         "cleanly stopped.")
        self.asusd_row.set_subtitle(subtitle)
        for css in ("success", "warning", "error"):
            self.asusd_value.remove_css_class(css)
        # Running is the state that costs the user something, so it is the
        # one that gets a colour. Absent and disabled are both fine.
        self.asusd_value.add_css_class(
            "warning" if name == hardware.ASUSD_RUNNING else "success")

        installed = bool(state.get("installed"))
        # Disable is worth offering while anything would bring it back:
        # running now, or stopped but still enabled.
        self.asusd_disable_button.set_sensitive(
            not self._asusd_busy
            and name in (hardware.ASUSD_RUNNING,
                         hardware.ASUSD_STOPPED_ENABLED))
        self.asusd_enable_button.set_sensitive(
            not self._asusd_busy and installed
            and name != hardware.ASUSD_RUNNING)
        # Rows for a package that is not here would be three controls that
        # cannot do anything; the state row above already says so.
        self.asusd_disable_row.set_visible(installed)
        self.asusd_enable_row.set_visible(installed)
        self.asusd_remove_row.set_visible(installed)

    def _on_check_asusd(self, _button):
        self._refresh_now()
        self.window.toast("Checked asusd.")

    def _on_asusd_disable(self, _button):
        self._set_asusd_running(False)

    def _on_asusd_enable(self, _button):
        self._set_asusd_running(True)

    def _set_asusd_running(self, running):
        """Stop+disable or enable+start asusd, through the helper."""
        if self._asusd_busy:
            return
        self._asusd_busy = True
        self.asusd_disable_button.set_sensitive(False)
        self.asusd_enable_button.set_sensitive(False)
        self.window.toast("Enabling asusd…" if running
                          else "Stopping and disabling asusd…")
        self.window.apply_async(
            lambda: hardware.set_asusd_running(running),
            lambda result, error: self._on_asusd_set(running, result, error))

    def _on_asusd_set(self, running, result, error):
        self._asusd_busy = False
        ok, message = (False, str(error)) if error is not None else result
        if ok:
            self.window.toast("asusd enabled and started." if running else
                              "asusd stopped and disabled — ROG Control now "
                              "has the hardware to itself.")
        else:
            self.window.toast(f"asusd change failed: {message}")
        # Ask systemd rather than assuming the button worked.
        self._refresh_now()

    def _on_copy_command(self, _button):
        command = self.uninstall_command
        if not command:
            return
        display = Gdk.Display.get_default()
        if display is None:
            self.window.toast("No display to copy through.")
            return
        display.get_clipboard().set(command)
        self.window.toast(f"Copied: {command}")

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
                f"{power_mode} — the enforcer settles this within a minute "
                f"by switching to the profile {power_mode} maps to")

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
        # The previous attempt's answer is about the previous attempt.
        self.mode_answer_row.set_visible(False)
        self.window.toast(f"Switching graphics mode to {mode}…")
        self.window.apply_async(
            lambda: hardware.set_gpu_mode(mode),
            lambda result, error: self._on_mode_applied(mode, result, error))

    def _on_mode_applied(self, mode, result, error):
        self._switching = False
        self.mode_row.set_sensitive(True)
        ok, message = (False, str(error)) if error is not None else result
        self._show_mode_answer(mode, ok, message)
        if ok:
            self.window.toast(f"Graphics mode set to {mode}. "
                              f"Log out to finish switching.")
        else:
            self.window.toast(f"Graphics mode change failed: {message}")
        self._refresh_now()

    def _show_mode_answer(self, mode, ok, message):
        """Put supergfxd's reply on the page, word for word.

        Verbatim and not summarised: when the daemon refuses -- the likely
        answer for a mode the hardware MUX has ruled out -- its own wording
        is the only thing that says which of several reasons applied. A
        toast is gone in five seconds; this stays until the next attempt."""
        self.mode_answer_row.set_title(f"supergfxd's answer to {mode}")
        self.mode_answer_value.set_text("accepted" if ok else "refused")
        for css in ("success", "warning"):
            self.mode_answer_value.remove_css_class(css)
        self.mode_answer_value.add_css_class("success" if ok else "warning")
        self.mode_answer_row.set_subtitle(
            (message or "").strip()
            or (MODE_ANSWER_SILENT_OK if ok else MODE_ANSWER_SILENT_FAIL))
        self.mode_answer_row.set_visible(True)

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
