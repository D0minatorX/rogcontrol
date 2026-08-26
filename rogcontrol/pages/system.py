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

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import APP_VERSION  # noqa: E402
from .. import config as config_mod  # noqa: E402
from .. import fancurve  # noqa: E402
from .. import hardware  # noqa: E402
from .. import profiles as profiles_mod  # noqa: E402

REFRESH_SECONDS = 5
DASH = "—"

# A flat, temporary override rather than a fourth profile: it holds every
# channel at one percentage regardless of temperature for a fixed time, then
# hands the fans straight back to whatever profile is active. Same shape as
# the calibration step in pages/fans.py (_write_flat), minus the measuring.
FAN_BOOST_PCT = 85
FAN_BOOST_SECONDS = 120
# Matches pages/fans.py's CHANNEL_GAP_S: the embedded controller silently
# drops a fan-curve write fired closer to the last one than this.
FAN_BOOST_CHANNEL_GAP_S = 5

FAN_BOOST_SUBTITLE = (
    f"Hold every fan at a flat {FAN_BOOST_PCT}% for "
    f"{FAN_BOOST_SECONDS // 60} minutes, then restore the active profile's "
    f"curve. Carries on if you close the window.")

FAN_BOOST_TOOLTIP = (
    f"Drives every fan channel to a flat {FAN_BOOST_PCT}% regardless of "
    f"temperature for {FAN_BOOST_SECONDS} seconds, then reapplies the active "
    "profile -- the same push Applying a profile does. Useful for clearing "
    "dust or checking the fans still spin up.\n\n"
    "The hold is recorded with a deadline, and the background enforcer both "
    "maintains it and ends it, so closing this window part-way through does "
    "not leave the fans stuck at "
    f"{FAN_BOOST_PCT}% -- they go back to the profile's own curve on time "
    "either way.")

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

SUPERGFX_STOPPED = (
    "supergfxctl is installed but its supergfxd service is not running, so "
    "the mode cannot be read and nothing can be switched. Enabling it starts "
    "it now and brings it back at every boot."
)

SUPERGFX_ENABLE_SUBTITLE = (
    "Runs systemctl enable --now supergfxd. The graphics-mode picker on the "
    "GPU page needs this daemon; without it the mode can be neither read nor "
    "changed."
)

SUPERGFX_ENABLE_TOOLTIP = (
    "Some distributions install supergfxctl without switching its service "
    "on. This turns it on and starts it, and it stays on across reboots."
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

# The one switch on this page that does not take effect when it is moved, so
# the row says which way round it is before it is touched rather than only
# afterwards in a toast.
PSR_SUBTITLE = (
    "Saves battery on the internal panel. Turn it off if the machine freezes "
    "after login in Hybrid or Integrated graphics. Takes effect at the next "
    "boot")

PSR_TOOLTIP = (
    "Panel self-refresh lets the display controller stop sending frames to a "
    "still screen and lets the panel redraw itself from its own memory, which "
    "saves power while nothing is moving.\n\n"
    "Turn it off if switching to Hybrid or Integrated graphics leaves the "
    "machine frozen a few seconds after the login screen. Those are the modes "
    "where the built-in screen is driven by the AMD graphics rather than the "
    "NVIDIA card, and some kernel versions crash in the panel self-refresh "
    "code when it is. In AsusMuxDgpu the screen is on the NVIDIA card, that "
    "code never runs, and this setting changes nothing.\n\n"
    "This one is not an ASUS firmware knob like the two above: it is a kernel "
    "boot parameter, so it is written into the bootloader's configuration and "
    "only takes effect after a reboot. The old configuration is backed up "
    "first, and put back automatically if any part of the change fails."
)

PSR_PENDING_ON = (
    "The bootloader has been changed to turn panel self-refresh back on, but "
    "this session is still running with it off. Reboot to finish.")

PSR_PENDING_OFF = (
    "The bootloader has been changed to turn panel self-refresh off, but this "
    "session is still running with it on — so a graphics-mode switch can "
    "still freeze until you reboot.")

PSR_FOREIGN = (
    "Something else on this machine already sets amdgpu.dcdebugmask ({0}) in "
    "the bootloader configuration. That is not this app's setting to rewrite, "
    "so this switch is disabled. Remove it by hand if you want this app to "
    "manage panel self-refresh.")

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
        # The same, for the supergfxd enable button. Its own flag rather than
        # sharing the asusd one: the two rows are about different daemons and
        # a single flag would grey out one while the other was working.
        self._supergfx_busy = False
        # Same idea for the boot sound: true while the helper is being asked
        # to change it, so a sample that lands mid-write cannot put the
        # switch back to the value the firmware has not been given yet.
        self._boot_sound_busy = False
        # And again for overdrive. Two flags rather than one shared "a
        # firmware write is in flight": the two rows are independent, and a
        # single flag would freeze the chime switch while overdrive was
        # being written for no reason the user could see.
        self._panel_od_busy = False
        # Its own flag on the same grounds as the two above, and needed more:
        # the helper call behind it runs limine-update, which takes seconds
        # rather than milliseconds, so there is a real window in which a
        # sample could land and put the switch back under the user.
        self._psr_busy = False
        # Fan boost: true from the click until the profile has been put
        # back at the end of the hold, so the button cannot be pressed again
        # mid-flight and a second boost cannot be stacked on the first. The
        # hold itself is a deadline on disk rather than any of these -- see
        # the fan boost section below -- so all this holds is what the page
        # is currently showing about it.
        self._boost_active = False
        self._boost_countdown_id = None
        self._boost_deadline = None
        self._boost_profile = None
        self.asusd_state = {}
        # What is in the picker, and separately the last non-empty answer
        # supergfxctl -s gave. The two are deliberately not the same list.

        self._build()
        self._reload_log()
        self.reload()
        self._loading = False
        self._refresh_now()
        # A boost can already be running: it belongs to a deadline on disk
        # rather than to any one window, so this one may well have been
        # opened in the middle of somebody else's.
        self.resume_fan_boost()
        self._timer_id = GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)
        self.connect("destroy", self._on_destroy)

    # -- construction --------------------------------------------------------

    def _build(self):
        self._build_supergfx()
        self._build_asusd()
        self._build_sync()
        self._build_fan_boost()
        self._build_firmware()
        self._build_psr()
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

        # Only ever shown when there is something to do with it: the package
        # is here and the daemon is not running. Offering "Enable" on a
        # machine with no supergfxctl would be a button that cannot work, and
        # offering it while the daemon already answers would be a button that
        # does nothing.
        self.supergfx_enable_row = Adw.ActionRow(
            title="Enable and start supergfxd",
            subtitle=SUPERGFX_ENABLE_SUBTITLE)
        self.supergfx_enable_row.set_subtitle_lines(0)
        self.supergfx_enable_row.set_tooltip_text(SUPERGFX_ENABLE_TOOLTIP)
        self.supergfx_enable_button = Gtk.Button(label="Enable")
        self.supergfx_enable_button.set_valign(Gtk.Align.CENTER)
        self.supergfx_enable_button.connect("clicked",
                                            self._on_supergfx_enable)
        self.supergfx_enable_row.add_suffix(self.supergfx_enable_button)
        self.supergfx_enable_row.set_activatable_widget(
            self.supergfx_enable_button)
        self.supergfx_enable_row.set_visible(False)
        group.add(self.supergfx_enable_row)

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

    def _build_fan_boost(self):
        """A temporary flat override, not a fourth profile.

        Gated on the same capability as the fans page itself -- a button
        that cannot reach the hardware is worse than no button."""
        if not self.window.caps.get("fan_curve"):
            return
        group = Adw.PreferencesGroup(title="Fan boost")
        self.add(group)
        self.fan_boost_row = Adw.ActionRow(title="Fan boost",
                                           subtitle=FAN_BOOST_SUBTITLE)
        self.fan_boost_row.set_subtitle_lines(0)
        self.fan_boost_row.set_tooltip_text(FAN_BOOST_TOOLTIP)
        self.fan_boost_button = Gtk.Button(
            label=f"Boost {FAN_BOOST_PCT}% for {FAN_BOOST_SECONDS // 60} min")
        self.fan_boost_button.set_valign(Gtk.Align.CENTER)
        self.fan_boost_button.connect("clicked", self._on_fan_boost_clicked)
        self.fan_boost_row.add_suffix(self.fan_boost_button)
        self.fan_boost_row.set_activatable_widget(self.fan_boost_button)
        group.add(self.fan_boost_row)

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

    def _build_psr(self):
        """The one switch here that changes the kernel command line.

        Its own group and not a third row under Firmware, because it is not
        firmware: it is a boot parameter, it needs a reboot, and grouping it
        with two switches that take effect the instant they are moved would
        make it look like it does too."""
        if not self.caps.get("psr_toggle"):
            return
        group = Adw.PreferencesGroup(title="Kernel boot options")
        self.add(group)

        self.psr_row = Adw.SwitchRow(title="AMD panel self-refresh",
                                     subtitle=PSR_SUBTITLE)
        self.psr_row.set_subtitle_lines(0)
        self.psr_row.set_tooltip_text(PSR_TOOLTIP)
        self.psr_row.connect("notify::active", self._on_psr)
        group.add(self.psr_row)

        # Only ever visible when the config and the running kernel disagree,
        # which is the whole of the time between changing this and rebooting.
        # A switch that has already moved is not evidence of anything having
        # happened yet, and this is the row that says so.
        self.psr_pending_row = Adw.ActionRow(title="Pending")
        self.psr_pending_row.set_subtitle_lines(0)
        self.psr_pending_row.set_visible(False)
        group.add(self.psr_pending_row)

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
            ("panel self-refresh toggle", caps.get("psr_toggle")),
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
        # The fan boost countdown IS cancelled here, now that it is only a
        # label. What ends a boost is the deadline on disk, which the
        # enforcer reads -- so a window torn down mid-hold costs nothing but
        # the countdown text, and the fans come off the flat curve on time
        # regardless. Before that, this timer was the only thing that could
        # end a boost, and dropping it here left the fans pinned until the
        # next login.
        if self._boost_countdown_id is not None:
            GLib.source_remove(self._boost_countdown_id)
            self._boost_countdown_id = None

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
        # Sampled every cycle, like the asusd state below: supergfxd can be
        # started or stopped under a running window, and a row latched on the
        # answer it gave at startup would be wrong for the rest of the
        # session.
        gpu_mode = (hardware.read_gpu_mode()
                    if self.caps.get("supergfxctl") else None)
        # Asked ONLY when the mode did not come back. It is three more
        # subprocesses per tick, and the one thing it is used for -- telling
        # "installed but switched off" apart from "installed and broken" --
        # cannot arise while the daemon is answering.
        supergfxd = (hardware.read_supergfxd_state()
                     if self.caps.get("supergfxctl") and gpu_mode is None
                     else None)
        return {
            "gpu_mode": gpu_mode,
            "supergfxd": supergfxd,
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
            # Three separate readings, because they can all three differ. The
            # config says what the next boot will do, /proc/cmdline says what
            # this one is doing, and a foreign dcdebugmask says the switch has
            # no business acting at all. Sampled rather than read once because
            # a package upgrade can regenerate the bootloader config under a
            # running window.
            "psr_pending": (hardware.read_psr_disabled_pending()
                            if self.caps.get("psr_toggle") else None),
            "psr_live": (hardware.read_psr_disabled_live()
                         if self.caps.get("psr_toggle") else None),
            "psr_foreign": (hardware.psr_foreign_dcdebugmask()
                            if self.caps.get("psr_toggle") else None),
        }

    def _on_sample(self, data, error):
        self._sampling = False
        if error is None:
            self._render(data)

    def _render(self, data):
        self._render_supergfx(data.get("gpu_mode"),
                              data.get("gpu_mode_error"),
                              data.get("supergfxd"))
        self._render_asusd(data.get("asusd") or {})
        self._render_sync(data.get("power_mode"))
        self._render_boot_sound(data.get("boot_sound"))
        self._render_panel_od(data.get("panel_od"))
        self._render_psr(data.get("psr_pending"), data.get("psr_live"),
                         data.get("psr_foreign"))

    def _render_supergfx(self, mode, error, state=None):
        """Four states, said apart: absent, installed but switched off,
        present but silent, working.

        "Not installed", "installed but not answering" and "installed and
        simply not switched on" are three different problems with three
        different fixes, and collapsing them into one dash is what makes a
        greyed-out picker look like a missing feature. Only the third of them
        is something this window can fix by pressing a button, so only that
        one shows the button."""
        if not self.caps.get("supergfxctl"):
            self.supergfx_enable_row.set_visible(False)
            self.supergfx_value.set_text("not installed")
            self.supergfx_row.set_subtitle(SUPERGFX_ABSENT)
            self._supergfx_css("warning")
            return
        if mode is None:
            # The daemon is not answering. Whether that is because it was
            # never switched on or because it is broken decides both what
            # the row says and whether there is anything to press.
            # has_unit, not installed: without a unit file there is nothing
            # for systemctl to enable, and a button whose command is certain
            # to fail is worse than no button. That case -- the binary built
            # by hand, no unit installed -- falls through to "not answering",
            # which is what it is.
            stopped = bool(state and state.get("has_unit")
                           and not state.get("active"))
            self.supergfx_enable_row.set_visible(stopped)
            self.supergfx_enable_button.set_sensitive(
                stopped and not self._supergfx_busy)
            self.supergfx_value.set_text(
                "stopped" if stopped else "not answering")
            self.supergfx_row.set_subtitle(
                (SUPERGFX_STOPPED if stopped else SUPERGFX_SILENT)
                + (f"\n\n{error}" if error else ""))
            self._supergfx_css("warning")
            return
        self.supergfx_enable_row.set_visible(False)
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

    # -- panel self-refresh --------------------------------------------------

    def _render_psr(self, pending, live, foreign):
        """The switch shows the config; the row below shows the disagreement.

        The switch follows ``pending`` and not ``live`` because pending is
        what the user last asked for, and a switch that snapped back to the
        running kernel's value would look like the change had been refused
        when it had in fact been made and was waiting on a reboot. The
        Pending row carries that distinction instead, in words."""
        if not self.caps.get("psr_toggle") or self._psr_busy:
            return
        if foreign:
            # Somebody else's setting. Say whose shape it is and stop, rather
            # than leaving an enabled switch whose every use comes back
            # refused by the helper.
            self.psr_row.set_sensitive(False)
            self.psr_row.set_subtitle(PSR_FOREIGN.format(foreign))
            self.psr_pending_row.set_visible(False)
            return
        self.psr_row.set_sensitive(True)
        self.psr_row.set_subtitle(PSR_SUBTITLE)
        if pending is None:
            return
        was_loading = self._loading
        self._loading = True
        try:
            # Inverted on purpose: the parameter's presence means PSR is off,
            # and a switch labelled "panel self-refresh" that was on when the
            # feature was disabled would be a switch showing the opposite of
            # what it says.
            self.psr_row.set_active(not pending)
        finally:
            self._loading = was_loading
        if live is None or live == pending:
            self.psr_pending_row.set_visible(False)
            return
        self.psr_pending_row.set_subtitle(
            PSR_PENDING_OFF if pending else PSR_PENDING_ON)
        self.psr_pending_row.set_visible(True)

    def _on_psr(self, row, _param):
        if self._loading or self._psr_busy:
            return
        # The switch is "panel self-refresh", so off is what the helper calls
        # disabled. One inversion, in one place, right next to the one in
        # _render_psr that has to match it.
        disabled = not row.get_active()
        self._psr_busy = True
        row.set_sensitive(False)
        self.psr_row.set_subtitle("Writing the bootloader configuration…")
        self.window.apply_async(
            lambda: hardware.set_psr_disabled(disabled),
            lambda result, error: self._on_psr_set(disabled, result, error))

    def _on_psr_set(self, disabled, result, error):
        self._psr_busy = False
        self.psr_row.set_sensitive(True)
        self.psr_row.set_subtitle(PSR_SUBTITLE)
        ok, message = (False, str(error)) if error is not None else result
        if not ok:
            # The helper puts the configuration back itself before reporting a
            # failure, so there is nothing to undo here -- only a switch to
            # put where the configuration actually is, which _refresh_now
            # does.
            self.window.toast(f"Panel self-refresh change failed: {message}")
            self._refresh_now()
            return
        # Not saved into the config the way the chime and overdrive are: this
        # lives in the bootloader, which survives everything this app could
        # re-assert it from, and a boot-apply service pushing it again every
        # boot would mean regenerating the bootloader config on every boot.
        self._refresh_now()
        self._ask_to_reboot_for_psr(disabled)

    def _ask_to_reboot_for_psr(self, disabled):
        """Offer the reboot, rather than take it.

        The same shape as the GPU page's MUX reboot prompt and for the same
        reason: nothing a running system does applies a kernel parameter, so
        "done" here is a promise about the next boot and the user is the one
        who decides when that is."""
        dialog = Adw.AlertDialog(
            heading="Reboot to apply?",
            body=("Panel self-refresh is turned off in the bootloader "
                  "configuration. It stays on until the machine reboots, so a "
                  "graphics-mode switch can still freeze until then."
                  if disabled else
                  "Panel self-refresh is turned back on in the bootloader "
                  "configuration. It takes effect at the next boot."))
        dialog.add_response("later", "Later")
        dialog.add_response("reboot", "Reboot now")
        dialog.set_response_appearance("reboot",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("later")
        dialog.set_close_response("later")
        dialog.connect("response", self._on_psr_reboot_response)
        dialog.present(self)

    def _on_psr_reboot_response(self, _dialog, response):
        if response != "reboot":
            self.window.toast("Saved — it takes effect at the next reboot.")
            return
        ok, message = hardware.reboot_system()
        if not ok:
            self.window.toast(f"Could not reboot: {message}")

    def _on_check_asusd(self, _button):
        self._refresh_now()
        self.window.toast("Checked asusd.")

    def _on_supergfx_enable(self, _button):
        """Switch supergfxd on, through the helper.

        Off the main loop: this is systemctl enable --now, which does not
        return until the daemon has actually started."""
        if self._supergfx_busy:
            return
        self._supergfx_busy = True
        self.supergfx_enable_button.set_sensitive(False)
        self.window.toast("Enabling supergfxd…")
        self.window.apply_async(hardware.set_supergfxd_running,
                                self._on_supergfx_enabled)

    def _on_supergfx_enabled(self, result, error):
        self._supergfx_busy = False
        ok, message = (False, str(error)) if error is not None else result
        if ok:
            self.window.toast("supergfxd enabled and started — the graphics "
                              "mode picker on the GPU page can switch now.")
            # The GPU page's picker was built against a daemon that was not
            # answering; it has to be rebuilt to become usable, which is what
            # the profile-switch path does after it moves the hardware.
            self.window.reload_pages()
        else:
            self.window.toast(f"Could not enable supergfxd: {message}")
        # Ask systemd rather than assuming the button worked.
        self._refresh_now()

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

    # -- fan boost -------------------------------------------------------
    #
    # The hold is a DEADLINE ON DISK, not a timer in this window. See
    # hardware.FAN_BOOST_STATE_PATH for the whole account; the short version
    # is that both halves of the undo used to live here -- a GLib countdown
    # and a paused enforcer -- so closing the window mid-hold left the fans
    # pinned at 85% and the enforcer switched off until the next login.
    #
    # Now the enforcer maintains the boost while it runs and ends it when the
    # deadline passes, whether or not anything is on screen. What is left
    # here is the button, the countdown label, and an immediate restore while
    # the window happens to be open -- none of which the feature depends on.

    def set_hardware_busy(self, busy):
        """Something else is writing the machine -- see app.claim_hardware.

        Never re-enables the button during a hold: the boost owns it until
        the deadline passes, long after the write that started it released
        the hardware."""
        if not hasattr(self, "fan_boost_button"):
            return
        self.fan_boost_button.set_sensitive(not busy and not self._boost_active)

    def _on_fan_boost_clicked(self, _button):
        if self._boost_active or not self.window.caps.get("fan_curve"):
            return
        # Only the write is claimed, not the whole two-minute hold: once the
        # flat curves are down the enforcer maintains them, and a profile
        # switch mid-hold is a thing the user is allowed to do.
        if not self.window.claim_hardware("boosting the fans"):
            return
        # Captured now, not read back when the hold ends: the profile can
        # switch itself mid-hold (the enforcer does it on AC/battery), and
        # the one to put back is the one that was running when the button
        # was pressed. See config.deferred_save_target for the same concern
        # on the fans page's own Apply.
        self._boost_profile = self.window.current_profile_name()
        self._boost_active = True
        self.fan_boost_button.set_sensitive(False)
        self.fan_boost_row.set_subtitle(
            f"Setting every fan to {FAN_BOOST_PCT}%…")
        self.window.apply_async(self._fan_boost_worker,
                                self._on_fan_boost_written)

    def _fan_boost_worker(self):
        """Worker thread: record the deadline, then hold every channel flat.

        The record goes down FIRST, before a single curve reaches the
        hardware. It is what stops the enforcer putting the profile's curve
        back on its next pass -- and, more importantly, it is what ends the
        boost if this process dies between here and the end of the hold. A
        boost the fans took but nothing recorded is the stuck-fans case this
        whole design exists to prevent.

        Same shape as pages/fans.py's _write_flat -- a flat curve at every
        sampled temperature -- and the same CHANNEL_GAP_S-style wait between
        channels, because the embedded controller drops a curve write fired
        too soon after the last one.

        The enforcer is deliberately NOT stopped any more: it reads the same
        record and pushes the same flat curve, so there is nothing left for
        the two of them to disagree about -- and it goes on re-asserting the
        curve when the EC drops it, which during a boost is exactly what
        should happen."""
        state = hardware.write_fan_boost(FAN_BOOST_PCT, FAN_BOOST_SECONDS,
                                         self._boost_profile)
        curves = hardware.fan_boost_curves(FAN_BOOST_PCT)
        results = []
        for i, channel in enumerate(hardware.FAN_CHANNELS):
            if i > 0:
                time.sleep(FAN_BOOST_CHANNEL_GAP_S)
            flat = fancurve.curve_to_flat(curves[channel], 8)
            ok, message = hardware.run_helper("fan", channel, *flat)
            results.append((channel, ok, message))
        return {"results": results, "until": state["until"]}

    def _on_fan_boost_written(self, data, error):
        # Before either branch: both _fan_boost_abort and the countdown that
        # follows a success are done writing, and the abort path goes on to
        # ask for a profile apply, which needs the machine free to claim.
        self.window.release_hardware()
        if error is not None:
            self._fan_boost_abort(f"Fan boost failed: {error}")
            return
        failures = [f"{hardware.FAN_LABELS[ch]}: {message}"
                   for ch, ok, message in data["results"] if not ok]
        if failures:
            self._fan_boost_abort("Fan boost failed: " + "; ".join(failures))
            return
        self._start_boost_countdown(data["until"])
        self.window.toast(
            f"Fans holding at {FAN_BOOST_PCT}% for "
            f"{FAN_BOOST_SECONDS // 60} minutes — this carries on if you "
            f"close the window.")

    def _start_boost_countdown(self, until):
        """Show the hold counting down. Only ever the label.

        ``until`` is wall-clock, because it came from a file another process
        also reads -- monotonic clocks are per boot and not comparable
        across processes that may have started at different times."""
        self._boost_active = True
        self._boost_deadline = until
        self.fan_boost_button.set_sensitive(False)
        self._fan_boost_tick()
        if self._boost_countdown_id is None:
            self._boost_countdown_id = GLib.timeout_add_seconds(
                1, self._fan_boost_tick)

    def resume_fan_boost(self):
        """Pick up a boost that was already running when this page was built.

        The window is no longer where the boost lives, so it can perfectly
        well be opened in the middle of one -- started before it was opened,
        or by an earlier window that has since been closed. Without this the
        page would offer a Boost button that starts a second one on top."""
        if self._boost_active or not hasattr(self, "fan_boost_row"):
            return
        state = hardware.read_fan_boost()
        if not hardware.fan_boost_active(state):
            return
        self._boost_profile = state.get("profile")
        self._start_boost_countdown(state["until"])

    def _fan_boost_tick(self):
        # Read back rather than trusted: the enforcer ends the boost too, and
        # if it got there first the record is already gone and the profile's
        # curve is already back on. Cancelling on its own is the only correct
        # response to that -- restoring a second time would cost another ten
        # seconds of fan writes for nothing.
        state = hardware.read_fan_boost()
        if state is None:
            self._boost_countdown_id = None
            self._finish_fan_boost()
            return GLib.SOURCE_REMOVE
        remaining = int(round(state["until"] - time.time()))
        if remaining <= 0:
            self._boost_countdown_id = None
            self._on_fan_boost_revert()
            return GLib.SOURCE_REMOVE
        self.fan_boost_row.set_subtitle(
            f"Holding at {FAN_BOOST_PCT}% — {remaining}s left")
        return GLib.SOURCE_CONTINUE

    def _on_fan_boost_revert(self):
        """The hold is over and this window is open, so restore it here.

        The enforcer would do it within a second or two anyway -- that is
        what makes closing the window safe -- but doing it here is instant
        and can say so on the page.

        The record is cleared FIRST. Until it is gone the enforcer treats the
        boost as live and would re-push the flat curve straight over the
        restore.

        Not a hand-rolled curve write -- apply_profile_async is the same push
        a profile switch does, so the fans end up wherever the rest of that
        profile's settings already are."""
        hardware.clear_fan_boost()
        self.fan_boost_row.set_subtitle("Restoring the active profile…")
        name = self._boost_profile or self.window.current_profile_name()
        self.window.apply_profile_async(name)
        self._finish_fan_boost()

    def _finish_fan_boost(self):
        self._boost_active = False
        self._boost_deadline = None
        # Not unconditionally back on: the hold usually ends with a profile
        # re-apply started immediately after this, and the button has to stay
        # out while that runs.
        self.fan_boost_button.set_sensitive(not self.window.hardware_busy())
        self.fan_boost_row.set_subtitle(FAN_BOOST_SUBTITLE)

    def _fan_boost_abort(self, message):
        """A failed write or a worker exception: put the button back rather
        than leaving it stuck on "Setting every fan to 85%…" forever.

        The record goes with it. It was written before the curves, so a write
        that failed leaves a deadline on disk for a boost that never
        happened -- which would have the enforcer hold the fans flat for two
        minutes on the strength of it.

        And the profile goes back on, because a failure here is usually a
        PARTIAL one: the channels are written one at a time, so "the GPU fan
        was refused" means the CPU fan is already sitting at 85% with nothing
        left to take it off. Clearing the record alone would not do it --
        the enforcer never applied the boost, so its own idea of what is on
        the hardware still says "the profile", and it would not re-push
        until its five-minute re-verify came round."""
        hardware.clear_fan_boost()
        self._finish_fan_boost()
        self.window.toast(message)
        name = self._boost_profile or self.window.current_profile_name()
        if name:
            self.window.apply_profile_async(name)

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
