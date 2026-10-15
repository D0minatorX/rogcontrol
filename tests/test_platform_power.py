"""PPD-free profile writes use an isolated sysfs tree, never live hardware."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rogcontrol import hardware
from test_enforcer_efficiency import load_enforcer


class PlatformHelperTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.platform = self.node('firmware/acpi/platform_profile', 'balanced')
        self.choices = self.node('firmware/acpi/platform_profile_choices', 'quiet balanced performance')
        self.helper = self.root / 'helper'
        self.helper.write_text(Path('rogcontrol/rogcontrol-helper').read_text().replace(
            '/sys/', str(self.root / 'sys') + '/'))
        busctl = self.root / 'busctl'
        busctl.write_text('#!/bin/bash\ncase "$*" in *introspect*) exit "${TEST_PPD_PRESENT:-1}";; esac\nexit "${TEST_PPD_WRITE:-0}"\n')
        busctl.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root) + ':' + os.environ['PATH'])

    def node(self, path, value):
        node = self.root / 'sys' / path
        node.parent.mkdir(parents=True, exist_ok=True)
        node.write_text(value)
        return node

    def policy(self, index=0, driver='amd-pstate-epp', governors='powersave performance'):
        base = f'devices/system/cpu/cpufreq/policy{index}/'
        self.node(base + 'scaling_driver', driver)
        self.node(base + 'scaling_available_governors', governors)
        return self.node(base + 'scaling_governor', 'performance')

    def run_helper(self, mode, *extra, **env):
        return subprocess.run(['bash', str(self.helper), 'powerprofile', mode, *extra],
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=5)

    def test_absent_ppd_maps_and_verifies_all_platform_modes(self):
        for mode, expected in [('power-saver', 'quiet'), ('balanced', 'balanced'),
                               ('performance', 'performance')]:
            with self.subTest(mode=mode):
                result = self.run_helper(mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.platform.read_text().strip(), expected)

    def test_low_power_fallback_only_when_quiet_is_unavailable(self):
        self.choices.write_text('low-power balanced performance')
        result = self.run_helper('power-saver')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.platform.read_text().strip(), 'low-power')

    def test_unsupported_profile_changes_neither_platform_nor_governor(self):
        governor = self.policy()
        self.choices.write_text('balanced performance')
        result = self.run_helper('power-saver')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unsupported', result.stderr.lower())
        self.assertEqual(self.platform.read_text(), 'balanced')
        self.assertEqual(governor.read_text(), 'performance')

    def test_active_pstate_uses_powersave_on_every_policy_for_epp(self):
        governors = [self.policy(0), self.policy(1, 'intel_pstate')]
        for mode in ('power-saver', 'balanced', 'performance'):
            for governor in governors:
                governor.write_text('performance')
            result = self.run_helper(mode)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([p.read_text().strip() for p in governors], ['powersave', 'powersave'])

    def test_legacy_dynamic_governors_and_unknown_driver_preservation(self):
        governors = [self.policy(0, 'acpi-cpufreq', 'performance powersave schedutil'),
                     self.policy(1, 'intel_cpufreq', 'performance ondemand'),
                     self.policy(2, 'unknown', 'performance powersave')]
        result = self.run_helper('balanced')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p.read_text().strip() for p in governors],
                         ['schedutil', 'ondemand', 'performance'])

    def test_ppd_success_or_rejection_never_writes_sysfs(self):
        governor = self.policy()
        for code in ('0', '1'):
            result = self.run_helper('performance', TEST_PPD_PRESENT='0', TEST_PPD_WRITE=code)
            self.assertEqual(result.returncode, int(code))
            self.assertEqual(self.platform.read_text(), 'balanced')
            self.assertEqual(governor.read_text(), 'performance')

    def test_invalid_mode_and_extra_arguments_do_not_write(self):
        for args in [('invalid',), ('performance', 'extra')]:
            result = self.run_helper(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.platform.read_text(), 'balanced')

    def test_write_failure_is_reported_not_success(self):
        self.platform.unlink()
        self.platform.mkdir()
        result = self.run_helper('performance')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('platform', result.stderr.lower())

    def test_governor_failure_is_reported(self):
        governor = self.policy()
        governor.unlink()
        governor.mkdir()
        result = self.run_helper('performance')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('governor', result.stderr.lower())

    def test_missing_choices_or_platform_never_creates_a_control(self):
        self.choices.unlink()
        self.assertNotEqual(self.run_helper('performance').returncode, 0)
        self.assertEqual(self.platform.read_text(), 'balanced')
        self.choices.write_text('balanced performance')
        self.platform.unlink()
        self.assertNotEqual(self.run_helper('performance').returncode, 0)
        self.assertFalse(self.platform.exists())

    def test_no_redundant_platform_or_governor_writes(self):
        governor = self.policy()
        governor.write_text('powersave')
        before = [p.stat().st_mtime_ns for p in (self.platform, governor)]
        result = self.run_helper('balanced')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p.stat().st_mtime_ns for p in (self.platform, governor)], before)

    def test_unadvertised_governor_is_not_written(self):
        governor = self.policy(governors='performance')
        result = self.run_helper('balanced')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('governor', result.stderr)
        self.assertEqual(governor.read_text(), 'performance')

    def test_wrong_platform_readback_is_failure(self):
        cat = self.root / 'cat'
        cat.write_text('#!/bin/bash\ncase "$1" in */platform_profile) echo balanced;; *) exec /usr/bin/cat "$@";; esac\n')
        cat.chmod(0o755)
        result = self.run_helper('performance')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('verification failed', result.stderr)

    def test_wrong_governor_readback_is_failure(self):
        self.policy()
        cat = self.root / 'cat'
        cat.write_text('#!/bin/bash\ncase "$1" in */scaling_governor) echo performance;; *) exec /usr/bin/cat "$@";; esac\n')
        cat.chmod(0o755)
        result = self.run_helper('balanced')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('governor update failed', result.stderr)

    def test_ui_apply_sets_governor_before_saved_epp_without_ppd(self):
        from rogcontrol.qt.controllers.tuning import apply_saved_profile
        governor = self.policy()
        base = 'devices/system/cpu/cpufreq/policy0/'
        self.node(base + 'energy_performance_available_preferences', 'performance balance_power power')
        epp = self.node(base + 'energy_performance_preference', 'performance')
        cfg = {'current_profile': 'Quiet', 'profiles': {'Quiet': {'cpu': {'epp': 'balance_power'}}}}
        def helper(*args, **kwargs):
            result = subprocess.run(['bash', str(self.helper), *map(str, args)], env=self.env,
                                    capture_output=True, text=True, timeout=5)
            return result.returncode == 0, result.stderr or result.stdout
        with patch.object(hardware, 'ppd_service_name', return_value=None), \
             patch.object(hardware, 'set_profile_kbd_color', return_value=None), \
             patch.object(hardware, 'run_helper', side_effect=helper):
            self.assertEqual(apply_saved_profile(cfg, {'cpu_epp': True}), [])
        self.assertEqual(self.platform.read_text().strip(), 'quiet')
        self.assertEqual(governor.read_text().strip(), 'powersave')
        self.assertEqual(epp.read_text().strip(), 'balance_power')


class PlatformRoutingTests(unittest.TestCase):
    def test_boot_apply_logs_platform_failure(self):
        spec = importlib.util.spec_from_file_location('platform_boot_apply', 'rogcontrol/rogcontrol-apply.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(hardware, 'set_power_mode_for_profile', return_value=(False, 'unsupported platform profile')), \
             patch.object(hardware, 'set_profile_kbd_color'), patch.object(hardware, 'log') as log:
            module.apply_once({'current_profile': 'Quiet', 'profiles': {}}, profile_only=True)
            self.assertTrue(any('unsupported platform profile' in c.args[0] for c in log.call_args_list))

    def test_ac_switch_without_ppd_sets_mode_before_applying_hardware(self):
        enforcer = load_enforcer()
        cfg = {'current_profile': 'Performance', 'battery_profile': 'Quiet',
               'profiles': {'Performance': {}, 'Quiet': {}}}
        events = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(cfg))
            enforcer._last_ac_state = True
            enforcer._last_charger_kind = None
            with patch.object(enforcer, 'CONFIG_PATH', str(path)), \
                 patch.object(hardware, 'read_power_source', return_value=(False, None)), \
                 patch.object(enforcer, 'store_last_ac_state'), patch.object(enforcer, 'charger_flash'), \
                 patch.object(enforcer, 'save_config'), patch.object(enforcer, 'notify'), \
                 patch.object(enforcer, 'log'), \
                 patch.object(hardware, 'ppd_service_name', return_value=None), \
                 patch.object(hardware, 'run_helper', side_effect=lambda *a, **kw: (events.append(a) or True, '')), \
                 patch.object(enforcer, 'apply_full_profile', side_effect=lambda *a, **kw: events.append('apply')):
                self.assertTrue(enforcer._check_ac_auto_switch(cfg, None, 'test'))
            self.assertEqual(events, [('powerprofile', 'power-saver'), 'apply'])

    def test_missing_ppd_reaches_helper_and_preserves_failure(self):
        for outcome in ((True, 'platform profile: quiet'), (False, 'unsupported profile')):
            with patch.object(hardware, 'ppd_service_name', return_value=None), \
                 patch.object(hardware, 'run_helper', return_value=outcome) as helper:
                self.assertEqual(hardware.set_power_mode_for_profile('Quiet'), outcome)
                helper.assert_called_once_with('powerprofile', 'power-saver', timeout=15)

    def test_reachable_ppd_failure_does_not_bypass_daemon(self):
        from types import SimpleNamespace
        with patch.object(hardware.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stderr='D-Bus error', stdout='')), \
             patch.object(hardware, 'run_helper') as helper:
            self.assertFalse(hardware.set_power_mode('balanced', service_name='net.hadess.PowerProfiles')[0])
            helper.assert_not_called()

    def test_background_platform_failure_is_reported_without_retry_loop(self):
        enforcer = load_enforcer()
        with patch.object(hardware, 'ppd_service_name', return_value=None), \
             patch.object(hardware, 'run_helper', return_value=(False, 'platform profile verification failed')) as helper, \
             patch.object(enforcer, 'log') as log:
            self.assertFalse(enforcer.set_profile_power_mode('performance'))
            helper.assert_called_once()
            self.assertIn('platform profile verification failed', log.call_args.args[0])
            self.assertEqual(log.call_args.args[1], 'WARN')

    def test_enforcer_profile_writer_supports_missing_ppd_but_ppd_sync_does_not(self):
        enforcer = load_enforcer()
        writer = getattr(enforcer, 'set_profile_power_mode', None)
        self.assertTrue(callable(writer), 'Automatic profile switches need a PPD-optional writer')
        with patch.object(hardware, 'set_power_mode', return_value=(True, '')) as setter:
            self.assertTrue(writer('balanced'))
            setter.assert_called_once_with('balanced', service_name=None)
            setter.reset_mock()
            self.assertFalse(enforcer.set_ppd_active_profile(None, 'balanced'))
            setter.assert_not_called()
