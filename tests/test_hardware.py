"""Tests for the sysfs readers.

Every reader takes a ``root``, so these build a fake /sys on disk and point
the module at it. That is the whole reason the parameter exists: patching
``open`` would test the patch, while a real directory tree tests the paths --
and wrong paths, not wrong parsing, are what actually broke in this codebase
(fan rpm lives on the ``asus`` hwmon, the curve lives on
``asus_custom_fan_curve``, and hwmonN numbering moves between boots).
"""

import os
import tempfile
import unittest

from rogcontrol import hardware


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class FakeSysfs(unittest.TestCase):
    """A temporary directory standing in for /sys and /proc."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def hwmon(self, index, name):
        """Create hwmon<index> named <name>, returning its path."""
        path = os.path.join(self.root, "sys/class/hwmon", f"hwmon{index}")
        write(os.path.join(path, "name"), f"{name}\n")
        return path

    def policy(self, index, **files):
        path = os.path.join(
            self.root, "sys/devices/system/cpu/cpufreq", f"policy{index}")
        for key, value in files.items():
            write(os.path.join(path, key), f"{value}\n")
        return path

    def battery(self, name="BAT0", **files):
        path = os.path.join(self.root, "sys/class/power_supply", name)
        write(os.path.join(path, "type"), "Battery\n")
        for key, value in files.items():
            write(os.path.join(path, key), f"{value}\n")
        return path


class TestReadFile(unittest.TestCase):
    def test_missing_file_is_none_not_an_exception(self):
        # Sensors that do not exist on this model are the normal case, so a
        # missing file must not take a whole refresh down.
        self.assertIsNone(hardware.read_file("/nonexistent/rogcontrol/test"))
        self.assertIsNone(hardware.read_int("/nonexistent/rogcontrol/test"))

    def test_unparseable_int_is_none(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt") as f:
            f.write("N/A\n")
            f.flush()
            self.assertEqual(hardware.read_file(f.name), "N/A")
            self.assertIsNone(hardware.read_int(f.name))


class TestFindHwmon(FakeSysfs):
    def test_finds_by_name_not_by_number(self):
        # The number is assigned in probe order and moves between boots,
        # which is why nothing may hardcode hwmon7.
        self.hwmon(3, "k10temp")
        expected = self.hwmon(11, "asus_custom_fan_curve")
        self.assertEqual(
            hardware.find_hwmon_by_name("asus_custom_fan_curve", root=self.root),
            expected)

    def test_absent_chip_is_none(self):
        self.hwmon(0, "acpitz_0")
        self.assertIsNone(hardware.find_hwmon_by_name("asus", root=self.root))

    def test_does_not_match_a_prefix(self):
        # "asus" and "asus_custom_fan_curve" are different chips exposing
        # different files; matching loosely would read fan rpm off the wrong
        # one and silently report nothing.
        self.hwmon(1, "asus_custom_fan_curve")
        self.assertIsNone(hardware.find_hwmon_by_name("asus", root=self.root))


class TestCpuReaders(FakeSysfs):
    def test_cpu_temp_is_millidegrees(self):
        path = self.hwmon(7, "k10temp")
        write(os.path.join(path, "temp1_input"), "65000\n")
        self.assertAlmostEqual(hardware.read_cpu_temp(root=self.root), 65.0)

    def test_cpu_temp_falls_back_to_thermal_zone(self):
        path = self.hwmon(2, "acpitz_0")
        write(os.path.join(path, "temp1_input"), "48000\n")
        self.assertAlmostEqual(hardware.read_cpu_temp(root=self.root), 48.0)

    def test_cpu_temp_none_without_any_sensor(self):
        self.hwmon(0, "ADP0")
        self.assertIsNone(hardware.read_cpu_temp(root=self.root))

    def test_peak_core_clock_is_the_highest_policy(self):
        self.policy(0, cpuinfo_avg_freq=2834586)
        self.policy(1, cpuinfo_avg_freq=4102000)
        self.policy(2, cpuinfo_avg_freq=1400000)
        # kHz in, MHz out, and the peak rather than a mean -- the useful
        # question is how high anything is boosting.
        self.assertEqual(hardware.read_peak_core_clock_mhz(root=self.root), 4102)

    def test_peak_core_clock_falls_back_to_scaling_cur_freq(self):
        # cpuinfo_avg_freq is amd-pstate's; other drivers only have the
        # governor's requested value.
        self.policy(0, scaling_cur_freq=3000000)
        self.assertEqual(hardware.read_peak_core_clock_mhz(root=self.root), 3000)

    def test_peak_core_clock_skips_policies_without_the_file(self):
        self.policy(0, cpuinfo_max_freq=5400000)   # no current-speed file
        self.policy(1, cpuinfo_avg_freq=2000000)
        self.assertEqual(hardware.read_peak_core_clock_mhz(root=self.root), 2000)

    def test_peak_core_clock_none_without_cpufreq(self):
        self.assertIsNone(hardware.read_peak_core_clock_mhz(root=self.root))

    def test_clock_range_comes_from_cpuinfo_not_scaling(self):
        # A cap already in force must not shrink the control's range, or
        # there would be no way back up.
        self.policy(0, cpuinfo_min_freq=400000, cpuinfo_max_freq=5400000,
                    scaling_max_freq=3200000)
        self.assertEqual(hardware.read_cpu_clock_range(root=self.root),
                         (400000, 5400000))

    def test_clock_range_rejects_a_degenerate_range(self):
        self.policy(0, cpuinfo_min_freq=5400000, cpuinfo_max_freq=5400000)
        self.assertIsNone(hardware.read_cpu_clock_range(root=self.root))

    def test_current_clock_cap(self):
        self.policy(0, scaling_max_freq=3200000)
        self.assertEqual(hardware.read_current_cpu_clock_cap(root=self.root),
                         3200000)

    def test_boost_reads_the_global_switch_first(self):
        write(os.path.join(self.root,
                           "sys/devices/system/cpu/cpufreq/boost"), "0\n")
        self.assertIs(hardware.read_cpu_boost_enabled(root=self.root), False)

    def test_boost_falls_back_to_per_policy(self):
        self.policy(0, boost=1)
        self.assertIs(hardware.read_cpu_boost_enabled(root=self.root), True)

    def test_boost_unknown_is_none_not_false(self):
        # "no switch on this machine" must be distinguishable from "off", or
        # the UI would show a control that cannot do anything.
        self.assertIsNone(hardware.read_cpu_boost_enabled(root=self.root))

    def test_epp_preferences(self):
        self.policy(0, energy_performance_available_preferences=(
            "default performance balance_performance balance_power power"))
        self.assertIn("balance_power",
                      hardware.read_epp_preferences(root=self.root))
        self.assertEqual(hardware.read_epp_preferences(root=self.root)[0],
                         "default")


class TestPackagePower(FakeSysfs):
    def test_package_power_comes_off_the_amdgpu_node(self):
        # Counterintuitive but correct: on this APU the whole-package PPT is
        # published by amdgpu, and there is no equivalent under k10temp.
        self.hwmon(7, "k10temp")
        path = self.hwmon(6, "amdgpu")
        write(os.path.join(path, "power1_input"), "32029000\n")
        self.assertAlmostEqual(
            hardware.read_package_power_w(root=self.root), 32.029, places=3)

    def test_none_without_amdgpu(self):
        self.hwmon(7, "k10temp")
        self.assertIsNone(hardware.read_package_power_w(root=self.root))


class TestFans(FakeSysfs):
    def test_rpms_come_off_the_asus_node(self):
        path = self.hwmon(10, "asus")
        for channel, rpm in (("1", 2200), ("2", 2200), ("3", 2500)):
            write(os.path.join(path, f"fan{channel}_input"), f"{rpm}\n")
        self.assertEqual(hardware.read_fan_rpms(root=self.root),
                         {"1": 2200, "2": 2200, "3": 2500})

    def test_missing_channel_is_none(self):
        path = self.hwmon(10, "asus")
        write(os.path.join(path, "fan1_input"), "2200\n")
        self.assertEqual(hardware.read_fan_rpms(root=self.root),
                         {"1": 2200, "2": None, "3": None})

    def test_no_asus_hwmon_gives_all_none(self):
        self.assertEqual(hardware.read_fan_rpms(root=self.root),
                         {"1": None, "2": None, "3": None})

    def test_curve_enabled_is_one_and_dropped_is_two(self):
        path = self.hwmon(11, "asus_custom_fan_curve")
        write(os.path.join(path, "pwm1_enable"), "1\n")
        write(os.path.join(path, "pwm2_enable"), "2\n")
        write(os.path.join(path, "pwm3_enable"), "1\n")
        self.assertEqual(hardware.read_fan_curve_enabled(root=self.root),
                         {"1": True, "2": False, "3": True})

    def test_no_curve_interface_is_none_not_false(self):
        # None means "this machine has no custom curve"; False means "the EC
        # threw our curve away". Collapsing them would make the overview
        # cry wolf on every machine without the interface.
        self.assertEqual(hardware.read_fan_curve_enabled(root=self.root),
                         {"1": None, "2": None, "3": None})

    def curve_points(self, channel, pairs):
        path = self.hwmon(11, "asus_custom_fan_curve")
        for i, (temp, pwm) in enumerate(pairs, start=1):
            write(os.path.join(path, f"pwm{channel}_auto_point{i}_temp"),
                  f"{temp}\n")
            write(os.path.join(path, f"pwm{channel}_auto_point{i}_pwm"),
                  f"{pwm}\n")
        return path

    def test_curve_points_read_back_as_temp_and_pwm(self):
        # pwm, not percent: this is what the driver stores, and the page
        # compares in the same units so that a curve cannot differ from
        # itself through rounding.
        pairs = [(50, 10), (55, 18), (60, 31), (68, 36),
                 (78, 41), (83, 43), (88, 46), (93, 153)]
        self.curve_points("1", pairs)
        self.assertEqual(hardware.read_fan_curve_points("1", root=self.root),
                         pairs)

    def test_curve_points_are_per_channel(self):
        self.curve_points("1", [(t, 10) for t in range(30, 38)])
        self.curve_points("2", [(t, 20) for t in range(40, 48)])
        self.assertEqual(hardware.read_fan_curve_points("2", root=self.root),
                         [(t, 20) for t in range(40, 48)])

    def test_curve_points_are_none_when_a_point_is_missing(self):
        # A partial read must not be reported as a curve: the page would
        # compare it against the editor, find a difference and claim the
        # user has unapplied changes they never made.
        self.curve_points("1", [(50, 10), (55, 18)])
        self.assertIsNone(hardware.read_fan_curve_points("1", root=self.root))

    def test_curve_points_are_none_without_the_interface(self):
        self.assertIsNone(hardware.read_fan_curve_points("1", root=self.root))


class TestBattery(FakeSysfs):
    def test_percentage_and_charging(self):
        self.battery(capacity=82, status="Charging")
        self.assertEqual(hardware.read_battery(root=self.root), (82, True))

    def test_not_charging_at_the_limit_is_not_charging(self):
        # A charge-limited ASUS on AC reports "Not charging"; showing that as
        # charging would make the readout lie.
        self.battery(capacity=80, status="Not charging")
        self.assertEqual(hardware.read_battery(root=self.root), (80, False))

    def test_skips_non_battery_supplies(self):
        write(os.path.join(self.root, "sys/class/power_supply/ADP0/type"),
              "Mains\n")
        self.battery(name="BAT1", capacity=55, status="Discharging")
        self.assertEqual(hardware.read_battery(root=self.root), (55, False))

    def test_no_battery(self):
        self.assertEqual(hardware.read_battery(root=self.root), (None, None))

    def test_charge_limit_read_from_firmware(self):
        self.battery(capacity=82, status="Not charging",
                     charge_control_end_threshold=80)
        self.assertEqual(hardware.read_charge_limit(root=self.root), 80)

    def test_charge_limit_absent(self):
        self.battery(capacity=82, status="Discharging")
        self.assertIsNone(hardware.read_charge_limit(root=self.root))

    def test_ac_connected(self):
        base = os.path.join(self.root, "sys/class/power_supply/ADP0")
        write(os.path.join(base, "type"), "Mains\n")
        write(os.path.join(base, "online"), "1\n")
        self.assertIs(hardware.is_ac_connected(root=self.root), True)
        write(os.path.join(base, "online"), "0\n")
        self.assertIs(hardware.is_ac_connected(root=self.root), False)

    def test_ac_unknown_without_mains(self):
        self.assertIsNone(hardware.is_ac_connected(root=self.root))


class TestCapabilities(FakeSysfs):
    def test_reports_what_the_fake_machine_has(self):
        self.hwmon(10, "asus")
        self.hwmon(11, "asus_custom_fan_curve")
        self.hwmon(7, "k10temp")
        self.policy(0, cpuinfo_min_freq=400000, cpuinfo_max_freq=5400000,
                    energy_performance_available_preferences="performance custom")
        self.battery(capacity=80, charge_control_end_threshold=80)
        caps = hardware.detect_capabilities(root=self.root)
        self.assertTrue(caps["fan_curve"])
        self.assertTrue(caps["fan_rpm"])
        self.assertTrue(caps["cpu_temp"])
        self.assertTrue(caps["charge_limit"])
        self.assertEqual(caps["cpu_clock"], (400000, 5400000))
        # "custom" needs a raw 0-255 value written elsewhere, so offering it
        # would only ever produce failures.
        self.assertEqual(caps["cpu_epp"], ["performance"])

    def test_bare_machine_reports_nothing_available(self):
        caps = hardware.detect_capabilities(root=self.root)
        for key in ("fan_curve", "fan_rpm", "cpu_temp", "pkg_power",
                    "charge_limit", "cpu_boost"):
            self.assertFalse(caps[key], key)
        self.assertIsNone(caps["cpu_clock"])
        self.assertEqual(caps["cpu_epp"], [])


if __name__ == "__main__":
    unittest.main()
