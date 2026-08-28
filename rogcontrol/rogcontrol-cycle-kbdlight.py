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

# The package's, with a timeout and a failure the log records; this script's
# own copies had neither, and its notify showed up unattributed for want of
# the -a flag.
notify = hardware.notify

# The rotation is KBD_RGB_MODES in insertion order, minus the two modes that
# are not effects a hotkey can cycle into.
#
# It used to be a hand-written list here, with a comment saying it "must stay
# in step with KBD_RGB_MODES" and nothing making it so -- and the stated
# consequence was real: a mode present there and missing here makes the
# hotkey jump back to Static instead of advancing, because the saved mode
# name is then not found in this list.
#
# The order that list was written in is the order KBD_RGB_MODES already has,
# including the property its comment claimed: the modes needing no
# temperature reading come first, so the common case does not depend on
# hwmon or nvidia-smi being reachable from a shortcut context.
#
# EXCLUSIVE_MODES is what comes out. Ambient needs a live screen-capture
# session, so it only exists while the main window is running; cycling into
# it from a hotkey would set a mode nothing is driving. Profile Colour is out
# for the opposite reason -- it is not an effect the user picks between, it
# is the keyboard being handed to the profile switcher, which owns it until
# the user takes it back on the Keyboard page. Cycling INTO it would paint
# one colour and look like Static; cycling OUT of it is exactly what should
# happen, and does: the saved name is not in this list, so the next press
# lands on Static and the profile switcher stops painting.
MODE_ORDER = [name for name in kbdcolor.KBD_RGB_MODES
              if name not in kbdcolor.EXCLUSIVE_MODES]


def available_modes():
    """The rotation this machine can actually perform.

    Through kbdcolor.supported_modes, the same gate the picker in the window
    uses, rather than a second set of rules -- this script had its own copy
    of MULTI_ZONE_MODES and of the Aura product-ID table, and a keyboard
    added to one would have been missing from the other.

    Only the two capabilities that cost a sysfs read are probed. Everything
    supported_modes can also gate on (an NVIDIA card, the screen-capture
    portal) is left at its default of "present", which keeps this script's
    existing behaviour: GPU Temp Color stays in the rotation on a machine
    with no card and reports what went wrong when it is reached, rather than
    being silently withheld. See pages/keyboard.py for why that is the
    doctrine."""
    caps = {
        "kbd_rgb_zones": (hardware.find_aura_keyboard()
                          in hardware.AURA_MULTI_ZONE_IDS),
        "kbd_battery": hardware.read_battery()[0] is not None,
    }
    modes = [name for name in kbdcolor.supported_modes(caps)
             if name not in kbdcolor.EXCLUSIVE_MODES]
    return modes or list(MODE_ORDER)


def apply_mode(mode_name, cfg_rgb):
    """Put ``mode_name`` on the keyboard. Returns ``(ok, message)``.

    Every argument comes from kbdcolor now. This function used to build each
    call by hand -- the gradient ramp, the pulse speed clamp, the temperature
    and battery colour maths were all transcribed here -- alongside its own
    read_cpu_temp, read_battery and read_gpu_temp. Two of those transcriptions
    had already drifted: the colours were taken from the config unclamped, so
    a hand-edited config reached the helper as values it refused, and the
    zone ramp rounded independently of gradient_zone_colors."""
    args = kbdcolor.helper_args(
        mode_name,
        kbdcolor.saved_color(cfg_rgb),
        kbdcolor.saved_color(cfg_rgb, "2", kbdcolor.DEFAULT_COLOR2),
        cfg_rgb.get("speed", kbdcolor.DEFAULT_SPEED))
    if args is None:
        # A live-reading mode: the colour is not in the config, it has to be
        # measured. read_live_color is the package's pairing of "take the
        # reading" with "map it to a colour", shared with the Keyboard page
        # and the enforcer's charger flash.
        color, reason = hardware.read_live_color(mode_name)
        if color is None:
            return False, reason
        args = kbdcolor.static_args(color)
    return hardware.run_helper_logged(*args, source="cycle-kbdlight")


def _mode_setter(mode):
    def mutate(cfg):
        block = cfg.get("kbd_rgb")
        if not isinstance(block, dict):
            block = {}
            cfg["kbd_rgb"] = block
        block["mode"] = mode
    return mutate


def main():
    # config.load_config, not a bare json.load with a fallback to {}. That
    # fallback wrote its near-empty dict straight back over an unparseable
    # config, destroying every profile in it; load_config keeps a
    # .corrupt-<timestamp> copy instead and hands back a fresh default.
    cfg_rgb = config_mod.load_config().get("kbd_rgb", {})
    current_mode = cfg_rgb.get("mode", "Static")
    modes = available_modes()
    try:
        current_idx = modes.index(current_mode)
    except ValueError:
        current_idx = -1

    next_mode = modes[(current_idx + 1) % len(modes)]
    ok, msg = apply_mode(next_mode, cfg_rgb)

    if ok:
        # The mode key on its own, in a freshly read config -- not the whole
        # kbd_rgb block read above. The helper call in between is long enough
        # for the speed hotkey or the Keyboard page to have written that
        # block, and putting the older copy back reverted their change.
        config_mod.update_config(_mode_setter(next_mode))
        notify("ROG Control", f"Keyboard light mode: {next_mode}")
    else:
        notify("ROG Control", f"Failed to change keyboard mode: {msg}")


if __name__ == "__main__":
    main()
