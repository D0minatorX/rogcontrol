"""Sparse application overrides and Linux executable discovery."""
import os
from copy import deepcopy
from pathlib import Path

FIELDS = {
    "cpu": ("stapm", "fast", "slow", "pl1", "pl2", "boost", "max_freq", "min_freq"),
    "gpu": ("watts", "clock_limit", "clock_offset", "mem_clock_offset"),
    "fans": ("1", "2", "3"),
}


def entries(config):
    profiles = config.get("app_profiles")
    return profiles if isinstance(profiles, dict) else {}


def running_paths(config, proc="/proc"):
    """Return matching paths ordered by the start of their newest process."""
    paths = entries(config)
    if not paths:
        return []
    newest = {}
    for process in Path(proc).iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            path = os.readlink(process / "exe")
            if path not in paths:
                continue
            # The command name can contain spaces and parentheses.
            stat = (process / "stat").read_text().rsplit(")", 1)[1].split()
            if stat[0] == "Z":
                continue
            identity = (int(stat[19]), int(process.name))
        except (OSError, ValueError, IndexError):
            continue
        newest[path] = max(identity, newest.get(path, identity))
    return sorted(newest, key=newest.__getitem__)


def valid_value(section, value, key=None):
    if key == "boost":
        return type(value) is bool
    if section == "fans":
        return (isinstance(value, list) and 2 <= len(value) <= 8
                and all(isinstance(point, list) and len(point) == 2
                        and all(type(n) is int and 0 <= n <= 100 for n in point)
                        for point in value)
                and all(a[0] < b[0] for a, b in zip(value, value[1:])))
    return type(value) is int


def baseline_value(section, key, values):
    if key in values:
        return values[key]
    if section == "cpu" and key in ("max_freq", "min_freq"):
        return 0
    if section == "cpu" and key == "boost":
        return True  # Profiles without the key leave the kernel default (boost on).
    if section == "gpu" and key == "clock_limit":
        return 0  # Explicit reset when the global profile has no ceiling.
    return None


def effective_profile(config, paths, baseline=None):
    if baseline is None:
        baseline = config.get("profiles", {}).get(config.get("current_profile")) or {}
    if not paths:
        return baseline
    effective = deepcopy(baseline)
    for path in paths:
        overrides = entries(config).get(path)
        if not isinstance(overrides, dict):
            continue
        for section, keys in FIELDS.items():
            values = overrides.get(section)
            base = baseline.get(section)
            if not isinstance(values, dict) or not isinstance(base, dict):
                continue
            if section == "cpu":
                if base.get("limits_enabled") is False:
                    keys = ("boost", "max_freq", "min_freq")
                if base.get("boost") is False:
                    keys = tuple(key for key in keys if key != "max_freq")
            for key in keys:
                if (baseline_value(section, key, base) is not None and key in values
                        and valid_value(section, values[key], key)):
                    effective[section][key] = deepcopy(values[key])
    cpu = effective.get("cpu", {})
    if cpu.get("max_freq") and cpu.get("min_freq", 0) > cpu["max_freq"]:
        for key in ("max_freq", "min_freq"):
            if key in baseline["cpu"]:
                cpu[key] = baseline["cpu"][key]
            else:
                cpu.pop(key, None)
    return effective


def active_profile(config, baseline=None):
    paths = running_paths(config)
    if not paths and baseline is not None:
        return baseline
    return effective_profile(config, paths, baseline)
