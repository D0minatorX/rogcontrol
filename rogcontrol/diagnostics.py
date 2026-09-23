"""Read-only hardware reports for troubleshooting."""

import glob
import json
import os
import platform
import subprocess
import time
from . import APP_VERSION, hardware

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
    base = hardware._under(root, hardware.ASUS_WMI_DIR)
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        lines.append(f"{hardware.ASUS_WMI_DIR} does not exist on this machine")
        return
    # ppt_* first: they are the reason this report exists, and a tester
    # skimming should not have to hunt for them among boot_sound and
    # nv_dynamic_boost.
    entries.sort(key=lambda name: (not name.startswith("ppt_"), name))
    for name in entries:
        path = os.path.join(base, name)
        if not os.path.isfile(path):
            continue
        value = hardware.read_file(path)
        lines.append(f"{name} = {value!r}")


def _report_powercap(lines, root):
    _report_section(lines, "powercap")
    base = hardware._under(root, hardware.POWERCAP_DIR)
    try:
        zones = sorted(os.listdir(base))
    except OSError:
        lines.append(f"{hardware.POWERCAP_DIR} does not exist on this machine")
        return
    for zone in zones:
        zone_path = os.path.join(base, zone)
        if not os.path.isdir(zone_path):
            continue
        name = hardware.read_file(os.path.join(zone_path, "name"))
        lines.append(f"{zone} (name={name!r})")
        for entry in sorted(os.listdir(zone_path)):
            if "constraint_" not in entry:
                continue
            path = os.path.join(zone_path, entry)
            value = hardware.read_file(path)
            writable = os.access(path, os.W_OK)
            lines.append(f"  {entry} = {value!r} (writable={writable})")


def _report_cpufreq(lines, root):
    _report_section(lines, "cpufreq")
    policies = sorted(glob.glob(hardware._under(root, hardware.CPUFREQ_GLOB)))
    if not policies:
        lines.append("no cpufreq policies found")
        return
    p0 = policies[0]
    lines.append(f"scaling_driver = {hardware.read_file(os.path.join(p0, 'scaling_driver'))!r}")
    lines.append(f"cpuinfo_min_freq = {hardware.read_file(os.path.join(p0, 'cpuinfo_min_freq'))!r}")
    lines.append(f"cpuinfo_max_freq = {hardware.read_file(os.path.join(p0, 'cpuinfo_max_freq'))!r}")
    lines.append(f"amd_pstate_lowest_nonlinear_freq = "
                f"{hardware.read_file(os.path.join(p0, 'amd_pstate_lowest_nonlinear_freq'))!r}")
    lines.append(f"global boost node = "
                f"{os.path.exists(hardware._under(root, '/sys/devices/system/cpu/cpufreq/boost'))}")
    lines.append(f"per-policy boost node = "
                f"{bool(glob.glob(os.path.join(p0, 'boost')))}")
    lines.append(f"intel_pstate/no_turbo = "
                f"{hardware.read_file(hardware._under(root, hardware.INTEL_NO_TURBO_PATH))!r}")
    lines.append(f"energy_performance_preference = "
                f"{hardware.read_file(os.path.join(p0, 'energy_performance_preference'))!r}")
    prefs = hardware.read_epp_preferences(root=root)
    lines.append(f"energy_performance_available_preferences = {prefs!r}")


def _report_hwmon(lines, root):
    _report_section(lines, "hwmon")
    for name in hardware.CPU_TEMP_HWMON_NAMES:
        found = hardware.find_hwmon_by_name(name, root=root)
        lines.append(f"{name}: {'found at ' + found if found else 'not found'}")


def hardware_report_text(root=None):
    """Everything a hardware report contains, as one string.

    Read-only, and unfiltered on purpose -- see the module comment above.
    ``root`` re-bases the sysfs reads for testing, exactly as everywhere
    else in this file; asusd/cardwired and the desktop session are asked for
    directly regardless, since neither has a meaningful re-based form."""
    lines = [
        "ROG Control hardware report",
        f"app version: {APP_VERSION}",
        f"kernel: {platform.release()}",
        f"machine: {platform.machine()}",
    ]
    os_release = hardware.read_file(hardware._under(root, "/etc/os-release")) or ""
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
    lines.append(f"vendor: {hardware.read_cpu_vendor(root=root)!r}")
    lines.append(f"name: {hardware.read_cpu_name(root=root)!r}")
    lines.append(f"logical cores: {os.cpu_count()}")

    _report_asus_wmi(lines, root)
    _report_powercap(lines, root)
    _report_cpufreq(lines, root)
    _report_hwmon(lines, root)

    _report_section(lines, "detect_capabilities()")
    lines.append(json.dumps(hardware.detect_capabilities(root=root), indent=2,
                            default=str, sort_keys=True))

    _report_section(lines, "asusd / cardwired")
    lines.append(f"asusd: {hardware.read_asusd_state()}")
    lines.append(f"cardwired: {hardware.read_cardwired_state()}")

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
    if hardware.have_cmd("xdg-user-dir"):
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
