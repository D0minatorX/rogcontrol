"""Regression checks for desktop discovery and suspend/resume display reads."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gi.repository import Gio, GLib
from rogcontrol import display_refresh as refresh


class DisplayRecoveryTests(unittest.TestCase):
    def test_session_desktop_fallback(self):
        for key, value, expected in [('XDG_SESSION_DESKTOP', 'gnome', 'gnome'),
                                     ('DESKTOP_SESSION', '/usr/share/xsessions/plasma', 'kde')]:
            with self.subTest(key=key), patch.dict(os.environ, {key: value}, clear=True):
                self.assertEqual(refresh.backend_name(), expected)

    def test_explicit_unsupported_desktop_is_not_overridden(self):
        with patch.dict(os.environ, {'XDG_CURRENT_DESKTOP': 'sway',
                                    'XDG_SESSION_DESKTOP': 'gnome'}, clear=True):
            self.assertIsNone(refresh.backend_name())

    def test_missing_environment_detects_running_compositor(self):
        connection = Mock()
        connection.call_sync.return_value = GLib.Variant('(as)', (['org.gnome.Mutter.DisplayConfig'],))
        with patch.dict(os.environ, {'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/test'}, clear=True), \
             patch.object(Gio, 'bus_get_sync', return_value=connection):
            self.assertEqual(refresh.backend_name(), 'gnome')

    def test_resume_timeout_retries_state_read(self):
        connection = Mock()
        connection.call_sync.side_effect = [
            GLib.Error.new_literal(Gio.io_error_quark(), 'Timeout was reached', Gio.IOErrorEnum.TIMED_OUT),
            GLib.Variant('(s)', ('recovered state',))]
        with patch.object(Gio, 'bus_get_sync', return_value=connection):
            self.assertEqual(refresh._gnome_state(), ('recovered state',))
        self.assertEqual(connection.call_sync.call_count, 2)
        self.assertTrue(all(call.args[3] == 'GetCurrentState'
                            for call in connection.call_sync.call_args_list))

    def test_persistent_timeout_is_bounded_and_permission_errors_not_retried(self):
        for code, attempts in [(Gio.IOErrorEnum.TIMED_OUT, 2),
                               (Gio.IOErrorEnum.PERMISSION_DENIED, 1)]:
            connection = Mock()
            connection.call_sync.side_effect = GLib.Error.new_literal(
                Gio.io_error_quark(), 'Display unavailable', code)
            with self.subTest(code=code), patch.object(Gio, 'bus_get_sync', return_value=connection):
                with self.assertRaises(GLib.Error):
                    refresh._gnome_state()
                self.assertEqual(connection.call_sync.call_count, attempts)

    def test_persistent_resume_timeout_keeps_pending_restore(self):
        connection = Mock()
        connection.call_sync.side_effect = GLib.Error.new_literal(
            Gio.io_error_quark(), 'Timeout was reached', Gio.IOErrorEnum.TIMED_OUT)
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'refresh.json'
            saved = '{"restore_hz": 165, "panels": ["internal"]}'
            state.write_text(saved)
            with patch.dict(os.environ, {'XDG_CURRENT_DESKTOP': 'GNOME'}), \
                 patch.object(refresh, 'availability', return_value=(True, 'GNOME')), \
                 patch.object(Gio, 'bus_get_sync', return_value=connection), \
                 patch.object(refresh, '_run') as write, \
                 patch.object(refresh, '_log_failure') as log:
                result = refresh.tick({'auto_display_refresh': True}, 'ac', state_path=state)
            self.assertIn('Timeout', result)
            self.assertEqual(state.read_text(), saved)
            write.assert_not_called()
            log.assert_called_once()

    def test_command_failure_retains_compositor_diagnostic(self):
        failure = subprocess.CalledProcessError(1, ['gdctl', 'set'],
                                                stderr='compositor rejected layout')
        with patch.object(subprocess, 'run', side_effect=failure):
            with self.assertRaisesRegex(Exception, 'compositor rejected layout'):
                refresh._run(['gdctl', 'set'])



if __name__ == '__main__':
    unittest.main()
