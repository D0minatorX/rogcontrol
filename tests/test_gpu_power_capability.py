"""Power limit visibility follows a successful write/readback probe."""

import unittest
from unittest.mock import patch

from rogcontrol import hardware


class GpuPowerCapabilityTests(unittest.TestCase):
    def test_unavailable_settable_limit_is_not_advertised(self):
        with patch.object(hardware, "read_nvidia_query", return_value=(None, 5.0, 140.0)), \
             patch.object(hardware, "run_helper") as helper:
            self.assertFalse(hardware.gpu_power_limit_supported())
            helper.assert_not_called()

    def test_changed_and_restored_limit_is_advertised(self):
        with patch.object(hardware, "read_nvidia_query", return_value=(100.0, 5.0, 140.0)), \
             patch.object(hardware, "run_helper", side_effect=[(True, ""), (True, "")]) as helper:
            self.assertTrue(hardware.gpu_power_limit_supported())
            self.assertEqual(helper.call_args_list[0].args, ("gpu", 99))
            self.assertEqual(helper.call_args_list[1].args, ("gpu", 100))

    def test_refused_changed_limit_is_not_advertised(self):
        with patch.object(hardware, "read_nvidia_query", return_value=(100.0, 5.0, 140.0)), \
             patch.object(hardware, "run_helper", return_value=(False, "not supported")) as helper:
            self.assertFalse(hardware.gpu_power_limit_supported())
            helper.assert_called_once_with("gpu", 99)


if __name__ == "__main__":
    unittest.main()
