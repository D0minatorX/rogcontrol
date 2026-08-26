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
import os
import subprocess
import sys

# The shared modules sit beside this script's package in the repo, and under
# ~/.local/lib once installed -- this script is installed into ~/.local/bin,
# where there is no package next to it. Same probe the boot apply, the tray
# and the enforcer do, repo first so a checkout tests the checkout.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.dirname(_HERE), os.path.expanduser("~/.local/lib")):
    if os.path.isfile(os.path.join(_candidate, "rogcontrol", "__init__.py")):
        if _candidate not in sys.path:
            sys.path.insert(0, _candidate)
        break

from rogcontrol import config as config_mod  # noqa: E402

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


def _speed_setter(speed):
    def mutate(cfg):
        block = cfg.get("kbd_rgb")
        if not isinstance(block, dict):
            block = {}
            cfg["kbd_rgb"] = block
        block["speed"] = speed
    return mutate


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("up", "down"):
        print("Usage: rogcontrol-adjust-kbdspeed.py up|down", file=sys.stderr)
        sys.exit(1)
    direction = sys.argv[1]

    # config.load_config, not a bare json.load with a fallback to {}. That
    # fallback wrote its near-empty dict straight back over an unparseable
    # config, destroying every profile in it; load_config keeps a
    # .corrupt-<timestamp> copy instead and hands back a fresh default.
    cfg_rgb = config_mod.load_config().get("kbd_rgb", {})
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
        # The speed key on its own, in a freshly read config -- not the whole
        # kbd_rgb block read above. The helper call in between is long enough
        # for the kbdlight cycler or the Keyboard page to have written that
        # block, and putting the older copy back reverted their mode or
        # colour along with it.
        config_mod.update_config(_speed_setter(new_speed))
        notify("ROG Control", f"Keyboard effect speed: {new_speed}/{SPEED_MAX}")
    else:
        notify("ROG Control", "Failed to change keyboard effect speed")


if __name__ == "__main__":
    main()
