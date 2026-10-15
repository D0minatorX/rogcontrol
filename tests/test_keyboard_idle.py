"""Idle timeout transitions with fake hardware and real runtime state."""
import tempfile
import unittest
from pathlib import Path
from rogcontrol import keyboard_idle


class KeyboardIdleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'idle.json'
        self.level = 2
        self.writes = []
        self.cfg = {'kbd_idle_timeout_ac_seconds': 60, 'kbd_idle_timeout_battery_seconds': 15,
                    'kbd_brightness': 2}
        self.worker = keyboard_idle.IdleController(
            read_brightness=lambda: self.level, write_brightness=self.write,
            state_path=self.path)

    def write(self, level):
        self.writes.append(level)
        self.level = level
        return True, ''

    def test_ac_battery_threshold_and_restore(self):
        self.worker.tick(self.cfg, True, 20)
        self.assertEqual(self.level, 2)
        self.worker.tick(self.cfg, False, 20)
        self.assertEqual(self.level, 0)
        self.assertTrue(keyboard_idle.is_dimmed(self.path))
        self.worker.tick(self.cfg, False, 25)
        self.assertEqual(self.writes, [0])
        self.worker.tick(self.cfg, False, 0)
        self.assertEqual(self.level, 2)
        self.assertFalse(keyboard_idle.is_dimmed(self.path))
        self.assertEqual(self.cfg['kbd_brightness'], 2)

    def test_disable_unavailable_and_unknown_power_restore(self):
        for ac, idle, value in [(False, 0, 0), (False, None, 15),
                                (None, 30, 15)]:
            self.cfg['kbd_idle_timeout_battery_seconds'] = 15
            self.worker.tick(self.cfg, False, 20)
            self.cfg['kbd_idle_timeout_battery_seconds'] = value
            self.worker.tick(self.cfg, ac, idle)
            self.assertEqual(self.level, 2)

    def test_defaults_and_manual_off_never_turn_light_on(self):
        self.worker.tick({}, False, 900)
        self.assertEqual(self.writes, [])
        self.level = 0
        self.worker.tick(self.cfg, False, 20)
        self.worker.tick(self.cfg, False, 0)
        self.assertEqual(self.writes, [])

    def test_explicit_change_while_dimmed_wins(self):
        self.worker.tick(self.cfg, False, 20)
        self.cfg['kbd_brightness'] = 0
        self.worker.tick(self.cfg, False, 0)
        self.assertEqual(self.level, 0)
        self.assertFalse(keyboard_idle.is_dimmed(self.path))

    def test_external_brightness_while_dimmed_is_preserved(self):
        self.worker.tick(self.cfg, False, 20)
        self.level = 3
        self.worker.tick(self.cfg, False, 0)
        self.assertEqual(self.level, 3)

    def test_restart_recovers_original_brightness(self):
        self.worker.tick(self.cfg, False, 20)
        restarted = keyboard_idle.IdleController(
            read_brightness=lambda: self.level, write_brightness=self.write,
            state_path=self.path)
        restarted.tick(self.cfg, False, 0)
        self.assertEqual(self.level, 2)

    def test_failed_restore_keeps_state_for_retry(self):
        self.worker.tick(self.cfg, False, 20)
        self.worker.write_brightness = lambda value: (False, 'failed')
        self.worker.tick(self.cfg, False, 0)
        self.assertTrue(keyboard_idle.is_dimmed(self.path))
        self.worker.write_brightness = self.write
        self.worker.tick(self.cfg, False, 0)
        self.assertEqual(self.level, 2)

    def test_failed_dim_clears_marker(self):
        self.worker.write_brightness = lambda value: (False, 'failed')
        self.worker.tick(self.cfg, False, 20)
        self.assertFalse(keyboard_idle.is_dimmed(self.path))

    def test_helper_timeout_after_dimming_keeps_restore_state(self):
        def interrupted_write(value):
            self.level = value
            return False, "timed out"
        self.worker.write_brightness = interrupted_write
        self.worker.tick(self.cfg, False, 20)
        self.assertTrue(keyboard_idle.is_dimmed(self.path))
        self.worker.write_brightness = self.write
        self.worker.tick(self.cfg, False, 0)
        self.assertEqual(self.level, 2)

    def test_invalid_timeouts_disable_instead_of_crashing(self):
        for invalid in (None, 'bad', -2, True):
            self.cfg['kbd_idle_timeout_battery_seconds'] = invalid
            self.worker.tick(self.cfg, False, 200)
        self.assertEqual(self.writes, [])


class DesktopIdleTests(unittest.TestCase):
    def test_mutter_milliseconds_and_kde_fallback(self):
        from unittest.mock import patch, Mock
        from gi.repository import Gio, GLib
        connection = Mock()
        connection.call_sync.side_effect = [RuntimeError('no Mutter'), GLib.Variant('(u)', (12345,))]
        with patch.object(Gio, 'bus_get_sync', return_value=connection):
            backend = keyboard_idle.DesktopIdle()
            self.assertEqual(backend.read(), 12.345)
            self.assertEqual(backend.backend[0], 'KDE X11')
            self.assertEqual(connection.call_sync.call_args.args[3], 'GetSessionIdleTime')
            self.assertEqual(connection.call_sync.call_args.args[7], 750)

    def test_unsupported_desktop_does_not_report_zero_idle(self):
        from unittest.mock import patch, Mock
        from gi.repository import Gio
        connection = Mock()
        connection.call_sync.side_effect = RuntimeError('NotSupported')
        with patch.object(Gio, 'bus_get_sync', return_value=connection), \
                patch.object(keyboard_idle.wayland_idle, 'WaylandIdle',
                             side_effect=OSError('protocol unavailable')):
            self.assertIsNone(keyboard_idle.DesktopIdle().read())
            self.assertIn('unavailable', keyboard_idle.support_status())


class WaylandFallbackTests(unittest.TestCase):
    def test_dbus_failure_uses_wayland_threshold_and_reuses_connection(self):
        from unittest.mock import patch, Mock
        from gi.repository import Gio
        from rogcontrol import wayland_idle
        connection = Mock()
        connection.call_sync.side_effect = RuntimeError('NotSupported')
        wire = Mock(version=2)
        wire.read.side_effect = [0, 15, 0]
        with patch.object(Gio, 'bus_get_sync', return_value=connection), \
                patch.object(wayland_idle, 'WaylandIdle', return_value=wire) as factory:
            idle = keyboard_idle.DesktopIdle()
            self.assertEqual(idle.read(15), 0)
            self.assertEqual(idle.read(15), 15)
            self.assertEqual(idle.read(60), 0)
            factory.assert_called_once()
            self.assertIn('Wayland', idle.backend[0])
            idle.close()
            wire.close.assert_called_once()

    def test_disconnect_returns_unknown_and_retries_after_backoff(self):
        from unittest.mock import patch, Mock
        from gi.repository import Gio
        from rogcontrol import wayland_idle
        connection = Mock()
        connection.call_sync.side_effect = RuntimeError('NotSupported')
        failed = Mock(version=2)
        failed.read.side_effect = OSError('disconnected')
        recovered = Mock(version=1)
        recovered.read.return_value = 0
        with patch.object(Gio, 'bus_get_sync', return_value=connection), \
                patch.object(wayland_idle, 'WaylandIdle', side_effect=[failed, recovered]) as factory, \
                patch.object(keyboard_idle, 'time') as clock:
            clock.monotonic.return_value = 10
            idle = keyboard_idle.DesktopIdle()
            self.assertIsNone(idle.read(15))
            failed.close.assert_called_once()
            self.assertIsNone(idle.read(15))
            self.assertEqual(factory.call_count, 1)
            clock.monotonic.return_value = 50
            self.assertEqual(idle.read(15), 0)
            idle.close()
