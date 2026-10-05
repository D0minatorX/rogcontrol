"""GPU page behavior for Cardwire."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from .. import hardware
from .gpu_base import (
    GpuPageBase, APPLY_ORDER, CAPABILITY, FAN_CHANNEL,
    MODE_ANSWER_SILENT_FAIL, MODE_ANSWER_SILENT_OK,
)

NVIDIA_ACCESS_KEYS = {
    "watts", "clock_limit", "clock_offset", "mem_clock_offset",
    "voltage_boost",
}

# -- Graphics mode -----------------------------------------------------------
#
# This section used to live on the System page. It belongs here: which GPU
# the screen is plugged into is a fact about the graphics card, and the
# controls that depend on it -- power limit, temperature target, Dynamic
# Boost -- are all on this page.

GPU_MODE_SUBTITLE = "Switches live — no logout required"

GPU_MODE_TOOLTIP = (
    "Integrated blocks new applications from accessing the discrete GPU; "
    "Hybrid leaves every GPU available; Smart blocks the discrete GPU by "
    "default and grants access to approved applications. Cardwire applies "
    "these policies live without logging out. Applications that are already "
    "running keep their existing GPU access until restarted."
)

# One short line each. The full explanation is on the row's tooltip: six
# lines of prose under the picker pushed the drop-down itself so narrow that
# the mode it was showing read "Asu...".
GPU_MODE_DESCRIPTIONS = {
    "Integrated": "NVIDIA blocked — best battery",
    "Hybrid": "All GPUs available",
    "Smart": "NVIDIA blocked except for approved applications",
    "Manual": "Control access to each GPU with Cardwire",
}

# The row that repeats cardwired's reply to a switch, word for word. A toast
# is gone in five seconds and a refusal is the thing you most want to still
# be able to read.
MODE_ANSWER_TITLE = "Cardwire's answer"

NO_DAEMON_SUBTITLE = (
    "cardwire is installed but cardwired is not answering, so the current "
    "mode cannot be read and nothing can be switched. Check the service with "
    "systemctl status cardwired."
)

X11_SUBTITLE = (
    "Cardwire only supports Wayland. Log out and choose a Wayland desktop "
    "session to switch GPU access modes.")

# How long after an accepted switch the mode is read back to see whether it
# actually happened. Cardwire switches policy live, so this only needs to
# allow its D-Bus state to settle.
MODE_VERIFY_SECONDS = 2


class GpuPage(GpuPageBase):
    """Cardwire live GPU access policy."""

    def _init_backend(self):
        self._nvidia_accessible = hardware.dgpu_available()

    def _build_gpu_mode(self):
        group = Adw.PreferencesGroup(title="GPU access mode")

        # No "Current mode" row: the picker below is a drop-down showing the
        # mode in force, so a row above it stating the same name twice was
        # only taking height. What the mode *means* moved onto the picker's
        # own subtitle, which is the row that was already there.

        # Why there is no picker, when there is no picker. A separate row and
        # not the ComboRow's own subtitle, because an insensitive row draws
        # its text dimmed -- and the one thing this text must be is readable.
        self.mode_blocked_row = Adw.ActionRow(title="Switch access mode")
        self.mode_blocked_row.set_subtitle_lines(0)
        self.mode_blocked_row.set_visible(False)
        group.add(self.mode_blocked_row)

        self.mode_row = Adw.ComboRow(title="Switch access mode",
                                     subtitle=GPU_MODE_SUBTITLE)
        self.mode_row.set_tooltip_text(GPU_MODE_TOOLTIP)
        # Start with Cardwire's normal laptop modes. The daemon's advertised
        # list is merged in when the first sample arrives.
        self.modes = hardware.gpu_mode_choices()
        self.mode_row.set_model(Gtk.StringList.new(self.modes))
        # One line, not unlimited: the subtitle is a few words now, and an
        # unbounded one is what let a paragraph grow under the picker and
        # squeeze the drop-down until it showed "Asu..." instead of the mode.
        self.mode_row.set_subtitle_lines(1)
        self.mode_row.connect("notify::selected", self._on_mode_changed)
        group.add(self.mode_row)

        # Empty until something has actually been switched, then cardwired's
        # reply verbatim -- an acceptance or, more usefully, its refusal.
        self.mode_answer_row, self.mode_answer_value = self._value_row(
            group, MODE_ANSWER_TITLE)
        self.mode_answer_row.set_visible(False)

        if not self.caps.get("cardwire"):
            self._block_switching(
                "Cardwire is not installed, so GPU access mode cannot be "
                "read or changed here. Install Cardwire and enable its "
                "cardwired service.")
        elif not self.caps.get("cardwire_wayland", True):
            self._block_switching(X11_SUBTITLE)
        return group

    def _apply_capability_gating(self):
        """Hide what this machine cannot do.

        A control for a setting this machine cannot act on does not belong
        on the page at all -- see the CPU page's version of this method for
        the fuller reasoning."""
        self.rows["watts"].set_visible(bool(self.caps.get("gpu_power_limit")))
        self.rows["clock_limit"].set_visible(bool(self.caps.get("gpu_clock_limit")))
        self.temp_cell.set_note(
            None if self.caps.get("nvidia")
            else "nvidia-smi is not installed.")
        if not self.caps.get("fan_rpm"):
            # The tachometer is on the asus hwmon, not the card, so it can be
            # missing on a machine whose GPU controls all work.
            self.fan_cell.set_note("No asus hwmon fan reading on this "
                                   "machine.")
        for key in ("clock_offset", "mem_clock_offset"):
            self.rows[key].set_visible(bool(self.caps.get(CAPABILITY[key])))
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
        self.rows["voltage_boost"].set_visible(
            bool(self.caps.get("nvidia_voltage_boost")))
        self.rows["dyn_boost"].set_visible(
            bool(self.caps.get("nv_dynamic_boost")))
        self.rows["temp_target"].set_visible(
            bool(self.caps.get("nv_temp_target")))
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
        else:
            self.clocks_group.set_visible(True)
        if any(self.rows[key].get_visible()
               for key in ("watts", "dyn_boost", "temp_target")):
            self.power_group.set_visible(True)
        self.set_nvidia_accessible(self._nvidia_accessible)

    def set_nvidia_accessible(self, accessible):
        """Enable direct NVIDIA controls only while Cardwire permits them."""
        self._nvidia_accessible = bool(accessible)
        for key in NVIDIA_ACCESS_KEYS:
            self.rows[key].set_sensitive(self._nvidia_accessible)

    def refresh_runtime_capabilities(self):
        """Update driver-derived controls without rebuilding the page."""
        limits = self.caps.get("gpu_limits") or hardware.default_gpu_limits()
        self.gpu_name = limits.get("name")
        self.min_w = limits.get("min_w", hardware.GPU_MIN_W_FALLBACK)
        self.max_w = limits.get("max_w", hardware.GPU_MAX_W_FALLBACK)
        self.clock_limit_max = limits.get(
            "clock_limit_max", hardware.CLOCK_LIMIT_FALLBACK_MAX)
        was_loading = self._loading
        self._loading = True
        try:
            watts = self.rows["watts"]
            watts.get_adjustment().set_lower(self.min_w)
            watts.get_adjustment().set_upper(self.max_w)
            watts.set_value_width_chars()
            watts.set_tooltip_text(
                f"The board power the card is allowed to draw. This card "
                f"reports {self.min_w}–{self.max_w} W.")
            ceiling = self.rows["clock_limit"]
            ceiling.get_adjustment().set_upper(self.clock_limit_max)
            ceiling.set_value_width_chars()
        finally:
            self._loading = was_loading
        self.status_group.set_description(
            self.gpu_name or "No NVIDIA card detected")
        self._nvidia_accessible = True
        self._apply_capability_gating()
        self.reload()

    def _sample(self):
        """Worker thread: the card's own numbers and the fan cooling it.

        Both, always, in one pass -- the fan is a sysfs read that costs
        nothing next to the nvidia-smi call, and a machine with no NVIDIA
        card still has a fan reading worth showing. VRAM lives on the
        Overview page's GPU section, not here -- see overview.py."""
        mode, modes = (hardware.read_cardwire_status()
                       if self.caps.get("cardwire") else (None, []))
        nvidia_accessible = (
            mode not in ("Integrated", "Smart")
            and hardware.nvidia_driver_loaded())
        # Asked before nvidia-smi, because that call wakes a suspended card.
        suspended = hardware.dgpu_is_suspended()
        return {
            "dgpu_suspended": suspended,
            # The Cardwire access check above is authoritative for this same
            # sample, so do not spawn a second `cardwire get` inside the
            # generic guarded query helper.
            "nvidia": (hardware.read_nvidia_stats(check_access=False)
                       if self.caps.get("nvidia") and nvidia_accessible
                       and not suspended
                       else (None, None)),
            "fan_rpm": hardware.read_fan_rpms().get(FAN_CHANNEL),
            "mode": mode,
            "modes": modes,
            "nvidia_accessible": nvidia_accessible,
        }

    def _render_backend_mode(self, data):
        if not self._switching:
            previous = self.current_mode
            self.current_mode = data.get("mode")
            self._render_modes(data.get("modes") or [], self.current_mode)
            accessible = bool(data.get("nvidia_accessible"))
            self.set_nvidia_accessible(accessible)
            if (self.current_mode != previous
                    or (self.current_mode == "Hybrid" and accessible
                        and not self.window._gpu_runtime_ready)):
                self.window.gpu_mode_changed(
                    previous, self.current_mode, accessible)

    # -- graphics mode -------------------------------------------------------

    def _render_modes(self, supported, active):
        """Fill the picker from Cardwire's normal and advertised modes."""
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
        if not self.caps.get("cardwire"):
            return
        if not self.caps.get("cardwire_wayland", True):
            self._block_switching(X11_SUBTITLE)
            return
        # Set both ways round, not just off: cardwired can be restarted under
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
        body = ("Cardwire applies this immediately without logging out. "
                "Applications that are already running keep their current "
                "GPU access until you restart those applications.")
        dialog = Adw.AlertDialog(
            heading=f"Switch GPU access mode to {mode}?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("switch", f"Switch to {mode}")
        dialog.set_response_appearance("switch",
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_mode_response, mode)
        dialog.present(self)

    def _on_mode_response(self, _dialog, response, mode):
        if response != "switch":
            self._render_modes(self.supported_modes, self.current_mode)
            return
        self._switching = True
        self.mode_row.set_sensitive(False)
        self.mode_answer_row.set_visible(False)
        self.window.toast(f"Switching GPU access mode to {mode}…")
        self.window.apply_async(
            lambda: hardware.set_gpu_mode(mode),
            lambda result, error: self._on_mode_applied(mode, result, error))

    def _on_mode_applied(self, mode, result, error):
        self._switching = False
        self.mode_row.set_sensitive(True)
        ok, message = (False, str(error)) if error is not None else result
        self._show_mode_answer(mode, ok, message)
        if not ok:
            self.window.toast(f"GPU access mode change failed: {message}")
            self._start_sample()
            return
        self.window.toast(f"GPU access mode set to {mode} — no logout needed.")
        self._start_sample()
        # Cardwire replies over D-Bus; read it back shortly afterward so the
        # picker reflects the daemon's authoritative state.
        GLib.timeout_add_seconds(MODE_VERIFY_SECONDS,
                                 self._verify_mode_took, mode)

    def _verify_mode_took(self, mode):
        """Say so when Cardwire accepted a switch and did not retain it."""
        actual = hardware.read_gpu_mode()
        if actual is None or actual == mode:
            return GLib.SOURCE_REMOVE
        self.current_mode = actual
        self._render_modes(self.supported_modes, actual)
        self._show_mode_answer(
            mode, False,
            f"Cardwire accepted {mode} but the mode is still {actual}. Its "
            f"own log says why: journalctl -u cardwired -b")
        self.window.toast(f"Switch to {mode} did not complete — still "
                          f"{actual}.")
        return GLib.SOURCE_REMOVE

    def _show_mode_answer(self, mode, ok, message):
        """Put Cardwire's reply on the page, word for word.

        Verbatim and not summarised: when the daemon refuses, its own
        wording is the only thing that says which of several reasons
        applied. A toast is gone in five seconds; this stays until the next
        attempt."""
        self.mode_answer_row.set_title(f"Cardwire's answer to {mode}")
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
                if self.caps.get(CAPABILITY[key])
                and (key not in NVIDIA_ACCESS_KEYS
                     or self._nvidia_accessible)]
