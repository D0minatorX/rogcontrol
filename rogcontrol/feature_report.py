"""Installer-facing view of the same feature probes used by the window.

The installer invokes this on every run, whether it is a fresh install,
update, or same-version repair. Profiles are never changed by the report.
"""

from . import display_refresh, graphics_backend, hardware


LABELS = {
    "gamescope": "Gamescope installed",
    "fan_curve": "Fan curves",
    "fan_rpm": "Fan RPM readout",
    "cpu_temp": "CPU temperature",
    "pkg_power": "CPU package power",
    "nv_temp_target": "GPU temperature target",
    "nv_dynamic_boost": "GPU Dynamic Boost",
    "boot_sound": "Boot sound",
    "panel_od": "Panel overdrive",
    "fw_power_reset": "Firmware power reset",
    "psr_toggle": "Panel self refresh toggle",
    "nvidia": "NVIDIA GPU monitoring and clocks",
    "gpu_power_limit": "GPU power limit",
    "gpu_clock_limit": "GPU clock ceiling",
    "nvidia_core_clock_offset": "GPU core clock offset",
    "nvidia_memory_clock_offset": "GPU memory clock offset",
    "nvidia_settings": "nvidia-settings installed",
    "supergfxctl": "supergfxctl installed",
    "cardwire": "Cardwire installed",
    "cardwire_wayland": "Wayland for Cardwire",
    "gpu_mode_switching": "Selected GPU mode switching",
    "rogauracore": "Keyboard RGB controller",
    "ryzenadj": "AMD CPU power and undervolt",
    "cpu_ppt": "ASUS CPU PL1/PL2",
    "cpu_rapl_limits": "CPU RAPL power limits",
    "cpu_power_limits": "CPU power limit backend",
    "cpu_boost": "CPU boost",
    "cpu_epp": "CPU energy preference",
    "cpu_clock": "CPU clock ceiling",
    "cpu_clock_floor": "CPU clock floor",
    "kbd_backlight": "Keyboard backlight",
    "kbd_rgb": "Keyboard RGB modes",
    "kbd_rgb_zones": "Keyboard RGB zones",
    "kbd_battery": "Battery keyboard indicator",
    "charge_limit": "Battery charge limit",
    "nvidia_powermizer_modes": "GPU PowerMizer modes",
    "nvidia_voltage_boost": "GPU Voltage Boost",
    "kbd_ambient": "Ambient keyboard mode",
}

METADATA = {"cpu_vendor", "aura_id", "gpu_limits", "gpu_offset_limits"}


def feature_rows(caps):
    """Return every capability as a displayable boolean, except metadata."""
    return [(key, LABELS.get(key, key.replace("_", " ").capitalize()), bool(value))
            for key, value in caps.items() if key not in METADATA]


def display_refresh_row():
    """Read the session's real internal-panel modes without changing them."""
    label = "Automatic display refresh"
    try:
        rates, current = display_refresh.get_refresh_rates()
        if not rates:
            raise ValueError("No supported refresh rates at the current resolution")
        detail = ", ".join(f"{rate:g} Hz" for rate in rates)
        if current is not None:
            detail += f"; current: {current:g} Hz"
        available = True
    except ValueError as error:
        detail = str(error)
        available = False
    # Preserve the installer's one-line key|label|boolean protocol even if a
    # desktop tool returns a multiline error or a pipe in an identifier.
    detail = " ".join(detail.replace("|", "/").split())
    return "display_refresh", f"{label} ({detail})", available


def detect_feature_rows():
    """Run the app's startup probes for an installer feature report."""
    caps = hardware.detect_capabilities()
    caps.update(hardware.probe_gpu_tuning_capabilities(caps))
    selected = graphics_backend.selected_backend()
    caps["gpu_mode_switching"] = bool(caps.get(selected)) and (
        selected != "cardwire" or caps.get("cardwire_wayland"))
    caps["nvidia_powermizer_modes"] = (
        hardware.detect_nvidia_powermizer_modes()
        if caps["nvidia_settings"] else ())
    caps["nvidia_voltage_boost"] = (
        hardware.probe_nvidia_voltage_boost() is not None
        if caps["nvidia"] else False)
    try:
        from .widgets.ambient import ambient_available
        caps["kbd_ambient"] = ambient_available()
    except Exception:
        caps["kbd_ambient"] = False
    return feature_rows(caps) + [display_refresh_row()]


def main():
    for key, label, available in detect_feature_rows():
        print(f"{key}|{label}|{int(available)}")


if __name__ == "__main__":
    main()
