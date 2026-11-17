"""GPU limits deferred by Cardwire and restored by the background service."""

import importlib.util
from pathlib import Path
import unittest
from unittest import mock

from rogcontrol import hardware


ENFORCER = Path(__file__).resolve().parents[1] / "rogcontrol" / "rogcontrol-enforcer.py"


def load_enforcer():
    spec = importlib.util.spec_from_file_location("enforcer_gpu_limits_test", ENFORCER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CardwireGpuLimitTests(unittest.TestCase):
    def test_blocked_limits_are_applied_after_access_returns(self):
        enforcer = load_enforcer()
        profile = {"watts": 95, "clock_limit": 1800}
        with mock.patch.object(enforcer.hardware, "nvidia_access_error",
                               side_effect=[hardware.CARDWIRE_BLOCKED_MESSAGE, None]), \
             mock.patch.object(enforcer.hardware, "gpu_power_limit_supported",
                               return_value=True), \
             mock.patch.object(enforcer.hardware, "gpu_clock_limit_max",
                               return_value=2200), \
             mock.patch.object(enforcer, "run_nvidia_helper",
                               return_value=True) as write:
            enforcer.retry_pending_gpu_limits(profile)
            write.assert_not_called()
            enforcer.retry_pending_gpu_limits(profile)
            self.assertEqual(write.call_args_list,
                             [mock.call("gpu", 95),
                              mock.call("gpuclocklimit", 1800)])
            self.assertEqual(enforcer._pending_gpu_limits, {})

    def test_pending_limits_follow_profile_changes_while_blocked(self):
        enforcer = load_enforcer()
        with mock.patch.object(enforcer.hardware, "nvidia_access_error",
                               side_effect=[hardware.CARDWIRE_BLOCKED_MESSAGE] * 2 + [None]), \
             mock.patch.object(enforcer.hardware, "gpu_power_limit_supported",
                               return_value=True), \
             mock.patch.object(enforcer, "run_nvidia_helper",
                               return_value=True) as write:
            enforcer.retry_pending_gpu_limits({"watts": 80})
            enforcer.retry_pending_gpu_limits({"watts": 100})
            enforcer.retry_pending_gpu_limits({"watts": 100})
            write.assert_called_once_with("gpu", 100)

    def test_failed_limit_stays_pending_for_next_retry(self):
        enforcer = load_enforcer()
        with mock.patch.object(enforcer.hardware, "nvidia_access_error",
                               side_effect=[hardware.CARDWIRE_BLOCKED_MESSAGE, None, None]), \
             mock.patch.object(enforcer.hardware, "gpu_power_limit_supported",
                               return_value=True), \
             mock.patch.object(enforcer, "run_nvidia_helper",
                               side_effect=[False, True]) as write:
            enforcer.retry_pending_gpu_limits({"watts": 95})
            enforcer.retry_pending_gpu_limits({"watts": 95})
            self.assertEqual(enforcer._pending_gpu_limits, {"watts": 95})
            enforcer.retry_pending_gpu_limits({"watts": 95})
            self.assertEqual(write.call_count, 2)
            self.assertEqual(enforcer._pending_gpu_limits, {})

    def test_power_capability_probe_can_recover_after_hybrid(self):
        enforcer = load_enforcer()
        with mock.patch.object(enforcer.hardware, "nvidia_access_error",
                               side_effect=[hardware.CARDWIRE_BLOCKED_MESSAGE,
                                            None, None]), \
             mock.patch.object(enforcer.hardware, "gpu_power_limit_supported",
                               side_effect=[False, True]), \
             mock.patch.object(enforcer, "run_nvidia_helper",
                               return_value=True) as write:
            enforcer.retry_pending_gpu_limits({"watts": 95})
            enforcer.retry_pending_gpu_limits({"watts": 95})
            write.assert_not_called()
            enforcer.retry_pending_gpu_limits({"watts": 95})
            write.assert_called_once_with("gpu", 95)


if __name__ == "__main__":
    unittest.main()
