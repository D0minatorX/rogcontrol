"""GPU page behavior for Supergfx."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from .. import hardware
from .gpu_base import (
    GpuPageBase, APPLY_ORDER, CAPABILITY, FAN_CHANNEL,
    MODE_ANSWER_SILENT_FAIL, MODE_ANSWER_SILENT_OK,
)

# -- Graphics mode -----------------------------------------------------------
#
# This section used to live on the System page. It belongs here: which GPU
# the screen is plugged into is a fact about the graphics card, and the
# controls that depend on it -- power limit, temperature target, Dynamic
# Boost -- are all on this page.

GPU_MODE_SUBTITLE = "Ends the session"

GPU_MODE_TOOLTIP = (
    "Integrated turns the NVIDIA card off entirely for battery life; hybrid "
    "leaves it available for the applications that ask for it; AsusMuxDgpu "
    "wires the display straight to it.\n\n"
    "Switching between Integrated and Hybrid restarts the display stack. "
    "Switching into or out of AsusMuxDgpu moves the hardware MUX, which only "
    "the firmware can do, so that one needs a reboot."
)

# One short line each. The full explanation is on the row's tooltip: six
# lines of prose under the picker pushed the drop-down itself so narrow that
# the mode it was showing read "Asu...".
GPU_MODE_DESCRIPTIONS = {
    "Integrated": "NVIDIA card off — best battery",
    "Hybrid": "NVIDIA card wakes on demand",
    "NvidiaNoModeset": "NVIDIA loaded without modesetting",
    "Vfio": "NVIDIA card bound to vfio for a VM",
    "AsusEgpu": "An external GPU drives the display",
    "AsusMuxDgpu": "Display wired straight to NVIDIA — fastest",
}

# The row that repeats supergfxd's reply to a switch, word for word. A toast
# is gone in five seconds and a refusal is the thing you most want to still
# be able to read.
MODE_ANSWER_TITLE = "supergfxd's answer"

NO_DAEMON_SUBTITLE = (
    "supergfxctl is installed but supergfxd is not answering, so the current "
    "mode cannot be read and nothing can be switched. Check the service with "
    "systemctl status supergfxd."
)

# How long the reboot dialog leaves between OK and the reboot itself. Long
# enough to notice it is happening and pull the plug on it with Ctrl+Alt+F2
# if it was pressed by accident; short enough not to look stuck.
REBOOT_DELAY_SECONDS = 5

# How long after an accepted switch the mode is read back to see whether it
# actually happened. Long enough to cover supergfxd's own teardown (measured
# at 13 seconds for the failing Integrated attempt on 2026-09-04: unload
# drivers, unbind and remove the card, restart the display manager), short
# enough that the user is still looking at the page.
MODE_VERIFY_SECONDS = 20


class GpuPage(GpuPageBase):
    """Supergfx graphics mode and session transitions."""

    def _build_gpu_mode(self):
        group = Adw.PreferencesGroup(title="Graphics mode")

        # No "Current mode" row: the picker below is a drop-down showing the
        # mode in force, so a row above it stating the same name twice was
        # only taking height. What the mode *means* moved onto the picker's
        # own subtitle, which is the row that was already there.

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
        # One line, not unlimited: the subtitle is a few words now, and an
        # unbounded one is what let a paragraph grow under the picker and
        # squeeze the drop-down until it showed "Asu..." instead of the mode.
        self.mode_row.set_subtitle_lines(1)
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
        return group

    def _apply_capability_gating(self):
        """Hide what this machine cannot do.

        A control for a setting this machine cannot act on does not belong
        on the page at all -- see the CPU page's version of this method for
        the fuller reasoning."""
        if not self.caps.get("gpu_clock_limit"):
            self.rows["clock_limit"].set_visible(False)
        if not self.caps.get("nvidia"):
            self.temp_cell.set_note("nvidia-smi is not installed.")
        if not self.caps.get("gpu_power_limit"):
            self.rows["watts"].set_visible(False)
        if not self.caps.get("fan_rpm"):
            # The tachometer is on the asus hwmon, not the card, so it can be
            # missing on a machine whose GPU controls all work.
            self.fan_cell.set_note("No asus hwmon fan reading on this "
                                   "machine.")
        for key in ("clock_offset", "mem_clock_offset"):
            if not self.caps.get(CAPABILITY[key]):
                self.rows[key].set_visible(False)
            kind = "core" if key == "clock_offset" else "memory"
            limits = self.caps.get("gpu_offset_limits", {}).get(kind, {})
            if limits.get("ok"):
                was_loading = self._loading
                self._loading = True
                try:
                    adj = self.rows[key].get_adjustment()
                    adj.set_lower(max(-1000, limits["minimum"]))
                    adj.set_upper(min(1000, limits["maximum"]))
                finally:
                    self._loading = was_loading
        if not self.caps.get("nvidia_voltage_boost"):
            self.rows["voltage_boost"].set_visible(False)
        if not self.caps.get("nv_dynamic_boost"):
            self.rows["dyn_boost"].set_visible(False)
        if not self.caps.get("nv_temp_target"):
            self.rows["temp_target"].set_visible(False)
        # Both groups can end up with nothing left in them -- a machine
        # with no NVIDIA card and no ASUS power knobs, say -- and an empty
        # titled group left standing says nothing a missing one would not.
        if not any(self.rows[key].get_visible()
                  for key in ("watts", "dyn_boost", "temp_target")):
            self.power_group.set_visible(False)
        if not any(self.rows[key].get_visible()
                  for key in ("clock_limit", "clock_offset", "mem_clock_offset",
                              "voltage_boost")):
            self.clocks_group.set_visible(False)

    def _sample(self):
        """Worker thread: the card's own numbers and the fan cooling it.

        Both, always, in one pass -- the fan is a sysfs read that costs
        nothing next to the nvidia-smi call, and a machine with no NVIDIA
        card still has a fan reading worth showing. VRAM lives on the
        Overview page's GPU section, not here -- see overview.py."""
        # Asked first, and it decides whether nvidia-smi runs at all: that
        # call wakes the card to answer it, so polling it every two seconds
        # would hold the dGPU awake for as long as this page is open. On a
        # hybrid machine that is both the wrong reading -- the card is never
        # seen idle -- and a real cost in battery.
        suspended = hardware.dgpu_is_suspended()
        return {
            "dgpu_suspended": suspended,
            "nvidia": (hardware.read_nvidia_stats()
                       if self.caps.get("nvidia") and not suspended
                       else (None, None)),
            "fan_rpm": hardware.read_fan_rpms().get(FAN_CHANNEL),
            "mode": (hardware.read_gpu_mode()
                     if self.caps.get("supergfxctl") else None),
            "modes": (hardware.read_supported_gpu_modes()
                      if self.caps.get("supergfxctl") else []),
        }

    def _render_backend_mode(self, data):
        if not self._switching:
            self.current_mode = data.get("mode")
            self._render_modes(data.get("modes") or [], self.current_mode)

    # -- graphics mode -------------------------------------------------------

    def _render_modes(self, supported, active):
        """Fill the picker without letting -s decide what is in it.

        See hardware.gpu_mode_choices: what the daemon lists is what it will
        take in the state it is in, which on a machine sitting in AsusMuxDgpu
        is that one mode. Filtering by it is what left this picker unable to
        switch anything."""
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
        # The picker names the mode; its subtitle says what that mode means,
        # so the description is not a second row.
        if active:
            self.mode_row.set_subtitle(GPU_MODE_DESCRIPTIONS.get(
                active, GPU_MODE_SUBTITLE))
        if not self.caps.get("supergfxctl"):
            return
        # Set both ways round, not just off: supergfxd can be restarted under
        # a running window, and a row latched insensitive on one sample would
        # never come back.
        if active is None:
            self._block_switching(NO_DAEMON_SUBTITLE)
            return
        self._allow_switching()

    def _on_mode_changed(self, row, _param):
        if self._loading or self._switching:
            return
        item = row.get_selected_item()
        if item is None:
            return
        mode = item.get_string()
        if hardware.mode_needs_hybrid_first(self.current_mode, mode):
            # Refused, not attempted. supergfxd would take this happily,
            # store Integrated, and power down the card the panel is wired
            # to -- then re-apply it at every login. That is the freeze this
            # machine spent three boots in.
            self._offer_hybrid_first(mode)
            return
        needs_reboot = hardware.mode_change_needs_reboot(self.current_mode,
                                                         mode)
        # Asked, never assumed: either answer ends the session. The picker is
        # put back first, so declining leaves the row showing what is
        # actually running rather than the mode that was not switched to.
        body = ("This moves the hardware MUX, which only the firmware can "
                "do, so the machine has to reboot to finish it."
                if needs_reboot else
                "This restarts the display stack. You will be logged out and "
                "anything unsaved in any application will be lost.")
        dialog = Adw.AlertDialog(
            heading=f"Switch graphics mode to {mode}?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("switch", f"Switch to {mode}")
        dialog.set_response_appearance("switch",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_mode_response, mode)
        dialog.present(self)

    def _offer_hybrid_first(self, mode):
        """Integrated cannot be reached directly from the MUX mode."""
        dialog = Adw.AlertDialog(
            heading="Switch to Hybrid first",
            body=f"{mode} powers the NVIDIA card down, but the hardware MUX "
                 f"still has your display wired to that card — so it cannot "
                 f"be done in one step, and doing it anyway freezes the "
                 f"session at every login.\n\n"
                 f"Switch to Hybrid first, which moves the MUX and needs a "
                 f"reboot. {mode} is available once the machine comes back.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("hybrid", "Switch to Hybrid")
        dialog.set_response_appearance("hybrid",
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("hybrid")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_hybrid_first_response)
        dialog.present(self)

    def _on_hybrid_first_response(self, _dialog, response):
        # Either way the picker goes back to what is running: it is showing
        # the mode that was asked for and refused.
        self._render_modes(self.supported_modes, self.current_mode)
        if response == "hybrid":
            self._on_mode_response(None, "switch", "Hybrid")

    def _on_mode_response(self, _dialog, response, mode):
        if response != "switch":
            self._render_modes(self.supported_modes, self.current_mode)
            return
        self._switching = True
        self.mode_row.set_sensitive(False)
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
        if not ok:
            self.window.toast(f"Graphics mode change failed: {message}")
            self._start_sample()
            return
        if self._switch_needs_reboot(mode):
            # The MUX flip is queued in firmware and applied at POST.
            # Nothing a running system does finishes it, so offering "log
            # out" here would send the user round a loop that cannot work.
            self._ask_to_reboot(mode)
            return
        self.window.toast(f"Graphics mode set to {mode}. "
                          f"Log out to finish switching.")
        self._start_sample()
        # And check that it stuck. supergfxd answers this call over D-Bus the
        # moment it accepts the request, then carries it out on its own
        # thread, so "accepted" is not "done" -- on 2026-09-04 it accepted
        # Integrated, failed on `rmmod nvidia: Module nvidia is in use`
        # seconds later, and came back up in Hybrid, while this page went on
        # showing the switch as successful.
        GLib.timeout_add_seconds(MODE_VERIFY_SECONDS,
                                 self._verify_mode_took, mode)

    def _switch_needs_reboot(self, mode):
        """Whether this accepted switch finishes at a reboot or at a logout.

        Three sources, most authoritative first, because getting this wrong
        costs the user a whole logout that changes nothing:

        1. supergfxd's config. always_reboot makes EVERY switch a reboot,
           Integrated/Hybrid included, and the app cannot infer that from the
           hardware -- it is a choice someone made in /etc/supergfxd.conf.
        2. supergfxd's pending action, when it has decided on one yet.
        3. The MUX, read from sysfs. True regardless of any daemon, and the
           answer when supergfxd is not installed at all.
        """
        if hardware.supergfxd_always_reboot():
            return True
        if hardware.read_gpu_pending_action() == hardware.PENDING_REBOOT:
            return True
        return hardware.mode_change_needs_reboot(self.current_mode, mode)

    def _verify_mode_took(self, mode):
        """Say so when supergfxd accepted a switch and then did not do it.

        Not a retry: a switch that failed halfway has already stopped the
        display manager and pulled the card off the PCI bus once, and doing
        that again unasked is how a wedged session becomes a lost one. The
        user is told what actually happened and left to decide."""
        actual = hardware.read_gpu_mode()
        if actual is None or actual == mode:
            return GLib.SOURCE_REMOVE
        self.current_mode = actual
        self._render_modes(self.supported_modes, actual)
        self._show_mode_answer(
            mode, False,
            f"supergfxd accepted {mode} but the mode is still {actual}. Its "
            f"own log says why: journalctl -u supergfxd -b")
        self.window.toast(f"Switch to {mode} did not complete — still "
                          f"{actual}.")
        return GLib.SOURCE_REMOVE

    def _ask_to_reboot(self, mode):
        dialog = Adw.AlertDialog(
            heading="Reboot to finish switching?",
            body=f"{mode} is set, and the hardware MUX changes at the next "
                 f"boot. The machine will restart in "
                 f"{REBOOT_DELAY_SECONDS} seconds.")
        dialog.add_response("later", "Later")
        dialog.add_response("reboot", "Reboot now")
        dialog.set_response_appearance("reboot",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("later")
        dialog.set_close_response("later")
        dialog.connect("response", self._on_reboot_response, mode)
        dialog.present(self)

    def _on_reboot_response(self, _dialog, response, mode):
        if response != "reboot":
            self.window.toast(f"{mode} is set — it takes effect at the next "
                              f"reboot.")
            self._start_sample()
            return
        self.window.toast(f"Rebooting in {REBOOT_DELAY_SECONDS} seconds…")
        GLib.timeout_add_seconds(REBOOT_DELAY_SECONDS, self._do_reboot)

    def _do_reboot(self):
        ok, message = hardware.reboot_system()
        if not ok:
            self.window.toast(f"Could not reboot: {message}")
        return GLib.SOURCE_REMOVE

    def _show_mode_answer(self, mode, ok, message):
        """Put supergfxd's reply on the page, word for word.

        Verbatim and not summarised: when the daemon refuses, its own
        wording is the only thing that says which of several reasons
        applied. A toast is gone in five seconds; this stays until the next
        attempt."""
        self.mode_answer_row.set_title(f"supergfxd's answer to {mode}")
        self.mode_answer_value.set_text("accepted" if ok else "refused")
        for css in ("success", "warning"):
            self.mode_answer_value.remove_css_class(css)
        self.mode_answer_value.add_css_class("success" if ok else "warning")
        self.mode_answer_row.set_subtitle(
            (message or "").strip()
            or (MODE_ANSWER_SILENT_OK if ok else MODE_ANSWER_SILENT_FAIL))
        self.mode_answer_row.set_visible(True)

    # -- applying ------------------------------------------------------------

    def _pending_values(self):
        """What the controls hold, for the settings this machine can write."""
        return [(key, int(self.rows[key].get_value())) for key in APPLY_ORDER
                if self.caps.get(CAPABILITY[key])]
