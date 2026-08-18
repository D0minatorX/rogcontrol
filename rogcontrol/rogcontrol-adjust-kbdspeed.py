#!/usr/bin/env python3
"""
rogcontrol-adjust-kbdspeed.py up|down
Raises or lowers the speed of the current keyboard lighting effect by one
step (range 1-3), without needing the GUI open. Bind "up" and "down" to two
separate keyboard shortcuts.

Only Breathing, Pulse, Color Cycle and Rainbow have a speed -- every other
mode (Static, Gradient Static, the live temperature/battery colours,
Ambient) is a fixed picture with nothing to animate, so there is nothing
for this to change. Notifies rather than erroring in that case: a shortcut
that does nothing on a mode with no speed is expected, not a failure.

Writes the applied state back to the same config file the main app reads,
so the GUI reflects the change next time it's opened.
"""
import json
import os
import subprocess
import sys

CONFIG_PATH = os.path.expanduser("~/.config/rogcontrol.json")
SPEED_MIN, SPEED_MAX = 1, 3

# Same set as SPEED_MODES in kbdcolor.py -- kept in step by hand, the same
# way MODE_ORDER in rogcontrol-cycle-kbdlight.py is, since this script runs
# standalone and does not import the package.
SPEED_MODES = ("Breathing", "Pulse", "Color Cycle", "Rainbow")


def run_helper(*args):
    result = subprocess.run(
        ["sudo", "-n", "/usr/local/bin/rogcontrol-helper", *[str(a) for a in args]],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def notify(title, body):
    try:
        subprocess.run(["notify-send", title, body], timeout=5)
    except Exception:
        pass


def apply_speed(mode, cfg_rgb, speed):
    r, g, b = cfg_rgb.get("r", 255), cfg_rgb.get("g", 0), cfg_rgb.get("b", 0)
    r2, g2, b2 = cfg_rgb.get("r2", 0), cfg_rgb.get("g2", 0), cfg_rgb.get("b2", 255)

    if mode == "Rainbow":
        return run_helper("kbdrgb", "rainbow", speed)
    if mode == "Color Cycle":
        return run_helper("kbdrgb", "single_colorcycle", speed)
    if mode == "Breathing":
        return run_helper("kbdrgb", "single_breathing", r, g, b, r2, g2, b2, speed)
    if mode == "Pulse":
        return run_helper("kbdrgb", "single_pulsing", r, g, b, speed)
    return False


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("up", "down"):
        print("Usage: rogcontrol-adjust-kbdspeed.py up|down", file=sys.stderr)
        sys.exit(1)
    direction = sys.argv[1]

    config = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError):
            config = {}

    cfg_rgb = config.get("kbd_rgb", {})
    mode = cfg_rgb.get("mode", "Static")
    if mode not in SPEED_MODES:
        notify("ROG Control", f"{mode} has no speed to change")
        return

    current = cfg_rgb.get("speed", SPEED_MIN)
    new_speed = current + 1 if direction == "up" else current - 1
    new_speed = max(SPEED_MIN, min(SPEED_MAX, new_speed))

    if new_speed == current:
        notify("ROG Control", f"Speed already at {'max' if direction == 'up' else 'min'}")
        return

    if apply_speed(mode, cfg_rgb, new_speed):
        cfg_rgb["speed"] = new_speed
        config["kbd_rgb"] = cfg_rgb
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
        notify("ROG Control", f"Keyboard effect speed: {new_speed}/{SPEED_MAX}")
    else:
        notify("ROG Control", "Failed to change keyboard effect speed")


if __name__ == "__main__":
    main()
