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

    def test_every_fan_curve_has_four_points_on_all_three_channels(self):
        for name, prof in profiles.DEFAULT_PROFILES.items():
            for channel, points in prof["fans"].items():
                self.assertEqual(len(points), 4,
                                 f"{name} channel {channel} point count")
                for point in points:
                    self.assertEqual(len(point), 2,
                                     f"{name} channel {channel} point shape")

    def test_fan_curves_are_the_measured_ones(self):
        want = {
            "Quiet": [[40, 25], [60, 40], [75, 60], [90, 80]],
            "Balanced Power": [[40, 30], [60, 55], [75, 75], [90, 90]],
            "Balanced Performance": [[40, 30], [60, 55], [75, 75], [90, 90]],
            "Performance": [[40, 45], [55, 70], [70, 85], [85, 100]],
        }
        for name, curve in want.items():
            for channel in ("1", "2", "3"):
                self.assertEqual(profiles.DEFAULT_PROFILES[name]["fans"][channel],
                                 curve, f"{name} channel {channel}")

    def test_curves_rise_with_temperature(self):
        for name, prof in profiles.DEFAULT_PROFILES.items():
            for channel, points in prof["fans"].items():
                temps = [t for t, _ in points]
                speeds = [s for _, s in points]
                self.assertEqual(temps, sorted(set(temps)),
                                 f"{name} channel {channel} temps")
                self.assertEqual(speeds, sorted(set(speeds)),
                                 f"{name} channel {channel} speeds")
                self.assertLessEqual(max(speeds), 100)
                self.assertGreaterEqual(min(speeds), 0)

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
                         [40, 45])

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
