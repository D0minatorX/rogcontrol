import unittest
from unittest import mock
from contextlib import nullcontext

from rogcontrol import decky


class DeckyGpuTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {'current_profile': 'Quiet', 'profiles': {'Quiet': {'cpu': {}, 'gpu': {'clock_limit': 1500}}}}
        self.caps = {'nvidia': True, 'cpu_clock': None}

    def test_state_reports_saved_gpu_ceiling_and_supported_range(self):
        with mock.patch.object(decky.profile_requests, 'hardware_apply_lock', return_value=nullcontext()), mock.patch.object(decky.hardware, 'gpu_clock_limit_supported', return_value=True), mock.patch.object(decky.hardware, 'detect_gpu_max_clock', return_value=2400):
            state = decky.get_state(self.cfg, self.caps)
        self.assertEqual(state['gpu']['clock_limit_mhz'], 1500)
        self.assertEqual(state['capabilities']['gpu_clock_range_mhz'], [200, 2400])

    def test_gpu_ceiling_is_applied_and_saved_to_active_profile(self):
        with mock.patch.object(decky.profile_requests, 'hardware_apply_lock', return_value=nullcontext()), mock.patch.object(decky.hardware, 'detect_capabilities', return_value=self.caps), mock.patch.object(decky.hardware, 'dgpu_is_suspended', return_value=False), mock.patch.object(decky.hardware, 'detect_gpu_max_clock', return_value=2400), mock.patch.object(decky.hardware, 'gpu_clock_limit_supported', return_value=True), mock.patch.object(decky.hardware, 'run_helper_logged', return_value=(True, '')) as helper, mock.patch.object(decky.config, 'load_config', return_value=self.cfg), mock.patch.object(decky.config, 'update_config', side_effect=lambda fn: (fn(self.cfg), self.cfg)[1]):
            decky.set_gpu_clock_limit(1700)
        self.assertEqual(self.cfg['profiles']['Quiet']['gpu']['clock_limit'], 1700)
        helper.assert_called_once_with('gpuclocklimit', 1700, source='decky', timeout=30)

    def test_telemetry_preserves_unavailable_values(self):
        with mock.patch.object(decky.config, 'load_config', return_value={'current_profile': 'Quiet'}), mock.patch.object(decky.hardware, 'read_cpu_temp', return_value=56), mock.patch.object(decky.hardware, 'read_fan_rpms', return_value={'1': 2200, '2': None, '3': 0}), mock.patch.object(decky.hardware, 'dgpu_is_suspended', return_value=True):
            readings = decky.get_telemetry()
        self.assertEqual(readings, {'cpu_temp': 56, 'gpu_temp': None, 'gpu_status': 'sleeping', 'fans': {'1': 2200, '2': None, '3': 0}, 'current_profile': 'Quiet'})


if __name__ == '__main__':
    unittest.main()
