"""Isolated, minimal NVAPI bridge for NVIDIA Voltage Boost.

NVIDIA does not document these Linux entry points.  This module is always
started in a short-lived child process by :mod:`rogcontrol.hardware`, so an
incompatible driver cannot take down the GTK window or the enforcer.  It has
no dependency on LACT or any other GPU-control application.
"""

import argparse
import ctypes
import json
import sys


NVAPI_OK = 0
MAX_PHYSICAL_GPUS = 64
QUERY_INITIALIZE = 0x0150E828
QUERY_UNLOAD = 0xD22BDD7E
QUERY_ENUM_PHYSICAL_GPUS = 0xE5AC921F
QUERY_GPU_GET_BUS_ID = 0x1BE0B8E5
QUERY_VOLT_RAILS_GET_CONTROL = 0x9DF23CA1
QUERY_VOLT_RAILS_SET_CONTROL = 0xB9306D9B


class VoltRailsControlV1(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32),
                ("percent_delta", ctypes.c_uint8),
                ("reserved", ctypes.c_uint8 * 32)]

    def __init__(self, percent=0):
        super().__init__()
        self.version = ctypes.sizeof(type(self)) | (1 << 16)
        self.percent_delta = percent


def _pci_bus_number(pci_bus):
    """The bus byte NVAPI reports for a ``domain:bus:device.function`` id."""
    try:
        return int(pci_bus.split(":")[1], 16)
    except (AttributeError, IndexError, ValueError):
        raise ValueError("invalid PCI bus identifier") from None


class NvApi:
    """The tiny subset of NVAPI needed for Voltage Boost."""

    def __init__(self):
        self.lib = ctypes.CDLL("libnvidia-api.so.1")
        self.query = self.lib.nvapi_QueryInterface
        self.query.argtypes = [ctypes.c_uint32]
        self.query.restype = ctypes.c_void_p
        self._check(self._call0(QUERY_INITIALIZE)())

    def _call0(self, query_id):
        address = self.query(query_id)
        if not address:
            raise RuntimeError("NVIDIA driver does not expose the required NVAPI call")
        return ctypes.CFUNCTYPE(ctypes.c_int)(address)

    def _call_gpu(self, query_id):
        address = self.query(query_id)
        if not address:
            raise RuntimeError("NVIDIA driver does not expose the required NVAPI call")
        return ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                ctypes.c_void_p)(address)

    @staticmethod
    def _check(status):
        if status != NVAPI_OK:
            raise RuntimeError(f"NVIDIA driver rejected the request (NVAPI {status})")

    def matching_gpu(self, pci_bus):
        handles = (ctypes.c_void_p * MAX_PHYSICAL_GPUS)()
        count = ctypes.c_uint32()
        enum = self._call_gpu(QUERY_ENUM_PHYSICAL_GPUS)
        self._check(enum(ctypes.byref(handles), ctypes.byref(count)))
        get_bus = self._call_gpu(QUERY_GPU_GET_BUS_ID)
        wanted = _pci_bus_number(pci_bus)
        matches = []
        for index in range(min(count.value, MAX_PHYSICAL_GPUS)):
            bus = ctypes.c_uint32()
            self._check(get_bus(handles[index], ctypes.byref(bus)))
            if bus.value == wanted:
                matches.append(handles[index])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # NVAPI exposes only the bus byte through this entry point. PCI
            # domains can duplicate that byte, so guessing would risk writing
            # a different NVIDIA card on a multi-domain system.
            raise RuntimeError("active NVIDIA GPU bus match is ambiguous")
        raise RuntimeError("active NVIDIA GPU was not found by NVAPI")

    def _read_voltage_control(self, handle):
        control = VoltRailsControlV1()
        self._check(self._call_gpu(QUERY_VOLT_RAILS_GET_CONTROL)(
            handle, ctypes.byref(control)))
        return control

    def read_voltage_boost(self, handle):
        return int(self._read_voltage_control(handle).percent_delta)

    def set_voltage_boost(self, handle, percent):
        # The 32 opaque bytes are driver-owned. Read-modify-write them so a
        # future driver can use them without an older ROG Control clearing
        # data it does not understand. This read is also the support
        # preflight: a failed read means no set call is ever issued.
        control = self._read_voltage_control(handle)
        control.percent_delta = percent
        self._check(self._call_gpu(QUERY_VOLT_RAILS_SET_CONTROL)(
            handle, ctypes.byref(control)))

    def close(self):
        try:
            self._call0(QUERY_UNLOAD)()
        except Exception:
            pass


def run(action, pci_bus, percent=None):
    """Perform a read or verified write and return a JSON-serialisable dict."""
    if action == "set" and (percent is None or not 0 <= percent <= 100):
        return {"ok": False, "error": "voltage boost must be between 0 and 100"}
    api = None
    try:
        api = NvApi()
        handle = api.matching_gpu(pci_bus)
        if action == "set":
            api.set_voltage_boost(handle, percent)
        value = api.read_voltage_boost(handle)
        if action == "set" and value != percent:
            return {"ok": False,
                    "error": f"driver reported {value}% after requesting {percent}%"}
        return {"ok": True, "value": value}
    except Exception as error:  # unsafe vendor API boundary
        return {"ok": False, "error": str(error)}
    finally:
        if api is not None:
            api.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="ROG Control NVIDIA Voltage Boost bridge")
    parser.add_argument("action", choices=("read", "set"))
    parser.add_argument("percent", nargs="?", type=int)
    parser.add_argument("--pci-bus", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.action, args.pci_bus, args.percent)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
