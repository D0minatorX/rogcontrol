"""Tests for the keyboard's colour arithmetic.

Everything in ``kbdcolor`` is a pure function over integer triples, which is
exactly why it was split out of the page: none of it needs a display, an
attached keyboard, or a privileged helper to write to. A gradient that ramps
the wrong way or a restore token dropped while saving is invisible until a
user notices their keyboard is wrong, so it gets pinned here instead.
"""

import unittest

from rogcontrol import kbdcolor


class TestSupportedModes(unittest.TestCase):
    def test_a_fully_capable_machine_is_offered_everything(self):
        caps = {"kbd_rgb_zones": True, "kbd_battery": True,
                "nvidia": True, "kbd_ambient": True}
        self.assertEqual(kbdcolor.supported_modes(caps),
                         list(kbdcolor.KBD_RGB_MODES))

    def test_missing_hardware_drops_only_the_modes_that_need_it(self):
        caps = {"kbd_rgb_zones": False, "kbd_battery": False,
                "nvidia": False, "kbd_ambient": False}
        modes = kbdcolor.supported_modes(caps)
        for gone in ("Gradient Static", "Battery Level", "GPU Temp Color",
                     "Ambient"):
            self.assertNotIn(gone, modes)
        # Everything a single-zone controller can still do stays.
        for kept in ("Static", "Breathing", "Pulse", "Color Cycle",
                     "Rainbow", "CPU Temp Color"):
            self.assertIn(kept, modes)

    def test_unknown_capabilities_are_generous_rather_than_restrictive(self):
        # Detection runs once at startup and the USB controller may not have
        # enumerated yet, so an unanswered question must not remove a mode.
        self.assertEqual(kbdcolor.supported_modes({}),
                         list(kbdcolor.KBD_RGB_MODES))
        self.assertEqual(kbdcolor.supported_modes(None),
                         list(kbdcolor.KBD_RGB_MODES))


class TestByteFloatRoundTrip(unittest.TestCase):
    def test_every_channel_value_survives_a_round_trip(self):
        # The whole reason float_to_byte rounds instead of truncating: a
        # colour loaded into a picker and saved back must be byte-identical,
        # or a pink walks towards red over a week of opening the app.
        for value in range(256):
            self.assertEqual(
                kbdcolor.float_to_byte(kbdcolor.byte_to_float(value)), value)

    def test_the_ends_of_the_range_map_to_the_ends(self):
        self.assertEqual(kbdcolor.byte_to_float(0), 0.0)
        self.assertEqual(kbdcolor.byte_to_float(255), 1.0)
        self.assertEqual(kbdcolor.float_to_byte(0.0), 0)
        self.assertEqual(kbdcolor.float_to_byte(1.0), 255)

    def test_out_of_range_floats_are_clamped_not_wrapped(self):
        self.assertEqual(kbdcolor.float_to_byte(-0.5), 0)
        self.assertEqual(kbdcolor.float_to_byte(1.5), 255)

    def test_unusable_values_land_somewhere_sane(self):
        # The config is a text file a user can edit by hand.
        self.assertEqual(kbdcolor.float_to_byte("pink"), 0)
        self.assertEqual(kbdcolor.clamp_byte(None), 0)
        self.assertEqual(kbdcolor.clamp_byte("nonsense", fallback=42), 42)
        self.assertEqual(kbdcolor.clamp_byte("130"), 130)
        self.assertEqual(kbdcolor.clamp_byte(300), 255)
        self.assertEqual(kbdcolor.clamp_byte(-1), 0)

    def test_speed_is_clamped_to_what_rogauracore_accepts(self):
        self.assertEqual(kbdcolor.clamp_speed(0), kbdcolor.SPEED_MIN)
        self.assertEqual(kbdcolor.clamp_speed(99), kbdcolor.SPEED_MAX)
        self.assertEqual(kbdcolor.clamp_speed(None), kbdcolor.DEFAULT_SPEED)
        self.assertEqual(kbdcolor.clamp_speed("2"), 2)


class TestSavedColor(unittest.TestCase):
    def test_reads_the_first_and_second_colours(self):
        saved = {"r": 255, "g": 70, "b": 100, "r2": 1, "g2": 2, "b2": 3}
        self.assertEqual(kbdcolor.saved_color(saved), (255, 70, 100))
        self.assertEqual(kbdcolor.saved_color(saved, "2"), (1, 2, 3))

    def test_missing_channels_fall_back_per_channel(self):
        # Not to black: a half-written block should keep the channels it has
        # and take the default only for the ones it is missing.
        self.assertEqual(kbdcolor.saved_color({"g": 130}, "",
                                              default=(9, 8, 7)),
                         (9, 130, 7))

    def test_an_empty_block_is_the_default_colour(self):
        self.assertEqual(kbdcolor.saved_color({}), kbdcolor.DEFAULT_COLOR)
        self.assertEqual(kbdcolor.saved_color(None), kbdcolor.DEFAULT_COLOR)


class TestMergeKbdRgb(unittest.TestCase):
    def test_carries_the_restore_token_across_a_save(self):
        # Losing it makes the desktop ask for screen permission again, which
        # is the bug this function exists to prevent.
        saved = {"mode": "Ambient", "ambient_restore_token": "tok",
                 "r3": 1, "g3": 2, "b3": 3, "color_count": 2}
        block = kbdcolor.merge_kbd_rgb(saved, "Static", (1, 2, 3), (4, 5, 6), 2)
        self.assertEqual(block["ambient_restore_token"], "tok")
        self.assertEqual((block["r3"], block["g3"], block["b3"]), (1, 2, 3))
        self.assertEqual(block["color_count"], 2)

    def test_writes_what_the_page_owns(self):
        block = kbdcolor.merge_kbd_rgb({}, "Breathing", (1, 2, 3), (4, 5, 6), 3)
        self.assertEqual(block["mode"], "Breathing")
        self.assertEqual((block["r"], block["g"], block["b"]), (1, 2, 3))
        self.assertEqual((block["r2"], block["g2"], block["b2"]), (4, 5, 6))
        self.assertEqual(block["speed"], 3)

    def test_does_not_invent_keys_that_were_never_stored(self):
        block = kbdcolor.merge_kbd_rgb({}, "Static", (0, 0, 0), (0, 0, 0), 1)
        for key in kbdcolor.CARRIED_KEYS:
            self.assertNotIn(key, block)


class TestTempToRgb(unittest.TestCase):
    def test_cold_is_blue_and_hot_is_red(self):
        self.assertEqual(kbdcolor.temp_to_rgb(kbdcolor.TEMP_COLOR_MIN_C),
                         (0, 0, 255))
        self.assertEqual(kbdcolor.temp_to_rgb(kbdcolor.TEMP_COLOR_MAX_C),
                         (255, 0, 0))

    def test_the_middle_of_the_range_is_green(self):
        middle = (kbdcolor.TEMP_COLOR_MIN_C + kbdcolor.TEMP_COLOR_MAX_C) / 2
        self.assertEqual(kbdcolor.temp_to_rgb(middle), (0, 255, 0))

    def test_beyond_the_ends_is_clamped_rather_than_extrapolated(self):
        self.assertEqual(kbdcolor.temp_to_rgb(-40), (0, 0, 255))
        self.assertEqual(kbdcolor.temp_to_rgb(200), (255, 0, 0))

    def test_it_ramps_the_right_way(self):
        # The bug this catches is an inverted gradient: a keyboard that goes
        # blue as the chip cooks looks fine until you check it against a
        # thermometer.
        lo, hi = kbdcolor.TEMP_COLOR_MIN_C, kbdcolor.TEMP_COLOR_MAX_C
        reds = [kbdcolor.temp_to_rgb(t)[0] for t in range(int(lo), int(hi) + 1)]
        self.assertEqual(reds, sorted(reds))
        blues = [kbdcolor.temp_to_rgb(t)[2]
                 for t in range(int(lo), int(hi) + 1)]
        self.assertEqual(blues, sorted(blues, reverse=True))

    def test_every_output_channel_is_a_byte(self):
        for temp in range(0, 121):
            for channel in kbdcolor.temp_to_rgb(temp):
                self.assertIsInstance(channel, int)
                self.assertGreaterEqual(channel, 0)
                self.assertLessEqual(channel, 255)


class TestBatteryToRgb(unittest.TestCase):
    def test_discharging_runs_green_to_red(self):
        self.assertEqual(kbdcolor.battery_to_rgb(100, False), (0, 255, 0))
        self.assertEqual(kbdcolor.battery_to_rgb(50, False), (255, 255, 0))
        self.assertEqual(kbdcolor.battery_to_rgb(0, False), (255, 0, 0))

    def test_charging_runs_blue_to_green_instead(self):
        # A different ramp on purpose: a glance should say whether it is
        # filling or draining without having to remember shades of green.
        self.assertEqual(kbdcolor.battery_to_rgb(0, True), (0, 0, 255))
        self.assertEqual(kbdcolor.battery_to_rgb(100, True), (0, 255, 0))

    def test_a_percentage_outside_the_range_is_clamped(self):
        self.assertEqual(kbdcolor.battery_to_rgb(-5, False), (255, 0, 0))
        self.assertEqual(kbdcolor.battery_to_rgb(140, False), (0, 255, 0))

    def test_green_drains_monotonically_as_the_battery_empties(self):
        greens = [kbdcolor.battery_to_rgb(p, False)[1] for p in range(101)]
        self.assertEqual(greens, sorted(greens))


class TestGradientZoneColors(unittest.TestCase):
    def test_the_ends_are_exactly_the_two_chosen_colours(self):
        # So a gradient between two identical colours is indistinguishable
        # from Static, which is what people expect of a gradient.
        zones = kbdcolor.gradient_zone_colors((0, 0, 0), (255, 255, 255))
        self.assertEqual(zones[0], (0, 0, 0))
        self.assertEqual(zones[-1], (255, 255, 255))

    def test_it_produces_one_colour_per_zone_evenly_spaced(self):
        zones = kbdcolor.gradient_zone_colors((0, 0, 0), (255, 0, 0))
        self.assertEqual(len(zones), kbdcolor.KBD_ZONES)
        self.assertEqual([z[0] for z in zones], [0, 85, 170, 255])

    def test_identical_colours_give_a_flat_ramp(self):
        zones = kbdcolor.gradient_zone_colors((7, 8, 9), (7, 8, 9))
        self.assertEqual(zones, [(7, 8, 9)] * kbdcolor.KBD_ZONES)

    def test_a_single_zone_is_just_the_first_colour(self):
        self.assertEqual(kbdcolor.gradient_zone_colors((1, 2, 3), (9, 9, 9),
                                                       zones=1),
                         [(1, 2, 3)])

    def test_it_ramps_downwards_too(self):
        zones = kbdcolor.gradient_zone_colors((255, 0, 0), (0, 0, 255))
        self.assertEqual([z[0] for z in zones], [255, 170, 85, 0])
        self.assertEqual([z[2] for z in zones], [0, 85, 170, 255])


class TestBoostAmbient(unittest.TestCase):
    def test_a_dim_colour_is_lifted_to_the_target_level(self):
        self.assertEqual(max(kbdcolor.boost_ambient((30, 15, 10))),
                         kbdcolor.AMBIENT_TARGET_LEVEL)

    def test_the_hue_survives_the_lift(self):
        # Scaling the brightest channel up must not tint the colour: the
        # ratios between channels are what makes it the screen's colour.
        boosted = kbdcolor.boost_ambient((40, 20, 10))
        self.assertAlmostEqual(boosted[0] / boosted[1], 2.0, delta=0.05)
        self.assertAlmostEqual(boosted[0] / boosted[2], 4.0, delta=0.15)

    def test_a_genuinely_dark_region_is_left_dark(self):
        # Amplifying near-black turns sensor noise into colour noise.
        dark = (kbdcolor.AMBIENT_DARK_LEVEL - 1, 2, 0)
        self.assertEqual(kbdcolor.boost_ambient(dark), dark)
        self.assertEqual(kbdcolor.boost_ambient((0, 0, 0)), (0, 0, 0))

    def test_an_already_bright_colour_is_left_alone(self):
        bright = (kbdcolor.AMBIENT_TARGET_LEVEL, 100, 50)
        self.assertEqual(kbdcolor.boost_ambient(bright), bright)
        self.assertEqual(kbdcolor.boost_ambient((255, 255, 255)),
                         (255, 255, 255))

    def test_it_never_pushes_a_channel_past_a_byte(self):
        for peak in range(kbdcolor.AMBIENT_DARK_LEVEL,
                          kbdcolor.AMBIENT_TARGET_LEVEL):
            for channel in kbdcolor.boost_ambient((peak, peak, peak)):
                self.assertLessEqual(channel, 255)


class TestAverageColor(unittest.TestCase):
    def test_it_averages_the_zones(self):
        self.assertEqual(
            kbdcolor.average_color([(0, 0, 0), (100, 200, 40)]),
            (50, 100, 20))

    def test_no_zones_is_black_rather_than_a_crash(self):
        self.assertEqual(kbdcolor.average_color([]), (0, 0, 0))


class TestZonesFromFrame(unittest.TestCase):
    """The Ambient sampler's frame arithmetic, on a hand-built buffer."""

    @staticmethod
    def frame(width, height, pixel, stride=None):
        """A packed RGB frame whose pixel colour is ``pixel(x, y)``."""
        stride = stride or width * 3
        data = bytearray(stride * height)
        for y in range(height):
            for x in range(width):
                i = y * stride + x * 3
                data[i:i + 3] = bytes(pixel(x, y))
        return bytes(data), stride

    def test_each_vertical_band_becomes_its_own_zone(self):
        # Four solid vertical bands in, four distinct zone colours out.
        bands = [(200, 0, 0), (0, 200, 0), (0, 0, 200), (200, 200, 0)]
        data, stride = self.frame(
            8, 4, lambda x, _y: bands[x // 2])
        zones = kbdcolor.zones_from_frame(data, 8, 4, stride)
        self.assertEqual(zones, bands)

    def test_a_padded_stride_does_not_skew_the_colours(self):
        # GStreamer pads rows to a 4-byte boundary. Reading with width * 3
        # instead walks progressively out of alignment down the frame, which
        # tints the lower zones -- the exact bug the stride guards against.
        width, height = 6, 4
        stride = width * 3 + 2  # deliberately padded
        data, _ = self.frame(width, height, lambda _x, _y: (0, 0, 255),
                             stride=stride)
        zones = kbdcolor.zones_from_frame(data, width, height, stride)
        self.assertEqual(zones, [(0, 0, 255)] * kbdcolor.KBD_ZONES)

    def test_the_zone_colours_are_boosted(self):
        # A dim screen must still light the keys, so the boost applies here
        # and not somewhere the sampler could forget to call it.
        data, stride = self.frame(8, 2, lambda _x, _y: (30, 0, 0))
        zones = kbdcolor.zones_from_frame(data, 8, 2, stride)
        self.assertEqual(zones,
                         [(kbdcolor.AMBIENT_TARGET_LEVEL, 0, 0)] * 4)

    def test_an_empty_frame_gives_nothing_rather_than_dividing_by_zero(self):
        self.assertIsNone(kbdcolor.zones_from_frame(b"", 4, 0, 12))

    def test_the_last_zone_takes_the_remainder_of_an_odd_width(self):
        # 7 columns over 4 zones: the bands are 1, 1, 1, 4 rather than
        # dropping the three columns that do not divide evenly.
        data, stride = self.frame(7, 1, lambda x, _y: (0, 0, 0) if x < 3
                                  else (255, 255, 255))
        zones = kbdcolor.zones_from_frame(data, 7, 1, stride)
        self.assertEqual(len(zones), kbdcolor.KBD_ZONES)
        self.assertEqual(zones[-1], (255, 255, 255))


class TestChangedEnough(unittest.TestCase):
    def test_the_first_frame_always_counts_as_a_change(self):
        self.assertTrue(kbdcolor.changed_enough([(1, 2, 3)], None))

    def test_an_identical_frame_is_not_worth_a_usb_write(self):
        zones = [(10, 20, 30), (40, 50, 60)]
        self.assertFalse(kbdcolor.changed_enough(zones, list(zones)))

    def test_noise_below_the_threshold_is_ignored(self):
        was = [(100, 100, 100)]
        nudged = [(100 + kbdcolor.AMBIENT_MIN_DELTA - 1, 100, 100)]
        self.assertFalse(kbdcolor.changed_enough(nudged, was))

    def test_a_real_change_gets_through(self):
        was = [(100, 100, 100)]
        moved = [(100 + kbdcolor.AMBIENT_MIN_DELTA, 100, 100)]
        self.assertTrue(kbdcolor.changed_enough(moved, was))

    def test_a_change_in_any_single_zone_is_enough(self):
        was = [(0, 0, 0)] * 4
        moved = [(0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 200)]
        self.assertTrue(kbdcolor.changed_enough(moved, was))


class TestHelperArgs(unittest.TestCase):
    def test_static_sends_one_colour(self):
        self.assertEqual(kbdcolor.helper_args("Static", (1, 2, 3)),
                         ("kbdrgb", "single_static", 1, 2, 3))

    def test_every_animated_mode_is_given_its_speed(self):
        # The speed argument used to be omitted for these, which meant the
        # slider moved and the animation did not.
        for mode in kbdcolor.SPEED_MODES:
            args = kbdcolor.helper_args(mode, (1, 2, 3), (4, 5, 6), speed=3)
            self.assertEqual(args[-1], 3, mode)

    def test_colour_cycle_and_rainbow_take_no_colour(self):
        self.assertEqual(kbdcolor.helper_args("Rainbow", speed=1),
                         ("kbdrgb", "rainbow", 1))
        self.assertEqual(kbdcolor.helper_args("Color Cycle", speed=2),
                         ("kbdrgb", "single_colorcycle", 2))

    def test_breathing_sends_both_colours(self):
        self.assertEqual(
            kbdcolor.helper_args("Breathing", (1, 2, 3), (4, 5, 6), 2),
            ("kbdrgb", "single_breathing", 1, 2, 3, 4, 5, 6, 2))

    def test_gradient_static_expands_to_one_colour_per_zone(self):
        args = kbdcolor.helper_args("Gradient Static", (0, 0, 0),
                                    (255, 255, 255))
        self.assertEqual(args[:2], ("kbdrgb", "multi_static"))
        self.assertEqual(len(args), 2 + kbdcolor.KBD_ZONES * 3)

    def test_the_live_modes_have_no_arguments_of_their_own(self):
        # Their colour is not known here: the caller takes a reading first.
        for mode in kbdcolor.LIVE_COLOUR_MODES + ("Ambient",):
            self.assertIsNone(kbdcolor.helper_args(mode), mode)

    def test_an_unknown_mode_falls_back_to_a_plain_colour(self):
        self.assertEqual(kbdcolor.helper_args("Disco Inferno", (9, 8, 7)),
                         ("kbdrgb", "single_static", 9, 8, 7))

    def test_out_of_range_input_is_clamped_before_it_reaches_the_helper(self):
        args = kbdcolor.helper_args("Static", (999, -5, 3.6))
        self.assertEqual(args, ("kbdrgb", "single_static", 255, 0, 4))


if __name__ == "__main__":
    unittest.main()
