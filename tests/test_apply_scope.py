"""What a profile switch is allowed to touch.

rogcontrol-apply.py has two callers with two different jobs. At login it is
the whole of "put the machine back the way it was". But the tray also runs
it to make a profile switch real, and it was applying the config's
kbd_brightness there too -- so picking a different profile pushed the
keyboard backlight to whatever value the config last recorded, which with
kbd_brightness at 0 meant the lights went out on every switch. Keyboard
brightness is global: there is one of it in the config and no profile has
its own, so a profile switch cannot have an opinion about it.

--profile-only is the split, and this pins it from both ends: what the flag
removes (the keyboard, and nothing else), and what it must NOT remove -- the
profile itself, the charge limit, and the two hardware invariants that live
in this same function, the 8-second gap between fan channels and the
boost-then-EPP-then-clock order.

The script is loaded from its path: hyphens make it a script name rather
than an importable module. Importing it only defines functions; every call
that would reach hardware is replaced below.
"""

import ast
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rogcontrol import profiles

PACKAGE_DIR = Path(profiles.__file__).resolve().parent
APPLY = PACKAGE_DIR / "rogcontrol-apply.py"
TRAY = PACKAGE_DIR / "rogcontrol-tray"


def load_apply():
    spec = importlib.util.spec_from_file_location(
        "rogcontrol_apply_under_test", APPLY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_config():
    """A config with a full profile and both global settings."""
    return {
        "current_profile": "Quiet",
        "kbd_brightness": 0,
        "charge_limit": 80,
        "profiles": {"Quiet": {
            "cpu": {"stapm": 35000, "fast": 50000, "slow": 35000,
                    "temp": 80, "coall": -5, "boost": False,
                    "epp": "power", "max_freq": 0},
            "gpu": {"watts": 60, "dyn_boost": 0, "temp_target": 75},
            "fans": {"1": [(40, 20), (80, 80)],
                     "2": [(40, 20), (80, 80)],
                     "3": [(40, 20), (80, 80)]},
        }},
    }


class ApplyScope(unittest.TestCase):

    def setUp(self):
        self.apply = load_apply()
        self.calls = []
        self.slept = []

    def run_apply(self, profile_only=False, config=None):
        """apply_once with every route to the hardware replaced."""
        module = self.apply
        with mock.patch.object(
                module, "run_helper",
                side_effect=lambda *a: self.calls.append(
                    [str(x) for x in a])), \
             mock.patch.object(module.hardware, "set_power_mode_for_profile",
                               return_value=(True, "")), \
             mock.patch.object(module.hardware, "gpu_clock_limit_max",
                               return_value=2000), \
             mock.patch.object(module.subprocess, "run"), \
             mock.patch.object(module.time, "sleep",
                               side_effect=self.slept.append):
            module.apply_once(make_config() if config is None else config,
                              profile_only=profile_only)
        return self.calls

    def actions(self):
        return [call[0] for call in self.calls]

    # -- the keyboard --------------------------------------------------------

    def test_a_full_apply_still_sets_the_keyboard(self):
        """Boot has not changed: this script's original job is to restore
        the keyboard along with everything else."""
        self.run_apply()
        self.assertIn(["kbd", "0"], self.calls)

    def test_a_profile_switch_leaves_the_keyboard_alone(self):
        """The bug. Nothing in a profile switch may write the backlight."""
        self.run_apply(profile_only=True)
        self.assertNotIn("kbd", self.actions())

    def test_a_profile_switch_touches_no_keyboard_setting_at_all(self):
        """Not the brightness, and not the colour either -- the config's
        kbd_rgb and kbd_power_events are just as global."""
        config = make_config()
        config["kbd_rgb"] = {"mode": "Static", "r": 255, "g": 0, "b": 0}
        config["kbd_power_events"] = {"suspend": "Off"}
        self.run_apply(profile_only=True, config=config)
        for action in self.actions():
            self.assertFalse(action.startswith("kbd"), action)

    # -- what the flag must NOT remove ---------------------------------------

    def test_a_profile_switch_still_applies_the_profile(self):
        """The flag narrows the globals, not the profile."""
        self.run_apply(profile_only=True)
        actions = self.actions()
        for expected in ("cpu", "cpuboost", "cpuepp", "cpuclock", "gpu",
                         "fan"):
            self.assertIn(expected, actions)

    def test_a_profile_switch_still_sets_the_charge_limit(self):
        """Global like the keyboard, and deliberately kept: nothing outside
        this app ever writes the charge threshold, so re-asserting it fights
        no one -- and a charge limit that has silently lapsed is invisible
        until the battery is damaged."""
        self.run_apply(profile_only=True)
        self.assertIn(["charge", "80"], self.calls)

    def test_the_os_power_mode_still_moves_with_the_profile(self):
        """Skipping it would have the enforcer read the disagreement as the
        OS asking for the old profile and switch back within a minute."""
        module = self.apply
        with mock.patch.object(module, "run_helper"), \
             mock.patch.object(module.hardware, "set_power_mode_for_profile",
                               return_value=(True, "")) as mode, \
             mock.patch.object(module.hardware, "gpu_clock_limit_max",
                               return_value=2000), \
             mock.patch.object(module.subprocess, "run"), \
             mock.patch.object(module.time, "sleep"):
            module.apply_once(make_config(), profile_only=True)
        mode.assert_called_once_with("Quiet")

    # -- the invariants, which live in the same function ---------------------

    def test_the_fan_channel_gap_survives_both_modes(self):
        """8 seconds between channels. The asus-wmi EC silently drops curve
        writes fired closer together."""
        for profile_only in (False, True):
            with self.subTest(profile_only=profile_only):
                self.calls, self.slept = [], []
                self.run_apply(profile_only=profile_only)
                fans = [c for c in self.calls if c[0] == "fan"]
                self.assertEqual(len(fans), 3)
                self.assertEqual(self.slept, [8, 8])

    def test_the_cpu_order_survives_both_modes(self):
        """boost, then EPP, then the clock cap: writing cpufreq's boost
        refreshes every policy and takes scaling_max_freq back to hardware
        maximum with it, so a cap written first is silently undone."""
        for profile_only in (False, True):
            with self.subTest(profile_only=profile_only):
                self.calls = []
                self.run_apply(profile_only=profile_only)
                actions = self.actions()
                self.assertLess(actions.index("cpuboost"),
                                actions.index("cpuepp"))
                self.assertLess(actions.index("cpuepp"),
                                actions.index("cpuclock"))

    # -- the flag itself -----------------------------------------------------

    def run_main(self, argv):
        """main() against a real config file, recording how it calls
        apply_once. The retry loop is real; only its sleep is not."""
        module = self.apply
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rogcontrol.json")
            with open(path, "w") as f:
                json.dump(make_config(), f)
            with mock.patch.object(module, "CONFIG_PATH", path), \
                 mock.patch.object(
                     module, "apply_once",
                     side_effect=lambda cfg, profile_only=False: seen.append(
                         profile_only)), \
                 mock.patch.object(module.time, "sleep"):
                module.main(argv)
        return seen

    def test_the_flag_reaches_apply_once(self):
        self.assertEqual(self.run_main(["--profile-only"]),
                         [True] * self.apply.RETRIES)

    def test_no_flag_is_a_full_apply(self):
        """The boot service passes no arguments and must keep everything."""
        self.assertEqual(self.run_main([]), [False] * self.apply.RETRIES)

    def test_an_unrelated_argument_does_not_turn_the_flag_on(self):
        self.assertEqual(self.run_main(["--quiet"]),
                         [False] * self.apply.RETRIES)


class TrayPassesTheFlag(unittest.TestCase):
    """The tray is the profile-switch caller, and the one that has to ask
    for --profile-only. It is read rather than imported: it pulls in GTK3,
    which this suite must not touch."""

    def apply_command_strings(self):
        tree = ast.parse(TRAY.read_text(encoding="utf-8"), filename=str(TRAY))
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef)
                    and node.name == "apply_command"):
                return [n.value for n in ast.walk(node)
                        if isinstance(n, ast.Constant)
                        and isinstance(n.value, str)]
        self.fail("rogcontrol-tray has no apply_command()")

    def test_the_tray_asks_for_a_profile_only_apply(self):
        self.assertIn("--profile-only", self.apply_command_strings())

    def test_the_tray_still_runs_the_apply_script(self):
        strings = self.apply_command_strings()
        self.assertTrue(any("rogcontrol-apply.py" in s for s in strings),
                        strings)


if __name__ == "__main__":
    unittest.main()
