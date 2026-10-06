"""Headless profile and keyboard commands shared by legacy shortcuts."""

import argparse
import os
import time
import traceback
from . import config as config_mod
from . import fancurve, hardware, kbdcolor

# See rogcontrol-apply.py: one copy of the curve maths and one of the helper
# call, both in the package.
interpolate_curve = fancurve.interpolate_curve
pct_to_pwm255 = fancurve.pct_to_pwm255


def run_helper(*args):
    """Run a privileged action and REPORT failure. The package's, so this
    hotkey cannot drift away from what the boot apply does."""
    return hardware.run_helper_logged(*args, source="cycle-profile",
                                      timeout=30)[0]

# Capabilities are detected when applying, never while importing the CLI.

# See pages/fans.py: retested down to 0.5s with no failures, kept at 5s for
# margin over the retested floor.
# The EC accepted 0.5s gaps across repeated hardware tests.  Keeping this
# at the measured floor makes shortcut profile changes responsive while still
# separating channel writes.
CHANNEL_GAP_S = 0.5

# Use shared desktop notifications for shortcut results.
notify = hardware.notify


def apply_profile(profile):
    cpu_caps = {"ryzenadj": hardware.cpu_is_amd(), "cpu_boost": True,
                    "cpu_epp": True, "cpu_clock": True,
                    "cpu_power_limits": hardware.cpu_power_limits_backend()}
    cpu = profile.get("cpu")
    if cpu:
        for _step, args in hardware.cpu_apply_plan(cpu, cpu_caps):
            run_helper(*args)
    gpu = profile.get("gpu")
    if gpu:
        # Optional GPU fields are only applied when present.
        if "watts" in gpu and hardware.gpu_power_limit_supported():
            run_helper("gpu", gpu["watts"])
        # Apply all configured GPU controls; the enforcer does not continuously restore them.
        if "clock_limit" in gpu:
            # Against the card's own maximum, not a hardcoded 3090: the
            # top of the slider means "no ceiling", and comparing against
            # another card's number turns that into a lock.
            run_helper("gpuclocklimit",
                       hardware.gpu_clock_limit_arg(
                           gpu["clock_limit"],
                           hardware.gpu_clock_limit_max()))
        if "dyn_boost" in gpu:
            run_helper("nvboost", gpu["dyn_boost"])
        if "temp_target" in gpu:
            run_helper("nvtemp", gpu["temp_target"])
        # Clock offsets use the shared timeout and error handling.
        for kind, key in (("core", "clock_offset"),
                          ("memory", "mem_clock_offset")):
            if key in gpu:
                ok, message = hardware.set_nvidia_clock_offset(kind, gpu[key])
                if not ok:
                    hardware.log(f"GPU {kind} clock offset failed: {message}",
                                 "ERROR", source="cycle-profile",
                                 dedupe_key=f"nv{kind}")
    # Skip matching fan curves to avoid unnecessary paced EC writes.
    fans = profile.get("fans", {})
    # A temporary fan boost owns the curves until its deadline.
    if hardware.fan_boost_active(hardware.read_fan_boost()):
        fans = {}
    held = {ch: hardware.read_fan_curve_points(ch) for ch in fans}
    enabled = hardware.read_fan_curve_enabled()
    todo = [(ch, pts) for ch, pts in fans.items()
            if not (enabled.get(ch) is not False
                    and held.get(ch) is not None
                    and fancurve.curve_matches_hardware(pts, held[ch]))]
    for i, (channel, points) in enumerate(todo):
        if i > 0:
            # See pages/fans.py module docstring: 0.5s was first found to
            # leave channels stuck, but a later retest found 0.5s-8s all
            # held. CHANNEL_GAP_S is kept above the retested floor.
            time.sleep(CHANNEL_GAP_S)
        expanded = interpolate_curve(points, 8)
        flat = []
        for t, pct in expanded:
            flat += [t, pct_to_pwm255(pct)]
        hardware.run_fan_helper_logged(channel, *flat, source="cycle")


def cycle_profile():
    if not os.path.exists(config_mod.CONFIG_PATH):
        return
    # Select against fresh config so another writer's changes are preserved.
    picked = {}

    def _pick_next(cfg):
        names = list(cfg.get("profiles", {}).keys())
        if not names:
            picked["next_name"] = None
            return
        current = cfg.get("current_profile")
        idx = names.index(current) if current in names else -1
        next_name = names[(idx + 1) % len(names)]
        cfg["current_profile"] = next_name
        picked["next_name"] = next_name

    config = config_mod.update_config(_pick_next)
    next_name = picked.get("next_name")
    if next_name is None:
        return

    # The profile selection itself is instantaneous.  Hardware writes below
    # may take several seconds, so tell the user which profile was selected
    # before starting the slow part.
    notify("ROG Control", f"Switching to {next_name}…")

    # Set OS mode before fan curves: changing the mode resets the EC curves.
    hardware.set_power_mode_for_profile(next_name)

    # Profile Color lighting follows the selected profile when enabled.
    hardware.set_profile_kbd_color(config, next_name)

    # The profile is already saved; report partial application failures.
    try:
        apply_profile(config["profiles"][next_name])
    except Exception as e:  # noqa: BLE001 - reported, not swallowed
        hardware.log(f"cycle to {next_name} failed: {e}", "ERROR",
                     source="cycle-profile", dedupe_key="cyclefail")
        traceback.print_exc()
        notify("ROG Control",
               f"Profile {next_name} was only partly applied — {e}")
        return
    notify("ROG Control", f"Profile switched to {next_name}")

# The package's, with a timeout and a failure the log records; this script's
# own copies had neither, and its notify showed up unattributed for want of
# the -a flag.

MODE_ORDER = [name for name in kbdcolor.KBD_RGB_MODES
              if name not in kbdcolor.EXCLUSIVE_MODES]


def available_modes():
    """Return supported shortcut lighting modes; exclude GUI-only modes."""
    caps = {
        "kbd_rgb_zones": (hardware.find_aura_keyboard()
                          in hardware.AURA_MULTI_ZONE_IDS),
        "kbd_battery": hardware.read_battery()[0] is not None,
    }
    modes = [name for name in kbdcolor.supported_modes(caps)
             if name not in kbdcolor.EXCLUSIVE_MODES]
    return modes or list(MODE_ORDER)


def apply_mode(mode_name, cfg_rgb):
    """Apply a saved lighting mode, sampling live colors when necessary."""
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


def cycle_keyboard():
    # Use the shared loader, including corrupt-file recovery.
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
        # Update only the mode in fresh config; preserve concurrent color/speed changes.
        config_mod.update_config(_mode_setter(next_mode))
        notify("ROG Control", f"Keyboard light mode: {next_mode}")
    else:
        notify("ROG Control", f"Failed to change keyboard mode: {msg}")

# The package's range, not a second pair of numbers. The helper validates
# against the same bounds; a script that clamped to a wider range would only
# be sending calls that are refused.
KBD_MIN, KBD_MAX = kbdcolor.KBD_MIN, kbdcolor.KBD_MAX

# Both the package's. This script had its own copy of each: a run_helper with
# no timeout at all -- so a wedged sudo left the keypress hanging forever
# with nothing logged -- and a notify with no -a flag, which showed the
# message unattributed. Neither difference was intended; they are what two
# hand-copied functions drift into.


def adjust_brightness(direction):


    # Use the shared loader, including corrupt-file recovery.
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
        # Save against fresh config after the hardware write.
        config_mod.update_config(
            lambda cfg: cfg.update({"kbd_brightness": new_level}))
        notify("ROG Control", f"Keyboard brightness: {new_level}/{KBD_MAX}")
    else:
        # Report the helper failure through the shortcut notification.
        notify("ROG Control",
               f"Could not change keyboard brightness: {message}")

SPEED_MIN, SPEED_MAX = kbdcolor.SPEED_MIN, kbdcolor.SPEED_MAX
SPEED_MODES = kbdcolor.SPEED_MODES

# The package's, with a timeout and a failure the log records. This script's
# own run_helper had neither.


def apply_speed(mode, cfg_rgb, speed):
    """Apply the current effect at a new speed using validated keyboard arguments."""
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


def adjust_speed(direction):


    # Use the shared loader, including corrupt-file recovery.
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
        # Update only speed in fresh config; preserve concurrent mode/color changes.
        config_mod.update_config(_speed_setter(new_speed))
        notify("ROG Control", f"Keyboard effect speed: {new_speed}/{SPEED_MAX}")
    else:
        # Report the helper failure through the shortcut notification.
        notify("ROG Control",
               f"Could not change keyboard effect speed: {message}")


def main(argv=None):
    """Dispatch headless commands without loading GTK or probing hardware."""
    parser = argparse.ArgumentParser(
        prog="rogcontrol", description="ROG Control profile and keyboard commands",
        epilog="Run rogcontrol without a command to open the window. "
               "Window flags: --show, --hide, --toggle, --minimized, --quit, --self-test.")
    commands = parser.add_subparsers(dest="command", required=True)
    profile = commands.add_parser("profile", help="Switch profiles")
    profile.add_argument("action", choices=("next",))
    keyboard = commands.add_parser("keyboard", help="Adjust keyboard lighting")
    keyboard_commands = keyboard.add_subparsers(dest="action", required=True)
    keyboard_commands.add_parser("next", help="Cycle lighting mode")
    for name in ("brightness", "speed"):
        command = keyboard_commands.add_parser(name)
        command.add_argument("direction", choices=("up", "down"))
    commands.add_parser("report", help="Save a hardware report")
    args = parser.parse_args(argv)
    if args.command == "profile":
        return cycle_profile() or 0
    if args.command == "report":
        from .diagnostics import write_hardware_report
        print(write_hardware_report())
        return 0
    if args.action == "next":
        return cycle_keyboard() or 0
    if args.action == "brightness":
        return adjust_brightness(args.direction) or 0
    return adjust_speed(args.direction) or 0
