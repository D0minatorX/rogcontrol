"""A rejected NVIDIA power limit must never be reported as applied."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


HELPER = Path(__file__).parents[1] / "rogcontrol" / "rogcontrol-helper"


class GpuPowerReportingTests(unittest.TestCase):
    def run_gpu(self, setter_output, setter_exit, held_limit):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "nvidia-smi"
            fake.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = '-pl' ]; then\n"
                f"  echo '{setter_output}'\n  exit {setter_exit}\n"
                "fi\n"
                "if [ \"$1\" = '-q' ]; then\n"
                f"  echo 'Current Power Limit : {held_limit} W'\n"
                "fi\n")
            fake.chmod(0o755)
            env = {**os.environ, "PATH": f"{root}:{os.environ['PATH']}"}
            return subprocess.run([str(HELPER), "gpu", "80"], env=env,
                                  capture_output=True, text=True)

    def test_zero_exit_with_refusal_and_unchanged_limit_fails(self):
        result = self.run_gpu("Changing power management limit is not supported in current scope. All done.", 0, "140.00")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("140", result.stderr)

    def test_success_requires_readback_of_requested_limit(self):
        result = self.run_gpu("Power limit set", 0, "80.00")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unavailable_readback_does_not_count_as_success(self):
        result = self.run_gpu("All done", 0, "N/A")
        self.assertNotEqual(result.returncode, 0)

    def test_command_failure_is_preserved(self):
        result = self.run_gpu("permission denied", 1, "140.00")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("permission denied", result.stderr)


if __name__ == "__main__":
    unittest.main()
