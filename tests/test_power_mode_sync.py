"""Power-mode reconciliation must preserve the app's selected profile."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_enforcer_efficiency import load_enforcer


class PowerModePermissionTests(unittest.TestCase):
    def test_access_denied_uses_validated_helper_fallback(self):
        from types import SimpleNamespace
        from rogcontrol import hardware
        with patch.object(hardware.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stderr='Access denied', stdout='')), \
             patch.object(hardware, 'run_helper', return_value=(True, '')) as helper:
            self.assertTrue(hardware.set_power_mode('power-saver', service_name='net.hadess.PowerProfiles')[0])
            helper.assert_called_once_with('powerprofile', 'power-saver', timeout=15)

    def test_invalid_mode_never_reaches_helper(self):
        from rogcontrol import hardware
        with patch.object(hardware, 'run_helper') as helper:
            self.assertFalse(hardware.set_power_mode('invalid')[0])
            helper.assert_not_called()


class PowerModeSyncTests(unittest.TestCase):
    def setUp(self):
        self.enforcer = load_enforcer()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'config.json'
        self.config = {
            'current_profile': 'Performance',
            'profiles': {'Performance': {'cpu': {'epp': 'performance'}},
                         'Quiet': {}, 'Balanced Power': {},
                         'Balanced Performance': {}},
        }
        self.path.write_text(json.dumps(self.config))
        self.patch('CONFIG_PATH', str(self.path))
        self.patch('log')
        self.sleep = self.patch('time.sleep')
        self.patch('time.monotonic', return_value=100)
        self.mode = self.patch('get_ppd_active_profile', return_value='power-saver')
        self.write = self.patch('subprocess.run')
        self.apply = self.patch('apply_full_profile')

    def patch(self, name, *args, **kwargs):
        owner = self.enforcer
        parts = name.split('.')
        for part in parts[:-1]:
            owner = getattr(owner, part)
        patcher = patch.object(owner, parts[-1], *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def sync(self, actual='power-saver', service='net.hadess.PowerProfiles'):
        return self.enforcer.sync_active_profile_power_mode(self.config, actual, service)

    def test_os_mismatch_restores_app_mode_without_changing_saved_profile(self):
        before = self.path.read_bytes()
        self.assertTrue(self.sync())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.config['current_profile'], 'Performance')
        self.assertEqual(self.write.call_args.args[0][-1], 'performance')
        self.sleep.assert_not_called()
        self.apply.assert_called_once_with(
            self.config, self.config['profiles']['Performance'],
            force_fan_reapply=True, full=True)

    def test_latest_app_selection_wins_over_stale_snapshot(self):
        fresh = dict(self.config, current_profile='Balanced Power')
        self.path.write_text(json.dumps(fresh))
        self.assertTrue(self.sync())
        self.assertEqual(json.loads(self.path.read_text()), fresh)
        self.assertEqual(self.write.call_args.args[0][-1], 'balanced')
        self.assertEqual(self.apply.call_args.args[0], fresh)

    def test_matching_balanced_mode_preserves_balanced_power(self):
        fresh = dict(self.config, current_profile='Balanced Power')
        self.path.write_text(json.dumps(fresh))
        self.mode.return_value = 'balanced'
        self.assertTrue(self.sync(actual='balanced'))
        self.assertEqual(json.loads(self.path.read_text()), fresh)
        self.write.assert_not_called()
        self.apply.assert_not_called()

    def test_custom_profile_or_missing_service_does_not_adopt_os_profile(self):
        for name, service in [('Custom', 'net.hadess.PowerProfiles'),
                              ('Performance', None)]:
            with self.subTest(name=name, service=service):
                fresh = dict(self.config, current_profile=name)
                self.path.write_text(json.dumps(fresh))
                self.assertFalse(self.sync(service=service))
                self.assertEqual(json.loads(self.path.read_text()), fresh)
                self.write.assert_not_called()
                self.apply.assert_not_called()

    def test_recent_write_does_not_delay_a_new_mismatch(self):
        self.enforcer._last_self_apply_time = 99
        self.assertTrue(self.sync())
        self.assertEqual(self.write.call_args.args[0][-1], 'performance')
        self.sleep.assert_not_called()

    def test_unsettled_mode_does_not_reapply(self):
        self.mode.return_value = 'balanced'
        self.assertFalse(self.sync())
        self.write.assert_not_called()
        self.apply.assert_not_called()

    def test_watcher_checks_again_after_two_seconds_without_dbus_monitor(self):
        self.patch('get_ppd_service_name', return_value='net.hadess.PowerProfiles')
        stop = self.patch('_automation_stop')
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        self.enforcer.ppd_watcher_thread()
        self.assertEqual([call.args for call in stop.wait.call_args_list], [(2,), (2,)])
        self.assertEqual(self.write.call_count, 2)
        self.sleep.assert_not_called()
