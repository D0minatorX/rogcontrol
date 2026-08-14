import unittest
from rogcontrol import profiles


class StockProfiles(unittest.TestCase):
    def test_order_is_quietest_first(self):
        self.assertEqual(list(profiles.DEFAULT_PROFILES),
                         ["Quiet", "Balanced Power",
                          "Balanced Performance", "Performance"])

    def test_every_profile_has_the_required_sections(self):
        for name, prof in profiles.DEFAULT_PROFILES.items():
            for section in ("cpu", "gpu", "fans"):
                self.assertIn(section, prof, f"{name} is missing {section}")
            self.assertEqual(sorted(prof["fans"]), ["1", "2", "3"])

    def test_every_profile_names_an_energy_preference(self):
        want = {"Quiet": "power", "Balanced Power": "balance_power",
                "Balanced Performance": "balance_performance",
                "Performance": "performance"}
        for name, epp in want.items():
            self.assertEqual(profiles.DEFAULT_PROFILES[name]["cpu"]["epp"], epp)

    def test_the_two_balanced_profiles_differ_only_in_epp(self):
        a = dict(profiles.DEFAULT_PROFILES["Balanced Power"]["cpu"])
        b = dict(profiles.DEFAULT_PROFILES["Balanced Performance"]["cpu"])
        a.pop("epp"); b.pop("epp")
        self.assertEqual(a, b)
        self.assertEqual(profiles.DEFAULT_PROFILES["Balanced Power"]["fans"],
                         profiles.DEFAULT_PROFILES["Balanced Performance"]["fans"])


class FieldTunedValues(unittest.TestCase):
    """The numbers themselves, pinned to literals.

    Everything above compares the module against itself, so a corrupted
    wattage or a dropped curve point would sail straight through. These are
    the values measured on real hardware; if one of them changes, that is a
    hardware retune and this test is meant to be the thing that says so.
    """

    def test_cpu_power_limits(self):
        want = {
            "Quiet": {"stapm": 25000, "fast": 35000, "slow": 25000, "temp": 85},
            "Balanced Power": {"stapm": 55000, "fast": 65000, "slow": 55000,
                               "temp": 90},
            "Balanced Performance": {"stapm": 55000, "fast": 65000,
                                     "slow": 55000, "temp": 90},
            "Performance": {"stapm": 75000, "fast": 90000, "slow": 75000,
                            "temp": 95},
        }
        for name, limits in want.items():
            cpu = profiles.DEFAULT_PROFILES[name]["cpu"]
            for key, value in limits.items():
                self.assertEqual(cpu[key], value, f"{name} cpu {key}")
            self.assertEqual(cpu["coall"], 0, f"{name} ships uncurved")

    def test_gpu_power_limits(self):
        want = {"Quiet": 65, "Balanced Power": 100,
                "Balanced Performance": 100, "Performance": 140}
        for name, watts in want.items():
            gpu = profiles.DEFAULT_PROFILES[name]["gpu"]
            self.assertEqual(gpu["watts"], watts, f"{name} gpu watts")
            self.assertEqual(gpu["clock_offset"], 0)
            self.assertEqual(gpu["mem_clock_offset"], 0)

    def test_every_fan_curve_has_eight_points_on_all_three_channels(self):
        """Eight, because eight is what the embedded controller stores: a
        stock profile with fewer would be padded by editor_points, and the
        curve being tuned would not be the curve being run."""
        for name, prof in profiles.DEFAULT_PROFILES.items():
            for channel, points in prof["fans"].items():
                self.assertEqual(len(points), 8,
                                 f"{name} channel {channel} point count")
                for point in points:
                    self.assertEqual(len(point), 2,
                                     f"{name} channel {channel} point shape")

    def test_fan_curves_are_the_measured_ones(self):
        """The shape that stopped the fans surging at idle: flat right
        across the band the EC's hidden Tctl spikes reach (50-86 C), with the
        real ramp above 90 C. See the comment above DEFAULT_PROFILES."""
        want = {
            "Quiet": {
                "main": [[50, 8], [60, 8], [70, 8], [80, 8], [86, 8],
                         [90, 10], [93, 50], [96, 90]],
                "mid": [[50, 6], [60, 6], [70, 6], [80, 6], [86, 6],
                        [90, 8], [93, 50], [96, 90]]},
            "Balanced Power": {
                "main": [[50, 10], [60, 10], [70, 10], [80, 10], [86, 10],
                         [90, 12], [93, 60], [96, 100]],
                "mid": [[50, 8], [60, 8], [70, 8], [80, 8], [86, 8],
                        [90, 10], [93, 60], [96, 100]]},
            "Balanced Performance": {
                "main": [[50, 10], [60, 10], [70, 10], [80, 10], [86, 10],
                         [90, 12], [93, 60], [96, 100]],
                "mid": [[50, 8], [60, 8], [70, 8], [80, 8], [86, 8],
                        [90, 10], [93, 60], [96, 100]]},
            "Performance": {
                "main": [[50, 16], [60, 16], [70, 16], [80, 16], [86, 16],
                         [90, 18], [93, 75], [96, 100]],
                "mid": [[50, 14], [60, 14], [70, 14], [80, 14], [86, 14],
                        [90, 16], [93, 75], [96, 100]]},
        }
        for name, curves in want.items():
            fans = profiles.DEFAULT_PROFILES[name]["fans"]
            # The CPU and GPU fans share a curve; the mid fan runs two points
            # cooler, which is how it was measured.
            for channel in ("1", "2"):
                self.assertEqual(fans[channel], curves["main"],
                                 f"{name} channel {channel}")
            self.assertEqual(fans["3"], curves["mid"], f"{name} mid fan")

    def test_the_flat_band_covers_the_hidden_spikes(self):
        """One speed from 50 C to 86 C, on every profile and every fan.

        This is the whole fix: Tctl bursts to 77-88 C in under a second at
        idle, the EC follows it within about a second, and any slope in that
        band turns those bursts into audible fan surges."""
        for name, prof in profiles.DEFAULT_PROFILES.items():
            for channel, points in prof["fans"].items():
                band = [pct for temp, pct in points if 50 <= temp <= 86]
                self.assertGreaterEqual(len(band), 5,
                                        f"{name} channel {channel} band")
                self.assertEqual(len(set(band)), 1,
                                 f"{name} channel {channel} is not flat "
                                 f"across 50-86 C: {band}")

    def test_the_real_ramp_is_above_ninety(self):
        for name, prof in profiles.DEFAULT_PROFILES.items():
            for channel, points in prof["fans"].items():
                flat = next(pct for temp, pct in points if temp == 50)
                top = points[-1]
                self.assertGreaterEqual(top[0], 93,
                                        f"{name} channel {channel} top point")
                self.assertGreaterEqual(top[1], flat + 40,
                                        f"{name} channel {channel} ramp")

    def test_curves_never_fall_with_temperature(self):
        """Non-decreasing, not strictly increasing: the flat band repeats one
        speed on purpose."""
        for name, prof in profiles.DEFAULT_PROFILES.items():
            for channel, points in prof["fans"].items():
                temps = [t for t, _ in points]
                speeds = [s for _, s in points]
                self.assertEqual(temps, sorted(set(temps)),
                                 f"{name} channel {channel} temps")
                self.assertEqual(speeds, sorted(speeds),
                                 f"{name} channel {channel} speeds")
                self.assertLessEqual(max(speeds), 100)
                self.assertGreaterEqual(min(speeds), 0)

    def test_the_tiers_are_quietest_first_in_the_flat_band(self):
        """Quiet quietest, Performance loudest, at the speed that is held
        almost all of the time."""
        order = list(profiles.DEFAULT_PROFILES)
        for quieter, louder in zip(order, order[1:]):
            for channel in ("1", "2", "3"):
                a = profiles.DEFAULT_PROFILES[quieter]["fans"][channel][0][1]
                b = profiles.DEFAULT_PROFILES[louder]["fans"][channel][0][1]
                self.assertLessEqual(a, b, f"{quieter} vs {louder} ch{channel}")

    def test_performance_is_the_most_aggressive_above_ninety(self):
        hot = {name: prof["fans"]["1"][-2][1]
               for name, prof in profiles.DEFAULT_PROFILES.items()}
        self.assertEqual(max(hot, key=hot.get), "Performance", hot)
        self.assertEqual(min(hot, key=hot.get), "Quiet", hot)

    def test_the_mid_fan_runs_below_the_other_two_in_the_flat_band(self):
        for name, prof in profiles.DEFAULT_PROFILES.items():
            fans = prof["fans"]
            self.assertLess(fans["3"][0][1], fans["1"][0][1], name)
            self.assertEqual(fans["1"], fans["2"], f"{name} cpu/gpu fans")

    def test_tiers_get_hotter_and_hungrier_in_menu_order(self):
        order = list(profiles.DEFAULT_PROFILES)
        for quieter, louder in zip(order, order[1:]):
            a, b = (profiles.DEFAULT_PROFILES[quieter],
                    profiles.DEFAULT_PROFILES[louder])
            self.assertLessEqual(a["cpu"]["stapm"], b["cpu"]["stapm"])
            self.assertLessEqual(a["cpu"]["temp"], b["cpu"]["temp"])
            self.assertLessEqual(a["gpu"]["watts"], b["gpu"]["watts"])


class PowerModeMapping(unittest.TestCase):
    def test_every_stock_profile_maps_to_a_mode(self):
        for name in profiles.DEFAULT_PROFILES:
            self.assertIn(name, profiles.PROFILE_TO_PPD_MODE)

    def test_only_the_three_real_ppd_modes_are_used(self):
        self.assertEqual(set(profiles.PROFILE_TO_PPD_MODE.values()),
                         {"performance", "balanced", "power-saver"})

    def test_shared_mode_resolves_to_the_first_profile(self):
        # Both Balanced profiles map to "balanced"; the reverse map must be
        # deterministic, not whichever key happened to be written last.
        self.assertEqual(profiles.PPD_MODE_TO_PROFILE["balanced"],
                         "Balanced Performance")

    def test_each_profile_maps_to_the_expected_mode(self):
        self.assertEqual(profiles.PROFILE_TO_PPD_MODE, {
            "Performance": "performance",
            "Balanced Performance": "balanced",
            "Balanced Power": "balanced",
            "Quiet": "power-saver",
        })

    def test_reverse_map_covers_every_mode(self):
        self.assertEqual(profiles.PPD_MODE_TO_PROFILE, {
            "performance": "Performance",
            "balanced": "Balanced Performance",
            "power-saver": "Quiet",
        })


class TailoredDefaults(unittest.TestCase):
    def test_gpu_watts_scale_to_the_card(self):
        out = profiles.tailored_default_profiles(gpu_min_w=5, gpu_max_w=70)
        self.assertLessEqual(out["Performance"]["gpu"]["watts"], 70)
        self.assertGreater(out["Performance"]["gpu"]["watts"],
                           out["Quiet"]["gpu"]["watts"])

    def test_cpu_limits_are_not_scaled(self):
        out = profiles.tailored_default_profiles(gpu_min_w=5, gpu_max_w=70)
        self.assertEqual(out["Quiet"]["cpu"],
                         profiles.DEFAULT_PROFILES["Quiet"]["cpu"])

    def test_result_is_a_copy(self):
        out = profiles.tailored_default_profiles(gpu_min_w=5, gpu_max_w=140)
        out["Quiet"]["cpu"]["stapm"] = 1
        self.assertNotEqual(profiles.DEFAULT_PROFILES["Quiet"]["cpu"]["stapm"], 1)

    def test_nested_fan_curves_are_copied_too(self):
        # A shallow copy would leave the curve lists shared with the module.
        out = profiles.tailored_default_profiles(gpu_min_w=5, gpu_max_w=140)
        out["Performance"]["fans"]["1"][0][1] = 99
        self.assertEqual(profiles.DEFAULT_PROFILES["Performance"]["fans"]["1"][0],
                         [50, 16])

    def test_tiers_keep_their_documented_shape(self):
        # The docstring promises Quiet ~46%, Balanced ~71%, Performance 100%
        # of the card's maximum. A 100W card makes those percentages literal.
        out = profiles.tailored_default_profiles(gpu_min_w=5, gpu_max_w=100)
        self.assertEqual(
            {name: prof["gpu"]["watts"] for name, prof in out.items()},
            {"Quiet": 46, "Balanced Power": 71,
             "Balanced Performance": 71, "Performance": 100})

    def test_the_reference_card_is_left_alone(self):
        # 140W is what the built-in numbers were chosen against, so scaling
        # to it must be a no-op.
        out = profiles.tailored_default_profiles(gpu_min_w=5, gpu_max_w=140)
        for name, prof in out.items():
            self.assertEqual(prof["gpu"]["watts"],
                             profiles.DEFAULT_PROFILES[name]["gpu"]["watts"])

    def test_a_tiny_card_never_drops_below_its_floor(self):
        out = profiles.tailored_default_profiles(gpu_min_w=20, gpu_max_w=30)
        for name, prof in out.items():
            self.assertGreaterEqual(prof["gpu"]["watts"], 20, name)
            self.assertLessEqual(prof["gpu"]["watts"], 30, name)


class PowerModeAgreement(unittest.TestCase):
    """The System page's sync verdict.

    Three-valued on purpose: a user's own profile has no OS power mode, and
    calling that "out of sync" would put a permanent warning on the page for
    a machine behaving perfectly."""

    def test_a_stock_profile_maps_to_its_mode(self):
        self.assertEqual(profiles.expected_ppd_mode("Quiet"), "power-saver")
        self.assertEqual(profiles.expected_ppd_mode("Performance"),
                         "performance")

    def test_a_user_profile_maps_to_nothing(self):
        self.assertIsNone(profiles.expected_ppd_mode("TEST"))
        self.assertIsNone(profiles.expected_ppd_mode(None))

    def test_agreeing_and_disagreeing(self):
        self.assertIs(
            profiles.ppd_modes_agree("Balanced Power", "balanced"), True)
        self.assertIs(
            profiles.ppd_modes_agree("Balanced Power", "performance"), False)

    def test_unknowable_rather_than_false(self):
        # No profile mapping, or no answer from the daemon: neither is a
        # disagreement, and reporting one as such would be a lie.
        self.assertIsNone(profiles.ppd_modes_agree("TEST", "balanced"))
        self.assertIsNone(profiles.ppd_modes_agree("Quiet", None))


if __name__ == "__main__":
    unittest.main()
