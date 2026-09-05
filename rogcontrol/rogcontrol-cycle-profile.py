#!/usr/bin/env python3
"""
rogcontrol-cycle-profile.py
Cycles to the next profile and applies it, without needing the GUI open.
Bind this to a keyboard shortcut.
"""
import os
import sys
import time
import traceback

# The shared modules sit beside this script's package in the repo, and under
# ~/.local/lib once installed -- this script is installed into ~/.local/bin,
# where there is no package next to it. Same probe the tray and the enforcer
# do, repo first so a checkout tests the checkout.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.dirname(_HERE), os.path.expanduser("~/.local/lib")):
    if os.path.isfile(os.path.join(_candidate, "rogcontrol", "__init__.py")):
        if _candidate not in sys.path:
            sys.path.insert(0, _candidate)
        break

from rogcontrol import config as config_mod  # noqa: E402
from rogcontrol import fancurve  # noqa: E402
from rogcontrol import hardware  # noqa: E402

# See rogcontrol-apply.py: one copy of the curve maths and one of the helper
# call, both in the package.
interpolate_curve = fancurve.interpolate_curve
pct_to_pwm255 = fancurve.pct_to_pwm255


def run_helper(*args):
    """Run a privileged action and REPORT failure. The package's, so this
    hotkey cannot drift away from what the boot apply does."""
    return hardware.run_helper_logged(*args, source="cycle-profile",
                                      timeout=30)[0]

# Every setting this machine has; the helper refuses anything it cannot
# do, and this script has no capability probe of its own. ryzenadj is the
# exception -- see rogcontrol-apply: a chip it cannot talk to is a failed
# call, not a refused one, so the vendor is checked here instead.
# cpu_power_limits is asked about for the same reason and covers Intel's
# ppt/RAPL backend too -- see hardware.cpu_power_limits_backend.
ALL_CPU_CAPS = {"ryzenadj": hardware.cpu_is_amd(), "cpu_boost": True,
                "cpu_epp": True, "cpu_clock": True,
                "cpu_power_limits": hardware.cpu_power_limits_backend()}

CONFIG_PATH = config_mod.CONFIG_PATH

# See pages/fans.py: retested down to 0.5s with no failures, kept at 5s for
# margin over the retested floor.
CHANNEL_GAP_S = 5

# The package's, so a hotkey that half-applied a profile is announced the
# same way the enforcer announces an automatic switch -- and so this script
# stops carrying a copy that had no -a flag and so showed up unattributed.
notify = hardware.notify


def apply_profile(profile):
    cpu = profile.get("cpu")
    if cpu:
        # One definition of what a CPU apply writes and in what order,
        # shared with the window and the enforcer. It used to be a chain
        # of ifs here as well, and every setting added since has had to
        # be added to each copy by hand -- the clock floor reached three
        # of the four and was silently dropped by the fourth.
        for _step, args in hardware.cpu_apply_plan(cpu, ALL_CPU_CAPS):
            run_helper(*args)
    gpu = profile.get("gpu")
    if gpu:
        # Asked for, not assumed -- see rogcontrol-apply.py. Nothing catches
        # an exception in this script at all, so a profile with an empty gpu
        # section made the hotkey traceback and stop, leaving the fan curves
        # below unwritten.
        if "watts" in gpu:
            run_helper("gpu", gpu["watts"])
        # These must be applied here too. The enforcer only re-asserts them
        # on a full apply now, not every cycle, so switching profiles from
        # this shortcut has to set them itself or they would be left on the
        # previous profile's values.
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
        # Through the package's own call rather than a hand-built command
        # line: it has a timeout, where this had none. A hotkey that hangs
        # on nvidia-settings is a keypress that never finishes and a profile
        # switch left half-applied.
        for kind, key in (("core", "clock_offset"),
                          ("memory", "mem_clock_offset")):
            if key in gpu:
                ok, message = hardware.set_nvidia_clock_offset(kind, gpu[key])
                if not ok:
                    hardware.log(f"GPU {kind} clock offset failed: {message}",
                                 "ERROR", source="cycle-profile",
                                 dedupe_key=f"nv{kind}")
    # Only the channels whose curve is not already the one the controller is
    # running -- see rogcontrol-apply.py and app.py's _apply_profile_worker.
    # This script used to skip that check and pay the CHANNEL_GAP_S EC gap
    # for all three channels on every switch, including a switch back to a
    # profile whose fans matched exactly -- which is why the notify-send
    # after it felt slow even when nothing about the fans had changed.
    fans = profile.get("fans", {})
    # Not while a fan boost is running -- see rogcontrol-apply.py for why
    # writing the profile's curves over a live boost only makes the two
    # fight until the boost expires.
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


def main():
    if not os.path.exists(CONFIG_PATH):
        return
    # Through config.update_config rather than a bare load/save: the read
    # happens immediately before the write, so a GUI apply, the enforcer's AC
    # auto-switch or the tray landing between the two can no longer be
    # silently overwritten. This script is bound to a keyboard shortcut --
    # the one profile-switch path most likely to be fired twice in a second
    # -- so that gap mattered more here than anywhere else it was fixed.
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

    # Before applying, and before the fan writes in particular: the OS power
    # mode has to move with the profile or the enforcer switches the profile
    # back within a minute, and changing the mode is what makes the EC drop
    # the custom curve. Returns None, changing nothing, for a profile of the
    # user's own that maps to no OS mode.
    hardware.set_power_mode_for_profile(next_name)

    # The keys go with it. This shortcut is the one profile switch that
    # happens with nothing else on screen -- no window, no tray menu, no
    # notification yet -- so the keyboard changing colour is often the only
    # confirmation the user gets before the notify-send lands. Returns None
    # and writes nothing unless the saved lighting mode is Profile Color.
    hardware.set_profile_kbd_color(config, next_name)

    # Guarded, because the config already says this profile is current --
    # it is saved above so the enforcer does not switch it straight back --
    # and an exception here would leave that claim standing over a machine
    # the settings never finished reaching, with a bare traceback on a
    # stderr no one is reading and no notification at all. The empty-gpu
    # KeyError that used to do exactly that is fixed, but it was only ever
    # one way in.
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


if __name__ == "__main__":
    main()
