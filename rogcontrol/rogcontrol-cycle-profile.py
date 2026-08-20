#!/usr/bin/env python3
"""
rogcontrol-cycle-profile.py
Cycles to the next profile and applies it, without needing the GUI open.
Bind this to a keyboard shortcut.
"""
import os
import subprocess
import sys
import time

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
    """Run a privileged action and REPORT failure."""
    ok, message = hardware.run_helper(*args, timeout=30)
    if not ok:
        cmd = " ".join(str(a) for a in args)
        hardware.log(f"{cmd} failed: {message}", "ERROR",
                     source="cycle-profile", dedupe_key=f"fail:{args[0]}")
    return ok

# Every setting this machine has; the helper refuses anything it cannot
# do, and this script has no capability probe of its own.
ALL_CPU_CAPS = {"ryzenadj": True, "cpu_boost": True, "cpu_epp": True,
                "cpu_clock": True}

CONFIG_PATH = config_mod.CONFIG_PATH

# See pages/fans.py: retested down to 0.5s with no failures, kept at 5s for
# margin over the retested floor.
CHANNEL_GAP_S = 5



def notify(title, body):
    try:
        subprocess.run(["notify-send", title, body], timeout=5)
    except Exception:
        pass


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
        if "clock_offset" in gpu:
            subprocess.run(
                ["nvidia-settings", "-a",
                 f"[gpu:0]/GPUGraphicsClockOffsetAllPerformanceLevels={gpu['clock_offset']}"],
                capture_output=True, text=True,
            )
        if "mem_clock_offset" in gpu:
            subprocess.run(
                ["nvidia-settings", "-a",
                 f"[gpu:0]/GPUMemoryTransferRateOffsetAllPerformanceLevels={gpu['mem_clock_offset']}"],
                capture_output=True, text=True,
            )
    # Only the channels whose curve is not already the one the controller is
    # running -- see rogcontrol-apply.py and app.py's _apply_profile_worker.
    # This script used to skip that check and pay the CHANNEL_GAP_S EC gap
    # for all three channels on every switch, including a switch back to a
    # profile whose fans matched exactly -- which is why the notify-send
    # after it felt slow even when nothing about the fans had changed.
    fans = profile.get("fans", {})
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
        run_helper("fan", channel, *flat)


def main():
    if not os.path.exists(CONFIG_PATH):
        return
    config = config_mod.load_config()

    names = list(config.get("profiles", {}).keys())
    if not names:
        return
    current = config.get("current_profile")
    idx = names.index(current) if current in names else -1
    next_name = names[(idx + 1) % len(names)]

    config["current_profile"] = next_name
    # Through config.save_config, which writes a temporary file and renames
    # it over the config. Writing in place -- which this did -- truncates the
    # real file the moment it is opened, so an interrupted write left the
    # user with no profiles at all. That is the entire reason config.py
    # exists, and this script is bound to a keyboard shortcut, so it is the
    # one most likely to be fired twice in a second.
    config_mod.save_config(config)

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

    apply_profile(config["profiles"][next_name])
    notify("ROG Control", f"Profile switched to {next_name}")


if __name__ == "__main__":
    main()
