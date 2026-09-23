"""The controls users reach for most often.

Quick Access owns the visible rows, but not their behavior. The rows are
reparented from the detailed pages after those pages have built themselves,
so their existing signal handlers, busy states, reload paths and safety
dialogs remain the single implementation of each hardware action.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from .. import config as config_mod  # noqa: E402
from .. import hardware  # noqa: E402


class QuickAccessPage(Adw.PreferencesPage):
    """Relocate the frequently used controls into three compact groups."""

    def __init__(self, window, pages):
        super().__init__()
        self.window = window
        self.pages = pages

        self.performance_group = Adw.PreferencesGroup(title="Performance")
        self.add(self.performance_group)
        self.profile_group = Adw.PreferencesGroup(
            title="Automatic profile switching")
        self.add(self.profile_group)
        self.firmware_group = Adw.PreferencesGroup(
            title="Display and firmware")
        self.add(self.firmware_group)
        self._moved_counts = {
            self.performance_group: 0,
            self.profile_group: 0,
            self.firmware_group: 0,
        }
        self._powermizer_loading = False

        self._move_performance_controls()
        self._move_profile_controls()
        self._move_firmware_controls()
        self._hide_empty_groups()
        # A row moved out of a PreferencesGroup keeps its last allocation in
        # GTK until the destination is mapped again. Quick Access is a stack
        # child, so returning to it can otherwise paint the first rows using
        # the source group's old geometry (the title/subtitle overlap seen
        # after switching pages). Refresh the page and each destination group
        # whenever the stack maps us back in.
        self.connect("map", self._on_map)

    def _on_map(self, *_args):
        """Invalidate allocations after returning to the visible page."""
        self.queue_resize()
        for group in (self.performance_group, self.profile_group,
                      self.firmware_group):
            group.queue_resize()

    def reload(self):
        """Follow profile switches for the one Quick Access-owned row."""
        if (self.powermizer_row is not None
                and self.powermizer_row.get_visible()):
            self._restore_powermizer_selection()

    def refresh_gpu_capabilities(self, accessible):
        """Refresh the runtime-only PowerMizer row after a Cardwire switch."""
        modes = tuple(self.window.caps.get("nvidia_powermizer_modes") or ())
        if not modes:
            if self.powermizer_row is not None:
                self.powermizer_row.set_visible(False)
            return
        if self.powermizer_row is None:
            self._build_powermizer_control()
        else:
            self._powermizer_modes = modes
            self.powermizer_row.set_model(Gtk.StringList.new(
                [hardware.NVIDIA_POWERMIZER_MODES[mode] for mode in modes]))
            self._restore_powermizer_selection()
        self.powermizer_row.set_visible(True)
        self.powermizer_row.set_sensitive(bool(accessible))

    def _move(self, row, destination):
        """Move one existing row without changing its signal handlers."""
        if row is None:
            return False
        source = row.get_ancestor(Adw.PreferencesGroup)
        if source is not None:
            # PreferencesGroup keeps its rows in an internal model. Calling
            # unparent() only removes the widget from the listbox and leaves
            # that model holding a second owner, which corrupts row geometry
            # when the Quick Access page is mapped again.
            source.remove(row)
        else:
            row.unparent()
        destination.add(row)
        self._moved_counts[destination] += 1
        return True

    def _move_performance_controls(self):
        cpu = self.pages.get("cpu")
        if self.window.caps.get("cpu_boost") and cpu is not None:
            self._move(cpu.rows.get("boost"), self.performance_group)

        gpu = self.pages.get("gpu")
        if self.window.caps.get("cardwire") and gpu is not None:
            for name in ("mode_blocked_row", "mode_row", "mode_answer_row"):
                self._move(getattr(gpu, name, None), self.performance_group)
            group = getattr(gpu, "mode_group", None)
            if group is not None:
                group.set_visible(False)

        system = self.pages.get("system")
        if self.window.caps.get("fan_curve") and system is not None:
            self._move(getattr(system, "fan_boost_row", None),
                       self.performance_group)
            group = getattr(system, "fan_boost_group", None)
            if group is not None:
                group.set_visible(False)

        self._build_powermizer_control()

    def _build_powermizer_control(self):
        """Create the one Quick Access-only GPU performance control.

        Unlike the surrounding rows this has no detailed-page counterpart:
        PowerMizer is intentionally immediate, while the GPU tuning page is
        a staged Apply/Revert workflow.  It appears only when the active GPU
        advertised this attribute and its actual supported values.
        """
        modes = tuple(self.window.caps.get("nvidia_powermizer_modes") or ())
        if not modes:
            self.powermizer_row = None
            return
        self._powermizer_modes = modes
        self.powermizer_row = Adw.ComboRow(
            title="GPU PowerMizer",
            subtitle="GPU clock behavior for this profile")
        self.powermizer_row.set_model(Gtk.StringList.new(
            [hardware.NVIDIA_POWERMIZER_MODES[mode] for mode in modes]))
        gpu = (self.window.current_profile() or {}).get("gpu") or {}
        saved = gpu.get("powermizer_mode", hardware.NVIDIA_POWERMIZER_AUTO)
        selected = modes.index(saved) if saved in modes else self._auto_index()
        self.powermizer_row.set_selected(selected)
        self.powermizer_row.connect("notify::selected", self._on_powermizer_changed)
        self.performance_group.add(self.powermizer_row)
        self._moved_counts[self.performance_group] += 1

    def _auto_index(self):
        if hardware.NVIDIA_POWERMIZER_AUTO in self._powermizer_modes:
            return self._powermizer_modes.index(hardware.NVIDIA_POWERMIZER_AUTO)
        return 0

    def _on_powermizer_changed(self, row, _pspec):
        if self._powermizer_loading:
            return
        selected = row.get_selected()
        if selected < 0 or selected >= len(self._powermizer_modes):
            return
        if not self.window.claim_hardware("applying GPU PowerMizer"):
            self._restore_powermizer_selection()
            return
        mode = self._powermizer_modes[selected]
        profile_name = self.window.current_profile_name()
        self.window.apply_async(
            lambda: hardware.set_nvidia_powermizer_mode(mode),
            lambda result, error: self._on_powermizer_applied(
                profile_name, mode, result, error))

    def _restore_powermizer_selection(self):
        gpu = (self.window.current_profile() or {}).get("gpu") or {}
        mode = gpu.get("powermizer_mode", hardware.NVIDIA_POWERMIZER_AUTO)
        self._powermizer_loading = True
        try:
            self.powermizer_row.set_selected(
                self._powermizer_modes.index(mode)
                if mode in self._powermizer_modes else self._auto_index())
        finally:
            self._powermizer_loading = False

    def _on_powermizer_applied(self, profile_name, mode, result, error):
        self.window.release_hardware()
        ok, message = result if error is None else (False, str(error))
        profile = (self.window.config.get("profiles") or {}).get(profile_name)
        if ok and profile is not None:
            profile.setdefault("gpu", {})["powermizer_mode"] = mode
            config_mod.save_config(self.window.config)
            self.window.toast("GPU PowerMizer mode applied.")
            return
        self._restore_powermizer_selection()
        if message == hardware.CARDWIRE_BLOCKED_MESSAGE:
            self.window.toast("GPU PowerMizer deferred — switch to Hybrid "
                              "mode to change it.")
            return
        self.window.toast(f"GPU PowerMizer mode failed: {message}")

    def _move_profile_controls(self):
        battery = self.pages.get("battery")
        if battery is None:
            return
        for source in ("ac", "battery", "usbc"):
            self._move(battery.combos.get(source), self.profile_group)
        group = getattr(battery, "switching_group", None)
        if group is not None:
            group.set_visible(False)

    def _move_firmware_controls(self):
        system = self.pages.get("system")
        if system is None:
            return

        if self.window.caps.get("boot_sound"):
            self._move(getattr(system, "boot_sound_row", None),
                       self.firmware_group)
        if self.window.caps.get("panel_od"):
            self._move(getattr(system, "panel_od_row", None),
                       self.firmware_group)
        if self.window.caps.get("psr_toggle"):
            self._move(getattr(system, "psr_row", None),
                       self.firmware_group)
            self._move(getattr(system, "psr_pending_row", None),
                       self.firmware_group)

        group = getattr(system, "firmware_group", None)
        if group is not None:
            group.set_visible(False)
        group = getattr(system, "psr_group", None)
        if group is not None:
            group.set_visible(False)

    def _hide_empty_groups(self):
        for group in (self.performance_group, self.profile_group,
                      self.firmware_group):
            group.set_visible(self._moved_counts[group] > 0)
