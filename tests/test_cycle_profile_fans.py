"""rogcontrol-cycle-profile.py's fan writes must skip channels already
holding the right curve.

This script duplicates rogcontrol-apply.py's apply_profile, and until now it
duplicated the *old* version -- every channel written unconditionally, with
the mandatory 8s EC gap between all three, every time. That made the
notify-send after a shortcut-triggered switch land 16+ seconds late even
when the fans did not change at all. See test_apply_scope.py for the same
guard already in place on the login/tray path.
"""

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

from rogcontrol import profiles

PACKAGE_DIR = Path(profiles.__file__).resolve().parent
CYCLE = PACKAGE_DIR / "rogcontrol-cycle-profile.py"


def load_cycle():
    spec = importlib.util.spec_from_file_location(
        "rogcontrol_cycle_profile_under_test", CYCLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROFILE = {
    "cpu": {"stapm": 35000, "fast": 50000, "slow": 35000,
            "temp": 80, "coall": -5, "boost": False,
            "epp": "power", "max_freq": 0},
    "gpu": {"watts": 60, "dyn_boost": 0, "temp_target": 75},
    "fans": {"1": [(40, 20), (80, 80)],
             "2": [(40, 20), (80, 80)],
             "3": [(40, 20), (80, 80)]},
}


def hardware_pairs_for(points):
    """What read_fan_curve_points would answer if the EC already held this
    exact curve -- (temp, pwm255) tuples, matching curve_matches_hardware's
    own input shape, not the config's (temp, pct) points."""
    from rogcontrol import fancurve
    flat = fancurve.curve_to_flat(points, 8)
    return list(zip(flat[0::2], flat[1::2]))


class CycleProfileFans(unittest.TestCase):

    def setUp(self):
        self.module = load_cycle()
        self.calls = []
        self.slept = []

    def run_apply(self, held):
        """apply_profile with hardware replaced; ``held`` maps channel to
        what read_fan_curve_points should answer for it (None = unread)."""
        module = self.module
        with mock.patch.object(
                module, "run_helper",
                side_effect=lambda *a: self.calls.append(
                    [str(x) for x in a])), \
             mock.patch.object(module.hardware, "run_helper",
                               return_value=(True, "")), \
             mock.patch.object(module.hardware, "read_fan_curve_points",
                               side_effect=lambda ch: held.get(ch)), \
             mock.patch.object(module.hardware, "read_fan_curve_enabled",
                               return_value={ch: True for ch in held}), \
             mock.patch.object(module.subprocess, "run"), \
             mock.patch.object(module.time, "sleep",
                               side_effect=self.slept.append):
            module.apply_profile(PROFILE)

    def actions(self):
        return [call[0] for call in self.calls]

    def test_matching_channels_are_skipped_entirely(self):
        held = {ch: hardware_pairs_for(pts)
                for ch, pts in PROFILE["fans"].items()}
        self.run_apply(held)
        self.assertNotIn("fan", self.actions())
        self.assertEqual(self.slept, [])

    def test_one_differing_channel_writes_only_that_one(self):
        held = {ch: hardware_pairs_for(pts)
                for ch, pts in PROFILE["fans"].items()}
        held["2"] = hardware_pairs_for([(40, 99), (80, 99)])
        self.run_apply(held)
        fan_calls = [c for c in self.calls if c[0] == "fan"]
        self.assertEqual(len(fan_calls), 1)
        self.assertEqual(fan_calls[0][1], "2")
        self.assertEqual(self.slept, [])

    def test_two_differing_channels_still_take_the_channel_gap(self):
        held = {"1": None, "2": None,
                 "3": hardware_pairs_for(PROFILE["fans"]["3"])}
        self.run_apply(held)
        fan_calls = [c for c in self.calls if c[0] == "fan"]
        self.assertEqual(len(fan_calls), 2)
        self.assertEqual(self.slept, [self.module.CHANNEL_GAP_S])


if __name__ == "__main__":
    unittest.main()
