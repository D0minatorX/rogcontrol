"""Read-only Decky sensor sampler, run by system Python as the desktop user.

Bundled with the plugin so new metrics work with the existing desktop install.
CPU usage and RAPL power are measured over an interval within this process;
Decky's CLI subprocesses do not share counter history between polls.
"""
import json
import math
import sys
import time


def cpu_times():
    try:
        with open('/proc/stat') as source:
            fields = source.readline().split()
        if fields[0] != 'cpu' or len(fields) < 9:
            return None
        # guest/guest_nice are already included in user/nice; don't count twice.
        ticks = [int(value) for value in fields[1:9]]
        return sum(ticks), ticks[3] + ticks[4]
    except (OSError, ValueError, IndexError):
        return None


def cpu_usage(before, after):
    if before is None or after is None:
        return None
    total, idle = after[0] - before[0], after[1] - before[1]
    if total <= 0 or idle < 0 or idle > total:
        return None
    return 100 * (total - idle) / total


def number(value, maximum=None):
    if value is None or not math.isfinite(value) or value < 0:
        return None
    return min(value, maximum) if maximum is not None else value


def collect(hardware, cfg):
    started = time.monotonic()
    before = cpu_times()
    hardware.read_cpu_power_w()  # Seed RAPL history in this short-lived process.
    suspended = hardware.dgpu_is_suspended()
    gpu = (None,) * 4 if suspended else hardware.read_nvidia_query(
        ('temperature.gpu', 'power.draw', 'clocks.gr', 'utilization.gpu'), timeout=3)
    # At least 100 ms of counter history, without adding delay to slow GPU reads.
    time.sleep(max(0, 0.1 - (time.monotonic() - started)))
    usage = cpu_usage(before, cpu_times())
    gpu_temp, gpu_power, gpu_clock, gpu_usage = [number(value) for value in gpu]
    return {
        'cpu_temp': number(hardware.read_cpu_temp()),
        'cpu_usage': number(usage, 100),
        'cpu_clock_mhz': number(hardware.read_peak_core_clock_mhz()),
        'cpu_power_w': number(hardware.read_cpu_power_w()),
        'gpu_temp': gpu_temp,
        'gpu_usage': number(gpu_usage, 100),
        'gpu_clock_mhz': gpu_clock,
        'gpu_power_w': gpu_power,
        'gpu_status': ('sleeping' if suspended else
                       'available' if any(value is not None for value in
                                          (gpu_temp, gpu_power, gpu_clock, gpu_usage)) else 'unavailable'),
        'fans': hardware.read_fan_rpms(),
        'current_profile': cfg.get('current_profile'),
    }


if __name__ == '__main__':
    try:
        sys.path.insert(0, sys.argv[1])
        from rogcontrol import config, hardware
        print(json.dumps({'ok': True, 'telemetry': collect(hardware, config.load_config())}, allow_nan=False))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        raise SystemExit(1)
