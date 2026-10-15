"""NVIDIA boot changes against temporary files and simulated rebuild commands."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from rogcontrol import nvidia_boot as boot


class NvidiaBootTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.node('/sys/module/nvidia/version', '615.78.08\n')
        self.node('/proc/driver/nvidia/params', 'RegistryDwords: ""\n')
        self.node('/etc/default/limine', '# test\n')
        self.tools = {'limine-update': '/test/limine-update', 'limine-enroll-config': '/test/limine-enroll-config'}

    def node(self, name, text):
        filename = boot.path(self.root, name)
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(text)
        return filename

    def state(self):
        return boot.read_state(self.root, self.tools.get)

    def apply(self, enabled):
        return boot.apply(enabled, self.root, self.tools.get)

    def test_existing_setting_toggles_both_ways_preserving_other_options(self):
        original = '# owner comment\noptions nvidia NVreg_Foo=42 NVreg_RegistryDwords="Other=7;' + boot.TOKEN + '=1;Keep=0x10;" # retain me\n'
        config = self.node('/etc/modprobe.d/nvidia-power.conf', original)
        self.assertTrue(self.state()['enabled'])
        with patch.object(boot, '_rebuild') as rebuild:
            self.apply(False)
            self.assertEqual(config.read_text(), original.replace(boot.TOKEN + '=1', boot.TOKEN + '=0'))
            self.apply(True)
        self.assertEqual(config.read_text(), original)
        self.assertEqual(rebuild.call_count, 2)
        self.assertEqual(rebuild.call_args.args[0], [['/test/limine-update'], ['/test/limine-enroll-config']])
        self.assertTrue(list(config.parent.glob(config.name + '.rogcontrol-backup-*')))

    def test_new_setting_creates_valid_configuration_and_marks_reboot_pending(self):
        with patch.object(boot, '_rebuild'):
            message = self.apply(True)
        self.assertIn('Reboot', message)
        self.assertEqual(boot.path(self.root, boot.DEFAULT_CONFIG).read_text(),
                         'options nvidia NVreg_RegistryDwords="' + boot.TOKEN + '=1"\n')
        state = self.state()
        self.assertTrue(state['enabled'])
        self.assertFalse(state['live'])
        self.assertTrue(state['pending'])
        self.node('/proc/driver/nvidia/params', 'RegistryDwords: "' + boot.TOKEN + '=1"\n')
        self.assertFalse(self.state()['pending'])

    def test_add_token_to_existing_registry_preserves_other_values(self):
        config = self.node('/etc/modprobe.d/nvidia.conf', "options nvidia NVreg_RegistryDwords='Other=0x20;'\n")
        with patch.object(boot, '_rebuild'):
            self.apply(True)
        self.assertIn('Other=0x20;' + boot.TOKEN + '=1', config.read_text())

    def test_failed_rebuild_restores_existing_config_and_rebuilds_original(self):
        original = 'options nvidia NVreg_RegistryDwords="Other=1"\n'
        config = self.node('/etc/modprobe.d/nvidia.conf', original)
        seen = []
        def rebuild(commands):
            seen.append(config.read_text())
            if len(seen) == 1:
                raise RuntimeError('initramfs failed')
        with patch.object(boot, '_rebuild', side_effect=rebuild):
            with self.assertRaisesRegex(RuntimeError, 'Original configuration restored'):
                self.apply(True)
        self.assertIn(boot.TOKEN + '=1', seen[0])
        self.assertEqual(seen[1], original)
        self.assertEqual(config.read_text(), original)

    def test_failure_removes_only_new_file_and_reports_failed_recovery(self):
        with patch.object(boot, '_rebuild', side_effect=RuntimeError('builder failed')) as rebuild:
            with self.assertRaisesRegex(RuntimeError, 'Repair boot images before rebooting'):
                self.apply(True)
        self.assertEqual(rebuild.call_count, 2)
        self.assertFalse(boot.path(self.root, boot.DEFAULT_CONFIG).exists())

    def test_unsupported_driver_or_missing_builder_never_writes(self):
        self.node('/sys/module/nvidia/version', '610.1.0')
        with patch.object(boot, '_rebuild') as rebuild:
            with self.assertRaisesRegex(ValueError, '615.71.09'):
                self.apply(True)
            self.node('/sys/module/nvidia/version', '615.78.08')
            self.tools.clear()
            with self.assertRaisesRegex(ValueError, 'Limine'):
                self.apply(True)
            rebuild.assert_not_called()
        self.assertFalse(boot.path(self.root, boot.DEFAULT_CONFIG).exists())

    def test_ambiguous_config_and_cmdline_overrides_are_refused(self):
        first = self.node('/etc/modprobe.d/a.conf', 'options nvidia NVreg_RegistryDwords="Other=1"\n')
        second = self.node('/etc/modprobe.d/b.conf', 'options nvidia NVreg_RegistryDwords="Other=2"\n')
        self.assertIn('Multiple', self.state()['message'])
        second.unlink()
        self.node('/proc/cmdline', 'quiet nvidia.NVreg_RegistryDwords=Other=3')
        self.assertIn('command line', self.state()['message'])
        self.assertEqual(first.read_text(), 'options nvidia NVreg_RegistryDwords="Other=1"\n')

    def test_modprobe_directory_precedence_and_vendor_ownership(self):
        self.node('/usr/lib/modprobe.d/nvidia.conf', 'options nvidia NVreg_RegistryDwords="Other=1"\n')
        self.assertFalse(self.state()['supported'])
        self.node('/etc/modprobe.d/nvidia.conf', 'options nvidia NVreg_RegistryDwords="Other=2"\n')
        self.assertTrue(self.state()['supported'])

    def test_duplicate_token_and_symlink_are_refused(self):
        config = self.node('/etc/modprobe.d/nvidia.conf', 'options nvidia NVreg_RegistryDwords="' + boot.TOKEN + '=1;' + boot.TOKEN + '=0"\n')
        self.assertFalse(self.state()['supported'])
        config.unlink()
        target = self.node('/outside.conf', 'options nvidia NVreg_RegistryDwords="Other=1"\n')
        config.symlink_to(target)
        self.assertFalse(self.state()['supported'])

    def test_distro_rebuild_selection_and_atomic_refusal(self):
        boot.path(self.root, '/etc/default/limine').unlink()
        self.tools.update({'mkinitcpio': '/test/mkinitcpio', 'update-initramfs': '/test/update-initramfs', 'dracut': '/test/dracut'})
        preset = self.node('/etc/mkinitcpio.d/linux.preset', '# test')
        self.assertEqual(boot.boot_commands(self.root, self.tools.get), [['/test/mkinitcpio', '-P']])
        preset.unlink()
        debian = self.node('/etc/debian_version', '13')
        self.assertEqual(boot.boot_commands(self.root, self.tools.get), [['/test/update-initramfs', '-u', '-k', 'all']])
        debian.unlink()
        self.assertEqual(boot.boot_commands(self.root, self.tools.get), [['/test/dracut', '--regenerate-all', '--force']])
        self.node('/run/ostree-booted', '')
        self.assertFalse(self.state()['supported'])

    def test_rebuild_stops_on_failure_and_reports_output(self):
        process = Mock(returncode=1)
        process.communicate.return_value = ('', 'failed to build')
        with patch.object(boot.subprocess, 'Popen', return_value=process) as run:
            with self.assertRaisesRegex(RuntimeError, 'failed to build'):
                boot._rebuild([['/test/limine-update'], ['/test/limine-enroll-config']])
        self.assertEqual(run.call_count, 1)

    def test_timed_out_builder_stops_children_before_recovery(self):
        process = Mock(pid=123, returncode=-15)
        process.communicate.side_effect = [subprocess.TimeoutExpired('builder', 300), ('', '')]
        with patch.object(boot.subprocess, 'Popen', return_value=process), patch.object(boot.os, 'killpg') as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                boot._rebuild([['/test/builder']])
        kill.assert_called_once_with(123, boot.signal.SIGTERM)
        self.assertEqual(process.communicate.call_count, 2)

    def test_privileged_entry_rejects_extra_arguments_without_writing(self):
        result = subprocess.run(['python3', '-I', 'rogcontrol/nvidia_boot.py', '1', '/etc/other'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exactly 0 or 1', result.stderr)
