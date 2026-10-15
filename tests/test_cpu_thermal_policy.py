"""Saved ASUS thermal policy follows a profile without fighting OS mode."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rogcontrol import hardware, profiles


class ThermalPolicyTests(unittest.TestCase):
    def test_explicit_policy_selects_matching_os_mode(self):
        self.assertEqual(profiles.expected_ppd_mode('Balanced Power', {'cpu': {'thermal_policy': 1}}), 'performance')
        self.assertEqual(profiles.expected_ppd_mode('Performance', {'cpu': {'thermal_policy': 2}}), 'power-saver')
        self.assertEqual(profiles.expected_ppd_mode('Custom', {'cpu': {'thermal_policy': 0}}), 'balanced')
        self.assertEqual(profiles.expected_ppd_mode('Quiet', {'cpu': {'thermal_policy': 'follow'}}), 'power-saver')
        self.assertEqual(profiles.expected_ppd_mode('Custom', {'cpu': {}}), None)

    def test_cpu_plan_writes_only_explicit_supported_policy(self):
        self.assertEqual(hardware.cpu_apply_plan({'thermal_policy': 1}, {'throttle_policy': True}),
                         [('thermal_policy', ('cpupolicy', 1))])
        self.assertEqual(hardware.cpu_apply_plan({'thermal_policy': 'follow'}, {'throttle_policy': True}), [])
        self.assertEqual(hardware.cpu_apply_plan({'thermal_policy': 1}, {}), [])

    def test_profile_mode_writer_uses_saved_policy(self):
        with patch.object(hardware, 'set_power_mode', return_value=(True, '')) as setter:
            hardware.set_power_mode_for_profile('Balanced Power', profile={'cpu': {'thermal_policy': 2}})
            setter.assert_called_once_with('power-saver', timeout=5)

    def test_cpu_page_saves_explicit_policy_and_restores_profile(self):
        from PySide6.QtCore import QCoreApplication
        from rogcontrol.qt.controllers.cpu import CpuController
        from test_qt_controllers import App
        QCoreApplication.instance() or QCoreApplication([])
        controller = CpuController(App())
        self.addCleanup(controller.close)
        caps = {'throttle_policy': True}
        controller.caps = caps
        rows = controller.build_rows(caps)
        self.assertEqual(next(row for row in rows if row['key'] == 'thermal_policy')['choices'],
                         ['follow', 0, 1, 2])
        with patch('rogcontrol.config.load_config', return_value={}), \
             patch.object(hardware, 'run_helper', return_value=(True, '')) as helper, \
             patch('rogcontrol.qt.controllers.tuning.apply_saved_profile', return_value=[]) as reapply:
            saved, failed = controller.write({'thermal_policy': 2}, {'thermal_policy': 'follow'}, caps)
            self.assertEqual((saved, failed), ({'thermal_policy': 2}, []))
            helper.assert_called_once_with('cpupolicy', 2)
            controller.after_write({'thermal_policy': 2}, saved, caps, {'current_profile': 'Balanced'})
            reapply.assert_called_once()

    def test_follow_choice_clears_previous_policy_without_firmware_write(self):
        from PySide6.QtCore import QCoreApplication
        from rogcontrol.qt.controllers.cpu import CpuController
        from test_qt_controllers import App
        QCoreApplication.instance() or QCoreApplication([])
        controller = CpuController(App())
        self.addCleanup(controller.close)
        caps = {'throttle_policy': True}
        with patch('rogcontrol.config.load_config', return_value={}), \
             patch.object(hardware, 'run_helper') as helper:
            saved, failed = controller.write({'thermal_policy': 'follow'}, {'thermal_policy': 1}, caps)
        self.assertEqual((saved, failed), ({'thermal_policy': 'follow'}, []))
        helper.assert_not_called()

    def test_firmware_policy_change_reapplies_fan_curve_despite_cached_readback(self):
        from rogcontrol.qt.controllers.tuning import apply_saved_profile
        cfg = {'current_profile': 'Quiet', 'profiles': {
            'Quiet': {'cpu': {}, 'gpu': {}, 'fans': {'1': [[50, 20], [90, 100]]}}}}
        with patch.object(hardware, 'set_power_mode_for_profile', return_value=(True, '')), \
             patch.object(hardware, 'set_profile_kbd_color', return_value=None), \
             patch.object(hardware, 'read_fan_boost', return_value=None), \
             patch.object(hardware, 'fan_boost_active', return_value=False), \
             patch.object(hardware, 'read_fan_curve_points', return_value=[[50, 20], [90, 100]]), \
             patch.object(hardware, 'read_fan_curve_enabled', return_value={'1': True}), \
             patch.object(hardware, 'FAN_CHANNELS', ('1',)), \
             patch('rogcontrol.fancurve.curve_matches_hardware', return_value=True), \
             patch.object(hardware, 'run_helper', return_value=(True, '')) as helper:
            apply_saved_profile(cfg, {'fan_curve': True}, force_fan_reapply=True)
            self.assertEqual(helper.call_args.args[:2], ('fan', '1'))

    def test_hotkey_profile_switch_applies_saved_policy_and_reapplies_curve(self):
        from rogcontrol import cli
        profile = {'cpu': {'thermal_policy': 1}, 'fans': {'1': [[50, 20], [90, 100]]}}
        with patch.object(hardware, 'cpu_is_amd', return_value=False), \
             patch.object(hardware, 'cpu_power_limits_backend', return_value=None), \
             patch.object(cli.os.path, 'exists', return_value=True), \
             patch.object(cli, 'run_helper') as helper, \
             patch.object(hardware, 'read_fan_boost', return_value=None), \
             patch.object(hardware, 'fan_boost_active', return_value=False), \
             patch.object(hardware, 'read_fan_curve_points', return_value=[[50, 20], [90, 100]]), \
             patch.object(hardware, 'read_fan_curve_enabled', return_value={'1': True}), \
             patch('rogcontrol.fancurve.curve_matches_hardware', return_value=True), \
             patch.object(hardware, 'run_fan_helper_logged', return_value=(True, '')) as fan:
            cli.apply_profile(profile)
            helper.assert_any_call('cpupolicy', 1)
            fan.assert_called_once()

    def test_login_retry_detects_policy_node_that_appeared_after_import(self):
        script = Path('rogcontrol/rogcontrol-apply.py')
        spec = importlib.util.spec_from_file_location('thermal_boot_apply', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ALL_CPU_CAPS['throttle_policy'] = False
        cfg = {'current_profile': 'Quiet', 'profiles': {
            'Quiet': {'cpu': {'thermal_policy': 2}, 'fans': {}}}}
        with patch.object(module.os.path, 'exists', return_value=True), \
             patch.object(hardware, 'set_power_mode_for_profile', return_value=(True, '')), \
             patch.object(hardware, 'set_profile_kbd_color', return_value=None), \
             patch.object(hardware, 'read_fan_boost', return_value=None), \
             patch.object(hardware, 'fan_boost_active', return_value=False), \
             patch.object(hardware, 'read_fan_curve_enabled', return_value={}), \
             patch.object(module, 'run_helper') as helper:
            module.apply_once(cfg, profile_only=True)
            helper.assert_any_call('cpupolicy', 2)

    def test_helper_accepts_only_documented_values_and_checks_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = root / 'sys/devices/platform/asus-nb-wmi/throttle_thermal_policy'
            node.parent.mkdir(parents=True)
            node.write_text('0\n')
            helper = root / 'helper'
            helper.write_text(Path('rogcontrol/rogcontrol-helper').read_text().replace('/sys/', str(root / 'sys') + '/'))
            for value in ('1', '2', '0'):
                result = subprocess.run(['bash', str(helper), 'cpupolicy', value], capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(node.read_text().strip(), value)
            before = node.read_text()
            for value in ('3', '-1', 'garbage'):
                result = subprocess.run(['bash', str(helper), 'cpupolicy', value], capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(node.read_text(), before)
