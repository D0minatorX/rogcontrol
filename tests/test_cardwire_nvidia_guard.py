"""Cardwire policy must stop NVIDIA subprocesses before they are launched."""

import unittest
from unittest import mock

from rogcontrol import hardware


class CardwireNvidiaGuardTests(unittest.TestCase):
    def test_privileged_gpu_writes_do_not_launch_while_blocked(self):
        for action, value in (("gpu", 95), ("gpuclocklimit", 1800)):
            with self.subTest(action=action), \
                 mock.patch.object(hardware, "nvidia_access_error",
                                   return_value=hardware.CARDWIRE_BLOCKED_MESSAGE), \
                 mock.patch.object(hardware.subprocess, "Popen") as launch:
                launch.return_value.communicate.return_value = ("", "")
                launch.return_value.returncode = 0
                ok, message = hardware.run_helper(action, value)
                self.assertFalse(ok)
                self.assertEqual(message, hardware.CARDWIRE_BLOCKED_MESSAGE)
                launch.assert_not_called()

    def test_gpu_target_lookup_skips_nvidia_tools_while_blocked(self):
        with mock.patch.object(hardware, "nvidia_access_error",
                               return_value=hardware.CARDWIRE_BLOCKED_MESSAGE), \
             mock.patch.object(hardware.subprocess, "run") as launch:
            self.assertIsNone(hardware.nvidia_settings_gpu_target({}, 5))
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
