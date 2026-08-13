#!/usr/bin/env python3
"""
rogcontrol-cycle-profile.py
Cycles to the next profile and applies it, without needing the GUI open.
Bind this to a keyboard shortcut.
"""
import json
import os
import subprocess
import time

CONFIG_PATH = os.path.expanduser("~/.config/rogcontrol.json")


def run_helper(*args):
    subprocess.run(
        ["sudo", "-n", "/usr/local/bin/rogcontrol-helper", *[str(a) for a in args]],
        capture_output=True, text=True,
    )


def interpolate_curve(points, n=8):
    """Expand a user curve to exactly n points for the firmware.

    The user's own points are preserved verbatim whenever they fit (the
    hardware takes 8, the editor allows at most 6). Extra slots are filled
    by bisecting the widest temperature gap, so the added points sit on the
    straight line the user already drew between their own points.

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


def notify(title, body):
    try:
        subprocess.run(["notify-send", title, body], timeout=5)
    except Exception:
        pass


def apply_profile(profile):
    cpu = profile.get("cpu")
    if cpu:
        run_helper("cpu", cpu["stapm"], cpu["fast"], cpu["slow"], cpu["temp"], cpu.get("coall", 0))
        # Absent means the profile has no boost preference, so leave the
        # cpufreq switch alone rather than forcing a default.
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
        # These must be applied here too. The enforcer only re-asserts them
        # on a full apply now, not every cycle, so switching profiles from
        # this shortcut has to set them itself or they would be left on the
        # previous profile's values.
        if "clock_limit" in gpu:
            run_helper("gpuclocklimit",
                       "reset" if gpu["clock_limit"] >= 3090 else gpu["clock_limit"])
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
    for i, (channel, points) in enumerate(profile.get("fans", {}).items()):
        if i > 0:
            # See rogcontrol-enforcer.py: the asus-wmi embedded controller
            # silently drops fan-curve writes fired too close together for
            # different channels. 0.5s measured NOT enough (channels stayed
            # stuck); 8s measured reliable (all channels converged
            # correctly, repeatedly, with distinct realistic targets and
            # zero reversion).
            time.sleep(8)
        expanded = interpolate_curve(points, 8)
        flat = []
        for t, pct in expanded:
            flat += [t, pct_to_pwm255(pct)]
        run_helper("fan", channel, *flat)


def main():
    if not os.path.exists(CONFIG_PATH):
        return
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    names = list(config["profiles"].keys())
    if not names:
        return
    current = config.get("current_profile")
    idx = names.index(current) if current in names else -1
    next_name = names[(idx + 1) % len(names)]

    config["current_profile"] = next_name
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

    apply_profile(config["profiles"][next_name])
    notify("ROG Control", f"Profile switched to {next_name}")


if __name__ == "__main__":
    main()
