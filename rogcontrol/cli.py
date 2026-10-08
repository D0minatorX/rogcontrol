"""Headless profile and keyboard commands shared by legacy shortcuts."""

import argparse
import json
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
    ok, message = hardware.run_helper_logged(*args, source="cycle-profile", timeout=30)
    if not ok:
        raise RuntimeError(message)
    return True

# Capabilities are detected when applying, never while importing the CLI.

CHANNEL_GAP_S = fancurve.CHANNEL_GAP_S

# Use shared desktop notifications for shortcut results.
notify = hardware.notify


def apply_profile(profile):
    failures = []

    def write(*args):
        try:
            run_helper(*args)
        except Exception as error:
            failures.append(f'{args[0]}: {error}')

    cpu_caps = {"ryzenadj": hardware.cpu_is_amd(), "cpu_boost": True,
                    "cpu_epp": True, "cpu_clock": True,
                    "cpu_power_limits": hardware.cpu_power_limits_backend()}
    cpu = profile.get("cpu")
    if cpu:
        for _step, args in hardware.cpu_apply_plan(cpu, cpu_caps):
            write(*args)
    gpu = profile.get("gpu")
    if gpu:
        # Optional GPU fields are only applied when present.
        if "watts" in gpu and hardware.gpu_power_limit_supported():
            write("gpu", gpu["watts"])
        # Apply all configured GPU controls; the enforcer does not continuously restore them.
        if "clock_limit" in gpu:
            # Against the card's own maximum, not a hardcoded 3090: the
            # top of the slider means "no ceiling", and comparing against
            # another card's number turns that into a lock.
            write("gpuclocklimit",
                       hardware.gpu_clock_limit_arg(
                           gpu["clock_limit"],
                           hardware.gpu_clock_limit_max()))
        ok, message = hardware.nv_apply_settings(gpu)
        if not ok:
            failures.append(f'GPU firmware: {message}')
        # Clock offsets use the shared timeout and error handling.
        for kind, key in (("core", "clock_offset"),
                          ("memory", "mem_clock_offset")):
            if key in gpu:
                ok, message = hardware.set_nvidia_clock_offset(kind, gpu[key])
                if not ok:
                    hardware.log(f"GPU {kind} clock offset failed: {message}",
                                 "ERROR", source="cycle-profile",
                                 dedupe_key=f"nv{kind}")
                    failures.append(f'GPU {kind} offset: {message}')
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
            # Separate channels using the shared nominal gap.
            time.sleep(CHANNEL_GAP_S)
        expanded = interpolate_curve(points, 8)
        flat = []
        for t, pct in expanded:
            flat += [t, pct_to_pwm255(pct)]
        ok, message = hardware.run_fan_helper_logged(channel, *flat, source="cycle")
        if not ok:
            failures.append(f'Fan {channel}: {message}')
    if failures:
        raise RuntimeError('; '.join(failures))


def cycle_profile(origin='shortcut'):
    from . import profile_requests
    if os.path.exists(config_mod.CONFIG_PATH):
        profile_requests.request_next(origin)


def apply_selected_profile(config, next_name):
    failures = []
    # Set OS mode before fan curves: changing the mode resets the EC curves.
    result = hardware.set_power_mode_for_profile(next_name)
    if result is not None and not result[0]:
        failures.append(f'OS power mode: {result[1]}')

    # Profile Color lighting follows the selected profile when enabled.
    result = hardware.set_profile_kbd_color(config, next_name)
    if result is not None and not result[0]:
        failures.append(f'Keyboard colour: {result[1]}')

    # The profile is already saved; report partial application failures.
    try:
        profile = dict(config["profiles"][next_name])
        if config.get('safety_tripped') and profile.get('cpu'):
            profile['cpu'] = config_mod.stock_cpu_values(profile['cpu'])
        apply_profile(profile)
        if failures:
            raise RuntimeError('; '.join(failures))
    except Exception as e:  # noqa: BLE001 - reported, not swallowed
        hardware.log(f"cycle to {next_name} failed: {e}", "ERROR",
                     source="cycle-profile", dedupe_key="cyclefail")
        traceback.print_exc()
        notify("ROG Control",
               f"Profile {next_name} was only partly applied — {e}")
        return
    # Do not announce an obsolete profile after the user has selected another.
    if config_mod.load_config().get('current_profile') == next_name:
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
    profile.add_argument("action", choices=("next", "drain"))
    profile.add_argument("--origin", choices=("shortcut", "bindings", "fnlock"), default="shortcut")
    keyboard = commands.add_parser("keyboard", help="Adjust keyboard lighting")
    keyboard_commands = keyboard.add_subparsers(dest="action", required=True)
    keyboard_commands.add_parser("next", help="Cycle lighting mode")
    for name in ("brightness", "speed"):
        command = keyboard_commands.add_parser(name)
        command.add_argument("direction", choices=("up", "down"))
    commands.add_parser("report", help="Save a hardware report")
    decky = commands.add_parser("decky", help="Decky plugin integration")
    decky_commands = decky.add_subparsers(dest="decky_action", required=True)
    decky_commands.add_parser("state", help="Print plugin-relevant state as JSON")
    decky_commands.add_parser("update-check", help="Check for a Decky plugin release")
    decky_commands.add_parser("update-install", help="Install a verified Decky plugin release")
    select = decky_commands.add_parser("profile", help="Select a saved profile")
    select.add_argument("name")
    cpu = decky_commands.add_parser("cpu", help="Set a current-profile CPU value")
    cpu.add_argument("setting", choices=("boost", "max-freq"))
    cpu.add_argument("value", help="on/off for boost; MHz (0 clears) for max-freq")
    args = parser.parse_args(argv)
    if args.command == "profile":
        if args.action == 'drain':
            from . import profile_requests
            return profile_requests.drain(apply_selected_profile) or 0
        return cycle_profile(args.origin) or 0
    if args.command == "report":
        from .diagnostics import write_hardware_report
        print(write_hardware_report())
        return 0
    if args.command == "decky":
        from . import decky as decky_api
        try:
            if args.decky_action == "state":
                result = {"ok": True, "state": decky_api.get_state()}
            elif args.decky_action == "update-check":
                from . import decky_install, decky_release
                installed = decky_install.detect_installation()
                result = {"ok": True,
                          **decky_release.check_for_update(installed.get("version"))}
            elif args.decky_action == "update-install":
                from . import decky_install, decky_release
                installed = decky_install.detect_installation()
                release = decky_release.check_for_update(installed.get("version"))
                if release.get("error"):
                    raise RuntimeError(release["error"])
                if not release.get("available"):
                    raise RuntimeError("no newer verified Decky plugin release is available")
                archive = decky_release.download_and_stage(
                    release["download_url"], release["sha256_url"],
                    release["version"])
                try:
                    result = decky_install.install_archive(
                        archive, expected_version=release["version"])
                finally:
                    decky_release.cleanup_staged(archive)
                result["restart_required"] = True
            elif args.decky_action == "profile":
                result = {"ok": True, **decky_api.set_profile(args.name)}
            elif args.setting == "boost":
                if args.value not in ("on", "off"):
                    raise ValueError("boost value must be on or off")
                result = decky_api.set_cpu_value("boost", args.value == "on")
            else:
                try:
                    mhz = int(args.value)
                except ValueError as error:
                    raise ValueError("max-freq value must be an integer MHz value") from error
                result = decky_api.set_cpu_value("max_freq", mhz)
            print(json.dumps(result, separators=(",", ":")))
            return 0
        except (ValueError, RuntimeError, OSError) as error:
            print(json.dumps({"ok": False, "error": str(error)},
                             separators=(",", ":")))
            return 1
    if args.action == "next":
        return cycle_keyboard() or 0
    if args.action == "brightness":
        return adjust_brightness(args.direction) or 0
    return adjust_speed(args.direction) or 0
