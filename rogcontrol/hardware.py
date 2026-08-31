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

import fcntl
import glob
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile

from . import APP_VERSION, kbdcolor
from .profiles import PROFILE_TO_PPD_MODE

HELPER = "/usr/local/bin/rogcontrol-helper"
ASUS_WMI_DIR = "/sys/devices/platform/asus-nb-wmi"
HWMON_DIR = "/sys/class/hwmon"
POWER_SUPPLY_DIR = "/sys/class/power_supply"
CPUFREQ_GLOB = "/sys/devices/system/cpu/cpufreq/policy*"
POWERCAP_DIR = "/sys/class/powercap"

# Guards every mutable module global in this file: the dedupe table below,
# the RAPL history, and the cached GPU clock ceiling.
#
# None of these were shared state when this file was one process reading
# sysfs on a timer. They are now: the enforcer runs a ppd watcher thread and
# a power-supply watcher thread alongside its main loop, and all three log,
# apply profiles and ask for the GPU ceiling. A dict is not corrupted by
# concurrent writes in CPython, but the read-decide-write sequences built on
# top of these are not atomic -- two threads can both pass the dedupe check
# for the same key and log the same line twice, and two can both miss the
# clock-ceiling cache and each pay an nvidia-smi exec.
#
# One lock rather than three: it is held for a dict lookup and a store, never
# across I/O (the nvidia-smi probe in gpu_clock_limit_max runs outside it),
# so there is nothing for finer-grained locks to win.
_state_lock = threading.RLock()

# Previous (energy_uj, monotonic timestamp) per RAPL package path, so
# read_cpu_power_w() can turn the running energy counter into a power
# figure. Keyed by path rather than a single slot so tests using a fake
# root never collide with a real reading taken in the same process.
_rapl_energy_history = {}

LOG_PATH = os.path.expanduser("~/.local/share/rogcontrol/rogcontrol.log")
LOG_MAX_BYTES = 256 * 1024
# Separate from the log itself, and never rotated, so its inode is stable --
# which is the whole point. Locking the log file would mean holding a
# descriptor onto a file another process is about to rename out from under
# it, and the lock would then be on the backup rather than on the live log.
LOG_LOCK_PATH = LOG_PATH + ".lock"
_last_logged = {}


def log(message, level="INFO", source="app", dedupe_key=None, dedupe_seconds=300):
    """Append one line to the shared app log.

    Shared by every caller of run_helper (enforcer, apply, cycle-profile) so
    a failing helper call is never silent no matter which of them hit it.

    dedupe_key suppresses an identical repeating message for a while. A
    failing helper call repeats every cycle, and without this a single
    broken sudoers rule would fill the log with the same line forever. The
    check and the stamp are one step under _state_lock, so two threads
    racing on the same key produce one line rather than two.

    Rotation is serialised across PROCESSES with an flock on a separate lock
    file. Five things write this log -- the window, the tray, the boot apply,
    the hotkey cycler and the enforcer -- and rotate-then-append is three
    unsynchronised steps: two processes could both see an oversized log and
    both rename it, the second one renaming a fresh log over the backup the
    first had just made, which loses the older half of the history exactly
    when someone is reading it to work out what went wrong. Appends are
    single writes under LOG_MAX_BYTES and were already atomic on their own;
    it is the rename they now share a lock with.

    Every failure here is swallowed. Logging is what the rest of this file
    does INSTEAD of raising, so it must never become a new way to raise --
    and on a machine with no writable state directory, or a filesystem with
    no flock, the app carries on without a log rather than not at all."""
    if dedupe_key is not None:
        now = time.monotonic()
        with _state_lock:
            if now - _last_logged.get(dedupe_key, -1e9) < dedupe_seconds:
                return
            _last_logged[dedupe_key] = now
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} [{level}] {source}: {message}\n"
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_LOCK_PATH, "a") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            except OSError:
                # No flock on this filesystem. Rotate anyway: an unlocked
                # rotation is what this did before, and the race it loses is
                # one backup file, not the log.
                pass
            if (os.path.exists(LOG_PATH)
                    and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES):
                os.replace(LOG_PATH, LOG_PATH + ".1")
            with open(LOG_PATH, "a") as f:
                f.write(line)
    except OSError:
        pass

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


_HWMON_CACHE = {}  # (root, name) -> resolved hwmon directory path


def find_hwmon_by_name(name, root=None):
    """Path of the hwmon directory whose ``name`` file holds ``name``.

    hwmonN numbering is assigned in probe order and moves between boots, so
    the number can never be hardcoded -- but it is stable for the life of a
    running process, and this is called every couple of seconds by several
    readers, so the resolved path is cached rather than re-listing
    ``/sys/class/hwmon`` and re-opening every device's ``name`` file each
    time. Still verified with one read on every call, so a chip that somehow
    renumbers mid-run falls through to a fresh scan instead of returning a
    stale path."""
    key = (root, name)
    cached = _HWMON_CACHE.get(key)
    if cached is not None:
        if read_file(os.path.join(cached, "name")) == name:
            return cached
        del _HWMON_CACHE[key]
    base = _under(root, HWMON_DIR)
    try:
        for entry in sorted(os.listdir(base)):
            path = os.path.join(base, entry)
            if read_file(os.path.join(path, "name")) == name:
                _HWMON_CACHE[key] = path
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


def helper_command(args, root=None):
    """The argv that reaches the privileged helper via a passwordless ``sudo -n``."""
    return ["sudo", "-n", HELPER, *[str(a) for a in args]]


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
    # A separate process group (start_new_session) so a timeout can kill
    # sudo's whole child tree, not just sudo itself. sudo -n execs the helper
    # directly (no fork), but the helper in turn forks ryzenadj/nvidia-smi/
    # rogauracore -- left running past the timeout, those keep the helper's
    # flock held (see rogcontrol-helper's cpu action), and every apply after
    # this one fails with "another apply in progress" until the orphan exits
    # on its own. subprocess.run's own timeout only ever kills the immediate
    # child, so this uses Popen directly to reach the group.
    try:
        proc = subprocess.Popen(
            helper_command(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
    except Exception as e:
        print(f"rogcontrol: helper could not run: {cmd} -> {e}", file=sys.stderr)
        return False, str(e)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        print(f"rogcontrol: helper timed out: {cmd}", file=sys.stderr)
        return False, "timed out"
    result = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
    if result.returncode != 0:
        msg = helper_error_message(result)
        print(f"rogcontrol: helper failed: {cmd} -> {msg}", file=sys.stderr)
        return False, msg
    return True, result.stdout.strip()


def run_helper_logged(*args, source="app", timeout=30):
    """``run_helper``, with the failure also written to the shared log.

    Returns ``(ok, message)``, the same shape as run_helper -- not a bare
    bool, even though most callers want only the bool. A tuple is always
    truthy, so a caller that forgets to unpack fails loudly at the first
    ``if not`` rather than quietly treating every failure as a success.

    For the callers with nowhere to put a message on screen: the boot apply,
    the hotkey scripts and the enforcer all run with no window, so a helper
    call that fails silently there is a setting the user believes is applied
    and is not. This existed as a hand-copied wrapper in each of them --
    identical bodies differing only in the ``source`` string -- which is the
    duplication that let the original silent-failure bug survive a release in
    every copy but the one it was fixed in.

    The dedupe key is the helper's subcommand, so a limit that fails on every
    60-second enforcer cycle is one line in the log rather than a thousand.
    The default timeout is 30s rather than run_helper's 10s: these callers
    write fan curves and CPU limits, which are slow, and none of them has a
    UI thread that a wait would block."""
    ok, message = run_helper(*args, timeout=timeout)
    if not ok:
        cmd = " ".join(str(a) for a in args)
        log(f"{cmd} failed: {message}", "ERROR", source=source,
            dedupe_key=f"fail:{args[0]}")
    return ok, message


def notify(title, body):
    """A desktop notification, best effort.

    One copy for the five processes that run with no window: the enforcer,
    the boot apply, the profile cycler and the two keyboard hotkeys. For all
    of them this is the ONLY channel to the user -- a hotkey that changed
    nothing and said nothing is indistinguishable from a hotkey that is not
    bound.

    Every failure is swallowed. There may be no notification daemon, no
    session bus, or no notify-send at all, and none of those is a reason to
    take a service down -- or even to fill the log, since it would repeat on
    every event forever."""
    try:
        subprocess.run(["notify-send", "-a", "ROG Control", title, body],
                       capture_output=True, timeout=5)
    except Exception:
        pass


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


def parse_service_state(unit_files="", is_active="", is_enabled="",
                        binary_found=False, service=ASUSD_SERVICE):
    """What a systemd unit is doing, from three systemctl answers plus PATH.

    Pure, so the states can be tested without a machine that has the package
    on it. Shared by asusd and supergfxd rather than written twice: the
    question ("installed? running? will it come back at boot?") and all four
    answers are the same for both, and the only difference is the unit name.

    ``unit_files`` is the output of ``systemctl list-unit-files <service>``,
    ``is_active`` and ``is_enabled`` the one-word answers from the matching
    subcommands, and ``binary_found`` whether the package's binary is on
    PATH.

    Installed is decided from the unit file *or* the binary, because the two
    can disagree in both directions: a package installed but never enabled
    still ships the unit, and a build installed by hand may put the binary in
    /usr/local/bin with no unit at all. Either one means the package is on
    this machine.
    """
    active = (is_active or "").strip()
    enabled = (is_enabled or "").strip()
    has_unit = service in (unit_files or "")
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


def parse_asusd_state(unit_files="", is_active="", is_enabled="",
                      binary_found=False):
    """:func:`parse_service_state` for asusd. Kept as its own name because
    that is what the page and the tests ask for."""
    return parse_service_state(unit_files, is_active, is_enabled,
                               binary_found, ASUSD_SERVICE)


def read_asusd_state(timeout=5):
    """:func:`parse_asusd_state` against the real systemctl.

    Nothing here needs root, and nothing here writes: it is three read-only
    queries plus two PATH lookups.
    """
    def ask(*args):
        try:
            result = subprocess.run(["systemctl", *args],
                                    capture_output=True, text=True,
                                    timeout=timeout)
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

PROC_CPUINFO = "/proc/cpuinfo"


def read_cpu_name(root=None):
    """The processor's marketing name, or None.

    The GPU page has named the card since it was written -- the CPU page
    said only "Processor", which is the one thing the user already knows.

    First "model name" wins: /proc/cpuinfo repeats it once per logical core,
    and this chip has 32 of them."""
    try:
        with open(_under(root, PROC_CPUINFO)) as f:
            for line in f:
                if line.startswith("model name"):
                    _, _, value = line.partition(":")
                    return value.strip() or None
    except OSError:
        return None
    return None


def start_tray():
    """Start the tray service if it is not running. Best effort.

    Called when the window is launched, which is what puts the icon back
    after the tray's own Quit. Quit stops the service and deliberately
    prevents systemd restarting it (see QUIT_EXIT_CODE in rogcontrol-tray),
    so without this the only way back would be a systemctl command or a
    reboot -- and "I quit the tray, then opened the app, and the icon never
    came back" is exactly how that felt.

    Failure is silent: a machine running this from a checkout has no unit
    installed, and that is not a reason to fail to open the window."""
    try:
        subprocess.run(["systemctl", "--user", "start", "rogcontrol-tray.service"],
                       capture_output=True, text=True, timeout=5)
    except Exception:
        pass


# Where a CPU temperature can come from, best first. k10temp's temp1_input
# is Tctl, which is what the embedded controller drives the fans from -- so
# on AMD it is the number to show next to a fan curve even though it reads a
# few degrees above the physical die sensor. coretemp is Intel's equivalent,
# and the generic ACPI thermal zone (named "acpitz" on some kernels and
# "acpitz_0" on others) is the last resort so the readout is not just blank.
# detect_capabilities asks about this same list, so the note under the
# reading and the reading itself cannot disagree.
CPU_TEMP_HWMON_NAMES = ("k10temp", "coretemp", "acpitz_0", "acpitz")

# What /proc/cpuinfo calls each vendor. ryzenadj talks to the Ryzen SMU
# mailbox, so everything it drives -- the four power limits and the Curve
# Optimizer -- exists on AMD and nowhere else. Intel's equivalents live
# behind RAPL and are not wired up here yet.
CPU_VENDOR_AMD = "AuthenticAMD"
CPU_VENDOR_INTEL = "GenuineIntel"


def read_cpu_vendor(root=None):
    """The chip's vendor id, or None if /proc/cpuinfo cannot be read.

    Returned raw ("AuthenticAMD", "GenuineIntel") rather than as a bool: the
    CPU page says which vendor it found, and "not AMD" and "Intel" are not
    the same statement on a machine whose cpuinfo has no vendor line at all.

    First "vendor_id" wins, as with the model name: the file repeats both
    once per logical core."""
    try:
        with open(_under(root, PROC_CPUINFO)) as f:
            for line in f:
                if line.startswith("vendor_id"):
                    _, _, value = line.partition(":")
                    return value.strip() or None
    except OSError:
        return None
    return None


def cpu_is_amd(root=None):
    """True when ryzenadj has a chip it can actually talk to.

    Unknown vendor counts as not-AMD: ryzenadj on a chip it does not
    understand is a failed write at best, and the page hiding a control is
    cheaper than an Apply that reports an error every time."""
    return read_cpu_vendor(root=root) == CPU_VENDOR_AMD


def read_cpu_temp(root=None):
    """Package temperature in C, or None.

    Whichever of CPU_TEMP_HWMON_NAMES this machine has, in that order --
    see that list for why k10temp is the one to prefer on AMD."""
    hw = None
    for name in CPU_TEMP_HWMON_NAMES:
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
    limits below actually cap. There is no equivalent under k10temp.

    Kept as the fallback for read_cpu_power_w() below, for machines with no
    RAPL powercap zone."""
    hw = find_hwmon_by_name("amdgpu", root=root)
    if not hw:
        return None
    micro = read_int(os.path.join(hw, "power1_input"))
    return None if micro is None else micro / 1e6


def find_rapl_package(root=None):
    """Path of the RAPL powercap zone named "package-*", or None.

    ``/sys/class/powercap`` holds one top-level ``intel-rapl:N`` per package
    plus subzones such as ``intel-rapl:N:0`` ("core", "uncore" ...) nested
    under it -- the colon count tells them apart, since a subzone always has
    one more than its parent. Only the top-level package zone is what
    "CPU package power" means; a subzone would silently under-report.

    Despite the ``intel-rapl`` name this driver also covers AMD Zen 2 and
    later, which expose RAPL-compatible MSRs -- this machine included."""
    base = _under(root, POWERCAP_DIR)
    try:
        for entry in sorted(os.listdir(base)):
            if not entry.startswith("intel-rapl:") or entry.count(":") != 1:
                continue
            path = os.path.join(base, entry)
            name = read_file(os.path.join(path, "name"))
            if name and name.startswith("package"):
                return path
    except OSError:
        pass
    return None


def read_cpu_power_w(root=None):
    """True CPU package power in watts, averaged since the previous call.

    RAPL exposes a running energy counter (microjoules), not a power
    sensor, so this is the standard way every tool -- powertop, btop,
    turbostat -- turns it into watts: divide the energy consumed between
    two reads by the wall time between them. That means the first call
    after startup has no baseline yet and returns None; every call after
    that returns the real average draw over the interval since the last one,
    which for a caller polling on a fixed timer is the current figure.

    Falls back to read_package_power_w() -- the amdgpu SoC/PPT reading --
    on a machine with no RAPL powercap zone."""
    path = find_rapl_package(root=root)
    if not path:
        return read_package_power_w(root=root)
    energy = read_int(os.path.join(path, "energy_uj"))
    if energy is None:
        return read_package_power_w(root=root)
    now = time.monotonic()
    # Read the previous sample and store this one as one step: the counter is
    # a running total, so two threads interleaving here would each measure
    # against the other's baseline and report half the real wattage.
    with _state_lock:
        previous = _rapl_energy_history.get(path)
        _rapl_energy_history[path] = (energy, now)
    if previous is None:
        return None
    prev_energy, prev_time = previous
    elapsed = now - prev_time
    if elapsed <= 0:
        return None
    delta = energy - prev_energy
    if delta < 0:
        # The counter wrapped between reads. max_energy_range_uj is the
        # width it wrapped at, so adding it back recovers the true delta.
        max_range = read_int(os.path.join(path, "max_energy_range_uj"))
        if max_range is None:
            return None
        delta += max_range
    return delta / 1e6 / elapsed


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


def read_cpu_clock_floor_default(root=None):
    """The floor cpufreq rests at when nothing has set one, in kHz, or None.

    NOT cpuinfo_min_freq. On amd-pstate the driver parks ``policy->min`` at
    ``amd_pstate_lowest_nonlinear_freq`` -- 1 492 514 kHz on a G614PR against
    a 421 798 kHz hardware minimum -- and that is where an untouched machine
    actually sits. So this is where "no floor" has to put it back to: writing
    the hardware minimum instead would leave every profile apply and every
    60-second enforcer pass quietly holding the machine a gigahertz below
    stock, which is a setting nobody asked for rather than a restore.

    It is also the bottom of the minimum-clock slider, for the same reason:
    below the resting floor there is no floor to raise, only permission to
    idle lower, and that is a different control.

    Falls back to cpuinfo_min_freq where there is no such file (intel_pstate,
    acpi-cpufreq), which is the resting floor on those drivers. The helper's
    ``cpuminclock`` resolves "min" exactly this way, per policy; the two have
    to agree or the slider's bottom would not be the value it writes."""
    for path in sorted(glob.glob(
            _under(root, CPUFREQ_GLOB) + "/cpuinfo_min_freq")):
        base = os.path.dirname(path)
        val = read_int(os.path.join(base, "amd_pstate_lowest_nonlinear_freq"))
        if val is None:
            val = read_int(path)
        if val is not None:
            return val
    return None


def read_current_cpu_clock_cap(root=None):
    """Ceiling currently in force, in kHz, or None."""
    for path in sorted(glob.glob(
            _under(root, CPUFREQ_GLOB) + "/scaling_max_freq")):
        val = read_int(path)
        if val is not None:
            return val
    return None



# intel_pstate's own boost switch, checked only after the two cpufreq
# locations above -- amd-pstate and acpi-cpufreq both keep priority, so an
# AMD machine never reaches this branch. Meaning is INVERTED next to the
# other two: 1 here means turbo is OFF. intel_pstate in active mode exposes
# neither of the cpufreq switches above, so without this Intel turbo boost
# had no control at all.
INTEL_NO_TURBO_PATH = "/sys/devices/system/cpu/intel_pstate/no_turbo"


def read_cpu_boost_enabled(root=None):
    """True/False if a cpufreq boost switch exists, else None.

    amd-pstate publishes one global switch; the other drivers put one under
    each policy, so both locations have to be checked. intel_pstate in
    active mode has neither -- it publishes ``no_turbo`` instead, meaning the
    opposite of the other two, so that one is read and inverted rather than
    just checked for existence."""
    val = read_int(_under(root, "/sys/devices/system/cpu/cpufreq/boost"))
    if val is not None:
        return bool(val)
    for path in sorted(glob.glob(_under(root, CPUFREQ_GLOB) + "/boost")):
        val = read_int(path)
        if val is not None:
            return bool(val)
    val = read_int(_under(root, INTEL_NO_TURBO_PATH))
    if val is not None:
        return not bool(val)
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


# The steps a CPU apply can make, in the only order that works. This is a
# hardware constraint, not a preference: writing cpufreq's ``boost`` refreshes
# every policy and takes ``scaling_max_freq`` back up to hardware maximum with
# it, so a clock cap written before boost is silently undone. The same order
# is spelled out again in app.py's whole-profile apply and in
# rogcontrol-apply.py, which is why it is worth having one tested definition
# of it here.
#
# ``fwreset`` is first and is the only optional-by-request one -- see
# cpu_apply_plan. The floor goes after the ceiling for a second, separate
# reason: cpuclock has to pull scaling_min_freq down on any policy whose
# floor would sit above the new ceiling, so a floor written first can be
# lowered by the very next step.
#
# EVERY step cpu_apply_plan can emit belongs here, and every one of them
# needs a row, a save entry and a label on the CPU page. A step that reaches
# the hardware with no save entry is written on every Apply and never stored,
# and the profile comes back without it each time the page reloads. That is
# what this tuple is checked against.
CPU_APPLY_STEPS = ("fwreset", "limits", "boost", "epp", "clock", "minclock")


def cpu_apply_plan(values, caps=None):
    """The helper calls one CPU apply makes, as ``[(step, args), ...]``.

    Pure: no hardware, no widgets, no subprocess -- it turns a set of wanted
    values plus what the machine can do into the exact argument lists to hand
    to :func:`run_helper`, in order. The CPU page and the tests both use it,
    so the order the page applies in is the order that is tested.

    ``values`` uses the config's own units and names: ``stapm``/``fast``/
    ``slow`` in milliwatts, ``temp`` in degrees, ``coall``, ``boost`` as a
    bool, ``epp`` as a name, ``max_freq`` in kHz with 0 meaning "no ceiling",
    and ``min_freq`` in kHz with 0 meaning "no floor". ``pl1``/``pl2`` are in
    watts, Intel's equivalent of stapm/fast, sent only when
    ``caps["cpu_power_limits"]`` is "ppt" or "rapl" rather than "ryzenadj".

    A step is left out when the machine cannot do it or the values say
    nothing about it -- a missing key means the caller has no opinion, and
    forcing a default would make every profile carry one.

    ``boost`` being explicitly false, on a machine that has a boost control,
    also drops the ``clock`` step: the cores are pinned at base clock by the
    boost write itself, so a ceiling on top of that is not a limit the
    profile is applying.

    ``limits_enabled`` is the exception to that: it is the CPU page's
    checkbox, false drops the whole ryzenadj step, and missing means true so
    that profiles predating it keep their limits. ``reset_to_firmware`` is
    the other: true adds a first step that makes the firmware reselect its
    own power table, and it is one-shot -- only a caller that has just
    watched the checkbox go off sets it.
    """
    caps = caps or {}
    plan = []
    # First, and only when the caller asks for it: hand the limits back to
    # the firmware. See THROTTLE_POLICY_PATH -- ryzenadj has no reset, so
    # unticking the checkbox has to make the firmware reselect its own power
    # table or the last limits this app wrote keep running until a reboot.
    #
    # ``reset_to_firmware`` is opt-in and one-shot, set by the CPU page on
    # the tick-to-untick transition and by nothing else. It is deliberately
    # NOT derived from ``limits_enabled`` being false: the enforcer builds
    # this plan every 60 seconds, and deriving it would poke the EC -- and
    # drop the fan curves with it -- once a minute for as long as a profile
    # had its limits off.
    #
    # Before the cpufreq steps, not after. The EC reselecting its table can
    # take the energy preference with it, so the EPP and clock writes below
    # go last and leave the governor where the profile wants it, which is
    # the half of "off" that stays this app's business.
    if values.get("reset_to_firmware") and caps.get("fw_power_reset"):
        plan.append(("fwreset", ("cpufwlimits",)))
    # ``limits_enabled`` is the CPU page's checkbox, and it gates the whole
    # ryzenadj call: the helper's ``cpu`` action takes the four limits and
    # the Curve Optimizer offset as one set, so there is no way to send part
    # of it. Off means this profile leaves the chip's power limits to the
    # firmware, and every caller of this plan -- the page, the enforcer's
    # 60-second pass, the tray apply and the hotkey cycler -- stops writing
    # them, which is the only way "off" can mean anything: a limit the page
    # skips and the enforcer re-asserts a minute later is not off.
    #
    # Missing means on, and so does a null: profiles written before the
    # checkbox existed have no such key, and an upgrade must not silently
    # stop applying their limits. Only an explicit false turns it off.
    limits_enabled = values.get("limits_enabled")
    if limits_enabled is None:
        limits_enabled = True
    if caps.get("ryzenadj") and limits_enabled and all(
            key in values for key in ("stapm", "fast", "slow", "temp")):
        plan.append(("limits", ("cpu", values["stapm"], values["fast"],
                                values["slow"], values["temp"],
                                values.get("coall", 0))))
    # Intel: the same "limits" step, a different call. caps["cpu_power_limits"]
    # only ever reads "ppt"/"rapl" here -- it resolves to "ryzenadj" first
    # whenever that backend is available, so this branch is never reached on
    # an AMD machine and the block above is untouched. Same step name as the
    # ryzenadj branch, on purpose: CPU_APPLY_STEPS, STEP_ROWS, STEP_SAVES and
    # STEP_LABELS all key on "limits" and need no branch of their own for it.
    #
    # pl1/pl2 rather than stapm/fast/slow/temp -- Intel has no equivalent of
    # the fast/slow windows or the temperature target (that is an SMU
    # setting), so only the two watts values are sent.
    elif (caps.get("cpu_power_limits") in ("ppt", "rapl") and limits_enabled
          and all(key in values for key in ("pl1", "pl2"))):
        plan.append(("limits", ("cpuppt", values["pl1"], values["pl2"])))
    if "boost" in values and caps.get("cpu_boost"):
        plan.append(("boost", ("cpuboost", 1 if values["boost"] else 0)))
    if values.get("epp") and caps.get("cpu_epp"):
        plan.append(("epp", ("cpuepp", values["epp"])))
    # Last, after boost. 0 means "no ceiling" and still has to be written, or
    # a cap from a previous profile survives the switch.
    #
    # Skipped entirely when turbo boost is off: with boost off every core is
    # already pinned at its base clock, so the ceiling is not a limit this
    # profile is applying and the CPU page greys the row to say so. Nothing
    # stale is left behind by skipping it -- the boost write above is what
    # pins the cores, and it takes scaling_max_freq back to the hardware's
    # own value on every policy as it goes.
    #
    # A missing ``boost`` key means the caller has no opinion on boost, not
    # that boost is off, so the ceiling is still written for it: only an
    # explicit false drops the step. So does a machine with no boost control
    # -- nothing pinned those cores at base clock there, whatever the profile
    # happens to hold, so the ceiling is the only thing capping them.
    boost_on = True
    if caps.get("cpu_boost") and "boost" in values:
        boost_on = bool(values["boost"])
    if "max_freq" in values and caps.get("cpu_clock") and boost_on:
        plan.append(("clock", ("cpuclock", values["max_freq"] or "max")))
    # The floor, after the ceiling. Same cpufreq policies, same "0 means no
    # limit and still has to be written" rule.
    #
    # This was in the tree once, taken out, and is back deliberately, so the
    # measurement that took it out is worth writing down. A 2.4 GHz floor was
    # set and held on every sample through ten minutes of 4K GPU plus all-core
    # CPU load, and the cores ran at 2.0 GHz throughout: the package was
    # pinned at its 45 W STAPM limit, and scaling_min_freq is a floor on what
    # the kernel REQUESTS, not a grant of power the SMU has not got. That
    # reading is correct and it is also the one workload where no cpufreq
    # write of any kind can do anything -- the conclusion drawn from it, that
    # the control does nothing, was too broad.
    #
    # What the floor does do, on this machine, now: on amd-pstate it lands in
    # the CPPC request as min_perf, and outside the power-limited case the
    # hardware holds it. It is visible in sysfs without setting anything --
    # amd-pstate rests policy->min at amd_pstate_lowest_nonlinear_freq
    # (1 492 514 kHz here), and cores that are awake but unloaded read exactly
    # that, not the 421 798 kHz hardware minimum. Raising it raises the clock
    # those cores sit at, which is what the control is for: latency and
    # responsiveness on light and medium load, where the package has watts to
    # spare.
    #
    # The other half of why it looked dead is a plain bug, and it is fixed by
    # this line existing here rather than in four hand-copied applies: the
    # enforcer's 60-second pass writes cpuboost every cycle, a boost write
    # refreshes every policy and resets the floor with the ceiling, and the
    # pass then re-asserted the ceiling only. A floor set from the page
    # survived at most a minute.
    if "min_freq" in values and caps.get("cpu_clock"):
        plan.append(("minclock", ("cpuminclock", values["min_freq"] or "min")))
    return plan


# -- Memory ------------------------------------------------------------------

MEMINFO_PATH = "/proc/meminfo"


def read_memory(root=None):
    """``(used_mib, total_mib)`` of system RAM, or ``(None, None)``.

    Used is MemTotal minus MemAvailable, NOT MemTotal minus MemFree. MemFree
    on a healthy Linux is close to zero -- the kernel spends everything spare
    on page cache and buffers, and hands it back the instant something wants
    it -- so a "used" built from MemFree reads 95% on an idle machine and is
    the reason people keep reporting memory leaks that are not there.
    MemAvailable is the kernel's own estimate of what a new process could
    have without swapping, which is what a person means by memory that is
    free, so what is left over is what they mean by memory that is used.

    /proc/meminfo counts in kB, which it means as KiB. Answering in MiB keeps
    this the same unit as the VRAM reader, so the two rows can be formatted
    by one function and compared by eye.

    (None, None) rather than a partial answer if either figure is missing:
    both halves of "7.1 / 30.5 GiB" have to come from the same reading for
    the pair to mean anything. MemAvailable has been there since Linux 3.14
    (2014), so in practice this is the unreadable-file case."""
    text = read_file(_under(root, MEMINFO_PATH))
    if text is None:
        return None, None
    found = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if name in ("MemTotal", "MemAvailable"):
            words = rest.split()
            try:
                found[name] = int(words[0])
            except (IndexError, ValueError):
                return None, None
    total, available = found.get("MemTotal"), found.get("MemAvailable")
    if total is None or available is None:
        return None, None
    # MemAvailable is an estimate and is free to come out above MemTotal on a
    # machine with a lot of reclaimable cache; a negative "used" would draw a
    # row that reads as broken.
    return max(0, total - available) / 1024, total / 1024


# -- GPU ---------------------------------------------------------------------

def read_nvidia_query(fields, timeout=5):
    """The named ``--query-gpu`` fields as floats, in order, each None if the
    card had no number for it.

    One nvidia-smi call per group of fields, because each invocation costs a
    couple of hundred milliseconds and the callers run on a 2-second timer.
    Every failure mode -- no driver, no binary, card powered down under
    supergfxctl, '[N/A]' where a number should be -- lands on None rather
    than an exception, since a laptop with the dGPU asleep is a normal state
    and not a reason for the overview to stop updating."""
    blanks = tuple(None for _ in fields)
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=" + ",".join(fields),
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout)
    except Exception:
        return blanks
    if result.returncode != 0 or not result.stdout.strip():
        return blanks
    # Multi-GPU machines print one line per card; the first is the one this
    # app drives.
    columns = result.stdout.strip().splitlines()[0].split(",")
    out = []
    # Padded, so a card that answers with fewer columns than were asked for
    # gives None for the missing ones instead of an IndexError.
    for column in (columns + [""] * len(fields))[:len(fields)]:
        try:
            out.append(float(column.strip()))
        except ValueError:
            out.append(None)
    return tuple(out)


def read_nvidia_stats(timeout=5):
    """(temp_c, power_w) for the NVIDIA card, either of which may be None."""
    return read_nvidia_query(("temperature.gpu", "power.draw"),
                             timeout=timeout)


PCI_DEVICES_DIR = "/sys/bus/pci/devices"
NVIDIA_PCI_VENDOR = "0x10de"


def nvidia_pci_path(root=None):
    """The sysfs directory of the NVIDIA card, or None.

    Found by vendor id rather than a hardcoded 0000:01:00.0, which is this
    laptop's address and nobody else's."""
    for path in sorted(glob.glob(_under(root, PCI_DEVICES_DIR) + "/*")):
        try:
            with open(os.path.join(path, "vendor")) as f:
                if f.read().strip().lower() != NVIDIA_PCI_VENDOR:
                    continue
        except OSError:
            continue
        if os.path.exists(os.path.join(path, "power", "runtime_status")):
            return path
    return None


def dgpu_is_suspended(root=None):
    """True when the NVIDIA card is runtime-suspended, False when awake.

    None when it cannot be told -- no card, no runtime PM, unreadable.

    This is read from sysfs rather than asked of nvidia-smi on purpose.
    Running nvidia-smi *wakes the card to answer*, so a page that polls it
    every two seconds holds the dGPU awake for as long as it is open and the
    card is never seen idle -- which is both the wrong reading and a real
    cost in battery on a hybrid machine."""
    path = nvidia_pci_path(root)
    if not path:
        return None
    try:
        with open(os.path.join(path, "power", "runtime_status")) as f:
            status = f.read().strip().lower()
    except OSError:
        return None
    if status == "suspended":
        return True
    if status == "active":
        return False
    return None


def read_vram(timeout=5):
    """(used_mib, total_mib) of the NVIDIA card's own memory, or (None, None).

    Its own call rather than two more columns on read_nvidia_stats: that one
    has three callers and two of them -- the GPU page's readout and the
    keyboard's GPU-temperature colour -- want a temperature and nothing
    else, and would then be paying to parse memory figures they throw away.

    MiB, which is nvidia-smi's own unit here; the page decides what to show
    it in."""
    return read_nvidia_query(("memory.used", "memory.total"), timeout=timeout)


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
    mhz = detect_gpu_max_clock(timeout)
    if mhz:
        limits["clock_limit_max"] = mhz
    return limits


def detect_gpu_max_clock(timeout=5):
    """The card's top clock in MHz, or None when it could not be asked.

    Separate from detect_gpu_limits because the difference between "the card
    says 2100" and "the card did not answer" is exactly what that one throws
    away by falling back, and gpu_clock_limit_max needs it."""
    try:
        result = subprocess.run(["nvidia-smi", "-q", "-d", "CLOCK"],
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return parse_gpu_max_clock(result.stdout)
    except Exception:
        pass
    return None


# A real answer from the card, kept for the life of the process. The
# monotonic stamp beside it is when the last *failed* probe ran; see
# gpu_clock_limit_max.
_gpu_clock_limit_max = None
_gpu_clock_limit_failed_at = None
GPU_CLOCK_RETRY_S = 300


def gpu_clock_limit_max(timeout=5):
    """The card's own top lockable clock.

    For the three scripts that apply a profile with no window open -- the
    boot apply, the hotkey cycler and the enforcer. The window already has
    this in ``caps["gpu_limits"]``; they have nowhere to keep it, and each
    used to compare a stored ceiling against a hardcoded 3090 instead. That
    is this laptop's card and nothing else's: on a card that boosts higher,
    a profile saved at the top of the slider would come back as a real lock
    a little below maximum -- pinning the clock, which is the exact opposite
    of the "no ceiling" the top of the slider means.

    A real answer is cached for good: the enforcer applies profiles for the
    life of the session, and an nvidia-smi call per apply is real cost for
    something that cannot change while the machine is running.

    A *failed* probe is not cached for good, only rate-limited to one every
    GPU_CLOCK_RETRY_S. Caching the fallback was the bug: on hybrid graphics
    the card is routinely asleep at boot, so the first probe of the session
    -- the boot apply's -- fails, and the process that made it (the enforcer
    runs all session) then used a 3090's ceiling for every profile it applied
    afterwards, long after the card had woken up and could have answered. The
    rate limit keeps the original reason for caching the failure: a machine
    with no NVIDIA card at all still pays one failed exec per five minutes,
    not one per apply."""
    global _gpu_clock_limit_max, _gpu_clock_limit_failed_at
    now = time.monotonic()
    # The cache is read under the lock; the PROBE deliberately is not. An
    # nvidia-smi call takes a couple of hundred milliseconds and can be made
    # by any of the enforcer's three threads, and holding a lock across it
    # would stall the others -- including the log() calls one of them is in
    # the middle of. The cost is that two threads that miss together both
    # probe, which wastes one exec and settles on the same answer.
    with _state_lock:
        if _gpu_clock_limit_max is not None:
            return _gpu_clock_limit_max
        if (_gpu_clock_limit_failed_at is not None
                and now - _gpu_clock_limit_failed_at < GPU_CLOCK_RETRY_S):
            return CLOCK_LIMIT_FALLBACK_MAX
    mhz = detect_gpu_max_clock(timeout)
    with _state_lock:
        if mhz:
            _gpu_clock_limit_max = mhz
            return mhz
        _gpu_clock_limit_failed_at = time.monotonic()
    return CLOCK_LIMIT_FALLBACK_MAX


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


# The two variables nvidia-settings needs to find the user's display. Read
# from the session manager when they are missing from this process, which is
# what session_display_env is for.
DISPLAY_VARS = ("DISPLAY", "XAUTHORITY")


def session_display_env(timeout=5):
    """This process's environment, with DISPLAY/XAUTHORITY filled in from the
    user's session manager when they are missing.

    The boot apply is a systemd user service wanted by default.target, and
    it can start BEFORE the compositor imports those two variables into the
    user manager -- measured at three seconds early on this machine. A
    process's environment is fixed when it execs, so every retry inside that
    one process then ran without a display and nvidia-settings answered "The
    control display is undefined" to all of them. Asking the manager at the
    moment of the call gets the variables it has imported since.

    ``systemctl --user show-environment`` needs no display of its own -- it
    talks to the manager over its socket -- so this is safe to call from a
    service that may have started before there was one."""
    env = os.environ.copy()
    if all(env.get(name) for name in DISPLAY_VARS):
        return env
    try:
        result = subprocess.run(["systemctl", "--user", "show-environment"],
                                capture_output=True, text=True, timeout=timeout)
    except Exception:
        return env
    if result.returncode != 0:
        return env
    for line in result.stdout.splitlines():
        name, sep, value = line.partition("=")
        if sep and name in DISPLAY_VARS and not env.get(name):
            env[name] = value
    return env


def set_nvidia_clock_offset(kind, mhz, timeout=10):
    """Apply one clock offset, returning ``(ok, message)`` like run_helper."""
    # Retried rather than checked once: the unit files order the enforcer
    # and the boot apply after graphical-session.target, but reaching that
    # target only means the compositor's jobs are done, not that it has
    # finished exporting DISPLAY into the user manager's environment --
    # that import is a separate step and can still land a moment later.
    # Three tries over three seconds matches the window the ordering fix's
    # own measurement (three seconds early) left uncovered.
    env = session_display_env()
    for _ in range(2):
        if env.get("DISPLAY"):
            break
        time.sleep(1)
        env = session_display_env()
    if not env.get("DISPLAY"):
        # Said plainly rather than left to nvidia-settings, whose own answer
        # ("The control display is undefined") reads like a broken driver
        # rather than a graphical session that is not up yet.
        return False, ("no graphical session yet -- nvidia-settings needs "
                       "DISPLAY, which the session had not published")
    try:
        result = subprocess.run(nvidia_settings_args(kind, mhz), env=env,
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


# -- CPU power limits on Intel (PL1/PL2 via asus-wmi, RAPL fallback) --------
#
# ryzenadj's four limits and the Curve Optimizer are AMD-only -- they talk to
# the Ryzen SMU mailbox and nothing else has one. An Intel ASUS laptop's
# nearest equivalent lives in the SAME platform driver as the two knobs
# above: asus-nb-wmi creates ppt_pl1_spl/ppt_pl2_sppt (plus ppt_fppt and
# ppt_apu_sppt, not used here) whenever the firmware answers for that WMI
# device id -- the same firmware path G-Helper's "PL1 (CPU sustained)" and
# "PL2 (CPU long boost)" sliders drive on Windows. The file existing IS the
# capability, exactly as with nv_dynamic_boost/nv_temp_target.
#
# Two cautions, both found probing an AMD machine that happens to have these
# nodes too (see docs/INTEL-SUPPORT-PLAN.txt): the driver's show() returns
# its own cached value, not a firmware read, so read back is a starting hint
# at best and the profile stays the source of truth, exactly as for
# ryzenadj; and where the ppt_* nodes are simply absent, RAPL's
# constraint_*_power_limit_uw is the fallback, and ASUS firmware frequently
# locks those, so every write through them has to be verified by reading the
# value back rather than trusted on a zero exit.
PPT_PL1_PATH = f"{ASUS_WMI_DIR}/ppt_pl1_spl"
PPT_PL2_PATH = f"{ASUS_WMI_DIR}/ppt_pl2_sppt"

# The firmware clamps beyond this anyway; the helper range-checks
# independently of what asus-wmi's own kernel-side NVIDIA_BOOST_MIN/MAX-style
# constants happen to be, on the same "never trust one side alone" grounds as
# every other range in this file.
PL_MIN_W, PL_MAX_W = 5, 150


def read_ppt_pl1(root=None):
    """PL1 (sustained package power) the firmware is holding, watts, or None.

    Same shape as read_nv_dynamic_boost -- a cached kernel value, clamped to
    the range the helper accepts."""
    return _read_clamped(PPT_PL1_PATH, PL_MIN_W, PL_MAX_W, root)


def read_ppt_pl2(root=None):
    """PL2 (short-term boost power) the firmware is holding, watts, or None."""
    return _read_clamped(PPT_PL2_PATH, PL_MIN_W, PL_MAX_W, root)


def find_rapl_constraints(root=None):
    """``(pl1_path, pl2_path)`` of the package zone's RAPL constraint files,
    each None if that constraint does not exist.

    RAPL numbers its constraints by index, not by name -- constraint_0 is
    conventionally the long-term (PL1-equivalent) limit and constraint_1 the
    short-term (PL2-equivalent) one, and that is the order every RAPL tool
    (turbostat, powercap-utils) assumes. Built on find_rapl_package, which
    already knows how to tell the top-level package zone from a core/uncore
    subzone."""
    base = find_rapl_package(root=root)
    if not base:
        return None, None
    pl1 = os.path.join(base, "constraint_0_power_limit_uw")
    pl2 = os.path.join(base, "constraint_1_power_limit_uw")
    return (pl1 if os.path.exists(pl1) else None,
            pl2 if os.path.exists(pl2) else None)


def cpu_power_limits_backend(root=None, ryzenadj=None, cpu_ppt=None,
                             cpu_rapl_limits=None):
    """Which CPU power-limit backend this machine has: "ryzenadj", "ppt",
    "rapl" or None -- the single priority order cpu_apply_plan's "limits"
    step branches on.

    detect_capabilities() has already answered the three keyword questions
    by the time it needs this, and passes them in rather than have this
    re-probe the same paths a second time. rogcontrol-apply.py,
    rogcontrol-cycle-profile.py and rogcontrol-enforcer.py have not run a
    full detect_capabilities() at all -- that also probes nvidia-smi,
    rogauracore and the keyboard controller, which cost a subprocess or a USB
    round trip these scripts have no other reason to pay -- so for them the
    three default to None and are computed here instead, exactly as
    caps["ryzenadj"] itself is a direct cpu_is_amd()-plus-have_cmd() check in
    every one of those scripts' own ALL_CPU_CAPS rather than a full probe.

    ryzenadj wins whenever it is there, which is what keeps the AMD path
    bit-for-bit unchanged; "ppt" next, since it is the real firmware knob and
    RAPL is explicitly the fallback for a model whose firmware locks its RAPL
    constraints; None means this machine gets no power-limit control at all
    and the CPU page says so instead of offering one."""
    if ryzenadj is None:
        ryzenadj = (cpu_is_amd(root=root)
                   and (have_cmd("ryzenadj")
                        or os.path.exists("/usr/local/bin/ryzenadj")))
    if ryzenadj:
        return "ryzenadj"
    if cpu_ppt is None:
        cpu_ppt = (os.path.exists(_under(root, PPT_PL1_PATH))
                  and os.path.exists(_under(root, PPT_PL2_PATH)))
    if cpu_ppt:
        return "ppt"
    if cpu_rapl_limits is None:
        pl1, pl2 = find_rapl_constraints(root=root)
        cpu_rapl_limits = bool(pl1 and pl2)
    if cpu_rapl_limits:
        return "rapl"
    return None


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


# Panel overdrive, on the same platform device and here for the same reason
# the chime is. It shortens the display's pixel response by driving each
# transition past its target voltage and letting it settle back, so fast
# motion smears less. That is a fact about the screen and not about how hard
# the machine is being driven, so it must not change when a profile does --
# and it is a trade rather than an improvement: less smearing costs some
# overshoot, which on the panels that show it looks like a pale ghost
# leading a moving edge. Whether that trade is worth taking is the user's
# call, which is why this is a switch they own rather than something a
# profile decides for them.
PANEL_OD_PATH = f"{ASUS_WMI_DIR}/panel_od"

# The firmware's own power-table selector, and the generic ACPI control that
# stands in for it on a machine without asus-wmi. Nothing here reads or
# writes them -- the write is the helper's ``cpufwlimits`` action, which
# re-states whichever value is already in one of these to make the firmware
# reselect its power table. These two paths exist here only so
# detect_capabilities can answer whether that action has anything to write
# to, and so the two copies of the path (helper, capability probe) sit next
# to a single explanation of what they are for.
#
# Why the app needs this at all: ryzenadj has no reset. A power limit it has
# written stays in the SMU until the machine reboots, so a profile that just
# stops writing its limits leaves the previous ones running -- and the CPU
# page's "Apply power limits" checkbox would be a switch with nothing behind
# it. Telling the firmware to reselect its table is the one lever that puts
# the firmware's numbers back without a reboot.
THROTTLE_POLICY_PATH = f"{ASUS_WMI_DIR}/throttle_thermal_policy"
PLATFORM_PROFILE_PATH = "/sys/firmware/acpi/platform_profile"


def read_panel_od(root=None):
    """1 if the panel is being overdriven, 0 if it is not, None if this
    machine has no such control.

    Three answers and no guessing, exactly as the chime above. The driver
    creates panel_od only on models whose firmware claims the feature, so
    its absence is common and is emphatically not "off": answering 0 there
    would leave a switch sitting in the off position, which reads as "this
    was tried and it is disabled" rather than "this machine cannot do it".
    Anything in the file that is not 0 or 1 is unavailable on the same
    terms -- a two-position switch has nowhere honest to put it."""
    val = read_int(_under(root, PANEL_OD_PATH))
    return val if val in (0, 1) else None


# -- Graphics mode (supergfxctl) ---------------------------------------------

SUPERGFXD_SERVICE = "supergfxd.service"


def read_supergfxd_state(timeout=5):
    """What supergfxd is doing, in the same shape as read_asusd_state.

    The difference from the asusd version is what it is FOR. asusd is a
    daemon this app would rather was not running; supergfxd is one it needs,
    so the interesting state here is "installed but not running" -- which is
    what the package leaves behind on a distribution that ships the unit
    without enabling it, and which looked from the window like the daemon
    was simply broken.

    Nothing here needs root and nothing here writes: three read-only queries
    plus a PATH lookup."""
    def ask(*args):
        try:
            result = subprocess.run(["systemctl", *args],
                                    capture_output=True, text=True,
                                    timeout=timeout)
        except Exception:
            return ""
        # Return code ignored for the same reason as read_asusd_state:
        # is-active exits non-zero for a stopped unit and is-enabled for a
        # disabled one, and the word on stdout is the answer either way.
        return result.stdout or ""

    return parse_service_state(
        unit_files=ask("list-unit-files", SUPERGFXD_SERVICE),
        is_active=ask("is-active", SUPERGFXD_SERVICE),
        is_enabled=ask("is-enabled", SUPERGFXD_SERVICE),
        binary_found=have_cmd("supergfxd") or have_cmd("supergfxctl"),
        service=SUPERGFXD_SERVICE)


def set_supergfxd_running(timeout=30):
    """Enable and start supergfxd, returning ``(ok, message)``.

    Through the privileged helper, which takes no argument and names the
    unit itself -- there is no route from here to systemctl with a unit name
    of anyone's choosing.

    There is deliberately no "stop supergfxd" counterpart. This app wants
    that daemon running: without it the graphics-mode picker cannot read or
    switch anything. Turning it off is not something the window should offer
    a button for, and a disable primitive in a passwordless helper is worth
    not having."""
    return run_helper("supergfxd_enable", timeout=timeout)


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
        result = subprocess.run(["supergfxctl", "-g"],
                                capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def dgpu_available(timeout=5):
    """False only when the mode is known and it is Integrated.

    Integrated powers the card down, so nvidia-smi cannot reach it -- every
    apply that touches the GPU logged an ERROR each enforcer cycle while
    sitting in that mode. Unknown (no supergfxctl, or it did not answer)
    returns True: a machine with no supergfxd is not one this ever guarded
    against, so it keeps applying as it always did."""
    return read_gpu_mode(timeout) != "Integrated"


def read_supported_gpu_modes(timeout=5):
    """The modes this machine can actually be switched to, or []."""
    try:
        result = subprocess.run(["supergfxctl", "-s"],
                                capture_output=True, text=True, timeout=timeout)
    except Exception:
        return []
    if result.returncode != 0:
        return []
    return parse_supergfx_modes(result.stdout)


# The one mode that lives on the far side of the hardware MUX. Switching
# into or out of it is a firmware change; the other two are not.
MUX_MODE = "AsusMuxDgpu"

GPU_MUX_PATH = ASUS_WMI_DIR + "/gpu_mux_mode"


def gpu_mux_is_dgpu(root=None):
    """True when the MUX has the panel wired to the discrete card.

    0 is discrete and 1 is Optimus in the asus-wmi ABI. None when the node
    is absent -- a machine with no MUX at all.

    Read from sysfs rather than taken from supergfxd's answer because the
    two can disagree, and when they do this one is the truth. supergfxd
    stores a mode in /etc/supergfxd.conf and re-applies it at every login;
    it accepted Integrated while the MUX was still on the discrete card and
    then spent every subsequent login trying to tear down the card driving
    the screen."""
    try:
        with open(_under(root, GPU_MUX_PATH)) as f:
            value = f.read().strip()
    except OSError:
        return None
    if value == "0":
        return True
    if value == "1":
        return False
    return None


def mode_change_needs_reboot(current, target, root=None):
    """True when the switch crosses the hardware MUX.

    The MUX is flipped by firmware at POST, so nothing a running system can
    do finishes the change. Measured here: supergfxd wrote gpu_mux_mode and
    reported success, the node went on reading the old value for the rest of
    the session, and the machine only came up in Hybrid after a reboot --
    which is why "log out to finish switching" was wrong advice for it.

    Integrated <-> Hybrid does not cross the MUX. That pair only toggles
    dgpu_disable, which takes effect without a restart, and G-Helper
    switches the same pair live on this hardware.

    ``current`` may be None -- supergfxd not answering yet, a switch made
    before the first sample landed. The MUX node answers instead, and it is
    the better source anyway: it is the thing being crossed."""
    if current is not None and current == target:
        return False
    if target == MUX_MODE:
        # Already on the discrete card is the one case that needs nothing.
        return gpu_mux_is_dgpu(root) is not True
    # Leaving the MUX mode. Trust the hardware over the daemon's name for it.
    on_mux = gpu_mux_is_dgpu(root)
    if on_mux is None:
        return current == MUX_MODE
    return on_mux


def mode_needs_hybrid_first(current, target, root=None):
    """True when this switch has to go through Hybrid to be safe.

    Integrated means "power the discrete card down". With the MUX wired to
    that card, the panel is on it, so carrying the request out kills the
    display -- and supergfxd stores the mode and re-applies it at every
    login, so the machine comes up, freezes, and does it again next time.
    Measured here: two boots lasting 53 and 11 seconds before the machine
    had to be forced off.

    The MUX has to move to Optimus first, which is a reboot, and only then
    can the card be switched off. So this pair is two steps and the app has
    to say so rather than hand the daemon a request that bricks the
    session."""
    if target != "Integrated":
        return False
    return gpu_mux_is_dgpu(root) is True


def _run_reboot(extra_args, timeout=10):
    try:
        result = subprocess.run(["systemctl", "reboot",
                                             *extra_args],
                                capture_output=True, text=True,
                                timeout=timeout)
    except Exception as e:
        return False, str(e)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "unknown error").strip()
    return True, ""


def reboot_system(timeout=10):
    """Reboot, returning ``(ok, message)``.

    Through systemctl rather than the privileged helper: logind lets the
    active local session reboot on its own, so this needs no sudoers rule
    and no password. Only ever reached from a dialog the user pressed.

    Asked politely first, then again ignoring inhibitors -- and only when
    the first refusal was an inhibitor. GNOME holds a permanent
    shutdown-block inhibitor for the session ("user session inhibited"),
    which systemctl honours, so the polite call fails on every GNOME desktop
    with:

        Call to Reboot failed: Operation denied due to active block inhibitor

    That is not a reason to leave the machine unrebooted: the user has
    already pressed a button that said the machine will restart and watched
    a countdown run out. GNOME's own restart button ends the session for the
    same result. The retry is deliberately narrow -- any other failure
    (permission, no logind) is reported rather than forced, so a genuine
    refusal is never overridden."""
    ok, message = _run_reboot([], timeout=timeout)
    if ok:
        return True, ""
    if "inhibitor" not in message.lower():
        return False, message
    ok, forced_message = _run_reboot(["-i"], timeout=timeout)
    return (True, "") if ok else (False, forced_message)


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


# -- Panel self-refresh (amdgpu boot parameter) ------------------------------
#
# PSR lets the display controller stop sending frames to a static screen and
# let the panel refresh itself from its own memory. It saves real battery, and
# on this hardware it is also the one thing standing between a working Hybrid
# graphics mode and a machine that freezes seconds after the login screen
# appears.
#
# Why it belongs in this app at all, when it is a kernel bug and not an ASUS
# knob: the freeze is only reachable in Hybrid and Integrated, because those
# are the modes where the internal panel hangs off the AMD iGPU. In
# AsusMuxDgpu the panel is wired to the NVIDIA card, amdgpu reads no PSR
# capability from it, and the faulty path never runs. So the setting that
# decides whether the graphics-mode picker on the GPU page is usable is this
# one -- which makes it this app's business even though nothing about it is
# ASUS firmware.
#
# Measured on an ROG Strix G16 G614PR, kernel 7.2.0-1-cachyos: three Hybrid
# boots, three hard freezes, each preceded by
#
#     WARNING: .../display/modules/power/power_psr.c:236
#     at mod_power_set_psr_event+0x2a1/0x310 [amdgpu]
#
# and three AsusMuxDgpu boots with no warning and no freeze. The frozen boots
# logged "sink PSR ver 3 DPCD caps 0x7a" for eDP-2; the clean ones logged
# "caps 0x0", which is the panel not being on the AMD side at all. One of the
# three froze at the GDM greeter, before any user session existed, which is
# also what rules this app out as the cause.
LIMINE_DEFAULT_PATH = "/etc/default/limine"

# The exact token the helper writes, spelled the same way in both places.
# 0x610 is DC_DISABLE_PSR | DC_DISABLE_PSR_SU | DC_DISABLE_REPLAY -- see the
# helper's psr action for why all three rather than PSR alone.
PSR_DISABLE_PARAM = "amdgpu.dcdebugmask=0x610"

PROC_CMDLINE_PATH = "/proc/cmdline"


def _read_text(path):
    """A whole file as text, or None when it cannot be read."""
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def read_psr_disabled_pending(root=None):
    """True when the bootloader config carries the PSR-off parameter.

    What the *next* boot will do, which is not necessarily what this one is
    doing -- see read_psr_disabled_live. None when the config cannot be read,
    which on a machine that is not running Limine is the ordinary case and not
    an error."""
    text = _read_text(_under(root, LIMINE_DEFAULT_PATH))
    if text is None:
        return None
    return PSR_DISABLE_PARAM in text


def read_psr_disabled_live(root=None):
    """True when the running kernel was given the PSR-off parameter.

    Read from /proc/cmdline rather than inferred from the config, because the
    two disagree for exactly as long as it takes to reboot -- and that gap is
    the thing the switch has to be able to tell the user about."""
    text = _read_text(_under(root, PROC_CMDLINE_PATH))
    if text is None:
        return None
    return PSR_DISABLE_PARAM in text.split()


def psr_foreign_dcdebugmask(root=None):
    """A dcdebugmask this app did not write, or None.

    Somebody else's debug mask is somebody else's setting: the helper refuses
    to touch it, and the switch says so rather than offering an action that
    will come back refused."""
    text = _read_text(_under(root, LIMINE_DEFAULT_PATH))
    if text is None:
        return None
    for match in re.findall(r"amdgpu\.dcdebugmask=\S+", text):
        if match != PSR_DISABLE_PARAM:
            return match
    return None


def set_psr_disabled(disabled, timeout=120):
    """Turn PSR off (True) or back on (False) at the next boot.

    Through the helper, which does the whole regenerate-and-re-enroll dance
    and puts the config back if any part of it fails. The timeout is minutes
    rather than seconds because limine-update rebuilds boot entries and
    hashes them, which is not a thing that finishes in ten."""
    return run_helper("psr", 0 if disabled else 1, timeout=timeout)


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
                ["busctl", "--system", "get-property", name,
                 path, name, "ActiveProfile"],
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
    curves to do it. The result is ~10 seconds of fan writes for the profile
    the user chose, ~10 seconds for the one they did not, and the switch
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
            ["busctl", "--system", "set-property", service,
             path, service, "ActiveProfile", "s", str(mode)],
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



def set_profile_kbd_color(cfg, profile_name=None, caps=None, timeout=10):
    """Take the keyboard colour along with a profile switch.

    Returns ``(ok, message)``, or None when this switch has no keyboard
    colour to write -- the user is on another lighting mode, or the machine
    has no controllable one. None is the same "does not apply, and that is
    not a failure" that set_power_mode_for_profile returns, and callers
    treat it the same way.

    THIS SITS BESIDE set_power_mode_for_profile ON PURPOSE. A profile
    becoming current is not one event in one process: it happens in the
    window, in the tray's login/switch apply, in the hotkey cycler, and
    inside the enforcer when the OS power mode moves or the charger comes
    out. Only the first of those has a window open, and a keyboard that
    only changed colour while the app happened to be running would be a
    feature the user could not trust. So the repaint is a shared call made
    from every path that already sets the OS power mode, rather than a
    fifth hand-maintained copy of the same three lines -- which is exactly
    how the CPU apply came to silently drop the clock floor in three places
    out of four.

    The colour itself is decided in kbdcolor, which has no hardware in it;
    this is only the write."""
    args = kbdcolor.profile_color_args(cfg, profile_name, caps)
    if args is None:
        return None
    return run_helper(*args, timeout=timeout)

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


def clear_log(path=None):
    """Empty the log and drop its rotated backup. True on success.

    Under the same LOG_LOCK_PATH flock log()'s own rotation uses, so a line
    one of the five writers is mid-append on cannot land between this
    truncating the file and it being reopened -- the same hazard the
    rotation lock already exists to avoid. Truncated in place rather than
    removed: every writer opens LOG_PATH in append mode expecting it to be
    there, and a path that briefly does not exist is one more case they
    would all have to handle for no benefit."""
    path = LOG_PATH if path is None else path
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(LOG_LOCK_PATH, "a") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
            open(path, "w").close()
            try:
                os.remove(path + ".1")
            except OSError:
                pass
        return True
    except OSError:
        return False


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


# -- Fan boost ---------------------------------------------------------------
#
# A temporary flat override: every channel held at one percentage regardless
# of temperature, for a fixed time, then the active profile's own curve back.
#
# WHY THE STATE IS A FILE AND NOT A VARIABLE IN THE WINDOW.
#
# It used to be a GLib countdown inside the System page, and the page also
# stopped the enforcer so nothing would fight the flat curve. Both halves of
# the undo therefore lived in the window -- so closing the window during the
# hold destroyed the timer, and the machine was left with all three fans
# pinned at 85% and the enforcer switched off until the next login. Nothing
# said so, and nothing put it back.
#
# A deadline on disk fixes it by moving the ownership rather than by adding a
# second timer: the enforcer is already running, already re-asserts fan
# curves, and already outlives every window. While a boost is in force it
# pushes the flat curve instead of the profile's (so it maintains the boost
# rather than fighting it, which is why the enforcer no longer has to be
# stopped at all), and the moment the deadline passes it clears the file and
# the profile's real curve goes back on -- window open or not, and after a
# reboot too.
#
# Wall clock rather than time.monotonic(), which is the one thing that
# cannot be got wrong here: two processes have to read the same deadline,
# and monotonic clocks are per boot and not comparable between them.
FAN_BOOST_STATE_PATH = os.path.expanduser(
    "~/.local/share/rogcontrol/fan-boost")

# The temperatures the flat curve is written at. Eight points, strictly
# increasing, all carrying the same percentage -- which is what makes it flat
# regardless of what the EC is reading. Same shape as the calibration steps in
# pages/fans.py.
FAN_BOOST_TEMPS = (30, 40, 50, 55, 60, 65, 70, 90)


def fan_boost_curves(pct, channels=FAN_CHANNELS):
    """``{channel: [(temp, pct), ...]}`` holding every fan flat at ``pct``.

    In the config's own curve shape, so it can be handed straight to
    fancurve.curve_to_flat and to the same comparison every other curve goes
    through."""
    points = [[temp, int(pct)] for temp in FAN_BOOST_TEMPS]
    return {channel: [list(point) for point in points] for channel in channels}


def read_fan_boost(path=None):
    """The boost on record, or None when there is not one.

    ``{"until": <epoch seconds>, "pct": 85, "profile": "Quiet"}``. Returns
    the record whether or not it has expired -- telling those two apart is
    :func:`fan_boost_active`, because the caller that has to notice a boost
    ENDING needs to see the expired record rather than nothing at all.

    Anything unreadable or unparseable is None: a corrupt state file must
    read as "no boost", never as a boost with no deadline."""
    try:
        with open(FAN_BOOST_STATE_PATH if path is None else path) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    try:
        until = float(state.get("until"))
        pct = int(state.get("pct"))
    except (TypeError, ValueError):
        return None
    if not 0 <= pct <= 100:
        return None
    return {"until": until, "pct": pct, "profile": state.get("profile")}


def fan_boost_active(state, now=None):
    """True while ``state``'s deadline is still ahead of us."""
    if not state:
        return False
    return state["until"] > (time.time() if now is None else now)


def write_fan_boost(pct, seconds, profile_name=None, path=None):
    """Record a boost that ends ``seconds`` from now. Returns the state.

    Written BEFORE the flat curve reaches the hardware, deliberately: the
    record is what stops the enforcer from putting the profile's curve back
    on its next pass, and what puts the profile's curve back if this process
    dies in the middle of the write. A boost the hardware took but nothing
    recorded is exactly the stuck-fans case this file exists to prevent.

    Temp file plus rename, like every other state this app keeps, so a reader
    landing mid-write sees the old record or the new one and never half of
    one."""
    path = FAN_BOOST_STATE_PATH if path is None else path
    state = {"until": time.time() + float(seconds), "pct": int(pct),
             "profile": profile_name}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except OSError as e:
        log(f"could not record the fan boost: {e}", "WARN", dedupe_key="boost")
    return state


def clear_fan_boost(path=None):
    """Forget the boost. Missing is success -- the other process got there
    first, which is the ordinary race between the window and the enforcer
    both noticing the same deadline pass."""
    try:
        os.remove(FAN_BOOST_STATE_PATH if path is None else path)
    except OSError:
        pass


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


# -- Battery health ----------------------------------------------------------

# The two families the power_supply class offers, in the order they are tried.
# A driver exposes one or the other and never both: charge_* is a coulomb
# count in µAh, energy_* is watt-hours in µWh. Which one a machine gets is a
# property of the driver rather than of the cell -- the same battery reads
# charge_* under one platform driver and energy_* under plain ACPI, and this
# machine's BAT0 is energy_* -- so both have to be handled and neither can be
# assumed present.
#
# The divisor turns the kernel's micro-units into the unit batteries are
# actually quoted in: µAh -> mAh, µWh -> Wh. Wh deliberately, not mWh: a
# laptop battery is "90 Wh" on its own label and on every spec sheet, and
# "90001 mWh" beside it would have to be read twice.
BATTERY_HEALTH_FAMILIES = (
    ("charge_full_design", "charge_full", 1000.0, "mAh"),
    ("energy_full_design", "energy_full", 1000000.0, "Wh"),
)


def battery_health(design, full):
    """``full`` as a percentage of ``design``, or None if it cannot be said.

    Split out from the file reading so the arithmetic -- which is where the
    divide-by-zero lives -- can be tested without a battery, the same way
    the fan-curve and meminfo maths are.

    A design capacity of zero is the case that matters. Firmware that has
    lost its battery-info block reports design 0 rather than dropping the
    file, and dividing by it would take a whole page refresh down. Zero is
    "unknown", so the answer is None, not 0% and not infinity.

    NOT clamped to 100. A cell that has not yet been through a full cycle
    routinely reports full above design -- 101-104% is ordinary on a new
    machine, and it is the firmware's own learned figure, not an error.
    Clamping would silently rewrite a true reading into a suspiciously
    round one, and a user comparing this against the same numbers from
    upower or the vendor tool would think the app was broken. The page
    shows whatever comes out; see the note it puts on the row above 100.

    A negative or missing half is refused outright: there is no reading a
    negative capacity could be a rounding artefact of."""
    if design is None or full is None:
        return None
    if design <= 0 or full < 0:
        return None
    return full / design * 100.0


def read_battery_health(root=None):
    """Wear figures for the first battery that reports them, or None.

    ``{"percent": 92.3, "full": 83.0, "design": 90.0, "unit": "Wh",
    "cycles": None}`` -- percent from battery_health, full and design
    converted out of the kernel's micro-units, and cycles only when the
    firmware genuinely counts them.

    None for the whole thing rather than a dict of Nones: a battery with
    neither family present -- and a desktop with no battery at all -- has
    nothing to say here, and the caller's job is then to hide the row
    rather than draw one full of dashes that looks like a failed read.

    The families are tried per battery rather than globally, and the first
    battery holding a complete pair wins. A machine with two packs can have
    the second be the only one whose driver fills these in (an empty or
    absent bay still gets a power_supply node), so stopping at the first
    battery entry the way read_battery does would report no health at all
    on hardware that has it. Skipping to the next entry costs nothing and
    is the only behaviour that is right on both one-battery and two-battery
    machines.

    ``cycle_count`` is reported as None when it reads 0. The ACPI spec has
    firmware write 0 for "this battery does not track cycles", which is by
    far the common case -- BAT0 on this machine says 0 with hundreds of
    cycles on it -- and a battery that really is on cycle zero has nothing
    worth showing either. Either way "0 cycles" would be a claim the
    hardware is not making, so it is treated as no answer at all."""
    base = _under(root, POWER_SUPPLY_DIR)
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return None
    for entry in entries:
        path = os.path.join(base, entry)
        if read_file(os.path.join(path, "type")) != "Battery":
            continue
        for design_file, full_file, divisor, unit in BATTERY_HEALTH_FAMILIES:
            design = read_int(os.path.join(path, design_file))
            full = read_int(os.path.join(path, full_file))
            percent = battery_health(design, full)
            if percent is None:
                continue
            cycles = read_int(os.path.join(path, "cycle_count"))
            return {
                "percent": percent,
                "full": full / divisor,
                "design": design / divisor,
                "unit": unit,
                "cycles": cycles if cycles and cycles > 0 else None,
            }
    return None


def is_ac_connected(root=None):
    """True on external power, False on battery, None if no supply is found.

    A thin wrapper over read_power_source -- kept because most callers only
    ever want the bool and the two-value tuple would just be unpacked and
    the kind discarded at every one of those call sites."""
    return read_power_source(root)[0]


def read_power_source(root=None):
    """(connected, kind) for the charger, or (None, None) if no supply is
    found at all.

    ``connected`` is True on external power, False on battery -- same
    semantics as the old is_ac_connected. ``kind`` is "mains" for the
    barrel-jack supply or "usb" for a USB-C PD charger, kernel-exposed as
    type "USB"; it is None whenever connected is not True, since there is
    nothing meaningful to name once nothing is delivering power.

    Covers both types in one pass because a laptop plugged into a type-C
    charger has no "Mains" entry online at all -- checking only Mains would
    read a USB-C-only charge as running on battery.

    More than one entry can read online=1 at once. Confirmed live on this
    hardware: the barrel jack's ADP0 stuck at online=1 with the barrel cable
    genuinely unplugged -- and ADP0 exposes no status/current file at all,
    on this machine or apparently ever, so there is nothing on its side to
    tell a stuck flag from a real one. A USB-C entry reporting
    status="Charging" is corroborated by telemetry ADP0 simply does not
    have, so it wins over a bare, uncorroborated online flag. Only used as
    a tiebreak: with one online entry, or with no USB entry actually
    claiming to charge, first-found order is unchanged from before."""
    base = _under(root, POWER_SUPPLY_DIR)
    found = False
    online_supplies = []
    try:
        for entry in sorted(os.listdir(base)):
            path = os.path.join(base, entry)
            supply_type = read_file(os.path.join(path, "type"))
            if supply_type not in ("Mains", "USB"):
                continue
            val = read_file(os.path.join(path, "online"))
            if val is None:
                continue
            found = True
            if val == "1":
                online_supplies.append((supply_type, path))
    except OSError:
        pass

    if not online_supplies:
        return (False, None) if found else (None, None)

    for supply_type, path in online_supplies:
        if (supply_type == "USB"
                and read_file(os.path.join(path, "status")) == "Charging"):
            return True, "usb"

    supply_type, _path = online_supplies[0]
    return True, "mains" if supply_type == "Mains" else "usb"


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


# What to say when a live-colour mode has no reading to colour from. Keyed by
# mode so the window, the enforcer and the hotkey cycler word the same
# failure the same way -- they each had their own sentence for it.
LIVE_READING_MISSING = {
    "Battery Level": "no battery found on this machine",
    "CPU Temp Color": "no CPU temperature reading yet",
    "GPU Temp Color": "no GPU temperature reading yet",
}


def read_live_color_reading(mode):
    """The sensor reading a live-colour mode needs, shaped for
    :func:`kbdcolor.live_restore_color`, or None for a mode that has none.

    Looked up by mode rather than read all at once on purpose: a GPU query is
    a subprocess call, and taking it for a user on Battery Level would cost
    that exec on every charger flash and every hotkey press for a number
    nothing reads."""
    if mode == "Battery Level":
        return read_battery()
    if mode == "CPU Temp Color":
        return read_cpu_temp()
    if mode == "GPU Temp Color":
        return read_nvidia_stats()[0]
    return None


def read_live_color(mode):
    """``(colour, reason)`` for a mode whose colour comes from a reading.

    Exactly one of the two is set. This pairing -- take the reading, map it
    to a colour -- existed three times over: the Keyboard page, the enforcer's
    charger flash and the keyboard-mode hotkey each had their own chain of
    ``if mode ==`` branches, so a mode added to one was silently unsupported
    in the others. The mapping itself stays in kbdcolor, which is pure and
    cannot read a sensor; this is the I/O half."""
    reading = read_live_color_reading(mode)
    color = kbdcolor.live_restore_color(mode, reading)
    if color is None:
        return None, LIVE_READING_MISSING.get(
            mode, f"{mode} has no reading to colour from")
    return color, None


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

def have_cmd(name, timeout=10):
    """True when ``name`` is on the PATH of the machine this app drives.

    A timeout so a capability probe cannot hang the window's startup. A probe
    that cannot answer reports "absent", which is the same answer it gave
    before this could fail at all."""
    try:
        return subprocess.run(["sh", "-c", f"command -v {name}"],
                              capture_output=True,
                              timeout=timeout).returncode == 0
    except Exception:
        return False


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
    # The same sensor names read_cpu_temp falls back through, in the same
    # order. Asking only about k10temp put an Intel machine in the state
    # where the page showed a live temperature and a note underneath it
    # saying no sensor had been found.
    caps["cpu_temp"] = any(find_hwmon_by_name(name, root=root) is not None
                           for name in CPU_TEMP_HWMON_NAMES)
    caps["pkg_power"] = (find_rapl_package(root=root) is not None
                          or find_hwmon_by_name("amdgpu", root=root) is not None)
    caps["nv_temp_target"] = os.path.exists(_under(root, f"{ASUS_WMI_DIR}/nv_temp_target"))
    caps["nv_dynamic_boost"] = os.path.exists(_under(root, f"{ASUS_WMI_DIR}/nv_dynamic_boost"))
    # Presence, not the value: a machine whose firmware has the chime turned
    # off still has the control, and gating on the reading would make the
    # switch disappear the moment it was switched off.
    caps["boot_sound"] = os.path.exists(_under(root, BOOT_SOUND_PATH))
    # Presence again, and for the same reason: a panel whose overdrive is
    # currently off still has the control, so gating on the value would take
    # the switch away the moment it was used to switch overdrive off.
    caps["panel_od"] = os.path.exists(_under(root, PANEL_OD_PATH))
    # Whether there is anything for the helper's cpufwlimits action to write,
    # which is what hands the CPU power limits back to the firmware when the
    # CPU page's checkbox is unticked. Either control will do; the helper
    # prefers the asus-wmi one and falls back to the ACPI one in the same
    # order this does.
    caps["fw_power_reset"] = (
        os.path.exists(_under(root, THROTTLE_POLICY_PATH))
        or os.path.exists(_under(root, PLATFORM_PROFILE_PATH)))
    # Four separate questions, all of which have to answer yes: there has to
    # be an AMD display driver for the parameter to mean anything, a Limine
    # config to put it in, and both Limine tools to regenerate and re-enroll
    # what was changed. Anything less and the switch would offer an action
    # that cannot finish -- and this is the one action in the app whose
    # half-finished state costs a boot rather than a setting.
    caps["psr_toggle"] = (
        os.path.exists(_under(root, "/sys/module/amdgpu"))
        and os.path.isfile(_under(root, LIMINE_DEFAULT_PATH))
        and have_cmd("limine-update")
        and have_cmd("limine-enroll-config"))
    caps["nvidia"] = have_cmd("nvidia-smi")
    # A separate question from nvidia-smi: the two clock offsets go through
    # nvidia-settings, which is its own package and is missing on plenty of
    # machines that have a working driver.
    caps["nvidia_settings"] = have_cmd("nvidia-settings")
    caps["supergfxctl"] = have_cmd("supergfxctl")
    caps["rogauracore"] = have_cmd("rogauracore")
    # Which vendor made the chip, for the page and for the gate below.
    caps["cpu_vendor"] = read_cpu_vendor(root=root)
    # Two questions, and both have to answer yes. The binary being installed
    # is not enough: the installer used to put ryzenadj on any Arch machine
    # it ran on, so an Intel laptop ended up with the power limits and the
    # Curve Optimizer on screen, driving a tool that only speaks to a Ryzen
    # SMU. Vendor first, so the page hides those controls rather than
    # offering an Apply that cannot work.
    caps["ryzenadj"] = (cpu_is_amd(root=root)
                        and (have_cmd("ryzenadj")
                             or os.path.exists("/usr/local/bin/ryzenadj")))
    # The Intel equivalent of ryzenadj's four limits: presence of the
    # asus-wmi PL1/PL2 nodes, or, failing that, RAPL's own constraint files.
    # Either is a real question on AMD too -- the nodes exist there as well
    # -- but caps["cpu_power_limits"] below is what callers actually branch
    # on, and it resolves to "ryzenadj" first whenever that is available, so
    # these two never cause a write on an AMD machine.
    caps["cpu_ppt"] = (os.path.exists(_under(root, PPT_PL1_PATH))
                       and os.path.exists(_under(root, PPT_PL2_PATH)))
    rapl_pl1, rapl_pl2 = find_rapl_constraints(root=root)
    caps["cpu_rapl_limits"] = bool(rapl_pl1 and rapl_pl2)
    # The one backend cpu_apply_plan actually reads -- see
    # cpu_power_limits_backend for the priority order.
    caps["cpu_power_limits"] = cpu_power_limits_backend(
        root=root, ryzenadj=caps["ryzenadj"], cpu_ppt=caps["cpu_ppt"],
        cpu_rapl_limits=caps["cpu_rapl_limits"])
    caps["cpu_boost"] = (
        os.path.exists(_under(root, "/sys/devices/system/cpu/cpufreq/boost"))
        or bool(glob.glob(_under(root, CPUFREQ_GLOB) + "/boost"))
        or os.path.exists(_under(root, INTEL_NO_TURBO_PATH)))
    # The preference names differ between amd-pstate and intel_pstate, so read
    # them from the kernel instead of hardcoding a list. "custom" is dropped:
    # it needs a raw 0-255 value written elsewhere, so offering it in a
    # dropdown would only produce failures.
    caps["cpu_epp"] = [p for p in read_epp_preferences(root=root) if p != "custom"]
    caps["cpu_clock"] = read_cpu_clock_range(root=root)
    # The bottom of the clock-floor slider, and what "no floor" writes. Its
    # own reading rather than cpu_clock[0] -- see read_cpu_clock_floor_default.
    caps["cpu_clock_floor"] = read_cpu_clock_floor_default(root=root)
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


# -- Hardware report -----------------------------------------------------
#
# What an Intel tester sends back. There is no access to Intel hardware for
# this release -- see docs/INTEL-SUPPORT-PLAN.txt -- so every Intel code
# path is gated on a sysfs node actually existing, and this report is how a
# real tester's machine gets checked against that assumption at all. Fully
# read-only: nothing below writes to the hardware, and it is deliberately
# unfiltered -- a section transcribed in full is something the developer can
# read the raw values out of later; a summary would only be as good as the
# question this release thought to ask.


def _report_section(lines, title):
    lines.append("")
    lines.append(f"== {title} ==")


def _report_asus_wmi(lines, root):
    _report_section(lines, "asus-nb-wmi")
    base = _under(root, ASUS_WMI_DIR)
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        lines.append(f"{ASUS_WMI_DIR} does not exist on this machine")
        return
    # ppt_* first: they are the reason this report exists, and a tester
    # skimming should not have to hunt for them among boot_sound and
    # nv_dynamic_boost.
    entries.sort(key=lambda name: (not name.startswith("ppt_"), name))
    for name in entries:
        path = os.path.join(base, name)
        if not os.path.isfile(path):
            continue
        value = read_file(path)
        lines.append(f"{name} = {value!r}")


def _report_powercap(lines, root):
    _report_section(lines, "powercap")
    base = _under(root, POWERCAP_DIR)
    try:
        zones = sorted(os.listdir(base))
    except OSError:
        lines.append(f"{POWERCAP_DIR} does not exist on this machine")
        return
    for zone in zones:
        zone_path = os.path.join(base, zone)
        if not os.path.isdir(zone_path):
            continue
        name = read_file(os.path.join(zone_path, "name"))
        lines.append(f"{zone} (name={name!r})")
        for entry in sorted(os.listdir(zone_path)):
            if "constraint_" not in entry:
                continue
            path = os.path.join(zone_path, entry)
            value = read_file(path)
            writable = os.access(path, os.W_OK)
            lines.append(f"  {entry} = {value!r} (writable={writable})")


def _report_cpufreq(lines, root):
    _report_section(lines, "cpufreq")
    policies = sorted(glob.glob(_under(root, CPUFREQ_GLOB)))
    if not policies:
        lines.append("no cpufreq policies found")
        return
    p0 = policies[0]
    lines.append(f"scaling_driver = {read_file(os.path.join(p0, 'scaling_driver'))!r}")
    lines.append(f"cpuinfo_min_freq = {read_file(os.path.join(p0, 'cpuinfo_min_freq'))!r}")
    lines.append(f"cpuinfo_max_freq = {read_file(os.path.join(p0, 'cpuinfo_max_freq'))!r}")
    lines.append(f"amd_pstate_lowest_nonlinear_freq = "
                f"{read_file(os.path.join(p0, 'amd_pstate_lowest_nonlinear_freq'))!r}")
    lines.append(f"global boost node = "
                f"{os.path.exists(_under(root, '/sys/devices/system/cpu/cpufreq/boost'))}")
    lines.append(f"per-policy boost node = "
                f"{bool(glob.glob(os.path.join(p0, 'boost')))}")
    lines.append(f"intel_pstate/no_turbo = "
                f"{read_file(_under(root, INTEL_NO_TURBO_PATH))!r}")
    lines.append(f"energy_performance_preference = "
                f"{read_file(os.path.join(p0, 'energy_performance_preference'))!r}")
    prefs = read_epp_preferences(root=root)
    lines.append(f"energy_performance_available_preferences = {prefs!r}")


def _report_hwmon(lines, root):
    _report_section(lines, "hwmon")
    for name in CPU_TEMP_HWMON_NAMES:
        found = find_hwmon_by_name(name, root=root)
        lines.append(f"{name}: {'found at ' + found if found else 'not found'}")


def hardware_report_text(root=None):
    """Everything a hardware report contains, as one string.

    Read-only, and unfiltered on purpose -- see the module comment above.
    ``root`` re-bases the sysfs reads for testing, exactly as everywhere
    else in this file; asusd/supergfxd and the desktop session are asked for
    directly regardless, since neither has a meaningful re-based form."""
    lines = [
        "ROG Control hardware report",
        f"app version: {APP_VERSION}",
        f"kernel: {platform.release()}",
        f"machine: {platform.machine()}",
    ]
    os_release = read_file(_under(root, "/etc/os-release")) or ""
    pretty = ""
    for line in os_release.splitlines():
        if line.startswith("PRETTY_NAME="):
            pretty = line.partition("=")[2].strip().strip('"')
            break
    lines.append(f"distribution: {pretty or 'unknown'}")
    lines.append(f"desktop session: "
                 f"{os.environ.get('XDG_CURRENT_DESKTOP', 'unknown')} / "
                 f"{os.environ.get('XDG_SESSION_TYPE', 'unknown')}")

    _report_section(lines, "CPU")
    lines.append(f"vendor: {read_cpu_vendor(root=root)!r}")
    lines.append(f"name: {read_cpu_name(root=root)!r}")
    lines.append(f"logical cores: {os.cpu_count()}")

    _report_asus_wmi(lines, root)
    _report_powercap(lines, root)
    _report_cpufreq(lines, root)
    _report_hwmon(lines, root)

    _report_section(lines, "detect_capabilities()")
    lines.append(json.dumps(detect_capabilities(root=root), indent=2,
                            default=str, sort_keys=True))

    _report_section(lines, "asusd / supergfxd")
    lines.append(f"asusd: {read_asusd_state()}")
    lines.append(f"supergfxd: {read_supergfxd_state()}")

    lines.append("")
    return "\n".join(lines)


def _hardware_report_dir_candidates():
    """Where write_hardware_report tries to save, in order.

    xdg-user-dir first, since it is the one answer that reflects a Downloads
    folder the user actually renamed or relocated; $XDG_DOWNLOAD_DIR next for
    a session that sets it without the binary being installed; then the
    plain default, then $HOME, which always exists and is always writable by
    the user running this, so the chain always ends somewhere usable even on
    a system with no Downloads folder at all."""
    if have_cmd("xdg-user-dir"):
        try:
            result = subprocess.run(["xdg-user-dir", "DOWNLOAD"],
                                    capture_output=True, text=True, timeout=5)
            path = result.stdout.strip()
            if result.returncode == 0 and path:
                yield path
        except Exception:
            pass
    env = os.environ.get("XDG_DOWNLOAD_DIR")
    if env:
        yield os.path.expanduser(env)
    yield os.path.expanduser("~/Downloads")
    yield os.path.expanduser("~")


def hardware_report_path():
    """Where write_hardware_report will save, without writing anything.

    Named after the machine and the day, not the app version: a tester
    re-running this a second time the same day overwrites their own report
    rather than leaving a trail of near-identical files in Downloads, and a
    second day's report is worth keeping separate from the first."""
    hostname = platform.node() or "unknown-host"
    stamp = time.strftime("%Y-%m-%d")
    name = f"rogcontrol-hardware-report-{hostname}-{stamp}.txt"
    for candidate in _hardware_report_dir_candidates():
        try:
            os.makedirs(candidate, exist_ok=True)
        except OSError:
            continue
        if os.access(candidate, os.W_OK):
            return os.path.join(candidate, name)
    # Every candidate above failed outright (permissions, a read-only home
    # during some unusual session) -- $HOME one more time, unconditionally,
    # so this always returns a path rather than raising.
    return os.path.join(os.path.expanduser("~"), name)


def write_hardware_report(root=None):
    """Write hardware_report_text() to hardware_report_path() and return the
    path written.

    Callers print the path themselves rather than this function doing it --
    the CLI flag, the installer and the System page each want a different
    sentence around it."""
    path = hardware_report_path()
    with open(path, "w") as f:
        f.write(hardware_report_text(root=root))
    return path


# --- checking for and applying an update -------------------------------------
#
# install.sh needs sudo per step (each call can prompt) and expects to run
# from beside the rest of a release checkout -- the helper binary, the
# .service files, the icons -- not just the python package this app imports
# from. So "update" means: ask GitHub what the latest tagged release is,
# download the zip a human attached to that release, and open a terminal
# running the install.sh inside it. A subprocess with no TTY cannot supply
# the sudo password install.sh's own steps ask for, which is why this never
# tries to run install.sh directly.

GITHUB_REPO = "D0minatorX/rogcontrol"
GITHUB_LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest")
# The naming convention every release asset has followed so far (see the
# Rog-Control-V*.zip files this repo ships). Matched by prefix rather than
# taking whichever .zip happens to be first, so a release with more than one
# attachment (a source archive GitHub adds automatically, say) cannot grab
# the wrong one.
UPDATE_ASSET_PREFIX = "Rog-Control-V"
UPDATE_USER_AGENT = "rogcontrol-update-check"


def _version_tuple(version):
    """"v1.0.0.9" (or "1.0.0.9") -> (1, 0, 0, 9), for a tuple comparison.

    Stops at the first component that is not a plain integer rather than
    raising, so a tag with something unexpected on the end ("1.0.0.9-rc1")
    still compares on the numeric prefix instead of failing the whole
    check."""
    parts = []
    for part in version.lstrip("vV").split("."):
        try:
            parts.append(int(part))
        except ValueError:
            break
    return tuple(parts)


def check_for_update(timeout=10):
    """Ask GitHub for the latest release and compare it to APP_VERSION.

    Returns a dict -- ``{"available", "version", "download_url", "error"}``
    -- rather than raising: this runs on a worker thread with exactly one
    caller, which always wants something to show the user, not an exception
    to catch. ``error`` is set only for a request that failed outright; a
    release with nothing newer, or newer but with no matching asset, is not
    an error."""
    try:
        request = urllib.request.Request(
            GITHUB_LATEST_RELEASE_URL,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": UPDATE_USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        return {"available": False, "version": None, "download_url": None,
                "error": str(e)}
    tag = data.get("tag_name") or ""
    latest = _version_tuple(tag)
    if not latest or latest <= _version_tuple(APP_VERSION):
        return {"available": False, "version": None, "download_url": None,
                "error": None}
    download_url = None
    for asset in data.get("assets") or []:
        name = asset.get("name") or ""
        if name.startswith(UPDATE_ASSET_PREFIX) and name.endswith(".zip"):
            download_url = asset.get("browser_download_url")
            break
    return {"available": True, "version": tag.lstrip("vV"),
            "download_url": download_url, "error": None}


def download_and_stage_update(download_url, timeout=60):
    """Download the release zip and extract it. Returns the path to the
    extracted copy's install.sh.

    Raises on any failure -- network, a corrupt zip, an archive with no
    install.sh in it -- since the one caller turns every one of those
    straight into a toast; there is no partial-success case worth a tuple
    for.

    Extracted into a fresh temp directory every time, never reused, so a
    previous run's leftovers (or a partial extraction from one that failed
    halfway) can never mix into this one."""
    stage_dir = tempfile.mkdtemp(prefix="rogcontrol-update-")
    zip_path = os.path.join(stage_dir, "update.zip")
    request = urllib.request.Request(
        download_url, headers={"User-Agent": UPDATE_USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response, \
            open(zip_path, "wb") as f:
        shutil.copyfileobj(response, f)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(stage_dir)
    for root, _dirs, files in os.walk(stage_dir):
        if "install.sh" in files:
            return os.path.join(root, "install.sh")
    raise RuntimeError("the downloaded update has no install.sh in it")


# Tried in this order -- GNOME first since it is the desktop install.sh's own
# detection treats as the common case, then KDE's terminal, then xterm as the
# one every X11/Wayland session with the base package set tends to carry.
# Each entry is (binary name, argv prefix before the command to run).
UPDATE_TERMINALS = (
    ("gnome-terminal", ["gnome-terminal", "--"]),
    ("konsole", ["konsole", "-e"]),
    ("xterm", ["xterm", "-e"]),
)


def launch_update_terminal(install_sh_path):
    """Open a terminal running the staged installer. Returns ``(ok, message)``.

    install.sh cannot simply be run as a subprocess: it calls sudo per step
    and each of those calls can stop to ask for a password, which needs a
    real TTY attached. Opening a terminal is what gives it one, the same way
    a user running it by hand would."""
    script_dir = os.path.dirname(install_sh_path)
    inner_command = (
        f"cd {shlex.quote(script_dir)} && ./install.sh; "
        "echo; read -p 'Press Enter to close... '")
    for name, prefix in UPDATE_TERMINALS:
        if shutil.which(name) is None:
            continue
        try:
            subprocess.Popen([*prefix, "bash", "-c", inner_command])
            return True, None
        except OSError:
            continue
    tried = ", ".join(name for name, _prefix in UPDATE_TERMINALS)
    return False, (f"No terminal emulator found (tried {tried}). Run it "
                   f"yourself: cd {script_dir} && ./install.sh")
