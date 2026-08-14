"""The enforcer's AC/battery auto-switch.

This is decision logic with a plug on one end and a ~16 second hardware
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
import types
import unittest
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

    def test_no_power_profiles_daemon_still_switches(self):
        config = make_config()
        self.cycle(config)
        self.ac = ON_AC
        self.assertTrue(self.cycle(config, service=None))
        self.assertEqual(self.modes, [])
        self.assertEqual(config["current_profile"], "Performance")


class Cadence(unittest.TestCase):
    """The 60 second granularity is a design decision, not an accident: it
    is the enforcer's existing cycle, and the alternative is a second poll
    loop for something that takes ~16 seconds to apply anyway."""

    def test_the_switch_runs_on_the_enforcer_cycle(self):
        self.assertEqual(load_enforcer().INTERVAL_SECONDS, 60)


if __name__ == "__main__":
    unittest.main()
