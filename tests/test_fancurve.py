import unittest
from rogcontrol import fancurve


class InterpolateCurve(unittest.TestCase):
    def test_users_own_points_are_preserved(self):
        pts = [(50, 5), (57, 10), (63, 15), (68, 22), (73, 52), (78, 85)]
        out = fancurve.interpolate_curve(pts, 8)
        for p in pts:
            self.assertIn(p, out)

    def test_always_returns_exactly_n(self):
        for pts in ([(40, 20), (80, 80)],
                    [(50, 5), (57, 10), (63, 15), (68, 22), (73, 52), (78, 85)]):
            self.assertEqual(len(fancurve.interpolate_curve(pts, 8)), 8)

    def test_fills_the_widest_gap_first(self):
        out = fancurve.interpolate_curve([(50, 10), (52, 12), (90, 90)], 4)
        self.assertIn((71, 51), out)

    def test_temperatures_strictly_increase(self):
        out = fancurve.interpolate_curve([(50, 5), (51, 9)], 8)
        temps = [t for t, _ in out]
        self.assertEqual(temps, sorted(set(temps)))

    def test_more_points_than_wanted_are_truncated(self):
        # Truncation keeps the *coolest* n points, i.e. the head of the sorted
        # list. Asserting the retained points and not just the length is what
        # catches a truncation that drops from the wrong end -- keeping the
        # hottest 8 would leave the fan with no curve below 42C.
        pts = [(t, t) for t in range(40, 50)]
        out = fancurve.interpolate_curve(pts, 8)
        self.assertEqual(out, [(t, t) for t in range(40, 48)])


class PwmConversion(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(fancurve.pct_to_pwm255(0), 0)
        self.assertEqual(fancurve.pct_to_pwm255(100), 255)

    def test_clamps_out_of_range(self):
        self.assertEqual(fancurve.pct_to_pwm255(-10), 0)
        self.assertEqual(fancurve.pct_to_pwm255(150), 255)

    def test_interior_values_are_rounded_not_truncated(self):
        # The endpoints land on whole PWM values, so they cannot tell rounding
        # apart from truncation. These interior percentages can: each one sits
        # above a .5 boundary, where int() would come in one PWM step low.
        for pct, pwm in ((1, 3), (10, 26), (25, 64), (50, 128), (90, 230)):
            with self.subTest(pct=pct):
                self.assertEqual(fancurve.pct_to_pwm255(pct), pwm)


class RpmCalibration(unittest.TestCase):
    def test_builtin_used_when_user_has_none(self):
        self.assertEqual(fancurve.get_rpm_cal({}, "1"), fancurve.FAN_RPM_CAL["1"])

    def test_user_calibration_wins(self):
        cfg = {"fan_rpm_cal": {"1": [1000, 40.0]}}
        self.assertEqual(fancurve.get_rpm_cal(cfg, "1"), (1000, 40.0))

    def test_malformed_calibration_falls_back(self):
        cfg = {"fan_rpm_cal": {"1": "nonsense"}}
        self.assertEqual(fancurve.get_rpm_cal(cfg, "1"), fancurve.FAN_RPM_CAL["1"])

    def test_unknown_channel_returns_none(self):
        # Documented behaviour for later callers: an unrecognised channel has
        # no calibration and no sensible default, so it comes back as None.
        # Anything converting a percentage to rpm must check before unpacking.
        self.assertIsNone(fancurve.get_rpm_cal({}, "9"))
        self.assertIsNone(fancurve.get_rpm_cal(None, "9"))

    def test_pct_to_rpm(self):
        self.assertEqual(fancurve.pct_to_rpm(20, 1655, 49.3), 2641)

    def test_pct_to_rpm_clamps_out_of_range(self):
        # The calibration is a line fitted over 0-100 only. Past either end it
        # would report rpm the fan cannot reach, so the percentage is clamped.
        self.assertEqual(fancurve.pct_to_rpm(150, 1655, 49.3), 6585)
        self.assertEqual(fancurve.pct_to_rpm(-50, 1655, 49.3), 1655)


if __name__ == "__main__":
    unittest.main()
