"""Reading the machine, and asking the privileged helper to change it.

Everything here is standard library only -- no GTK -- so the app, the helper
scripts, the enforcer service and the tests can share one copy. It is the
sysfs/subprocess half of the old rogcontrol.py, lifted out unchanged in
behaviour: same paths, same fallbacks, same "return None rather than raise"
contract, because callers upstack are drawing labels and a missing sensor
must show as "--" rather than take the window down.

Every reader takes an optional ``root``. Passing one re-bases the absolute
sysfs paths under a directory, which is the only way to test any of this
without the hardware attached -- the alternative, patching ``open``, tests
the mock rather than the path.
"""

import glob
import os
import subprocess
import sys

HELPER = "/usr/local/bin/rogcontrol-helper"
ASUS_WMI_DIR = "/sys/devices/platform/asus-nb-wmi"
HWMON_DIR = "/sys/class/hwmon"
POWER_SUPPLY_DIR = "/sys/class/power_supply"
CPUFREQ_GLOB = "/sys/devices/system/cpu/cpufreq/policy*"

# Curve Optimizer range the helper will accept. Mirrored here so the UI can
# clamp a spin button to the same window rather than offering values that are
# only rejected once they reach the helper.
COALL_MIN, COALL_MAX = -30, 0


def _under(root, path):
    """Re-base an absolute path under ``root``.

    ``root`` of None (or "/") is the real machine, which is the only thing
    the app ever passes; the tests pass a temporary directory."""
    if root in (None, "", "/"):
        return path
    return os.path.join(root, path.lstrip("/"))


def read_file(path):
    """Contents of a sysfs file, stripped, or None if it cannot be read.

    Returning None rather than raising is deliberate: a sensor that is not
    present on this model is the normal case, not an error."""
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def read_int(path):
    """read_file, parsed as an int, or None. Saves every caller the same
    two-branch dance around a missing file *and* an unparseable one."""
    val = read_file(path)
    if val is None:
        return None
    try:
        return int(val.strip())
    except ValueError:
        return None


def find_hwmon_by_name(name, root=None):
    """Path of the hwmon directory whose ``name`` file holds ``name``.

    hwmonN numbering is assigned in probe order and moves between boots, so
    the number can never be hardcoded -- this lookup is the only safe way to
    reach a specific chip."""
    base = _under(root, HWMON_DIR)
    try:
        for entry in sorted(os.listdir(base)):
            path = os.path.join(base, entry)
            if read_file(os.path.join(path, "name")) == name:
                return path
    except OSError:
        pass
    return None


def run_helper(*args, timeout=10):
    """Run one privileged action, returning ``(ok, message)``.

    ``sudo -n`` never prompts: if the sudoers rule for the helper is missing
    this fails immediately with a message rather than hanging a worker thread
    on a password prompt nobody can see. The helper validates every argument
    itself -- these calls drive real hardware, so the range checks live on the
    privileged side where they cannot be bypassed by a caller."""
    cmd = " ".join(str(a) for a in args)
    try:
        result = subprocess.run(
            ["sudo", "-n", HELPER, *[str(a) for a in args]],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as e:
        print(f"rogcontrol: helper could not run: {cmd} -> {e}", file=sys.stderr)
        return False, str(e)
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "unknown error").strip()
        print(f"rogcontrol: helper failed: {cmd} -> {msg}", file=sys.stderr)
        return False, msg
    return True, result.stdout.strip()


ENFORCER_SERVICE = "rogcontrol-enforcer.service"


def set_enforcer_running(running, timeout=10):
    """Start or stop the background enforcer, returning True if it worked.

    It is a --user unit, so no sudo and no password. The one caller that
    needs this is fan calibration: the enforcer re-asserts the profile's
    curve on its own schedule, and a curve pushed back mid-measurement makes
    the fan settle at a speed nobody asked for and the fit meaningless.

    False rather than an exception on failure, because "the enforcer is not
    installed" is a perfectly ordinary state -- and the caller only needs to
    know whether it has something to restart afterwards."""
    action = "start" if running else "stop"
    try:
        result = subprocess.run(
            ["systemctl", "--user", action, ENFORCER_SERVICE],
            capture_output=True, text=True, timeout=timeout)
    except Exception:
        return False
    return result.returncode == 0


# -- CPU ---------------------------------------------------------------------

def read_cpu_temp(root=None):
    """Package temperature in C, or None.

    k10temp's temp1_input is Tctl, which is what the embedded controller
    drives the fans from -- so it is the number to show next to a fan curve,
    even though it reads a few degrees above the physical die sensor."""
    hw = find_hwmon_by_name("k10temp", root=root)
    if not hw:
        # Not an AMD machine (or the module is not loaded); fall back to the
        # generic ACPI thermal zone so the readout is not simply blank. The
        # node is named "acpitz" on some kernels and "acpitz_0" on others.
        for name in ("coretemp", "acpitz_0", "acpitz"):
            hw = find_hwmon_by_name(name, root=root)
            if hw:
                break
    if not hw:
        return None
    milli = read_int(os.path.join(hw, "temp1_input"))
    return None if milli is None else milli / 1000.0


def read_peak_core_clock_mhz(root=None):
    """Highest current core clock in MHz across all cpufreq policies, or None.

    Reads ``cpuinfo_avg_freq`` -- amd-pstate's average of what the core
    actually ran at since the last read, derived from the hardware's APERF/
    MPERF counters. ``scaling_cur_freq`` is the governor's *request* and
    /proc/cpuinfo is a snapshot of one instant, so both routinely disagree
    with what the silicon did. Policies without the file are skipped, which
    is what makes this fall back cleanly on non-amd-pstate machines.

    The peak rather than the mean: with 32 threads, the interesting question
    is how high anything is boosting, and a mean over mostly-idle cores
    answers a different one."""
    best = None
    for base in sorted(glob.glob(_under(root, CPUFREQ_GLOB))):
        khz = read_int(os.path.join(base, "cpuinfo_avg_freq"))
        if khz is None:
            khz = read_int(os.path.join(base, "scaling_cur_freq"))
        if khz is None:
            continue
        if best is None or khz > best:
            best = khz
    return None if best is None else round(best / 1000)


def read_package_power_w(root=None):
    """Whole-package power draw in watts, or None.

    This comes off the *amdgpu* hwmon rather than a CPU one, which looks
    wrong and is not: on this APU-plus-dGPU design the amdgpu node exposes
    ``power1_input`` for the SoC package, and it is the PPT figure the power
    limits below actually cap. There is no equivalent under k10temp."""
    hw = find_hwmon_by_name("amdgpu", root=root)
    if not hw:
        return None
    micro = read_int(os.path.join(hw, "power1_input"))
    return None if micro is None else micro / 1e6


def read_cpu_clock_range(root=None):
    """(min_khz, max_khz) the cores can be capped between, or None.

    Both come from cpuinfo_*, i.e. what the hardware can do, not the current
    scaling_* window -- otherwise a cap already in effect would shrink the
    control's range and there would be no way back up."""
    for path in sorted(glob.glob(
            _under(root, CPUFREQ_GLOB) + "/cpuinfo_max_freq")):
        base = os.path.dirname(path)
        hi = read_int(path)
        lo = read_int(os.path.join(base, "cpuinfo_min_freq"))
        if hi is not None and lo is not None and hi > lo:
            return lo, hi
    return None


def read_current_cpu_clock_cap(root=None):
    """Ceiling currently in force, in kHz, or None."""
    for path in sorted(glob.glob(
            _under(root, CPUFREQ_GLOB) + "/scaling_max_freq")):
        val = read_int(path)
        if val is not None:
            return val
    return None


def read_cpu_boost_enabled(root=None):
    """True/False if a cpufreq boost switch exists, else None.

    amd-pstate publishes one global switch; the other drivers put one under
    each policy, so both locations have to be checked."""
    val = read_int(_under(root, "/sys/devices/system/cpu/cpufreq/boost"))
    if val is not None:
        return bool(val)
    for path in sorted(glob.glob(_under(root, CPUFREQ_GLOB) + "/boost")):
        val = read_int(path)
        if val is not None:
            return bool(val)
    return None


def read_epp_preferences(root=None):
    """EPP names this kernel accepts, or [] if the machine has no EPP."""
    for path in sorted(glob.glob(
            _under(root, CPUFREQ_GLOB) + "/energy_performance_available_preferences")):
        val = read_file(path)
        if val:
            return val.split()
    return []


def read_current_epp(root=None):
    """EPP the hardware is on right now, or None."""
    for path in sorted(glob.glob(
            _under(root, CPUFREQ_GLOB) + "/energy_performance_preference")):
        val = read_file(path)
        if val:
            return val
    return None


# -- GPU ---------------------------------------------------------------------

def read_nvidia_stats(timeout=5):
    """(temp_c, power_w) for the NVIDIA card, either of which may be None.

    One nvidia-smi call for both, because each invocation costs a couple of
    hundred milliseconds and this runs on a 2-second timer. Every failure
    mode -- no driver, no binary, card powered down under supergfxctl,
    '[N/A]' where a number should be -- lands on None rather than an
    exception, since a laptop with the dGPU asleep is a normal state and not
    a reason for the overview to stop updating."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None, None
    if result.returncode != 0 or not result.stdout.strip():
        return None, None
    # Multi-GPU machines print one line per card; the first is the one this
    # app drives.
    fields = result.stdout.strip().splitlines()[0].split(",")
    out = []
    for field in (fields + ["", ""])[:2]:
        try:
            out.append(float(field.strip()))
        except ValueError:
            out.append(None)
    return out[0], out[1]


# Ranges every GPU control is bounded by.
#
# The two asus-wmi knobs are the kernel driver's own limits (NVIDIA_BOOST_MIN/
# MAX and NVIDIA_TEMP_MIN/MAX in asus-wmi.c) -- a write outside them comes
# back -EINVAL, so offering a wider slider would only produce failures.
#
# The power and clock-ceiling numbers are *fallbacks*. The card is asked for
# its own in detect_gpu_limits below, because a 140 W ceiling is this laptop's
# and hardcoding it would misdescribe every other card.
DYN_BOOST_MIN, DYN_BOOST_MAX = 5, 25
TEMP_TARGET_MIN, TEMP_TARGET_MAX = 75, 87
CLOCK_OFFSET_MIN, CLOCK_OFFSET_MAX = -1000, 1000
MEM_CLOCK_OFFSET_MIN, MEM_CLOCK_OFFSET_MAX = -1000, 1000
GPU_MIN_W_FALLBACK, GPU_MAX_W_FALLBACK = 5, 140
CLOCK_LIMIT_MIN, CLOCK_LIMIT_FALLBACK_MAX = 200, 3090


def parse_gpu_power_limits(text):
    """``(min_w, max_w)`` from the power-limit CSV, or None.

    ``0 < lo < hi`` is the whole validation, and it is not paranoia: a card
    that reports "[N/A]" for one of them, or a pair the wrong way round,
    would otherwise hand a slider an empty or inverted range."""
    for line in (text or "").strip().splitlines():
        fields = [f.strip() for f in line.split(",")]
        if len(fields) < 3:
            continue
        try:
            lo, hi = float(fields[1]), float(fields[2])
        except ValueError:
            continue
        if 0 < lo < hi:
            return round(lo), round(hi)
    return None


def parse_gpu_name(text):
    """The card's name from the same CSV row, or None."""
    for line in (text or "").strip().splitlines():
        name = line.split(",")[0].strip()
        if name and name != "[N/A]":
            return name
    return None


def parse_gpu_max_clock(text):
    """Highest lockable graphics clock in MHz from ``nvidia-smi -q -d CLOCK``.

    The file lists several "Graphics" lines -- current, application, default,
    max -- so the section heading is what disambiguates them; taking the
    first Graphics line in the file would report the clock the card happens
    to be running at right now as its ceiling."""
    in_max = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("Max Clocks"):
            in_max = True
        elif in_max and stripped.startswith("Graphics"):
            try:
                mhz = int(stripped.split(":", 1)[1].strip().split()[0])
            except (IndexError, ValueError):
                return None
            return mhz if mhz > 0 else None
    return None


def default_gpu_limits():
    """The ranges to use when the card cannot be asked. A fresh dict every
    call: it is handed to the UI, which is free to keep it."""
    return {"name": None,
            "min_w": GPU_MIN_W_FALLBACK,
            "max_w": GPU_MAX_W_FALLBACK,
            "clock_limit_max": CLOCK_LIMIT_FALLBACK_MAX}


def detect_gpu_limits(timeout=5):
    """Ask the card for its real power and clock limits.

    Two nvidia-smi calls, run once at startup, because the alternative is a
    power slider that stops at another laptop's ceiling. Each failure falls
    back independently -- a driver that answers the CSV query but not the
    CLOCK dump still gets its true wattage range."""
    limits = default_gpu_limits()
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,power.min_limit,power.max_limit",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            limits["name"] = parse_gpu_name(result.stdout) or limits["name"]
            found = parse_gpu_power_limits(result.stdout)
            if found:
                limits["min_w"], limits["max_w"] = found
    except Exception:
        pass
    try:
        result = subprocess.run(["nvidia-smi", "-q", "-d", "CLOCK"],
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            mhz = parse_gpu_max_clock(result.stdout)
            if mhz:
                limits["clock_limit_max"] = mhz
    except Exception:
        pass
    return limits


def gpu_clock_limit_arg(mhz, max_mhz):
    """What to hand ``run_helper("gpuclocklimit", ...)``.

    The top of the slider is "reset", not a lock at the stock maximum:
    locking there still *pins* the clock, so the card would stop idling down
    and stop boosting -- the opposite of the no-limit the position means."""
    mhz = int(mhz)
    return "reset" if mhz >= int(max_mhz) else mhz


# nvidia-settings attribute names for the two offsets. These shift the whole
# voltage/frequency curve -- a genuine over/underclock -- unlike the ceiling
# above, and they are not a helper action: nvidia-settings needs the user's
# X/Wayland session, so running it through sudo would talk to the wrong
# display or none at all.
NV_CLOCK_ATTRIBUTES = {
    "core": "GPUGraphicsClockOffsetAllPerformanceLevels",
    "memory": "GPUMemoryTransferRateOffsetAllPerformanceLevels",
}


def nvidia_settings_args(kind, mhz):
    """The full nvidia-settings command line for one clock offset."""
    attribute = NV_CLOCK_ATTRIBUTES[kind]
    return ["nvidia-settings", "-a", f"[gpu:0]/{attribute}={int(mhz)}"]


def set_nvidia_clock_offset(kind, mhz, timeout=10):
    """Apply one clock offset, returning ``(ok, message)`` like run_helper."""
    try:
        result = subprocess.run(nvidia_settings_args(kind, mhz),
                                capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return False, str(e)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "unknown error").strip()
    return True, result.stdout.strip()


def read_nv_dynamic_boost(root=None):
    """Dynamic Boost watts the firmware is holding, clamped, or None."""
    return _read_clamped(f"{ASUS_WMI_DIR}/nv_dynamic_boost",
                         DYN_BOOST_MIN, DYN_BOOST_MAX, root)


def read_nv_temp_target(root=None):
    """GPU temperature target the firmware is holding, clamped, or None."""
    return _read_clamped(f"{ASUS_WMI_DIR}/nv_temp_target",
                         TEMP_TARGET_MIN, TEMP_TARGET_MAX, root)


def _read_clamped(path, low, high, root):
    """An int from sysfs, held inside the range the driver accepts.

    Clamped rather than rejected: these are used as the starting value for a
    profile that has none, and a machine reporting something outside the
    kernel's own range should still land on a usable number."""
    val = read_int(_under(root, path))
    return None if val is None else max(low, min(high, val))


# -- Graphics mode (supergfxctl) ---------------------------------------------

# Used only when supergfxctl will not say what it supports. Listing a mode
# the daemon rejects is harmless -- the switch fails with its message -- but
# the daemon's own list is always preferred, because on this machine it
# reports exactly one supported mode and a picker offering three would be
# offering two that cannot work.
GPU_MODES_FALLBACK = ("Integrated", "Hybrid", "AsusMuxDgpu")


def parse_supergfx_modes(text):
    """The mode list out of ``supergfxctl -s``: ``[Integrated, Hybrid]``."""
    text = (text or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.strip("[]").split(",")
            if part.strip()]


def read_gpu_mode(timeout=5):
    """The graphics mode in force, as supergfxctl spells it, or None."""
    try:
        result = subprocess.run(["supergfxctl", "-g"], capture_output=True,
                                text=True, timeout=timeout)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def read_supported_gpu_modes(timeout=5):
    """The modes this machine can actually be switched to, or []."""
    try:
        result = subprocess.run(["supergfxctl", "-s"], capture_output=True,
                                text=True, timeout=timeout)
    except Exception:
        return []
    if result.returncode != 0:
        return []
    return parse_supergfx_modes(result.stdout)


def set_gpu_mode(mode, timeout=10):
    """Switch graphics mode, returning ``(ok, message)``.

    Not run through the helper: supergfxctl talks to supergfxd over the
    system bus and does its own authorisation."""
    try:
        result = subprocess.run(["supergfxctl", "-m", str(mode)],
                                capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return False, str(e)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "unknown error").strip()
    return True, result.stdout.strip()


# -- OS power mode (power-profiles-daemon) -----------------------------------

# PPD has shipped under two bus names across distro versions, so both are
# tried rather than one being assumed. Same list as the enforcer's.
PPD_BUS_NAMES = ("net.hadess.PowerProfiles",
                 "org.freedesktop.UPower.PowerProfiles")


def parse_busctl_string(text):
    """The value out of ``busctl get-property``'s ``s "balanced"`` reply."""
    parts = (text or "").strip().split(None, 1)
    if len(parts) != 2 or parts[0] != "s":
        return None
    return parts[1].strip().strip('"') or None


def read_power_mode(timeout=5):
    """The OS power mode (PPD's ActiveProfile), or None.

    powerprofilesctl first because it is one call and no bus-name guessing;
    busctl behind it because the CLI is a separate package and the daemon
    can be there without it -- which is exactly the machine where this
    readout matters most."""
    try:
        result = subprocess.run(["powerprofilesctl", "get"],
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    for name in PPD_BUS_NAMES:
        path = "/" + name.replace(".", "/")
        try:
            result = subprocess.run(
                ["busctl", "--system", "get-property", name, path, name,
                 "ActiveProfile"],
                capture_output=True, text=True, timeout=timeout)
        except Exception:
            continue
        if result.returncode == 0:
            mode = parse_busctl_string(result.stdout)
            if mode:
                return mode
    return None


# -- Log ---------------------------------------------------------------------

# Written by the GTK3 app, the boot-apply service and the enforcer, all of
# which run outside this process. Reading it back is the only way the window
# can show what happened to the hardware while it was closed.
LOG_PATH = os.path.expanduser("~/.local/share/rogcontrol/rogcontrol.log")

# Most a tail ever reads off disk. The log rotates, but a rotation that has
# not happened yet must not turn "show me the log" into a multi-megabyte
# read on the main loop.
LOG_TAIL_BYTES = 256 * 1024


def read_log_tail(max_lines=200, path=None, max_bytes=LOG_TAIL_BYTES):
    """The last ``max_lines`` lines of the log, or None if it cannot be read.

    Reads from the end rather than the start, and drops the first line of
    that window unless the window began at the start of the file -- seeking
    into the middle of a file lands mid-line, and a half line at the top of
    the view reads as a corrupt log."""
    path = LOG_PATH if path is None else path
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            start = max(0, size - max_bytes)
            f.seek(start)
            data = f.read()
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if start > 0 and lines:
        lines = lines[1:]
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[-max_lines:]
    return "\n".join(lines)


# -- Fans --------------------------------------------------------------------

FAN_CHANNELS = ("1", "2", "3")
FAN_LABELS = {"1": "CPU fan", "2": "GPU fan", "3": "Mid fan"}


def read_fan_rpms(root=None):
    """{channel: rpm} for the three fans; a channel missing its file is None.

    The rpm files live on the plain ``asus`` hwmon, while the curve that
    drives them is written to ``asus_custom_fan_curve`` -- two different
    nodes for the two halves of the same fan, which is exactly the trap this
    function exists to hide."""
    hw = find_hwmon_by_name("asus", root=root)
    rpms = {ch: None for ch in FAN_CHANNELS}
    if not hw:
        return rpms
    for ch in FAN_CHANNELS:
        rpms[ch] = read_int(os.path.join(hw, f"fan{ch}_input"))
    return rpms


def read_fan_curve_points(channel, root=None, n=8):
    """The curve the driver holds for one fan: ``[(temp, pwm), ...]`` or None.

    pwm as written, 0-255, not percent -- see fancurve.curve_matches_hardware
    for why the comparison is done in the units the hardware actually stores.

    None means "cannot tell": no such hwmon, or a point file that would not
    read. That is not the same as "the curve differs", and the caller must
    not treat it as one, or a machine without the interface would show a
    permanent unsaved-changes warning."""
    hw = find_hwmon_by_name("asus_custom_fan_curve", root=root)
    if not hw:
        return None
    points = []
    for i in range(1, n + 1):
        temp = read_int(os.path.join(hw, f"pwm{channel}_auto_point{i}_temp"))
        pwm = read_int(os.path.join(hw, f"pwm{channel}_auto_point{i}_pwm"))
        if temp is None or pwm is None:
            return None
        points.append((temp, pwm))
    return points


def read_fan_curve_enabled(root=None):
    """{channel: bool} -- is the EC running our custom curve on this fan?

    ``pwmN_enable`` reads 1 while the custom curve is in force and 2 when the
    firmware has taken the fan back onto its own automatic curve. It does
    that on its own, on power-mode changes and on resume, which is the single
    failure this app exists to catch: the curve is applied, the fans are
    still loud, and nothing on screen said the EC had quietly dropped it.

    A channel whose file cannot be read is None (unknown), not False, so
    "no custom fan curve interface at all" cannot be mistaken for "the EC
    dropped the curve"."""
    hw = find_hwmon_by_name("asus_custom_fan_curve", root=root)
    state = {ch: None for ch in FAN_CHANNELS}
    if not hw:
        return state
    for ch in FAN_CHANNELS:
        val = read_int(os.path.join(hw, f"pwm{ch}_enable"))
        state[ch] = None if val is None else (val == 1)
    return state


# -- Battery -----------------------------------------------------------------

def read_battery(root=None):
    """(percent, charging) for the first real battery, or (None, None).

    "charging" covers Charging only -- Full and "Not charging" (which is what
    a charge-limited ASUS reports when sitting on AC at its threshold) are
    not charging, and showing them as such would make the readout lie about
    what the battery is doing."""
    base = _under(root, POWER_SUPPLY_DIR)
    try:
        for entry in sorted(os.listdir(base)):
            path = os.path.join(base, entry)
            if read_file(os.path.join(path, "type")) != "Battery":
                continue
            capacity = read_int(os.path.join(path, "capacity"))
            if capacity is None:
                continue
            status = read_file(os.path.join(path, "status")) or ""
            return capacity, status == "Charging"
    except OSError:
        pass
    return None, None


def read_charge_limit(root=None):
    """Charge threshold the firmware is actually holding, in percent, or None.

    Read from the hardware rather than trusted from the config, because the
    firmware is free to ignore or clamp what was asked for."""
    base = _under(root, POWER_SUPPLY_DIR)
    try:
        for entry in sorted(os.listdir(base)):
            path = os.path.join(base, entry)
            if read_file(os.path.join(path, "type")) != "Battery":
                continue
            val = read_int(os.path.join(path, "charge_control_end_threshold"))
            if val is not None:
                return val
    except OSError:
        pass
    return None


def is_ac_connected(root=None):
    """True on mains, False on battery, None if there is no Mains supply."""
    base = _under(root, POWER_SUPPLY_DIR)
    try:
        for entry in sorted(os.listdir(base)):
            path = os.path.join(base, entry)
            if read_file(os.path.join(path, "type")) != "Mains":
                continue
            val = read_file(os.path.join(path, "online"))
            if val is not None:
                return val == "1"
    except OSError:
        pass
    return None


# -- Capabilities ------------------------------------------------------------

def have_cmd(name):
    return subprocess.run(["sh", "-c", f"command -v {name}"],
                          capture_output=True).returncode == 0


def detect_capabilities(root=None):
    """Probe what this particular machine actually supports.

    Returns a plain dict rather than setting a module global: the old version
    stashed it in one, which meant importing the module and reading the
    capabilities were two steps that could be got out of order. Callers hold
    their own copy.

    What is *installed* (ryzenadj, nvidia-smi) is still probed via PATH and
    ignores ``root`` -- there is no meaningful way to re-base a PATH lookup,
    and a test that cares passes the dict in rather than calling this."""
    caps = {}
    caps["fan_curve"] = find_hwmon_by_name("asus_custom_fan_curve", root=root) is not None
    caps["fan_rpm"] = find_hwmon_by_name("asus", root=root) is not None
    caps["cpu_temp"] = find_hwmon_by_name("k10temp", root=root) is not None
    caps["pkg_power"] = find_hwmon_by_name("amdgpu", root=root) is not None
    caps["nv_temp_target"] = os.path.exists(_under(root, f"{ASUS_WMI_DIR}/nv_temp_target"))
    caps["nv_dynamic_boost"] = os.path.exists(_under(root, f"{ASUS_WMI_DIR}/nv_dynamic_boost"))
    caps["nvidia"] = have_cmd("nvidia-smi")
    # A separate question from nvidia-smi: the two clock offsets go through
    # nvidia-settings, which is its own package and is missing on plenty of
    # machines that have a working driver.
    caps["nvidia_settings"] = have_cmd("nvidia-settings")
    caps["supergfxctl"] = have_cmd("supergfxctl")
    caps["rogauracore"] = have_cmd("rogauracore")
    caps["ryzenadj"] = have_cmd("ryzenadj") or os.path.exists("/usr/local/bin/ryzenadj")
    caps["cpu_boost"] = (
        os.path.exists(_under(root, "/sys/devices/system/cpu/cpufreq/boost"))
        or bool(glob.glob(_under(root, CPUFREQ_GLOB) + "/boost")))
    # The preference names differ between amd-pstate and intel_pstate, so read
    # them from the kernel instead of hardcoding a list. "custom" is dropped:
    # it needs a raw 0-255 value written elsewhere, so offering it in a
    # dropdown would only produce failures.
    caps["cpu_epp"] = [p for p in read_epp_preferences(root=root) if p != "custom"]
    caps["cpu_clock"] = read_cpu_clock_range(root=root)
    caps["kbd_backlight"] = os.path.exists(
        _under(root, "/sys/class/leds/asus::kbd_backlight/brightness"))
    caps["charge_limit"] = read_charge_limit(root=root) is not None
    return caps
