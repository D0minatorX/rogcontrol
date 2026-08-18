#!/usr/bin/env python3
"""
rogcontrol-power-diag.py
Standalone diagnostic: logs CPU/GPU temps and power, plus the live
SMU-programmed STAPM/Fast/Slow/Tctl values from `ryzenadj -i`, to a CSV
while you play -- to see whether an early-game power spike is STAPM
converging toward its target (expected AMD SMU behaviour) or the limit
never actually landing (a bug).

Not part of the app. Run from a terminal, Ctrl+C to stop.
"""
import argparse
import csv
import datetime
import fcntl
import os
import shutil
import subprocess
import sys
import threading
import time

# The shared modules sit beside this script's package in the repo, and under
# ~/.local/lib once installed -- same probe the tray and the enforcer do,
# repo first so a checkout tests the checkout.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.dirname(_HERE), os.path.expanduser("~/.local/lib")):
    if os.path.isfile(os.path.join(_candidate, "rogcontrol", "__init__.py")):
        if _candidate not in sys.path:
            sys.path.insert(0, _candidate)
        break

from rogcontrol import hardware  # noqa: E402

DEFAULT_INTERVAL_S = 2.0
DEFAULT_OUTPUT_DIR = os.path.expanduser("~/.local/share/rogcontrol")
SUDO_KEEPALIVE_S = 60
RYZENADJ_TIMEOUT_S = 5
LOCK_PATHS = ("/run/rogcontrol-helper.lock", "/var/lock/rogcontrol-helper.lock")

CSV_FIELDS = [
    "timestamp", "cpu_temp_c", "cpu_package_power_w",
    "gpu_temp_c", "gpu_power_w",
    "stapm_limit_w", "stapm_value_w",
    "fast_limit_w", "fast_value_w",
    "slow_limit_w", "slow_value_w",
    "tctl_limit_c", "tctl_value_c",
]

RYZENADJ_KEYS = {
    "stapm_limit_w": "STAPM LIMIT", "stapm_value_w": "STAPM VALUE",
    "fast_limit_w": "PPT LIMIT FAST", "fast_value_w": "PPT VALUE FAST",
    "slow_limit_w": "PPT LIMIT SLOW", "slow_value_w": "PPT VALUE SLOW",
    "tctl_limit_c": "THM LIMIT CORE", "tctl_value_c": "THM VALUE CORE",
}


def find_ryzenadj():
    """Mirror rogcontrol-helper's own lookup: local build first, else PATH."""
    if os.access("/usr/local/bin/ryzenadj", os.X_OK):
        return "/usr/local/bin/ryzenadj"
    return shutil.which("ryzenadj") or "/usr/local/bin/ryzenadj"


class SmuLock:
    """Best-effort participant in rogcontrol-helper's own SMU flock, so this
    script's read-only `ryzenadj -i` polling never races the enforcer's 60s
    `cpu` write over the same /dev/mem mailbox. A read-only fd can still
    flock(); if the lock file does not exist yet (no CPU apply has run
    since boot) or cannot be opened, this degrades to no locking -- same
    philosophy as the helper's own smu_lock_file()."""

    def __enter__(self):
        self._fd = None
        for path in LOCK_PATHS:
            if os.path.exists(path):
                try:
                    self._fd = open(path, "r")
                    fcntl.flock(self._fd, fcntl.LOCK_EX)
                except OSError:
                    self._fd = None
                break
        return self

    def __exit__(self, *exc):
        if self._fd:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            self._fd.close()


def parse_ryzenadj_table(text):
    """{NAME: float value} for every '| Name | Value | ... |' row. Tolerant
    of column width/format drift across ryzenadj versions -- only the
    pipe-delimited structure is assumed, never exact spacing."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        name, value = cells[0].upper(), cells[1]
        try:
            out[name] = float(value)
        except ValueError:
            continue
    return out


def read_ryzenadj_live(ryzenadj_path):
    """{csv_field: value} for the live SMU values, all None if the call
    failed (sudo cache expired, secure boot blocking /dev/mem, etc.) --
    same "None rather than raise" contract as hardware.py's readers."""
    blanks = {k: None for k in RYZENADJ_KEYS}
    try:
        with SmuLock():
            result = subprocess.run(
                ["sudo", "-n", ryzenadj_path, "-i"],
                capture_output=True, text=True, timeout=RYZENADJ_TIMEOUT_S)
    except Exception:
        return blanks
    if result.returncode != 0:
        return blanks
    table = parse_ryzenadj_table(result.stdout)
    return {field: table.get(name) for field, name in RYZENADJ_KEYS.items()}


def sudo_keepalive(stop_event):
    """Background thread: touches the sudo timestamp every 60s so a
    multi-minute gaming session never hits a re-prompt mid-loop."""
    while not stop_event.wait(SUDO_KEEPALIVE_S):
        try:
            subprocess.run(["sudo", "-n", "-v"], capture_output=True, timeout=5)
        except Exception:
            pass


def sample_once(ryzenadj_path, use_sudo):
    cpu_temp = hardware.read_cpu_temp()
    cpu_power = hardware.read_package_power_w()
    gpu_temp, gpu_power = hardware.read_nvidia_stats()
    row = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "cpu_temp_c": cpu_temp, "cpu_package_power_w": cpu_power,
        "gpu_temp_c": gpu_temp, "gpu_power_w": gpu_power,
    }
    row.update(read_ryzenadj_live(ryzenadj_path) if use_sudo
               else {k: None for k in RYZENADJ_KEYS})
    return row


def format_summary(row):
    def fmt(v, unit=""):
        return "--" if v is None else f"{v:.1f}{unit}"
    return (f"{row['timestamp']}  "
            f"CPU {fmt(row['cpu_temp_c'], 'C')} {fmt(row['cpu_package_power_w'], 'W')}  "
            f"STAPM {fmt(row['stapm_value_w'], 'W')}/{fmt(row['stapm_limit_w'], 'W')}  "
            f"Fast {fmt(row['fast_value_w'], 'W')}/{fmt(row['fast_limit_w'], 'W')}  "
            f"Slow {fmt(row['slow_value_w'], 'W')}/{fmt(row['slow_limit_w'], 'W')}  "
            f"GPU {fmt(row['gpu_temp_c'], 'C')} {fmt(row['gpu_power_w'], 'W')}")


def default_output_path():
    os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(DEFAULT_OUTPUT_DIR, f"power-diag-{stamp}.csv")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-d", "--duration", type=float, default=None,
                         help="seconds to run; omit to run until Ctrl+C")
    parser.add_argument("-n", "--interval", type=float, default=DEFAULT_INTERVAL_S,
                         help=f"seconds between samples (default {DEFAULT_INTERVAL_S})")
    parser.add_argument("-o", "--output", default=None,
                         help="CSV path (default ~/.local/share/rogcontrol/power-diag-<timestamp>.csv)")
    parser.add_argument("--no-ryzenadj", action="store_true",
                         help="skip ryzenadj -i / sudo entirely (temps+power only)")
    args = parser.parse_args()

    output_path = args.output or default_output_path()
    ryzenadj_path = find_ryzenadj()
    use_sudo = not args.no_ryzenadj
    stop_event = threading.Event()

    if use_sudo:
        print("Needs your sudo password once, to read live SMU values via `ryzenadj -i`.")
        if subprocess.run(["sudo", "-v"]).returncode != 0:
            print("Continuing without live STAPM/Fast/Slow values "
                  "(ryzenadj -i needs sudo).", file=sys.stderr)
            use_sudo = False
        else:
            threading.Thread(target=sudo_keepalive, args=(stop_event,), daemon=True).start()

    print(f"Logging to {output_path} every {args.interval}s"
          + (f" for {args.duration}s" if args.duration else " until Ctrl+C") + "...")

    is_tty = sys.stdout.isatty()
    count = 0
    start = time.monotonic()
    try:
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
            while args.duration is None or time.monotonic() - start < args.duration:
                sample_start = time.monotonic()
                row = sample_once(ryzenadj_path, use_sudo)
                writer.writerow(row)
                f.flush()
                count += 1
                line = format_summary(row)
                if is_tty:
                    print("\r" + line + "    ", end="", flush=True)
                else:
                    print(line, flush=True)
                elapsed = time.monotonic() - sample_start
                time.sleep(max(0.0, args.interval - elapsed))
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()

    print(f"\nStopped after {count} samples "
          f"({time.monotonic() - start:.0f}s). CSV: {output_path}")


if __name__ == "__main__":
    main()
