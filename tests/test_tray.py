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
import time
from threading import Event, get_ident
def wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline, 'completion timed out'
        app.processEvents()
        time.sleep(0.001)
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
    wait_until(lambda: not tray._request_pending)
    assert 'worker unavailable' in tray.indicator.toolTip()
    tray.stop()
''')

    def test_blocked_request_keeps_qt_responsive_and_completion_on_gui_thread(self):
        self.run_tray_code('''
app = m['QApplication']([])
cfg = {'current_profile': 'Quiet', 'profiles': {'Quiet': {}, 'Performance': {}}}
entered, release = Event(), Event()
main_thread = get_ident()
load_threads, request_threads, completion_threads = [], [], []
def load():
    load_threads.append(get_ident())
    return cfg
def request(*args, **kwargs):
    request_threads.append(get_ident())
    entered.set()
    assert release.wait(3)
class ObservedTray(m['Tray']):
    def _on_profile_completed(self, *args):
        completion_threads.append(get_ident())
        super()._on_profile_completed(*args)
with patch.object(m['config_mod'], 'load_config', side_effect=load), patch.object(m['config_mod'], 'config_mtime', return_value=1), patch.object(m['QSystemTrayIcon'], 'isSystemTrayAvailable', return_value=False), patch.object(m['profile_requests'], 'request_profile', side_effect=request) as requested:
    tray = ObservedTray()
    tray._items['Performance'].trigger()
    wait_until(entered.is_set)
    tray._items['Performance'].trigger()
    heartbeat = []
    m['QTimer'].singleShot(0, lambda: heartbeat.append(True))
    wait_until(lambda: bool(heartbeat))
    assert tray._request_pending and requested.call_count == 1
    assert load_threads == [main_thread, request_threads[0]]
    assert request_threads[0] != main_thread
    release.set()
    wait_until(lambda: not tray._request_pending)
    assert completion_threads == [main_thread]
    assert 'requested Performance' in tray.indicator.toolTip()
    assert tray._items['Quiet'].isChecked()
    tray.stop()
''')

    def test_stop_during_request_ignores_late_completion(self):
        self.run_tray_code('''
app = m['QApplication']([])
cfg = {'current_profile': 'Quiet', 'profiles': {'Quiet': {}, 'Performance': {}}}
entered, release, returned = Event(), Event(), Event()
def request(*args, **kwargs):
    entered.set()
    assert release.wait(3)
    returned.set()
with patch.object(m['config_mod'], 'load_config', return_value=cfg), patch.object(m['config_mod'], 'config_mtime', return_value=1), patch.object(m['QSystemTrayIcon'], 'isSystemTrayAvailable', return_value=False), patch.object(m['profile_requests'], 'request_profile', side_effect=request) as requested:
    tray = m['Tray']()
    tray._on_profile('Performance')
    wait_until(entered.is_set)
    tooltip = tray.indicator.toolTip()
    tray.stop()
    tray._on_profile('Performance')
    release.set()
    wait_until(returned.is_set)
    for _ in range(50):
        app.processEvents()
        time.sleep(0.001)
    assert tray.indicator.toolTip() == tooltip
    assert requested.call_count == 1
''')

    def test_process_exits_while_profile_request_is_blocked(self):
        self.run_tray_code('''
app = m['QApplication']([])
cfg = {'current_profile': 'Quiet', 'profiles': {'Quiet': {}, 'Performance': {}}}
entered = Event()
def request(*args, **kwargs):
    entered.set()
    Event().wait(60)
with patch.object(m['config_mod'], 'load_config', return_value=cfg), patch.object(m['config_mod'], 'config_mtime', return_value=1), patch.object(m['QSystemTrayIcon'], 'isSystemTrayAvailable', return_value=False), patch.object(m['profile_requests'], 'request_profile', side_effect=request):
    tray = m['Tray']()
    tray._on_profile('Performance')
    wait_until(entered.is_set)
    m['QTimer'].singleShot(0, tray.stop)
    app.exec()
    assert tray._closed
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
