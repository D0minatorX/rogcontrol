#!/usr/bin/env python3
"""
ROG Control - a G-Helper-style power, fan and lighting app for ASUS ROG
laptops on Linux, without needing asusctl.

Hardware capabilities are detected at startup; anything the machine does
not expose is disabled in the interface rather than silently failing.
"""

import gi
import glob
import json
import os
import subprocess
import sys
import threading
import time

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3 as AppIndicator
except (ImportError, ValueError):
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator

from gi.repository import Gtk, GLib, Gdk, Gio, Pango

# Shown in the window title. Keep in step with VERSION in install.sh.
APP_VERSION = "1.0"

CONFIG_PATH = os.path.expanduser("~/.config/rogcontrol.json")
LOG_PATH = os.path.expanduser("~/.local/share/rogcontrol/rogcontrol.log")
LOG_MAX_BYTES = 256 * 1024   # keep one rotation, plenty for recent history


def log(message, level="INFO"):
    """Append one line to the app log.

    There was previously no record of what the app had tried or what came
    back, which made diagnosing anything a matter of reading journalctl for
    sudo invocations and guessing. Failures in particular were invisible:
    the enforcer discarded the helper's exit code entirely.

    Deliberately dependency-free and best-effort -- logging must never be
    the reason an action fails."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a") as f:
            f.write(f"{stamp} [{level}] {message}\n")
    except OSError:
        pass




# Fallbacks only -- the real limits are read from the GPU at startup by
# detect_gpu_limits(). Hardcoding these would break the app on any card
# other than the one it was written against.
GPU_MIN_W, GPU_MAX_W = 5, 140
KBD_MIN, KBD_MAX = 0, 3
GPU_MODES = ["Integrated", "Hybrid", "AsusMuxDgpu"]
FAN_CHANNELS = {"1": "CPU Fan", "2": "GPU Fan", "3": "Mid Fan"}
# rpm = floor + slope * curve_percent, per channel.
#
# Fan speed is NOT a straight fraction of max rpm, which is what this app
# assumed. Every channel idles at a hard floor of roughly 1650-1750 rpm --
# 0% on the curve does not stop the fan, and never did. Measured on this
# machine with flat curves at 11/31/50/70/100%, each held ~22s to settle,
# with the enforcer paused so nothing else could re-push a curve mid-test.
# Least-squares fit over those five points lands within 22-56 rpm on every
# channel, well under the 100 rpm granularity the hardware reports.
#
# This is what made the displayed numbers wrong: asking for a curve the app
# labelled 3400 rpm (50%) actually delivered ~4100, because the real
# mapping starts at the floor rather than at zero.
# Measured ceilings at a flat 100% curve, settled a full minute, were
# 6600 / 6500 / 7800 rpm -- the CPU and GPU fans physically top out a few
# hundred rpm below the 6800 nominal spec figure this app used to assume.
#
# NOTE FOR OTHER MACHINES: these numbers were measured on one ROG Strix
# G614PR. The *shape* of the relationship (an rpm floor plus a linear
# response, rather than a plain fraction of maximum) should hold on any
# ASUS laptop using this fan interface, but the exact floor and slope will
# differ per model and per fan. If your reported rpm looks off, re-measure:
# set a flat curve at a few known percentages, note the settled rpm for
# each, and fit floor/slope. Only the numbers below need changing --
# nothing else depends on the specific values.
FAN_RPM_CAL = {
    "1": (1655, 49.3),   # CPU Fan:   0% = 1655 rpm, 100% = 6585 rpm
    "2": (1643, 48.8),   # GPU Fan:   0% = 1643 rpm, 100% = 6523 rpm
    "3": (1734, 60.8),   # Mid Fan:   0% = 1734 rpm, 100% = 7814 rpm
}


def get_rpm_cal(config, channel):
    """Calibration for one fan: the user's own measured values if they have
    run the calibration, otherwise the built-in ones. The built-ins came
    off a single ROG Strix G614PR, so on any other machine they are a rough
    guess until Calibrate is run -- which is exactly why the button exists."""
    saved = (config or {}).get("fan_rpm_cal", {}).get(channel)
    if isinstance(saved, (list, tuple)) and len(saved) == 2:
        try:
            floor, slope = float(saved[0]), float(saved[1])
            if slope > 0:
                return (floor, slope)
        except (TypeError, ValueError):
            pass
    return FAN_RPM_CAL.get(channel)


def fit_rpm_cal(points):
    """Least-squares fit of rpm = floor + slope * percent over measured
    (percent, rpm) pairs. Returns None if the data is unusable -- too few
    points, or a slope that isn't positive, which would mean the fan never
    responded and the numbers should be rejected rather than saved."""
    pts = [(float(p), float(r)) for p, r in points if r is not None]
    if len(pts) < 2:
        return None
    n = len(pts)
    sx = sum(p for p, _ in pts); sy = sum(r for _, r in pts)
    sxy = sum(p * r for p, r in pts); sxx = sum(p * p for p, _ in pts)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    slope = (n * sxy - sx * sy) / denom
    floor = (sy - slope * sx) / n
    if slope <= 0:
        return None
    return (round(floor, 1), round(slope, 2))


def pct_to_rpm(rpm_cal, pct):
    """Curve percentage -> the rpm the fan will actually run at."""
    if not rpm_cal:
        return None
    floor, slope = rpm_cal
    return round(floor + slope * max(0, min(100, pct)))


def rpm_to_pct(rpm_cal, rpm):
    """Inverse of pct_to_rpm, for turning a live rpm reading back into the
    curve percentage it corresponds to. Clamped to 0-100: an idling fan
    sits slightly below the fitted floor, which would otherwise read as a
    small negative percentage."""
    if not rpm_cal:
        return None
    floor, slope = rpm_cal
    if slope <= 0:
        return None
    return round(max(0, min(100, (rpm - floor) / slope)))
# Seconds to wait between applying one fan channel's curve and the next.
# The asus-wmi embedded controller silently drops fan-curve writes fired
# too close together for different channels. Measured directly on this
# hardware: one channel applied in isolation reliably took effect (fan
# smoothly ramped to the new target); a 0.5s gap was NOT enough (2 of 3
# channels stayed stuck on their old value indefinitely); an 8s gap was
# sufficient for all 3 to converge correctly, repeatedly, with zero
# reversion. Applying all three therefore takes ~16s, which is why the
# fan applies run off the GTK main thread.
FAN_CHANNEL_GAP_S = 8
COALL_MIN, COALL_MAX = -30, 0
CLOCK_MIN, CLOCK_MAX = -1000, 1000
MEM_CLOCK_MIN, MEM_CLOCK_MAX = -1000, 1000
# Hard ceiling on the GPU core clock (nvidia-smi --lock-gpu-clocks), as
# opposed to the offset above which shifts the stock curve. The maximum is
# read from the GPU itself at startup rather than hardcoded, so this works
# on cards other than the one it was developed against; the fallback is
# only used if nvidia-smi can't be queried. The slider's top position means
# "Default" and unlocks rather than capping.
CLOCK_LIMIT_MIN, CLOCK_LIMIT_FALLBACK_MAX = 200, 3090
CLOCK_LIMIT_MAX = CLOCK_LIMIT_FALLBACK_MAX  # replaced by detect_gpu_limits()
# Dynamic Boost (watts of the shared power budget the firmware may hand to
# the GPU) and GPU temperature target. Both are asus-wmi platform knobs,
# not nvidia-smi ones. Ranges are the kernel driver's own limits
# (NVIDIA_BOOST_MIN/MAX and NVIDIA_TEMP_MIN/MAX in asus-wmi.c) -- writing
# outside them is rejected with -EINVAL.
DYN_BOOST_MIN, DYN_BOOST_MAX = 5, 25
TEMP_TARGET_MIN, TEMP_TARGET_MAX = 75, 87
# What the firmware currently reports. Read at startup and used as the
# starting value for a fresh profile, so a new install begins at whatever
# the machine shipped with rather than at an arbitrary end of the slider.
# A profile that already stores a value always wins over these.
FIRMWARE_DYN_BOOST = DYN_BOOST_MIN
FIRMWARE_TEMP_TARGET = TEMP_TARGET_MIN
# Keyboard RGB via rogauracore, which has confirmed support for this
# laptop's N-Key controller (USB ID 0b05:19b6, verified via `lsusb` on this
# exact machine) -- these map friendly UI names to rogauracore's actual
# command names, not guessed raw mode numbers like the old sysfs approach.
KBD_RGB_MODES = {
    "Static": "single_static",
    "Breathing": "single_breathing",
    "Pulse": "single_pulsing",     # rogauracore's real pulse effect -- a
                                    # distinct sharp flash, not a breathing
                                    # fade with a different color
    "Color Cycle": "single_colorcycle",
    "Rainbow": "rainbow",
    "Gradient Static": "gradient_static",
    "GPU Temp Color": "gpu_temp_color",
    "CPU Temp Color": "cpu_temp_color",
    "Battery Level": "battery_color",
    "Ambient": "ambient",           # follows what is on the primary monitor
}

# Blackbody-ish gradient used by GPU/CPU Temp Color modes: cool blue at low
# temp, up through green/yellow, to red at high temp. Bounds chosen around
# typical laptop CPU/GPU operating ranges under load.
TEMP_COLOR_MIN_C = 40
TEMP_COLOR_MAX_C = 90

# Our profile name -> power-profiles-daemon's fixed mode names, kept in
# sync with the same mapping in rogcontrol-enforcer.py. Control is one-way
# (app -> OS): selecting a profile here sets PPD's mode to match; the
# enforcer service is what actively reverts PPD if changed from elsewhere.
PROFILE_TO_PPD_MODE = {
    "Performance": "performance",
    "Balanced Performance": "balanced",
    "Balanced Power": "balanced",
    "Quiet": "power-saver",
}

# Ordered coolest/quietest first, which is the order they appear in the
# profile menu and the tray.
#
# Each profile carries an energy preference (EPP), applied for you when the
# profile becomes active -- there is no control for it in the window. That is
# the whole point of the two Balanced profiles: identical power limits and
# fan curve, differing only in how hard the CPU chases clocks. Both map to
# the OS "balanced" power mode, and the reverse mapping resolves that mode to
# "Balanced Performance" (see PPD_MODE_TO_PROFILE in rogcontrol-enforcer.py).
DEFAULT_PROFILES = {
    "Quiet": {
        "cpu": {"stapm": 25000, "fast": 35000, "slow": 25000, "temp": 85, "coall": 0,
                "epp": "power"},
        "gpu": {"watts": 65, "clock_offset": 0, "mem_clock_offset": 0},
        "fans": {
            "1": [[40, 25], [60, 40], [75, 60], [90, 80]],
            "2": [[40, 25], [60, 40], [75, 60], [90, 80]],
            "3": [[40, 25], [60, 40], [75, 60], [90, 80]],
        },
    },
    "Balanced Power": {
        "cpu": {"stapm": 55000, "fast": 65000, "slow": 55000, "temp": 90, "coall": 0,
                "epp": "balance_power"},
        "gpu": {"watts": 100, "clock_offset": 0, "mem_clock_offset": 0},
        "fans": {
            "1": [[40, 30], [60, 55], [75, 75], [90, 90]],
            "2": [[40, 30], [60, 55], [75, 75], [90, 90]],
            "3": [[40, 30], [60, 55], [75, 75], [90, 90]],
        },
    },
    "Balanced Performance": {
        "cpu": {"stapm": 55000, "fast": 65000, "slow": 55000, "temp": 90, "coall": 0,
                "epp": "balance_performance"},
        "gpu": {"watts": 100, "clock_offset": 0, "mem_clock_offset": 0},
        "fans": {
            "1": [[40, 30], [60, 55], [75, 75], [90, 90]],
            "2": [[40, 30], [60, 55], [75, 75], [90, 90]],
            "3": [[40, 30], [60, 55], [75, 75], [90, 90]],
        },
    },
    "Performance": {
        "cpu": {"stapm": 75000, "fast": 90000, "slow": 75000, "temp": 95, "coall": 0,
                "epp": "performance"},
        "gpu": {"watts": 140, "clock_offset": 0, "mem_clock_offset": 0},
        "fans": {
            "1": [[40, 45], [55, 70], [70, 85], [85, 100]],
            "2": [[40, 45], [55, 70], [70, 85], [85, 100]],
            "3": [[40, 45], [55, 70], [70, 85], [85, 100]],
        },
    },
}

DARK_CSS = b"""
window { background-color: #17171a; }
notebook header { background-color: #1c1c1f; border-bottom: 1px solid #2c2c30; }
notebook tab { padding: 6px 14px; color: #a0a0a6; }
notebook tab:checked { color: #ffffff; border-bottom: 2px solid #c8202a; }
label { color: #e8e8ea; }
scale trough { background-color: #2a2a2e; border-radius: 6px; min-height: 6px; }
scale highlight { background-color: #c8202a; border-radius: 6px; }
button { background-color: #24242a; color: #e8e8ea; border: 1px solid #34343a;
         border-radius: 6px; padding: 5px 10px; }
button:hover { background-color: #2e2e34; }
combobox, spinbutton, entry { background-color: #24242a; color: #e8e8ea;
         border: 1px solid #34343a; border-radius: 4px; }
separator { background-color: #2c2c30; min-height: 1px; }
radiobutton label { color: #e8e8ea; }
checkbutton label { color: #e8e8ea; }
"""


ASUS_WMI_DIR = "/sys/devices/platform/asus-nb-wmi"

# Filled in by detect_capabilities() at startup. Controls whose underlying
# interface is missing get disabled with a reason rather than left live to
# fail silently when clicked -- what is available varies a lot between ASUS
# models, kernels and driver versions.
CAPS = {}


def _have_cmd(name):
    return subprocess.run(["sh", "-c", f"command -v {name}"],
                          capture_output=True).returncode == 0


def read_epp_preferences():
    """EPP names this kernel accepts, or [] if the machine has no EPP."""
    for path in sorted(glob.glob(
            "/sys/devices/system/cpu/cpufreq/policy*/"
            "energy_performance_available_preferences")):
        try:
            with open(path) as f:
                return f.read().split()
        except OSError:
            continue
    return []


def read_cpu_clock_range():
    """(min_khz, max_khz) this CPU's cores can be capped between, or None.

    Both come from cpuinfo_*, i.e. what the hardware can do, not the current
    scaling_* window -- otherwise a cap already in effect would shrink the
    slider's range and there would be no way back up."""
    for path in sorted(glob.glob(
            "/sys/devices/system/cpu/cpufreq/policy*/cpuinfo_max_freq")):
        base = os.path.dirname(path)
        try:
            with open(path) as f:
                hi = int(f.read().strip())
            with open(os.path.join(base, "cpuinfo_min_freq")) as f:
                lo = int(f.read().strip())
        except (OSError, ValueError):
            continue
        if hi > lo:
            return lo, hi
    return None


def read_current_cpu_clock_cap():
    """Ceiling currently in force, in kHz, or None."""
    for path in sorted(glob.glob(
            "/sys/devices/system/cpu/cpufreq/policy*/scaling_max_freq")):
        try:
            with open(path) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            continue
    return None


def read_current_epp():
    """EPP the hardware is actually on right now, or None."""
    for path in sorted(glob.glob("/sys/devices/system/cpu/cpufreq/policy*/"
                                 "energy_performance_preference")):
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            continue
    return None


def detect_capabilities():
    """Probe what this particular machine actually supports."""
    global CAPS
    caps = {}
    caps["fan_curve"] = find_hwmon_by_name("asus_custom_fan_curve") is not None
    caps["fan_rpm"] = find_hwmon_by_name("asus") is not None
    caps["nv_temp_target"] = os.path.exists(f"{ASUS_WMI_DIR}/nv_temp_target")
    caps["nv_dynamic_boost"] = os.path.exists(f"{ASUS_WMI_DIR}/nv_dynamic_boost")
    caps["nvidia"] = _have_cmd("nvidia-smi")
    caps["nvidia_settings"] = _have_cmd("nvidia-settings")
    caps["supergfxctl"] = _have_cmd("supergfxctl")
    caps["rogauracore"] = _have_cmd("rogauracore")
    caps["ryzenadj"] = _have_cmd("ryzenadj") or os.path.exists("/usr/local/bin/ryzenadj")
    # amd-pstate publishes one global boost switch; other cpufreq drivers put
    # it under each policy. Either is enough to offer the control.
    caps["cpu_boost"] = (
        os.path.exists("/sys/devices/system/cpu/cpufreq/boost")
        or bool(glob.glob("/sys/devices/system/cpu/cpufreq/policy*/boost")))
    # The preference names differ between amd-pstate and intel_pstate, so read
    # them from the kernel instead of hardcoding a list. "custom" is dropped:
    # it needs a raw 0-255 value written elsewhere, so offering it in a
    # dropdown would only produce failures.
    caps["cpu_epp"] = [
        p for p in read_epp_preferences() if p != "custom"]
    caps["cpu_clock"] = read_cpu_clock_range()
    caps["kbd_backlight"] = os.path.exists(
        "/sys/class/leds/asus::kbd_backlight/brightness")
    # RGB support is two separate questions: is there an Aura controller at
    # all, and does it have addressable zones. A mode that cannot work on this
    # machine is removed from the dropdown rather than left there to be picked
    # and silently do nothing.
    aura_id = find_aura_keyboard()
    caps["aura_id"] = aura_id
    caps["kbd_rgb"] = bool(aura_id) and caps["rogauracore"]
    caps["kbd_rgb_zones"] = bool(aura_id) and aura_id in AURA_MULTI_ZONE_IDS
    caps["kbd_battery"] = read_battery()[0] is not None
    caps["kbd_ambient"] = ambient_available()
    # Starting values straight from the firmware, clamped to the range the
    # kernel driver will accept in case a machine reports something odd.
    global FIRMWARE_DYN_BOOST, FIRMWARE_TEMP_TARGET
    for path, lo, hi, name in (
        (f"{ASUS_WMI_DIR}/nv_dynamic_boost", DYN_BOOST_MIN, DYN_BOOST_MAX, "boost"),
        (f"{ASUS_WMI_DIR}/nv_temp_target", TEMP_TARGET_MIN, TEMP_TARGET_MAX, "temp"),
    ):
        try:
            val = int(read_file(path))
        except (TypeError, ValueError):
            continue
        val = max(lo, min(hi, val))
        if name == "boost":
            FIRMWARE_DYN_BOOST = val
        else:
            FIRMWARE_TEMP_TARGET = val

    caps["charge_limit"] = any(
        os.path.exists(os.path.join("/sys/class/power_supply", e,
                                    "charge_control_end_threshold"))
        for e in (os.listdir("/sys/class/power_supply")
                  if os.path.isdir("/sys/class/power_supply") else []))
    CAPS = caps
    return caps


def relax_min_size(widget):
    """Stop a subtree from dictating a large minimum window size.

    Two things force the window to stay big: wrapping labels ask for their
    full natural width, and long rows of controls ask for the sum of their
    children. Capping the wrap width lets a label reflow instead, and the
    surrounding ScrolledWindow handles anything that still cannot shrink."""
    if isinstance(widget, Gtk.Label) and widget.get_line_wrap():
        widget.set_max_width_chars(40)
        widget.set_width_chars(0)
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            relax_min_size(child)


def compact_combo(combo, chars=8):
    """A combo box normally demands enough width for its longest entry, so
    two of them side by side set a wide floor for the whole window. Letting
    the text ellipsize means they shrink with the window instead.

    The open dropdown must NOT ellipsize, though: it draws with these same
    renderers, so the list was being truncated exactly like the button. With
    two profiles sharing a prefix that made them indistinguishable -- both
    "Balanced Power" and "Balanced Performance" read as "Balanced P...".
    So the limit is lifted while the popup is open and restored when it
    closes, and the popup is allowed to size itself rather than inheriting
    the button's width."""
    cells = combo.get_cells()
    for cell in cells:
        cell.set_property("ellipsize", Pango.EllipsizeMode.END)
        cell.set_property("max-width-chars", chars)
    combo.set_popup_fixed_width(False)

    def on_popup_shown(c, _param):
        open_now = c.get_property("popup-shown")
        for cell in cells:
            cell.set_property(
                "ellipsize",
                Pango.EllipsizeMode.NONE if open_now else Pango.EllipsizeMode.END)
            cell.set_property("max-width-chars", -1 if open_now else chars)

    combo.connect("notify::popup-shown", on_popup_shown)

    # The button can still be too narrow to read, so the full name is always
    # available as a tooltip.
    def on_changed(c):
        text = c.get_active_text()
        if text:
            c.set_tooltip_text(text)

    combo.connect("changed", on_changed)
    on_changed(combo)
    return combo


def scrollable(child):
    """Put a tab's contents in a scroller so the window can be made smaller
    than them. Without this the window's minimum size is the sum of
    everything on the tallest tab -- which is why it could only ever be
    made bigger."""
    relax_min_size(child)
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    # Natural size still follows the content, so the default window size is
    # unchanged; only the *minimum* becomes small.
    sw.set_propagate_natural_width(True)
    sw.set_propagate_natural_height(True)
    sw.add(child)
    return sw


def fan_curve_already_set(channel, points):
    """True if the firmware already holds this exact curve for this channel.

    Applying a fan curve costs an 8 second inter-channel gap, so switching
    between two profiles whose curves happen to match wasted ~16 seconds
    writing values that were already there. Reading the points back lets
    those channels be skipped.

    Caveat worth stating: these sysfs point files are the kernel driver's
    cached copy of what was last written, not a read of the EC itself, so a
    match proves 'we wrote this' rather than 'the hardware is running this'.
    That is the same assumption the enforcer's change-detection already
    makes, and it is why a forced re-apply still happens on a power-mode
    change and on the periodic safety re-check -- those are the cases where
    the EC is known to have thrown the curve away behind our back."""
    hw = find_hwmon_by_name("asus_custom_fan_curve")
    if not hw:
        return False
    expanded = interpolate_curve(points, 8)
    if len(expanded) != 8:
        return False
    for i, (temp, pct) in enumerate(expanded, start=1):
        got_t = read_file(os.path.join(hw, f"pwm{channel}_auto_point{i}_temp"))
        got_p = read_file(os.path.join(hw, f"pwm{channel}_auto_point{i}_pwm"))
        try:
            if int(got_t) != int(temp) or int(got_p) != pct_to_pwm255(pct):
                return False
        except (TypeError, ValueError):
            return False
    return True


def disable_widget(widget, reason):
    """Grey a control out and say why, instead of leaving it clickable and
    silently broken."""
    widget.set_sensitive(False)
    try:
        widget.set_tooltip_text(f"Not available on this machine: {reason}")
    except Exception:
        pass


def detect_gpu_limits():
    """Read this GPU's real power and clock limits instead of assuming the
    ones from the machine this was developed on. Falls back to the module
    defaults if nvidia-smi isn't answering, so the app still starts."""
    global GPU_MIN_W, GPU_MAX_W, CLOCK_LIMIT_MAX
    CLOCK_LIMIT_MAX = CLOCK_LIMIT_FALLBACK_MAX
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.min_limit,power.max_limit",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            lo, hi = (float(x) for x in r.stdout.strip().split(",")[:2])
            if 0 < lo < hi:
                GPU_MIN_W, GPU_MAX_W = round(lo), round(hi)
    except Exception:
        pass
    try:
        r = subprocess.run(["nvidia-smi", "-q", "-d", "CLOCK"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            # "Max Clocks" section, first "Graphics" line under it
            in_max = False
            for line in r.stdout.splitlines():
                s = line.strip()
                if s.startswith("Max Clocks"):
                    in_max = True
                elif in_max and s.startswith("Graphics"):
                    mhz = int(s.split(":", 1)[1].strip().split()[0])
                    if mhz > 0:
                        CLOCK_LIMIT_MAX = mhz
                    break
    except Exception:
        pass


def find_hwmon_by_name(name):
    base = "/sys/class/hwmon"
    try:
        for entry in os.listdir(base):
            path = os.path.join(base, entry)
            try:
                with open(os.path.join(path, "name")) as f:
                    if f.read().strip() == name:
                        return path
            except OSError:
                continue
    except OSError:
        pass
    return None


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


MULTI_ZONE_MODES = ("Gradient Static",)

AMBIENT_ZONES = 4
# The screen is scaled to this before averaging. Small enough to cost
# nothing, big enough that scaling averages whole regions instead of point
# sampling a few stray pixels.
AMBIENT_GRID_W, AMBIENT_GRID_H = 64, 36
AMBIENT_INTERVAL_S = 0.5
# Below this much change, the keyboard is left alone: every update is a USB
# round trip through rogauracore, and repainting on noise makes the
# keyboard flicker while costing wakeups.
AMBIENT_MIN_DELTA = 12
# Region averages come out dim; the brightest channel of a zone is scaled up
# to this, which preserves the hue and only raises the level.
AMBIENT_TARGET_LEVEL = 200
# Below this the region is treated as genuinely dark and left alone, rather
# than amplifying near-black into colour noise.
AMBIENT_DARK_LEVEL = 12


def ambient_available():
    """True if this machine can stream its screen for Ambient mode.

    Screen capture on Wayland goes through the desktop portal -- there is no
    unsandboxed screenshot API to fall back on -- so this needs both the
    ScreenCast portal and GStreamer's PipeWire source."""
    try:
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
    except (ImportError, ValueError):
        return False
    if not Gst.is_initialized():
        Gst.init(None)
    if not all(Gst.ElementFactory.find(name) for name in
               ("pipewiresrc", "videoconvert", "videoscale", "appsink")):
        return False
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.ScreenCast", None)
        return proxy.get_cached_property("version") is not None
    except GLib.Error:
        return False


class AmbientSampler:
    """Paints the keyboard with what is on the primary monitor.

    The desktop asks for permission the first time; the portal hands back a
    restore token which is saved, so later runs reconnect to the same monitor
    without prompting again.

    Portal negotiation is asynchronous and runs on the GTK main loop, because
    every step answers on a D-Bus signal. Only the sampling loop is a thread,
    since writing the keyboard blocks for a moment and must not stutter the
    window."""

    def __init__(self, on_colors, on_status, restore_token=None,
                 on_token=None):
        self.on_colors = on_colors        # called with 4 (r, g, b) tuples
        self.on_status = on_status        # called with a status string
        self.on_token = on_token          # called when the portal issues one
        self.restore_token = restore_token
        self._session = None
        self._pipeline = None
        self._appsink = None
        self._thread = None
        self._stop = threading.Event()
        self._token_counter = 0
        self._subscriptions = []

    # -- portal handshake -------------------------------------------------

    def _unique_token(self, prefix):
        self._token_counter += 1
        # The portal builds the request object path from this, so it has to
        # be unique per call and contain only path-safe characters.
        return f"rogcontrol_{prefix}_{os.getpid()}_{self._token_counter}"

    def _await_response(self, token, callback):
        """Run callback(results) when the portal answers this request."""
        sender = self._bus.get_unique_name()[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        def on_signal(_conn, _sender, _path, _iface, _signal, params):
            code, results = params.unpack()
            for sub in self._subscriptions:
                if sub[1] == path:
                    self._bus.signal_unsubscribe(sub[0])
                    self._subscriptions.remove(sub)
                    break
            if code != 0:
                # 1 is the user cancelling the picker, which is a choice
                # rather than a failure.
                self.on_status("Ambient: screen sharing declined"
                               if code == 1 else
                               f"Ambient: portal returned {code}")
                self.stop()
                return
            callback(results)

        sub_id = self._bus.signal_subscribe(
            "org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request",
            "Response", path, None, Gio.DBusSignalFlags.NONE, on_signal)
        self._subscriptions.append((sub_id, path))

    def start(self):
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._proxy = Gio.DBusProxy.new_sync(
                self._bus, Gio.DBusProxyFlags.NONE, None,
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.ScreenCast", None)
        except GLib.Error as e:
            self.on_status(f"Ambient: no screen portal ({e.message})")
            return

        token = self._unique_token("create")
        self._await_response(token, self._on_session_created)
        self.on_status("Ambient: asking for screen access...")
        self._proxy.call(
            "CreateSession",
            GLib.Variant("(a{sv})", ({
                "handle_token": GLib.Variant("s", token),
                "session_handle_token": GLib.Variant(
                    "s", self._unique_token("session")),
            },)),
            Gio.DBusCallFlags.NONE, -1, None, self._ignore_reply)

    def _ignore_reply(self, proxy, result):
        # The real answer arrives as a Response signal; this only surfaces an
        # immediate D-Bus failure.
        try:
            proxy.call_finish(result)
        except GLib.Error as e:
            self.on_status(f"Ambient: portal call failed ({e.message})")

    def _on_session_created(self, results):
        self._session = results["session_handle"]
        token = self._unique_token("select")
        self._await_response(token, self._on_sources_selected)
        options = {
            "handle_token": GLib.Variant("s", token),
            "types": GLib.Variant("u", 1),        # monitors only
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", 1),  # cursor not drawn
            # 2 = keep permission until explicitly revoked, which is what
            # makes the picker a one-time prompt.
            "persist_mode": GLib.Variant("u", 2),
        }
        if self.restore_token:
            options["restore_token"] = GLib.Variant("s", self.restore_token)
        self._proxy.call(
            "SelectSources",
            GLib.Variant("(oa{sv})", (self._session, options)),
            Gio.DBusCallFlags.NONE, -1, None, self._ignore_reply)

    def _on_sources_selected(self, _results):
        token = self._unique_token("start")
        self._await_response(token, self._on_started)
        self._proxy.call(
            "Start",
            GLib.Variant("(osa{sv})", (
                self._session, "",
                {"handle_token": GLib.Variant("s", token)})),
            Gio.DBusCallFlags.NONE, -1, None, self._ignore_reply)

    def _on_started(self, results):
        new_token = results.get("restore_token")
        if new_token and self.on_token:
            self.on_token(new_token)
        streams = results.get("streams") or []
        if not streams:
            self.on_status("Ambient: no monitor was shared")
            self.stop()
            return
        node_id = streams[0][0]
        try:
            reply, fd_list = self._proxy.call_with_unix_fd_list_sync(
                "OpenPipeWireRemote",
                GLib.Variant("(oa{sv})", (self._session, {})),
                Gio.DBusCallFlags.NONE, -1, None, None)
            fd = fd_list.get(reply.unpack()[0])
        except GLib.Error as e:
            self.on_status(f"Ambient: cannot open the stream ({e.message})")
            self.stop()
            return
        self._build_pipeline(fd, node_id)

    # -- capture ----------------------------------------------------------

    def _build_pipeline(self, fd, node_id):
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        if not Gst.is_initialized():
            Gst.init(None)
        try:
            # videorate matters more than it looks: the desktop only sends a
            # frame when the screen actually changes, so on a still screen the
            # sampler would block forever waiting for one. videorate repeats
            # the last frame at a fixed rate, and the unchanged-colour check
            # in the loop means those repeats cost nothing.
            self._pipeline = Gst.parse_launch(
                f"pipewiresrc fd={fd} path={node_id} always-copy=true ! "
                "videorate ! videoconvert ! videoscale ! "
                f"video/x-raw,format=RGB,width={AMBIENT_GRID_W},"
                f"height={AMBIENT_GRID_H},framerate=2/1 ! "
                "appsink name=sink max-buffers=1 drop=true sync=false")
            self._appsink = self._pipeline.get_by_name("sink")
            self._pipeline.set_state(Gst.State.PLAYING)
        except GLib.Error as e:
            self.on_status(f"Ambient: capture failed ({e.message})")
            self.stop()
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        self.on_status("Current mode: Ambient")

    def _sample_loop(self):
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        last = None
        while not self._stop.is_set():
            sample = self._appsink.emit("try-pull-sample", Gst.SECOND)
            if sample is None:
                continue
            colors = self._sample_to_zones(sample)
            if colors is None:
                continue
            if last is None or max(abs(a - b)
                                   for zc, lc in zip(colors, last)
                                   for a, b in zip(zc, lc)) >= AMBIENT_MIN_DELTA:
                last = colors
                self.on_colors(colors)
            self._stop.wait(AMBIENT_INTERVAL_S)

    def _sample_to_zones(self, sample):
        """Average each vertical band of the frame into one colour."""
        buf = sample.get_buffer()
        ok, info = buf.map(0)  # Gst.MapFlags.READ
        if not ok:
            return None
        try:
            data = bytes(info.data)
        finally:
            buf.unmap(info)
        caps = sample.get_caps().get_structure(0)
        width = caps.get_value("width")
        height = caps.get_value("height")
        # GStreamer pads each row to a 4-byte boundary, so the stride is not
        # necessarily width * 3 -- using the wrong one skews the colours
        # progressively down the frame.
        stride = len(data) // height if height else width * 3
        band = max(1, width // AMBIENT_ZONES)
        zones = []
        for z in range(AMBIENT_ZONES):
            x0 = z * band
            x1 = width if z == AMBIENT_ZONES - 1 else (z + 1) * band
            r = g = b = n = 0
            for y in range(height):
                row = y * stride
                for x in range(x0, x1):
                    i = row + x * 3
                    r += data[i]
                    g += data[i + 1]
                    b += data[i + 2]
                    n += 1
            if not n:
                return None
            zones.append(self._boost((r // n, g // n, b // n)))
        return zones

    @staticmethod
    def _boost(color):
        """Lift a screen average to something a keyboard can actually show.

        Averaging a whole region lands most colours in the 30-70 range, which
        the keys render as a barely-lit smudge. Scaling the brightest channel
        up to AMBIENT_TARGET_LEVEL keeps the hue exactly and only changes how
        bright it is. A genuinely dark region is left dark rather than being
        amplified into colour noise."""
        peak = max(color)
        if peak < AMBIENT_DARK_LEVEL or peak >= AMBIENT_TARGET_LEVEL:
            return color
        gain = AMBIENT_TARGET_LEVEL / peak
        return tuple(min(255, round(c * gain)) for c in color)

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None
        if self._pipeline is not None:
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        self._appsink = None
        for sub_id, _path in self._subscriptions:
            self._bus.signal_unsubscribe(sub_id)
        self._subscriptions = []
        if self._session:
            # Closing the session releases the capture; the granted permission
            # survives it, which is what the restore token is for.
            try:
                Gio.DBusProxy.new_sync(
                    self._bus, Gio.DBusProxyFlags.NONE, None,
                    "org.freedesktop.portal.Desktop", self._session,
                    "org.freedesktop.portal.Session", None).call_sync(
                        "Close", None, Gio.DBusCallFlags.NONE, -1, None)
            except GLib.Error:
                pass
            self._session = None


def supported_kbd_modes():
    """The RGB modes this machine can actually perform, in dropdown order.

    Detection runs once at startup, so this is deliberately generous: a mode
    is only dropped when something it strictly requires is known to be
    absent. The alternative -- listing everything and letting unsupported
    picks fail silently -- is what makes an app look broken on hardware the
    author never had."""
    modes = []
    for name in KBD_RGB_MODES:
        if name in MULTI_ZONE_MODES and not CAPS.get("kbd_rgb_zones", True):
            continue
        if name == "Battery Level" and not CAPS.get("kbd_battery", True):
            continue
        if name == "GPU Temp Color" and not CAPS.get("nvidia", True):
            continue
        if name == "Ambient" and not CAPS.get("kbd_ambient", True):
            continue
        modes.append(name)
    return modes


def find_aura_keyboard():
    """USB product ID of the ASUS Aura keyboard controller, or None.

    Reads /sys directly rather than shelling out to lsusb, which is not
    installed everywhere and would be a new dependency for a detection that
    is three file reads."""
    base = "/sys/bus/usb/devices"
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


def read_battery():
    """(percent, charging) for the first real battery, or (None, None).

    "charging" covers Charging only -- Full and "Not charging" (which is what
    a charge-limited ASUS reports when sitting on AC at its threshold) are not
    charging, and showing them as such would make the light lie about what the
    battery is doing."""
    base = "/sys/class/power_supply"
    try:
        for entry in sorted(os.listdir(base)):
            path = os.path.join(base, entry)
            if read_file(os.path.join(path, "type")) != "Battery":
                continue
            capacity = read_file(os.path.join(path, "capacity"))
            if capacity is None:
                continue
            status = read_file(os.path.join(path, "status")) or ""
            return int(capacity), status == "Charging"
    except (OSError, ValueError):
        pass
    return None, None


def battery_to_rgb(percent, charging):
    """Colour for Battery Level mode.

    Discharging runs green -> yellow -> red as it empties, the convention
    everyone already reads without being told. Charging runs blue -> green
    instead, so a glance tells you whether it is filling or draining without
    having to remember which shade of green meant what."""
    pct = max(0, min(100, percent))
    if charging:
        frac = pct / 100
        return 0, round(255 * frac), round(255 * (1 - frac))
    if pct >= 50:
        frac = (100 - pct) / 50          # 100% green -> 50% yellow
        return round(255 * frac), 255, 0
    frac = (50 - pct) / 50               # 50% yellow -> 0% red
    return 255, round(255 * (1 - frac)), 0


def find_power_supply_mains():
    base = "/sys/class/power_supply"
    try:
        for entry in os.listdir(base):
            t = read_file(os.path.join(base, entry, "type"))
            if t == "Mains":
                return os.path.join(base, entry)
    except OSError:
        pass
    return None


def is_ac_connected():
    path = find_power_supply_mains()
    if path:
        val = read_file(os.path.join(path, "online"))
        if val is not None:
            return val == "1"
    return None


def read_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def run_helper(*args):
    cmd = " ".join(str(a) for a in args)
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/local/bin/rogcontrol-helper", *[str(a) for a in args]],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            msg = (result.stderr or result.stdout or "unknown error").strip()
            log(f"helper failed: {cmd} -> {msg}", "ERROR")
            return False, msg
        return True, result.stdout.strip()
    except Exception as e:
        log(f"helper could not run: {cmd} -> {e}", "ERROR")
        return False, str(e)


def set_ppd_mode(mode):
    """Sets power-profiles-daemon's active mode via powerprofilesctl -- the
    standard user-facing CLI for this, no root needed (PPD runs as its own
    system service and accepts this over the session bus/polkit)."""
    try:
        subprocess.run(["powerprofilesctl", "set", mode],
                        capture_output=True, text=True, timeout=5)
    except Exception:
        pass


def notify(title, body):
    try:
        subprocess.run(["notify-send", title, body], timeout=5)
    except Exception:
        pass


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


def temp_to_rgb(temp_c, lo=TEMP_COLOR_MIN_C, hi=TEMP_COLOR_MAX_C):
    """Maps a temperature to a blue-green-yellow-red gradient. Below lo is
    pure blue, above hi is pure red, in between sweeps through the hue
    range like a simplified thermal gradient."""
    t = max(0.0, min(1.0, (temp_c - lo) / max(1, (hi - lo))))
    if t < 0.5:
        # blue -> green
        frac = t / 0.5
        r, g, b = 0, round(255 * frac), round(255 * (1 - frac))
    else:
        # green -> red
        frac = (t - 0.5) / 0.5
        r, g, b = round(255 * frac), round(255 * (1 - frac)), 0
    return r, g, b


# Stamped into the config file. Nothing reads it yet: it exists so a future
# release that needs a genuine one-time step -- a rename or a split, something
# that cannot be detected by looking at the file -- has something to gate on.
CONFIG_VERSION = 1

DEFAULT_CONFIG = {
    "current_profile": "Balanced Performance",
    "kbd_brightness": 2,
    "charge_limit": 100,
    "ac_profile": "Performance",
    "battery_profile": "Quiet",
    "window_size": [600, 700],
    "fan_display_unit": "percent",
}


def tailored_default_profiles():
    """The stock profiles, with GPU wattage scaled to the GPU actually
    fitted rather than the 140W one this was written on.

    The tiers keep their relative shape (Quiet ~46%, Balanced ~71%,
    Performance 100% of the card's maximum), so a 60W card gets a sensible
    28/43/60W spread instead of three profiles asking for wattages the
    driver will simply refuse.

    CPU limits are deliberately NOT scaled: nothing here can read a chip's
    real ceiling, and inventing one would be worse than leaving a
    conservative starting value the user can tune. They stay inside the
    range the privileged helper validates, and the firmware clamps anything
    it dislikes."""
    profiles = json.loads(json.dumps(DEFAULT_PROFILES))
    reference_max = 140.0  # what the built-in numbers were chosen against
    for prof in profiles.values():
        gpu = prof.get("gpu")
        if not gpu or "watts" not in gpu:
            continue
        ratio = gpu["watts"] / reference_max
        gpu["watts"] = max(GPU_MIN_W, min(GPU_MAX_W, round(ratio * GPU_MAX_W)))
    return profiles


def migrate_config(cfg):
    """Bring a config from any older version up to date WITHOUT touching
    anything the user has set.

    Only missing keys are filled in. Every existing value is left exactly as
    it is, including ones this version doesn't recognise -- an unknown key
    is more likely to be from a newer build than junk, and silently dropping
    it would lose the user's settings on a downgrade/upgrade cycle.

    There is deliberately no migration chain here. config_version is only a
    stamp, so that a future release which needs a genuine one-time step -- a
    rename, a split, anything that cannot be detected from the file itself --
    has something to gate on."""
    for key, value in DEFAULT_CONFIG.items():
        cfg.setdefault(key, json.loads(json.dumps(value)))

    # Profiles: keep the user's, add the stock ones only if there are none
    # at all. A user who deleted "Quiet" should not silently get it back.
    profiles = cfg.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        # Only reached on a fresh install (or a config with no profiles at
        # all), so this is the one place hardware-tailored defaults apply.
        # An update never gets here, which is what keeps existing profiles
        # from being replaced.
        cfg["profiles"] = tailored_default_profiles()
    else:
        # Fill in only sections a profile is missing entirely, so profiles
        # written by an older version still load.
        for name, prof in profiles.items():
            if not isinstance(prof, dict):
                continue
            base = DEFAULT_PROFILES.get(name) or DEFAULT_PROFILES["Balanced Performance"]
            for section in ("cpu", "gpu", "fans"):
                if section not in prof:
                    prof[section] = json.loads(json.dumps(base[section]))

    # current_profile must name a profile that exists
    if cfg.get("current_profile") not in cfg["profiles"]:
        cfg["current_profile"] = next(iter(cfg["profiles"]))

    cfg["config_version"] = CONFIG_VERSION
    return cfg


def load_config():
    """Load the user's config, migrating it forward in place. A config that
    cannot be parsed is preserved as a .corrupt-<timestamp> copy rather than
    being silently replaced -- the previous behaviour overwrote it on the
    next save, destroying the user's profiles with no way back."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                return migrate_config(cfg)
            raise ValueError("config is not a JSON object")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            stamp = int(time.time())
            backup = f"{CONFIG_PATH}.corrupt-{stamp}"
            # Two failures in the same second must not overwrite the first
            # backup -- that would lose the very thing we are saving.
            n = 1
            while os.path.exists(backup):
                backup = f"{CONFIG_PATH}.corrupt-{stamp}-{n}"
                n += 1
            try:
                os.replace(CONFIG_PATH, backup)
                print(f"rogcontrol: could not read config ({e}); "
                      f"kept a copy at {backup}", file=sys.stderr)
            except OSError:
                pass
    return migrate_config({})


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


class FanCurveGraph(Gtk.DrawingArea):
    """Draggable temp-vs-fan% curve, with live numeric labels so you can
    actually see what value you're setting. Double-click empty space to
    add a point, double-click a point to remove it (min 2, max 6)."""

    PAD = 26

    def __init__(self, on_change, rpm_cal=None):
        super().__init__()
        self.set_size_request(-1, 160)
        self.points = [(40, 30), (60, 55), (75, 75), (90, 90)]
        self.on_change = on_change
        self.rpm_cal = rpm_cal
        self.show_rpm = False  # False = %, True = RPM -- shared with FanCurveEditor's value label
        self.dragging_index = None
        self.hover_index = None
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_button_press)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("button-release-event", self.on_button_release)

    def set_points(self, points):
        self.points = sorted(tuple(p) for p in points)
        self.queue_draw()

    def _to_pixel(self, temp, pct, w, h):
        pad = self.PAD
        x = pad + (temp / 100) * (w - 2 * pad)
        y = h - pad - (pct / 100) * (h - 2 * pad)
        return x, y

    def _to_value(self, x, y, w, h):
        pad = self.PAD
        temp = (x - pad) / max(1, (w - 2 * pad)) * 100
        pct = (h - pad - y) / max(1, (h - 2 * pad)) * 100
        return max(0, min(100, round(temp))), max(0, min(100, round(pct)))

    def _pct_display(self, pct):
        """Format a 0-100 fan% value as either '{pct}%' or the rpm the fan
        will actually run at. The rpm comes from this channel's measured
        calibration (see FAN_RPM_CAL), not from pct * max_rpm -- fans have
        a hard idle floor around 1650-1750 rpm, so the naive fraction was
        badly wrong at the low end and off by several hundred rpm even in
        the middle of the range."""
        if self.show_rpm and self.rpm_cal:
            return f"{pct_to_rpm(self.rpm_cal, pct)}rpm"
        return f"{pct}%"

    def on_draw(self, widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        pad = self.PAD

        cr.set_source_rgb(0.11, 0.11, 0.12)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        # grid + axis value labels (temp along bottom, % or RPM along left)
        cr.set_source_rgb(0.2, 0.2, 0.22)
        cr.set_line_width(1)
        cr.select_font_face("sans-serif")
        cr.set_font_size(10)
        for i in range(5):
            x = pad + i / 4 * (w - 2 * pad)
            cr.move_to(x, pad); cr.line_to(x, h - pad); cr.stroke()
            cr.set_source_rgb(0.55, 0.55, 0.58)
            cr.move_to(x - 8, h - 8)
            cr.show_text(f"{round(i / 4 * 100)}C")
            cr.set_source_rgb(0.2, 0.2, 0.22)

            y = pad + i / 4 * (h - 2 * pad)
            cr.move_to(pad, y); cr.line_to(w - pad, y); cr.stroke()
            cr.set_source_rgb(0.55, 0.55, 0.58)
            cr.move_to(2, y + 3)
            cr.show_text(self._pct_display(round(100 - i / 4 * 100)))
            cr.set_source_rgb(0.2, 0.2, 0.22)

        pts = sorted(self.points)
        cr.set_source_rgb(0.78, 0.13, 0.16)
        cr.set_line_width(2.5)
        for i, (t, p) in enumerate(pts):
            x, y = self._to_pixel(t, p, w, h)
            if i == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.stroke()

        for i, (t, p) in enumerate(pts):
            x, y = self._to_pixel(t, p, w, h)
            is_active = (i == self.dragging_index) or (i == self.hover_index)
            cr.set_source_rgb(1, 0.95, 0.6) if is_active else cr.set_source_rgb(1, 0.82, 0.4)
            cr.arc(x, y, 7 if is_active else 6, 0, 2 * 3.14159)
            cr.fill()
            # always-visible label next to each point
            cr.set_source_rgb(0.92, 0.92, 0.94)
            cr.set_font_size(11 if is_active else 10)
            label = f"{t}C, {self._pct_display(p)}"
            label_x = min(max(x + 8, pad), w - pad - 60)
            label_y = max(y - 8, pad + 10)
            cr.move_to(label_x, label_y)
            cr.show_text(label)
        return False

    def _find_near_point(self, x, y, w, h, threshold=14):
        for i, (t, p) in enumerate(sorted(self.points)):
            px, py = self._to_pixel(t, p, w, h)
            if (px - x) ** 2 + (py - y) ** 2 <= threshold ** 2:
                return i
        return None

    def on_button_press(self, widget, event):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        idx = self._find_near_point(event.x, event.y, w, h)
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            pts = sorted(self.points)
            if idx is not None and len(pts) > 2:
                del pts[idx]
                self.points = pts
                self.queue_draw()
                self.on_change(self.points, False)
            elif idx is None and len(pts) < 6:
                t, p = self._to_value(event.x, event.y, w, h)
                self.points = sorted(pts + [(t, p)])
                self.queue_draw()
                self.on_change(self.points, False)
            return True
        self.dragging_index = idx
        return True

    def on_motion(self, widget, event):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        if self.dragging_index is None:
            self.hover_index = self._find_near_point(event.x, event.y, w, h)
            self.queue_draw()
            return True
        t, p = self._to_value(event.x, event.y, w, h)
        pts = sorted(self.points)
        if self.dragging_index < len(pts):
            pts[self.dragging_index] = (t, p)
        self.points = pts
        self.queue_draw()
        return True

    def on_button_release(self, widget, event):
        if self.dragging_index is not None:
            self.dragging_index = None
            self.on_change(sorted(self.points), False)
        return True


class FanCurveEditor(Gtk.Box):
    def __init__(self, label, channel, rpm_cal, on_change, initial_show_rpm=False, on_display_pref_changed=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.channel = channel
        self.rpm_cal = rpm_cal
        self.on_change = on_change
        self.on_display_pref_changed = on_display_pref_changed
        self.show_rpm = initial_show_rpm   # shared with the graph; click value to toggle
        self._last_rpm = None

        header = Gtk.Box(spacing=6)
        header.pack_start(Gtk.Label(label=f"<b>{label}</b>", use_markup=True, xalign=0), True, True, 0)

        # Clickable value label: shows RPM or "--%" depending on self.show_rpm.
        # Wrapped in an EventBox since Gtk.Label itself doesn't emit click events.
        self.value_event_box = Gtk.EventBox()
        self.rpm_label = Gtk.Label(label="-- RPM")
        self.value_event_box.add(self.rpm_label)
        self.value_event_box.connect("button-press-event", self.on_value_clicked)
        # Give it a visual hint that it's clickable
        self.value_event_box.set_tooltip_text("Click to switch between RPM and % (also changes the graph below)")
        header.pack_start(self.value_event_box, False, False, 0)
        self.pack_start(header, False, False, 0)

        self.graph = FanCurveGraph(self.on_graph_changed, rpm_cal=rpm_cal)
        self.graph.show_rpm = initial_show_rpm
        self.pack_start(self.graph, False, False, 0)

        self.graph.set_tooltip_text(
            "Drag a point to move it — its temperature and fan speed show live "
            "next to it.\n\n"
            "Double-click empty space to add a point; double-click a point to "
            "remove it. Between 2 and 6 points.")

        self.pack_start(Gtk.Separator(), False, False, 6)

    def on_value_clicked(self, _widget, _event):
        self.show_rpm = not self.show_rpm
        self.graph.show_rpm = self.show_rpm
        self.graph.queue_draw()
        self._refresh_value_label()
        if self.on_display_pref_changed:
            self.on_display_pref_changed(self.show_rpm)

    def _refresh_value_label(self):
        if self.show_rpm:
            if self._last_rpm is not None:
                self.rpm_label.set_text(f"{self._last_rpm} RPM")
            else:
                self.rpm_label.set_text("-- RPM")
        else:
            # The commanded duty cycle isn't reported separately from the
            # rpm reading, so derive it from the live rpm using this
            # channel's measured calibration -- the inverse of what the
            # graph does when showing rpm. A plain rpm/max_rpm fraction
            # (what this used to do) ignores the ~1650-1750 rpm idle floor
            # and reads ~25% high with the fan doing nothing.
            if self._last_rpm is not None and self.rpm_cal:
                self.rpm_label.set_text(f"{rpm_to_pct(self.rpm_cal, self._last_rpm)}%")
            else:
                self.rpm_label.set_text("--%")

    def set_points(self, points):
        self.graph.set_points(points)

    def on_graph_changed(self, points, force):
        self.on_change(self.channel, points, force)

    def update_rpm(self, rpm):
        try:
            self._last_rpm = int(rpm)
        except (TypeError, ValueError):
            self._last_rpm = None
        self._refresh_value_label()

    def update_rpm_error(self, msg):
        self._last_rpm = None
        self.rpm_label.set_text(f"error: {msg}")


class RogControlWindow(Gtk.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application,
                         title=f"ROG Control v{APP_VERSION}")
        self._debounce_sources = {}
        self._last_ac_state = None
        self._last_config_mtime = None
        self._last_cpu_temp = None
        self._last_gpu_temp = None
        self.config = load_config()

        saved_size = self.config.get("window_size") or [600, 700]
        self.set_default_size(int(saved_size[0]), int(saved_size[1]))
        self.set_resizable(True)
        icon_path = os.path.expanduser(
            "~/.local/share/icons/hicolor/256x256/apps/rogcontrol.png")
        if os.path.exists(icon_path):
            self.set_icon_from_file(icon_path)
        else:
            self.set_icon_name("rogcontrol")
        self.connect("delete-event", self.on_close)
        # Debounced save-on-resize: "configure-event" fires continuously
        # while dragging a window edge, so we debounce the actual write to
        # disk rather than hitting the filesystem on every intermediate
        # frame of the resize.
        self.connect("configure-event", self.on_window_configure)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)

        outer.pack_start(self.build_profile_bar(), False, False, 0)
        outer.pack_start(self.build_power_bar(), False, False, 0)

        self.notebook = Gtk.Notebook()
        # Let the tab strip scroll rather than forcing the window to stay
        # wide enough for every tab label at once.
        self.notebook.set_scrollable(True)
        outer.pack_start(self.notebook, True, True, 0)

        for builder, label in (
            (self.build_cpu_tab, "CPU"),
            (self.build_gpu_tab, "GPU"),
            (self.build_fan_tab, "Fans"),
            (self.build_charge_tab, "Charge"),
            (self.build_kbd_tab, "Keyboard"),
            (self.build_system_tab, "System"),
        ):
            self.notebook.append_page(scrollable(builder()), Gtk.Label(label=label))

        self.load_profile_into_ui(self.config["current_profile"], apply_hw=False, notify_change=False)
        self.kbd_scale.set_value(self.config.get("kbd_brightness", 2))
        self.charge_scale.set_value(self.config.get("charge_limit", 100))

        GLib.timeout_add_seconds(2, self.refresh_live_readouts)
        GLib.timeout_add_seconds(5, self.check_external_config_change)

    # -- profile + power bars ---------------------------------------------
    @staticmethod
    def _wrapping_bar(groups, margin=10):
        """Lay out a row of controls so it re-flows onto extra lines when the
        window is narrowed, instead of pinning a minimum width. A plain
        horizontal box demands the sum of its children forever, which is
        what stopped the window being made smaller."""
        flow = Gtk.FlowBox(margin=margin)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_min_children_per_line(1)
        flow.set_max_children_per_line(len(groups))
        flow.set_row_spacing(4)
        flow.set_column_spacing(8)
        flow.set_homogeneous(False)
        for group in groups:
            flow.insert(group, -1)
        return flow

    def build_profile_bar(self):
        left = Gtk.Box(spacing=8)
        left.pack_start(Gtk.Label(label="Profile:"), False, False, 0)
        self.profile_combo = Gtk.ComboBoxText()
        for name in self.config["profiles"]:
            self.profile_combo.append_text(name)
        self._set_combo_active_text(self.profile_combo, self.config["current_profile"])
        # Wide enough for the longest stock name ("Balanced Performance"),
        # so the button itself stays readable rather than relying on the
        # tooltip.
        compact_combo(self.profile_combo, 20)
        self.profile_combo.connect("changed", self.on_profile_selected)
        left.pack_start(self.profile_combo, False, False, 0)

        buttons = Gtk.Box(spacing=8)
        new_btn = Gtk.Button(label="+ New")
        new_btn.connect("clicked", self.on_new_profile)
        buttons.pack_start(new_btn, False, False, 0)
        del_btn = Gtk.Button(label="Delete")
        del_btn.connect("clicked", self.on_delete_profile)
        buttons.pack_start(del_btn, False, False, 0)

        exp_btn = Gtk.Button(label="Export")
        exp_btn.set_tooltip_text(
            "Save the current profile to a file — CPU limits, GPU settings and "
            "fan curves. Useful for backing one up or sharing it.")
        exp_btn.connect("clicked", self.on_export_profile)
        buttons.pack_start(exp_btn, False, False, 0)

        imp_btn = Gtk.Button(label="Import")
        imp_btn.set_tooltip_text(
            "Load a profile from a file. It is added as a new profile; nothing "
            "existing is overwritten without asking.")
        imp_btn.connect("clicked", self.on_import_profile)
        buttons.pack_start(imp_btn, False, False, 0)

        return self._wrapping_bar([left, buttons])

    def build_power_bar(self):
        ac = Gtk.Box(spacing=8)
        ac.pack_start(Gtk.Label(label="On AC use:"), False, False, 0)
        self.ac_combo = Gtk.ComboBoxText()
        self.ac_combo.append_text("(don't auto-switch)")
        for name in self.config["profiles"]:
            self.ac_combo.append_text(name)
        self._set_combo_active_text(self.ac_combo, self.config.get("ac_profile") or "(don't auto-switch)")
        compact_combo(self.ac_combo, 20)
        self.ac_combo.set_tooltip_text("Profile to switch to when mains power is connected")
        self.ac_combo.connect("changed", self.on_ac_profile_changed)
        ac.pack_start(self.ac_combo, False, False, 0)

        batt = Gtk.Box(spacing=8)
        batt.pack_start(Gtk.Label(label="On Battery use:"), False, False, 0)
        self.batt_combo = Gtk.ComboBoxText()
        self.batt_combo.append_text("(don't auto-switch)")
        for name in self.config["profiles"]:
            self.batt_combo.append_text(name)
        self._set_combo_active_text(self.batt_combo, self.config.get("battery_profile") or "(don't auto-switch)")
        compact_combo(self.batt_combo, 20)
        self.batt_combo.set_tooltip_text("Profile to switch to when running on battery")
        self.batt_combo.connect("changed", self.on_batt_profile_changed)
        batt.pack_start(self.batt_combo, False, False, 0)

        return self._wrapping_bar([ac, batt])

    def _set_combo_active_text(self, combo, text):
        model = combo.get_model()
        for i, row in enumerate(model):
            if row[0] == text:
                combo.set_active(i)
                return

    def on_ac_profile_changed(self, combo):
        text = combo.get_active_text()
        self.config["ac_profile"] = None if text == "(don't auto-switch)" else text
        save_config(self.config)

    def on_batt_profile_changed(self, combo):
        text = combo.get_active_text()
        self.config["battery_profile"] = None if text == "(don't auto-switch)" else text
        save_config(self.config)

    def on_tray_profile_toggled(self, item, name):
        # RadioMenuItem fires "toggled" for the item being deselected too,
        # and again when we mirror an external change back into the menu --
        # both would re-trigger a full profile apply.
        if getattr(self, "_tray_updating", False) or not item.get_active():
            return
        if name == self.config.get("current_profile"):
            return
        # Route through the combo so there is exactly one profile-switch
        # path; on_profile_selected does the saving and the hardware apply.
        self._set_combo_active_text(self.profile_combo, name)

    def refresh_tray_profile(self):
        """Point the tray radio at whatever profile is now current, without
        that looking like a user selection."""
        items = getattr(self, "_tray_profile_items", None)
        if not items:
            return
        item = items.get(self.config.get("current_profile"))
        if item is None or item.get_active():
            return
        self._tray_updating = True
        try:
            item.set_active(True)
        finally:
            self._tray_updating = False

    def on_profile_selected(self, combo):
        # Mirroring a change that came from somewhere else (the enforcer
        # adopting an OS power-mode change, or the AC/battery auto-switch)
        # only needs the widgets updated -- the hardware was already set by
        # whoever made the change. Without this the combo update re-ran the
        # whole ~16s apply a second time.
        if getattr(self, "_syncing_profile", False):
            return
        name = combo.get_active_text()
        if name:
            self.config["current_profile"] = name
            save_config(self.config)
            self.refresh_tray_profile()
            # PPD mode MUST be set before the hardware apply, not after.
            # Changing the power profile flips asus-wmi's
            # throttle_thermal_policy, and the EC silently discards the
            # custom fan curve whenever that changes. Applying fans first
            # meant the mode change immediately wiped what we'd just
            # written -- and since the fan apply is now asynchronous
            # (~16s of inter-channel gaps), the old ordering also let
            # set_ppd_mode fire *while* those writes were still going out.
            ppd_mode = PROFILE_TO_PPD_MODE.get(name)
            if ppd_mode:
                set_ppd_mode(ppd_mode)
            self.load_profile_into_ui(name, apply_hw=True, notify_change=True)

    def on_new_profile(self, _btn):
        dialog = Gtk.Dialog(title="New Profile", transient_for=self, modal=True)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                            Gtk.STOCK_OK, Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Profile name")
        box = dialog.get_content_area()
        box.add(entry)
        dialog.show_all()
        response = dialog.run()
        name = entry.get_text().strip()
        dialog.destroy()
        if response == Gtk.ResponseType.OK and name and name not in self.config["profiles"]:
            current = self.current_profile_data()
            self.config["profiles"][name] = json.loads(json.dumps(current))
            self.config["current_profile"] = name
            save_config(self.config)
            self.profile_combo.append_text(name)
            self.ac_combo.append_text(name)
            self.batt_combo.append_text(name)
            self._set_combo_active_text(self.profile_combo, name)

    PROFILE_FILE_VERSION = 1

    def on_export_profile(self, _btn):
        name = self.profile_combo.get_active_text()
        if not name:
            return
        dlg = Gtk.FileChooserDialog(
            title=f"Export profile '{name}'", transient_for=self,
            action=Gtk.FileChooserAction.SAVE)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        dlg.set_do_overwrite_confirmation(True)
        dlg.set_current_name(f"{name.replace('/', '_')}.rogprofile.json")
        resp = dlg.run()
        path = dlg.get_filename()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK or not path:
            return
        payload = {
            "rogcontrol_profile_version": self.PROFILE_FILE_VERSION,
            "exported_by": f"ROG Control v{APP_VERSION}",
            "name": name,
            "profile": self.config["profiles"].get(name, {}),
        }
        try:
            with open(path, "w") as f:
                json.dump(payload, f, indent=2)
            self.set_title(f"ROG Control v{APP_VERSION}")
            log(f"exported profile '{name}' to {path}")
            self._profile_msg(f"Exported '{name}'.")
        except OSError as e:
            log(f"export failed: {e}", "ERROR")
            self._profile_msg(f"Export failed: {e}")

    def on_import_profile(self, _btn):
        dlg = Gtk.FileChooserDialog(
            title="Import profile", transient_for=self,
            action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        resp = dlg.run()
        path = dlg.get_filename()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK or not path:
            return

        # Anything can be in a file the user picked, so validate rather than
        # trusting it -- a malformed profile would otherwise be written into
        # the config and break the app on next launch.
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("not a profile file")
            profile = data.get("profile")
            name = data.get("name") or os.path.basename(path).split(".")[0]
            if not isinstance(profile, dict) or not isinstance(name, str):
                raise ValueError("no profile in this file")
            if not any(k in profile for k in ("cpu", "gpu", "fans")):
                raise ValueError("profile has no cpu, gpu or fans section")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            log(f"import failed: {path}: {e}", "ERROR")
            self._profile_msg(f"Could not import: {e}")
            return

        if name in self.config["profiles"]:
            ask = Gtk.MessageDialog(
                transient_for=self, modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text=f"A profile named '{name}' already exists")
            ask.format_secondary_text(
                "Overwrite it, or import as a copy with a new name?")
            ask.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                            "Import as copy", Gtk.ResponseType.ACCEPT,
                            "Overwrite", Gtk.ResponseType.OK)
            r = ask.run()
            ask.destroy()
            if r == Gtk.ResponseType.CANCEL:
                return
            if r == Gtk.ResponseType.ACCEPT:
                base, n = name, 2
                while f"{base} ({n})" in self.config["profiles"]:
                    n += 1
                name = f"{base} ({n})"

        # Fill in any section the file omitted, so a partial profile cannot
        # produce a half-configured entry.
        base = DEFAULT_PROFILES.get("Balanced Performance")
        for section in ("cpu", "gpu", "fans"):
            profile.setdefault(section, json.loads(json.dumps(base[section])))

        existed = name in self.config["profiles"]
        self.config["profiles"][name] = profile
        save_config(self.config)
        if not existed:
            for combo in (self.profile_combo, self.ac_combo, self.batt_combo):
                combo.append_text(name)
        log(f"imported profile '{name}' from {path}")
        self._profile_msg(f"Imported '{name}'. Select it to apply.")

    def _profile_msg(self, text):
        """Profile import/export feedback goes to the fan status line, which
        is the only always-visible status area."""
        if hasattr(self, "fan_status"):
            self.fan_status.set_text(text)

    def on_delete_profile(self, _btn):
        name = self.profile_combo.get_active_text()
        if name and len(self.config["profiles"]) > 1:
            del self.config["profiles"][name]
            remaining = list(self.config["profiles"].keys())
            self.config["current_profile"] = remaining[0]
            save_config(self.config)
            self.profile_combo.remove_all()
            for n in remaining:
                self.profile_combo.append_text(n)
            self._set_combo_active_text(self.profile_combo, remaining[0])
            self.load_profile_into_ui(remaining[0], apply_hw=True, notify_change=True)

    def current_profile_data(self):
        return self.config["profiles"][self.config["current_profile"]]

    def load_profile_into_ui(self, name, apply_hw, notify_change):
        data = self.config["profiles"].get(name)
        if not data:
            return
        cpu = data.get("cpu", {})
        self.cpu_sliders["stapm"].set_value(cpu.get("stapm", 55000) / 1000)
        self.cpu_sliders["fast"].set_value(cpu.get("fast", 65000) / 1000)
        self.cpu_sliders["slow"].set_value(cpu.get("slow", 55000) / 1000)
        self.cpu_sliders["temp"].set_value(cpu.get("temp", 90))
        self.cpu_sliders["coall"].set_value(cpu.get("coall", 0))
        # Absent means "leave boost alone", which is why the checkbox shows on
        # for a profile that has never set it -- nothing is written until the
        # box is actually touched and applied.
        self.cpu_boost_check.set_active(cpu.get("boost", True))
        # 0 or absent means no ceiling. Absent additionally means "never set",
        # which is what stops old profiles from writing anything.
        cap_khz = cpu.get("max_freq") or 0
        self.cpu_clock_scale.set_value(
            cap_khz / 1e6 if cap_khz else self.cpu_clock_max_ghz)

        gpu = data.get("gpu", {})
        self.gpu_scale.set_value(gpu.get("watts", 100))
        self.gpu_clock_scale.set_value(gpu.get("clock_offset", 0))
        self.gpu_mem_clock_scale.set_value(gpu.get("mem_clock_offset", 0))
        self.gpu_clock_limit_scale.set_value(gpu.get("clock_limit", CLOCK_LIMIT_MAX))
        self.gpu_boost_scale.set_value(gpu.get("dyn_boost", FIRMWARE_DYN_BOOST))
        self.gpu_temp_target_scale.set_value(gpu.get("temp_target", FIRMWARE_TEMP_TARGET))

        fans = data.get("fans", {})
        for channel, editor in self.fan_editors.items():
            editor.set_points(fans.get(channel, DEFAULT_PROFILES["Balanced Performance"]["fans"][channel]))

        if apply_hw:
            # Each apply call is wrapped independently so a single failure
            # (e.g. nvidia-settings not reachable) can't silently abort the
            # rest of the profile switch -- previously an unhandled
            # exception here could stop CPU/fans from applying too, which
            # would look exactly like "profile switch does nothing until I
            # press Apply manually."
            for label, fn in [
                ("cpu", lambda: self.apply_cpu(save=False)),
                ("gpu", lambda: self.apply_gpu(save=False)),
                ("gpu clock limit", lambda: self.apply_gpu_clock_limit(save=False)),
                ("gpu clock", lambda: self.apply_gpu_clock(save=False)),
                ("gpu mem clock", lambda: self.apply_gpu_mem_clock(save=False)),
                ("gpu dynamic boost", lambda: self.apply_gpu_dyn_boost(save=False)),
                ("gpu temp target", lambda: self.apply_gpu_temp_target(save=False)),
            ]:
                try:
                    fn()
                except Exception as e:
                    print(f"Profile switch: {label} apply failed: {e}", file=sys.stderr)
            # Fans are applied asynchronously: the ~16s of inter-channel
            # gaps (see FAN_CHANNEL_GAP_S) would otherwise freeze the window
            # on every profile switch, including the automatic AC/battery
            # ones that happen with no user interaction at all.
            channel_points = [(channel, list(editor.graph.points))
                              for channel, editor in self.fan_editors.items()]

            def fans_done(failures):
                if failures:
                    self.fan_status.set_text("Errors: " + "; ".join(failures))
                else:
                    self.fan_status.set_text("Applied all fan curves.")

            self.fan_status.set_text("Applying fan curves...")
            self.apply_fan_curves_async(channel_points, save=False, on_done=fans_done)
        if notify_change:
            notify("ROG Control", f"Profile switched to {name}")

    # -- debounce ----------------------------------------------------------
    def debounce(self, key, callback, delay_ms=400):
        if key in self._debounce_sources:
            GLib.source_remove(self._debounce_sources[key])

        def fire():
            self._debounce_sources.pop(key, None)
            callback()
            return False

        self._debounce_sources[key] = GLib.timeout_add(delay_ms, fire)

    # -- CPU tab -------------------------------------------------------------
    def build_cpu_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        self.cpu_temp_label = Gtk.Label(label="Temp: -- C", xalign=0)
        box.pack_start(self.cpu_temp_label, False, False, 0)
        self.cpu_clock_label = Gtk.Label(label="Clock: -- MHz", xalign=0)
        box.pack_start(self.cpu_clock_label, False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 4)

        self.cpu_sliders = {}
        specs = [("STAPM limit (W)", "stapm", 15, 150),
                 ("Fast limit (W)", "fast", 15, 165),
                 ("Slow limit (W)", "slow", 15, 150),
                 ("Temp target (C)", "temp", 60, 100)]
        for label, key, lo, hi in specs:
            box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
            adj = Gtk.Adjustment(value=lo, lower=lo, upper=hi, step_increment=1)
            scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
            scale.set_digits(0)
            scale.set_hexpand(True)
            # Preview-only: moving a slider updates the displayed number but
            # does NOT write to hardware. Nothing is applied until "Apply".
            box.pack_start(scale, False, False, 0)
            self.cpu_sliders[key] = scale

        box.pack_start(Gtk.Separator(), False, False, 4)
        COALL_TIP = ("Curve Optimizer, applied to all cores.\n\n"
                     "Negative values undervolt: cooler and often slightly faster, "
                     "since the chip has more thermal headroom to boost. Too far "
                     "negative causes instability or crashes under load — move in "
                     "small steps and test. 0 is stock.")
        coall_label = Gtk.Label(label="Curve Optimizer / undervolt", xalign=0)
        coall_label.set_tooltip_text(COALL_TIP)
        box.pack_start(coall_label, False, False, 0)
        adj = Gtk.Adjustment(value=0, lower=COALL_MIN, upper=COALL_MAX, step_increment=1)
        coall_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        coall_scale.set_digits(0)
        coall_scale.set_tooltip_text(COALL_TIP)
        box.pack_start(coall_scale, False, False, 0)
        self.cpu_sliders["coall"] = coall_scale

        BOOST_TIP = ("Turbo boost. Off pins every core at its base clock.\n\n"
                     "Worth trying if the fans surge at idle: the embedded "
                     "controller reads the raw hottest core, and a boost spike "
                     "hits 85-90 C for a few milliseconds even when the "
                     "reported temperature sits near 57 C — which is enough to "
                     "make the EC jump to the top of your fan curve. Costs "
                     "peak single-thread speed.")
        self.cpu_boost_check = Gtk.CheckButton(label="CPU turbo boost")
        self.cpu_boost_check.set_tooltip_text(BOOST_TIP)
        self.cpu_boost_check.set_active(True)
        box.pack_start(self.cpu_boost_check, False, False, 0)

        CLOCK_TIP = ("Hard ceiling on core clock. Cores still drop as low as "
                     "they like when idle — this only stops them going above "
                     "the limit.\n\n"
                     "Leave it at the top for the stock behaviour.\n\n"
                     "Measured on this laptop: capping at 3.0 GHz stopped the "
                     "idle fan surges completely (fan 2 went from a 1400 rpm "
                     "swing to 100) and cost nothing under sustained load, "
                     "which is power-limited anyway. It costs peak "
                     "single-thread speed.")
        clock_range = CAPS.get("cpu_clock")
        lo_khz, hi_khz = clock_range if clock_range else (400000, 5000000)
        self.cpu_clock_max_ghz = hi_khz / 1e6
        clock_label = Gtk.Label(
            label=f"Max CPU clock (GHz) — {self.cpu_clock_max_ghz:.1f} is default",
            xalign=0)
        clock_label.set_tooltip_text(CLOCK_TIP)
        box.pack_start(clock_label, False, False, 0)
        adj = Gtk.Adjustment(value=self.cpu_clock_max_ghz, lower=lo_khz / 1e6,
                             upper=self.cpu_clock_max_ghz, step_increment=0.1)
        self.cpu_clock_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                         adjustment=adj)
        self.cpu_clock_scale.set_digits(1)
        self.cpu_clock_scale.set_tooltip_text(CLOCK_TIP)
        box.pack_start(self.cpu_clock_scale, False, False, 0)

        cpu_apply_btn = Gtk.Button(label="Apply")
        cpu_apply_btn.connect("clicked", lambda _b: self.apply_cpu())
        box.pack_start(cpu_apply_btn, False, False, 6)

        self.cpu_status = Gtk.Label(label="", xalign=0)
        box.pack_start(self.cpu_status, False, False, 8)

        if not CAPS.get("cpu_boost"):
            disable_widget(self.cpu_boost_check,
                           "no cpufreq boost switch on this machine")
        if not CAPS.get("cpu_clock"):
            disable_widget(self.cpu_clock_scale,
                           "no cpufreq clock limit on this machine")
        if not CAPS.get("ryzenadj"):
            greyed = list(self.cpu_sliders.values())
            # Boost and EPP go through cpufreq, not ryzenadj, so they stay
            # usable on a machine ryzenadj does not support -- which means the
            # Apply button has to stay usable too, or neither could ever be
            # applied.
            if not any(CAPS.get(k) for k in ("cpu_boost", "cpu_epp", "cpu_clock")):
                greyed.append(cpu_apply_btn)
            for w in greyed:
                disable_widget(w, "ryzenadj not installed (AMD Ryzen only)")
            self.cpu_status.set_text(
                "ryzenadj not found — CPU power limits and undervolt unavailable. "
                "Temperature and clock readouts still work.")
        return box

    def apply_cpu(self, save=True):
        stapm = int(self.cpu_sliders["stapm"].get_value()) * 1000
        fast = int(self.cpu_sliders["fast"].get_value()) * 1000
        slow = int(self.cpu_sliders["slow"].get_value()) * 1000
        temp = int(self.cpu_sliders["temp"].get_value())
        coall = int(self.cpu_sliders["coall"].get_value())
        boost = self.cpu_boost_check.get_active()
        # The two halves are independent: boost is a cpufreq switch, the rest
        # is ryzenadj. Reporting and saving them together would mean a machine
        # without ryzenadj could never save a boost preference.
        limits_ok, limits_msg = True, ""
        if CAPS.get("ryzenadj"):
            limits_ok, limits_msg = run_helper("cpu", stapm, fast, slow, temp, coall)
        boost_ok, boost_msg = True, ""
        if CAPS.get("cpu_boost"):
            boost_ok, boost_msg = run_helper("cpuboost", 1 if boost else 0)

        # EPP belongs to the profile, not to this tab: it is applied when the
        # profile becomes active and re-asserted by the enforcer. Applying it
        # here as well keeps the hardware in step when the CPU tab is used
        # without switching profile.
        epp = self.current_profile_data().get("cpu", {}).get("epp")
        epp_ok, epp_msg = True, ""
        if CAPS.get("cpu_epp") and epp:
            epp_ok, epp_msg = run_helper("cpuepp", epp)

        # Clock last: writing cpufreq's boost switch refreshes every policy and
        # takes the ceiling back to hardware max with it, so the cap has to be
        # written after boost, not before.
        cap_khz = 0
        clock_ok, clock_msg = True, ""
        if CAPS.get("cpu_clock"):
            ghz = self.cpu_clock_scale.get_value()
            # Slider at the top means "no limit": the hardware maximum is
            # written back rather than a cap that happens to equal it, so the
            # profile stores 0 and reads as unlimited everywhere.
            if ghz >= self.cpu_clock_max_ghz - 0.05:
                clock_ok, clock_msg = run_helper("cpuclock", "max")
            else:
                cap_khz = int(round(ghz * 1e6))
                clock_ok, clock_msg = run_helper("cpuclock", cap_khz)

        errors = []
        if not limits_ok:
            errors.append(f"limits: {limits_msg}")
        if not boost_ok:
            errors.append(f"boost: {boost_msg}")
        if not epp_ok:
            errors.append(f"EPP: {epp_msg}")
        if not clock_ok:
            errors.append(f"clock limit: {clock_msg}")
        self.cpu_status.set_text("Applied." if not errors else "Error: " + "; ".join(errors))

        if save and (limits_ok or boost_ok or epp_ok or clock_ok):
            data = self.current_profile_data().setdefault("cpu", {})
            if limits_ok:
                data.update({"stapm": stapm, "fast": fast, "slow": slow,
                             "temp": temp, "coall": coall})
            if boost_ok and CAPS.get("cpu_boost"):
                data["boost"] = boost
            if clock_ok and CAPS.get("cpu_clock"):
                # Stored even when it is 0, unlike EPP: 0 means "this profile
                # wants no ceiling" and has to be applied, or switching away
                # from a limited profile would leave its cap behind.
                data["max_freq"] = cap_khz
            save_config(self.config)

    # -- GPU tab (mode + core/mem clock offsets + force-max) ----
    def build_gpu_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        self.gpu_temp_label = Gtk.Label(label="Temp: -- C", xalign=0)
        box.pack_start(self.gpu_temp_label, False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 4)

        box.pack_start(Gtk.Label(label=f"GPU power limit (W) \u2014 range {GPU_MIN_W}-{GPU_MAX_W}", xalign=0), False, False, 0)
        adj = Gtk.Adjustment(value=100, lower=GPU_MIN_W, upper=GPU_MAX_W, step_increment=1)
        self.gpu_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        self.gpu_scale.set_digits(0)
        box.pack_start(self.gpu_scale, False, False, 0)


        box.pack_start(Gtk.Separator(), False, False, 4)
        # Hard ceiling on the core clock. Slider at its maximum means
        # "Default" -- no cap at all -- rather than a 3090MHz lock, since
        # locking even at the stock maximum still pins the clock and stops
        # the GPU boosting or downclocking on its own.
        CLOCK_LIMIT_TIP = (
            "A ceiling, not a target — the GPU still idles and boosts freely "
            "below it, and this does not raise any power or thermal limit.\n\n"
            "Lower it to cut heat and noise. The top of the slider is the "
            "highest clock this GPU reports as lockable and means Default: "
            "no limit is applied at all.")
        self.gpu_clock_limit_label = Gtk.Label(label="Core Clock Limit: Default", xalign=0)
        self.gpu_clock_limit_label.set_tooltip_text(CLOCK_LIMIT_TIP)
        box.pack_start(self.gpu_clock_limit_label, False, False, 0)
        limit_adj = Gtk.Adjustment(value=CLOCK_LIMIT_MAX, lower=CLOCK_LIMIT_MIN,
                                   upper=CLOCK_LIMIT_MAX, step_increment=15)
        self.gpu_clock_limit_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=limit_adj)
        self.gpu_clock_limit_scale.set_digits(0)
        self.gpu_clock_limit_scale.connect("value-changed", self.on_clock_limit_moved)
        self.gpu_clock_limit_scale.set_tooltip_text(CLOCK_LIMIT_TIP)
        box.pack_start(self.gpu_clock_limit_scale, False, False, 0)

        # The offset sliders are a real overclock/underclock: they shift the
        # voltage-frequency curve, so the card draws more power and runs
        # hotter at a given clock. The limit slider above is only a ceiling
        # and cannot do that. Explained on hover rather than as body text,
        # which kept the window tall.
        OFFSET_TIP = (
            "A genuine overclock when positive — this raises the voltage/"
            "frequency curve, so the card draws more power and runs hotter at "
            "the same clock. Unlike Core Clock Limit above, which is only a "
            "ceiling.\n\n"
            "0 is stock. Increase in small steps and test for stability; "
            "too much causes crashes or graphical corruption.")
        core_off_label = Gtk.Label(label="Core Clock Offset (MHz)", xalign=0)
        core_off_label.set_tooltip_text(OFFSET_TIP)
        box.pack_start(core_off_label, False, False, 0)
        clock_adj = Gtk.Adjustment(value=0, lower=CLOCK_MIN, upper=CLOCK_MAX, step_increment=25)
        self.gpu_clock_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=clock_adj)
        self.gpu_clock_scale.set_digits(0)
        self.gpu_clock_scale.set_tooltip_text(OFFSET_TIP)
        box.pack_start(self.gpu_clock_scale, False, False, 0)

        mem_off_label = Gtk.Label(label="VRAM / Memory Clock Offset (MHz)", xalign=0)
        mem_off_label.set_tooltip_text(OFFSET_TIP)
        box.pack_start(mem_off_label, False, False, 0)
        mem_adj = Gtk.Adjustment(value=0, lower=MEM_CLOCK_MIN, upper=MEM_CLOCK_MAX, step_increment=25)
        self.gpu_mem_clock_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=mem_adj)
        self.gpu_mem_clock_scale.set_digits(0)
        self.gpu_mem_clock_scale.set_tooltip_text(OFFSET_TIP)
        box.pack_start(self.gpu_mem_clock_scale, False, False, 0)

        box.pack_start(Gtk.Separator(), False, False, 4)
        BOOST_TIP = (
            f"Extra power ({DYN_BOOST_MIN}-{DYN_BOOST_MAX} W) the firmware may "
            "shift from the CPU to the GPU under load.\n\n"
            "Higher favours the GPU in games; lower leaves more headroom for "
            "the CPU. The range is fixed by the firmware — it cannot go below "
            f"{DYN_BOOST_MIN} W.")
        boost_label = Gtk.Label(label=f"Dynamic Boost (W) — {DYN_BOOST_MIN}-{DYN_BOOST_MAX}", xalign=0)
        boost_label.set_tooltip_text(BOOST_TIP)
        box.pack_start(boost_label, False, False, 0)
        boost_adj = Gtk.Adjustment(value=FIRMWARE_DYN_BOOST, lower=DYN_BOOST_MIN,
                                   upper=DYN_BOOST_MAX, step_increment=1)
        self.gpu_boost_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=boost_adj)
        self.gpu_boost_scale.set_digits(0)
        self.gpu_boost_scale.set_tooltip_text(BOOST_TIP)
        box.pack_start(self.gpu_boost_scale, False, False, 0)

        TT_TIP = (
            f"The temperature ({TEMP_TARGET_MIN}-{TEMP_TARGET_MAX} C) the GPU "
            "aims to hold before it starts reducing clocks.\n\n"
            "Lower runs cooler and quieter but throttles sooner; higher allows "
            "more sustained performance. The range is fixed by the firmware.")
        tt_label = Gtk.Label(label=f"GPU Temperature Target (C) — {TEMP_TARGET_MIN}-{TEMP_TARGET_MAX}", xalign=0)
        tt_label.set_tooltip_text(TT_TIP)
        box.pack_start(tt_label, False, False, 0)
        tt_adj = Gtk.Adjustment(value=FIRMWARE_TEMP_TARGET, lower=TEMP_TARGET_MIN,
                                upper=TEMP_TARGET_MAX, step_increment=1)
        self.gpu_temp_target_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=tt_adj)
        self.gpu_temp_target_scale.set_digits(0)
        self.gpu_temp_target_scale.set_tooltip_text(TT_TIP)
        box.pack_start(self.gpu_temp_target_scale, False, False, 0)

        if not CAPS.get("nv_dynamic_boost"):
            disable_widget(self.gpu_boost_scale, "asus-wmi nv_dynamic_boost not exposed")
        if not CAPS.get("nv_temp_target"):
            disable_widget(self.gpu_temp_target_scale, "asus-wmi nv_temp_target not exposed")
        if not CAPS.get("nvidia"):
            for w in (self.gpu_scale, self.gpu_clock_limit_scale):
                disable_widget(w, "nvidia-smi not installed")
        if not CAPS.get("nvidia_settings"):
            for w in (self.gpu_clock_scale, self.gpu_mem_clock_scale):
                disable_widget(w, "nvidia-settings not installed")

        gpu_apply_btn = Gtk.Button(label="Apply")
        gpu_apply_btn.connect("clicked", self.on_gpu_apply_clicked)
        box.pack_start(gpu_apply_btn, False, False, 6)

        self.gpu_status = Gtk.Label(label="", xalign=0)
        box.pack_start(self.gpu_status, False, False, 8)

        box.pack_start(Gtk.Separator(), False, False, 4)
        box.pack_start(Gtk.Label(label="GPU Mode (requires logout/reboot to fully apply)", xalign=0), False, False, 0)
        group = None
        self.gpumode_radios = {}
        for mode in GPU_MODES:
            rb = Gtk.RadioButton.new_with_label_from_widget(group, mode)
            if group is None:
                group = rb
            rb.connect("toggled", self.on_gpumode_change, mode)
            box.pack_start(rb, False, False, 0)
            self.gpumode_radios[mode] = rb
        self.gpumode_status = Gtk.Label(label="", xalign=0)
        box.pack_start(self.gpumode_status, False, False, 4)
        if not CAPS.get("supergfxctl"):
            for rb in self.gpumode_radios.values():
                disable_widget(rb, "supergfxctl not installed")
            self.gpumode_status.set_text("supergfxctl not installed — GPU mode switching unavailable.")
        return box

    def on_clock_limit_moved(self, scale):
        mhz = int(scale.get_value())
        self.gpu_clock_limit_label.set_text(
            "Core Clock Limit: Default" if mhz >= CLOCK_LIMIT_MAX
            else f"Core Clock Limit: {mhz} MHz")

    def on_gpu_apply_clicked(self, _btn):
        self.apply_gpu()
        self.apply_gpu_clock_limit()
        self.apply_gpu_clock()
        self.apply_gpu_mem_clock()
        self.apply_gpu_dyn_boost()
        self.apply_gpu_temp_target()

    def apply_gpu_clock_limit(self, save=True):
        mhz = int(self.gpu_clock_limit_scale.get_value())
        # Top of the slider means "no cap", which is a reset rather than a
        # lock at the stock maximum -- locking there would still pin the
        # clock and prevent normal boost/idle behaviour.
        arg = "reset" if mhz >= CLOCK_LIMIT_MAX else mhz
        ok, msg = run_helper("gpuclocklimit", arg)
        self.gpu_status.set_text(
            ("Core clock limit removed." if arg == "reset" else f"Core clock limited to {mhz} MHz.")
            if ok else f"Core clock limit error: {msg}")
        if ok and save:
            self.current_profile_data().setdefault("gpu", {})["clock_limit"] = mhz
            save_config(self.config)

    def apply_gpu_dyn_boost(self, save=True):
        watts = int(self.gpu_boost_scale.get_value())
        ok, msg = run_helper("nvboost", watts)
        self.gpu_status.set_text(f"Dynamic Boost {watts}W applied." if ok
                                 else f"Dynamic Boost error: {msg}")
        if ok and save:
            self.current_profile_data().setdefault("gpu", {})["dyn_boost"] = watts
            save_config(self.config)

    def apply_gpu_temp_target(self, save=True):
        temp = int(self.gpu_temp_target_scale.get_value())
        ok, msg = run_helper("nvtemp", temp)
        self.gpu_status.set_text(f"GPU temp target {temp}C applied." if ok
                                 else f"GPU temp target error: {msg}")
        if ok and save:
            self.current_profile_data().setdefault("gpu", {})["temp_target"] = temp
            save_config(self.config)

    def apply_gpu(self, save=True):
        watts = int(self.gpu_scale.get_value())
        ok, msg = run_helper("gpu", watts)
        self.gpu_status.set_text("Applied." if ok else f"Error: {msg}")
        if ok and save:
            self.current_profile_data().setdefault("gpu", {})["watts"] = watts
            save_config(self.config)


    def apply_gpu_clock(self, save=True):
        mhz = int(self.gpu_clock_scale.get_value())
        try:
            result = subprocess.run(
                ["nvidia-settings", "-a",
                 f"[gpu:0]/GPUGraphicsClockOffsetAllPerformanceLevels={mhz}"],
                capture_output=True, text=True, timeout=10)
            ok = result.returncode == 0
            msg = (result.stderr or result.stdout).strip()
        except Exception as e:
            ok, msg = False, str(e)
        self.gpu_status.set_text("Core clock offset applied." if ok else f"Core clock error: {msg}")
        if ok and save:
            self.current_profile_data().setdefault("gpu", {})["clock_offset"] = mhz
            save_config(self.config)

    def apply_gpu_mem_clock(self, save=True):
        mhz = int(self.gpu_mem_clock_scale.get_value())
        try:
            result = subprocess.run(
                ["nvidia-settings", "-a",
                 f"[gpu:0]/GPUMemoryTransferRateOffsetAllPerformanceLevels={mhz}"],
                capture_output=True, text=True, timeout=10)
            ok = result.returncode == 0
            msg = (result.stderr or result.stdout).strip()
        except Exception as e:
            ok, msg = False, str(e)
        self.gpu_status.set_text("Memory clock offset applied." if ok else f"Memory clock error: {msg}")
        if ok and save:
            self.current_profile_data().setdefault("gpu", {})["mem_clock_offset"] = mhz
            save_config(self.config)

    def on_gpumode_change(self, rb, mode):
        if not rb.get_active():
            return
        if getattr(self, "_syncing_gpumode_radio", False):
            return  # programmatic selection to reflect current state, not a user click
        try:
            result = subprocess.run(["supergfxctl", "-m", mode], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                self.gpumode_status.set_text(f"Switched to {mode}. Log out/reboot to fully apply.")
            else:
                self.gpumode_status.set_text(f"Error: {(result.stderr or result.stdout).strip()}")
        except Exception as e:
            self.gpumode_status.set_text(f"Error: {e}")

    def sync_gpumode_radio_to_active(self, active_mode_text):
        """Select the radio button matching the currently active GPU mode
        (as reported by supergfxctl -g) without firing another mode-switch
        command back at supergfxd. Matching is case-insensitive since
        supergfxctl's exact output casing isn't guaranteed to match our
        GPU_MODES constant strings."""
        active_normalized = active_mode_text.strip().lower()
        rb = None
        for mode, candidate in self.gpumode_radios.items():
            if mode.lower() == active_normalized:
                rb = candidate
                break
        if rb is None or rb.get_active():
            return
        self._syncing_gpumode_radio = True
        rb.set_active(True)
        self._syncing_gpumode_radio = False

    # -- Fan tab -------------------------------------------------------------
    def build_fan_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12)
        self.fan_editors = {}
        saved_show_rpm = self.config.get("fan_display_unit", "percent") == "rpm"
        for channel, name in FAN_CHANNELS.items():
            editor = FanCurveEditor(
                name, channel, get_rpm_cal(self.config, channel), self.on_fan_curve_changed,
                initial_show_rpm=saved_show_rpm,
                on_display_pref_changed=self.on_fan_display_pref_changed)
            box.pack_start(editor, False, False, 0)
            self.fan_editors[channel] = editor

        btn_row = Gtk.Box(spacing=8)
        apply_all_btn = Gtk.Button(label="Apply All Fan Curves")
        apply_all_btn.connect("clicked", self.on_apply_all_fans_clicked)
        btn_row.pack_start(apply_all_btn, True, True, 0)

        self.calibrate_btn = Gtk.Button(label="Calibrate fan RPM")
        self.calibrate_btn.set_tooltip_text(
            "Measures how this machine's fans actually respond, so the RPM "
            "numbers shown are real rather than estimated. Takes about two "
            "minutes and the fans will audibly speed up and down.")
        self.calibrate_btn.connect("clicked", self.on_calibrate_clicked)
        btn_row.pack_start(self.calibrate_btn, False, False, 0)
        box.pack_start(btn_row, False, False, 6)

        self.fan_status = Gtk.Label(label="", xalign=0)
        self.fan_status.set_line_wrap(True)
        box.pack_start(self.fan_status, False, False, 4)

        if not CAPS.get("fan_curve"):
            for w in (apply_all_btn, self.calibrate_btn):
                disable_widget(w, "asus_custom_fan_curve hwmon not present")
            for ed in self.fan_editors.values():
                disable_widget(ed, "asus_custom_fan_curve hwmon not present")
            self.fan_status.set_text(
                "This kernel/model does not expose asus_custom_fan_curve, "
                "so fan curves cannot be set.")
        elif not self.config.get("fan_rpm_cal"):
            # Never calibrated: the displayed RPM is from the developer's
            # machine and will be wrong here to some degree. Say so rather
            # than presenting borrowed numbers as measured fact.
            self.fan_status.set_text(
                "RPM values shown are estimates from the developer's laptop. "
                "Run “Calibrate fan RPM” once to measure your own.")
        return box

    # -- fan RPM calibration -------------------------------------------------
    CAL_PERCENTS = (20, 45, 70)   # spread across the usable range
    CAL_SETTLE_S = 22             # fans need ~20s to reach a new steady speed

    def on_calibrate_clicked(self, btn):
        dlg = Gtk.MessageDialog(
            transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL, text="Calibrate fan RPM?")
        dlg.format_secondary_text(
            "This measures how your fans actually respond so the RPM figures "
            "shown are yours, not estimates.\n\n"
            "• Takes about two minutes\n"
            "• The fans will audibly speed up and slow down\n"
            "• The background enforcer is paused, then restarted\n"
            "• Your saved fan curves are not modified and are restored at the end\n\n"
            "Best run while the machine is idle.")
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK:
            return

        btn.set_sensitive(False)
        self.fan_status.set_text("Calibrating… starting")
        threading.Thread(target=self._calibration_worker, daemon=True).start()

    def _cal_progress(self, text):
        GLib.idle_add(self.fan_status.set_text, text)

    def _calibration_worker(self):
        """Runs off the GTK thread. Drives all three channels to the same
        flat percentage at once (one settle wait covers every fan instead of
        one per fan), reads the resulting RPM, and fits floor+slope."""
        samples = {ch: [] for ch in FAN_CHANNELS}
        enforcer_paused = False
        try:
            # The enforcer would re-push the real curve mid-measurement.
            if subprocess.run(["systemctl", "--user", "stop",
                               "rogcontrol-enforcer.service"],
                              capture_output=True).returncode == 0:
                enforcer_paused = True

            total = len(self.CAL_PERCENTS)
            for idx, pct in enumerate(self.CAL_PERCENTS, 1):
                pwm = pct_to_pwm255(pct)
                for i, channel in enumerate(FAN_CHANNELS):
                    if i > 0:
                        time.sleep(FAN_CHANNEL_GAP_S)
                    flat = []
                    for t in (30, 40, 50, 55, 60, 65, 70, 90):
                        flat += [t, pwm]
                    run_helper("fan", channel, *flat)
                for remaining in range(self.CAL_SETTLE_S, 0, -1):
                    self._cal_progress(
                        f"Calibrating… step {idx} of {total} ({pct}% fan) — "
                        f"settling, {remaining}s")
                    time.sleep(1)
                for channel in FAN_CHANNELS:
                    samples[channel].append((pct, self._read_fan_rpm(channel)))

            cal = {}
            failed = []
            for channel in FAN_CHANNELS:
                fit = fit_rpm_cal(samples[channel])
                name = FAN_CHANNELS.get(channel, channel)
                if fit:
                    cal[channel] = [fit[0], fit[1]]
                else:
                    failed.append(name)
            GLib.idle_add(self._calibration_done, cal, failed, samples)
        except Exception as e:
            GLib.idle_add(self._calibration_failed, str(e))
        finally:
            if enforcer_paused:
                subprocess.run(["systemctl", "--user", "start",
                                "rogcontrol-enforcer.service"],
                               capture_output=True)

    def _read_fan_rpm(self, channel):
        asus = find_hwmon_by_name("asus")
        if not asus:
            return None
        val = read_file(os.path.join(asus, f"fan{channel}_input"))
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    def _calibration_done(self, cal, failed, samples):
        self.calibrate_btn.set_sensitive(True)
        if not cal:
            self.fan_status.set_text(
                "Calibration failed — no fan responded measurably. "
                "Previous values kept.")
            return False
        self.config["fan_rpm_cal"] = cal
        save_config(self.config)
        # Push the new calibration into the live widgets
        for channel, editor in self.fan_editors.items():
            new = get_rpm_cal(self.config, channel)
            editor.rpm_cal = new
            editor.graph.rpm_cal = new
            editor.graph.queue_draw()
            editor._refresh_value_label()
        parts = []
        for channel in FAN_CHANNELS:
            if channel in cal:
                floor, slope = cal[channel]
                parts.append(f"{FAN_CHANNELS[channel]}: {round(floor)}–"
                             f"{round(floor + slope * 100)} rpm")
        msg = "Calibrated. " + "; ".join(parts)
        if failed:
            msg += f" (no usable reading from: {', '.join(failed)} — kept previous)"
        self.fan_status.set_text(msg)
        return False

    def _calibration_failed(self, err):
        self.calibrate_btn.set_sensitive(True)
        self.fan_status.set_text(f"Calibration error: {err}. Previous values kept.")
        return False

    def on_fan_curve_changed(self, channel, points, force):
        # Dragging a point still live-previews to hardware (debounced) so you
        # can see/hear the effect immediately, but no longer auto-saves to
        # the profile on every drag -- saving now happens for all channels
        # together via the single "Apply All Fan Curves" button.
        self.debounce(f"fan{channel}", lambda: self.apply_fan(channel, points, save=False))

    def on_fan_display_pref_changed(self, show_rpm):
        # One shared RPM/% choice across all three fan editors -- clicking
        # the value on any single fan updates all of them together, and the
        # choice is remembered across restarts.
        for editor in self.fan_editors.values():
            if editor.show_rpm != show_rpm:
                editor.show_rpm = show_rpm
                editor.graph.show_rpm = show_rpm
                editor.graph.queue_draw()
                editor._refresh_value_label()
        self.config["fan_display_unit"] = "rpm" if show_rpm else "percent"
        save_config(self.config)

    def apply_fan_curves_async(self, channel_points, save=True, on_done=None,
                               force=False):
        """Applies fan curves for several channels off the GTK main thread.

        Each channel needs an 8s gap from the next (see FAN_CHANNEL_GAP_S),
        so applying all three takes ~16s -- long enough that doing it
        synchronously visibly freezes the window. The helper calls run in a
        background thread; only the status-label update and config save are
        marshalled back to the main thread via GLib.idle_add, since GTK
        widgets must not be touched from a worker thread."""
        def worker():
            results = []
            # Skip channels the firmware already holds, so switching between
            # profiles with matching curves is instant instead of ~16s.
            todo = [(c, p) for c, p in channel_points
                    if not (force is False and fan_curve_already_set(c, p))]
            skipped = len(channel_points) - len(todo)
            for c, p in channel_points:
                if (c, p) not in todo:
                    results.append((c, p, True, "already set"))
            if skipped and not todo:
                GLib.idle_add(lambda: (self.fan_status.set_text(
                    "Fan curves already applied — nothing to change."), False)[1])
            for i, (channel, points) in enumerate(todo):
                if i > 0:
                    time.sleep(FAN_CHANNEL_GAP_S)
                if len(points) < 2:
                    results.append((channel, points, False, "need at least 2 points"))
                    continue
                expanded = interpolate_curve(points, 8)
                flat = []
                for t, pct in expanded:
                    flat.append(t)
                    flat.append(pct_to_pwm255(pct))
                ok, msg = run_helper("fan", channel, *flat)
                results.append((channel, points, ok, msg))
            GLib.idle_add(finish, results)

        def finish(results):
            failures = []
            for channel, points, ok, msg in results:
                if ok and save:
                    self.current_profile_data().setdefault("fans", {})[channel] = points
                elif not ok:
                    failures.append(f"{FAN_CHANNELS.get(channel, channel)}: {msg}")
            if save:
                save_config(self.config)
            if on_done:
                on_done(failures)
            return False  # one-shot idle callback

        threading.Thread(target=worker, daemon=True).start()

    def on_apply_all_fans_clicked(self, btn):
        channel_points = [(channel, list(editor.graph.points))
                          for channel, editor in self.fan_editors.items()]
        total_s = FAN_CHANNEL_GAP_S * max(0, len(channel_points) - 1)
        btn.set_sensitive(False)
        self.fan_status.set_text(
            f"Applying fan curves... (~{total_s}s — each fan is set "
            "separately so the firmware doesn't drop one)")

        def done(failures):
            btn.set_sensitive(True)
            if failures:
                self.fan_status.set_text("Errors: " + "; ".join(failures))
            else:
                self.fan_status.set_text("Applied all fan curves.")

        self.apply_fan_curves_async(channel_points, save=True, on_done=done)

    def apply_fan(self, channel, points, save=True, return_result=False):
        if len(points) < 2:
            if return_result:
                return False, "need at least 2 points"
            return
        expanded = interpolate_curve(points, 8)
        flat = []
        for t, pct in expanded:
            flat.append(t)
            flat.append(pct_to_pwm255(pct))
        ok, msg = run_helper("fan", channel, *flat)
        name = FAN_CHANNELS.get(channel, channel)
        if not return_result:
            self.fan_status.set_text(f"{name}: {'Applied.' if ok else 'Error: ' + msg}")
        if ok and save:
            self.current_profile_data().setdefault("fans", {})[channel] = points
            save_config(self.config)
        if return_result:
            return ok, msg

    # -- Charge tab ----------------------------------------------------------
    def build_charge_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        CHARGE_TIP = ("Caps charging at this percentage to help preserve battery "
                      "lifespan.\n\nThis is independent of profiles — it applies "
                      "whichever profile is active. 100% disables the cap.")
        charge_label = Gtk.Label(label="Battery charge limit (%)", xalign=0)
        charge_label.set_tooltip_text(CHARGE_TIP)
        box.pack_start(charge_label, False, False, 0)
        adj = Gtk.Adjustment(value=100, lower=0, upper=100, step_increment=1)
        self.charge_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        self.charge_scale.set_digits(0)
        self.charge_scale.set_tooltip_text(CHARGE_TIP)
        self.charge_scale.connect("value-changed", lambda _s: self.debounce("charge", self.apply_charge))
        box.pack_start(self.charge_scale, False, False, 0)
        self.charge_status = Gtk.Label(label="", xalign=0)
        box.pack_start(self.charge_status, False, False, 8)
        if not CAPS.get("charge_limit"):
            disable_widget(self.charge_scale, "no charge_control_end_threshold on this battery")
            self.charge_status.set_text(
                "This machine does not expose a charge limit threshold.")
        return box

    def apply_charge(self):
        pct = int(self.charge_scale.get_value())
        ok, msg = run_helper("charge", pct)
        self.charge_status.set_text("Applied." if ok else f"Error: {msg}")
        if ok:
            self.config["charge_limit"] = pct
            save_config(self.config)

    # -- Keyboard tab (brightness unchanged + experimental RGB modes) --------
    def build_kbd_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        box.pack_start(Gtk.Label(label=f"Keyboard backlight ({KBD_MIN}-{KBD_MAX})", xalign=0), False, False, 0)
        adj = Gtk.Adjustment(value=2, lower=KBD_MIN, upper=KBD_MAX, step_increment=1)
        self.kbd_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        self.kbd_scale.set_digits(0)
        self.kbd_scale.connect("value-changed", self.on_kbd_change)
        box.pack_start(self.kbd_scale, False, False, 0)
        self.kbd_status = Gtk.Label(label="", xalign=0)
        box.pack_start(self.kbd_status, False, False, 0)

        box.pack_start(Gtk.Separator(), False, False, 8)
        rgb_label = Gtk.Label(label="RGB", xalign=0)
        rgb_label.set_tooltip_text(
            "Keyboard RGB colours and effects, applied via rogauracore.\n\n"
            "Works only on ASUS keyboard controllers rogauracore recognises; "
            "run 'rogauracore' in a terminal to see the devices it supports.")
        box.pack_start(rgb_label, False, False, 0)

        self.kbd_mode_combo = Gtk.ComboBoxText()
        for name in supported_kbd_modes():
            self.kbd_mode_combo.append_text(name)
        # Show the mode that is actually applied, not always the first entry.
        # Set before the "changed" handler is connected, so restoring the
        # saved value doesn't count as a user change and re-push it.
        saved_rgb = self.config.get("kbd_rgb", {})
        self._set_combo_active_text(
            self.kbd_mode_combo, saved_rgb.get("mode") or "Static")
        # A mode this build does not know about (an older config, or one from
        # a newer build) leaves nothing selected, so fall back to the first.
        if self.kbd_mode_combo.get_active() < 0:
            self.kbd_mode_combo.set_active(0)
        compact_combo(self.kbd_mode_combo, 16)
        self.kbd_mode_combo.connect("changed", self.on_kbd_mode_combo_changed)
        box.pack_start(self.kbd_mode_combo, False, False, 0)

        color_box = Gtk.FlowBox()
        color_box.set_selection_mode(Gtk.SelectionMode.NONE)
        color_box.set_min_children_per_line(1)
        color_box.set_max_children_per_line(3)
        color_box.set_column_spacing(6)
        color_box.set_row_spacing(4)
        # Seeded from the saved config at creation time -- before any spin
        # button is connected -- so restoring them cannot trigger an apply.
        self.kbd_r = Gtk.Adjustment(value=saved_rgb.get("r", 255), lower=0, upper=255, step_increment=1)
        self.kbd_g = Gtk.Adjustment(value=saved_rgb.get("g", 0), lower=0, upper=255, step_increment=1)
        self.kbd_b = Gtk.Adjustment(value=saved_rgb.get("b", 0), lower=0, upper=255, step_increment=1)
        for label, adj in [("R", self.kbd_r), ("G", self.kbd_g), ("B", self.kbd_b)]:
            cell = Gtk.Box(spacing=4)
            cell.pack_start(Gtk.Label(label=label), False, False, 0)
            spin = Gtk.SpinButton(adjustment=adj)
            spin.connect("value-changed", lambda _s: self.debounce("kbdrgb", self.apply_kbd_rgb))
            cell.pack_start(spin, False, False, 0)
            color_box.insert(cell, -1)
        box.pack_start(color_box, False, False, 0)

        # Second color, only meaningful for the Breathing mode
        self.kbd_color2_box = Gtk.FlowBox()
        self.kbd_color2_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.kbd_color2_box.set_min_children_per_line(1)
        self.kbd_color2_box.set_max_children_per_line(4)
        self.kbd_color2_box.set_column_spacing(6)
        self.kbd_color2_box.set_row_spacing(4)
        self.kbd_r2 = Gtk.Adjustment(value=saved_rgb.get("r2", 0), lower=0, upper=255, step_increment=1)
        self.kbd_g2 = Gtk.Adjustment(value=saved_rgb.get("g2", 0), lower=0, upper=255, step_increment=1)
        self.kbd_b2 = Gtk.Adjustment(value=saved_rgb.get("b2", 255), lower=0, upper=255, step_increment=1)
        c2_label = Gtk.Label(label="2nd colour:", xalign=0)
        c2_label.set_tooltip_text(
            "Used by Breathing (fades between the two colours) and Gradient "
            "Static (the two colours are blended across the keyboard zones).")
        self.kbd_color2_box.insert(c2_label, -1)
        for label, adj in [("R", self.kbd_r2), ("G", self.kbd_g2), ("B", self.kbd_b2)]:
            cell = Gtk.Box(spacing=4)
            cell.pack_start(Gtk.Label(label=label), False, False, 0)
            spin = Gtk.SpinButton(adjustment=adj)
            spin.connect("value-changed", lambda _s: self.debounce("kbdrgb", self.apply_kbd_rgb))
            cell.pack_start(spin, False, False, 0)
            self.kbd_color2_box.insert(cell, -1)
        box.pack_start(self.kbd_color2_box, False, False, 0)
        self.kbd_color2_box.set_sensitive(
            (saved_rgb.get("mode") or "Static")
            in ("Breathing", "Gradient Static"))

        box.pack_start(Gtk.Label(label="Speed (1=slow, 3=fast) — Breathing, Pulse, Color Cycle, Rainbow", xalign=0), False, False, 0)
        speed_adj = Gtk.Adjustment(value=saved_rgb.get("speed", 2), lower=1, upper=3, step_increment=1)
        self.kbd_speed_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=speed_adj)
        self.kbd_speed_scale.set_digits(0)
        self.kbd_speed_scale.connect("value-changed", lambda _s: self.debounce("kbdrgb", self.apply_kbd_rgb))
        box.pack_start(self.kbd_speed_scale, False, False, 0)

        self.kbd_rgb_status = Gtk.Label(label="", xalign=0)
        self.kbd_rgb_status.set_text(
            f"Current mode: {self.kbd_mode_combo.get_active_text() or 'Static'}")
        box.pack_start(self.kbd_rgb_status, False, False, 0)

        # Keyboard controls are deliberately NEVER disabled by capability
        # detection, unlike the other tabs. Detection runs once at startup,
        # and the keyboard interfaces are the ones most likely to be absent
        # at that instant but fine moments later -- asus-nb-wmi may still be
        # settling, or the USB controller may not have enumerated yet. A
        # control greyed out on a bad guess stays greyed out for the whole
        # session with no way to retry, which is worse than a control that
        # tries and reports what went wrong.
        #
        # So they stay live and any problem is reported when used. Missing
        # pieces are only hinted at here.
        if not CAPS.get("kbd_backlight"):
            self.kbd_status.set_text(
                "No asus::kbd_backlight LED was found at startup — brightness "
                "may not work, but the control is still enabled in case it "
                "appeared later.")
        if not CAPS.get("rogauracore"):
            self.kbd_rgb_status.set_text(
                "rogauracore was not found at startup — RGB colours and modes "
                "need it. Controls left enabled; install rogauracore if they "
                "report an error.")
        return box

    # -- System tab ------------------------------------------------------------
    def build_system_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)

        status_row = Gtk.Box(spacing=8)
        heading = Gtk.Label(label="asusd status:", xalign=0)
        heading.set_tooltip_text(
            "asusd (the asusctl daemon) can conflict with this app, since both "
            "try to manage the same hardware.\n\n"
            "This does not affect supergfxd, which you manage separately.")
        status_row.pack_start(heading, False, False, 0)
        self.asusd_status_label = Gtk.Label(label="checking...", xalign=0)
        status_row.pack_start(self.asusd_status_label, False, False, 0)
        box.pack_start(status_row, False, False, 4)

        # All four actions together, in a row that re-flows when narrow.
        self.asusd_disable_btn = Gtk.Button(label="Disable asusd")
        self.asusd_disable_btn.set_tooltip_text(
            "Stops asusd now and at boot, but leaves it installed. Reversible.")
        self.asusd_disable_btn.connect("clicked", self.on_asusd_disable_clicked)

        self.asusd_enable_btn = Gtk.Button(label="Enable asusd")
        self.asusd_enable_btn.set_tooltip_text("Starts asusd and enables it at boot")
        self.asusd_enable_btn.connect("clicked", self.on_asusd_enable_clicked)

        refresh_btn = Gtk.Button(label="Refresh Status")
        refresh_btn.set_tooltip_text("Re-check whether asusd is installed and running")
        refresh_btn.connect("clicked", lambda _b: self.refresh_asusd_status())

        self.asusd_remove_btn = Gtk.Button(label="Uninstall asusctl")
        self.asusd_remove_btn.set_tooltip_text(
            "Removes the asusctl package from your system entirely.\n\n"
            "Disabling only stops asusd for now — it stays installed and a "
            "package update can start it again. Uninstalling is permanent and "
            "is confirmed first. supergfxd is a separate package and is not "
            "touched.")
        self.asusd_remove_btn.connect("clicked", self.on_asusd_remove_clicked)

        box.pack_start(self._wrapping_bar(
            [self.asusd_disable_btn, self.asusd_enable_btn,
             refresh_btn, self.asusd_remove_btn], margin=0), False, False, 0)

        self.asusd_status_msg = Gtk.Label(label="", xalign=0)
        self.asusd_status_msg.set_line_wrap(True)
        box.pack_start(self.asusd_status_msg, False, False, 4)

        GLib.idle_add(self.refresh_asusd_status)
        return box

    def refresh_asusd_status(self):
        ok, output = run_helper("asusd_status")
        status = output.strip() if ok else "error"
        labels = {
            "active": "Active (running)",
            "inactive-enabled": "Inactive, but enabled at boot",
            "inactive-disabled": "Disabled",
            "not-installed": "Not installed",
            "error": f"Error checking status: {output}",
        }
        self.asusd_status_label.set_text(labels.get(status, status))
        can_toggle = status in ("active", "inactive-enabled", "inactive-disabled")
        self.asusd_disable_btn.set_sensitive(can_toggle and status != "inactive-disabled")
        self.asusd_enable_btn.set_sensitive(can_toggle and status != "active" and status != "inactive-enabled")
        # Nothing to uninstall if it was never there.
        if hasattr(self, "asusd_remove_btn"):
            self.asusd_remove_btn.set_sensitive(status != "not-installed")
            if status == "not-installed":
                self.asusd_remove_btn.set_tooltip_text("asusctl is not installed")
        return False  # for GLib.idle_add one-shot

    def on_asusd_remove_clicked(self, btn):
        # Uninstalling a package is not undoable from here, so it is asked
        # about explicitly rather than happening on a single click.
        dlg = Gtk.MessageDialog(
            transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE, text="Uninstall asusctl and asusd?")
        dlg.format_secondary_text(
            "This removes the asusctl package from your system, not just the "
            "running service.\n\n"
            "• Anything else relying on asusctl will stop working\n"
            "• Reinstalling means fetching the package again\n"
            "• supergfxd is a separate package and is not touched\n\n"
            "If you only want it out of the way for now, use \"Disable asusd\" "
            "instead — that is reversible.")
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                        "Uninstall", Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK:
            return

        btn.set_sensitive(False)
        self.asusd_status_msg.set_text("Uninstalling asusctl…")

        def worker():
            ok, msg = run_helper("asusd_remove")
            GLib.idle_add(done, ok, msg)

        def done(ok, msg):
            btn.set_sensitive(True)
            if ok:
                self.asusd_status_msg.set_text(
                    msg.strip() if "not installed" in msg else "asusctl uninstalled.")
            else:
                # Most likely another installed package still requires it,
                # which the package manager refuses to break.
                self.asusd_status_msg.set_text(f"Could not uninstall: {msg}")
            self.refresh_asusd_status()
            return False

        threading.Thread(target=worker, daemon=True).start()

    def on_asusd_disable_clicked(self, _btn):
        ok, msg = run_helper("asusd_disable")
        self.asusd_status_msg.set_text("Disabled." if ok else f"Error: {msg}")
        self.refresh_asusd_status()

    def on_asusd_enable_clicked(self, _btn):
        ok, msg = run_helper("asusd_enable")
        self.asusd_status_msg.set_text("Enabled." if ok else f"Error: {msg}")
        self.refresh_asusd_status()

    KBD_BACKLIGHT_PATH = "/sys/class/leds/asus::kbd_backlight/brightness"

    def sync_kbd_brightness_from_hardware(self):
        """Follow the real backlight level, so the slider matches reality.

        The brightness can be changed from outside this app -- the keyboard's
        own Fn keys, GNOME's quick-settings slider, or our own shortcut
        script. Previously the slider was set once at startup and never
        looked again, so any of those left it showing a stale value. This
        reads the LED back and moves the slider to match.

        The guard flag matters: set_value() emits "value-changed", which
        would call on_kbd_change and write the value straight back to the
        hardware, so without it a hardware-originated change would bounce
        back as an app-originated one."""
        raw = read_file(self.KBD_BACKLIGHT_PATH)
        try:
            level = int(raw)
        except (TypeError, ValueError):
            return  # no LED node, or unreadable -- leave the slider alone
        if level == int(self.kbd_scale.get_value()):
            return
        self._syncing_kbd_scale = True
        try:
            self.kbd_scale.set_value(level)
        finally:
            self._syncing_kbd_scale = False
        # Keep the stored value in step, or the enforcer would push the old
        # brightness back on its next cycle and undo the external change.
        if self.config.get("kbd_brightness") != level:
            self.config["kbd_brightness"] = level
            save_config(self.config)

    def on_kbd_change(self, scale):
        if getattr(self, "_syncing_kbd_scale", False):
            return  # moved by the hardware sync above, not by the user
        level = int(scale.get_value())
        ok, msg = run_helper("kbd", level)
        self.kbd_status.set_text("Applied." if ok else f"Error: {msg}")
        if ok:
            self.config["kbd_brightness"] = level
            save_config(self.config)

    def on_kbd_mode_combo_changed(self, combo):
        mode_name = combo.get_active_text()
        # Only the two-colour modes actually use the second colour.
        self.kbd_color2_box.set_sensitive(
            mode_name in ("Breathing", "Gradient Static"))
        self.apply_kbd_rgb()

    def apply_kbd_rgb(self):
        mode_name = self.kbd_mode_combo.get_active_text()
        rogauracore_cmd = KBD_RGB_MODES.get(mode_name, "single_static")
        r, g, b = int(self.kbd_r.get_value()), int(self.kbd_g.get_value()), int(self.kbd_b.get_value())
        speed = int(self.kbd_speed_scale.get_value())

        # Ambient is the one mode that keeps running: every other mode is a
        # single write the firmware then animates by itself, so leaving the
        # screen capture alive after switching away would hold a capture
        # session open for nothing.
        if rogauracore_cmd != "ambient":
            self.stop_ambient()

        if rogauracore_cmd == "ambient":
            self.start_ambient()
            # Saved immediately: the first frame can be a second away, and the
            # mode should survive a restart even if the portal is declined.
            self._save_kbd_rgb(mode_name, r, g, b, speed)
            return

        if rogauracore_cmd == "rainbow":
            # rogauracore takes "rainbow [SPEED]". The speed was never passed,
            # so every slider position produced the same animation.
            ok, msg = run_helper("kbdrgb", "rainbow", speed)
        elif rogauracore_cmd == "single_colorcycle":
            ok, msg = run_helper("kbdrgb", "single_colorcycle", speed)
        elif rogauracore_cmd == "single_breathing":
            r2 = int(self.kbd_r2.get_value())
            g2 = int(self.kbd_g2.get_value())
            b2 = int(self.kbd_b2.get_value())
            # Likewise "single_breathing COLOR1 [COLOR2] [SPEED]" -- the speed
            # argument was omitted here too.
            ok, msg = run_helper("kbdrgb", "single_breathing", r, g, b, r2, g2, b2, speed)
        elif rogauracore_cmd == "single_pulsing":
            # rogauracore's real pulse effect -- a distinct sharp flash
            # animation, unlike single_breathing's slow fade (which is what
            # Pulse used to be faked with, making it look identical to
            # Breathing regardless of color choice).
            ok, msg = run_helper("kbdrgb", "single_pulsing", r, g, b, speed)
        elif rogauracore_cmd == "gradient_static":
            zones = self._get_gradient_zone_colors()
            ok, msg = run_helper("kbdrgb", "multi_static", *[c for z in zones for c in z])
        elif rogauracore_cmd == "battery_color":
            percent, charging = read_battery()
            if percent is None:
                ok, msg = False, "no battery found on this machine"
            else:
                br, bg, bb = battery_to_rgb(percent, charging)
                self._last_kbd_battery = (br, bg, bb)
                ok, msg = run_helper("kbdrgb", "single_static", br, bg, bb)
        elif rogauracore_cmd in ("gpu_temp_color", "cpu_temp_color"):
            # These re-color periodically from refresh_live_readouts; here
            # we just apply once immediately using the last-known temp so
            # switching to the mode has instant visible effect.
            temp = self._last_gpu_temp if rogauracore_cmd == "gpu_temp_color" else self._last_cpu_temp
            if temp is None:
                ok, msg = False, "no temperature reading yet"
            else:
                tr, tg, tb = temp_to_rgb(temp)
                ok, msg = run_helper("kbdrgb", "single_static", tr, tg, tb)
        else:
            ok, msg = run_helper("kbdrgb", "single_static", r, g, b)

        # Name the mode that is actually live rather than a generic "Applied",
        # so the tab always shows what the keyboard is doing.
        self.kbd_rgb_status.set_text(
            f"Current mode: {mode_name}" if ok else f"Error: {msg}")
        if ok:
            self._save_kbd_rgb(mode_name, r, g, b, speed)

    def _save_kbd_rgb(self, mode_name, r, g, b, speed):
        saved = self.config.get("kbd_rgb", {})
        self.config["kbd_rgb"] = {
            "mode": mode_name, "r": r, "g": g, "b": b,
            "r2": int(self.kbd_r2.get_value()), "g2": int(self.kbd_g2.get_value()),
            "b2": int(self.kbd_b2.get_value()), "speed": speed,
        }
        # Neither of these is a colour this tab sets, so they are carried
        # across rather than rebuilt. Losing the token would make the desktop
        # ask for screen permission again; the third and fourth colours belong
        # to a mode that no longer exists, and are kept only so downgrading
        # does not lose them.
        for key in ("ambient_restore_token", "r3", "g3", "b3",
                    "r4", "g4", "b4", "color_count"):
            if key in saved:
                self.config["kbd_rgb"][key] = saved[key]
        save_config(self.config)

    # -- Ambient mode -----------------------------------------------------

    def start_ambient(self):
        if getattr(self, "_ambient", None):
            return
        if not CAPS.get("kbd_ambient"):
            self.kbd_rgb_status.set_text(
                "Ambient needs the screen-sharing portal and GStreamer's "
                "PipeWire plugin, which this system does not have")
            return

        def on_colors(zones):
            # Called from the sampler thread; the helper call is safe there,
            # the status label is not.
            if CAPS.get("kbd_rgb_zones"):
                run_helper("kbdrgb", "multi_static",
                           *[c for z in zones for c in z])
            else:
                n = len(zones)
                avg = tuple(sum(z[i] for z in zones) // n for i in range(3))
                run_helper("kbdrgb", "single_static", *avg)

        def on_status(text):
            GLib.idle_add(self.kbd_rgb_status.set_text, text)

        def on_token(token):
            saved = self.config.setdefault("kbd_rgb", {})
            if saved.get("ambient_restore_token") != token:
                saved["ambient_restore_token"] = token
                GLib.idle_add(save_config, self.config)

        self._ambient = AmbientSampler(
            on_colors, on_status,
            restore_token=self.config.get("kbd_rgb", {}).get(
                "ambient_restore_token"),
            on_token=on_token)
        self._ambient.start()

    def stop_ambient(self):
        sampler = getattr(self, "_ambient", None)
        if sampler:
            self._ambient = None
            sampler.stop()

    def _get_gradient_zone_colors(self):
        """Four zone colours forming an even ramp from colour 1 to colour 2.

        Walks from one colour to the other in equal steps across the four
        zones, which is what people mean by a gradient."""
        c1 = (int(self.kbd_r.get_value()), int(self.kbd_g.get_value()),
              int(self.kbd_b.get_value()))
        c2 = (int(self.kbd_r2.get_value()), int(self.kbd_g2.get_value()),
              int(self.kbd_b2.get_value()))
        return [tuple(max(0, min(255, round(a + (b - a) * (i / 3))))
                      for a, b in zip(c1, c2))
                for i in range(4)]

    def refresh_live_readouts(self):
        k10 = find_hwmon_by_name("k10temp")
        if k10:
            val = read_file(os.path.join(k10, "temp1_input"))
            if val:
                temp_c = int(val) / 1000
                self.cpu_temp_label.set_text(f"Temp: {temp_c:.1f} C")
                self._last_cpu_temp = temp_c

        # Live clock speed: RyzenAdj has no reliable direct clock-set lever
        # on Zen4 (max/min gfxclk are documented non-functional since Zen2/
        # Zen3), so instead of a control that would silently do nothing,
        # this is a read-only readout showing the effect of the power
        # limit / Curve Optimizer settings actually in use. We show the
        # highest current per-core clock (from /proc/cpuinfo) rather than
        # every core individually, since a 32-thread list would clutter
        # this spot -- the max is the most relevant single number for
        # "how high is it boosting right now."
        try:
            speeds = []
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("cpu MHz"):
                        try:
                            speeds.append(float(line.split(":", 1)[1].strip()))
                        except ValueError:
                            pass
            if speeds:
                self.cpu_clock_label.set_text(f"Clock: {round(max(speeds))} MHz (max core)")
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                gpu_temp_c = float(result.stdout.strip())
                self.gpu_temp_label.set_text(f"Temp: {result.stdout.strip()} C")
                self._last_gpu_temp = gpu_temp_c
        except Exception as e:
            self.gpu_temp_label.set_text(f"Temp: error ({e})")

        # Re-apply keyboard color if a temp-color mode is currently active,
        # so the color actually tracks temperature over time rather than
        # only reflecting whatever it was at the moment you picked the mode.
        current_kbd_mode = self.config.get("kbd_rgb", {}).get("mode")
        if current_kbd_mode in ("GPU Temp Color", "CPU Temp Color"):
            temp = self._last_gpu_temp if current_kbd_mode == "GPU Temp Color" else self._last_cpu_temp
            if temp is not None:
                tr, tg, tb = temp_to_rgb(temp)
                run_helper("kbdrgb", "single_static", tr, tg, tb)
        elif current_kbd_mode == "Battery Level":
            # Charge moves in whole percent over minutes, unlike temperature,
            # so this only writes when the colour actually changes rather than
            # firing a USB write on every refresh tick for an identical value.
            percent, charging = read_battery()
            if percent is not None:
                rgb = battery_to_rgb(percent, charging)
                if rgb != getattr(self, "_last_kbd_battery", None):
                    self._last_kbd_battery = rgb
                    run_helper("kbdrgb", "single_static", *rgb)

        try:
            result = subprocess.run(["supergfxctl", "-g"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                active_mode = result.stdout.strip()
                self.sync_gpumode_radio_to_active(active_mode)
        except Exception:
            pass

        asus = find_hwmon_by_name("asus")
        if asus:
            for i, channel in [(1, "1"), (2, "2"), (3, "3")]:
                try:
                    val = read_file(os.path.join(asus, f"fan{i}_input"))
                    if channel in self.fan_editors:
                        if val is not None:
                            self.fan_editors[channel].update_rpm(val)
                        else:
                            self.fan_editors[channel].update_rpm_error(f"no value at fan{i}_input")
                except Exception as e:
                    if channel in self.fan_editors:
                        self.fan_editors[channel].update_rpm_error(str(e))
        else:
            for editor in self.fan_editors.values():
                editor.update_rpm_error("asus hwmon not found")

        self.sync_kbd_brightness_from_hardware()
        self.check_ac_auto_switch()
        return True

    def check_ac_auto_switch(self):
        ac = is_ac_connected()
        if ac is None:
            return
        if self._last_ac_state is None:
            self._last_ac_state = ac
            return
        if ac != self._last_ac_state:
            self._last_ac_state = ac
            target = self.config.get("ac_profile") if ac else self.config.get("battery_profile")
            if target and target in self.config["profiles"]:
                self.config["current_profile"] = target
                save_config(self.config)
                self._syncing_profile = True
                try:
                    self._set_combo_active_text(self.profile_combo, target)
                finally:
                    self._syncing_profile = False
                self.load_profile_into_ui(target, apply_hw=True, notify_change=True)
                self.refresh_tray_profile()

    def check_external_config_change(self):
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            return True
        if self._last_config_mtime is None:
            self._last_config_mtime = mtime
            return True
        if mtime != self._last_config_mtime:
            self._last_config_mtime = mtime
            try:
                with open(CONFIG_PATH) as f:
                    fresh = json.load(f)
            except (OSError, json.JSONDecodeError):
                return True
            name = fresh.get("current_profile")
            profile_changed = name != self.config.get("current_profile")
            # The profile NAME staying the same does not mean its contents
            # did. The enforcer, the hotkey profile cycler and an import can
            # all rewrite curves and limits under the same name, and an open
            # window used to keep showing whatever it loaded at startup --
            # so the graphs said one thing while the hardware did another,
            # and "Apply All Fan Curves" would then push the stale picture
            # back over the newer values.
            contents_changed = (
                not profile_changed
                and fresh.get("profiles", {}).get(name)
                != self.config.get("profiles", {}).get(name))
            self.config = fresh
            if profile_changed:
                self._syncing_profile = True
                try:
                    self._set_combo_active_text(self.profile_combo, name)
                finally:
                    self._syncing_profile = False
            if profile_changed or contents_changed:
                self.load_profile_into_ui(name, apply_hw=False, notify_change=False)
                self.refresh_tray_profile()
        return True

    def on_close(self, *_args):
        self.hide()
        return True

    def on_window_configure(self, widget, event):
        # configure-event fires continuously while dragging a window edge,
        # so debounce the actual disk write rather than saving on every
        # intermediate frame of the resize.
        width, height = self.get_size()

        def save_size():
            self.config["window_size"] = [width, height]
            save_config(self.config)

        self.debounce("window_size", save_size, delay_ms=500)
        return False


def build_tray(window):
    # Use the absolute installed path rather than an icon-theme name lookup
    # ("rogcontrol") -- a name lookup depends on the icon cache and can
    # resolve to a stale leftover icon (e.g. an old SVG) if one still sits
    # in the theme directory under the same name. Pointing directly at the
    # PNG file guarantees the tray always shows the actual current icon.
    icon_path = os.path.expanduser(
        "~/.local/share/icons/hicolor/256x256/apps/rogcontrol.png")
    icon_ref = icon_path if os.path.exists(icon_path) else "rogcontrol"
    indicator = AppIndicator.Indicator.new(
        "rogcontrol", icon_ref,
        AppIndicator.IndicatorCategory.HARDWARE,
    )
    indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

    menu = Gtk.Menu()
    show_item = Gtk.MenuItem(label="Show Window")
    show_item.connect("activate", lambda _i: (window.show_all(), window.present()))
    menu.append(show_item)
    menu.append(Gtk.SeparatorMenuItem())

    # Quick profile switching without opening the window. Only the built-in
    # profiles appear: a tray menu is a switcher, not a profile manager, and
    # a long list of custom profiles would push Quit off the screen. Custom
    # ones stay switchable in the window.
    profile_items = {}
    group = None
    current = window.config.get("current_profile")
    for name in window.config.get("profiles", {}):
        if name not in DEFAULT_PROFILES:
            continue
        item = Gtk.RadioMenuItem.new_with_label_from_widget(group, name)
        if group is None:
            group = item
        item.set_active(name == current)
        # Connected after set_active so building the menu doesn't look like
        # the user picking a profile.
        item.connect("toggled", window.on_tray_profile_toggled, name)
        menu.append(item)
        profile_items[name] = item
    window._tray_profile_items = profile_items
    if profile_items:
        menu.append(Gtk.SeparatorMenuItem())

    quit_item = Gtk.MenuItem(label="Quit")
    quit_item.connect("activate", lambda _i: window.get_application().quit())
    menu.append(quit_item)
    menu.show_all()
    indicator.set_menu(menu)
    return indicator


def apply_dark_theme():
    provider = Gtk.CssProvider()
    provider.load_from_data(DARK_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


class RogControlApplication(Gtk.Application):
    """Wrapping in Gtk.Application (rather than a bare Gtk.Window +
    Gtk.main()) gives us real single-instance behavior for free: GLib
    registers the app under a unique ID over D-Bus, and if you launch the
    app a second time (e.g. from the app grid while it's already running
    in the tray), GLib detects the existing registration and calls
    `do_activate` on the ALREADY-RUNNING process instead of starting a new
    one -- which is exactly what fixes 'opens a second separate app'."""

    def __init__(self):
        super().__init__(application_id="com.fadi.rogcontrol",
                          flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.tray = None
        self._start_minimized = False
        self._toggle_requested = False
        self.connect("command-line", self.on_command_line)

    def on_command_line(self, app, command_line):
        args = command_line.get_arguments()
        self._start_minimized = "--minimized" in args or "--tray" in args
        self._toggle_requested = "--toggle" in args
        self.activate()
        return 0

    def do_shutdown(self):
        # Ambient holds a screen-capture session open, so it has to be closed
        # deliberately -- nothing else here outlives the process.
        if self.window is not None:
            self.window.stop_ambient()
        Gtk.Application.do_shutdown(self)

    def do_activate(self):
        if self.window is None:
            apply_dark_theme()
            self.window = RogControlWindow(application=self)
            self.tray = build_tray(self.window)
            # Ambient is the only mode that needs a process behind it, so a
            # saved Ambient mode has to be restarted here; every other mode is
            # already live in the firmware from when it was applied.
            if (self.window.config.get("kbd_rgb", {}).get("mode") == "Ambient"
                    and CAPS.get("kbd_ambient")):
                self.window.start_ambient()
            if not self._start_minimized:
                self.window.show_all()
            # else: stays hidden, tray-only, until "Show Window" is clicked
        elif self._toggle_requested and self.window.is_visible():
            # --toggle: pressing the shortcut again while the window is
            # already open closes it, instead of just re-focusing it --
            # matches the show/hide behavior of a typical app-launcher key.
            self.window.hide()
        else:
            # Second launch while already running (or --toggle while the
            # window was hidden): bring the existing window to the front.
            self.window.show_all()
            self.window.present()


def main():
    detect_capabilities()
    detect_gpu_limits()  # must run before any widget reads the limits
    app = RogControlApplication()
    app.run(sys.argv)


if __name__ == "__main__":
    main()
