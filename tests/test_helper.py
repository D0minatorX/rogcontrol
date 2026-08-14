"""Calling the privileged helper: the retry, and what a failure reports.

Nine "cpu ... failed: no compatible ryzen_smu kernel module found, fallback
to /dev/mem" lines in the user's log are what this module exists to stop.
That message is a lie twice over. It is a WARNING ryzenadj prints on every
single run on a machine with no ryzen_smu module, successful runs included --
it is the first line of stderr, so reporting the first thing on stderr
reported the warning instead of the reason. And the failures behind it were
transient: ryzenadj reaches the SMU through /dev/mem here, that path
occasionally loses a race, and the identical call works a moment later.

Two halves, tested separately because they live on opposite sides of sudo:

* The retry is in the bash helper. It is exercised by running the real
  script with a stub standing in for ryzenadj -- a copy of the script with
  the ryzenadj path rewritten, so the branch under test is the shipped text
  and only the binary it calls is fake. Nothing here needs root: the stub is
  what gets called, and the validation above it never touches hardware.

* The message filtering is a pure function over a finished subprocess
  result, so it is called directly with stand-ins for the results that
  matter, including the exact streams a real failing ryzenadj produces on
  this machine.
"""

import importlib.util
import os
import stat
import subprocess
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from rogcontrol import hardware, profiles

PACKAGE_DIR = Path(profiles.__file__).resolve().parent
HELPER = PACKAGE_DIR / "rogcontrol-helper"
ENFORCER = PACKAGE_DIR / "rogcontrol-enforcer.py"

# A valid cpu call: every value inside the helper's safety ranges, so the
# tests below reach ryzenadj rather than stopping at a range check.
GOOD_ARGS = ["35000", "50000", "35000", "80", "-5"]

# The warning ryzenadj prints on this machine every time it runs.
WARNING = "no compatible ryzen_smu kernel module found, fallback to /dev/mem"

# What a genuinely failing ryzenadj prints here, verified by running it.
# Note which stream is which: the reason is on STDOUT, and stderr carries
# the warning plus repeated library chatter. Reading stderr alone could not
# have found the reason even with the warning filtered out.
REAL_FAILURE_STDOUT = ("PCI Bus is not writeable, check secure boot\n"
                       "Unable to get MP1 SMU Obj\n"
                       "Unable to init ryzenadj\n")
REAL_FAILURE_STDERR = (
    WARNING + "\n"
    + "pcilib: Cannot open /sys/bus/pci/devices/0000:00:00.0/config\n" * 5)


def result(returncode=1, stdout="", stderr=""):
    """A stand-in for subprocess.CompletedProcess."""
    return types.SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr=stderr)


class HelperRetry(unittest.TestCase):
    """The cpu branch retries ryzenadj once, and only once."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.calls = os.path.join(self.tmp, "calls")
        self.stub = os.path.join(self.tmp, "ryzenadj")
        self.helper = os.path.join(self.tmp, "rogcontrol-helper")

    def install_stub(self, body):
        """A fake ryzenadj that records every call, then runs ``body``.

        ``body`` sees $N, the number of this call, counting from 1."""
        script = ("#!/bin/bash\n"
                  f'echo "$@" >> "{self.calls}"\n'
                  f'N=$(wc -l < "{self.calls}")\n'
                  + body + "\n")
        with open(self.stub, "w") as f:
            f.write(script)
        os.chmod(self.stub, 0o755)

        # The shipped script, with only the ryzenadj path swapped. Everything
        # the tests care about -- the validation, the retry, the sleep, the
        # streams -- is the real text.
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn("/usr/local/bin/ryzenadj", text)
        text = text.replace("/usr/local/bin/ryzenadj", self.stub)
        with open(self.helper, "w") as f:
            f.write(text)
        os.chmod(self.helper, os.stat(self.helper).st_mode | stat.S_IEXEC)

    def run_cpu(self, *args):
        return subprocess.run(
            ["bash", self.helper, "cpu", *(args or GOOD_ARGS)],
            capture_output=True, text=True, timeout=30)

    def call_count(self):
        if not os.path.exists(self.calls):
            return 0
        with open(self.calls) as f:
            return len([line for line in f if line.strip()])

    def test_success_first_time_does_not_retry(self):
        """The common case must stay one call. A retry on success would
        double every apply the enforcer makes."""
        self.install_stub("exit 0")
        completed = self.run_cpu()
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(self.call_count(), 1)

    def test_a_transient_failure_is_retried_and_succeeds(self):
        """The whole point: first call fails, second works, caller sees
        success and never hears about it."""
        self.install_stub(
            f'if [ "$N" = "1" ]; then echo "{WARNING}" >&2; exit 1; fi\n'
            "exit 0")
        completed = self.run_cpu()
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(self.call_count(), 2)

    def test_the_retry_passes_the_same_validated_values(self):
        """The second attempt must be the same call, not a weakened one."""
        self.install_stub('if [ "$N" = "1" ]; then exit 1; fi\nexit 0')
        self.run_cpu()
        with open(self.calls) as f:
            attempts = [line.strip() for line in f if line.strip()]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(
            attempts[0],
            "--stapm-limit=35000 --fast-limit=50000 --slow-limit=35000 "
            "--tctl-temp=80 --set-coall=-5")

    def test_a_real_failure_is_reported_after_exactly_two_attempts(self):
        """Not a loop. A ryzenadj that is broken rather than unlucky fails
        the same way twice, and the caller must hear so promptly."""
        self.install_stub(
            f'printf %s "{REAL_FAILURE_STDOUT}"\n'
            f'printf %s "{REAL_FAILURE_STDERR}" >&2\n'
            "exit 1")
        completed = self.run_cpu()
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self.call_count(), 2)

    def test_the_last_attempts_streams_reach_the_caller(self):
        """Both streams pass through untouched, so the message filter has
        the real reason to work with."""
        self.install_stub(
            f'printf %s "{REAL_FAILURE_STDOUT}"\n'
            f'printf %s "{REAL_FAILURE_STDERR}" >&2\n'
            "exit 1")
        completed = self.run_cpu()
        self.assertIn("PCI Bus is not writeable", completed.stdout)
        self.assertIn("pcilib", completed.stderr)
        # End to end: what the log would have said about this failure.
        self.assertIn("PCI Bus is not writeable",
                      hardware.helper_error_message(completed))

    def test_the_retry_waits_before_trying_again(self):
        """An instant retry re-runs into the same contention. The pause is
        the reason the second attempt has a different outcome."""
        self.install_stub('if [ "$N" = "1" ]; then exit 1; fi\nexit 0')
        started = time.monotonic()
        self.run_cpu()
        self.assertGreaterEqual(time.monotonic() - started, 0.25)

    def test_a_single_attempt_does_not_wait(self):
        """The pause must not be paid by every successful apply."""
        self.install_stub("exit 0")
        started = time.monotonic()
        self.run_cpu()
        self.assertLess(time.monotonic() - started, 0.25)

    def test_validation_still_rejects_out_of_range_values(self):
        """The retry sits below the safety checks and must not have moved
        them: a value outside the range never reaches ryzenadj, once or
        twice."""
        self.install_stub("exit 0")
        for args, expected in (
                (["9000", "50000", "35000", "80", "-5"], "stapm-limit"),
                (["35000", "200000", "35000", "80", "-5"], "fast-limit"),
                (["35000", "50000", "9000", "80", "-5"], "slow-limit"),
                (["35000", "50000", "35000", "110", "-5"], "tctl-temp"),
                (["35000", "50000", "35000", "80", "-50"], "coall"),
                (["abc", "50000", "35000", "80", "-5"], "Invalid numeric"),
                (["35000", "50000", "35000", "80", "x"], "Invalid coall")):
            with self.subTest(args=args):
                if os.path.exists(self.calls):
                    os.remove(self.calls)
                completed = self.run_cpu(*args)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(expected, completed.stderr)
                self.assertEqual(self.call_count(), 0)


class ErrorMessage(unittest.TestCase):
    """helper_error_message: a finished result -> what the log should say."""

    def test_the_known_warning_never_surfaces_alone(self):
        """The exact line the user saw nine times."""
        msg = hardware.helper_error_message(result(stderr=WARNING + "\n"))
        self.assertNotIn("ryzen_smu kernel module found", msg)

    def test_warning_only_says_there_was_no_message(self):
        """Nothing else was printed, so say that rather than dressing the
        warning up as the reason."""
        msg = hardware.helper_error_message(result(stderr=WARNING + "\n"))
        self.assertIn("no message", msg)
        self.assertIn("exit 1", msg)

    def test_the_real_reason_survives_the_filter(self):
        """The streams a really-failing ryzenadj produces on this machine."""
        msg = hardware.helper_error_message(
            result(stdout=REAL_FAILURE_STDOUT, stderr=REAL_FAILURE_STDERR))
        self.assertIn("PCI Bus is not writeable, check secure boot", msg)
        self.assertIn("Unable to init ryzenadj", msg)
        self.assertNotIn("ryzen_smu kernel module found", msg)

    def test_the_reason_comes_first(self):
        """ryzenadj puts the reason on stdout and chatter on stderr, so
        stdout leads -- a truncated log line still carries the reason."""
        msg = hardware.helper_error_message(
            result(stdout=REAL_FAILURE_STDOUT, stderr=REAL_FAILURE_STDERR))
        self.assertTrue(msg.startswith("PCI Bus is not writeable"), msg)

    def test_repeated_lines_collapse(self):
        """A failing ryzenadj prints the same pcilib line five times."""
        msg = hardware.helper_error_message(
            result(stdout=REAL_FAILURE_STDOUT, stderr=REAL_FAILURE_STDERR))
        self.assertEqual(msg.count("pcilib: Cannot open"), 1)

    def test_a_helper_validation_error_is_unchanged(self):
        """Every other branch of the helper reports on stderr with nothing
        on stdout, and those messages must pass through verbatim."""
        msg = hardware.helper_error_message(
            result(stderr="stapm-limit out of safe range (15000-150000)\n"))
        self.assertEqual(msg, "stapm-limit out of safe range (15000-150000)")

    def test_no_output_at_all(self):
        msg = hardware.helper_error_message(result(returncode=127))
        self.assertIn("unknown error", msg)
        self.assertIn("exit 127", msg)

    def test_the_filter_is_not_case_or_position_sensitive(self):
        """The warning is matched wherever in the output it lands."""
        msg = hardware.helper_error_message(
            result(stderr="something broke\n" + WARNING.upper() + "\n"))
        self.assertEqual(msg, "something broke")


class FailureIsTheExitCode(unittest.TestCase):
    """Both run_helpers report failure on a non-zero exit code and nothing
    else. ``cpu`` writes to stderr on every run here, so a run_helper that
    took output for failure would log every successful apply as an error --
    which is one of the explanations the nine log lines had to be checked
    against."""

    def test_hardware_run_helper_ignores_stderr_on_success(self):
        completed = result(returncode=0, stdout="", stderr=WARNING + "\n")
        with mock.patch.object(hardware.subprocess, "run",
                               return_value=completed):
            ok, msg = hardware.run_helper("cpu", *GOOD_ARGS)
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_hardware_run_helper_reports_a_non_zero_exit(self):
        completed = result(returncode=1, stdout=REAL_FAILURE_STDOUT,
                           stderr=REAL_FAILURE_STDERR)
        with mock.patch.object(hardware.subprocess, "run",
                               return_value=completed):
            ok, msg = hardware.run_helper("cpu", *GOOD_ARGS)
        self.assertFalse(ok)
        self.assertIn("PCI Bus is not writeable", msg)

    def load_enforcer(self):
        spec = importlib.util.spec_from_file_location(
            "rogcontrol_enforcer_helper_test", ENFORCER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_enforcer_helper(self, completed):
        """The enforcer's run_helper against a canned result.

        Its log() is replaced rather than merely redirected: this suite must
        not append to the user's real log file."""
        enforcer = self.load_enforcer()
        logged = []
        enforcer.log = lambda message, level="INFO", **kw: logged.append(
            (level, message))
        with mock.patch.object(enforcer.subprocess, "run",
                               return_value=completed):
            ok = enforcer.run_helper("cpu", *GOOD_ARGS)
        return ok, logged

    def test_enforcer_run_helper_ignores_stderr_on_success(self):
        ok, logged = self.run_enforcer_helper(
            result(returncode=0, stderr=WARNING + "\n"))
        self.assertTrue(ok)
        self.assertEqual(logged, [])

    def test_enforcer_run_helper_logs_the_real_reason(self):
        ok, logged = self.run_enforcer_helper(
            result(returncode=1, stdout=REAL_FAILURE_STDOUT,
                   stderr=REAL_FAILURE_STDERR))
        self.assertFalse(ok)
        self.assertEqual(len(logged), 1)
        level, message = logged[0]
        self.assertEqual(level, "ERROR")
        self.assertIn("PCI Bus is not writeable", message)
        self.assertNotIn("ryzen_smu kernel module found", message)


if __name__ == "__main__":
    unittest.main()
