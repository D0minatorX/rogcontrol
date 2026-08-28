#!/usr/bin/env python3
"""
rogcontrol-adjust-kbdbrightness.py up|down
Raises or lowers keyboard backlight brightness by one step (range 0-3),
without needing the GUI open. Bind "up" and "down" to two separate
keyboard shortcuts.
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

# The package's range, not a second pair of numbers. The helper validates
# against the same bounds; a script that clamped to a wider range would only
# be sending calls that are refused.
KBD_MIN, KBD_MAX = kbdcolor.KBD_MIN, kbdcolor.KBD_MAX

# Both the package's. This script had its own copy of each: a run_helper with
# no timeout at all -- so a wedged sudo left the keypress hanging forever
# with nothing logged -- and a notify with no -a flag, which showed the
# message unattributed. Neither difference was intended; they are what two
# hand-copied functions drift into.
notify = hardware.notify


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("up", "down"):
        print("Usage: rogcontrol-adjust-kbdbrightness.py up|down", file=sys.stderr)
        sys.exit(1)
    direction = sys.argv[1]

    # config.load_config, not a bare json.load with a fallback to {}. That
    # fallback wrote its near-empty dict straight back over an unparseable
    # config, destroying every profile in it; load_config keeps a
    # .corrupt-<timestamp> copy instead and hands back a fresh default.
    current = config_mod.load_config().get("kbd_brightness", 2)
    new_level = current + 1 if direction == "up" else current - 1
    new_level = max(KBD_MIN, min(KBD_MAX, new_level))

    if new_level == current:
        # Already at the limit -- still notify so the key press feels
        # acknowledged rather than silently doing nothing.
        notify("ROG Control", f"Keyboard brightness already at {'max' if direction == 'up' else 'min'}")
        return

    ok, message = hardware.run_helper_logged(
        *kbdcolor.kbd_brightness_args(new_level),
        source="adjust-kbdbrightness")
    if ok:
        # Re-read and write in one step rather than saving the copy read
        # above: the helper call between the two is long enough for the
        # window, the tray or the enforcer to have written the file, and
        # writing the older copy back threw away whatever they changed.
        config_mod.update_config(
            lambda cfg: cfg.update({"kbd_brightness": new_level}))
        notify("ROG Control", f"Keyboard brightness: {new_level}/{KBD_MAX}")
    else:
        # With the reason, not just "failed". This is the only channel
        # this script has, and a hotkey that says nothing but "failed" is
        # one the user cannot act on.
        notify("ROG Control",
               f"Could not change keyboard brightness: {message}")


if __name__ == "__main__":
    main()
