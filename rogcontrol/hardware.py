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

from .profiles import PROFILE_TO_PPD_MODE

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


# ryzenadj prints this to stderr on EVERY run on a machine with no ryzen_smu
# module -- including every run that works. This machine has no such module,
# so the line is always there, it is always the first line of stderr, and
# reporting "the first thing on stderr" therefore reported this instead of
# whatever actually went wrong. It is a statement about which path ryzenadj
# took, not a failure.
HELPER_NOISE_MARKERS = (
    "no compatible ryzen_smu kernel module found",
)

NO_ERROR_TEXT = (
    "failed with no message beyond ryzenadj's usual ryzen_smu/dev-mem warning")


def _is_helper_noise(line):
    lowered = line.lower()
    return any(marker in lowered for marker in HELPER_NOISE_MARKERS)


def helper_error_message(result):
    """Why a failed helper call failed, with the known noise removed.

    Takes anything with ``stdout``/``stderr``/``returncode`` -- a
    CompletedProcess in production, a stand-in in the tests.

    Both streams are read, and stdout comes first, because ryzenadj puts the
    human-readable reason on *stdout* ("PCI Bus is not writeable, check
    secure boot") and keeps stderr for library chatter. Reading stderr alone,
    which is what this used to do, could not have shown the real reason even
    with the warning filtered out. Identical lines collapse: a failing
    ryzenadj repeats the same pcilib line five times, and five copies of it
    tell you nothing four of them did not.

    Only ever called on a non-zero exit -- a successful call is not an error
    however much it writes to stderr."""
    lines, seen, had_noise = [], set(), False
    for stream in (getattr(result, "stdout", ""),
                   getattr(result, "stderr", "")):
        for raw in (stream or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if _is_helper_noise(line):
                had_noise = True
                continue
            if line not in seen:
                seen.add(line)
                lines.append(line)
    if lines:
        return "; ".join(lines)
    # Nothing but the warning, or nothing at all. Say which, and carry the
    # exit code, because that is then the only fact left about the failure.
    code = getattr(result, "returncode", None)
    suffix = "" if code is None else f" (exit {code})"
    return (NO_ERROR_TEXT if had_noise else "unknown error") + suffix


def run_helper(*args, timeout=10):
    """Run one privileged action, returning ``(ok, message)``.

    ``sudo -n`` never prompts: if the sudoers rule for the helper is missing
    this fails immediately with a message rather than hanging a worker thread
    on a password prompt nobody can see. The helper validates every argument
    itself -- these calls drive real hardware, so the range checks live on the
    privileged side where they cannot be bypassed by a caller.

    Failure is a non-zero exit code and nothing else. Output on stderr is not
    failure: the one call that matters here, ``cpu``, writes to stderr on
    every single run."""
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
        msg = helper_error_message(result)
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


# -- asusd (asusctl's daemon) -------------------------------------------------
#
# asusd is the other program that drives this hardware: the same asus-wmi
# platform knobs, the same custom fan curves, the same keyboard lighting. Two
# daemons re-asserting different fan curves on the same three channels is not
# a configuration, it is a fight, and the fans are where it is audible. So the
# System page has to be able to say whether it is there, and to stop it.
#
# Reading its state needs no privilege at all -- systemctl answers is-active
# and is-enabled to any user -- so only the two changes go through the helper.

ASUSD_SERVICE = "asusd.service"
ASUSD_PACKAGE = "asusctl"

# The four states worth telling the user apart.
ASUSD_ABSENT = "absent"
ASUSD_RUNNING = "running"
ASUSD_STOPPED_ENABLED = "stopped-enabled"
ASUSD_STOPPED_DISABLED = "stopped-disabled"

# systemd's "enabled" has more than one spelling, and only these two mean
# "this will come back on its own at the next boot". "static" and "indirect"
# units are not started by systemd on their own.
ENABLED_WORDS = ("enabled", "enabled-runtime")

# How to remove asusctl, per package manager. The app never runs any of
# these: removing packages is a transaction the user should see, in their own
# terminal, with its own confirmation. This is text to show, not a command to
# execute -- which is also why the sudo is written into it.
UNINSTALL_COMMANDS = (
    ("pacman", "sudo pacman -Rs asusctl"),
    ("dnf", "sudo dnf remove asusctl"),
    ("zypper", "sudo zypper remove asusctl"),
    ("apt-get", "sudo apt remove asusctl"),
    ("emerge", "sudo emerge --deselect asusctl"),
    ("xbps-remove", "sudo xbps-remove -R asusctl"),
)


def parse_asusd_state(unit_files="", is_active="", is_enabled="",
                      binary_found=False):
    """What asusd is doing, from three systemctl answers plus PATH.

    Pure, so the states can be tested without a machine that has asusctl on
    it -- which this one does not.

    ``unit_files`` is the output of ``systemctl list-unit-files
    asusd.service``, ``is_active`` and ``is_enabled`` the one-word answers
    from the matching subcommands, and ``binary_found`` whether asusd or
    asusctl is on PATH.

    Installed is decided from the unit file *or* the binary, because the two
    can disagree in both directions: a package installed but never enabled
    still ships the unit, and a build installed by hand may put the binary in
    /usr/local/bin with no unit at all. Either one means asusctl is on this
    machine and can take the hardware.
    """
    active = (is_active or "").strip()
    enabled = (is_enabled or "").strip()
    has_unit = ASUSD_SERVICE in (unit_files or "")
    installed = bool(has_unit or binary_found)
    state = {
        "installed": installed,
        "active": active == "active",
        # None rather than False when there is no unit to enable: "will not
        # start at boot" and "cannot be asked" are different answers.
        "enabled": (enabled in ENABLED_WORDS) if has_unit else None,
        "has_unit": has_unit,
        "raw_active": active,
        "raw_enabled": enabled,
    }
    if not installed:
        state["state"] = ASUSD_ABSENT
    elif state["active"]:
        state["state"] = ASUSD_RUNNING
    elif state["enabled"]:
        state["state"] = ASUSD_STOPPED_ENABLED
    else:
        state["state"] = ASUSD_STOPPED_DISABLED
    return state


def read_asusd_state(timeout=5):
    """:func:`parse_asusd_state` against the real systemctl.

    Nothing here needs root, and nothing here writes: it is three read-only
    queries plus two PATH lookups.
    """
    def ask(*args):
        try:
            result = subprocess.run(["systemctl", *args], capture_output=True,
                                    text=True, timeout=timeout)
        except Exception:
            return ""
        # The return code is deliberately ignored: is-active exits non-zero
        # for a stopped unit and is-enabled for a disabled one, and in both
        # cases the word on stdout is exactly the answer being asked for.
        return result.stdout or ""

    return parse_asusd_state(
        unit_files=ask("list-unit-files", ASUSD_SERVICE),
        is_active=ask("is-active", ASUSD_SERVICE),
        is_enabled=ask("is-enabled", ASUSD_SERVICE),
        binary_found=have_cmd("asusd") or have_cmd("asusctl"))


def set_asusd_running(running, timeout=20):
    """Enable+start or disable+stop asusd, returning ``(ok, message)``.

    Through the privileged helper, which takes no argument for either action
    and names the unit itself -- there is no route from here to systemctl
    with a unit name of anyone's choosing.
    """
    return run_helper("asusd_enable" if running else "asusd_disable",
                      timeout=timeout)


def asusd_uninstall_command(have=None):
    """The exact removal command for this distro, or None if unrecognised.

    Text for the user to read and run themselves. This app does not remove
    packages: a package manager run from a GUI is a transaction nobody sees,
    and asusctl is not always the only thing that would go with it."""
    have = have or have_cmd
    for tool, command in UNINSTALL_COMMANDS:
        if have(tool):
            return command
    return None


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


# The four steps of a CPU apply, in the only order that works. This is a
# hardware constraint, not a preference: writing cpufreq's ``boost`` refreshes
# every policy and takes ``scaling_max_freq`` back up to hardware maximum with
# it, so a clock cap written before boost is silently undone. The same order
# is spelled out again in app.py's whole-profile apply and in
# rogcontrol-apply.py, which is why it is worth having one tested definition
# of it here.
CPU_APPLY_STEPS = ("limits", "boost", "epp", "clock")


def cpu_apply_plan(values, caps=None):
    """The helper calls one CPU apply makes, as ``[(step, args), ...]``.

    Pure: no hardware, no widgets, no subprocess -- it turns a set of wanted
    values plus what the machine can do into the exact argument lists to hand
    to :func:`run_helper`, in order. The CPU page and the tests both use it,
    so the order the page applies in is the order that is tested.

    ``values`` uses the config's own units and names: ``stapm``/``fast``/
    ``slow`` in milliwatts, ``temp`` in degrees, ``coall``, ``boost`` as a
    bool, ``epp`` as a name, ``max_freq`` in kHz with 0 meaning "no ceiling".

    A step is left out when the machine cannot do it or the values say
    nothing about it -- a missing key means the caller has no opinion, and
    forcing a default would make every profile carry one.
    """
    caps = caps or {}
    plan = []
    if caps.get("ryzenadj") and all(
            key in values for key in ("stapm", "fast", "slow", "temp")):
        plan.append(("limits", ("cpu", values["stapm"], values["fast"],
                                values["slow"], values["temp"],
                                values.get("coall", 0))))
    if "boost" in values and caps.get("cpu_boost"):
        plan.append(("boost", ("cpuboost", 1 if values["boost"] else 0)))
    if values.get("epp") and caps.get("cpu_epp"):
        plan.append(("epp", ("cpuepp", values["epp"])))
    # Last, after boost. 0 means "no ceiling" and still has to be written, or
    # a cap from a previous profile survives the switch.
    if "max_freq" in values and caps.get("cpu_clock"):
        plan.append(("clock", ("cpuclock", values["max_freq"] or "max")))
    return plan


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


# Filled in by gpu_clock_limit_max on first use.
_gpu_clock_limit_max = None


def gpu_clock_limit_max(timeout=5):
    """The card's own top lockable clock, detected once per process.

    For the three scripts that apply a profile with no window open -- the
    boot apply, the hotkey cycler and the enforcer. The window already has
    this in ``caps["gpu_limits"]``; they have nowhere to keep it, and each
    used to compare a stored ceiling against a hardcoded 3090 instead. That
    is this laptop's card and nothing else's: on a card that boosts higher,
    a profile saved at the top of the slider would come back as a real lock
    a little below maximum -- pinning the clock, which is the exact opposite
    of the "no ceiling" the top of the slider means.

    Cached because the enforcer applies profiles for the life of the
    session, and two nvidia-smi calls per apply is real cost for an answer
    that cannot change while the machine is running. Cached even when
    detection failed and the fallback came back: a machine with no NVIDIA
    card would otherwise pay for two failed execs on every single apply."""
    global _gpu_clock_limit_max
    if _gpu_clock_limit_max is None:
        _gpu_clock_limit_max = detect_gpu_limits(timeout)["clock_limit_max"]
    return _gpu_clock_limit_max


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


# -- Firmware settings -------------------------------------------------------

# The startup chime, on the same asus-wmi platform device as the two knobs
# above. Unlike them it belongs to the machine rather than to a profile: it is
# stored in the firmware, it happens before any operating system is running,
# and nobody wants their laptop to start chiming because they picked
# Performance.
BOOT_SOUND_PATH = f"{ASUS_WMI_DIR}/boot_sound"


def read_boot_sound(root=None):
    """1 if the firmware plays its chime at power-on, 0 if it does not, None
    if this machine has no such control.

    Anything else the file might hold is None as well. This feeds a switch
    with two positions, and guessing which one an unexpected value means
    would show a state the firmware is not in -- "unavailable" is the honest
    answer to a reading nothing here understands."""
    val = read_int(_under(root, BOOT_SOUND_PATH))
    return val if val in (0, 1) else None


# -- Graphics mode (supergfxctl) ---------------------------------------------

# The three modes this app offers, always, in the order a user thinks about
# them: least power, both, most power. Same list the GTK3 app had.
GPU_MODES = ("Integrated", "Hybrid", "AsusMuxDgpu")


def gpu_mode_choices(active=None, supported=()):
    """Every mode to offer: the three above, plus anything else in play.

    ``supergfxctl -s`` deliberately does **not** filter this. It answers
    "what will the daemon accept in the state it is in right now", and on a
    laptop whose hardware MUX is set to the discrete GPU that answer is the
    single mode it is already in -- so filtering by it leaves a picker with
    nothing in it but the current mode, which is how the ability to switch
    went missing. What the daemon lists is shown to the user as information
    beside the picker; a mode it will not take comes back refused, in its own
    words, which is a better answer than an empty list.

    Anything ``-s`` reports that is not one of the three is added rather than
    dropped -- Vfio, AsusEgpu, NvidiaNoModeset are real modes on the machines
    that have them -- and so is the mode actually in force, which must always
    be selectable or the picker would show some other mode as current.
    """
    modes = list(GPU_MODES)
    for extra in list(supported or ()) + [active]:
        if extra and extra not in modes:
            modes.append(extra)
    return modes


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


def ppd_service_name(timeout=5):
    """The bus name power-profiles-daemon is actually answering on, or None.

    Introspection rather than a hardcoded name for the same reason as
    PPD_BUS_NAMES: the daemon is mid-rename across distro versions and both
    names are in the wild. Same detection the enforcer does."""
    for name in PPD_BUS_NAMES:
        try:
            result = subprocess.run(
                ["busctl", "--system", "introspect", name,
                 "/" + name.replace(".", "/")],
                capture_output=True, text=True, timeout=timeout)
        except Exception:
            continue
        if result.returncode == 0:
            return name
    return None


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


def set_power_mode(mode, timeout=5):
    """Set the OS power mode (PPD's ActiveProfile). Returns ``(ok, message)``.

    This is not cosmetic and it is not optional. Selecting a profile without
    it leaves the OS on the old mode, and the enforcer -- which treats an
    external mode change as the OS asking for a profile -- then switches the
    profile back within its 60 second cycle and re-pushes all three fan
    curves to do it. The result is ~16 seconds of fan writes for the profile
    the user chose, ~16 seconds for the one they did not, and the switch
    silently undone. Set the mode first, and there is nothing to disagree
    with.

    No exception on failure: PPD is not guaranteed to be installed, and a
    machine without it must still be able to switch profiles."""
    service = ppd_service_name(timeout=timeout)
    if not service:
        return False, "power-profiles-daemon is not answering"
    path = "/" + service.replace(".", "/")
    try:
        result = subprocess.run(
            ["busctl", "--system", "set-property", service, path, service,
             "ActiveProfile", "s", str(mode)],
            capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return False, str(e)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "unknown error").strip()
    return True, str(mode)


def set_power_mode_for_profile(profile_name, timeout=5):
    """Take the OS power mode along with a profile switch.

    Returns ``(ok, message)``, or None when this profile maps to no OS mode.

    None is the whole point of the split: PPD has exactly three modes, and a
    profile the user invented maps to none of them. Forcing one anyway would
    park every custom profile on some arbitrary mode and -- because the mode
    is what the enforcer compares against -- hand the EC a power-mode change
    (and a wiped fan curve) on every switch to it."""
    mode = PROFILE_TO_PPD_MODE.get(profile_name)
    if mode is None:
        return None
    return set_power_mode(mode, timeout=timeout)


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


# -- Keyboard ----------------------------------------------------------------

KBD_BACKLIGHT_PATH = "/sys/class/leds/asus::kbd_backlight/brightness"
USB_DEVICES_DIR = "/sys/bus/usb/devices"

# ASUS Aura keyboard controllers, by USB product ID under vendor 0x0b05.
#
# Every one of these takes the single-colour effects. The multi-zone ones
# (multi_static / multi_breathing) need a controller with four addressable
# zones, which is a smaller set -- sending them to a single-zone keyboard
# lights zone 1 and silently drops the rest, which looks like a broken app
# rather than an unsupported feature.
#
# An ASUS keyboard that isn't listed here still gets the single-colour modes,
# because those are safe everywhere. Only the multi-zone ones are withheld
# until a device is known to handle them. Send the output of `lsusb | grep
# 0b05` if your model does support zones and isn't listed.
AURA_SINGLE_ZONE_IDS = {
    "1854",  # GL553 / GL753
    "1866",  # GL503 / GL703 / GX501 Zephyrus
    "1869",  # GL551 / GL771
    "1822",  # GL502
    "1837",  # GL702
    "19b6",  # N-KEY (current Strix / Scar generation)
    "1a30",  # newer N-KEY revision
}
AURA_MULTI_ZONE_IDS = {"1854", "1866", "1869", "19b6", "1a30"}


def read_kbd_brightness(root=None):
    """Backlight level 0-3 the LED class is actually holding, or None.

    Read back rather than trusted from the config: the level can be changed
    from outside this app -- the keyboard's own Fn keys, GNOME's quick
    settings, this project's own shortcut script -- and a slider that was set
    once at startup and never looked again shows a stale value all session."""
    return read_int(_under(root, KBD_BACKLIGHT_PATH))


def find_aura_keyboard(root=None):
    """USB product ID of the ASUS Aura keyboard controller, or None.

    Reads /sys directly rather than shelling out to lsusb, which is not
    installed everywhere and would be a new dependency for a detection that
    is three file reads."""
    base = _under(root, USB_DEVICES_DIR)
    try:
        for entry in sorted(os.listdir(base)):
            path = os.path.join(base, entry)
            if read_file(os.path.join(path, "idVendor")) != "0b05":
                continue
            product_id = read_file(os.path.join(path, "idProduct"))
            if product_id:
                return product_id.lower()
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
    # Presence, not the value: a machine whose firmware has the chime turned
    # off still has the control, and gating on the reading would make the
    # switch disappear the moment it was switched off.
    caps["boot_sound"] = os.path.exists(_under(root, BOOT_SOUND_PATH))
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
    # RGB support is two separate questions: is there an Aura controller at
    # all, and does it have addressable zones. A mode that cannot work on this
    # machine is dropped from the picker rather than left there to be chosen
    # and silently do nothing.
    aura_id = find_aura_keyboard(root=root)
    caps["aura_id"] = aura_id
    caps["kbd_rgb"] = bool(aura_id) and caps["rogauracore"]
    caps["kbd_rgb_zones"] = bool(aura_id) and aura_id in AURA_MULTI_ZONE_IDS
    caps["kbd_battery"] = read_battery(root=root)[0] is not None
    # kbd_ambient is deliberately absent: answering it needs GStreamer and a
    # session bus, and this module stays importable by the helper scripts and
    # the tests, which have neither. The app fills it in from
    # widgets.ambient.ambient_available() the same way it fills in gpu_limits.
    caps["charge_limit"] = read_charge_limit(root=root) is not None
    return caps
