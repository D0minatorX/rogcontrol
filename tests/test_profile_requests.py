import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from rogcontrol import config, profile_requests as requests


class RequestsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, value in ((patch.object(config, 'CONFIG_PATH', str(Path(self.tmp.name)/'config.json')), None),
                              (patch.object(requests, 'directory', return_value=Path(self.tmp.name)), None),
                              (patch.object(requests, '_launch'), None),
                              (patch.object(requests, 'active_session', return_value=True), None),
                              (patch.object(requests.hardware, 'notify'), None)):
            target.start(); self.addCleanup(target.stop)
        config.save_config({'profiles': {'A': {}, 'B': {}, 'C': {}},
                            'current_profile': 'A', 'key_bindings': {'enabled': True}})

    def test_three_presses_keep_final_target(self):
        for _ in range(3): requests.request_next('bindings')
        self.assertEqual(config.load_config()['current_profile'], 'A')
        applied = []
        requests.drain(lambda cfg, name: applied.append(name))
        self.assertEqual(applied, ['A'])

    def test_presses_during_apply_coalesce_to_exact_profile(self):
        requests.request_next('bindings')
        applied = []
        def apply(cfg, name):
            applied.append(name)
            if name == 'B':
                requests.request_next('bindings')
                requests.request_next('bindings')
        requests.drain(apply)
        self.assertEqual(applied, ['B', 'A'])

    def test_disabling_feature_cancels_queued_write(self):
        requests.request_next('bindings')
        config.update_config(lambda cfg: cfg['key_bindings'].update(enabled=False))
        applied = []
        requests.drain(lambda cfg, name: applied.append(name))
        self.assertEqual(applied, [])
        self.assertEqual(config.load_config()['current_profile'], 'A')

    def test_locked_session_cancels_pending_hardware_action(self):
        requests.request_next('bindings')
        applied = []
        with patch.object(requests, 'active_session', return_value=False):
            requests.drain(lambda cfg, name: applied.append(name))
        self.assertEqual(applied, [])
        self.assertEqual(config.load_config()['current_profile'], 'A')

    def test_manual_selection_supersedes_pending_shortcut(self):
        requests.request_next('bindings')
        config.update_config(lambda cfg: cfg.update(current_profile='C'))
        applied = []
        requests.drain(lambda cfg, name: applied.append(name))
        self.assertEqual(applied, [])

    def test_second_worker_cannot_apply_concurrently(self):
        requests.request_next('bindings')
        applied = []
        def apply(cfg, name):
            applied.append(name)
            if name == 'B':
                requests.request_next('bindings')
                requests.drain(lambda *_: self.fail('concurrent writer'))
        requests.drain(apply)
        self.assertEqual(applied, ['B', 'C'])


class ApplyFailureTests(unittest.TestCase):
    def test_cpu_gpu_failure_does_not_block_fans(self):
        from contextlib import ExitStack
        from rogcontrol import cli
        hw = cli.hardware
        profile = {'cpu': {'epp': 'balance_performance'}, 'gpu': {'dyn_boost': 5},
                   'fans': {'1': [[30, 20], [80, 80]]}}
        with ExitStack() as stack:
            for name, value in {
                'cpu_is_amd': False, 'cpu_power_limits_backend': None,
                'cpu_apply_plan': [('epp', ('cpuepp', 'balance_performance'))],
                'run_helper_logged': (False, 'CPU unavailable'),
                'nv_apply_settings': (False, 'GPU unavailable'),
                'read_fan_boost': None, 'fan_boost_active': False,
                'read_fan_curve_points': None, 'read_fan_curve_enabled': {},
            }.items():
                stack.enter_context(patch.object(hw, name, return_value=value))
            fan = stack.enter_context(patch.object(hw, 'run_fan_helper_logged', return_value=(True, '')))
            with self.assertRaisesRegex(RuntimeError, 'CPU unavailable.*GPU unavailable'):
                cli.apply_profile(profile)
            fan.assert_called_once()
