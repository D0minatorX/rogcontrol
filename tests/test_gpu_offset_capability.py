"""Detect writable NVIDIA clock offsets without leaving a probe value set."""

import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from rogcontrol import feature_report, hardware


ATTRIBUTE = "GPUGraphicsClockOffsetAllPerformanceLevels"


def answer(value):
    return subprocess.CompletedProcess(
        ["nvidia-settings"], 0,
        stdout=(f"  Attribute '{ATTRIBUTE}' (host:0[gpu:0]): {value}.\n"
                "    The valid values for 'GPUGraphicsClockOffsetAllPerformanceLevels' "
                "are in the range -1000 - 1000 (inclusive).\n"),
        stderr="")


class OffsetCapabilityTests(unittest.TestCase):
    @mock.patch.object(hardware, "nvidia_settings_gpu_target", return_value="[gpu:0]")
    @mock.patch.object(hardware, "_nvidia_settings_session", return_value=({"DISPLAY": ":0"}, None))
    @mock.patch.object(hardware, "have_cmd", return_value=True)
    def test_changed_value_is_read_back_and_original_restored(self, _cmd, _session, _target):
        replies = [answer(0), subprocess.CompletedProcess([], 0, "", ""),
                   answer(25), subprocess.CompletedProcess([], 0, "", ""), answer(0)]
        with mock.patch.object(hardware.subprocess, "run", side_effect=replies) as run:
            self.assertTrue(hardware.probe_nvidia_clock_offset("core"))
        writes = [call.args[0] for call in run.call_args_list
                  if call.args[0][1] == "-a"]
        self.assertEqual(writes, [
            ["nvidia-settings", "-a", "[gpu:0]/" + ATTRIBUTE + "=25"],
            ["nvidia-settings", "-a", "[gpu:0]/" + ATTRIBUTE + "=0"],
        ])

    @mock.patch.object(hardware, "nvidia_settings_gpu_target", return_value="[gpu:0]")
    @mock.patch.object(hardware, "_nvidia_settings_session", return_value=({"DISPLAY": ":0"}, None))
    @mock.patch.object(hardware, "have_cmd", return_value=True)
    def test_attribute_absent_hides_offset_without_a_write(self, _cmd, _session, _target):
        unsupported = subprocess.CompletedProcess([], 1, "", "Attribute not found")
        with mock.patch.object(hardware.subprocess, "run", return_value=unsupported) as run:
            self.assertFalse(hardware.probe_nvidia_clock_offset("core"))
            self.assertEqual(run.call_count, 1)

    @mock.patch.object(hardware, "nvidia_settings_gpu_target", return_value="[gpu:0]")
    @mock.patch.object(hardware, "_nvidia_settings_session", return_value=({"DISPLAY": ":0"}, None))
    @mock.patch.object(hardware, "have_cmd", return_value=True)
    def test_false_success_is_hidden_and_original_restored(self, _cmd, _session, _target):
        replies = [answer(0), subprocess.CompletedProcess([], 0, "", ""),
                   answer(0), subprocess.CompletedProcess([], 0, "", ""), answer(0)]
        with mock.patch.object(hardware.subprocess, "run", side_effect=replies) as run:
            self.assertFalse(hardware.probe_nvidia_clock_offset("core"))
            self.assertEqual(run.call_count, 5)

    @mock.patch.object(hardware, "nvidia_settings_gpu_target", return_value="[gpu:2]")
    @mock.patch.object(hardware, "_nvidia_settings_session", return_value=({"DISPLAY": ":0"}, None))
    @mock.patch.object(hardware, "have_cmd", return_value=True)
    def test_memory_attribute_is_independent_and_targets_selected_gpu(self, _cmd, _session, _target):
        memory = "GPUMemoryTransferRateOffsetAllPerformanceLevels"
        def reply(value):
            return subprocess.CompletedProcess(
                [], 0, f"Attribute '{memory}' (host:0[gpu:2]): {value}.\n"
                f"The valid values for '{memory}' are in the range -2000 - 6000 (inclusive).", "")
        replies = [reply(0), subprocess.CompletedProcess([], 0, "", ""),
                   reply(25), subprocess.CompletedProcess([], 0, "", ""), reply(0)]
        with mock.patch.object(hardware.subprocess, "run", side_effect=replies) as run:
            self.assertTrue(hardware.probe_nvidia_clock_offset("memory"))
        self.assertEqual(run.call_args_list[1].args[0],
                         ["nvidia-settings", "-a", f"[gpu:2]/{memory}=25"])


class ClockCeilingCapabilityTests(unittest.TestCase):
    @mock.patch.object(hardware, "nvidia_access_error", return_value=None)
    @mock.patch.object(hardware, "detect_gpu_max_clock", return_value=2100)
    @mock.patch.object(hardware, "have_cmd", return_value=True)
    def test_write_and_reset_are_both_required(self, _cmd, _max, _access):
        with mock.patch.object(hardware, "run_helper",
                               side_effect=[(True, ""), (True, "")]) as helper:
            self.assertTrue(hardware.gpu_clock_limit_supported())
        self.assertEqual(helper.call_args_list,
                         [mock.call("gpuclocklimit", 2085),
                          mock.call("gpuclocklimit", "reset")])

    @mock.patch.object(hardware, "nvidia_access_error", return_value=None)
    @mock.patch.object(hardware, "detect_gpu_max_clock", return_value=2100)
    @mock.patch.object(hardware, "have_cmd", return_value=True)
    def test_failed_write_still_resets_and_hides(self, _cmd, _max, _access):
        with mock.patch.object(hardware, "run_helper",
                               side_effect=[(False, "refused"), (True, "")]) as helper:
            self.assertFalse(hardware.gpu_clock_limit_supported())
        self.assertEqual(helper.call_count, 2)

    @mock.patch.object(hardware, "nvidia_access_error", return_value="blocked")
    @mock.patch.object(hardware, "have_cmd", return_value=True)
    def test_cardwire_blocked_does_not_write(self, _cmd, _access):
        with mock.patch.object(hardware, "run_helper") as helper:
            self.assertFalse(hardware.gpu_clock_limit_supported())
            helper.assert_not_called()


class CapabilityAggregationTests(unittest.TestCase):
    def test_each_control_reports_its_own_result(self):
        with (mock.patch.object(hardware, "gpu_clock_limit_supported", return_value=True),
              mock.patch.object(hardware, "probe_nvidia_clock_offset",
                                side_effect=[True, False])):
            self.assertEqual(hardware.probe_gpu_tuning_capabilities(
                {"nvidia": True, "nvidia_settings": True}), {
                "gpu_clock_limit": True,
                "nvidia_core_clock_offset": True,
                "nvidia_memory_clock_offset": False,
            })

    def test_installer_reports_each_verified_clock_control(self):
        verified = {"gpu_clock_limit": True,
                    "nvidia_core_clock_offset": False,
                    "nvidia_memory_clock_offset": True}
        with (mock.patch.object(feature_report.hardware, "detect_capabilities",
                                return_value={"nvidia": True,
                                              "nvidia_settings": True,
                                              "cardwire": False}),
              mock.patch.object(feature_report.hardware,
                                "probe_gpu_tuning_capabilities",
                                return_value=verified),
              mock.patch.object(feature_report.hardware,
                                "detect_nvidia_powermizer_modes", return_value=()),
              mock.patch.object(feature_report.hardware,
                                "probe_nvidia_voltage_boost", return_value=None),
              mock.patch.object(feature_report.graphics_backend,
                                "selected_backend", return_value="cardwire")):
            rows = feature_report.detect_feature_rows()
        reported = {key: value for key, _label, value in rows}
        for key, value in verified.items():
            self.assertEqual(reported[key], value)


class ClockControlVisibilityTests(unittest.TestCase):
    def test_each_clock_row_follows_its_own_capability(self):
        from rogcontrol.ui import Adw
        from rogcontrol.pages.gpu import GpuPage
        Adw.init()
        caps = {"nvidia": True, "nvidia_settings": True,
                "gpu_clock_limit": True,
                "nvidia_core_clock_offset": True,
                "nvidia_memory_clock_offset": False}
        window = SimpleNamespace(caps=caps, current_profile=lambda: {"gpu": {}},
                                 apply_async=mock.Mock())
        page = GpuPage(window)
        self.addCleanup(page._on_destroy, None)
        self.assertTrue(page.rows["clock_limit"].get_visible())
        self.assertTrue(page.rows["clock_offset"].get_visible())
        self.assertFalse(page.rows["mem_clock_offset"].get_visible())
        caps.update({"gpu_clock_limit": False,
                     "nvidia_core_clock_offset": False,
                     "nvidia_memory_clock_offset": True})
        page._apply_capability_gating()
        self.assertFalse(page.rows["clock_limit"].get_visible())
        self.assertFalse(page.rows["clock_offset"].get_visible())
        self.assertTrue(page.rows["mem_clock_offset"].get_visible())


if __name__ == "__main__":
    unittest.main()
