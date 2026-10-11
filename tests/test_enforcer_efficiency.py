"""Local regression checks: no live hardware writes."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from rogcontrol import fancurve


def load_enforcer():
    path = Path(__file__).resolve().parents[1] / 'rogcontrol/rogcontrol-enforcer.py'
    spec = importlib.util.spec_from_file_location('enforcer_efficiency', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EnforcerEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.enforcer = load_enforcer()
        self.fans = {ch: [(30, 20), (90, 90)] for ch in ('1', '2', '3')}
        self.profile = {'fans': self.fans}
        self.config = {'current_profile': 'balanced'}
        self.signature = ('balanced', json.dumps(self.fans, sort_keys=True))
        self.enforcer._last_applied_fans = self.signature
        self.enforcer._last_fan_apply_time = 100
        self.actual = {ch: [(t, fancurve.pct_to_pwm255(p))
                            for t, p in fancurve.interpolate_curve(points)]
                       for ch, points in self.fans.items()}
        self.enabled = {ch: True for ch in self.fans}
        self.mock('time.monotonic', return_value=160)
        self.sleep = self.mock('time.sleep')
        self.mock('hardware.read_fan_boost', return_value=None)
        self.read_enabled = self.mock('hardware.read_fan_curve_enabled',
                                     side_effect=lambda: self.enabled)
        self.read_points = self.mock('hardware.read_fan_curve_points',
                                    side_effect=lambda ch: self.actual[ch])
        self.write = self.mock('hardware.run_fan_helper_logged', return_value=(True, ''))
        self.mock('run_helper', side_effect=AssertionError('unexpected helper'))

    def mock(self, name, **kwargs):
        owner = self.enforcer
        parts = name.split('.')
        for part in parts[:-1]:
            owner = getattr(owner, part)
        patcher = patch.object(owner, parts[-1], **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def apply(self, **kwargs):
        self.enforcer.apply_full_profile(self.config, self.profile, full=False, **kwargs)

    def test_matching_periodic_fans_have_fresh_reads_without_writes(self):
        self.apply()
        self.apply()
        self.assertEqual(self.read_enabled.call_count, 2)
        self.assertEqual(self.read_points.call_count, 6)
        self.write.assert_not_called()
        self.sleep.assert_not_called()

    def test_disabled_channel_only_is_reapplied(self):
        self.enabled['2'] = False
        self.apply()
        self.assertEqual(self.write.call_count, 1)
        self.assertEqual(self.write.call_args.args[0], '2')
        self.assertEqual(self.enforcer._last_fan_apply_time, 100)

    def test_mismatching_channel_only_is_reapplied(self):
        self.actual['3'][0] = (30, 1)
        self.apply()
        self.assertEqual(self.write.call_count, 1)
        self.assertEqual(self.write.call_args.args[0], '3')

    def test_unknown_readback_waits_for_firmware_safety_refresh(self):
        self.actual = {ch: None for ch in self.fans}
        self.enabled = {ch: None for ch in self.fans}
        self.apply()
        self.write.assert_not_called()
        self.enforcer._last_fan_apply_time = -200
        self.apply()
        self.assertEqual(self.write.call_count, 3)

    def test_matching_driver_cache_still_gets_slow_firmware_refresh(self):
        self.enforcer._last_fan_apply_time = -200
        self.apply()
        self.assertEqual(self.write.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(self.enforcer._last_fan_apply_time, 160)

    def test_mode_or_boost_end_force_reapplies_all(self):
        self.apply(force_fan_reapply=True)
        self.assertEqual(self.write.call_count, 3)

    def test_failure_invalidates_signature_and_retries_next_pass(self):
        self.enabled['2'] = False
        self.write.return_value = (False, 'timed out')
        self.apply()
        self.assertIsNone(self.enforcer._last_applied_fans)
        self.assertEqual(self.enforcer._last_fan_apply_time, 100)
        self.enabled['2'] = True
        self.write.reset_mock()
        self.write.return_value = (True, '')
        self.apply()
        self.assertEqual(self.write.call_count, 3)
        self.assertEqual(self.enforcer._last_applied_fans, self.signature)

    def test_gpu_firmware_batch_logs_failure(self):
        batch = self.mock('hardware.nv_apply_settings', return_value=(False, 'failure'))
        log = self.mock('log')
        gpu = {'dyn_boost': 15, 'temp_target': 80}
        self.enforcer.apply_gpu_clock_offsets(gpu)
        batch.assert_called_once_with(gpu)
        self.assertIn('failure', log.call_args.args[0])
        self.assertEqual(log.call_args.args[1], 'ERROR')


if __name__ == '__main__':
    unittest.main()
