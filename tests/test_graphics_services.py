"""Exercise graphics service actions with a fake systemctl, never the host."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rogcontrol import hardware


class GraphicsServiceTests(unittest.TestCase):
    def test_hardware_routes_enable_and_disable_for_each_backend(self):
        for name in ('supergfxd', 'cardwired'):
            setter = getattr(hardware, 'set_' + name + '_running')
            with patch.object(hardware, 'run_helper', return_value=(True, '')) as helper:
                setter()
                helper.assert_called_with(name + '_enable', timeout=30)
                setter(running=False)
                helper.assert_called_with(name + '_disable', timeout=30)

    def test_helper_runs_only_fixed_unit_actions_and_propagates_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / 'calls'
            systemctl = root / 'systemctl'
            systemctl.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$GRAPHICS_TEST_LOG"\nexit "${GRAPHICS_TEST_EXIT:-0}"\n')
            systemctl.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'], GRAPHICS_TEST_LOG=str(log))
            for service in ('supergfxd', 'cardwired'):
                for action, commands in (
                    ('enable', ['unmask', 'enable --now']),
                    ('disable', ['disable --now', 'mask --now']),
                ):
                    with self.subTest(service=service, action=action):
                        log.write_text('')
                        argv = ['bash', 'rogcontrol/rogcontrol-helper', service + '_' + action]
                        result = subprocess.run(argv, env=env, capture_output=True, timeout=5)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(log.read_text().splitlines(), [cmd + ' ' + service + '.service' for cmd in commands])
                        log.write_text('')
                        result = subprocess.run(argv + ['other.service'], env=env, capture_output=True, timeout=5)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(log.read_text(), '')
                        result = subprocess.run(argv, env=dict(env, GRAPHICS_TEST_EXIT='1'), capture_output=True, timeout=5)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(len(log.read_text().splitlines()), 1)
