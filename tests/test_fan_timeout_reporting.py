"""Transient fan timeouts become errors only after a consecutive failure."""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from rogcontrol import hardware


class FanTimeoutReportingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        log_path = os.path.join(self.tempdir.name, "rogcontrol.log")
        for name, value in (
            ("LOG_PATH", log_path),
            ("LOG_LOCK_PATH", log_path + ".lock"),
            ("FAN_TIMEOUT_STATE_PATH", os.path.join(self.tempdir.name, "timeouts")),
            ("_last_logged", {}),
        ):
            patcher = patch.object(hardware, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.log_path = log_path

    def lines(self):
        with open(self.log_path) as f:
            return f.read().splitlines()

    def test_each_fan_needs_two_consecutive_timeouts(self):
        with patch.object(hardware, "run_helper", return_value=(False, "timed out")), \
             patch.object(hardware, "nvidia_driver_loaded", return_value=True):
            for channel in ("1", "2", "3"):
                hardware.run_fan_helper_logged(channel, source="apply")
                hardware.run_fan_helper_logged(channel, source="enforcer")

        lines = self.lines()
        for channel in ("1", "2", "3"):
            matching = [line for line in lines if f"fan {channel} (" in line]
            self.assertEqual(len(matching), 2)
            self.assertIn("[INFO]", matching[0])
            self.assertIn("[ERROR]", matching[1])

    def test_success_resets_timeout_streak(self):
        with patch.object(hardware, "run_helper", side_effect=[
            (False, "timed out"), (True, ""), (False, "timed out")
        ]):
            for _ in range(3):
                hardware.run_fan_helper_logged("1", source="apply")

        self.assertEqual(sum("[ERROR]" in line for line in self.lines()), 0)
        self.assertEqual(sum("[INFO]" in line for line in self.lines()), 2)

    def test_new_streak_after_success_reports_another_error(self):
        with patch.object(hardware, "run_helper", side_effect=[
            (False, "timed out"), (False, "timed out"), (True, ""),
            (False, "timed out"), (False, "timed out")
        ]):
            for _ in range(5):
                hardware.run_fan_helper_logged("1", source="apply")

        self.assertEqual(sum("[ERROR]" in line for line in self.lines()), 2)

    def test_timeout_streak_is_shared_between_processes(self):
        code = (
            "import os, sys; from unittest.mock import patch; "
            "from rogcontrol import hardware; "
            "hardware.FAN_TIMEOUT_STATE_PATH = os.path.join(sys.argv[1], 'timeouts'); "
            "hardware.LOG_PATH = os.path.join(sys.argv[1], 'rogcontrol.log'); "
            "hardware.LOG_LOCK_PATH = hardware.LOG_PATH + '.lock'; "
            "p = patch.object(hardware, 'run_helper', return_value=(False, 'timed out')); "
            "p.start(); hardware.run_fan_helper_logged('1', source='apply')"
        )
        subprocess.run([sys.executable, "-c", code, self.tempdir.name],
                       check=True, capture_output=True, text=True)
        with patch.object(hardware, "run_helper", return_value=(False, "timed out")):
            hardware.run_fan_helper_logged("1", source="enforcer")

        lines = self.lines()
        self.assertEqual(len(lines), 2)
        self.assertIn("[INFO] apply:", lines[0])
        self.assertIn("[ERROR] enforcer:", lines[1])

    def test_second_gpu_timeout_is_error_even_when_card_is_down(self):
        with patch.object(hardware, "run_helper", return_value=(False, "timed out")), \
             patch.object(hardware, "nvidia_driver_loaded", return_value=False):
            hardware.run_fan_helper_logged("2", source="apply")
            hardware.run_fan_helper_logged("2", source="enforcer")

        lines = self.lines()
        self.assertIn("[INFO]", lines[0])
        self.assertIn("[ERROR]", lines[1])

    def test_other_failure_is_immediate_error_and_breaks_streak(self):
        with patch.object(hardware, "run_helper", side_effect=[
            (False, "timed out"), (False, "permission denied"),
            (False, "timed out")
        ]):
            for _ in range(3):
                hardware.run_fan_helper_logged("1", source="apply")

        lines = self.lines()
        self.assertEqual(sum("[ERROR]" in line for line in lines), 1)
        self.assertIn("permission denied", next(line for line in lines if "[ERROR]" in line))


if __name__ == "__main__":
    unittest.main()
