#!/usr/bin/env python3
"""
rogcontrol-enforcer.py
Continuously re-pushes the active profile (CPU limits, GPU power/clock
offsets, charge limit, keyboard brightness) - to fight the BIOS/firmware
periodically resetting things back to its own defaults. Runs as a
long-lived systemd --user service (Restart=always).

FAN CURVES ARE THE EXCEPTION: they are only re-pushed when the curve data
actually changes, or when an external power-mode change is detected. Each
fan channel needs an 8s gap from the next (asus-wmi EC limitation, see
apply_full_profile), so re-pushing all 3 unconditionally every cycle --
which is what this originally did -- could interrupt the fans before
they'd finished settling on the values just written.

POWER-PROFILES-DAEMON ENFORCEMENT: on this hardware, asus-wmi's
throttle_thermal_policy (which is what changing GNOME's Power Mode
actually does under the hood via power-profiles-daemon/platform_profile)
disables custom fan curves as a side effect every time it changes -- this
is documented, confirmed kernel driver behavior, not a bug in this app.
That means every GNOME power-mode change was silently wiping whatever
fan curve we'd set. Per your request, control is now one-way from this
app: our profile sets GNOME's power mode to match, and if power-profiles-
daemon's mode is ever changed by anything else (GNOME's own quick
settings, a hardware key, etc.), this enforcer forces it back to match
our active profile AND immediately re-applies the fan curve, rather than
waiting for the next 15s poll.

AC/BATTERY AUTO-SWITCH: the config's ac_profile/battery_profile are acted
on here too. They used to be checked by the GTK3 window's own poll loop,
which meant unplugging the laptop with the app closed did nothing at all --
and the app is closed most of the time. See the section further down.
"""

import glob
import json
import os
import subprocess
import sys
import threading
import time

# The shared modules sit beside this script's package in the repo, and under
# ~/.local/lib once installed -- this script is installed into ~/.local/bin,
# where there is no package next to it. The repo is tried first so that
# running from a checkout tests the checkout rather than what is installed.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.dirname(_HERE), os.path.expanduser("~/.local/lib")):
    if os.path.isfile(os.path.join(_candidate, "rogcontrol", "__init__.py")):
        if _candidate not in sys.path:
            sys.path.insert(0, _candidate)
        break

from rogcontrol import config as config_mod  # noqa: E402
from rogcontrol import hardware  # noqa: E402

CONFIG_PATH = os.path.expanduser("~/.config/rogcontrol.json")
# Upkeep cadence. Only the CPU limits and the fan-curve safety
# re-check run on this interval now, so it can be far slower than
# the old 15s without losing anything.
INTERVAL_SECONDS = 60

# Sysfs knobs whose value changing means the EC has just silently thrown
# away the custom fan curve (documented asus-wmi behavior). Cheap to read,
# so the enforcer samples them every cycle and forces a fan re-apply when
# either moves -- this is what stops a wiped curve from staying wiped.
PLATFORM_PROFILE_PATH = "/sys/firmware/acpi/platform_profile"
THROTTLE_POLICY_PATH = "/sys/devices/platform/asus-nb-wmi/throttle_thermal_policy"
_last_thermal_state = None

# Safety net: even with no detected trigger, re-push the curve this often.
# A dropped channel is otherwise invisible -- pwm<N>_enable reads back the
# driver's own cached flag, not what the EC actually accepted, so there is
# no way to detect a silently-ignored channel by reading sysfs. Long enough
# that the ~16s apply stays rare.
FAN_REVERIFY_SECONDS = 300
_last_fan_apply_time = 0.0

# (profile_name, fan-curve-json) of the last fan curve actually pushed to
# hardware. Each channel apply needs an 8s gap from the others (see
# apply_full_profile below), so blindly re-running all 3 channels on every
# INTERVAL_SECONDS tick -- as this enforcer originally did -- meant a fresh
# 3-channel push could interrupt the previous one's fans before they'd even
# finished settling, on top of the raw per-channel timing issue. Tracking
# what's already been applied lets the periodic loop skip re-pushing when
# nothing has changed, while still reacting immediately to a real profile
# switch or a detected external PPD mode change (which is confirmed to
# silently disable the curve on this hardware -- see module docstring).
_last_applied_fans = None

# An external power-mode change is only adopted once it has held still for
# this long, and never within SELF_APPLY_QUIET_SECONDS of this service's own
# full apply.
#
# Why: adopting a mode change switches profile, and a profile switch re-pushes
# all three fan channels (~16s of writes) with a completely different curve.
# Measured on this machine, the power mode flipped balanced/performance five
# times in seventeen minutes, so the fans were being handed a different curve
# every ~90 seconds and never settled on either -- which is exactly the
# "fans ignore the curve and ramp up out of nowhere" symptom. Debouncing turns
# a burst of mode flapping into at most one profile switch, and the quiet
# window stops this service from mistaking the mode change caused by its own
# apply for a fresh external request and chasing its own tail.
ADOPT_DEBOUNCE_SECONDS = 10
SELF_APPLY_QUIET_SECONDS = 30
_last_self_apply_time = 0.0

# Our profile name -> power-profiles-daemon's fixed mode names. PPD only
# ever has exactly these three modes; anything else is invalid to set. More
# than one profile may map to the same mode -- the two Balanced profiles
# differ in EPP, which PPD has no concept of.
PROFILE_TO_PPD_MODE = {
    "Performance": "performance",
    "Balanced Performance": "balanced",
    "Balanced Power": "balanced",
    "Quiet": "power-saver",
}

# The same mapping backwards, for adopting a power-mode change made outside
# this app (GNOME's power menu, a keyboard key, powerprofilesctl).
#
# First name wins, so "balanced" from the OS resolves to "Balanced
# Performance". A plain dict comprehension would have silently made it
# "Balanced Power", since the later key overwrites the earlier one.
PPD_MODE_TO_PROFILE = {}
for _name, _mode in PROFILE_TO_PPD_MODE.items():
    PPD_MODE_TO_PROFILE.setdefault(_mode, _name)


LOG_PATH = os.path.expanduser("~/.local/share/rogcontrol/rogcontrol.log")
LOG_MAX_BYTES = 256 * 1024
_last_logged = {}


def log(message, level="INFO", dedupe_key=None, dedupe_seconds=300):
    """Append one line to the shared app log.

    dedupe_key suppresses an identical repeating message for a while. A
    failing helper call repeats every cycle, and without this a single
    broken sudoers rule would fill the log with the same line forever."""
    if dedupe_key is not None:
        now = time.monotonic()
        if now - _last_logged.get(dedupe_key, -1e9) < dedupe_seconds:
            return
        _last_logged[dedupe_key] = now
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a") as f:
            f.write(f"{stamp} [{level}] enforcer: {message}\n")
    except OSError:
        pass


def run_helper(*args):
    """Run a privileged action and REPORT failure.

    This used to discard the exit code and both output streams, so the
    enforcer could not tell anyone that anything had gone wrong. A broken
    sudoers rule, a missing helper or a failing ryzenadj all looked exactly
    like normal operation while nothing was actually being applied."""
    cmd = " ".join(str(a) for a in args)
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/local/bin/rogcontrol-helper", *[str(a) for a in args]],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        log(f"could not run helper: {cmd} -> {e}", "ERROR", dedupe_key=f"run:{args[0]}")
        return False
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "unknown error").strip()
        log(f"{cmd} failed: {msg}", "ERROR", dedupe_key=f"fail:{args[0]}")
        return False
    return True


def get_ppd_service_name():
    """power-profiles-daemon has shipped under two D-Bus names across
    distro versions (net.hadess.PowerProfiles is the long-standing one,
    org.freedesktop.UPower.PowerProfiles is the newer canonical name it's
    migrating to). Try both rather than assuming."""
    for name in ("net.hadess.PowerProfiles", "org.freedesktop.UPower.PowerProfiles"):
        result = subprocess.run(
            ["busctl", "--system", "introspect", name, f"/{name.replace('.', '/')}"],
            capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return name
    return None


def get_ppd_active_profile(service_name):
    if not service_name:
        return None
    path = "/" + service_name.replace(".", "/")
    result = subprocess.run(
        ["busctl", "--system", "get-property", service_name, path,
         service_name, "ActiveProfile"],
        capture_output=True, text=True, timeout=5)
    if result.returncode != 0:
        return None
    # busctl prints: s "balanced"
    parts = result.stdout.strip().split(None, 1)
    if len(parts) == 2:
        return parts[1].strip('"')
    return None


def set_ppd_active_profile(service_name, mode):
    if not service_name:
        return
    # Our own write comes back as a PropertiesChanged signal that is
    # indistinguishable from a user changing the mode. Stamp it so the
    # adoption gate can ignore the echo.
    global _last_self_apply_time
    _last_self_apply_time = time.monotonic()
    path = "/" + service_name.replace(".", "/")
    subprocess.run(
        ["busctl", "--system", "set-property", service_name, path,
         service_name, "ActiveProfile", "s", mode],
        capture_output=True, text=True, timeout=5)


def boost_control_available():
    """True if cpufreq exposes a boost switch, globally or per policy."""
    return (os.path.exists("/sys/devices/system/cpu/cpufreq/boost")
            or bool(glob.glob("/sys/devices/system/cpu/cpufreq/policy*/boost")))


def epp_control_available():
    """True if cpufreq exposes an Energy Performance Preference."""
    return bool(glob.glob("/sys/devices/system/cpu/cpufreq/policy*/"
                          "energy_performance_preference"))


def clock_limit_available():
    """True if cpufreq exposes a per-policy maximum clock."""
    return bool(glob.glob("/sys/devices/system/cpu/cpufreq/policy*/"
                          "scaling_max_freq"))


def apply_full_profile(config, profile, force_fan_reapply=False, full=True):
    """Apply a profile to the hardware.

    full=True applies everything, and is what a profile switch, a detected
    external power-mode change, or startup needs.

    full=False is the periodic upkeep pass, and deliberately applies only
    the settings that were measured to actually drift. With the enforcer
    stopped for 90 seconds, the GPU power limit, clock limit, temperature
    target, dynamic boost, charge threshold and keyboard brightness all
    held their values exactly -- they are firmware-backed and stay put.
    Re-writing them every cycle achieved nothing except ~36,000 sudo
    invocations a day, a journal entry for each, and in the keyboard's case
    actively undoing changes made with the Fn keys.

    force_fan_reapply should be True from callers reacting to a detected
    external PPD mode change -- that's exactly when the EC is known to have
    just silently disabled the curve, so the cache must be bypassed."""
    global _last_applied_fans, _last_fan_apply_time
    if profile:
        # CPU limits are kept in the periodic pass: AMD firmware is known to
        # walk STAPM/PPT back on its own, so this one genuinely needs
        # re-asserting. It is cheap -- a single ryzenadj call.
        cpu = profile.get("cpu")
        if cpu:
            run_helper("cpu", cpu["stapm"], cpu["fast"], cpu["slow"],
                       cpu["temp"], cpu.get("coall", 0))
            # Kept in the periodic pass alongside the CPU limits, not behind
            # `full`: `full` is only true when the thermal state moved, so a
            # service restart or a fresh boot would otherwise never assert the
            # profile's boost setting at all. One cheap sysfs write.
            #
            # A profile with no "boost" key means the user never expressed a
            # preference, and nothing is written -- otherwise every existing
            # profile would silently start forcing boost on.
            if "boost" in cpu and boost_control_available():
                run_helper("cpuboost", 1 if cpu["boost"] else 0)
            # Same rule for EPP: only written when the profile actually names
            # one. Cheap, and it belongs next to boost because both are
            # cpufreq settings the firmware can reset across suspend.
            if "epp" in cpu and epp_control_available():
                run_helper("cpuepp", cpu["epp"])
            # After boost on purpose: writing cpufreq's boost switch refreshes
            # every policy and resets the ceiling to hardware max, so a cap
            # written first would be wiped by the boost write above.
            #
            # 0 means "this profile wants no ceiling", which still has to be
            # written -- otherwise a cap set by the previous profile would
            # survive the switch. Only a missing key means "leave alone".
            if "max_freq" in cpu and clock_limit_available():
                run_helper("cpuclock", cpu["max_freq"] or "max")

        if full:
            gpu = profile.get("gpu")
            if gpu:
                run_helper("gpu", gpu["watts"])
                apply_gpu_clock_offsets(gpu)

        fans = profile.get("fans", {})
        fans_signature = (config.get("current_profile"), json.dumps(fans, sort_keys=True))
        stale = (time.monotonic() - _last_fan_apply_time) >= FAN_REVERIFY_SECONDS
        if force_fan_reapply or stale or fans_signature != _last_applied_fans:
            for i, (channel, points) in enumerate(fans.items()):
                if i > 0:
                    # The asus-wmi embedded controller silently drops fan-
                    # curve writes fired too close together for different
                    # channels. Measured directly on this hardware: applying
                    # one channel in isolation reliably took effect (fan
                    # smoothly ramped to the new target); a 0.5s gap between
                    # channels was NOT enough (2 of 3 channels stayed stuck
                    # on their old value indefinitely); an 8s gap was
                    # sufficient for all 3 channels to converge correctly,
                    # repeatedly, with zero reversion. This -- combined with
                    # the unconditional re-push every INTERVAL_SECONDS this
                    # function used to do regardless of whether anything had
                    # changed, which could interrupt a channel before it
                    # finished settling -- is why the curve looked like it
                    # was being "ignored" even though this enforcer was
                    # correctly re-pushing it the whole time.
                    time.sleep(8)
                expanded = interpolate_curve(points, 8)
                flat = []
                for t, pct in expanded:
                    flat += [t, pct_to_pwm255(pct)]
                run_helper("fan", channel, *flat)
            _last_applied_fans = fans_signature
            _last_fan_apply_time = time.monotonic()

def mode_change_is_settled(service_name, actual_mode):
    """True when an external mode change is worth acting on.

    Rejects two cases: the echo of this service forcing the mode back itself,
    and a mode that does not still hold ADOPT_DEBOUNCE_SECONDS later. Both
    produced profile switches nobody asked for, and every profile switch
    re-pushes all three fan curves.

    Note the quiet window covers only set_ppd_active_profile, not every full
    re-apply: a mode change also triggers a thermal-state re-apply on the next
    cycle, and stamping that too kept pushing the window forward so a real
    change was never adopted at all."""
    if time.monotonic() - _last_self_apply_time < SELF_APPLY_QUIET_SECONDS:
        return False
    time.sleep(ADOPT_DEBOUNCE_SECONDS)
    if not service_name:
        return True
    still = get_ppd_active_profile(service_name)
    if still != actual_mode:
        log(f"power mode moved to '{actual_mode}' then to '{still}' within "
            f"{ADOPT_DEBOUNCE_SECONDS}s -- ignoring, nothing re-applied",
            dedupe_key="ppdflap")
        return False
    return True


def save_config(config, why):
    """Write the config back out, atomically, and log a failure rather than
    raising into the cycle.

    Atomic because the app polls this file: an in-place write is visible
    half-written, and what a reader would see is a truncated config with no
    profiles in it. ``why`` names the caller, because more than one thing in
    here now writes this file and "could not save" on its own says nothing
    about which."""
    try:
        config_mod.save_config(config, CONFIG_PATH)
    except OSError as e:
        log(f"could not save {why}: {e}", "ERROR", dedupe_key="save")


def adopt_external_ppd_mode(config, actual_mode, service_name):
    """React to a power mode changed outside this app.

    The mode is treated as a request to switch profile, not as something to
    undo: the OS picks WHICH profile, this app still decides what that
    profile does to the hardware. So GNOME's power menu selecting
    "Performance" switches the app to the Performance profile and applies
    that profile's own CPU limits, fan curve and GPU settings -- rather than
    the previous behaviour, which silently forced the OS back within a
    minute and made the sync look one-way.

    A mode with no matching profile (someone renamed or deleted the stock
    ones) falls back to forcing the OS back, since there is nothing sensible
    to switch to.

    Returns True if the mode was handled.
    """
    if not mode_change_is_settled(service_name, actual_mode):
        return False
    # The gate above waits, so the config on disk may have moved on (the app
    # or the hotkey cycler can switch profile in that window). Re-read it
    # rather than writing back a stale copy.
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    target = PPD_MODE_TO_PROFILE.get(actual_mode)
    profiles = config.get("profiles", {})
    if not target or target not in profiles:
        current_profile_name = config.get("current_profile")
        expected_mode = PROFILE_TO_PPD_MODE.get(current_profile_name)
        if expected_mode and service_name:
            log(f"power mode '{actual_mode}' has no matching profile; "
                f"restoring '{expected_mode}'", "WARN", dedupe_key="ppdadopt")
            set_ppd_active_profile(service_name, expected_mode)
            apply_full_profile(config, profiles.get(current_profile_name),
                               force_fan_reapply=True)
        return False

    if target == config.get("current_profile"):
        return True

    log(f"power mode changed externally to '{actual_mode}' -- "
        f"switching profile to '{target}'")
    config["current_profile"] = target
    save_config(config, "adopted profile")

    # A mode change is exactly when the EC drops the custom fan curve, so
    # this must be a full re-apply, not the usual drift check.
    apply_full_profile(config, profiles.get(target),
                       force_fan_reapply=True, full=True)
    return True


# -- AC / battery auto-switch -------------------------------------------------
#
# This used to live in the GTK3 window's own poll loop, which meant it only
# worked while the window happened to be open: unplugging the laptop with the
# app closed did nothing at all. It belongs here instead -- this service is
# always running, and it already owns "apply the profile the config names".
#
# The power source is sampled once per cycle, so a switch lands up to
# INTERVAL_SECONDS (60s) after the plug moves. That delay is deliberate:
# reacting faster would mean either a second poll loop or a udev/upower
# subscription, and the thing being changed takes ~16 seconds to apply
# anyway (each fan channel needs an 8 second gap from the next), so a minute
# of granularity costs nothing a user can feel.

# Whether the machine was on mains last time we looked. None until the first
# sample, which is what stops startup from counting as a transition -- the
# profile the config names is applied at startup regardless, and treating
# "we have just started up on battery" as an unplug would override a profile
# the user picked deliberately.
_last_ac_state = None


def ac_switch_target(previous_ac, current_ac, config):
    """The profile the power source change calls for, or None for "do
    nothing".

    Pure: state in, a name out, no I/O -- which is what makes the rules
    below testable, and they are all rules about when NOT to act:

    * ``current_ac`` None means there is no Mains supply to read (a desktop,
      or a kernel that does not expose one). Nothing can be inferred.
    * ``previous_ac`` None is the first sample. Remember it, act on the next
      one; see _last_ac_state.
    * Unchanged state is the overwhelmingly common case -- this runs every
      60 seconds and the plug moves maybe twice a day. Acting on it would
      re-apply the auto-switch profile over anything the user chose in
      between, once a minute, forever.
    * A null (or missing) ``ac_profile``/``battery_profile`` is how the
      Battery page stores "don't auto-switch" for that power source. It is
      not a failure and must not fall back to a default.
    * A stored profile that no longer exists -- renamed or deleted since it
      was chosen -- has nothing to switch to.
    * A target that is already current needs no switch. Applying it anyway
      would push all three fan curves for no reason.
    """
    if current_ac is None or previous_ac is None:
        return None
    if current_ac == previous_ac:
        return None
    key = "ac_profile" if current_ac else "battery_profile"
    target = config.get(key)
    if not target:
        return None
    if target not in (config.get("profiles") or {}):
        return None
    if target == config.get("current_profile"):
        return None
    return target


def check_ac_auto_switch(config, service_name):
    """Sample the power source and switch profile if it has just changed.

    Returns True if a profile was switched and applied, so the caller knows
    the hardware has already been dealt with this cycle."""
    global _last_ac_state
    current_ac = hardware.is_ac_connected()
    previous_ac = _last_ac_state
    if current_ac is not None:
        _last_ac_state = current_ac

    target = ac_switch_target(previous_ac, current_ac, config)
    if target is None:
        return False

    source = "AC" if current_ac else "battery"

    # Re-read before writing back, exactly as adopt_external_ppd_mode does.
    # ``config`` was read at the top of this cycle and the window, the tray
    # or the hotkey cycler can have written the file since; saving the stale
    # copy would throw away whatever they changed -- a curve the user had
    # just edited, a charge limit, a keyboard colour. Updated in place, not
    # rebound, because the caller keeps using this dict for the rest of the
    # cycle.
    try:
        with open(CONFIG_PATH) as f:
            fresh = json.load(f)
        if isinstance(fresh, dict):
            config.clear()
            config.update(fresh)
    except (OSError, json.JSONDecodeError):
        pass

    # And re-decide against what is actually on disk now: the user may have
    # chosen this very profile in the window while we were reading. Applying
    # it again would push all three fan curves for nothing.
    if target not in (config.get("profiles") or {}):
        return False
    if target == config.get("current_profile"):
        log(f"power source changed to {source} -- already on '{target}'")
        return False

    log(f"power source changed to {source} -- switching profile to '{target}'")
    config["current_profile"] = target
    save_config(config, "auto-switched profile")

    # Take the OS power mode with us, exactly as a profile switch in the app
    # does. Without this the enforcer's own PPD check would find the mode
    # disagreeing with the profile we just chose on the very next cycle, and
    # adopt the stale mode back -- undoing the auto-switch within a minute.
    # It also stamps the self-apply quiet window, so the adoption gate knows
    # the mode change that follows is ours.
    mode = PROFILE_TO_PPD_MODE.get(target)
    if mode and service_name:
        set_ppd_active_profile(service_name, mode)

    # Full apply, forcing the fans: a power-mode change is exactly when the
    # EC silently drops the custom curve, so the curve has to be pushed after
    # the mode write above rather than left to the signature check.
    apply_full_profile(config, (config.get("profiles") or {}).get(target),
                       force_fan_reapply=True, full=True)
    return True


def ppd_watcher_thread():
    """Runs in the background for the life of the service. Listens for
    PropertiesChanged on the PPD service and, whenever ActiveProfile
    changes to something that doesn't match our current app profile,
    forces it back AND immediately re-applies the fan curve -- rather
    than waiting up to INTERVAL_SECONDS for the next poll, which would
    leave the fan curve silently wiped (asus-wmi's documented behavior)
    for that whole window."""
    service_name = get_ppd_service_name()
    if not service_name:
        return  # PPD not present on this system; nothing to watch

    while True:
        try:
            # busctl monitor blocks and streams signal lines as they occur;
            # we filter for PropertiesChanged on our service.
            #
            # NOTE: becoming a bus monitor on the SYSTEM bus requires root.
            # Running unprivileged (which this user service does), busctl
            # exits immediately with "Call to
            # org.freedesktop.DBus.Monitoring.BecomeMonitor failed: Access
            # denied". That closes stdout without raising, so the loop below
            # ends normally -- and this outer `while True` used to respawn
            # instantly with no delay, turning the whole thread into a tight
            # fork/exec loop. Measured cost: ~8.5% of a CPU core burned
            # continuously, plus the dbus-broker and polkitd churn from each
            # rejected connection, which kept the package from reaching deep
            # idle and held the average core clock ~1.8GHz higher than with
            # the service stopped. Hence: verify the monitor actually works
            # before looping on it, and always back off between retries.
            proc = subprocess.Popen(
                ["busctl", "--system", "monitor", service_name],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            got_output = False
            for line in proc.stdout:
                got_output = True
                if "PropertiesChanged" not in line and "ActiveProfile" not in line:
                    continue
                if not os.path.exists(CONFIG_PATH):
                    continue
                try:
                    with open(CONFIG_PATH) as f:
                        config = json.load(f)
                except (OSError, json.JSONDecodeError):
                    continue
                current_profile_name = config.get("current_profile")
                expected_mode = PROFILE_TO_PPD_MODE.get(current_profile_name)
                if not expected_mode:
                    continue
                actual_mode = get_ppd_active_profile(service_name)
                if actual_mode and actual_mode != expected_mode:
                    # Something other than this app changed the power mode.
                    # Adopt it as a profile switch and re-apply everything --
                    # asus-wmi disables the custom fan curve as a side effect
                    # of any mode change, so it has to be re-pushed now.
                    adopt_external_ppd_mode(config, actual_mode, service_name)

            # Reached only when busctl exited on its own.
            proc.wait()
            if not got_output:
                # Monitor never produced a single line -- it isn't usable
                # here (almost certainly the unprivileged-system-bus case
                # described above). Retrying can only burn CPU, so stop the
                # thread entirely. The polling loop in main() still enforces
                # PPD every INTERVAL_SECONDS; the only thing lost is
                # sub-INTERVAL_SECONDS reaction time to an external change.
                print("rogcontrol-enforcer: D-Bus monitor unavailable "
                      f"(busctl exited {proc.returncode} with no output); "
                      "falling back to polling only.", file=sys.stderr)
                return
            time.sleep(5)  # monitor ended after working; back off, reconnect
        except Exception as e:
            log(f"PPD watcher error: {e}", "WARN", dedupe_key="ppdwatch")
            time.sleep(5)  # busctl monitor died or errored; back off and retry


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


def read_thermal_state():
    """Current (platform_profile, throttle_thermal_policy). Either changing
    means the EC has just discarded the custom fan curve."""
    state = []
    for path in (PLATFORM_PROFILE_PATH, THROTTLE_POLICY_PATH):
        try:
            with open(path) as f:
                state.append(f.read().strip())
        except OSError:
            state.append(None)
    return tuple(state)


def thermal_state_changed():
    """True when the EC-wipe trigger fired since the last check. Returns
    False on the very first call so startup isn't treated as a change --
    the initial apply happens anyway via the fan-signature check."""
    global _last_thermal_state
    current = read_thermal_state()
    changed = _last_thermal_state is not None and current != _last_thermal_state
    _last_thermal_state = current
    return changed


def apply_gpu_clock_offsets(gpu):
    if "clock_offset" in gpu:
        subprocess.run(
            ["nvidia-settings", "-a",
             f"[gpu:0]/GPUGraphicsClockOffsetAllPerformanceLevels={gpu['clock_offset']}"],
            capture_output=True, text=True)
    if "clock_limit" in gpu:
        run_helper("gpuclocklimit",
                   "reset" if gpu["clock_limit"] >= 3090 else gpu["clock_limit"])
    if "dyn_boost" in gpu:
        run_helper("nvboost", gpu["dyn_boost"])
    if "temp_target" in gpu:
        run_helper("nvtemp", gpu["temp_target"])
    if "mem_clock_offset" in gpu:
        subprocess.run(
            ["nvidia-settings", "-a",
             f"[gpu:0]/GPUMemoryTransferRateOffsetAllPerformanceLevels={gpu['mem_clock_offset']}"],
            capture_output=True, text=True)


def main():
    # Background thread reacts to power-mode changes immediately; the
    # main loop below is the periodic fallback in case a signal is missed.
    watcher = threading.Thread(target=ppd_watcher_thread, daemon=True)
    watcher.start()

    ppd_service_name = get_ppd_service_name()

    while True:
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH) as f:
                    config = json.load(f)

                # First, because it can change which profile the rest of this
                # cycle is about. It applies the new profile itself when it
                # switches.
                switched = check_ac_auto_switch(config, ppd_service_name)

                current_profile_name = config.get("current_profile")
                profile = config.get("profiles", {}).get(current_profile_name)
                # A platform_profile / throttle_thermal_policy change means
                # the EC just dropped the curve, so force a re-push even if
                # the curve data itself is unchanged. Sampled even after an
                # auto-switch, so that the mode change the switch just made
                # itself becomes the new baseline instead of looking like an
                # external one next cycle.
                thermal_changed = thermal_state_changed()
                if not switched:
                    apply_full_profile(config, profile,
                                       force_fan_reapply=thermal_changed,
                                       full=thermal_changed)

                # Keyboard brightness and charge limit are NOT re-applied
                # here. Both were measured to hold their values with the
                # enforcer stopped, so re-writing them every cycle only
                # produced noise -- and for the keyboard it actively fought
                # the user, reverting an Fn-key change within one cycle
                # whenever the app was not running to sync it back. They are
                # still applied on profile switch and at boot, which is when
                # they are meant to change.

                # Fallback for when the signal watcher misses something (the
                # busctl monitor restarting, a dropped signal, or the monitor
                # being unavailable entirely on this system). Same adoption
                # rule as the fast path, just up to INTERVAL_SECONDS later.
                expected_mode = PROFILE_TO_PPD_MODE.get(current_profile_name)
                if expected_mode and ppd_service_name:
                    actual_mode = get_ppd_active_profile(ppd_service_name)
                    if actual_mode and actual_mode != expected_mode:
                        adopt_external_ppd_mode(config, actual_mode,
                                                ppd_service_name)
        except Exception as e:
            # Never let one bad cycle kill the service, but do not hide it
            # either -- this was a silent 'pass' before.
            log(f"cycle failed: {e}", "ERROR", dedupe_key="cycle")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
