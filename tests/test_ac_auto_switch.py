"""The enforcer's AC/battery auto-switch.

This is decision logic with a plug on one end and a ~10 second hardware
apply on the other, and it can only be exercised for real by unplugging the
machine and waiting a minute. So the decision itself is a pure function of
(previous power state, current power state, config), and this pins it --
above all the cases where it must do *nothing*, which is what it does
almost every time it runs.

The enforcer is loaded from its path: ``rogcontrol-enforcer.py`` is a script
name, not an importable module name. Importing it is safe -- everything it
does at module level is build a lookup table -- but anything with a side
effect (the log file, the config file, the hardware) is replaced in the
tests below rather than merely avoided, because a test that quietly wrote to
the user's real config would be worse than no test.
"""

import importlib.util
import json
import os
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
from pathlib import Path

from rogcontrol import profiles

ENFORCER_PATH = (Path(profiles.__file__).resolve().parent
                 / "rogcontrol-enforcer.py")


def load_enforcer():
    """A fresh copy of the enforcer module.

    Fresh per test on purpose: the remembered power state is a module
    global, and a test that inherited another test's copy of it would pass
    or fail depending on the order they ran in."""
    spec = importlib.util.spec_from_file_location(
        "rogcontrol_enforcer_under_test", ENFORCER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_config(**overrides):
    """A config shaped like a real one, with only the keys this cares about.

    The profile bodies are empty dicts: what gets applied is not this
    module's business, only which profile is named."""
    config = {
        "current_profile": "Balanced Performance",
        "ac_profile": "Performance",
        "battery_profile": "Quiet",
        "profiles": {"Quiet": {}, "Balanced Performance": {},
                     "Performance": {}},
    }
    config.update(overrides)
    return config


ON_AC, ON_BATTERY, UNKNOWN = True, False, None


class Decision(unittest.TestCase):
    """ac_switch_target: (previous, current, config) -> profile name or None."""

    def setUp(self):
        self.enforcer = load_enforcer()

    def target(self, previous, current, config=None):
        return self.enforcer.ac_switch_target(
            previous, current, make_config() if config is None else config)

    # -- the two cases where it acts -----------------------------------------

    def test_plugging_in_switches_to_the_ac_profile(self):
        self.assertEqual(self.target(ON_BATTERY, ON_AC), "Performance")

    def test_unplugging_switches_to_the_battery_profile(self):
        self.assertEqual(self.target(ON_AC, ON_BATTERY), "Quiet")

    # -- acts only on a change -----------------------------------------------

    def test_still_on_ac_does_nothing(self):
        self.assertIsNone(self.target(ON_AC, ON_AC))

    def test_still_on_battery_does_nothing(self):
        self.assertIsNone(self.target(ON_BATTERY, ON_BATTERY))

    def test_unchanged_does_nothing_even_when_the_profile_was_changed_since(self):
        """The user picking something else must stick.

        Without the "only on a change" rule this would re-impose the AC
        profile every 60 seconds for as long as the laptop stayed plugged
        in."""
        config = make_config(current_profile="Quiet")
        self.assertIsNone(self.target(ON_AC, ON_AC, config))

    def test_the_first_sample_never_switches(self):
        """Startup is not a transition. The profile the config names is
        applied at startup anyway; treating "we came up on battery" as an
        unplug would override whatever the user last chose."""
        self.assertIsNone(self.target(None, ON_BATTERY))
        self.assertIsNone(self.target(None, ON_AC))

    def test_an_unreadable_power_source_does_nothing(self):
        """No Mains supply in sysfs -- a desktop, or a kernel that does not
        expose one. Nothing can be inferred from that, in either direction."""
        self.assertIsNone(self.target(ON_AC, UNKNOWN))
        self.assertIsNone(self.target(ON_BATTERY, UNKNOWN))
        self.assertIsNone(self.target(None, UNKNOWN))

    # -- null means "don't auto-switch" --------------------------------------

    def test_null_battery_profile_does_not_switch_on_unplug(self):
        config = make_config(battery_profile=None)
        self.assertIsNone(self.target(ON_AC, ON_BATTERY, config))

    def test_null_ac_profile_does_not_switch_on_plug_in(self):
        config = make_config(ac_profile=None)
        self.assertIsNone(self.target(ON_BATTERY, ON_AC, config))

    def test_null_for_one_source_leaves_the_other_working(self):
        """The two pickers are independent: "don't switch on battery" must
        not turn off switching on AC as well."""
        config = make_config(battery_profile=None)
        self.assertEqual(self.target(ON_BATTERY, ON_AC, config), "Performance")

    def test_a_missing_key_is_the_same_as_null(self):
        config = make_config()
        del config["ac_profile"]
        self.assertIsNone(self.target(ON_BATTERY, ON_AC, config))

    def test_an_empty_name_is_the_same_as_null(self):
        config = make_config(ac_profile="")
        self.assertIsNone(self.target(ON_BATTERY, ON_AC, config))

    # -- targets that cannot be honoured -------------------------------------

    def test_a_profile_that_no_longer_exists_is_not_switched_to(self):
        """Renamed or deleted since it was chosen in the Battery page."""
        config = make_config(ac_profile="Deleted Since")
        self.assertIsNone(self.target(ON_BATTERY, ON_AC, config))

    def test_a_config_with_no_profiles_at_all_does_not_raise(self):
        config = make_config()
        del config["profiles"]
        self.assertIsNone(self.target(ON_BATTERY, ON_AC, config))

    def test_a_target_that_is_already_active_is_left_alone(self):
        """Switching to where we already are would push all three fan
        curves for no reason."""
        config = make_config(current_profile="Performance")
        self.assertIsNone(self.target(ON_BATTERY, ON_AC, config))


class Cycle(unittest.TestCase):
    """check_ac_auto_switch: the sampling and remembering around the
    decision, which is where "acts only on a change" is actually enforced --
    the pure function is only ever as right as the state handed to it."""

    def setUp(self):
        self.enforcer = load_enforcer()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config_path = os.path.join(tmp.name, "rogcontrol.json")
        self.enforcer.CONFIG_PATH = self.config_path
        # Redirected before anything runs: the enforcer now remembers the
        # power source on disk, and the real path is in the user's own state
        # directory next to their log.
        self.state_path = os.path.join(tmp.name, "last-power-source")
        self.enforcer.AC_STATE_PATH = self.state_path

        self.ac = ON_BATTERY
        self.applied = []
        self.modes = []
        # Replaced rather than left alone: log() appends to the user's real
        # app log, and the other two reach the machine.
        self.enforcer.hardware = types.SimpleNamespace(
            is_ac_connected=lambda: self.ac)
        self.enforcer.log = lambda *a, **k: None
        self.enforcer.apply_full_profile = (
            lambda config, profile, **kwargs: self.applied.append(
                (config.get("current_profile"), kwargs)))
        self.enforcer.set_ppd_active_profile = (
            lambda service, mode: self.modes.append((service, mode)))
        self.notifications = []
        # notify() shells out to notify-send, which would put a real
        # notification on the developer's screen for every test in here.
        self.enforcer.notify = (
            lambda title, body: self.notifications.append((title, body)))

    def cycle(self, config, service="net.hadess.PowerProfiles"):
        return self.enforcer.check_ac_auto_switch(config, service)

    def test_the_first_cycle_only_remembers(self):
        config = make_config()
        self.assertFalse(self.cycle(config))
        self.assertEqual(self.applied, [])
        self.assertEqual(config["current_profile"], "Balanced Performance")

    def test_repeated_cycles_in_one_state_do_nothing(self):
        config = make_config()
        self.cycle(config)
        for _ in range(3):
            self.assertFalse(self.cycle(config))
        self.assertEqual(self.applied, [])

    def test_a_transition_switches_writes_and_applies(self):
        config = make_config()
        self.cycle(config)          # first sample: on battery
        self.ac = ON_AC
        self.assertTrue(self.cycle(config))

        self.assertEqual(config["current_profile"], "Performance")
        with open(self.config_path) as f:
            self.assertEqual(json.load(f)["current_profile"], "Performance")
        # Full apply, fans forced: the power-mode write below is exactly what
        # makes the EC drop the custom curve.
        self.assertEqual(self.applied, [("Performance",
                                         {"force_fan_reapply": True,
                                          "full": True})])
        # The OS power mode comes too, or the enforcer's own PPD check would
        # adopt the stale mode back on the next cycle and undo this.
        self.assertEqual(self.modes, [("net.hadess.PowerProfiles",
                                       "performance")])

    def test_it_does_not_switch_twice_for_one_transition(self):
        config = make_config()
        self.cycle(config)
        self.ac = ON_AC
        self.cycle(config)
        self.applied.clear()
        self.assertFalse(self.cycle(config))
        self.assertEqual(self.applied, [])

    def test_an_unreadable_reading_does_not_erase_what_was_remembered(self):
        """A sysfs read that comes back empty is not a power source change,
        and must not be remembered as one -- otherwise the next real reading
        looks like the first sample and the transition is missed."""
        config = make_config()
        self.ac = ON_AC
        self.cycle(config)
        self.ac = UNKNOWN
        self.assertFalse(self.cycle(config))
        self.ac = ON_BATTERY
        self.assertTrue(self.cycle(config))
        self.assertEqual(config["current_profile"], "Quiet")

    def test_a_custom_profile_switches_without_touching_the_power_mode(self):
        """A profile the user invented has no OS power mode to match, and
        inventing one would be worse than leaving the mode where it is."""
        config = make_config(ac_profile="Mine")
        config["profiles"]["Mine"] = {}
        self.cycle(config)
        self.ac = ON_AC
        self.assertTrue(self.cycle(config))
        self.assertEqual(config["current_profile"], "Mine")
        self.assertEqual(self.modes, [])

    def test_a_write_from_the_window_is_not_clobbered_by_the_switch(self):
        """``config`` was read at the top of the cycle. The window, the tray
        and the hotkey cycler can all have written the file since, and
        saving the stale copy back would throw their change away."""
        config = make_config()
        self.cycle(config)
        # Meanwhile, the window saves an edited curve and a charge limit.
        edited = make_config()
        edited["charge_limit"] = 61
        edited["profiles"]["Performance"] = {"fans": {"1": [[40, 55]]}}
        with open(self.config_path, "w") as f:
            json.dump(edited, f)

        self.ac = ON_AC
        self.assertTrue(self.cycle(config))
        with open(self.config_path) as f:
            saved = json.load(f)
        self.assertEqual(saved["charge_limit"], 61)
        self.assertEqual(saved["profiles"]["Performance"],
                         {"fans": {"1": [[40, 55]]}})
        self.assertEqual(saved["current_profile"], "Performance")
        # And the caller's dict is the fresh one, since the rest of the cycle
        # goes on using it.
        self.assertEqual(config["charge_limit"], 61)

    def test_a_profile_chosen_elsewhere_is_not_re_applied(self):
        """The user picked the same profile in the window during this cycle.
        Applying it again would push all three fan curves for nothing."""
        config = make_config()
        self.cycle(config)
        already = make_config(current_profile="Performance")
        with open(self.config_path, "w") as f:
            json.dump(already, f)

        self.ac = ON_AC
        self.assertFalse(self.cycle(config))
        self.assertEqual(self.applied, [])
        self.assertEqual(self.modes, [])

    def test_a_switch_is_announced(self):
        """An automatic switch happens with nobody watching the log. Without
        a notification the fans change pitch a minute after the plug moved
        and nothing on screen connects the two."""
        config = make_config()
        self.cycle(config)
        self.ac = ON_AC
        self.cycle(config)
        self.assertEqual(len(self.notifications), 1)
        _title, body = self.notifications[0]
        self.assertIn("Performance", body)
        self.assertIn("AC", body)

    def test_unplugging_says_battery(self):
        config = make_config()
        self.ac = ON_AC
        self.cycle(config)
        self.ac = ON_BATTERY
        self.cycle(config)
        self.assertIn("battery", self.notifications[0][1])
        self.assertIn("Quiet", self.notifications[0][1])

    def test_nothing_is_announced_when_nothing_changed(self):
        # This runs every 60 seconds for the life of the session; a
        # notification per cycle would be unusable.
        config = make_config()
        for _ in range(4):
            self.cycle(config)
        self.assertEqual(self.notifications, [])

    def test_a_switch_that_does_not_happen_is_not_announced(self):
        # The user picked that profile in the window during this cycle, so
        # there is nothing to tell them.
        config = make_config()
        self.cycle(config)
        already = make_config(current_profile="Performance")
        with open(self.config_path, "w") as f:
            json.dump(already, f)
        self.ac = ON_AC
        self.assertFalse(self.cycle(config))
        self.assertEqual(self.notifications, [])

    def test_the_notification_comes_before_the_slow_apply(self):
        # ~10 seconds of fan writes follow. A notification after them is
        # explaining something the user has finished wondering about.
        config = make_config()
        order = []
        self.enforcer.notify = lambda *a: order.append("notify")
        self.enforcer.apply_full_profile = (
            lambda *a, **k: order.append("apply"))
        self.cycle(config)
        self.ac = ON_AC
        self.cycle(config)
        self.assertEqual(order, ["notify", "apply"])

    def test_no_power_profiles_daemon_still_switches(self):
        config = make_config()
        self.cycle(config)
        self.ac = ON_AC
        self.assertTrue(self.cycle(config, service=None))
        self.assertEqual(self.modes, [])
        self.assertEqual(config["current_profile"], "Performance")


class RestartMemory(unittest.TestCase):
    """What a *fresh start* does, which is the half of this feature that was
    silently broken.

    The remembered power source used to be an in-memory global starting at
    None, so the first sample after any start was recorded and never acted
    on. The service is Restart=always and every install restarts it, so
    starting while on battery with the AC profile active left a real mismatch
    in place until the plug next moved -- possibly never.

    Acting on the first sample instead would trade that for a worse bug: a
    restart would override a profile the user had just chosen by hand. So the
    power source is remembered on disk, and a restart is compared against it
    rather than being treated as a transition in itself. These tests pin all
    three arms of that: unchanged across a restart, changed across a restart,
    and nothing known yet.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config_path = os.path.join(tmp.name, "rogcontrol.json")
        self.state_path = os.path.join(tmp.name, "last-power-source")
        self.ac = ON_BATTERY
        self.applied = []

    def write_config(self, config):
        with open(self.config_path, "w") as f:
            json.dump(config, f)

    def start(self):
        """One service start: a fresh module, pointed at this test's files,
        with the power source loaded back exactly as main() does."""
        enforcer = load_enforcer()
        enforcer.CONFIG_PATH = self.config_path
        enforcer.AC_STATE_PATH = self.state_path
        enforcer.hardware = types.SimpleNamespace(
            is_ac_connected=lambda: self.ac)
        enforcer.log = lambda *a, **k: None
        enforcer.notify = lambda *a, **k: None
        enforcer.set_ppd_active_profile = lambda *a: None
        enforcer.apply_full_profile = (
            lambda config, profile, **kw: self.applied.append(
                config.get("current_profile")))
        enforcer._last_ac_state = enforcer.load_last_ac_state()
        return enforcer

    def first_cycle(self, enforcer, config):
        """The first pass of main()'s loop, which is where a start either
        corrects a mismatch or leaves the user alone."""
        self.write_config(config)
        return enforcer.check_ac_auto_switch(config, None)

    def test_the_very_first_run_ever_only_records(self):
        """No state file yet -- nothing can be inferred, so nothing is done.
        This is the one case that keeps the old conservative behaviour."""
        enforcer = self.start()
        config = make_config(current_profile="Performance")
        self.assertFalse(self.first_cycle(enforcer, config))
        self.assertEqual(self.applied, [])
        self.assertEqual(config["current_profile"], "Performance")
        # ...but it is recorded, so the *next* start knows something.
        self.assertEqual(enforcer.load_last_ac_state(), ON_BATTERY)

    def test_a_plain_restart_does_not_override_a_profile_chosen_by_hand(self):
        """The plug has not moved; the user picked Performance on battery on
        purpose. A restart must not undo that."""
        first = self.start()
        self.first_cycle(first, make_config())

        # The user now picks something the battery rule would not have.
        chosen = make_config(current_profile="Performance")
        self.applied.clear()

        second = self.start()          # systemd restarts the service
        self.assertFalse(self.first_cycle(second, chosen))
        self.assertEqual(self.applied, [])
        self.assertEqual(chosen["current_profile"], "Performance")

    def test_a_plug_that_moved_while_the_service_was_down_is_acted_on(self):
        """The other arm, and the reason the file exists at all: the change
        really happened, so a restart is the first chance to notice it."""
        self.ac = ON_AC
        first = self.start()
        self.first_cycle(first, make_config(current_profile="Performance"))
        self.applied.clear()

        self.ac = ON_BATTERY           # unplugged while the service was down
        second = self.start()
        config = make_config(current_profile="Performance")
        self.assertTrue(self.first_cycle(second, config))
        self.assertEqual(config["current_profile"], "Quiet")
        self.assertEqual(self.applied, ["Quiet"])

    def test_the_transition_is_only_acted_on_once_across_a_restart(self):
        """Having acted on it, the new source is what gets remembered -- a
        second restart must not switch again."""
        self.ac = ON_AC
        self.first_cycle(self.start(), make_config(current_profile="Performance"))
        self.ac = ON_BATTERY
        self.first_cycle(self.start(), make_config(current_profile="Performance"))
        self.applied.clear()
        third = self.start()
        self.assertFalse(self.first_cycle(third, make_config(current_profile="Quiet")))
        self.assertEqual(self.applied, [])

    def test_the_remembered_source_is_readable(self):
        """Someone debugging this at 1am should be able to cat the file."""
        self.ac = ON_AC
        self.first_cycle(self.start(), make_config())
        with open(self.state_path) as f:
            self.assertEqual(f.read().strip(), "AC")
        self.ac = ON_BATTERY
        self.first_cycle(self.start(), make_config())
        with open(self.state_path) as f:
            self.assertEqual(f.read().strip(), "battery")

    def test_a_corrupt_state_file_is_treated_as_nothing_known(self):
        """Half a file, or someone else's file. Falling back to "record, act
        next time" is the safe reading -- inventing a previous state would
        invent a transition."""
        with open(self.state_path, "w") as f:
            f.write("")
        enforcer = self.start()
        self.assertIsNone(enforcer._last_ac_state)
        config = make_config(current_profile="Performance")
        self.assertFalse(self.first_cycle(enforcer, config))
        self.assertEqual(self.applied, [])

    def test_an_unreadable_state_directory_does_not_break_the_switch(self):
        """Persistence is an improvement, not a dependency: if it cannot be
        written the enforcer must still behave exactly as it did before."""
        enforcer = self.start()
        enforcer.AC_STATE_PATH = "/proc/definitely-not-writable/state"
        config = make_config()
        self.assertFalse(enforcer.check_ac_auto_switch(config, None))
        self.ac = ON_AC
        self.write_config(config)
        self.assertTrue(enforcer.check_ac_auto_switch(config, None))
        self.assertEqual(config["current_profile"], "Performance")

    def test_the_state_file_is_written_only_when_the_source_changes(self):
        """This is checked on every cycle and on every udev event. Writing
        it each time would be ~1440 writes a day for a fact that changes
        twice."""
        enforcer = self.start()
        writes = []
        real = enforcer.store_last_ac_state
        enforcer.store_last_ac_state = lambda v: (writes.append(v), real(v))[1]
        config = make_config()
        for _ in range(4):
            enforcer.check_ac_auto_switch(config, None)
        self.assertEqual(writes, [ON_BATTERY])
        self.ac = ON_AC
        self.write_config(config)
        enforcer.check_ac_auto_switch(config, None)
        self.assertEqual(writes, [ON_BATTERY, ON_AC])


class FakeMonitor:
    """Stands in for the running ``udevadm monitor`` process.

    A real pipe, not a list of lines: the watcher blocks on reading it in its
    own thread, so a test that handed it an already-finished iterator would
    prove nothing about whether events arrive promptly."""

    def __init__(self):
        read_fd, write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "r")
        self._write = os.fdopen(write_fd, "w")
        self.returncode = None

    def emit(self, line):
        self._write.write(line if line.endswith("\n") else line + "\n")
        self._write.flush()

    def end(self):
        try:
            self._write.close()
        except OSError:
            pass

    def wait(self):
        self.returncode = 0
        return 0

    def kill(self):
        self.end()


EVENT_LINE = ("UDEV  [12345.678901] change   "
              "/devices/LNXSYSTM:00/ACPI0003:00/power_supply/ADP0 "
              "(power_supply)")
BANNER_LINES = ["monitor will print the received events for:",
                "UDEV - the event which udev sends out after rule processing",
                ""]


class Watcher(unittest.TestCase):
    """The udev watcher: the plug moving is acted on when it moves, not up to
    INTERVAL_SECONDS later.

    Up to a minute of nothing happening after unplugging is what "the
    auto-switch does not work" actually looked like -- the decision was right
    the whole time, it was just late enough to read as absent. These tests
    drive the real watcher loop with a monitor whose events are under the
    test's control, because the real one only speaks when the plug moves and
    the plug is not the test's to move."""

    def setUp(self):
        self.enforcer = load_enforcer()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config_path = os.path.join(tmp.name, "rogcontrol.json")
        self.enforcer.CONFIG_PATH = self.config_path
        self.enforcer.AC_STATE_PATH = os.path.join(tmp.name, "state")
        self.ac = ON_AC
        self.applied = threading.Event()
        self.switched_to = []
        self.enforcer.hardware = types.SimpleNamespace(
            is_ac_connected=lambda: self.ac)
        self.enforcer.log = lambda *a, **k: None
        self.enforcer.notify = lambda *a, **k: None
        self.enforcer.set_ppd_active_profile = lambda *a: None

        def applied(config, profile, **kwargs):
            self.switched_to.append(config.get("current_profile"))
            self.applied.set()
        self.enforcer.apply_full_profile = applied

    def write_config(self, config):
        with open(self.config_path, "w") as f:
            json.dump(config, f)

    def run_watcher(self, monitor_factory):
        self.enforcer.spawn_power_supply_monitor = monitor_factory
        thread = threading.Thread(
            target=self.enforcer.power_supply_watcher_thread, args=(None,),
            daemon=True)
        thread.start()
        return thread

    # -- the point of the whole thing ----------------------------------------

    def test_unplugging_switches_within_a_second_not_within_a_minute(self):
        config = make_config()
        self.write_config(config)
        # Prime the remembered state the way a first cycle on mains would.
        self.enforcer.check_ac_auto_switch(dict(config), None)

        monitor = FakeMonitor()
        self.run_watcher(lambda: monitor)

        self.ac = ON_BATTERY          # the plug moves
        started = time.monotonic()
        monitor.emit(EVENT_LINE)

        self.assertTrue(self.applied.wait(timeout=10),
                        "the udev event did not produce a switch at all")
        elapsed = time.monotonic() - started
        self.assertEqual(self.switched_to, ["Quiet"])
        # The bar is "before the poll would have got there", and the poll is
        # a minute away; the settle delay is the only thing in the path.
        self.assertLess(elapsed, 5)
        self.assertLess(elapsed, self.enforcer.INTERVAL_SECONDS)
        with open(self.config_path) as f:
            self.assertEqual(json.load(f)["current_profile"], "Quiet")
        monitor.end()

    def test_the_banner_is_not_mistaken_for_an_event(self):
        """udevadm prints two lines before any event. Treating those as a
        power-source change would switch profile at startup every time."""
        config = make_config()
        self.write_config(config)
        self.enforcer.check_ac_auto_switch(dict(config), None)
        monitor = FakeMonitor()
        self.run_watcher(lambda: monitor)
        self.ac = ON_BATTERY
        for line in BANNER_LINES:
            monitor.emit(line)
        self.assertFalse(self.applied.wait(timeout=1.5))
        self.assertEqual(self.switched_to, [])
        monitor.end()

    def test_a_burst_of_events_produces_one_switch(self):
        """A plug change emits an event for the mains supply and one for the
        battery. Two switches would mean two ~10 second fan applies."""
        config = make_config()
        self.write_config(config)
        self.enforcer.check_ac_auto_switch(dict(config), None)
        monitor = FakeMonitor()
        self.run_watcher(lambda: monitor)
        self.ac = ON_BATTERY
        for _ in range(3):
            monitor.emit(EVENT_LINE)
        self.assertTrue(self.applied.wait(timeout=10))
        time.sleep(1.5)               # let the rest of the burst through
        self.assertEqual(self.switched_to, ["Quiet"])
        monitor.end()

    def test_an_event_with_no_config_on_disk_is_survivable(self):
        """The watcher reads the config itself; there may not be one yet."""
        monitor = FakeMonitor()
        thread = self.run_watcher(lambda: monitor)
        monitor.emit(EVENT_LINE)
        self.assertFalse(self.applied.wait(timeout=1.5))
        self.assertEqual(self.switched_to, [])
        self.assertTrue(thread.is_alive())
        monitor.end()

    # -- degrading to the poll, never failing closed -------------------------

    def test_a_monitor_that_cannot_start_stops_the_thread(self):
        """No udevadm, or a Popen that raised. Retrying cannot help, and the
        60s poll still catches every transition."""
        thread = self.run_watcher(lambda: None)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def test_the_poll_still_switches_when_the_watcher_is_unavailable(self):
        """The fallback is the whole reason a dead watcher is allowed to give
        up quietly, so it is worth showing it actually works."""
        thread = self.run_watcher(lambda: None)
        thread.join(timeout=5)
        config = make_config()
        self.write_config(config)
        self.assertFalse(self.enforcer.check_ac_auto_switch(config, None))
        self.ac = ON_BATTERY
        self.assertTrue(self.enforcer.check_ac_auto_switch(config, None))
        self.assertEqual(self.switched_to, ["Quiet"])

    def test_a_monitor_that_keeps_dying_is_given_up_on(self):
        """The lesson already paid for once in this file: the PPD watcher
        respawned an instantly-exiting monitor with no delay and burned ~8.5%
        of a core. Bounded retries, then fall back to polling."""
        self.enforcer.WATCH_BACKOFF_SECONDS = 0
        spawns = []

        def dying_monitor():
            monitor = FakeMonitor()
            monitor.end()            # exits before producing anything
            spawns.append(monitor)
            return monitor

        thread = self.run_watcher(dying_monitor)
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "watcher retried forever")
        self.assertEqual(len(spawns), self.enforcer.WATCH_MAX_FAILED_STARTS)

    def test_a_monitor_that_worked_and_then_ended_is_reconnected_to(self):
        """udevd restarting is not a reason to stop watching for the rest of
        the session."""
        self.enforcer.WATCH_BACKOFF_SECONDS = 0
        self.enforcer.WATCH_MIN_HEALTHY_SECONDS = 0   # every start counts as healthy
        spawns = []

        def monitor_factory():
            monitor = FakeMonitor()
            spawns.append(monitor)
            if len(spawns) >= 3:
                # Third one stays up, so the thread parks there instead of
                # spinning and the test can stop counting.
                return monitor
            monitor.end()
            return monitor

        thread = self.run_watcher(monitor_factory)
        for _ in range(50):
            if len(spawns) >= 3:
                break
            time.sleep(0.1)
        self.assertGreaterEqual(len(spawns), 3)
        self.assertTrue(thread.is_alive())
        spawns[-1].end()

    def test_a_broken_event_does_not_take_the_watcher_down(self):
        """Losing the watcher on one bad event would drop the machine back to
        60s lag silently, which is the bug this replaces."""
        boom = []

        def exploding(service_name):
            boom.append(service_name)
            raise RuntimeError("no")
        self.enforcer.handle_power_supply_event = exploding
        monitor = FakeMonitor()
        thread = self.run_watcher(lambda: monitor)
        monitor.emit(EVENT_LINE)
        for _ in range(50):
            if boom:
                break
            time.sleep(0.1)
        self.assertEqual(len(boom), 1)
        self.assertTrue(thread.is_alive())
        monitor.end()


class MonitorCommand(unittest.TestCase):
    """How the monitor is actually started -- the part a test with a fake
    monitor cannot see."""

    def setUp(self):
        self.enforcer = load_enforcer()

    def spawn_with(self, which):
        calls = []
        self.enforcer.shutil = types.SimpleNamespace(which=which)
        self.enforcer.subprocess = types.SimpleNamespace(
            Popen=lambda cmd, **kwargs: calls.append(cmd) or "proc",
            PIPE=-1, DEVNULL=-3)
        return self.enforcer.spawn_power_supply_monitor(), calls

    def test_it_watches_the_power_supply_subsystem_unprivileged(self):
        proc, calls = self.spawn_with(lambda name: "/usr/bin/" + name)
        self.assertEqual(proc, "proc")
        cmd = calls[0]
        self.assertIn("udevadm", cmd)
        self.assertIn("monitor", cmd)
        self.assertIn("--subsystem-match=power_supply", cmd)
        # --udev reads udev's multicast group, which an ordinary user can
        # read. --kernel would need root, and would fail the same silent way
        # `busctl --system monitor` does in the PPD watcher.
        self.assertIn("--udev", cmd)
        self.assertNotIn("--kernel", cmd)
        # Line buffered, or a 4KB block buffer would hold an event back
        # indefinitely -- which would look exactly like the lag being fixed.
        self.assertEqual(cmd[:2], ["stdbuf", "-oL"])

    def test_no_udevadm_means_no_monitor_rather_than_a_crash(self):
        proc, calls = self.spawn_with(lambda name: None)
        self.assertIsNone(proc)
        self.assertEqual(calls, [])

    def test_a_missing_stdbuf_is_not_fatal(self):
        proc, calls = self.spawn_with(
            lambda name: None if name == "stdbuf" else "/usr/bin/udevadm")
        self.assertEqual(proc, "proc")
        self.assertEqual(calls[0][0], "udevadm")

    def test_the_real_monitor_starts_on_this_machine(self):
        """Not a mock: the whole design rests on `udevadm monitor --udev`
        being usable by the unprivileged user this service runs as, which is
        precisely what the equivalent D-Bus call is not."""
        proc = self.enforcer.spawn_power_supply_monitor()
        if proc is None:
            self.skipTest("no udevadm on this machine")
        try:
            # Still running a moment later, i.e. it did not exit on a
            # permission error the way `busctl --system monitor` does.
            time.sleep(0.5)
            self.assertIsNone(proc.poll())
        finally:
            proc.kill()
            proc.wait()
            proc.stdout.close()


class Cadence(unittest.TestCase):
    """The 60 second cycle stays what it is -- shortening it would make
    everything else in the loop run 60x more often for one feature's sake.
    It is the fallback now; the udev watcher is the fast path."""

    def test_the_cycle_is_unchanged(self):
        self.assertEqual(load_enforcer().INTERVAL_SECONDS, 60)

    def test_the_fast_path_does_not_depend_on_the_cycle(self):
        enforcer = load_enforcer()
        self.assertTrue(callable(enforcer.power_supply_watcher_thread))
        self.assertLess(enforcer.POWER_SUPPLY_SETTLE_SECONDS,
                        enforcer.INTERVAL_SECONDS)


if __name__ == "__main__":
    unittest.main()


class EnforcerNamesResolve(unittest.TestCase):
    """No undefined name may reach the enforcer's cycle.

    A branch once called a function that lives in rogcontrol.profiles and
    was never imported here. The NameError escaped its caller and took the
    whole 60-second pass with it -- no CPU limits re-asserted, no fan
    curves, nothing, with a single "cycle failed" line in the log. The chip
    then ran at its firmware defaults, roughly twice the configured power
    limit.
    """

    def test_every_name_the_enforcer_uses_at_module_level_resolves(self):
        """A cheap guard against the same shape of typo anywhere else."""
        import ast
        import builtins
        with open(ENFORCER_PATH, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        defined = {"__name__", "__file__"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                defined.add(node.id)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    defined.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    defined.add(a.asname or a.name)
            elif isinstance(node, ast.arg):
                defined.add(node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                defined.add(node.name)
        missing = sorted({
            (n.id, n.lineno) for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            and n.id not in defined and not hasattr(builtins, n.id)})
        self.assertEqual(missing, [], f"undefined names: {missing}")
