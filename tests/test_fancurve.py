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

    def test_rpm_to_pct_is_the_inverse(self):
        self.assertEqual(fancurve.rpm_to_pct(2641, 1655, 49.3), 20)

    def test_rpm_to_pct_clamps_an_idling_fan_to_zero(self):
        # A fan sitting just under the fitted floor is the normal idle case,
        # and a negative percentage on screen would be nonsense.
        self.assertEqual(fancurve.rpm_to_pct(1600, 1655, 49.3), 0)

    def test_rpm_to_pct_rejects_a_flat_calibration(self):
        self.assertIsNone(fancurve.rpm_to_pct(3000, 1655, 0))


class FitRpmCal(unittest.TestCase):
    def test_recovers_a_line_it_was_given(self):
        samples = [(pct, 1655 + 49.3 * pct) for pct in (20, 45, 70)]
        self.assertEqual(fancurve.fit_rpm_cal(samples), (1655.0, 49.3))

    def test_ignores_channels_that_did_not_report(self):
        samples = [(20, 2641), (45, None), (70, 5106)]
        floor, slope = fancurve.fit_rpm_cal(samples)
        self.assertAlmostEqual(floor, 1655, delta=2)
        self.assertAlmostEqual(slope, 49.3, delta=0.2)

    def test_too_few_usable_readings_is_none(self):
        self.assertIsNone(fancurve.fit_rpm_cal([(20, 2641), (45, None)]))

    def test_the_ceiling_is_exactly_what_was_measured_there(self):
        """The actual bug report: a mid fan measured 7800 rpm at a flat
        100% curve, and the least-squares fit this replaced reported 7400
        -- the regression line balanced the high point against three lower
        ones instead of matching it. What the highest sampled percentage
        measured must be exactly what pct_to_rpm(100, ...) reports, not a
        value averaged down to fit everything else."""
        samples = [(20, 2200), (45, 4100), (70, 6100), (100, 7800)]
        floor, slope = fancurve.fit_rpm_cal(samples)
        self.assertEqual(fancurve.pct_to_rpm(100, floor, slope), 7800)
        self.assertEqual(fancurve.pct_to_rpm(20, floor, slope), 2200)

    def test_extreme_points_win_even_out_of_order_or_duplicated(self):
        samples = [(70, 6100), (20, 2200), (100, 7800), (45, 4100),
                  (100, 7800)]
        floor, slope = fancurve.fit_rpm_cal(samples)
        self.assertEqual(fancurve.pct_to_rpm(100, floor, slope), 7800)

    def test_a_fan_that_never_moved_is_rejected(self):
        # Slope of zero (or negative) means the fan did not respond. Saving
        # that calibration would make every rpm figure in the app wrong in a
        # way the user has no way to see, so the previous one is kept.
        self.assertIsNone(fancurve.fit_rpm_cal([(20, 3000), (45, 3000),
                                                (70, 3000)]))
        self.assertIsNone(fancurve.fit_rpm_cal([(20, 3000), (70, 2000)]))

    def test_no_gradient_to_fit_is_none(self):
        self.assertIsNone(fancurve.fit_rpm_cal([(20, 2600), (20, 2700)]))


class CurveToFlat(unittest.TestCase):
    def test_is_the_sixteen_values_the_helper_demands(self):
        flat = fancurve.curve_to_flat(
            [(50, 5), (57, 10), (63, 15), (68, 22), (73, 52), (78, 85)])
        self.assertEqual(len(flat), 16)
        # 50C at 5% is pwm 13; the interpolated point between it and 57C at
        # 10% lands at 53C and 8%, which is pwm 20.
        self.assertEqual(flat[:4], [50, 13, 53, 20])
        self.assertEqual(flat[-2:], [78, 217])

    def test_percentages_become_pwm(self):
        self.assertEqual(fancurve.curve_to_flat([(40, 0), (90, 100)], 2),
                         [40, 0, 90, 255])


class CurveMatchesHardware(unittest.TestCase):
    def curve(self):
        return [(50, 5), (57, 10), (63, 15), (68, 22), (73, 52), (78, 85)]

    def as_pairs(self, points):
        flat = fancurve.curve_to_flat(points)
        return list(zip(flat[0::2], flat[1::2]))

    def test_the_curve_we_would_write_matches_itself(self):
        self.assertTrue(fancurve.curve_matches_hardware(
            self.curve(), self.as_pairs(self.curve())))

    def test_one_degree_of_difference_does_not_match(self):
        points = self.curve()
        pairs = self.as_pairs(points)
        pairs[3] = (pairs[3][0] + 1, pairs[3][1])
        self.assertFalse(fancurve.curve_matches_hardware(points, pairs))

    def test_unreadable_hardware_never_matches(self):
        # None is "cannot tell", and the caller is expected to have filtered
        # it out; matching on it would silently claim the fan is running a
        # curve nobody has read.
        self.assertFalse(fancurve.curve_matches_hardware(self.curve(), None))
        self.assertFalse(fancurve.curve_matches_hardware(self.curve(), []))
        self.assertFalse(fancurve.curve_matches_hardware(
            self.curve(), [(None, None)] * 8))

    def test_wrong_number_of_points_does_not_match(self):
        self.assertFalse(fancurve.curve_matches_hardware(
            self.curve(), self.as_pairs(self.curve())[:7]))


class EditorPoints(unittest.TestCase):
    def test_always_exactly_eight(self):
        for points in ([[40, 25], [60, 40], [75, 60], [90, 80]],
                       [[50, 5], [55, 8], [58, 11], [61, 14], [64, 16], [68, 22]],
                       [[t, t] for t in range(30, 45)],
                       [[50, 50]],
                       []):
            with self.subTest(points=points):
                self.assertEqual(len(fancurve.editor_points(points)), 8)

    def test_an_eight_point_curve_is_left_alone(self):
        # The editor holds eight, so this is the load/save round trip every
        # profile takes from now on. It must be a no-op.
        points = [[50, 9], [57, 21], [64, 35], [69, 49],
                  [76, 62], [84, 99], [90, 100], [95, 100]]
        self.assertEqual(fancurve.editor_points(points), points)

    def test_a_stock_four_point_curve_keeps_its_own_points(self):
        points = [[40, 25], [60, 40], [75, 60], [90, 80]]
        out = fancurve.editor_points(points)
        for point in points:
            self.assertIn(point, out)

    def test_temperatures_strictly_increase(self):
        out = fancurve.editor_points([[50, 5], [50, 90], [51, 20], [51, 40]])
        temps = [t for t, _ in out]
        self.assertEqual(temps, sorted(set(temps)))

    def test_everything_lands_inside_the_axes(self):
        out = fancurve.editor_points([[-10, -20], [150, 300], [60, 50]])
        for temp, pct in out:
            self.assertTrue(0 <= temp <= 100, out)
            self.assertTrue(0 <= pct <= 100, out)

    def test_a_curve_crammed_against_the_top_still_gets_eight(self):
        # Eight points cannot fit above 97C one degree apart, so the fill has
        # to walk downwards instead of off the end of the axis.
        out = fancurve.editor_points([[99, 100], [100, 100]])
        self.assertEqual(len(out), 8)
        self.assertTrue(all(0 <= t <= 100 for t, _ in out), out)
        self.assertEqual([t for t, _ in out], sorted(set(t for t, _ in out)))


class SixToEightConversion(unittest.TestCase):
    """Loading the curves already in the user's config.

    Every curve saved by the old six-point editor is expanded to eight the
    first time this app opens it. Days of tuning are stored in those six
    numbers, so the expansion is only acceptable if it is purely additive:
    the six survive at the exact temperatures and percentages they were left
    at, and the two new points land on the line already drawn between them.
    """

    # A real six-point shape: slow ramp, then a wall where the fan is told to
    # deal with a Tctl burst.
    SIX = [[50, 9], [57, 21], [64, 35], [69, 49], [76, 62], [84, 99]]

    def test_the_original_six_survive_verbatim(self):
        out = fancurve.editor_points(self.SIX)
        self.assertEqual(len(out), 8)
        for point in self.SIX:
            self.assertIn(point, out, f"{point} was lost or moved: {out}")

    def test_the_two_added_points_bisect_the_widest_gaps(self):
        # Gaps are 7, 7, 5, 7, 8. The widest (84-76) is split first, giving
        # 80. That leaves 7, 7, 5, 7, 4, 4 and the next split is the *last*
        # of the tied 7s, not the first: the gaps are ranked with max() over
        # (gap, index) pairs, so a tie is broken by the larger index.
        out = fancurve.editor_points(self.SIX)
        added = [p for p in out if p not in self.SIX]
        self.assertEqual(added, [[72, 56], [80, 80]])

    def test_added_points_sit_on_the_line_they_were_drawn_between(self):
        # An added point takes the average of the two percentages around it,
        # which is the line's value at the exact midpoint temperature. The
        # temperature itself is floored to a whole degree, so across an odd
        # gap the point can sit up to half a degree's worth of slope off the
        # line. It is never off by more than that, and never outside the two
        # points it was inserted between -- which is what stops the expansion
        # from putting a step in a curve the user drew as a straight run.
        out = fancurve.editor_points(self.SIX)
        for temp, pct in out:
            if [temp, pct] in self.SIX:
                continue
            before = max(p for p in self.SIX if p[0] < temp)
            after = min(p for p in self.SIX if p[0] > temp)
            self.assertTrue(before[1] <= pct <= after[1],
                            f"({temp},{pct}) escaped {before}..{after}")
            span = after[0] - before[0]
            per_degree = (after[1] - before[1]) / span
            expected = before[1] + per_degree * (temp - before[0])
            # 1e-9 because the bound is reached exactly on this curve and
            # both sides are computed in binary floating point.
            self.assertLessEqual(abs(pct - expected),
                                 0.5 * per_degree + 0.5 + 1e-9,
                                 f"({temp},{pct}) is off the line by more "
                                 f"than a floored degree plus rounding")

    def test_converting_twice_changes_nothing(self):
        # The config is rewritten with eight points on the next apply, so the
        # conversion runs again on every later load. If it were not stable
        # the curve would creep a little further every time the app opened.
        once = fancurve.editor_points(self.SIX)
        self.assertEqual(fancurve.editor_points(once), once)
        self.assertEqual(fancurve.editor_points(fancurve.editor_points(once)),
                         once)

    def test_the_curve_the_firmware_gets_is_the_curve_on_screen(self):
        # The point of eight: no expansion between the editor and the write,
        # so every handle is one firmware slot.
        out = fancurve.editor_points(self.SIX)
        flat = fancurve.curve_to_flat(out, 8)
        expected = []
        for temp, pct in out:
            expected += [temp, fancurve.pct_to_pwm255(pct)]
        self.assertEqual(flat, expected)

    def test_every_profile_in_a_config_converts_without_loss(self):
        # Curve shapes taken across the stock profiles and the user's own:
        # flat, steep, front-loaded, and one already at eight points.
        for curve in ([[30, 0], [50, 0], [70, 30], [90, 60]],
                      [[40, 100], [50, 100], [60, 100], [90, 100]],
                      [[50, 9], [57, 21], [64, 35], [69, 49], [76, 62], [84, 99]],
                      [[45, 10], [55, 20], [60, 30], [65, 45],
                       [70, 60], [75, 75], [80, 90], [85, 100]]):
            with self.subTest(curve=curve):
                out = fancurve.editor_points(curve)
                self.assertEqual(len(out), 8)
                for point in curve:
                    self.assertIn(point, out)


class MovePoint(unittest.TestCase):
    def points(self):
        return [[50, 5], [57, 10], [63, 15], [68, 22],
                [73, 52], [78, 85], [85, 92], [92, 100]]

    def test_moves_the_point_it_is_given(self):
        out = fancurve.move_point(self.points(), 2, 65, 30)
        self.assertEqual(out[2], [65, 30])

    def test_stops_one_degree_short_of_the_next_point(self):
        # Not a swap and not an overlap: reordering mid-drag would renumber
        # the list under the hand doing the dragging.
        out = fancurve.move_point(self.points(), 2, 90, 15)
        self.assertEqual(out[2], [67, 15])

    def test_stops_one_degree_above_the_previous_point(self):
        out = fancurve.move_point(self.points(), 2, 10, 15)
        self.assertEqual(out[2], [58, 15])

    def test_the_ends_clamp_to_the_axis(self):
        self.assertEqual(fancurve.move_point(self.points(), 0, -50, 5)[0],
                         [0, 5])
        self.assertEqual(fancurve.move_point(self.points(), 7, 400, 100)[7],
                         [100, 100])

    def test_percent_clamps_at_both_ends(self):
        self.assertEqual(fancurve.move_point(self.points(), 3, 68, 500)[3][1],
                         100)
        self.assertEqual(fancurve.move_point(self.points(), 3, 68, -20)[3][1],
                         0)

    def test_other_points_are_untouched(self):
        original = self.points()
        out = fancurve.move_point(original, 2, 65, 30)
        self.assertEqual(out[:2] + out[3:], original[:2] + original[3:])

    def test_the_incoming_list_is_not_mutated(self):
        original = self.points()
        fancurve.move_point(original, 2, 65, 30)
        self.assertEqual(original, self.points())

    def test_an_index_off_the_end_changes_nothing(self):
        self.assertEqual(fancurve.move_point(self.points(), 99, 65, 30),
                         self.points())

    def test_a_drag_stays_valid_for_the_editor(self):
        # Whatever a drag does, the result must still be eight ordered points
        # the firmware will accept.
        points = self.points()
        for index in range(8):
            for temp, pct in ((0, 0), (100, 100), (60, 60), (-5, 130)):
                moved = fancurve.move_point(points, index, temp, pct)
                temps = [t for t, _ in moved]
                self.assertEqual(temps, sorted(set(temps)),
                                 f"index={index} temp={temp}")
                self.assertEqual(len(fancurve.interpolate_curve(moved, 8)), 8)


if __name__ == "__main__":
    unittest.main()
