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

The retry was not the whole story. Two ryzenadj runs at the same instant
collide in the /dev/mem SMU mailbox and BOTH fail -- measured, with the real
helper: ten concurrent pairs produced three failures, another eight produced
seven, and the failing runs left the machine half-configured (power limits
set, tctl-temp and coall "rejected by SMU"). The enforcer re-applies every 60
seconds while the window applies on demand, so the two overlap sooner or
later. The cpu action therefore takes an exclusive lock, tested below with a
fake ryzenadj that reports on any second copy of itself running at the same
time.
"""

import fcntl
import importlib.util
import os
import re
import signal
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

# The one sysfs file the bootsound action writes, swapped for a temporary
# file below so the branch under test is the shipped text.
BOOT_SOUND_PATH = "/sys/devices/platform/asus-nb-wmi/boot_sound"

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


class SmuLock(unittest.TestCase):
    """The cpu action is exclusive: concurrent callers queue, never collide.

    Same approach as HelperRetry above -- the shipped script with only the
    ryzenadj path swapped -- but the stub here is a fake ryzenadj that
    notices a second copy of itself running at the same time and fails the
    way the real one does when that happens. That makes the collision the
    tests are about observable without root and without an SMU.

    The lock file goes wherever ROGCONTROL_HELPER_LOCK_DIR says, which the
    helper honours only for a non-root caller: under sudo the path is fixed,
    so no environment can aim a root-owned write. That is also why this
    whole class is skipped when the suite is run as root.
    """

    # How long the fake ryzenadj stays "inside" the SMU. Long enough that two
    # processes started microseconds apart are certain to overlap if nothing
    # stops them, short enough not to slow the suite down.
    HOLD = "0.15"

    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest("the lock directory override is ignored for root, "
                          "by design")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.lockdir = os.path.join(self.tmp, "lock")
        os.mkdir(self.lockdir)
        self.lockfile = os.path.join(self.lockdir, "rogcontrol-helper.lock")
        self.inflight = os.path.join(self.tmp, "inflight")
        self.overlaps = os.path.join(self.tmp, "overlaps")
        self.calls = os.path.join(self.tmp, "calls")
        self.stub = os.path.join(self.tmp, "ryzenadj")
        self.text = HELPER.read_text(encoding="utf-8")
        self.assertIn("/usr/local/bin/ryzenadj", self.text)
        self.write_stub()

    # -- the fake ryzenadj -------------------------------------------------

    def write_stub(self, body=None):
        """A ryzenadj that records overlapping runs, then runs ``body``.

        Each run drops a file named after its pid while it is "inside" and
        removes it on the way out, so a second run that starts before the
        first has finished sees two and says so -- in the overlaps file, and
        by failing with the output a really-collided ryzenadj produces."""
        script = f"""#!/bin/bash
mkdir -p "{self.inflight}"
ME="{self.inflight}/$$"
: > "$ME"
BEFORE=$(ls "{self.inflight}" | wc -l)
sleep {self.HOLD}
AFTER=$(ls "{self.inflight}" | wc -l)
rm -f "$ME"
echo "$@" >> "{self.calls}"
if [ "$BEFORE" -gt 1 ] || [ "$AFTER" -gt 1 ]; then
    echo overlap >> "{self.overlaps}"
    echo "PCI Bus is not writeable, check secure boot"
    echo "{WARNING}" >&2
    exit 1
fi
{body or "exit 0"}
"""
        with open(self.stub, "w") as f:
            f.write(script)
        os.chmod(self.stub, 0o755)

    # -- the script under test ---------------------------------------------

    def build(self, locked=True, wait=None, name=None):
        """A copy of the shipped helper, calling the stub.

        ``locked=False`` removes the one line that takes the lock and
        changes nothing else: that is the helper as it was before the fix,
        and it is what the control test needs to show the fake ryzenadj can
        see a collision at all."""
        text = self.text.replace("/usr/local/bin/ryzenadj", self.stub)
        if not locked:
            marker = "\n        take_smu_lock\n"
            self.assertIn(marker, text)
            text = text.replace(marker, "\n")
        if wait is not None:
            text, n = re.subn(r"(?m)^SMU_LOCK_WAIT=\d+$",
                              f"SMU_LOCK_WAIT={wait}", text)
            self.assertEqual(n, 1)
        path = os.path.join(self.tmp, name or f"helper-{locked}-{wait}")
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    def env(self):
        return dict(os.environ, ROGCONTROL_HELPER_LOCK_DIR=self.lockdir)

    def run_cpu(self, helper, *args, **kw):
        return subprocess.run(
            ["bash", helper, "cpu", *(args or GOOD_ARGS)],
            capture_output=True, text=True, env=self.env(), timeout=60, **kw)

    def start_cpu(self, helper, *args, **kw):
        return subprocess.Popen(
            ["bash", helper, "cpu", *(args or GOOD_ARGS)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=self.env(), **kw)

    def run_pairs(self, helper, pairs=4):
        """Two applies started at the same instant, ``pairs`` times over.

        Pairs rather than one big burst because that is the shape of the
        real thing: the enforcer's 60-second re-apply landing on top of an
        apply from the window. The failure is intermittent, so one pair
        would prove nothing either way."""
        codes = []
        for _ in range(pairs):
            a = self.start_cpu(helper)
            b = self.start_cpu(helper)
            a.communicate()
            b.communicate()
            codes.extend([a.returncode, b.returncode])
        return codes

    def overlap_count(self):
        if not os.path.exists(self.overlaps):
            return 0
        with open(self.overlaps) as f:
            return len([line for line in f if line.strip()])

    # -- the fix ------------------------------------------------------------

    def test_concurrent_applies_never_overlap(self):
        """The point of the lock: whatever the callers do, only one process
        is inside ryzenadj at a time, so none of them collides."""
        helper = self.build(locked=True)
        codes = self.run_pairs(helper)
        self.assertEqual(self.overlap_count(), 0)
        self.assertEqual(codes, [0] * len(codes))

    def test_without_the_lock_the_same_applies_do_collide(self):
        """The control. Without that one line the pairs overlap and fail --
        which is both the bug and the proof that the test above is testing
        something."""
        helper = self.build(locked=False)
        codes = self.run_pairs(helper, pairs=3)
        self.assertGreater(self.overlap_count(), 0)
        self.assertTrue(any(c != 0 for c in codes), codes)

    def test_a_successful_apply_exits_zero(self):
        """The helper runs under ``set -e``. Taking a lock must not leave a
        successful apply reporting failure -- every apply takes this path."""
        helper = self.build(locked=True)
        completed = self.run_cpu(helper)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")

    def test_the_lock_is_taken_in_the_lock_directory(self):
        helper = self.build(locked=True)
        self.assertEqual(self.run_cpu(helper).returncode, 0)
        self.assertTrue(os.path.exists(self.lockfile))

    # -- the bounded wait ---------------------------------------------------

    def hold_the_lock(self):
        """Hold the helper's lock from this process, as a stuck apply would."""
        fd = os.open(self.lockfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                     0o644)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def test_the_shipped_wait_is_a_few_seconds(self):
        """The timing tests below run with the wait shortened, so that the
        suite does not spend the real one waiting. This is what keeps the
        shipped value honest -- it has to stay inside the caller timeouts
        (the window allows 10 seconds per helper call)."""
        wait = int(re.search(r"(?m)^SMU_LOCK_WAIT=(\d+)$", self.text).group(1))
        self.assertGreaterEqual(wait, 1)
        self.assertLessEqual(wait, 8)
        self.assertIn('flock -w "$SMU_LOCK_WAIT" 9', self.text)

    def test_a_caller_that_cannot_get_in_gives_up_and_says_why(self):
        """Nobody waits forever: the apply is refused, with a message that
        names the reason, rather than the caller's own timeout killing it
        with nothing to report."""
        self.hold_the_lock()
        helper = self.build(locked=True, wait=1)
        started = time.monotonic()
        completed = self.run_cpu(helper)
        elapsed = time.monotonic() - started
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("another rogcontrol CPU apply is in progress",
                      completed.stderr)
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 20)
        # Refused means refused: nothing was written on the way out.
        self.assertFalse(os.path.exists(self.calls))

    def test_an_invalid_value_is_refused_without_waiting_for_the_lock(self):
        """The lock sits below the safety checks. A value out of range is
        still rejected immediately -- it must not queue behind somebody
        else's apply only to be rejected afterwards."""
        self.hold_the_lock()
        helper = self.build(locked=True, wait=5)
        started = time.monotonic()
        completed = self.run_cpu(helper, "9000", "50000", "35000", "80", "-5")
        elapsed = time.monotonic() - started
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("stapm-limit out of safe range", completed.stderr)
        self.assertNotIn("in progress", completed.stderr)
        self.assertLess(elapsed, 4)

    # -- the lock always comes back ----------------------------------------

    def test_a_crashing_ryzenadj_leaves_no_stale_lock(self):
        """flock on a file descriptor, so the kernel drops it when the
        process dies. A ryzenadj killed mid-apply must not lock the machine
        out of every later apply."""
        self.write_stub("kill -9 $$")
        helper = self.build(locked=True, wait=1)
        self.assertNotEqual(self.run_cpu(helper).returncode, 0)
        self.write_stub()
        self.assertEqual(self.run_cpu(helper).returncode, 0)

    def test_a_killed_helper_leaves_no_stale_lock(self):
        """Same for the helper itself being killed while it holds the lock,
        which is what a caller's subprocess timeout does."""
        self.write_stub("sleep 30")
        helper = self.build(locked=True, wait=1)
        running = self.start_cpu(helper, start_new_session=True)
        # Wait until it really holds the lock, so this is a kill mid-apply.
        probe = os.open(self.lockfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                        0o644)
        self.addCleanup(os.close, probe)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                break
            fcntl.flock(probe, fcntl.LOCK_UN)
            time.sleep(0.05)
        else:
            self.fail("the helper never took the lock")
        os.killpg(os.getpgid(running.pid), signal.SIGKILL)
        running.communicate()
        # The killed stub never got to remove its own "I am inside" marker.
        # That is this fake's bookkeeping, not the helper's, so clear it --
        # what is under test is whether the lock came back.
        for stale in os.listdir(self.inflight):
            os.remove(os.path.join(self.inflight, stale))
        self.write_stub()
        completed = self.run_cpu(helper)
        self.assertNotIn("in progress", completed.stderr)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    # -- degrading rather than refusing ------------------------------------

    def test_an_apply_still_runs_when_there_is_nowhere_to_put_a_lock(self):
        """A machine with no writable lock directory is not a machine that
        should stop applying CPU limits: it gets today's behaviour, not a
        refusal."""
        helper = self.build(locked=True)
        completed = subprocess.run(
            ["bash", helper, "cpu", *GOOD_ARGS],
            capture_output=True, text=True, timeout=60,
            env=dict(os.environ,
                     ROGCONTROL_HELPER_LOCK_DIR=os.path.join(self.tmp, "gone")))
        self.assertEqual(completed.returncode, 0, completed.stderr)


class BootSound(unittest.TestCase):
    """The bootsound action: two values, and nothing else reaches the file.

    Run against the shipped script with only the sysfs path swapped for a
    temporary file -- the validation under test is the real text, and the
    write lands somewhere harmless. Nothing here needs root for the same
    reason: the only privileged thing about this action is where it normally
    writes.

    A fixed pair of values rather than a range check is the whole point. This
    is a root-owned write to a firmware knob, and the driver answers anything
    else with -EINVAL, so a helper that passed the caller's string through
    would turn a typo into an unexplained failure."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.sysfs = os.path.join(self.tmp, "boot_sound")
        self.helper = os.path.join(self.tmp, "rogcontrol-helper")
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn(BOOT_SOUND_PATH, text)
        with open(self.helper, "w") as f:
            f.write(text.replace(BOOT_SOUND_PATH, self.sysfs))

    def present(self, value="0"):
        """The machine has the control, currently holding ``value``."""
        with open(self.sysfs, "w") as f:
            f.write(f"{value}\n")

    def run_bootsound(self, *args):
        return subprocess.run(["bash", self.helper, "bootsound", *args],
                              capture_output=True, text=True, timeout=30)

    def written(self):
        with open(self.sysfs) as f:
            return f.read().strip()

    def test_on_is_written(self):
        self.present("0")
        completed = self.run_bootsound("1")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.written(), "1")

    def test_off_is_written(self):
        self.present("1")
        completed = self.run_bootsound("0")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.written(), "0")

    def test_nothing_but_zero_and_one_is_accepted(self):
        for bad in ("2", "-1", "on", "off", "true", "", "0 1", "1;reboot",
                    "01", " 1"):
            self.present("0")
            completed = self.run_bootsound(bad)
            self.assertEqual(completed.returncode, 1, f"{bad!r} was accepted")
            self.assertIn("bootsound takes 0 (off) or 1 (on)",
                          completed.stderr)
            # Refused means refused: the file is untouched.
            self.assertEqual(self.written(), "0", f"{bad!r} reached the file")

    def test_no_value_at_all_is_refused(self):
        self.present("0")
        completed = self.run_bootsound()
        self.assertEqual(completed.returncode, 1)
        self.assertIn("bootsound takes 0 (off) or 1 (on)", completed.stderr)
        self.assertEqual(self.written(), "0")

    def test_a_machine_without_the_control_says_so(self):
        # No file at all: the switch is greyed out in the window, but the
        # headless callers (boot-apply, a hand-run helper) need a reason
        # rather than a shell redirection error.
        completed = self.run_bootsound("1")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("no boot_sound control on this machine",
                      completed.stderr)

    def test_the_value_is_checked_before_the_file_is_looked_for(self):
        # A bad value is refused for being a bad value, on every machine.
        completed = self.run_bootsound("2")
        self.assertIn("bootsound takes 0 (off) or 1 (on)", completed.stderr)

    def test_it_is_listed_in_the_usage_line(self):
        # An action missing from usage is an action nobody finds.
        completed = subprocess.run(["bash", self.helper],
                                   capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("bootsound", completed.stderr)


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


class EppGovernorOrder(unittest.TestCase):
    """The governor is checked before the preference name is validated.

    While the governor is "performance" the kernel shortens
    ``energy_performance_available_preferences`` to that single word. With
    the name validated first, every profile carrying any other preference
    was rejected:

        invalid EPP preference 'balance_power'; machine offers: performance

    -- on every apply, logged by the enforcer once a cycle. The name was
    never invalid; the list was temporarily one entry long. The governor
    check has to come first so that case exits 0 quietly instead.
    """

    def cpuepp_block(self):
        src = HELPER.read_text(encoding="utf-8")
        start = src.index("\n    cpuepp)")
        return src[start:src.index("\n        ;;", start)]

    def test_the_governor_check_precedes_the_name_validation(self):
        block = self.cpuepp_block()
        self.assertLess(
            block.index("scaling_governor"), block.index("MATCH=0"),
            "the EPP name is validated before the governor is checked, which "
            "rejects every valid preference while the governor is performance")

    def test_a_performance_governor_exits_zero(self):
        block = self.cpuepp_block()
        gov = block.index("scaling_governor")
        self.assertIn("exit 0", block[gov:gov + 600],
                      "a performance governor must succeed quietly")
