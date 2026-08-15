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
from unittest import mock

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

    def usb_device(self, name, vendor, product):
        path = os.path.join(self.root, "sys/bus/usb/devices", name)
        write(os.path.join(path, "idVendor"), f"{vendor}\n")
        write(os.path.join(path, "idProduct"), f"{product}\n")
        return path

    def kbd_backlight(self, level):
        write(os.path.join(self.root, hardware.KBD_BACKLIGHT_PATH.lstrip("/")),
              f"{level}\n")


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


class TestGpuLimitParsing(unittest.TestCase):
    """Reading the card's own limits out of nvidia-smi.

    The fixtures are literal output from an RTX 5070 Ti Laptop GPU on driver
    610.57.04. Parsing is where this breaks -- the CSV gains a column, the
    query dump reorders its sections -- so the input is kept verbatim rather
    than reduced to the two numbers it contains."""

    CSV = "NVIDIA GeForce RTX 5070 Ti Laptop GPU, 5.00, 140.00\n"

    CLOCK_DUMP = """
GPU 00000000:01:00.0
    Clocks
        Graphics                                       : 555 MHz
        SM                                             : 555 MHz
    Max Clocks
        Graphics                                       : 3090 MHz
        SM                                             : 3090 MHz
        Memory                                         : 14001 MHz
    Max Customer Boost Clocks
        Graphics                                       : N/A
"""

    def test_power_range_off_the_card(self):
        self.assertEqual(hardware.parse_gpu_power_limits(self.CSV), (5, 140))

    def test_the_card_names_itself(self):
        self.assertEqual(hardware.parse_gpu_name(self.CSV),
                         "NVIDIA GeForce RTX 5070 Ti Laptop GPU")

    def test_an_unusable_range_is_refused(self):
        # A slider handed any of these would have an empty or inverted
        # range, which is worse than falling back to the defaults.
        for row in ("GPU, [N/A], 140.00", "GPU, 140.00, 5.00",
                    "GPU, 0.00, 0.00", "GPU, 140.00", "", "   "):
            self.assertIsNone(hardware.parse_gpu_power_limits(row), row)

    def test_max_clock_comes_from_the_max_section(self):
        # 555 is the clock the card happens to be running at; taking the
        # first "Graphics" line would report that as the ceiling.
        self.assertEqual(hardware.parse_gpu_max_clock(self.CLOCK_DUMP), 3090)

    def test_a_dump_without_a_max_section(self):
        self.assertIsNone(hardware.parse_gpu_max_clock(
            "    Clocks\n        Graphics : 555 MHz\n"))
        self.assertIsNone(hardware.parse_gpu_max_clock(""))

    def test_an_unparseable_max_clock(self):
        self.assertIsNone(hardware.parse_gpu_max_clock(
            "    Max Clocks\n        Graphics                : N/A\n"))

    def test_the_defaults_are_a_fresh_dict_each_time(self):
        # Handed straight to the UI, which keeps it: one shared dict would
        # let one page's edit reach every other caller.
        first = hardware.default_gpu_limits()
        first["max_w"] = 1
        self.assertEqual(hardware.default_gpu_limits()["max_w"],
                         hardware.GPU_MAX_W_FALLBACK)


class TestGpuClockLimitArg(unittest.TestCase):
    """The top of the slider unlocks; it does not lock at the maximum.

    Locking at the stock maximum still *pins* the clock, so the card stops
    idling down and stops boosting -- the opposite of the "no limit" the
    position means."""

    def test_the_top_of_the_range_resets(self):
        self.assertEqual(hardware.gpu_clock_limit_arg(3090, 3090), "reset")

    def test_above_the_top_still_resets(self):
        # A saved ceiling from a bigger card, loaded on a smaller one.
        self.assertEqual(hardware.gpu_clock_limit_arg(4000, 3090), "reset")

    def test_anything_below_is_a_real_cap(self):
        self.assertEqual(hardware.gpu_clock_limit_arg(3075, 3090), 3075)
        self.assertEqual(hardware.gpu_clock_limit_arg(200, 3090), 200)

    def test_the_value_is_an_int_the_helper_will_take(self):
        # The slider hands over a float; the helper validates with a shell
        # integer test and would reject "1500.0".
        self.assertEqual(hardware.gpu_clock_limit_arg(1500.0, 3090), 1500)


class TestGpuClockLimitMax(unittest.TestCase):
    """The detected ceiling the headless scripts compare against.

    They used to compare a stored clock_limit against a hardcoded 3090 --
    this laptop's card. On any other card that is either a lock just below
    maximum where the user asked for no ceiling at all, or a ceiling that
    can never be reached."""

    def setUp(self):
        # Module-level cache: leave it as it was found, in either direction.
        self.saved = hardware._gpu_clock_limit_max
        hardware._gpu_clock_limit_max = None
        self.addCleanup(setattr, hardware, "_gpu_clock_limit_max", self.saved)

    def test_it_reports_what_the_card_says(self):
        with mock.patch.object(hardware, "detect_gpu_limits",
                               return_value={"clock_limit_max": 2100}) as det:
            self.assertEqual(hardware.gpu_clock_limit_max(), 2100)
            self.assertEqual(det.call_count, 1)

    def test_it_is_detected_once_per_process(self):
        # The enforcer applies profiles for the life of the session; two
        # nvidia-smi calls per apply would be paid forever.
        with mock.patch.object(hardware, "detect_gpu_limits",
                               return_value={"clock_limit_max": 2100}) as det:
            for _ in range(5):
                hardware.gpu_clock_limit_max()
            self.assertEqual(det.call_count, 1)

    def test_a_machine_with_no_card_caches_the_fallback_too(self):
        # Otherwise every apply pays for two failed execs.
        with mock.patch.object(
                hardware, "detect_gpu_limits",
                side_effect=lambda *a, **k: hardware.default_gpu_limits()) as det:
            self.assertEqual(hardware.gpu_clock_limit_max(),
                             hardware.CLOCK_LIMIT_FALLBACK_MAX)
            hardware.gpu_clock_limit_max()
            self.assertEqual(det.call_count, 1)

    def test_it_feeds_gpu_clock_limit_arg(self):
        # The pair is the point: a ceiling at or above the card's own
        # maximum unlocks, anything below is a real cap.
        with mock.patch.object(hardware, "detect_gpu_limits",
                               return_value={"clock_limit_max": 2100}):
            top = hardware.gpu_clock_limit_max()
            self.assertEqual(hardware.gpu_clock_limit_arg(2100, top), "reset")
            self.assertEqual(hardware.gpu_clock_limit_arg(3090, top), "reset")
            self.assertEqual(hardware.gpu_clock_limit_arg(1800, top), 1800)


class TestNvidiaSettingsArgs(unittest.TestCase):
    """The two clock offsets. The attribute names are the whole test: a
    typo in either is silently accepted by nvidia-settings and does
    nothing."""

    def test_core_offset(self):
        self.assertEqual(
            hardware.nvidia_settings_args("core", 150),
            ["nvidia-settings", "-a",
             "[gpu:0]/GPUGraphicsClockOffsetAllPerformanceLevels=150"])

    def test_memory_offset(self):
        self.assertEqual(
            hardware.nvidia_settings_args("memory", -200),
            ["nvidia-settings", "-a",
             "[gpu:0]/GPUMemoryTransferRateOffsetAllPerformanceLevels=-200"])

    def test_floats_are_written_as_integers(self):
        self.assertTrue(
            hardware.nvidia_settings_args("core", 25.0)[2].endswith("=25"))


class TestSupergfxParsing(unittest.TestCase):
    def test_a_list_of_modes(self):
        self.assertEqual(
            hardware.parse_supergfx_modes("[Integrated, Hybrid]"),
            ["Integrated", "Hybrid"])

    def test_the_single_mode_this_machine_reports(self):
        self.assertEqual(hardware.parse_supergfx_modes("[AsusMuxDgpu]\n"),
                         ["AsusMuxDgpu"])

    def test_nothing_at_all(self):
        for text in ("", "   \n", "[]", None):
            self.assertEqual(hardware.parse_supergfx_modes(text), [], text)


class TestGpuModeChoices(unittest.TestCase):
    """What the picker offers, which is deliberately not what -s reports.

    The regression these guard: the picker was once built from
    ``supergfxctl -s``, and on a laptop whose hardware MUX is wired to the
    discrete GPU that command reports the one mode already in force. The
    picker held a single entry and could switch nothing."""

    def test_all_three_are_offered_by_default(self):
        self.assertEqual(hardware.gpu_mode_choices(),
                         ["Integrated", "Hybrid", "AsusMuxDgpu"])

    def test_the_offered_list_is_not_filtered_by_what_s_reports(self):
        # The exact state of the machine this was written on: -s says one
        # mode, and all three must still be on offer.
        self.assertEqual(
            hardware.gpu_mode_choices("AsusMuxDgpu", ["AsusMuxDgpu"]),
            ["Integrated", "Hybrid", "AsusMuxDgpu"])

    def test_a_short_supported_list_never_shrinks_the_offer(self):
        for supported in ([], ["Integrated"], ["Integrated", "Hybrid"],
                          ["AsusMuxDgpu"], None):
            offered = hardware.gpu_mode_choices(supported=supported)
            for mode in hardware.GPU_MODES:
                self.assertIn(mode, offered, supported)

    def test_supported_modes_outside_the_three_are_added_not_dropped(self):
        self.assertEqual(
            hardware.gpu_mode_choices(supported=["Integrated", "Vfio"]),
            ["Integrated", "Hybrid", "AsusMuxDgpu", "Vfio"])

    def test_the_active_mode_is_always_selectable(self):
        # Otherwise the picker would show some other mode as current, which
        # reads as a mode change that never happened.
        self.assertIn("AsusEgpu", hardware.gpu_mode_choices("AsusEgpu"))

    def test_no_mode_is_listed_twice(self):
        offered = hardware.gpu_mode_choices(
            "Vfio", ["Hybrid", "Vfio", "Integrated", "Vfio"])
        self.assertEqual(len(offered), len(set(offered)))
        self.assertEqual(offered,
                         ["Integrated", "Hybrid", "AsusMuxDgpu", "Vfio"])

    def test_the_three_keep_their_order_ahead_of_any_extras(self):
        offered = hardware.gpu_mode_choices("Vfio", ["Vfio", "AsusEgpu"])
        self.assertEqual(offered[:3], list(hardware.GPU_MODES))

    def test_nothing_known_at_all(self):
        self.assertEqual(hardware.gpu_mode_choices(None, ()),
                         list(hardware.GPU_MODES))


class TestBusctlParsing(unittest.TestCase):
    def test_the_power_mode_out_of_a_property_reply(self):
        self.assertEqual(hardware.parse_busctl_string('s "balanced"\n'),
                         "balanced")

    def test_a_reply_that_is_not_a_string_property(self):
        for text in ("", "b true", "s", 'u 3', None):
            self.assertIsNone(hardware.parse_busctl_string(text), text)


class TestSetPowerMode(unittest.TestCase):
    """The profile -> OS power mode decision.

    Only the decision is tested: whether a mode change is attempted at all,
    and which mode is asked for. The busctl call itself is one subprocess
    line, and a test that asserted its argv would only restate it -- while
    the thing that actually broke was a profile switch that asked for no
    mode whatsoever, and an enforcer that undid the switch a minute later
    because of it."""

    def setUp(self):
        self.asked = []
        real = hardware.set_power_mode

        def record(mode, timeout=5):
            self.asked.append(mode)
            return True, mode

        hardware.set_power_mode = record
        self.addCleanup(setattr, hardware, "set_power_mode", real)

    def test_each_stock_profile_asks_for_its_own_mode(self):
        for name, mode in (("Quiet", "power-saver"),
                           ("Balanced Power", "balanced"),
                           ("Balanced Performance", "balanced"),
                           ("Performance", "performance")):
            self.assertEqual(hardware.set_power_mode_for_profile(name),
                             (True, mode), name)
        self.assertEqual(self.asked, ["power-saver", "balanced", "balanced",
                                      "performance"])

    def test_a_profile_of_the_users_own_changes_no_mode(self):
        # None, not (False, ...): there is nothing wrong here. PPD has three
        # modes and this profile is not one of them, so the OS mode is left
        # exactly where the user put it.
        self.assertIsNone(hardware.set_power_mode_for_profile("My Profile"))
        self.assertEqual(self.asked, [])

    def test_no_profile_at_all_changes_no_mode(self):
        self.assertIsNone(hardware.set_power_mode_for_profile(None))
        self.assertEqual(self.asked, [])

    def test_only_modes_ppd_actually_has_are_ever_asked_for(self):
        # A mode outside this set is not a wrong setting, it is a failed
        # set-property -- the profile switch would silently leave the OS
        # behind, which is the bug this whole path exists to close.
        from rogcontrol.profiles import PROFILE_TO_PPD_MODE
        self.assertEqual(set(PROFILE_TO_PPD_MODE.values()),
                         {"power-saver", "balanced", "performance"})


class TestPpdServiceName(unittest.TestCase):
    def test_both_bus_names_the_daemon_has_shipped_under_are_tried(self):
        tried = []

        def fake_run(argv, **_kwargs):
            tried.append(argv[3])
            raise OSError("no busctl here")

        real = hardware.subprocess.run
        hardware.subprocess.run = fake_run
        self.addCleanup(setattr, hardware.subprocess, "run", real)
        self.assertIsNone(hardware.ppd_service_name())
        self.assertEqual(tried, list(hardware.PPD_BUS_NAMES))

    def test_no_daemon_means_no_mode_change_rather_than_a_crash(self):
        def fake_name(timeout=5):
            return None

        real = hardware.ppd_service_name
        hardware.ppd_service_name = fake_name
        self.addCleanup(setattr, hardware, "ppd_service_name", real)
        ok, message = hardware.set_power_mode("balanced")
        self.assertFalse(ok)
        self.assertIn("power-profiles-daemon", message)


class TestLogTail(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "rogcontrol.log")
        self.addCleanup(self._tmp.cleanup)

    def test_the_last_lines_in_order(self):
        write(self.path, "\n".join(f"line {i}" for i in range(10)) + "\n")
        self.assertEqual(hardware.read_log_tail(3, path=self.path),
                         "line 7\nline 8\nline 9")

    def test_a_short_log_comes_back_whole(self):
        write(self.path, "only line\n")
        self.assertEqual(hardware.read_log_tail(100, path=self.path),
                         "only line")

    def test_a_missing_log_is_none_rather_than_empty(self):
        # "no log yet" and "the log is empty" are different things to put on
        # screen, so they must not collapse into the same value here.
        self.assertIsNone(hardware.read_log_tail(path=self.path))

    def test_an_empty_log_is_empty_rather_than_none(self):
        write(self.path, "")
        self.assertEqual(hardware.read_log_tail(path=self.path), "")

    def test_a_byte_window_never_starts_mid_line(self):
        # Seeking into the middle of a file lands inside a line; a half line
        # at the top of the view reads as a corrupt log.
        write(self.path, "".join(f"line {i}\n" for i in range(1000)))
        text = hardware.read_log_tail(5, path=self.path, max_bytes=40)
        for line in text.splitlines():
            self.assertRegex(line, r"^line \d+$")

    def test_the_log_lives_where_every_other_tool_writes_it(self):
        self.assertEqual(
            hardware.LOG_PATH,
            os.path.expanduser("~/.local/share/rogcontrol/rogcontrol.log"))


class TestFirmwareGpuDefaults(FakeSysfs):
    """The asus-wmi knobs, read back as the starting value for a profile
    that has never stored one."""

    def wmi(self, name, value):
        write(os.path.join(self.root, hardware.ASUS_WMI_DIR.lstrip("/"), name),
              f"{value}\n")

    def test_reads_what_the_firmware_holds(self):
        self.wmi("nv_dynamic_boost", 15)
        self.wmi("nv_temp_target", 80)
        self.assertEqual(hardware.read_nv_dynamic_boost(root=self.root), 15)
        self.assertEqual(hardware.read_nv_temp_target(root=self.root), 80)

    def test_a_value_outside_the_drivers_range_is_clamped(self):
        # The kernel rejects a write outside NVIDIA_BOOST_MIN/MAX, so a
        # firmware reporting 99 must not become a slider position that can
        # only ever fail.
        self.wmi("nv_dynamic_boost", 99)
        self.wmi("nv_temp_target", 10)
        self.assertEqual(hardware.read_nv_dynamic_boost(root=self.root),
                         hardware.DYN_BOOST_MAX)
        self.assertEqual(hardware.read_nv_temp_target(root=self.root),
                         hardware.TEMP_TARGET_MIN)

    def test_absent_nodes_read_as_none(self):
        self.assertIsNone(hardware.read_nv_dynamic_boost(root=self.root))
        self.assertIsNone(hardware.read_nv_temp_target(root=self.root))


class TestKeyboard(FakeSysfs):
    def test_reads_the_backlight_level_back_from_the_led_class(self):
        # Read back rather than trusted from the config: the level moves
        # under this app's feet via the keyboard's own Fn keys.
        self.kbd_backlight(2)
        self.assertEqual(hardware.read_kbd_brightness(root=self.root), 2)

    def test_no_led_class_is_none_rather_than_a_guess(self):
        self.assertIsNone(hardware.read_kbd_brightness(root=self.root))

    def test_finds_the_aura_controller_by_vendor(self):
        self.usb_device("1-1", "1d6b", "0002")     # a root hub
        self.usb_device("3-3", "0b05", "19b6")     # the N-KEY controller
        self.assertEqual(hardware.find_aura_keyboard(root=self.root), "19b6")

    def test_a_machine_with_no_asus_controller_is_none(self):
        self.usb_device("1-1", "1d6b", "0002")
        self.assertIsNone(hardware.find_aura_keyboard(root=self.root))

    def test_a_missing_usb_tree_is_none_rather_than_an_error(self):
        self.assertIsNone(hardware.find_aura_keyboard(root=self.root))

    def test_a_product_id_is_lowercased(self):
        # Some kernels print these uppercase, and the id sets are lower.
        self.usb_device("3-3", "0b05", "19B6")
        self.assertEqual(hardware.find_aura_keyboard(root=self.root), "19b6")

    def test_every_multi_zone_id_is_also_a_known_controller(self):
        self.assertTrue(hardware.AURA_MULTI_ZONE_IDS
                        <= hardware.AURA_SINGLE_ZONE_IDS)

    def test_zones_are_only_claimed_for_a_controller_known_to_have_them(self):
        # Sending multi_static to a single-zone keyboard lights zone 1 and
        # silently drops the rest, which reads as a broken app rather than an
        # unsupported feature.
        self.usb_device("3-3", "0b05", "1822")     # single zone only
        caps = hardware.detect_capabilities(root=self.root)
        self.assertEqual(caps["aura_id"], "1822")
        self.assertFalse(caps["kbd_rgb_zones"])

    def test_a_four_zone_controller_gets_the_zone_modes(self):
        self.usb_device("3-3", "0b05", "19b6")
        caps = hardware.detect_capabilities(root=self.root)
        self.assertTrue(caps["kbd_rgb_zones"])

    def test_battery_colour_needs_a_battery(self):
        self.assertFalse(
            hardware.detect_capabilities(root=self.root)["kbd_battery"])
        self.battery(capacity=55, status="Discharging")
        self.assertTrue(
            hardware.detect_capabilities(root=self.root)["kbd_battery"])

    def test_ambient_is_left_for_the_app_to_answer(self):
        # Answering it needs GStreamer and a session bus, which this module
        # is deliberately free of -- the app fills it in.
        self.assertNotIn("kbd_ambient",
                         hardware.detect_capabilities(root=self.root))


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
