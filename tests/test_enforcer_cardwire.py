import importlib.util
from pathlib import Path
import unittest
from unittest import mock

from rogcontrol import hardware


ENFORCER_PATH = (Path(__file__).resolve().parents[1]
                 / "rogcontrol" / "rogcontrol-enforcer.py")


def load_enforcer():
    spec = importlib.util.spec_from_file_location(
        "rogcontrol_enforcer_cardwire_test", ENFORCER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EnforcerCardwireTests(unittest.TestCase):
    def test_pending_gpu_work_is_not_probed_while_cardwire_blocks(self):
        enforcer = load_enforcer()
        enforcer._pending_gpu_offsets = {"core": 100}
        enforcer._pending_powermizer_mode = 2
        enforcer._pending_voltage_boost = 25

        with mock.patch.object(
                enforcer.hardware, "nvidia_access_error",
                return_value=hardware.CARDWIRE_BLOCKED_MESSAGE), \
                mock.patch.object(enforcer, "set_clock_offset") as clock, \
                mock.patch.object(enforcer, "set_powermizer_mode") as power, \
                mock.patch.object(enforcer, "set_voltage_boost") as voltage, \
                mock.patch.object(enforcer, "log") as log:
            enforcer.retry_pending_gpu_offsets()

        clock.assert_not_called()
        power.assert_not_called()
        voltage.assert_not_called()
        self.assertEqual(enforcer._pending_gpu_offsets, {"core": 100})
        self.assertEqual(enforcer._pending_powermizer_mode, 2)
        self.assertEqual(enforcer._pending_voltage_boost, 25)
        log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
