#!/usr/bin/env python3
"""
rogcontrol-apply.py
Reapplies your last-saved ROG Control profile plus independent settings
(keyboard brightness, charge limit, GPU clock offsets)
at login. Retries several times with delays, since some services
(nvidia, asus-wmi) may not be fully ready right at login.
"""

import json
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

from rogcontrol import hardware  # noqa: E402

CONFIG_PATH = os.path.expanduser("~/.config/rogcontrol.json")
RETRIES = 3
DELAY_SECONDS = 10


def run_helper(*args):
    subprocess.run(
        ["sudo", "-n", "/usr/local/bin/rogcontrol-helper", *[str(a) for a in args]],
        capture_output=True, text=True,
    )


def interpolate_curve(points, n=8):
    """Expand a user curve to exactly n points for the firmware.

    The user's own points are preserved verbatim whenever they fit (the
    hardware takes 8, and so does the editor -- older profiles carry six).
    Extra slots are filled by bisecting the widest temperature gap, so the
    added points sit on the straight line the user already drew between
    their own points.

    The previous version resampled by *index*, which silently moved every
    interior point: a 6-point curve came back as 8 points at completely
    different temperatures, so a point placed at 60C ended up as steps at
    57C and 61C and the curve the firmware ran was not the one on screen.
    That matters because the EC steps between points rather than
    interpolating, so a moved point moves where the fan audibly changes.
    """
    pts = sorted({(int(t), int(p)) for t, p in points})
    if len(pts) >= n:
        return pts[:n]

    while len(pts) < n:
        # widest gap first, so added points are spread out evenly
        gaps = [(pts[i + 1][0] - pts[i][0], i) for i in range(len(pts) - 1)]
        gap, i = max(gaps) if gaps else (0, 0)
        if gap >= 2:
            t = (pts[i][0] + pts[i + 1][0]) // 2
            p = round((pts[i][1] + pts[i + 1][1]) / 2)
            pts.insert(i + 1, (t, p))
            continue
        # No gap left to split (points are adjacent degrees). Extend past
        # the top point instead, holding its percentage, so temps stay
        # strictly increasing -- the firmware needs 8 distinct entries.
        last_t, last_p = pts[-1]
        if last_t < 100:
            pts.append((min(100, last_t + 1), last_p))
        else:
            first_t, first_p = pts[0]
            if first_t <= 0:
                break  # nowhere left to go; return what we have
            pts.insert(0, (first_t - 1, first_p))
    return pts[:n]


def pct_to_pwm255(pct):
    return round(max(0, min(100, pct)) / 100 * 255)


def apply_gpu_clock_offsets(gpu):
    if "clock_offset" in gpu:
        subprocess.run(
            ["nvidia-settings", "-a",
             f"[gpu:0]/GPUGraphicsClockOffsetAllPerformanceLevels={gpu['clock_offset']}"],
            capture_output=True, text=True)
    if "clock_limit" in gpu:
        # Against the card's own maximum, not a hardcoded 3090: the top of
        # the slider means "no ceiling", and comparing against another
        # card's number turns that into a lock (or refuses a real cap).
        run_helper("gpuclocklimit",
                   hardware.gpu_clock_limit_arg(
                       gpu["clock_limit"], hardware.gpu_clock_limit_max()))
    if "dyn_boost" in gpu:
        run_helper("nvboost", gpu["dyn_boost"])
    if "temp_target" in gpu:
        run_helper("nvtemp", gpu["temp_target"])
    if "mem_clock_offset" in gpu:
        subprocess.run(
            ["nvidia-settings", "-a",
             f"[gpu:0]/GPUMemoryTransferRateOffsetAllPerformanceLevels={gpu['mem_clock_offset']}"],
            capture_output=True, text=True)


def apply_once(config):
    profile_name = config.get("current_profile")
    profile = config.get("profiles", {}).get(profile_name)

    # First, before anything else is written. This script is what the tray
    # runs to make a profile switch real, and without it the tray switched
    # the profile while leaving power-profiles-daemon on the old mode -- so
    # the enforcer read the disagreement as the OS asking for the old
    # profile, switched back within a minute and re-pushed all three fan
    # curves to do it. It also has to come before the fan writes: changing
    # the mode is what makes the EC drop the custom curve, so a curve
    # written first is handed to a controller about to throw it away.
    #
    # A profile that maps to no OS mode returns None and changes nothing.
    hardware.set_power_mode_for_profile(profile_name)

    if profile:
        cpu = profile.get("cpu")
        if cpu:
            run_helper("cpu", cpu["stapm"], cpu["fast"], cpu["slow"], cpu["temp"], cpu.get("coall", 0))
            # Absent means the profile has no boost preference, so leave the
            # cpufreq switch wherever the firmware left it.
            if "boost" in cpu:
                run_helper("cpuboost", 1 if cpu["boost"] else 0)
            if "epp" in cpu:
                run_helper("cpuepp", cpu["epp"])
            # After boost: the boost write refreshes every cpufreq policy and
            # takes the ceiling back to hardware max with it.
            if "max_freq" in cpu:
                run_helper("cpuclock", cpu["max_freq"] or "max")

        gpu = profile.get("gpu")
        if gpu:
            run_helper("gpu", gpu["watts"])
            apply_gpu_clock_offsets(gpu)

        for i, (channel, points) in enumerate(profile.get("fans", {}).items()):
            if i > 0:
                # See rogcontrol-enforcer.py: the asus-wmi embedded
                # controller silently drops fan-curve writes fired too
                # close together for different channels. 0.5s measured NOT
                # enough (channels stayed stuck); 8s measured reliable
                # (all channels converged correctly, repeatedly, with
                # distinct realistic targets and zero reversion).
                time.sleep(8)
            expanded = interpolate_curve(points, 8)
            flat = []
            for t, pct in expanded:
                flat += [t, pct_to_pwm255(pct)]
            run_helper("fan", channel, *flat)

    if "kbd_brightness" in config:
        run_helper("kbd", config["kbd_brightness"])
    if "charge_limit" in config:
        run_helper("charge", config["charge_limit"])


def main():
    if not os.path.exists(CONFIG_PATH):
        return
    for attempt in range(RETRIES):
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
            apply_once(config)
        except Exception:
            pass
        if attempt < RETRIES - 1:
            time.sleep(DELAY_SECONDS)


if __name__ == "__main__":
    main()
