#!/usr/bin/env python3
"""
rogcontrol-enforcer.py
Continuously re-pushes the active profile (CPU limits, GPU power/clock
offsets) - to fight the BIOS/firmware periodically resetting things back to
its own defaults. Runs as a long-lived systemd --user service
(Restart=always).

It has not written the charge limit or the keyboard brightness for some
time -- see the note beside the upkeep pass -- and this line said it did,
which is exactly the wrong thing for the file to claim while someone is
hunting for what keeps changing the keyboard.

FAN CURVES ARE THE EXCEPTION: they are only re-pushed when the curve data
actually changes, or when an external power-mode change is detected. Each
fan channel needs a CHANNEL_GAP_S gap from the next (asus-wmi EC
limitation, see apply_full_profile), so re-pushing all 3 unconditionally
every cycle -- which is what this originally did -- could interrupt the
fans before they'd finished settling on the values just written.

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
and the app is closed most of the time. It then moved into this service's
60s cycle, which fixed that but made every switch land up to a minute after
the plug moved -- long enough to read as "auto-switching does not work".
It now has its own udev watcher for the plug moving, plus the 60s cycle as
the fallback, plus a remembered power source that survives a restart. See
the section further down.

CHARGER-CONNECT FLASH: opt-in, and hung off the same transition the
auto-switch reads, so the keys blink an acknowledgement about 0.8s after the
plug moves and then go back to exactly the lighting they were on. It refuses
to run at all for the modes whose previous state cannot be reconstructed
from the config -- see kbdcolor.FLASH_UNRESTORABLE_MODES.
"""

import glob
import json
import os
import shutil
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
from rogcontrol import fancurve  # noqa: E402
from rogcontrol import hardware  # noqa: E402
from rogcontrol import kbdcolor  # noqa: E402

# One copy of the curve maths, in the package. See rogcontrol-apply.py.
interpolate_curve = fancurve.interpolate_curve
pct_to_pwm255 = fancurve.pct_to_pwm255

CONFIG_PATH = os.path.expanduser("~/.config/rogcontrol.json")
# Upkeep cadence. Only the CPU limits and the fan-curve safety
# re-check run on this interval now, so it can be far slower than
# the old 15s without losing anything.
INTERVAL_SECONDS = 60

# See pages/fans.py: retested down to 0.5s with no failures, kept at 5s for
# margin over the retested floor.
CHANNEL_GAP_S = 5

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
# that the ~10s apply stays rare.
FAN_REVERIFY_SECONDS = 300
_last_fan_apply_time = 0.0

# (profile_name, fan-curve-json) of the last fan curve actually pushed to
# hardware. Each channel apply needs a CHANNEL_GAP_S gap from the others (see
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
# all three fan channels (~10s of writes) with a completely different curve.
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

# The keyboard colour this service last wrote, so a full apply that changes
# nothing about the profile does not cost a ~270 ms USB round trip through
# rogauracore. apply_full_profile(full=True) is reached on a profile switch
# (which is what this is for) but also at service start and on every
# platform_profile change, and repainting the keys the colour they are
# already showing is pure noise on the USB bus.
#
# Deliberately not persisted: after a restart the first full apply repaints
# once, which is exactly right -- the keys may well have been left on
# another profile's colour by whatever happened while this service was down.
_last_kbd_color_args = None

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


def notify(title, body):
    """A desktop notification, best effort.

    The AC/battery switch is the only thing this service does that the user
    did not ask for at that moment: the plug moves, and a minute later the
    fans and the power limits are somewhere else. The GTK3 version showed a
    notification for it and the rewrite lost it, which made an automatic
    switch indistinguishable from the machine changing its mind on its own.

    Every failure is swallowed. There may be no notification daemon, no
    session bus, or no notify-send at all, and none of those are a reason to
    take a service down -- or even to fill the log, since it would repeat on
    every switch forever."""
    try:
        subprocess.run(["notify-send", "-a", "ROG Control", title, body],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def run_helper(*args):
    """Run a privileged action and REPORT failure.

    This used to discard the exit code and both output streams, so the
    enforcer could not tell anyone that anything had gone wrong. A broken
    sudoers rule, a missing helper or a failing ryzenadj all looked exactly
    like normal operation while nothing was actually being applied.

    A non-zero exit code is the only thing counted as failure. ``cpu`` writes
    to stderr on every run on this hardware, successful runs included, so
    anything that treated output as failure would log nine successes an hour
    as errors."""
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
        msg = hardware.helper_error_message(result)
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
    global _last_applied_fans, _last_fan_apply_time, _last_kbd_color_args

    # The keyboard's colour follows the active profile, when the user has
    # asked for that. This is the enforcer's share of a repaint that is made
    # from four places -- the window, the tray's apply, the hotkey cycler and
    # here -- because a profile switch is not one event in one process. This
    # is the copy that covers the ones nobody is watching: the OS power menu
    # being used while the app is closed (adopt_external_ppd_mode), and the
    # charger coming out (check_ac_auto_switch). Both funnel through here
    # with full=True, which is why the hook is on this function rather than
    # on each of them.
    #
    # full=False is the 60s upkeep pass and is deliberately excluded, on the
    # same grounds as the keyboard brightness below it: this service does not
    # re-assert keyboard state on a timer, because doing so fights the user.
    # Only an actual switch repaints.
    if full:
        args = kbdcolor.profile_color_args(config)
        if args is not None and args != _last_kbd_color_args:
            _last_kbd_color_args = args
            run_helper(*args)

    if profile:
        # CPU limits are kept in the periodic pass: AMD firmware is known to
        # walk STAPM/PPT back on its own, so this one genuinely needs
        # re-asserting. It is cheap -- a single ryzenadj call.
        # Built by hardware.cpu_apply_plan, not by a chain of ifs here.
        # This code used to be a second, hand-maintained copy of the CPU
        # apply, and it drifted every time a setting was added: the clock
        # floor was added to the page and to the plan, and this pass -- which
        # writes cpuboost every cycle, and a boost write refreshes every
        # cpufreq policy and resets both the ceiling and the floor -- went on
        # re-asserting the ceiling and silently dropping the floor. The user
        # set a floor, the page saved it, and sixty seconds later it was gone
        # again with nothing logged.
        #
        # The plan also fixes the ordering for free: boost first, then the
        # ceiling, then the floor, which is the order the boost reset makes
        # mandatory.
        #
        # CPU limits stay in the periodic pass rather than behind `full`: AMD
        # firmware walks STAPM/PPT back on its own, and `full` is only true
        # when the thermal state moved, so a service restart or a fresh boot
        # would otherwise never assert any of this.
        cpu = profile.get("cpu")
        if cpu:
            caps = {
                "ryzenadj": True,
                "cpu_boost": boost_control_available(),
                "cpu_epp": epp_control_available(),
                "cpu_clock": clock_limit_available(),
            }
            for _step, args in hardware.cpu_apply_plan(cpu, caps):
                run_helper(*args)

        if full:
            gpu = profile.get("gpu")
            if gpu and hardware.dgpu_available():
                run_helper("gpu", gpu["watts"])
                apply_gpu_clock_offsets(gpu)

        fans = profile.get("fans", {})
        fans_signature = (config.get("current_profile"), json.dumps(fans, sort_keys=True))
        stale = (time.monotonic() - _last_fan_apply_time) >= FAN_REVERIFY_SECONDS
        if force_fan_reapply or stale or fans_signature != _last_applied_fans:
            for i, (channel, points) in enumerate(fans.items()):
                if i > 0:
                    # The asus-wmi embedded controller can silently drop
                    # fan-curve writes fired too close together for
                    # different channels. First measured directly on this
                    # hardware: applying one channel in isolation reliably
                    # took effect, but a 0.5s gap between channels left 2 of
                    # 3 stuck on their old value. A later, more careful
                    # retest -- several rounds at each gap from 0.5s to 8s,
                    # reading the curve back from the driver afterward --
                    # found 0.5s through 8s all held; the original 0.5s
                    # failure was not reproduced. CHANNEL_GAP_S is kept above
                    # the retested floor rather than dropped to it. This --
                    # combined with the unconditional re-push every
                    # INTERVAL_SECONDS this function used to do regardless of
                    # whether anything had changed, which could interrupt a
                    # channel before it finished settling -- is why the curve
                    # looked like it was being "ignored" even though this
                    # enforcer was correctly re-pushing it the whole time.
                    time.sleep(CHANNEL_GAP_S)
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
# The power source used to be sampled once per cycle and nowhere else, so a
# switch landed up to INTERVAL_SECONDS (60s) after the plug moved. That was
# argued for on the grounds that the apply takes ~10 seconds anyway so a
# minute of granularity costs nothing -- which is wrong about the part that
# matters. Sixteen seconds of fans ramping is feedback that something
# happened; up to sixty seconds of *nothing* happening is indistinguishable
# from a broken feature, and that is exactly how it was reported. So the plug
# moving is now watched for directly (power_supply_watcher_thread below) and
# the cycle is kept only as the fallback.
#
# Both paths go through check_ac_auto_switch, and _ac_lock serialises them:
# a switch holds the lock for the whole ~10 second apply, so the watcher and
# the cycle can never be halfway through two different profiles at once.
_ac_lock = threading.Lock()

# Whether the machine was on mains last time we looked, and where that is
# remembered across restarts.
#
# WHY PERSIST IT: this was a plain in-memory global starting at None, and the
# first sample after any start was only recorded, never acted on. The service
# is Restart=always and every install restarts it, so "start while on battery
# with the AC profile active" left a real mismatch uncorrected until the plug
# next moved. Acting on the first sample instead would be worse -- a restart
# would then override a profile the user had deliberately picked by hand.
#
# Remembering the power source on disk dissolves the tension rather than
# picking a side: a restart is no longer confused with a transition, because
# there is something to compare against. Plain restart, power source
# unchanged -> previous == current -> nothing happens, the user's manual
# choice stands. Service was down (or the machine was off) while the plug
# actually moved -> previous != current -> the change is acted on, which is
# the correct answer for a change that really did happen. Genuinely first
# ever run, no file yet -> previous is None -> record only, act on the next
# one, which stays the conservative default for the one case where nothing
# can be inferred.
#
# It is written only when the value changes, not once a cycle: this is a
# 1440-writes-a-day loop otherwise, for a fact that changes twice a day.
_last_ac_state = None
AC_STATE_PATH = os.path.expanduser(
    "~/.local/share/rogcontrol/last-power-source")

# CHARGER-KIND NOTIFICATION: separate from the AC/battery auto-switch above
# on purpose. The switch only fires when ac_profile/battery_profile is
# configured and names a real change of profile -- someone with neither set
# would never hear anything when the plug moves. This tells you which
# charger you just connected (or disconnected) regardless of that config,
# same persist-across-restart shape as _last_ac_state so a service restart
# is never read as a charger swap.
_last_charger_kind = None
CHARGER_KIND_PATH = os.path.expanduser(
    "~/.local/share/rogcontrol/last-charger-kind")

CHARGER_KIND_LABELS = {"mains": "AC charger", "usb": "USB-C charger"}


def load_last_charger_kind():
    """The charger kind this service saw last time it ran, or None (first
    run, unusable file, or it was on battery)."""
    try:
        with open(CHARGER_KIND_PATH) as f:
            value = f.read().strip()
    except OSError:
        return None
    return value if value in CHARGER_KIND_LABELS else None


def store_last_charger_kind(kind):
    """Remember the charger kind for the next start. Best effort, temp file
    plus rename for the same reason store_last_ac_state uses it -- a start
    racing a write must read the old value or the new one, never a
    truncated one."""
    try:
        os.makedirs(os.path.dirname(CHARGER_KIND_PATH), exist_ok=True)
        tmp = CHARGER_KIND_PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write(kind or "")
        os.replace(tmp, CHARGER_KIND_PATH)
    except OSError as e:
        log(f"could not remember charger kind: {e}", "WARN",
            dedupe_key="chargerkind")


def charger_kind_notify_message(previous_kind, current_kind):
    """The (title, body) to announce for a charger-kind change, or None for
    "nothing worth saying".

    Pure, like ac_switch_target -- same reasons: testable without I/O, and
    the rules here are all about when NOT to speak.

    * Unchanged kind is the common case every cycle and every udev event --
      say nothing.
    * previous_kind None with current_kind None is the first-ever sample on
      battery, or a restart on battery: nothing changed, nothing to say.
    * previous_kind None with current_kind set IS spoken, unlike
      ac_switch_target's first-sample rule -- there is no risk of
      re-applying a stale profile here, only of staying silent about a
      charger that is plugged in right now.
    * current_kind set and different from previous: a connect, worded for
      the kind that just showed up.
    * current_kind None and previous_kind set: a disconnect, worded for the
      kind that just left -- current_kind carries nothing to name."""
    if current_kind == previous_kind:
        return None
    if current_kind is not None:
        return ("ROG Control", f"{CHARGER_KIND_LABELS[current_kind]} connected")
    if previous_kind is not None:
        return ("ROG Control",
                f"{CHARGER_KIND_LABELS[previous_kind]} disconnected — "
                "on battery power")
    return None


def load_last_ac_state():
    """The power source this service saw last time it ran, or None if there
    is no usable record (first run, wiped state dir, unreadable file).

    None is the safe answer for every failure: it means "record the next
    sample, act on the one after", which is what the code did unconditionally
    before this file existed."""
    try:
        with open(AC_STATE_PATH) as f:
            value = f.read().strip()
    except OSError:
        return None
    if value == "AC":
        return True
    if value == "battery":
        return False
    return None


def store_last_ac_state(on_ac):
    """Remember the power source for the next start. Best effort.

    Written via a temp file and rename so a start that races a write reads
    either the old value or the new one, never a half-written one -- a
    truncated file would parse as None and silently cost one transition.
    Words rather than 0/1 so the file is readable when someone is trying to
    work out why a switch did or did not happen."""
    try:
        os.makedirs(os.path.dirname(AC_STATE_PATH), exist_ok=True)
        tmp = AC_STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write("AC" if on_ac else "battery")
        os.replace(tmp, AC_STATE_PATH)
    except OSError as e:
        log(f"could not remember power source: {e}", "WARN", dedupe_key="acstate")


def ac_switch_target(previous_ac, current_ac, config):
    """The profile the power source change calls for, or None for "do
    nothing".

    Pure: state in, a name out, no I/O -- which is what makes the rules
    below testable, and they are all rules about when NOT to act:

    * ``current_ac`` None means there is no Mains supply to read (a desktop,
      or a kernel that does not expose one). Nothing can be inferred.
    * ``previous_ac`` None means nothing is known about the power source
      yet -- the genuinely-first run, before anything was ever recorded.
      Remember it, act on the next one; see _last_ac_state. Note this is now
      rare rather than once-per-restart, because the previous state is loaded
      back from disk at startup.
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


# -- the charger-connect flash ------------------------------------------------
#
# WHY IT LIVES HERE. The acknowledgement has to arrive whether or not the
# window is open, and the plug mostly moves with it closed -- which is the
# same argument that moved the AC/battery auto-switch into this service in
# the first place. This is also the only process that already knows a
# transition happened: _check_ac_auto_switch has both the previous and the
# current power source in hand, from the udev watcher within about half a
# second of the plug moving and from the 60s cycle as the fallback. Hanging
# the flash off that costs no new watcher, no new state file and no new
# process, and it inherits _ac_lock, so a flash can never interleave with a
# profile apply's own keyboard write.
#
# LATENCY the user actually sees, on the udev path: the uevent arrives
# essentially with the driver's own update to `online`, then
# POWER_SUPPLY_SETTLE_SECONDS (0.5s) to let the burst settle and avoid
# reading mid-update, then one ~270 ms helper round trip -- so the keys are
# lit roughly 0.8s after the plug moves. That reads as an acknowledgement.
# On a machine where the udev watcher could not start it degrades to the
# 60s poll, at which point the flash is meaningless as feedback; it is left
# on rather than disabled there because a late blink is not harmful and the
# alternative is a feature that silently does nothing on those machines.
#
# The flash goes BEFORE the auto-switch decision below, not after: the
# switch's own apply takes ~10 seconds, and an acknowledgement that lands
# after the fans have already changed pitch is answering a question the user
# has finished asking. It also fires regardless of whether ac_profile /
# battery_profile are configured, for the same reason the charger-kind
# notification does -- somebody with neither set would otherwise never see
# it.

# When the last flash STARTED, on the monotonic clock, or None for "not this
# run". Deliberately not persisted, unlike _last_ac_state: this only exists
# to collapse a burst of contact bounce that is seconds wide, and a service
# restart is already far longer than the debounce window.
_last_charger_flash_at = None


def charger_flash_event(previous_ac, current_ac):
    """"connected", "disconnected", or None for "nothing happened".

    Pure, and the same transition rule as ac_switch_target for the same
    reasons -- but returning the direction rather than a profile, because a
    flash fires on both edges while a switch only fires when a profile is
    configured for the side being moved to.

    Both edges are answered on purpose. Unplugging is the half people
    actually get wrong: a cable knocked out of a barrel jack, or a dock that
    quietly stopped delivering, is invisible until the battery is low, and
    the blink is the cheapest possible way to notice. Flashing only on
    connect would answer the question nobody was asking.

    * ``current_ac`` None means no power supply could be read at all -- a
      desktop, or a kernel that exposes none. Nothing can be inferred.
    * ``previous_ac`` None is the genuinely-first-ever run, before anything
      was recorded. Nothing to compare against, so nothing to acknowledge.
      Note this is rare rather than once-per-restart, because the previous
      state is loaded back from disk in main().
    * Unchanged is the overwhelmingly common case: this runs on every cycle
      and on every uevent, and the plug moves maybe twice a day.

    A restart therefore does not flash (same source before and after), while
    a plug that genuinely moved while this service was down does -- which is
    the right answer to "is the power source different from the last one you
    were told about?"
    """
    if current_ac is None or previous_ac is None:
        return None
    if current_ac == previous_ac:
        return None
    return "connected" if current_ac else "disconnected"


def charger_flash_due(last_flash_at, now, gap=None):
    """True when enough time has passed since the last flash to allow another.

    Pure, so the debounce can be pinned without a test that waits five real
    seconds. Measured from the START of the previous flash rather than its
    end, so the ~720 ms the flash itself occupies is inside the window rather
    than added to it.

    ``last_flash_at`` None means nothing has flashed yet this run, which is
    always allowed. See kbdcolor.FLASH_DEBOUNCE_SECONDS for why a refused
    flash is dropped rather than queued."""
    if gap is None:
        gap = kbdcolor.FLASH_DEBOUNCE_SECONDS
    if last_flash_at is None:
        return True
    return (now - last_flash_at) >= gap


def charger_flash(config, event):
    """Blink the keys twice and put them back. Returns True if it flashed.

    The I/O half, and the only part of this feature that sleeps or writes:
    every rule about *whether* to flash is in charger_flash_event,
    charger_flash_due and kbdcolor.charger_flash_plan, all of which are pure.

    The restore call is worked out BEFORE the flash colour is written, and
    the whole thing is abandoned if it cannot be. There is deliberately no
    path that puts the flash colour on the keys while still hoping to find
    out what to put back -- that path is how a "brief" flash becomes
    permanent.

    A failed FINAL restore is the one outcome that is worse than not
    flashing at all, so it is retried once and logged as an error either
    way. One retry rather than a loop: a second failure means the helper or
    the controller is gone, and the user has a bigger problem than the
    colour of their keyboard. The backlight brightness -- woken for the
    blink on every flash, see kbdcolor.brightness_wake_args -- gets the same
    treatment for the same reason: a keyboard stuck lit because the charger
    moved is exactly the kind of surprise this feature exists not to cause.
    """
    global _last_charger_flash_at, _last_kbd_color_args
    # Asked first, and again inside charger_flash_plan. Opted out is the
    # default and so the overwhelmingly common case, and there is no reason
    # to take any reading below for a feature nobody switched on.
    if not kbdcolor.charger_flash_enabled(config):
        return False
    # The one sensor read charger_flash_plan cannot take itself, because it
    # is pure. Looking at the saved mode costs nothing (a dict lookup, no
    # I/O) and picks at most one of these -- a GPU query in particular is a
    # subprocess call, not worth making for a Battery Level user.
    saved_mode = (config.get("kbd_rgb") or {}).get("mode")
    if saved_mode == "Battery Level":
        live_reading = hardware.read_battery()
    elif saved_mode == "CPU Temp Color":
        live_reading = hardware.read_cpu_temp()
    elif saved_mode == "GPU Temp Color":
        live_reading = hardware.read_nvidia_stats()[0]
    else:
        live_reading = None
    plan = kbdcolor.charger_flash_plan(
        config, brightness=hardware.read_kbd_brightness(),
        live_reading=live_reading)
    if plan is None:
        return False
    if not charger_flash_due(_last_charger_flash_at, time.monotonic()):
        log(f"charger {event} -- flash skipped, one was shown less than "
            f"{kbdcolor.FLASH_DEBOUNCE_SECONDS:g}s ago", "INFO",
            dedupe_key="flashdebounce", dedupe_seconds=60)
        return False
    flash_args, restore_args, brightness_wake, brightness_restore = plan
    _last_charger_flash_at = time.monotonic()
    # The wake pulse: the keyboard's EC cuts backlight power on its own idle
    # timer, independent of the LED class value, and a colour write alone
    # cannot revive it once that has happened -- confirmed on real hardware.
    # One or two writes, always run, always before the colour: there is no
    # way to read back whether the EC has already cut power, so this cannot
    # be made conditional on the current brightness reading. Best-effort --
    # a failed wake write does not abort the flash, since the colour writes
    # below are harmless (if pointless) on a backlight that stayed dark.
    for args in brightness_wake:
        if not run_helper(*args):
            log("charger flash: a brightness wake write failed, continuing",
                "ERROR", dedupe_key="flashwake")
    if not run_helper(*flash_args):
        # The colour write failed, but the wake pulse above may not have --
        # put the brightness back before giving up, or the one write that
        # DID land is exactly the stuck-lit keyboard this all exists to
        # prevent.
        run_helper(*brightness_restore)
        return False
    time.sleep(kbdcolor.FLASH_HOLD_SECONDS)
    # The middle blinks are best-effort: their own restore write is what
    # makes them read as a blink rather than one long flash, so a failure
    # here is logged and the sequence carries on into the next flash rather
    # than aborting -- the keys are not left on the flash colour either way,
    # since the very next write is another flash. Only the FINAL restore,
    # below, gets the retry-and-give-up treatment, because that is the one
    # failure that can strand the keyboard.
    for _ in range(kbdcolor.FLASH_BLINK_COUNT - 1):
        if not run_helper(*restore_args):
            log("charger flash: a mid-sequence restore failed, continuing",
                "ERROR", dedupe_key="flashrestore")
        if not run_helper(*flash_args):
            if not run_helper(*brightness_restore):
                log("charger flash could not lower the backlight back off",
                    "ERROR", dedupe_key="flashbrightness")
            return True
        time.sleep(kbdcolor.FLASH_HOLD_SECONDS)
    if not run_helper(*restore_args):
        log("charger flash could not restore the lighting -- retrying once",
            "ERROR", dedupe_key="flashrestore")
        if not run_helper(*restore_args):
            log("charger flash left the keyboard on the flash colour: the "
                f"restore call {' '.join(str(a) for a in restore_args)} "
                "failed twice", "ERROR", dedupe_key="flashrestore2")
            # The keys are now showing something this service did not intend,
            # so the "already painted, skip the write" cache is a lie. Cleared
            # so the next full apply repaints instead of skipping -- which is
            # the only chance Profile Color has of recovering without the
            # user opening the window.
            _last_kbd_color_args = None
    if not run_helper(*brightness_restore):
        log("charger flash could not lower the backlight back off -- "
            "retrying once", "ERROR", dedupe_key="flashbrightness")
        if not run_helper(*brightness_restore):
            log("charger flash left the backlight at the wrong level: the "
                f"restore call {' '.join(str(a) for a in brightness_restore)} "
                "failed twice", "ERROR", dedupe_key="flashbrightness2")
    return True


def check_ac_auto_switch(config, service_name, trigger="poll"):
    """Sample the power source and switch profile if it has just changed.

    Called from two places -- the udev watcher the moment the plug moves, and
    the periodic cycle as the fallback -- so it takes _ac_lock for its whole
    body, apply included. Without that, a udev event arriving while the cycle
    was mid-apply would start a second ~10 second apply of a different profile
    over the top of the first, and the fan channels would be interleaved.
    Blocking the cycle behind the watcher is the right way round: the watcher
    is reacting to something that actually happened.

    ``trigger`` is only for the log, and only so that "the switch was late"
    and "the switch never fired at all" stop looking the same in there.

    Returns True if a profile was switched and applied, so the caller knows
    the hardware has already been dealt with this cycle."""
    with _ac_lock:
        return _check_ac_auto_switch(config, service_name, trigger)


def _check_ac_auto_switch(config, service_name, trigger):
    global _last_ac_state, _last_charger_kind
    current_ac, current_kind = hardware.read_power_source()

    previous_kind = _last_charger_kind
    if current_kind != previous_kind:
        message = charger_kind_notify_message(previous_kind, current_kind)
        _last_charger_kind = current_kind
        store_last_charger_kind(current_kind)
        if message is not None:
            notify(*message)

    previous_ac = _last_ac_state
    if current_ac is not None and current_ac != _last_ac_state:
        _last_ac_state = current_ac
        # Only on a change: this runs every cycle and on every udev event,
        # and the answer is the same almost every time.
        store_last_ac_state(current_ac)

    # Before the switch decision, so the acknowledgement is not stuck behind
    # the ~10 second apply a switch triggers. It is also independent of it:
    # the flash answers "the power source changed", which is true whether or
    # not a profile was configured to change with it.
    event = charger_flash_event(previous_ac, current_ac)
    if event is not None:
        try:
            charger_flash(config, event)
        except Exception as e:
            # A flash is cosmetic. It must never be the reason an auto-switch
            # did not happen.
            log(f"charger flash failed: {e}", "WARN", dedupe_key="flash")

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
        log(f"power source changed to {source} (via {trigger}) -- "
            f"already on profile '{target}', nothing to do", "INFO")
        return False

    # INFO, and naming both the source and the profile, because this is the
    # one line that answers "did the auto-switch fire?". The last time this
    # was reported broken the log had nothing in it at all for the feature,
    # so there was no way to tell a wrong decision from a decision never
    # reached.
    log(f"power source changed to {source} (via {trigger}) -- "
        f"switching profile to '{target}'", "INFO")
    config["current_profile"] = target
    save_config(config, "auto-switched profile")

    # Before the apply, not after: the apply takes ~10 seconds (the fan
    # channels need 8 seconds between them), and a notification that arrives
    # a quarter of a minute after the fans have already changed pitch is
    # explaining something the user has finished wondering about.
    notify("ROG Control",
           f"On {source} power — switched to “{target}”")

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


# -- reacting to the plug the moment it moves ---------------------------------
#
# WHY UDEV, and not the two obvious alternatives:
#
# * UPower's PropertiesChanged on OnBattery would work and is the tidier API,
#   but it adds a dependency on a daemon that need not be installed. This
#   service already talks to power-profiles-daemon, and it is tempting to
#   assume that implies UPower -- it does not; PPD does not require it. A
#   watcher that silently never fires because a package is absent is the
#   failure mode being fixed here, not one to reintroduce.
#
# * Watching /sys/.../online with inotify cannot work at all. sysfs does not
#   raise inotify events when an attribute's value changes, and the
#   power_supply core does not sysfs_notify() that file either, so a poll() or
#   an inotify watch on it would sit there forever looking healthy and never
#   fire once. That is the worst of the three failure modes: undetectable.
#
# udev is what is left, and it is also the most direct: the kernel's
# power_supply driver emits a change uevent for exactly this event, and the
# `online` file this service already reads is written by the same driver in
# the same operation. Same source of truth, no new daemon, and udev is present
# on any system that can run this service at all.
#
# Unprivileged is the deciding practical detail. The PPD watcher below wanted
# `busctl --system monitor`, which needs root and quietly does nothing without
# it; `udevadm monitor --udev` reads udev's multicast group, which is readable
# by ordinary users -- verified on this machine as the user this service runs
# as.

# The uevent is emitted around the same moment the driver updates `online`,
# and a plug also produces a small burst of events (the mains supply and the
# battery both change). A short settle both avoids reading mid-update and
# collapses the burst into effectively one check -- the rest find nothing
# changed and cost a sysfs read each.
POWER_SUPPLY_SETTLE_SECONDS = 0.5

# Restart policy for the monitor, and the whole of the lesson from the PPD
# watcher below: an earlier version of that thread respawned a monitor that
# exited instantly, with no delay, and burned ~8.5% of a core doing it. So:
# never respawn without backing off, and give up entirely on a monitor that
# will not stay up, because a watcher that cannot run is not an emergency --
# INTERVAL_SECONDS polling still catches every transition, just later.
WATCH_BACKOFF_SECONDS = 5
WATCH_MIN_HEALTHY_SECONDS = 30
WATCH_MAX_FAILED_STARTS = 5


def spawn_power_supply_monitor():
    """A running `udevadm monitor` on the power_supply subsystem, or None if
    one cannot be started.

    None is a supported answer, not an error: every caller falls back to the
    periodic poll. Split out from the thread below mostly so the thread can be
    tested against a monitor whose events are under the test's control --
    the real one only speaks when the plug moves.

    stdbuf, when present, guarantees line buffering. udevadm was measured to
    flush its own output promptly through a pipe here, so this is belt and
    braces against a build that does not -- full 4KB block buffering would
    hold an event line back for hours, which would look exactly like the lag
    this whole watcher exists to remove."""
    if shutil.which("udevadm") is None:
        return None
    cmd = ["udevadm", "monitor", "--udev", "--subsystem-match=power_supply"]
    if shutil.which("stdbuf") is not None:
        cmd = ["stdbuf", "-oL"] + cmd
    try:
        return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return None


def handle_power_supply_event(service_name):
    """One power_supply uevent: re-read the config and re-check the plug.

    The config is read here rather than passed in because this thread has no
    cycle to inherit one from, and the file is the only thing that knows which
    profile is current."""
    if not os.path.exists(CONFIG_PATH):
        return False
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(config, dict):
        return False
    return check_ac_auto_switch(config, service_name, trigger="udev")


def power_supply_watcher_thread(service_name):
    """Runs in the background for the life of the service. Turns "the plug
    moved" into a profile switch straight away instead of up to
    INTERVAL_SECONDS later.

    Everything here degrades to the poll rather than failing closed: no
    udevadm, a monitor that will not start, a monitor that keeps dying -- all
    end this thread quietly and leave the cycle in main() doing the same job
    a minute at a time."""
    failed_starts = 0
    while True:
        started = time.monotonic()
        proc = spawn_power_supply_monitor()
        if proc is None:
            log("no usable udevadm -- AC/battery switching falls back to the "
                f"{INTERVAL_SECONDS}s poll", "WARN", dedupe_key="acwatch")
            return
        try:
            for line in proc.stdout:
                # udevadm prints a two-line banner first; only real events
                # name the subsystem.
                if "power_supply" not in line:
                    continue
                if POWER_SUPPLY_SETTLE_SECONDS:
                    time.sleep(POWER_SUPPLY_SETTLE_SECONDS)
                try:
                    handle_power_supply_event(service_name)
                except Exception as e:
                    # One bad event must not take the watcher down -- that
                    # would silently drop the machine back to 60s lag.
                    log(f"power-supply watcher event failed: {e}", "WARN",
                        dedupe_key="acwatchev")
            proc.wait()
        except Exception as e:
            log(f"power-supply watcher error: {e}", "WARN",
                dedupe_key="acwatch")
            try:
                proc.kill()
            except Exception:
                pass

        # Only reached when the monitor ended. A monitor that ran for a decent
        # while and then stopped (udevd restarted, say) is worth reconnecting
        # to; one that dies immediately, every time, is not going to start
        # working, and retrying it forever is the tight loop this file has
        # already paid for once.
        if time.monotonic() - started < WATCH_MIN_HEALTHY_SECONDS:
            failed_starts += 1
        else:
            failed_starts = 0
        if failed_starts >= WATCH_MAX_FAILED_STARTS:
            log(f"udev power-supply monitor would not stay up "
                f"({failed_starts} short-lived starts) -- giving up, "
                f"AC/battery switching falls back to the {INTERVAL_SECONDS}s "
                "poll", "WARN", dedupe_key="acwatch")
            return
        time.sleep(WATCH_BACKOFF_SECONDS)


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


def main():
    # Background thread reacts to power-mode changes immediately; the
    # main loop below is the periodic fallback in case a signal is missed.
    watcher = threading.Thread(target=ppd_watcher_thread, daemon=True)
    watcher.start()

    ppd_service_name = get_ppd_service_name()

    # Pick the power source back up from where the last run left it, BEFORE
    # the first cycle samples it. This is what makes the first cycle able to
    # tell "the service restarted" (same source: do nothing, leave the user's
    # profile alone) from "the plug moved while the service was down or the
    # machine was off" (different source: switch, it really did change).
    global _last_ac_state, _last_charger_kind
    _last_ac_state = load_last_ac_state()
    _last_charger_kind = load_last_charger_kind()

    # And react to the plug the moment it moves, rather than up to
    # INTERVAL_SECONDS later. Falls back to the cycle below if it cannot run.
    ac_watcher = threading.Thread(target=power_supply_watcher_thread,
                                  args=(ppd_service_name,), daemon=True)
    ac_watcher.start()

    while True:
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH) as f:
                    config = json.load(f)

                # First, because it can change which profile the rest of this
                # cycle is about. It applies the new profile itself when it
                # switches.
                #
                # This is the fallback path now -- the udev watcher normally
                # gets there within a second of the plug moving -- but it is
                # still what catches a transition on a system where the
                # watcher could not start, and it is what acts on a plug that
                # moved while this service was not running.
                switched = check_ac_auto_switch(config, ppd_service_name,
                                                trigger="poll")

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
                # whenever the app was not running to sync it back.
                #
                # Nor does this enforcer's own AC/battery auto-switch touch
                # them: it goes through apply_full_profile, which writes the
                # profile and only the profile. The charge limit is applied
                # at boot and on a profile switch; the keyboard is applied at
                # boot and when the user changes it, and nowhere else -- a
                # profile switch that reset the backlight to the config's
                # last value was the same fight in a different place.

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
