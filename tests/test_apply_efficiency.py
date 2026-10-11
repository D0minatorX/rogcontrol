import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rogcontrol import hardware


class ApplyEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def node(self, path, value):
        p = self.root / path.lstrip('/')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(value))
        return p

    def test_clock_skip_requires_every_policy_and_fresh_readback(self):
        for i in range(2):
            self.node(f'/sys/devices/system/cpu/cpufreq/policy{i}/scaling_max_freq', 3000000)
        args = ('cpuclock', 3000000)
        self.assertTrue(hardware.helper_action_unchanged(args, root=self.root))
        self.node('/sys/devices/system/cpu/cpufreq/policy1/scaling_max_freq', 4000000)
        self.assertFalse(hardware.helper_action_unchanged(args, root=self.root))

    def test_missing_policy_control_never_counts_as_equal(self):
        self.node('/sys/devices/system/cpu/cpufreq/policy0/boost', 1)
        self.node('/sys/devices/system/cpu/cpufreq/policy1/scaling_max_freq', 3000000)
        self.assertFalse(hardware.helper_action_unchanged(('cpuboost', 1), root=self.root))

    def test_global_boost_does_not_hide_disabled_policy(self):
        self.node('/sys/devices/system/cpu/cpufreq/boost', 1)
        self.node('/sys/devices/system/cpu/cpufreq/policy0/boost', 0)
        self.node('/sys/devices/system/cpu/cpufreq/policy1/boost', 1)
        self.assertFalse(hardware.helper_action_unchanged(('cpuboost', 1), root=self.root))

    def test_helper_repairs_policy_boost_even_when_global_is_enabled(self):
        self.node('/sys/devices/system/cpu/cpufreq/boost', 1)
        policy = self.node('/sys/devices/system/cpu/cpufreq/policy0/boost', 0)
        source = Path('rogcontrol/rogcontrol-helper').read_text().replace('/sys/', str(self.root) + '/sys/')
        helper = self.root / 'helper'
        helper.write_text(source)
        result = subprocess.run(['bash', str(helper), 'cpuboost', '1'], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(policy.read_text().strip(), '1')

    def test_boost_readback_reports_disabled_policy_despite_global_enable(self):
        self.node('/sys/devices/system/cpu/cpufreq/boost', 1)
        self.node('/sys/devices/system/cpu/cpufreq/policy0/boost', 1)
        self.node('/sys/devices/system/cpu/cpufreq/policy1/boost', 0)
        self.assertFalse(hardware.read_cpu_boost_enabled(root=self.root))

    def test_floor_restore_resolves_each_policy_default(self):
        for i, value in enumerate((1000000, 1500000)):
            self.node(f'/sys/devices/system/cpu/cpufreq/policy{i}/scaling_min_freq', value)
            self.node(f'/sys/devices/system/cpu/cpufreq/policy{i}/amd_pstate_lowest_nonlinear_freq', value)
        self.assertTrue(hardware.helper_action_unchanged(('cpuminclock', 'min'), root=self.root))
        self.assertFalse(hardware.helper_action_unchanged(('cpu', 30, 40, 40, 85), root=self.root))

    def test_run_helper_skips_process_only_on_confirmed_equal(self):
        with patch.object(hardware, 'helper_action_unchanged', return_value=True), patch.object(hardware.subprocess, 'Popen') as spawn:
            self.assertTrue(hardware.run_helper('cpuboost', 1)[0])
            spawn.assert_not_called()

    def test_firmware_batch_uses_one_process_and_preserves_missing_fields(self):
        calls = []
        def run(*args):
            calls.append(args)
            return True, ''
        self.assertTrue(hardware.nv_apply_settings({'dyn_boost': 15, 'temp_target': 80}, run=run)[0])
        self.assertEqual(calls, [('nvfirmware', 15, 80)])

    def test_batch_validates_all_fields_before_writing(self):
        boost = self.node('/sys/devices/platform/asus-nb-wmi/nv_dynamic_boost', 10)
        self.node('/sys/devices/platform/asus-nb-wmi/nv_temp_target', 80)
        source = Path('rogcontrol/rogcontrol-helper').read_text().replace('/sys/', str(self.root) + '/sys/')
        helper = self.root / 'helper'
        helper.write_text(source)
        bad = subprocess.run(['bash', str(helper), 'nvfirmware', '15', '999'], capture_output=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(boost.read_text(), '10')
        good = subprocess.run(['bash', str(helper), 'nvfirmware', '15', '80'], capture_output=True)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(boost.read_text().strip(), '15')

    def test_gpu_watts_skips_only_when_every_card_reports_requested_limit(self):
        binary = self.root / 'nvidia-smi'
        log = self.root / 'calls'
        report = self.root / 'report'
        binary.write_text('#!/bin/bash\necho "$*" >> "$CALL_LOG"\nif [ "$1" = -q ]; then cat "$REPORT"; fi\n')
        binary.chmod(0o755)
        env = dict(os.environ, PATH=f'{self.root}:' + os.environ['PATH'], CALL_LOG=str(log), REPORT=str(report))
        report.write_text('Current Power Limit : 100.00 W\nCurrent Power Limit : 100.00 W\n')
        result = subprocess.run(['bash', 'rogcontrol/rogcontrol-helper', 'gpu', '100'], env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.read_text().splitlines(), ['-q -d POWER'])
        log.write_text('')
        report.write_text('Current Power Limit : 100.00 W\nCurrent Power Limit : 80.00 W\n')
        subprocess.run(['bash', 'rogcontrol/rogcontrol-helper', 'gpu', '100'], env=env, capture_output=True)
        self.assertIn('-pl 100', log.read_text().splitlines())

    def test_boost_reset_is_observed_before_subsequent_floor_check(self):
        self.node('/sys/devices/system/cpu/cpufreq/boost', 0)
        floor = self.node('/sys/devices/system/cpu/cpufreq/policy0/scaling_min_freq', 2000000)
        self.assertTrue(hardware.helper_action_unchanged(('cpuminclock', 2000000), root=self.root))
        self.assertFalse(hardware.helper_action_unchanged(('cpuboost', 1), root=self.root))
        floor.write_text('1000000')  # Effect of the boost write in the kernel.
        self.assertFalse(hardware.helper_action_unchanged(('cpuminclock', 2000000), root=self.root))

    def test_epp_requires_all_policies_and_missing_read_never_skips(self):
        for i in range(2):
            self.node(f'/sys/devices/system/cpu/cpufreq/policy{i}/energy_performance_preference', 'balance_power')
        self.assertTrue(hardware.helper_action_unchanged(('cpuepp', 'balance_power'), root=self.root))
        self.node('/sys/devices/system/cpu/cpufreq/policy1/energy_performance_preference', 'power')
        self.assertFalse(hardware.helper_action_unchanged(('cpuepp', 'balance_power'), root=self.root))
        self.assertFalse(hardware.helper_action_unchanged(('nvtemp', 80), root=self.root))

    def test_firmware_cached_sysfs_values_never_prove_current_hardware_state(self):
        self.node('/sys/devices/platform/asus-nb-wmi/nv_dynamic_boost', 15)
        self.node('/sys/devices/platform/asus-nb-wmi/nv_temp_target', 80)
        for args in [('nvboost', 15), ('nvtemp', 80), ('nvfirmware', 15, 80)]:
            self.assertFalse(hardware.helper_action_unchanged(args, root=self.root))


if __name__ == '__main__':
    unittest.main()
