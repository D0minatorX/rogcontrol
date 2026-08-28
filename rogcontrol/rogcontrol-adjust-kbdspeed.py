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
from rogcontrol import hardware  # noqa: E402
from rogcontrol import kbdcolor  # noqa: E402

# All four the package's. This script had hand-copied numbers, a hand-copied
# SPEED_MODES tuple and its own apply_speed -- four independent transcriptions
# of things kbdcolor owns, and its own comment admitted the tuple was "kept in
# step by hand". Adding a fifth animated mode to kbdcolor now reaches this
# script; before, it silently reported "has no speed to change".
SPEED_MIN, SPEED_MAX = kbdcolor.SPEED_MIN, kbdcolor.SPEED_MAX
SPEED_MODES = kbdcolor.SPEED_MODES

# The package's, with a timeout and a failure the log records. This script's
# own run_helper had neither.
notify = hardware.notify


def apply_speed(mode, cfg_rgb, speed):
    """Re-send the current mode at a new speed.

    kbdcolor.helper_args builds the same call the Keyboard page and the boot
    apply send, which is the point: this used to build it here, and the copy
    got Breathing's second colour from r2/g2/b2 with no clamping, so a config
    a user had edited by hand could reach the helper as a value it refused.

    Returns ``(ok, message)``. ``args`` is None only for a mode with no
    speed, which main() has already ruled out.
    """
    args = kbdcolor.helper_args(
        mode,
        kbdcolor.saved_color(cfg_rgb),
        kbdcolor.saved_color(cfg_rgb, "2", kbdcolor.DEFAULT_COLOR2),
        speed)
    if args is None:
        return False, f"{mode} has no speed to change"
    return hardware.run_helper_logged(*args, source="adjust-kbdspeed")


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

    ok, message = apply_speed(mode, cfg_rgb, new_speed)
    if ok:
        # The speed key on its own, in a freshly read config -- not the whole
        # kbd_rgb block read above. The helper call in between is long enough
        # for the kbdlight cycler or the Keyboard page to have written that
        # block, and putting the older copy back reverted their mode or
        # colour along with it.
        config_mod.update_config(_speed_setter(new_speed))
        notify("ROG Control", f"Keyboard effect speed: {new_speed}/{SPEED_MAX}")
    else:
        # With the reason: see rogcontrol-adjust-kbdbrightness.py.
        notify("ROG Control",
               f"Could not change keyboard effect speed: {message}")


if __name__ == "__main__":
    main()
