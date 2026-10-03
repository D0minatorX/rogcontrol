"""Isolated NVML global clock-offset bridge; values use driver offset MHz.

Use the global VF APIs to preserve the existing all-performance-level slider
semantics. Do not substitute a P0-only write when these APIs are unavailable.
"""

import argparse
import ctypes
import fcntl
import json
import re
import signal
import time


# Memory offsets may quantize odd MHz requests. Use an even UI/probe step
# rather than weakening exact readback checks or accepting a no-op write.
CLOCK_OFFSET_STEPS = {"core": 25, "memory": 50}


class NvmlError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Nvml:
    def __init__(self, pci_bus):
        self.lib = ctypes.CDLL("libnvidia-ml.so.1")
        self.error_string = self.lib.nvmlErrorString
        self.error_string.argtypes = [ctypes.c_int]
        self.error_string.restype = ctypes.c_char_p
        self.call("nvmlInit_v2", [])
        self.device = ctypes.c_void_p()
        try:
            self.call("nvmlDeviceGetHandleByPciBusId_v2",
                      [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)],
                      pci_bus.encode("ascii"), ctypes.byref(self.device))
        except Exception:
            self.close()
            raise

    def call(self, name, types, *args):
        fn = getattr(self.lib, name)
        fn.argtypes = types
        fn.restype = ctypes.c_int
        status = fn(*args)
        if status:
            raise NvmlError(status, self.error_string(status).decode())

    def read(self, kind):
        stem = "Gpc" if kind == "core" else "Mem"
        value, low, high = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        pointer = ctypes.POINTER(ctypes.c_int)
        self.call(f"nvmlDeviceGet{stem}ClkVfOffset",
                  [ctypes.c_void_p, pointer], self.device, ctypes.byref(value))
        self.call(f"nvmlDeviceGet{stem}ClkMinMaxVfOffset",
                  [ctypes.c_void_p, pointer, pointer], self.device,
                  ctypes.byref(low), ctypes.byref(high))
        return {"value": value.value, "minimum": low.value, "maximum": high.value}

    def write(self, kind, value):
        stem = "Gpc" if kind == "core" else "Mem"
        self.call(f"nvmlDeviceSet{stem}ClkVfOffset",
                  [ctypes.c_void_p, ctypes.c_int], self.device, value)

    def close(self):
        self.call("nvmlShutdown", [])


def operate(api, action, kind, value=None):
    original = api.read(kind)
    low, high = original["minimum"], original["maximum"]
    if not low <= original["value"] <= high:
        raise ValueError("invalid offset range returned by driver")
    if action == "read":
        return original
    if action == "probe":
        # Prefer a downward step, avoiding an automatic positive overclock.
        step = CLOCK_OFFSET_STEPS[kind]
        candidate = original["value"] - step
        if candidate < low:
            candidate = original["value"] + step
        if not low <= candidate <= high:
            raise ValueError("no testable offset range")
    else:
        candidate = value
    if not isinstance(candidate, int) or not low <= candidate <= high:
        raise ValueError("offset is outside this GPU's reported range")
    applied = False
    try:
        api.write(kind, candidate)
        actual = api.read(kind)
        if actual["value"] != candidate:
            raise RuntimeError("driver did not retain the requested offset")
        applied = True
    finally:
        if action == "probe" or not applied:
            try:
                try:
                    unchanged = api.read(kind)["value"] == original["value"]
                except Exception:
                    unchanged = False
                if not unchanged:
                    api.write(kind, original["value"])
                if api.read(kind)["value"] != original["value"]:
                    raise RuntimeError("restoration readback mismatch")
            except Exception as error:
                # Never classify an uncertain restoration as permission or
                # unsupported: another backend must not write after this.
                raise RuntimeError("could not restore the original GPU offset: "
                                   + str(error)) from error
    return original if action == "probe" else actual


def run(action, kind, pci_bus, value=None):
    if (action not in ("read", "probe", "set") or kind not in ("core", "memory")
            or not re.fullmatch(r"[0-9a-fA-F]{4,8}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]", pci_bus)):
        return {"ok": False, "error": "invalid clock request"}
    api = None
    try:
        api = Nvml(pci_bus)
        return {"ok": True, **operate(api, action, kind, value)}
    except NvmlError as error:
        return {"ok": False, "error": str(error), "code": error.code}
    except (AttributeError, OSError) as error:
        return {"ok": False, "error": str(error), "unavailable": True}
    except Exception as error:
        return {"ok": False, "error": str(error)}
    finally:
        if api is not None:
            try:
                api.close()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("read", "probe", "set"))
    parser.add_argument("kind", choices=("core", "memory"))
    parser.add_argument("pci_bus")
    parser.add_argument("value", nargs="?", type=int)
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise InterruptedError("clock operation interrupted")
    # Give Python transactions a chance to restore on normal cancellation.
    # A wedged driver/SIGKILL still cannot offer a restoration guarantee.
    signal.signal(signal.SIGTERM, interrupted)
    # Root-owned fixed lock serializes our GUI, installer and background
    # service across the complete read/write/restore transaction.
    with open("/run/rogcontrol-nvidia-clocks.lock", "a") as lock:
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    print(json.dumps({"ok": False, "error": "clock controls busy"}))
                    return
                time.sleep(0.05)
        result = run(args.action, args.kind, args.pci_bus, args.value)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
