"""Decky live metrics: interval math, partial sensors, and sleeping GPU safety."""
import importlib.util
from pathlib import Path
import unittest
import sys
import tempfile
import types
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('decky_telemetry', Path(__file__).resolve().parents[1] / 'decky/telemetry.py')
telemetry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(telemetry)


class LiveInfoTests(unittest.TestCase):
    def test_bridge_uses_bundled_sampler_with_system_python(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / '.local/bin').mkdir(parents=True)
            (home / '.local/bin/rogcontrol').touch()
            bridge_spec = importlib.util.spec_from_file_location(
                'decky_bridge', Path(__file__).resolve().parents[1] / 'decky/main.py')
            bridge = importlib.util.module_from_spec(bridge_spec)
            with patch.dict(sys.modules, {'decky': types.SimpleNamespace(DECKY_USER_HOME=directory)}):
                bridge_spec.loader.exec_module(bridge)
            completed = types.SimpleNamespace(stdout='{"ok":true,"telemetry":{"cpu_usage":25}}',
                                              stderr='', returncode=0)
            with patch.object(bridge.os, 'geteuid', return_value=1000), \
                 patch.object(bridge.subprocess, 'run', return_value=completed) as run:
                result = bridge.Plugin()._command(['telemetry'], 15)
            self.assertEqual(result['telemetry']['cpu_usage'], 25)
            self.assertEqual(run.call_args.args[0], [
                '/usr/bin/python3', str(Path(bridge.__file__).with_name('telemetry.py')),
                str(home / '.local/lib')])
            self.assertNotIn('PYTHONHOME', run.call_args.kwargs['env'])

    def test_cpu_usage_excludes_guest_and_iowait(self):
        before = 'cpu 100 0 20 300 10 0 0 0 40 0\n'
        after = 'cpu 130 0 30 340 30 0 0 0 60 0\n'
        with patch('builtins.open', side_effect=[__import__('io').StringIO(before), __import__('io').StringIO(after)]):
            first = telemetry.cpu_times()
            second = telemetry.cpu_times()
        self.assertEqual(telemetry.cpu_usage(first, second), 40)

    def test_counter_reset_or_missing_sample_is_unavailable(self):
        for first, second in [(None, (100, 50)), ((100, 50), (90, 40)),
                              ((100, 50), (100, 50)), ((100, 50), (110, 40))]:
            self.assertIsNone(telemetry.cpu_usage(first, second))

    def hardware(self, sleeping=False):
        h = Mock()
        h.dgpu_is_suspended.return_value = sleeping
        h.read_cpu_power_w.side_effect = [None, 12.5]
        h.read_nvidia_query.return_value = (61, 35.2, 1200, 42)
        h.read_cpu_temp.return_value = 57
        h.read_peak_core_clock_mhz.return_value = 3100
        h.read_fan_rpms.return_value = {'1': 2100, '2': 0}
        return h

    def test_live_fields_sample_package_power_and_group_gpu_query(self):
        h = self.hardware()
        with patch.object(telemetry, 'cpu_times', side_effect=[(100, 50), (200, 100)]), \
             patch.object(telemetry.time, 'sleep'):
            result = telemetry.collect(h, {'current_profile': 'Quiet'})
        self.assertEqual(result['cpu_usage'], 50)
        self.assertEqual(result['cpu_power_w'], 12.5)
        self.assertEqual(result['cpu_clock_mhz'], 3100)
        self.assertEqual(result['gpu_usage'], 42)
        self.assertEqual(result['gpu_power_w'], 35.2)
        self.assertEqual(result['gpu_clock_mhz'], 1200)
        h.read_nvidia_query.assert_called_once_with(
            ('temperature.gpu', 'power.draw', 'clocks.gr', 'utilization.gpu'), timeout=3)

    def test_sleeping_gpu_is_not_queried(self):
        h = self.hardware(sleeping=True)
        with patch.object(telemetry, 'cpu_times', return_value=None), patch.object(telemetry.time, 'sleep'):
            result = telemetry.collect(h, {})
        h.read_nvidia_query.assert_not_called()
        self.assertEqual(result['gpu_status'], 'sleeping')
        for key in ('gpu_temp', 'gpu_usage', 'gpu_clock_mhz', 'gpu_power_w'):
            self.assertIsNone(result[key])

    def test_partial_gpu_values_preserve_zero_and_discard_nonfinite(self):
        h = self.hardware()
        h.read_nvidia_query.return_value = (None, float('nan'), 0, 0)
        with patch.object(telemetry, 'cpu_times', return_value=None), patch.object(telemetry.time, 'sleep'):
            result = telemetry.collect(h, {})
        self.assertEqual(result['gpu_status'], 'available')
        self.assertEqual(result['gpu_usage'], 0)
        self.assertEqual(result['gpu_clock_mhz'], 0)
        self.assertIsNone(result['gpu_power_w'])


if __name__ == '__main__':
    unittest.main()
