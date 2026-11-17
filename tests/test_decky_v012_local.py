import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from rogcontrol import decky, profile_requests as requests, decky_install


class ProfileStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {'XDG_RUNTIME_DIR': self.temp.name})
        self.env.start(); self.addCleanup(self.env.stop)
        self.cfg = {'current_profile': 'Quiet', 'profiles': {'Quiet': {}, 'Turbo': {}}}
        self.mocks = [mock.patch.object(requests.config, 'load_config', side_effect=lambda: copy.deepcopy(self.cfg)),
                      mock.patch.object(requests.config, 'update_config', side_effect=self.update),
                      mock.patch.object(requests, '_launch'), mock.patch.object(requests, 'active_session', return_value=True),
                      mock.patch.object(requests.hardware, 'notify'), mock.patch.object(requests.hardware, 'log')]
        for p in self.mocks: p.start(); self.addCleanup(p.stop)
    def update(self, mutate):
        mutate(self.cfg); return copy.deepcopy(self.cfg)
    def test_status_waits_for_hardware_completion(self):
        request=requests.request_profile('Turbo')
        self.assertEqual(requests.request_status(request['request_id'])['status'], 'pending')
        def apply(cfg, name):
            self.assertEqual(requests.request_status(request['request_id'])['status'], 'applying')
        requests.drain(apply)
        self.assertEqual(requests.request_status(request['request_id'])['status'], 'applied')
    def test_failure_is_reported_after_profile_name_changes(self):
        request=requests.request_profile('Turbo')
        def apply(cfg, name): raise RuntimeError('fan write failed')
        requests.drain(apply)
        status=requests.request_status(request['request_id'])
        self.assertEqual(status['status'], 'failed'); self.assertIn('fan write failed', status['error'])
    def test_replaced_pending_request_is_reported(self):
        first=requests.request_profile('Turbo'); requests.request_profile('Quiet')
        self.assertEqual(requests.request_status(first['request_id'])['status'], 'superseded')
    def test_locked_session_reports_actionable_failure(self):
        request=requests.request_profile('Turbo')
        with mock.patch.object(requests, 'active_session', return_value=False): requests.drain(lambda cfg, name: self.fail('should not apply'))
        status=requests.request_status(request['request_id'])
        self.assertEqual(status['status'],'failed'); self.assertIn('Unlock',status['error'])
    def test_shortcut_supersedes_pending_decky_request(self):
        first=requests.request_profile('Turbo'); requests.request_next()
        self.assertEqual(requests.request_status(first['request_id'])['status'], 'superseded')


class SensorTests(unittest.TestCase):
    def test_sleeping_gpu_does_not_query_driver(self):
        with mock.patch.object(decky.hardware,'dgpu_is_suspended',return_value=True), mock.patch.object(decky.hardware,'read_nvidia_query') as query, mock.patch.object(decky.hardware,'read_cpu_temp',return_value=51), mock.patch.object(decky.hardware,'read_fan_rpms',return_value={'1':0}), mock.patch.object(decky.config,'load_config',return_value={'current_profile':'Quiet'}):
            state=decky.get_telemetry()
        query.assert_not_called(); self.assertEqual(state['gpu_status'], 'sleeping'); self.assertEqual(state['current_profile'], 'Quiet'); self.assertEqual(state['fans']['1'],0)
    def test_state_does_not_probe_sleeping_gpu(self):
        with mock.patch.object(decky.hardware,'dgpu_is_suspended',return_value=True), mock.patch.object(decky.hardware,'gpu_clock_limit_supported') as probe:
            state=decky.get_state({'current_profile':'Quiet','profiles':{'Quiet':{'gpu':{'clock_limit':1500}}}}, {'nvidia':True})
        probe.assert_not_called(); self.assertEqual(state['gpu']['clock_limit_mhz'],1500); self.assertIsNone(state['capabilities']['gpu_clock_range_mhz'])


class InstallerSafetyTests(unittest.TestCase):
    def test_rejects_unrelated_existing_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'ROG-Control'; root.mkdir(); (root/'package.json').write_text('{"name":"unrelated","version":"1"}')
            with mock.patch.object(decky_install.decky_release,'validate_archive',return_value={'version':'0.1.2'}):
                with self.assertRaisesRegex(ValueError,'not an existing ROG Control'):
                    decky_install.install_archive('unused.zip', plugin_root_path=root)
            self.assertEqual(json.loads((root/'package.json').read_text())['name'],'unrelated')

if __name__ == '__main__': unittest.main()

class DeckyBridgeTests(unittest.TestCase):
    def test_root_loader_preserves_privilege_only_for_update(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp); (home/'.local/bin').mkdir(parents=True); (home/'.local/bin/rogcontrol').write_text('')
            decky_stub=types.SimpleNamespace(DECKY_USER_HOME=str(home), logger=mock.Mock())
            with mock.patch.dict(sys.modules, {'decky':decky_stub}):
                spec=importlib.util.spec_from_file_location('decky_plugin_test', Path('decky/main.py'))
                module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            account=types.SimpleNamespace(pw_name='deckuser',pw_uid=12345)
            completed=types.SimpleNamespace(stdout='{"ok":true}', stderr='', returncode=0)
            with mock.patch.object(module.os,'geteuid',return_value=0), mock.patch.object(module.pwd,'getpwuid',return_value=account), mock.patch.object(module.shutil,'which',return_value='/usr/bin/runuser'), mock.patch.object(module.subprocess,'run',return_value=completed) as run:
                plugin=module.Plugin(); plugin._command(['state'],30); normal=run.call_args.args[0]
                plugin._command(['update-install'],180,True); update=run.call_args.args[0]
            self.assertEqual(normal[:3], ['/usr/bin/runuser','--user','deckuser'])
            self.assertEqual(update[0], str(home/'.local/bin/rogcontrol'))
            self.assertEqual(update[1:], ['decky','update-install'])
