"""Headless state and CPU/profile actions exposed to the Decky plugin."""

from . import config, hardware, profile_requests


def _cpu_capabilities(caps):
    """Translate the app's capability report into the small Decky contract."""
    clock = caps.get("cpu_clock")
    return {
        "boost": bool(caps.get("cpu_boost")),
        "max_freq": bool(clock),
        "clock_range_mhz": ([clock[0] // 1000, clock[1] // 1000]
                             if clock else None),
    }


def get_state(config_data=None, caps=None):
    """Return saved controls and detected support without importing GTK."""
    cfg = config_data if config_data is not None else config.load_config()
    detected = caps if caps is not None else hardware.detect_capabilities(probe_gpu=False)
    name = cfg.get("current_profile")
    profile = (cfg.get("profiles") or {}).get(name) or {}
    cpu = profile.get("cpu") or {}
    limits = _cpu_capabilities(detected)
    gpu = profile.get("gpu") or {}
    maximum = None
    supported = False
    if detected.get("nvidia") and not hardware.dgpu_is_suspended():
        with profile_requests.hardware_apply_lock():
            # Reread under the lock when inspecting the live profile: the probe
            # reapplies its saved ceiling and must not restore a stale value.
            if config_data is None:
                cfg = config.load_config()
                name = cfg.get("current_profile")
                profile = (cfg.get("profiles") or {}).get(name) or {}
                cpu = profile.get("cpu") or {}
                gpu = profile.get("gpu") or {}
            maximum = hardware.detect_gpu_max_clock()
            supported = bool(maximum and hardware.gpu_clock_limit_supported(gpu.get("clock_limit")))
    limits["gpu_clock_range_mhz"] = [hardware.CLOCK_LIMIT_MIN, maximum] if supported else None
    return {
        "protocol_version": 2,
        "profiles": list((cfg.get("profiles") or {}).keys()),
        "current_profile": name,
        "cpu": {
            "boost": bool(cpu.get("boost", True)),
            # Config and kernel cpufreq values are in kHz; the plugin displays MHz.
            "max_freq_mhz": (int(cpu.get("max_freq", 0) or 0) // 1000),
        },
        "gpu": {"clock_limit_mhz": gpu.get("clock_limit")},
        "capabilities": limits,
    }


def set_profile(name):
    """Queue a validated profile selection through the shared worker."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("profile name is required")
    return profile_requests.request_profile(name.strip(), origin="decky")


def _apply_cpu_profile(cpu, caps):
    failures = []
    for _step, args in hardware.cpu_apply_plan(cpu, caps):
        ok, message = hardware.run_helper_logged(
            *args, source="decky", timeout=30)
        if not ok:
            failures.append(message)
    if failures:
        raise RuntimeError("; ".join(failures))


def set_cpu_value(key, value):
    """Save and apply one supported CPU setting on the current profile.

    ``max_freq`` uses MHz at this interface and 0 means no clock ceiling.
    """
    if key not in ("boost", "max_freq"):
        raise ValueError("unsupported CPU setting")
    caps = hardware.detect_capabilities(probe_gpu=False)
    if key == "boost" and not caps.get("cpu_boost"):
        raise ValueError("CPU boost control is unavailable")
    clock = caps.get("cpu_clock")
    if key == "max_freq" and not clock:
        raise ValueError("CPU clock ceiling is unavailable")
    if key == "boost":
        if not isinstance(value, bool):
            raise ValueError("CPU boost value must be true or false")
        stored_value = value
    else:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("CPU clock value must be an integer MHz value")
        min_mhz, max_mhz = clock[0] // 1000, clock[1] // 1000
        if value != 0 and not min_mhz <= value <= max_mhz:
            raise ValueError(f"CPU clock must be 0 or between {min_mhz} and {max_mhz} MHz")
        stored_value = value * 1000

    def mutate(cfg):
        profile_name = cfg.get("current_profile")
        profiles = cfg.get("profiles") or {}
        if profile_name not in profiles:
            raise ValueError("there is no active profile")
        cpu = profiles[profile_name].setdefault("cpu", {})
        cpu[key] = stored_value

    with profile_requests.hardware_apply_lock():
        updated = config.update_config(mutate)
        current = updated["profiles"][updated["current_profile"]].get("cpu") or {}
        _apply_cpu_profile(current, {
            "ryzenadj": bool(caps.get("ryzenadj")),
            "cpu_boost": bool(caps.get("cpu_boost")),
            "cpu_epp": caps.get("cpu_epp"),
            "cpu_clock": bool(caps.get("cpu_clock")),
            "cpu_power_limits": caps.get("cpu_power_limits"),
        })
    # Read the current profile again before the GPU capability probe: a
    # profile switch could occur after the CPU hardware lock was released.
    return {"ok": True, "state": get_state(caps=caps)}


def get_telemetry():
    """Read live sensors without changing GPU settings."""
    gpu_temp = None
    suspended = hardware.dgpu_is_suspended()
    if not suspended:
        gpu_temp = hardware.read_nvidia_query(("temperature.gpu",), timeout=3)[0]
    return {"cpu_temp": hardware.read_cpu_temp(), "gpu_temp": gpu_temp,
            "gpu_status": "sleeping" if suspended else ("available" if gpu_temp is not None else "unavailable"),
            "fans": hardware.read_fan_rpms(),
            "current_profile": config.load_config().get("current_profile")}


def set_gpu_clock_limit(mhz):
    """Apply and save the active profile's GPU ceiling; 0 clears it."""
    if isinstance(mhz, bool) or not isinstance(mhz, int):
        raise ValueError("GPU clock must be an integer MHz value")
    with profile_requests.hardware_apply_lock():
        caps = hardware.detect_capabilities(probe_gpu=False)
        cfg = config.load_config()
        name = cfg.get("current_profile")
        if name not in (cfg.get("profiles") or {}):
            raise ValueError("there is no active profile")
        gpu = cfg["profiles"][name].get("gpu") or {}
        if hardware.dgpu_is_suspended():
            raise RuntimeError("GPU is sleeping; wake it before changing the clock ceiling")
        maximum = hardware.detect_gpu_max_clock() if caps.get("nvidia") else None
        if not maximum or not hardware.gpu_clock_limit_supported(gpu.get("clock_limit")):
            raise ValueError("GPU clock ceiling is unavailable")
        if mhz != 0 and not hardware.CLOCK_LIMIT_MIN <= mhz <= maximum:
            raise ValueError(f"GPU clock must be 0 or between {hardware.CLOCK_LIMIT_MIN} and {maximum} MHz")
        value = maximum if mhz == 0 else mhz
        ok, message = hardware.run_helper_logged(
            "gpuclocklimit", hardware.gpu_clock_limit_arg(value, maximum), source="decky", timeout=30)
        if not ok:
            raise RuntimeError(message or "Could not apply GPU clock ceiling")
        def mutate(current):
            if current.get("current_profile") != name or name not in (current.get("profiles") or {}):
                raise RuntimeError("Active profile changed while applying GPU ceiling")
            current["profiles"][name].setdefault("gpu", {})["clock_limit"] = value
        config.update_config(mutate)
    return {"ok": True}
