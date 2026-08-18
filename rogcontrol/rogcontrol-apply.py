#!/usr/bin/env python3
"""
rogcontrol-apply.py
Reapplies your last-saved ROG Control profile plus independent settings
(keyboard brightness, charge limit, boot sound, GPU clock offsets)
at login. Retries several times with delays, since some services
(nvidia, asus-wmi) may not be fully ready right at login.

--profile-only applies the profile and leaves the keyboard alone.

That flag exists because this script has two callers with two different
jobs. At login it is the whole of "put the machine back the way it was",
keyboard included. But the tray also runs it to make a profile switch real,
and a profile switch has no business touching the keyboard: keyboard
brightness is a GLOBAL setting -- there is one kbd_brightness in the config
and no profile has its own -- so re-applying it on every switch does not
express the new profile, it just overwrites whatever the keyboard is
currently doing with the last value written to the config. With
kbd_brightness at 0 that means the backlight goes out every time the user
picks a different profile, which is what they reported.

The charge limit is global in exactly the same way and IS still applied
here. The difference is who else writes it: the keyboard backlight is also
changed by the Fn keys, the kbdlight cycler and the ambient sampler, so a
stale config value fights the user. Nothing but this app ever writes the
charge threshold, so re-asserting it costs one helper call, changes nothing
the user can see, and covers the case where the EC has forgotten it -- and
a charge limit that has silently lapsed is invisible until the battery is
already damaged.

The boot sound is global on the same terms, and applied on the same terms:
nothing else on the machine writes it, the write is idempotent, and a chime
that has come back because a BIOS update reset the firmware is only heard at
the next power-on -- by which time nobody is looking at this app.
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

from rogcontrol import fancurve  # noqa: E402
from rogcontrol import hardware  # noqa: E402

# The curve maths and the helper call are the package's, not this script's.
# Both used to be copied in here verbatim -- interpolate_curve existed in
# four files and run_helper in seven -- which is how the silent-failure bug
# fixed in the enforcer survived here for another release.
interpolate_curve = fancurve.interpolate_curve
pct_to_pwm255 = fancurve.pct_to_pwm255


def run_helper(*args):
    """Run a privileged action and REPORT failure.

    A thin wrapper over hardware.run_helper: the shared one knows how to run
    the helper and how to read a failure out of it, and this adds only the
    one thing that is specific to a headless script -- writing the failure
    somewhere the user can find it afterwards."""
    ok, message = hardware.run_helper(*args, timeout=30)
    if not ok:
        cmd = " ".join(str(a) for a in args)
        hardware.log(f"{cmd} failed: {message}", "ERROR",
                     source="apply", dedupe_key=f"fail:{args[0]}")
    return ok

# Every setting this machine has; the helper refuses anything it cannot
# do, and this script has no capability probe of its own.
ALL_CPU_CAPS = {"ryzenadj": True, "cpu_boost": True, "cpu_epp": True,
                "cpu_clock": True}

CONFIG_PATH = os.path.expanduser("~/.config/rogcontrol.json")
RETRIES = 3
DELAY_SECONDS = 10

# See pages/fans.py: retested down to 0.5s with no failures, kept at 5s for
# margin over the retested floor.
CHANNEL_GAP_S = 5



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


def apply_once(config, profile_only=False):
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
            # One definition of what a CPU apply writes and in what order,
            # shared with the window and the enforcer. It used to be a chain
            # of ifs here as well, and every setting added since has had to
            # be added to each copy by hand -- the clock floor reached three
            # of the four and was silently dropped by the fourth.
            for _step, args in hardware.cpu_apply_plan(cpu, ALL_CPU_CAPS):
                run_helper(*args)
        gpu = profile.get("gpu")
        if gpu and hardware.dgpu_available():
            run_helper("gpu", gpu["watts"])
            apply_gpu_clock_offsets(gpu)

        # Only the channels whose curve is not already the one the
        # controller is running. Each write costs a CHANNEL_GAP_S gap before
        # the next, so a switch back to a profile whose fans match costs
        # nothing instead of ten seconds. Read from the driver rather than
        # remembered: the EC drops curves behind this app's back on every
        # power-mode change, and a channel it has thrown away has to be
        # rewritten even though nothing in the config moved.
        wanted = profile.get("fans", {})
        held = {ch: hardware.read_fan_curve_points(ch) for ch in wanted}
        enabled = hardware.read_fan_curve_enabled()
        todo = [(ch, pts) for ch, pts in wanted.items()
                if not (enabled.get(ch) is not False
                        and held.get(ch) is not None
                        and fancurve.curve_matches_hardware(pts, held[ch]))]
        for i, (channel, points) in enumerate(todo):
            if i > 0:
                # See pages/fans.py module docstring: 0.5s was first found
                # to leave channels stuck, but a later retest found 0.5s-8s
                # all held. CHANNEL_GAP_S is kept above the retested floor.
                time.sleep(CHANNEL_GAP_S)
            expanded = interpolate_curve(points, 8)
            flat = []
            for t, pct in expanded:
                flat += [t, pct_to_pwm255(pct)]
            run_helper("fan", channel, *flat)

    # Global settings, below the profile because neither belongs to one.
    # See the module docstring for why only one of them is skipped on a
    # profile switch.
    if "kbd_brightness" in config and not profile_only:
        run_helper("kbd", config["kbd_brightness"])
    if "charge_limit" in config:
        run_helper("charge", config["charge_limit"])
    # Absent means the user has never set it, so the firmware's own value is
    # left alone -- there is no sensible default to assert over a setting
    # that lives in the firmware and that this app did not choose. Once set,
    # it is re-asserted here so a firmware reset (or a BIOS update, which
    # returns it to the ASUS default) does not silently bring the chime back.
    if "boot_sound" in config:
        run_helper("bootsound", 1 if config["boot_sound"] else 0)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    profile_only = "--profile-only" in argv
    if not os.path.exists(CONFIG_PATH):
        return
    # The retries are for login, and only for login. At login nvidia and
    # asus-wmi may not have finished coming up, so the whole apply is run
    # three times ten seconds apart and the last one wins.
    #
    # A profile switch is the other caller, and there the retries were pure
    # cost: the machine is already up, nothing is going to become ready, and
    # the loop ran the entire apply -- sixteen seconds of fan writes included
    # -- three times with two ten second sleeps in between. Measured at 69
    # seconds for a switch that has 16 seconds of work in it.
    attempts = 1 if profile_only else RETRIES
    for attempt in range(attempts):
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
            apply_once(config, profile_only=profile_only)
        except Exception:
            pass
        if attempt < attempts - 1:
            time.sleep(DELAY_SECONDS)


if __name__ == "__main__":
    main()
