"""Global NVML offsets: verified writes, restoration and session-free routing."""
import unittest
import signal
import subprocess
from unittest import mock

from rogcontrol import hardware, nvidia_clocks
from tests.test_cardwire_gpu_limits import load_enforcer


class FakeNvml:
    def __init__(self):
        self.value = 0
        self.writes = []

    def read(self, kind):
        return dict(value=self.value, minimum=-100, maximum=100)

    def write(self, kind, value):
        self.writes.append((kind, value))
        self.value = value


class BridgeTests(unittest.TestCase):
    def test_probe_restores_both_domains(self):
        for kind in ("core", "memory"):
            api = FakeNvml()
            result = nvidia_clocks.operate(api, "probe", kind)
            self.assertEqual(result["value"], 0)
            expected = -50 if kind == "memory" else -25
            self.assertEqual(api.writes, [(kind, expected), (kind, 0)])
            self.assertEqual(api.value, 0)

    def test_memory_probe_survives_odd_offset_quantization(self):
        api = FakeNvml()
        def quantized_write(kind, value):
            api.value = int(value / 2) * 2
        api.write = quantized_write
        result = nvidia_clocks.operate(api, "probe", "memory")
        self.assertEqual(result["value"], 0)
        self.assertEqual(api.value, 0)

    def test_set_retains_verified_value(self):
        api = FakeNvml()
        self.assertEqual(nvidia_clocks.operate(api, "set", "core", 50)["value"], 50)
        self.assertEqual(api.value, 50)

    def test_range_rejection_never_writes(self):
        api = FakeNvml()
        with self.assertRaises(ValueError):
            nvidia_clocks.operate(api, "set", "core", 101)
        self.assertEqual(api.writes, [])

    def test_noop_success_is_rejected(self):
        api = FakeNvml()
        api.write = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "did not retain"):
            nvidia_clocks.operate(api, "probe", "core")

    def test_restore_failure_is_not_fallback_eligible(self):
        api = FakeNvml()
        write = api.write
        def fail_restore(kind, value):
            if value == 0:
                raise nvidia_clocks.NvmlError(4, "permission denied")
            write(kind, value)
        api.write = fail_restore
        api.close = mock.Mock()
        with mock.patch.object(nvidia_clocks, "Nvml", return_value=api):
            result = nvidia_clocks.run("probe", "core", "0000:01:00.0")
        self.assertFalse(result["ok"])
        self.assertNotIn("code", result)
        self.assertNotIn("unavailable", result)
        self.assertIn("restore", result["error"])

    def test_failed_readback_still_attempts_restoration(self):
        api = FakeNvml()
        original_read = api.read
        reads = 0
        def flaky_read(kind):
            nonlocal reads
            reads += 1
            if reads in (2, 3):
                raise RuntimeError("read unavailable")
            return original_read(kind)
        api.read = flaky_read
        with self.assertRaisesRegex(RuntimeError, "read unavailable"):
            nvidia_clocks.operate(api, "probe", "core")
        self.assertEqual(api.value, 0)
        self.assertEqual(api.writes[-1], ("core", 0))


class RoutingTests(unittest.TestCase):
    def setUp(self):
        hardware._nvidia_offset_backends.clear()

    def tearDown(self):
        hardware._nvidia_offset_backends.clear()

    def test_nvml_works_without_display(self):
        with mock.patch.object(hardware, "nvidia_access_error", return_value=None), \
             mock.patch.object(hardware, "primary_nvidia_pci_bus", return_value="0000:01:00.0"), \
             mock.patch.object(hardware, "_run_nvml_offset", side_effect=[
                 dict(ok=True, value=0, minimum=-100, maximum=100),
                 dict(ok=True, value=25)]) as bridge, \
             mock.patch.object(hardware, "_nvidia_settings_session") as session:
            self.assertEqual(hardware.set_nvidia_clock_offset("core", 25), (True, "25"))
            session.assert_not_called()
            self.assertEqual(bridge.call_args_list[-1].args[:4],
                             ("set", "core", "0000:01:00.0", 25))

    def test_blocked_or_absent_gpu_never_calls_bridge(self):
        for error in (hardware.CARDWIRE_BLOCKED_MESSAGE, hardware.NO_DRIVER_MESSAGE):
            with mock.patch.object(hardware, "nvidia_access_error", return_value=error), \
                 mock.patch.object(hardware, "_run_nvml_offset") as bridge, \
                 mock.patch.object(hardware, "primary_nvidia_pci_bus") as pci:
                self.assertFalse(hardware.detect_nvidia_offset("core")["ok"])
                self.assertEqual(hardware.set_nvidia_clock_offset("core", 0), (False, error))
                bridge.assert_not_called()
                pci.assert_not_called()

    def test_uncertain_failure_does_not_fallback(self):
        with mock.patch.object(hardware, "nvidia_access_error", return_value=None), \
             mock.patch.object(hardware, "primary_nvidia_pci_bus", return_value="0000:01:00.0"), \
             mock.patch.object(hardware, "_run_nvml_offset", return_value=dict(ok=False, error="restore failed")), \
             mock.patch.object(hardware, "probe_nvidia_clock_offset") as fallback:
            self.assertFalse(hardware.detect_nvidia_offset("core")["ok"])
            fallback.assert_not_called()

    def test_unsupported_nvml_uses_verified_x_fallback(self):
        with mock.patch.object(hardware, "nvidia_access_error", return_value=None), \
             mock.patch.object(hardware, "primary_nvidia_pci_bus", return_value="0000:01:00.0"), \
             mock.patch.object(hardware, "_run_nvml_offset", return_value=dict(ok=False, code=3)), \
             mock.patch.object(hardware, "probe_nvidia_clock_offset", return_value=True), \
             mock.patch.object(hardware, "_nvidia_settings_session", return_value=({"DISPLAY": ":0"}, None)), \
             mock.patch.object(hardware, "nvidia_settings_gpu_target", return_value="[gpu:2]"), \
             mock.patch.object(hardware, "_read_nvidia_clock_offset", return_value=(0, -100, 100)):
            self.assertEqual(hardware.detect_nvidia_offset("memory")["backend"], "nvidia-settings")

    def test_supergfx_integrated_and_cardwire_smart_block_detection(self):
        for cardwire, mode in ((False, "Integrated"), (True, "Integrated"), (True, "Smart")):
            with mock.patch.object(hardware.graphics_backend, "using_cardwire", return_value=cardwire), \
                 mock.patch.object(hardware, "read_gpu_mode", return_value=mode), \
                 mock.patch.object(hardware, "primary_nvidia_pci_bus") as pci:
                self.assertFalse(hardware.detect_nvidia_offset("core")["ok"])
                pci.assert_not_called()

    def test_transport_failure_never_signals_safe_fallback(self):
        with mock.patch.object(hardware, "run_helper", return_value=(False, "timeout")):
            self.assertNotIn("unavailable", hardware._run_nvml_offset("probe", "core", "0000:01:00.0"))

    def test_sudo_preexecution_denial_allows_fallback(self):
        with mock.patch.object(hardware, "run_helper", return_value=(False, "sudo: a password is required")):
            self.assertTrue(hardware._run_nvml_offset("probe", "core", "0000:01:00.0")["unavailable"])

    def test_missing_smi_uses_unambiguous_proc_identity(self):
        with mock.patch.object(hardware, "primary_nvidia_pci_bus", return_value=None), \
             mock.patch.object(hardware.os, "listdir", return_value=["0000:01:00.0"]):
            self.assertEqual(hardware._offset_pci_bus(1), "0000:01:00.0")

    def test_missing_smi_does_not_guess_among_multiple_gpus(self):
        with mock.patch.object(hardware, "primary_nvidia_pci_bus", return_value=None), \
             mock.patch.object(hardware.os, "listdir", return_value=["0000:01:00.0", "0000:02:00.0"]):
            self.assertIsNone(hardware._offset_pci_bus(1))

    def test_timeout_allows_graceful_restoration_before_hard_kill(self):
        proc = mock.Mock(pid=1234)
        proc.communicate.side_effect = [subprocess.TimeoutExpired("nvclock", 1), ("", ""), ("", "")]
        proc.poll.return_value = 0
        with mock.patch.object(hardware, "nvidia_access_error", return_value=None), \
             mock.patch.object(hardware.subprocess, "Popen", return_value=proc), \
             mock.patch.object(hardware.os, "killpg") as kill:
            self.assertEqual(hardware.run_helper("nvclock", "probe", "core", "0000:01:00.0", timeout=1), (False, "timed out"))
            kill.assert_called_once_with(1234, signal.SIGTERM)

    def test_headless_enforcer_retries_offsets(self):
        enforcer = load_enforcer()
        enforcer._pending_gpu_offsets = {"core": 25}
        with mock.patch.object(enforcer.hardware, "nvidia_access_error", return_value=None), \
             mock.patch.object(enforcer.hardware, "session_display_ready", return_value=False), \
             mock.patch.object(enforcer.hardware, "set_nvidia_clock_offset", return_value=(True, "25")) as write, \
             mock.patch.object(enforcer, "log"):
            enforcer.retry_pending_gpu_offsets()
            write.assert_called_once_with("core", 25)
            self.assertEqual(enforcer._pending_gpu_offsets, {})

    def test_busy_offsets_queue_latest_profile_value_including_stock(self):
        enforcer = load_enforcer()
        with mock.patch.object(enforcer.hardware, "set_nvidia_clock_offset",
                               return_value=(False, hardware.NVIDIA_CLOCK_BUSY_MESSAGE)), \
             mock.patch.object(enforcer, "log"):
            enforcer.set_clock_offset("core", 100)
            enforcer.set_clock_offset("core", 0)
            self.assertEqual(enforcer._pending_gpu_offsets, {"core": 0})
