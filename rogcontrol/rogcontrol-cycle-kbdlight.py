#!/usr/bin/env python3
"""
rogcontrol-cycle-kbdlight.py
Cycles the keyboard RGB to the next MODE (Static, Breathing, Pulse, Color
Cycle, Rainbow, Gradient Static, GPU Temp Color, CPU
Temp Color), keeping whatever color/speed is already configured rather than
resetting it. Bind this to a keyboard shortcut.

Writes the applied state back to the same config file the main app reads,
so the GUI reflects the change next time it's opened.
"""
import json
import os
import subprocess

CONFIG_PATH = os.path.expanduser("~/.config/rogcontrol.json")

# Same rotation as KBD_RGB_MODES in the main app, in the order to cycle
# through. Modes that don't need a temperature reading come first so the
# common case (Static/Breathing/Rainbow/etc) doesn't depend on hwmon/
# nvidia-smi being reachable from a shortcut context.
#
# This must stay in step with KBD_RGB_MODES. A mode present there but missing
# here makes the hotkey jump back to Static instead of advancing, because the
# saved mode name is then not found in this list.
MODE_ORDER = [
    "Static", "Breathing", "Pulse", "Color Cycle",
    "Rainbow", "Gradient Static",
    "GPU Temp Color", "CPU Temp Color", "Battery Level",
]

# Deliberately not in the rotation: Ambient needs a live screen-capture
# session, so it only exists while the main window is running. Cycling into
# it from a hotkey would set a mode nothing is driving.
#
# Profile Colour is out of the rotation too, for the opposite reason. It is
# not a keyboard effect the user picks between -- it is the keyboard being
# handed over to the profile switcher, which then owns it until the user
# takes it back on the Keyboard page. Cycling INTO it from here would paint
# one colour and look like Static; cycling OUT of it is exactly what should
# happen, and does: the saved name is not in this list, so the next press
# lands on Static and the profile switcher stops painting.

# Kept in step with the same lists in the main app. Multi-zone effects need a
# four-zone Aura controller; on anything else they light zone 1 and drop the
# rest, so the hotkey skips over them rather than cycling into a mode that
# does nothing visible.
MULTI_ZONE_MODES = ("Gradient Static",)
AURA_MULTI_ZONE_IDS = {"1854", "1866", "1869", "19b6", "1a30"}

TEMP_COLOR_MIN_C = 40
TEMP_COLOR_MAX_C = 90


def find_aura_keyboard():
    """USB product ID of the ASUS Aura keyboard controller, or None."""
    base = "/sys/bus/usb/devices"
    try:
        for entry in sorted(os.listdir(base)):
            path = f"{base}/{entry}"
            if not os.path.exists(f"{path}/idVendor"):
                continue
            if open(f"{path}/idVendor").read().strip() != "0b05":
                continue
            if os.path.exists(f"{path}/idProduct"):
                return open(f"{path}/idProduct").read().strip().lower()
    except Exception:
        pass
    return None


def available_modes():
    aura_id = find_aura_keyboard()
    zones = bool(aura_id) and aura_id in AURA_MULTI_ZONE_IDS
    has_battery = read_battery()[0] is not None
    modes = []
    for name in MODE_ORDER:
        if name in MULTI_ZONE_MODES and not zones:
            continue
        if name == "Battery Level" and not has_battery:
            continue
        modes.append(name)
    return modes or list(MODE_ORDER)


def run_helper(*args):
    result = subprocess.run(
        ["sudo", "-n", "/usr/local/bin/rogcontrol-helper", *[str(a) for a in args]],
        capture_output=True, text=True,
    )
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def notify(title, body):
    try:
        subprocess.run(["notify-send", title, body], timeout=5)
    except Exception:
        pass


def temp_to_rgb(temp_c, lo=TEMP_COLOR_MIN_C, hi=TEMP_COLOR_MAX_C):
    t = max(0.0, min(1.0, (temp_c - lo) / max(1, (hi - lo))))
    if t < 0.5:
        frac = t / 0.5
        return 0, round(255 * frac), round(255 * (1 - frac))
    frac = (t - 0.5) / 0.5
    return round(255 * frac), round(255 * (1 - frac)), 0


def read_cpu_temp():
    try:
        for d in os.listdir("/sys/class/hwmon"):
            path = f"/sys/class/hwmon/{d}"
            name_path = f"{path}/name"
            if os.path.exists(name_path) and open(name_path).read().strip() == "k10temp":
                val = open(f"{path}/temp1_input").read().strip()
                return int(val) / 1000
    except Exception:
        pass
    return None


def read_battery():
    """(percent, charging) for the first real battery, or (None, None)."""
    base = "/sys/class/power_supply"
    try:
        for entry in sorted(os.listdir(base)):
            path = f"{base}/{entry}"
            if not os.path.exists(f"{path}/type"):
                continue
            if open(f"{path}/type").read().strip() != "Battery":
                continue
            if not os.path.exists(f"{path}/capacity"):
                continue
            percent = int(open(f"{path}/capacity").read().strip())
            status = ""
            if os.path.exists(f"{path}/status"):
                status = open(f"{path}/status").read().strip()
            return percent, status == "Charging"
    except Exception:
        pass
    return None, None


def battery_to_rgb(percent, charging):
    """Same mapping as the main app: green -> yellow -> red while draining,
    blue -> green while charging."""
    pct = max(0, min(100, percent))
    if charging:
        frac = pct / 100
        return 0, round(255 * frac), round(255 * (1 - frac))
    if pct >= 50:
        frac = (100 - pct) / 50
        return round(255 * frac), 255, 0
    frac = (50 - pct) / 50
    return 255, round(255 * (1 - frac)), 0


def read_gpu_temp():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


def apply_mode(mode_name, cfg_rgb):
    r, g, b = cfg_rgb.get("r", 255), cfg_rgb.get("g", 0), cfg_rgb.get("b", 0)
    r2, g2, b2 = cfg_rgb.get("r2", 0), cfg_rgb.get("g2", 0), cfg_rgb.get("b2", 255)
    speed = cfg_rgb.get("speed", 1)

    if mode_name == "Rainbow":
        return run_helper("kbdrgb", "rainbow", speed)
    if mode_name == "Color Cycle":
        return run_helper("kbdrgb", "single_colorcycle", speed)
    if mode_name == "Breathing":
        return run_helper("kbdrgb", "single_breathing", r, g, b, r2, g2, b2, speed)
    if mode_name == "Pulse":
        # rogauracore's real pulse effect -- a distinct sharp flash, not a
        # breathing fade with a different color. Speed must be 1-3.
        pulse_speed = speed if speed in (1, 2, 3) else 2
        return run_helper("kbdrgb", "single_pulsing", r, g, b, pulse_speed)
    if mode_name == "Gradient Static":
        # Even ramp from colour 1 to colour 2 across the four zones, matching
        # _get_gradient_zone_colors() in the main app.
        zones = [tuple(max(0, min(255, round(a + (b - a) * (i / 3))))
                       for a, b in zip((r, g, b), (r2, g2, b2)))
                 for i in range(4)]
        return run_helper("kbdrgb", "multi_static", *[c for z in zones for c in z])
    if mode_name == "GPU Temp Color":
        temp = read_gpu_temp()
        if temp is None:
            return False, "no GPU temperature reading available"
        tr, tg, tb = temp_to_rgb(temp)
        return run_helper("kbdrgb", "single_static", tr, tg, tb)
    if mode_name == "CPU Temp Color":
        temp = read_cpu_temp()
        if temp is None:
            return False, "no CPU temperature reading available"
        tr, tg, tb = temp_to_rgb(temp)
        return run_helper("kbdrgb", "single_static", tr, tg, tb)
    if mode_name == "Battery Level":
        percent, charging = read_battery()
        if percent is None:
            return False, "no battery found on this machine"
        br, bg, bb = battery_to_rgb(percent, charging)
        return run_helper("kbdrgb", "single_static", br, bg, bb)
    # Static (default/fallback)
    return run_helper("kbdrgb", "single_static", r, g, b)


def main():
    config = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError):
            config = {}

    cfg_rgb = config.get("kbd_rgb", {})
    current_mode = cfg_rgb.get("mode", "Static")
    modes = available_modes()
    try:
        current_idx = modes.index(current_mode)
    except ValueError:
        current_idx = -1

    next_mode = modes[(current_idx + 1) % len(modes)]
    ok, msg = apply_mode(next_mode, cfg_rgb)

    if ok:
        cfg_rgb["mode"] = next_mode
        config["kbd_rgb"] = cfg_rgb
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
        notify("ROG Control", f"Keyboard light mode: {next_mode}")
    else:
        notify("ROG Control", f"Failed to change keyboard mode: {msg}")


if __name__ == "__main__":
    main()
