"""PPD management tests use fake process results; no system changes."""
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import Mock, patch

from rogcontrol import ppd_service as ppd


class PpdServiceTests(unittest.TestCase):
    def test_package_actions_use_native_manager_without_auto_confirmation(self):
        for manager, install, uninstall in (
            ('pacman', 'sudo pacman -S', 'sudo pacman -R'),
            ('apt-get', 'sudo apt-get update && sudo apt-get install', 'sudo apt-get remove'),
            ('dnf', 'sudo dnf install', 'sudo dnf remove'),
            ('zypper', 'sudo zypper install', 'sudo zypper remove'),
            ('rpm-ostree', 'sudo rpm-ostree install', 'sudo rpm-ostree uninstall'),
        ):
            with self.subTest(manager=manager), patch.object(ppd, 'package_manager', return_value=manager), patch.object(ppd, 'tuned_ppd_installed', return_value=False):
                for action, prefix in (('install', install), ('uninstall', uninstall)):
                    command = ppd.action_command(action)
                    self.assertTrue(command.startswith(prefix + ' power-profiles-daemon'))
                    self.assertNotIn(' -y', command)
                    self.assertNotIn('--noconfirm', command)
                    if manager == 'rpm-ostree':
                        self.assertIn('Reboot', command)

    def test_disable_masks_dbus_activation_and_enable_unmasks(self):
        with patch.object(ppd, 'tuned_ppd_installed', return_value=False):
            self.assertEqual(ppd.action_command('disable'), 'sudo systemctl disable --now power-profiles-daemon.service && sudo systemctl mask power-profiles-daemon.service')
            self.assertEqual(ppd.action_command('enable'), 'sudo systemctl unmask power-profiles-daemon.service && sudo systemctl enable --now power-profiles-daemon.service')

    def test_invalid_actions_and_tuned_provider_never_launch_terminal(self):
        with patch.object(ppd, 'tuned_ppd_installed', return_value=True), patch.object(ppd.subprocess, 'Popen') as launch:
            for action in ('install', 'uninstall', 'enable', 'disable', 'install; touch /tmp/no'):
                self.assertFalse(ppd.launch_action(action)[0])
            launch.assert_not_called()

    def test_immutable_host_never_falls_back_to_dnf(self):
        with patch.object(ppd.os.path, 'exists', return_value=True), patch.object(ppd.shutil, 'which', side_effect=lambda name: '/usr/bin/dnf' if name == 'dnf' else None):
            self.assertIsNone(ppd.package_manager())

    def test_leftover_mask_does_not_mark_removed_package_installed(self):
        replies = ['power-profiles-daemon.service masked', 'inactive', 'masked']
        with patch.object(ppd, '_query', side_effect=[SimpleNamespace(stdout=v, returncode=0) for v in replies]), patch.object(ppd, '_package_installed', return_value=False), patch.object(ppd.shutil, 'which', return_value=None), patch.object(ppd, 'package_manager', return_value='apt-get'):
            state = ppd.read_state()
        self.assertTrue(state['masked'])
        self.assertFalse(state['installed'])
        self.assertFalse(state['has_unit'])

    def test_removed_debian_package_is_not_a_tuned_conflict(self):
        with patch.object(ppd.shutil, 'which', side_effect=lambda name: name if name == 'dpkg-query' else None), patch.object(ppd, '_query', return_value=SimpleNamespace(returncode=0, stdout='deinstall ok config-files')):
            self.assertFalse(ppd.tuned_ppd_installed())

    def test_launch_reports_terminal_opened_not_operation_completed(self):
        process = Mock()
        process.wait.side_effect = subprocess.TimeoutExpired('terminal', 0.3)
        with patch.object(ppd, 'action_command', return_value='sudo pacman -S power-profiles-daemon'), patch.object(ppd.shutil, 'which', return_value='/usr/bin/terminal'), patch.object(ppd.subprocess, 'Popen', return_value=process) as launch:
            ok, message = ppd.launch_action('install')
        self.assertTrue(ok)
        self.assertIn('Terminal opened', message)
        self.assertIn('result=$?', launch.call_args.args[0][-1])

    def test_no_terminal_returns_an_actionable_failure(self):
        with patch.object(ppd, 'action_command', return_value='sudo pacman -S power-profiles-daemon'), patch.object(ppd.shutil, 'which', return_value=None):
            ok, message = ppd.launch_action('install')
        self.assertFalse(ok)
        self.assertIn('Run: sudo pacman -S power-profiles-daemon', message)
