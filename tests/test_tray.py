"""Qt tray lifecycle checks in an isolated process without a real tray host."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'rogcontrol/rogcontrol-tray'


class QtTrayTests(unittest.TestCase):
    def run_tray_code(self, body):
        prefix = f'''import os, runpy, sys
from unittest.mock import patch
before = dict(os.environ)
m = runpy.run_path({str(SCRIPT)!r})
assert 'gi.repository.Gtk' not in sys.modules, 'tray still loads GTK'
assert all(os.environ.get(k) == before.get(k) for k in ('XDG_CONFIG_HOME', 'GTK_THEME')), 'tray import changed desktop environment'
'''
        result = subprocess.run([sys.executable, '-c', prefix + body],
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_import_preserves_environment_and_launch_targets_same_package(self):
        self.run_tray_code('''
args, env = m['app_command']('--toggle')
assert args[-2:] == ['--qt', '--toggle'], args
assert env.get('GTK_THEME') == before.get('GTK_THEME')
assert 'PYTHONPATH' in env
''')

    def test_custom_profile_has_no_false_stock_selection_and_poll_does_not_apply(self):
        self.run_tray_code('''
app = m['QApplication']([])
cfg = {'current_profile': 'Custom', 'profiles': {'Quiet': {}, 'Performance': {}, 'Custom': {}}}
with patch.object(m['config_mod'], 'load_config', return_value=cfg), patch.object(m['config_mod'], 'config_mtime', return_value=1), patch.object(m['QSystemTrayIcon'], 'isSystemTrayAvailable', return_value=False):
    tray = m['Tray']()
    assert not any(a.isChecked() for a in tray._items.values())
    tray._sync_menu('Quiet')
    assert tray._items['Quiet'].isChecked()
    tray._sync_menu('Custom')
    assert not any(a.isChecked() for a in tray._items.values())
    assert cfg['current_profile'] == 'Custom'
    tray.stop()
''')

    def test_failed_profile_request_restores_menu_and_reports_error(self):
        self.run_tray_code('''
app = m['QApplication']([])
cfg = {'current_profile': 'Quiet', 'profiles': {'Quiet': {}, 'Performance': {}}}
with patch.object(m['config_mod'], 'load_config', return_value=cfg), patch.object(m['config_mod'], 'config_mtime', return_value=1), patch.object(m['QSystemTrayIcon'], 'isSystemTrayAvailable', return_value=False), patch.object(m['profile_requests'], 'request_profile', side_effect=OSError('worker unavailable')):
    tray = m['Tray']()
    tray._items['Performance'].trigger()
    assert tray._items['Quiet'].isChecked()
    assert cfg['current_profile'] == 'Quiet'
    assert 'worker unavailable' in tray.indicator.toolTip()
    tray.stop()
''')

    def test_user_quit_distinguished_from_service_stop(self):
        self.run_tray_code('''
app = m['QApplication']([])
cfg = {'current_profile': 'Quiet', 'profiles': {'Quiet': {}}}
with patch.object(m['config_mod'], 'load_config', return_value=cfg), patch.object(m['config_mod'], 'config_mtime', return_value=1), patch.object(m['QSystemTrayIcon'], 'isSystemTrayAvailable', return_value=False), patch.object(m['subprocess'], 'Popen') as launch:
    tray = m['Tray']()
    tray.stop()
    assert not tray.user_quit
    tray._on_quit()
    assert tray.user_quit
    assert launch.call_args.args[0][-1] == '--quit'
''')


if __name__ == '__main__':
    unittest.main()
