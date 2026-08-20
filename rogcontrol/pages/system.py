"""System: asusd, power-mode sync, the boot sound, the log, and detection.

Read-mostly things, and one conflict worth naming.

The graphics mode picker used to live here and is now on the GPU page. It
belongs there: which card the screen is plugged into is a fact about the
graphics card, and every control that depends on it -- power limit,
temperature target, Dynamic Boost -- is on that page already.

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

The boot sound is here rather than on a tuning page because it is a property
of the machine and not of how hard it is being driven: the firmware plays it
before any operating system is running, and switching profile must not change
it. So it is written straight to the hardware, kept at the top level of the
config rather than inside a profile, and re-asserted at login by the
boot-apply service in case a firmware reset has brought the chime back.

Panel overdrive sits beside it on all four of those counts, which is why it
is here and not on the GPU page. The GPU page is about the discrete card --
what it is allowed to draw, how hot it may get, which card the screen is
plugged into -- and overdrive is none of those: it is the panel's own
response-time setting, written to the same asus-wmi platform device as the
chime, held in firmware, and no more part of a profile than the chime is.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import APP_VERSION  # noqa: E402
from .. import config as config_mod  # noqa: E402
from .. import hardware  # noqa: E402
from .. import profiles as profiles_mod  # noqa: E402

REFRESH_SECONDS = 5
DASH = "—"

# Lines of log kept in the view. Enough to cover a boot's worth of applies
# and the failure before it; more than this and the widget costs more to
# render than the page is worth.
LOG_LINES = 300

SUPERGFX_SUBTITLE = (
    "The daemon that switches between integrated, hybrid and dGPU graphics. "
    "The picker itself is on the GPU page."
)

SUPERGFX_ABSENT = (
    "supergfxctl is not installed, so the graphics mode cannot be read or "
    "changed. Install supergfxctl and enable its supergfxd service."
)

SUPERGFX_SILENT = (
    "supergfxctl is installed but supergfxd is not answering, so the mode "
    "cannot be read and nothing can be switched. Check it with "
    "systemctl status supergfxd."
)

SUPERGFX_OK = "The picker on the GPU page can switch modes."

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

# The boot sound is the one control on this page that is not about a daemon.
# It follows the tuning pages rather than the status rows around it: the
# consequence on the row, the paragraph on hover.
BOOT_SOUND_SUBTITLE = "The chime the firmware plays at power-on"

BOOT_SOUND_TOOLTIP = (
    "ASUS firmware plays a short chime through the speakers when the laptop "
    "powers on, before any operating system is running. This switch is the "
    "firmware's own setting for it — the same one Armoury Crate writes under "
    "Windows — so it is not part of a profile and does not change when you "
    "switch one. Writing it needs root, so it goes through this app's "
    "privileged helper, and it is remembered so the boot-apply service can "
    "put it back if a firmware reset loses it."
)

# Overdrive is the same shape of control as the chime above it, so it is
# written the same way: what it costs on the row, the paragraph on hover.
PANEL_OD_SUBTITLE = "Faster pixel response, at the cost of some overshoot"

PANEL_OD_TOOLTIP = (
    "Panel overdrive drives each pixel transition past its target and lets "
    "it settle back, so the display changes state faster and fast motion "
    "smears less. It is a trade, not a free improvement: pushing a "
    "transition too far shows up as overshoot — a pale ghost leading a "
    "moving edge — and how visible that is depends on the panel. Leave it "
    "on if motion looks cleaner to you and off if the ghosting does not. "
    "Like the boot sound this is the firmware's own setting, so it is not "
    "part of a profile and does not change when you switch one. Writing it "
    "needs root, so it goes through this app's privileged helper, and it is "
    "remembered so the boot-apply service can put it back if a firmware "
    "reset loses it."
)

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
        # True from the moment an asusd enable/disable is asked for until
        # systemd has been asked what actually happened, so the two buttons
        # cannot be pressed again mid-flight.
        self._asusd_busy = False
        # Same idea for the boot sound: true while the helper is being asked
        # to change it, so a sample that lands mid-write cannot put the
        # switch back to the value the firmware has not been given yet.
        self._boot_sound_busy = False
        # And again for overdrive. Two flags rather than one shared "a
        # firmware write is in flight": the two rows are independent, and a
        # single flag would freeze the chime switch while overdrive was
        # being written for no reason the user could see.
        self._panel_od_busy = False
        self.asusd_state = {}
        # What is in the picker, and separately the last non-empty answer
        # supergfxctl -s gave. The two are deliberately not the same list.

        self._build()
        self._reload_log()
        self.reload()
        self._loading = False
        self._refresh_now()
        self._timer_id = GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)
        self.connect("destroy", self._on_destroy)

    # -- construction --------------------------------------------------------

    def _build(self):
        self._build_supergfx()
        self._build_asusd()
        self._build_sync()
        self._build_firmware()
        self._build_log()
        self._build_about()

    def _build_supergfx(self):
        """Whether the daemon the graphics-mode picker needs is answering.

        The picker itself is on the GPU page. This row is here because it is
        the same question as the asusd row below it -- is the daemon this app
        depends on present and talking -- and because when the answer is no,
        the GPU page's picker is greyed out and the reason belongs somewhere
        a user looking for "why can I not switch" will find it."""
        group = Adw.PreferencesGroup(title="Graphics mode daemon")
        self.add(group)
        self.supergfx_row, self.supergfx_value = self._value_row(
            group, "supergfxd", SUPERGFX_SUBTITLE, strong=True)

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

    def _build_firmware(self):
        """Settings that live in the firmware rather than in a profile."""
        self.firmware_group = group = Adw.PreferencesGroup(title="Firmware")
        self.add(group)
        self.boot_sound_row = Adw.SwitchRow(title="Boot sound",
                                            subtitle=BOOT_SOUND_SUBTITLE)
        self.boot_sound_row.set_tooltip_text(BOOT_SOUND_TOOLTIP)
        self.boot_sound_row.connect("notify::active", self._on_boot_sound)
        group.add(self.boot_sound_row)

        self.panel_od_row = Adw.SwitchRow(title="Panel overdrive",
                                          subtitle=PANEL_OD_SUBTITLE)
        self.panel_od_row.set_tooltip_text(PANEL_OD_TOOLTIP)
        self.panel_od_row.connect("notify::active", self._on_panel_od)
        group.add(self.panel_od_row)

        # Per row now that there are two of them, and the group only goes
        # when neither is there: plenty of machines have the chime and no
        # panel_od, and a group hidden on the first missing capability would
        # take a working control away with it.
        self.boot_sound_row.set_visible(bool(self.caps.get("boot_sound")))
        self.panel_od_row.set_visible(bool(self.caps.get("panel_od")))
        if not (self.caps.get("boot_sound") or self.caps.get("panel_od")):
            # Nothing left in "Firmware" to show a heading for.
            self.firmware_group.set_visible(False)


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
            ("panel overdrive", caps.get("panel_od")),
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
        if self._sampling:
            return
        self._sampling = True
        self.window.apply_async(self._sample, self._on_sample)

    def _sample(self):
        """Worker thread: a handful of subprocesses, no widgets."""
        return {
            # Sampled every cycle, like the asusd state below: supergfxd can
            # be started or stopped under a running window, and a row latched
            # on the answer it gave at startup would be wrong for the rest of
            # the session.
            "gpu_mode": (hardware.read_gpu_mode()
                         if self.caps.get("supergfxctl") else None),
            "power_mode": hardware.read_power_mode(),
            # Read every cycle rather than once at startup: asusd can be
            # installed, started or stopped while this window is open, and a
            # page claiming it is absent while it is fighting over the fans
            # is the worst version of this row.
            "asusd": hardware.read_asusd_state(),
            # One sysfs read. Sampled every cycle rather than once at
            # startup for the same reason as the mode above: the firmware
            # setup screen and a BIOS update both write this, and a switch
            # read once would show a stale position all session.
            "boot_sound": (hardware.read_boot_sound()
                           if self.caps.get("boot_sound") else None),
            # One more sysfs read, sampled for the same reason: the firmware
            # setup screen writes this too, and so does anything else on the
            # machine that talks to asus-wmi.
            "panel_od": (hardware.read_panel_od()
                         if self.caps.get("panel_od") else None),
        }

    def _on_sample(self, data, error):
        self._sampling = False
        if error is None:
            self._render(data)

    def _render(self, data):
        self._render_supergfx(data.get("gpu_mode"), data.get("gpu_mode_error"))
        self._render_asusd(data.get("asusd") or {})
        self._render_sync(data.get("power_mode"))
        self._render_boot_sound(data.get("boot_sound"))
        self._render_panel_od(data.get("panel_od"))

    def _render_supergfx(self, mode, error):
        """Three states, said apart: absent, present but silent, working.

        "Not installed" and "installed but not answering" are different
        problems with different fixes, and collapsing them into one dash is
        what makes a greyed-out picker look like a missing feature."""
        if not self.caps.get("supergfxctl"):
            self.supergfx_value.set_text("not installed")
            self.supergfx_row.set_subtitle(SUPERGFX_ABSENT)
            self._supergfx_css("warning")
            return
        if mode is None:
            self.supergfx_value.set_text("not answering")
            self.supergfx_row.set_subtitle(
                SUPERGFX_SILENT + (f"\n\n{error}" if error else ""))
            self._supergfx_css("warning")
            return
        self.supergfx_value.set_text("running")
        self.supergfx_row.set_subtitle(
            f"Answering, and reporting {mode}. " + SUPERGFX_OK)
        self._supergfx_css("success")

    def _supergfx_css(self, name):
        for css in ("success", "warning"):
            self.supergfx_value.remove_css_class(css)
        self.supergfx_value.add_css_class(name)

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

    # -- boot sound ----------------------------------------------------------

    def _render_boot_sound(self, value):
        """Put the switch where the firmware actually is.

        ``_loading`` around the set, exactly as the mode picker does it:
        moving the switch here must not look like the user moving it, or
        every sample would fire a helper call writing back the value it has
        just read."""
        if value is None or self._boot_sound_busy:
            return
        was_loading = self._loading
        self._loading = True
        try:
            self.boot_sound_row.set_active(bool(value))
        finally:
            self._loading = was_loading

    def _on_boot_sound(self, row, _param):
        if self._loading or self._boot_sound_busy:
            return
        wanted = 1 if row.get_active() else 0
        self._boot_sound_busy = True
        row.set_sensitive(False)
        self.window.apply_async(
            lambda: hardware.run_helper("bootsound", wanted),
            lambda result, error: self._on_boot_sound_set(
                wanted, result, error))

    def _on_boot_sound_set(self, wanted, result, error):
        self._boot_sound_busy = False
        self.boot_sound_row.set_sensitive(True)
        ok, message = (False, str(error)) if error is not None else result
        if ok:
            # Top level, not inside the profile: this describes the machine,
            # not how hard it is being driven, so it must not change when a
            # profile does. The boot-apply service re-asserts it from here
            # after a firmware reset has forgotten it.
            self.window.config["boot_sound"] = wanted
            config_mod.save_config(self.window.config)
            self.window.toast("Boot sound on — the firmware will chime at "
                              "power-on." if wanted else
                              "Boot sound off — the firmware will start "
                              "silently.")
        else:
            self.window.toast(f"Boot sound change failed: {message}")
        # Ask the firmware rather than assuming the switch worked; a refused
        # write puts the switch straight back.
        self._refresh_now()

    # -- panel overdrive -----------------------------------------------------

    def _render_panel_od(self, value):
        """Put the switch where the panel actually is.

        Same ``_loading`` guard as the chime, for the same reason: a set
        that looks like the user moving the switch would have every sample
        fire a helper call writing back the value it has just read."""
        if value is None or self._panel_od_busy:
            return
        was_loading = self._loading
        self._loading = True
        try:
            self.panel_od_row.set_active(bool(value))
        finally:
            self._loading = was_loading

    def _on_panel_od(self, row, _param):
        if self._loading or self._panel_od_busy:
            return
        wanted = 1 if row.get_active() else 0
        self._panel_od_busy = True
        row.set_sensitive(False)
        self.window.apply_async(
            lambda: hardware.run_helper("panelod", wanted),
            lambda result, error: self._on_panel_od_set(
                wanted, result, error))

    def _on_panel_od_set(self, wanted, result, error):
        self._panel_od_busy = False
        self.panel_od_row.set_sensitive(True)
        ok, message = (False, str(error)) if error is not None else result
        if ok:
            # Top level rather than inside the profile, for the reason the
            # chime is: this describes the screen, not how hard the machine
            # is being driven, so it must survive a profile switch. The
            # boot-apply service re-asserts it from here.
            self.window.config["panel_od"] = wanted
            config_mod.save_config(self.window.config)
            self.window.toast("Panel overdrive on — faster pixel response, "
                              "watch for ghosting on moving edges."
                              if wanted else
                              "Panel overdrive off — the panel runs at its "
                              "own response time.")
        else:
            self.window.toast(f"Panel overdrive change failed: {message}")
        # Ask the hardware rather than assuming the switch worked; a refused
        # write puts the switch straight back.
        self._refresh_now()

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
