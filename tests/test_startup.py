"""Login startup controls the tray and background services, never a window."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rogcontrol import config, startup


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.config_dir = Path(self.temp.name) / 'config'
        for context in (
                patch.dict(os.environ, {'HOME': str(self.home),
                                        'XDG_CONFIG_HOME': str(self.config_dir)}),
                patch.object(config, 'CONFIG_PATH', str(self.config_dir / 'rogcontrol.json'))):
            context.start()
            self.addCleanup(context.stop)
        self.commands = []
        self.enabled = {name: True for name in (
            'rogcontrol-apply.service', 'rogcontrol-enforcer.service',
            'rogcontrol-tray.service')}
        context = patch('subprocess.run', side_effect=self.run_systemctl)
        context.start()
        self.addCleanup(context.stop)

    def run_systemctl(self, command, **kwargs):
        self.commands.append(command)
        operation = command[2]
        units = [part for part in command[3:] if part.endswith('.service')]
        if operation == 'is-enabled':
            enabled = self.enabled[units[0]]
            return subprocess.CompletedProcess(command, 0 if enabled else 1,
                                               'enabled\n' if enabled else 'disabled\n', '')
        if operation in ('enable', 'disable'):
            for unit in units:
                self.enabled[unit] = operation == 'enable'
        return subprocess.CompletedProcess(command, 0, '', '')

    def test_existing_installs_default_on_and_toggle_all_services(self):
        self.assertTrue(startup.is_enabled())
        startup.set_enabled(False)
        self.assertFalse(startup.is_enabled())
        self.assertFalse(any(self.enabled.values()))
        self.assertFalse(any('--now' in cmd or 'restart' in cmd for cmd in self.commands))
        startup.set_enabled(True)
        self.assertTrue(startup.is_enabled())
        self.assertTrue(all(self.enabled.values()))

    def test_both_legacy_entries_removed_from_custom_and_default_config(self):
        entries = []
        for directory in (self.config_dir, self.home / '.config'):
            for name in ('rogcontrol-window.desktop', 'rogcontrol-autostart.desktop'):
                entry = directory / 'autostart' / name
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.write_text('legacy')
                entries.append(entry)
        startup.set_enabled(False)
        self.assertTrue(all(not path.exists() for path in entries))

    def test_partial_service_failure_rolls_back_without_saving(self):
        original = self.run_systemctl
        def fail_once(command, **kwargs):
            if command[2] == 'disable':
                self.enabled['rogcontrol-apply.service'] = False
                return subprocess.CompletedProcess(command, 1, '', 'Permission denied')
            return original(command, **kwargs)
        with patch('subprocess.run', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'Permission denied'):
                startup.set_enabled(False)
        self.assertTrue(all(self.enabled.values()))
        self.assertTrue(startup.is_enabled())

    def test_save_failure_restores_old_service_setting(self):
        with patch.object(config, 'save_config', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                startup.set_enabled(False)
        self.assertTrue(all(self.enabled.values()))
        self.assertTrue(startup.is_enabled())

    def test_installer_applies_saved_state_without_overwriting_preference(self):
        startup.configure_services(True, start_now=True)
        # The oneshot profile apply has slow hardware retries. Queue it rather
        # than timing out and rolling back otherwise successful enablement.
        self.assertIn(['systemctl', '--user', 'restart', '--no-block',
                       'rogcontrol-apply.service', 'rogcontrol-enforcer.service',
                       'rogcontrol-tray.service'], self.commands)
        self.commands.clear()
        startup.configure_services(False, start_now=True)
        self.assertTrue(any(command[2:4] == ['disable', '--now'] for command in self.commands))
        self.assertTrue(startup.is_enabled())

    def test_cleanup_failure_preserves_preference_and_mixed_service_enablement(self):
        self.enabled['rogcontrol-tray.service'] = False
        previous = dict(self.enabled)
        with patch.object(startup, '_remove_legacy_autostart',
                          side_effect=PermissionError('autostart is read-only')):
            with self.assertRaisesRegex(OSError, 'read-only'):
                startup.set_enabled(False)
        self.assertEqual(self.enabled, previous)
        self.assertTrue(startup.is_enabled())

    def test_timeout_reports_failure_and_keeps_preference(self):
        def timeout(command, **kwargs):
            if command[2] == 'disable':
                raise subprocess.TimeoutExpired(command, 30)
            return self.run_systemctl(command, **kwargs)
        with patch('subprocess.run', side_effect=timeout):
            with self.assertRaisesRegex(OSError, 'Timed out'):
                startup.set_enabled(False)
        self.assertTrue(startup.is_enabled())
        self.assertTrue(all(self.enabled.values()))
