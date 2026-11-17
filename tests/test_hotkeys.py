import unittest
from unittest.mock import patch
from rogcontrol import hotkeys


class HotkeyTests(unittest.TestCase):
    def test_opt_in_and_action_allowlist(self):
        self.assertFalse(hotkeys.preferences({})['enabled'])
        cfg = {'key_bindings': {'enabled': True, 'rog': 'shell:bad'}}
        self.assertEqual(hotkeys.preferences(cfg)['rog'], 'none')
        self.assertEqual(hotkeys.preferences({})['performance'], 'profile')

    def test_scan_precedence_and_releases_clear_scan(self):
        d = hotkeys.Decoder()
        self.assertIsNone(d.feed(4, 4, 139))
        self.assertEqual(d.feed(1, 202, 1), 'rog')
        d.feed(4, 4, 179)
        d.feed(1, 202, 0)
        self.assertIsNone(d.feed(1, 30, 1))
        self.assertEqual(d.feed(1, 203, 1), 'performance')
        self.assertIsNone(d.feed(1, 203, 2))

    def test_dropped_and_frame_boundaries(self):
        d = hotkeys.Decoder()
        d.feed(4, 4, 56)
        d.feed(0, 0, 0)
        self.assertIsNone(d.feed(1, 30, 1))
        d.feed(0, 3, 0)
        self.assertIsNone(d.feed(1, 148, 1))
        d.feed(0, 0, 0)
        self.assertEqual(d.feed(1, 148, 1), 'rog')

    def test_only_supported_physical_devices(self):
        self.assertTrue(hotkeys.eligible('Asus WMI hotkeys', 0, 0))
        self.assertTrue(hotkeys.eligible('ASUSTek N-KEY Device', 0x0b05, 0x19b6))
        self.assertFalse(hotkeys.eligible('ROG Control virtual keyboard', 1, 1))
        self.assertFalse(hotkeys.eligible('USB keyboard', 0x046d, 0x19b6))

    def test_duplicate_sources_not_double_fired(self):
        d = hotkeys.Deduplicator()
        self.assertTrue(d.accept('rog', 'hid', 10))
        self.assertFalse(d.accept('rog', 'wmi', 10.05))
        self.assertTrue(d.accept('rog', 'hid', 10.1))
        self.assertTrue(d.accept('rog', 'wmi', 11))

    def test_session_fails_closed(self):
        properties = 'Active=yes\nLockedHint=no\nRemote=no\nType=wayland\nUser=1000\nSeat=seat0\n'
        with patch.object(hotkeys, '_output', side_effect=['11', properties]), patch('os.getuid', return_value=1000):
            self.assertTrue(hotkeys.session_active())
        with patch.object(hotkeys, '_output', side_effect=['11', properties.replace('LockedHint=no', 'LockedHint=yes')]):
            self.assertFalse(hotkeys.session_active())
        with patch.object(hotkeys, '_output', return_value=''):
            self.assertFalse(hotkeys.session_active())

    def test_command_does_not_accept_shell(self):
        self.assertIsNone(hotkeys.command('arbitrary; shell'))
        self.assertEqual(hotkeys.command('profile')[-2:], ['profile', 'next'])
        self.assertEqual(hotkeys.command('toggle')[-1], '--toggle')

class WorkerTests(unittest.TestCase):
    def test_disabled_worker_never_opens_input(self):
        import threading
        from unittest.mock import Mock
        stop = threading.Event()
        def wait(_): stop.set()
        with patch.object(hotkeys.config, 'load_config', return_value={}), \
             patch.object(hotkeys, 'discover') as discover, \
             patch.object(hotkeys, 'set_status'), \
             patch.object(hotkeys.SessionMonitor, 'run'), \
             patch.object(stop, 'wait', side_effect=wait):
            hotkeys.run(stop)
        discover.assert_not_called()

    def test_live_settings_enable_and_disable_closes_device(self):
        import threading
        import evdev
        from unittest.mock import Mock
        stop = threading.Event()
        device = Mock(path='/dev/input/event99')
        configs = iter([{'key_bindings': {'enabled': True}}, {}])
        def load():
            try: return next(configs)
            except StopIteration:
                stop.set(); return {}
        with patch.object(hotkeys.config, 'load_config', side_effect=load), \
             patch.object(hotkeys, 'discover', return_value=[(device.path, 'Asus WMI hotkeys', False)]), \
             patch.object(hotkeys, 'set_status'), \
             patch.object(hotkeys.SessionMonitor, 'run'), \
             patch.object(hotkeys.SessionMonitor, 'allowed', return_value=True), \
             patch.object(hotkeys.time, 'monotonic', side_effect=range(10, 100)), \
             patch.object(hotkeys.select, 'select', return_value=([],[],[])), \
             patch.object(stop, 'wait'), \
             patch.object(evdev, 'InputDevice', return_value=device) as opened:
            hotkeys.run(stop)
        opened.assert_called_once_with(device.path)
        device.close.assert_called_once()

    def test_reconnect_retries_one_device_without_closing_working_hotkeys(self):
        self._reconnect_worker([0, 2, 4], recover=True)

    def test_persistent_input_permission_failure_remains_visible(self):
        self._reconnect_worker([0, 5, 11], recover=False)

    def _reconnect_worker(self, times, recover):
        import evdev
        import threading
        from unittest.mock import Mock
        stop = threading.Event()
        keyboard = Mock(path='/dev/input/event4', fd=4)
        wmi = Mock(path='/dev/input/event6', fd=6)
        clock = [times[0]]
        attempts = []
        status = []
        def open_device(path):
            attempts.append(path)
            if path == keyboard.path and (not recover or clock[0] < times[-1]):
                raise PermissionError(13, 'Permission denied', path)
            return keyboard if path == keyboard.path else wmi
        def select_ready(*args):
            # The accessible WMI device must remain open throughout the retry.
            self.assertEqual(wmi.close.call_count, 0)
            if len(times) > 1:
                times.pop(0)
                clock[0] = times[0]
            else:
                stop.set()
            return [], [], []
        with patch.object(hotkeys.config, 'load_config', return_value={'key_bindings': {'enabled': True}}), \
             patch.object(hotkeys, 'discover', return_value=[(keyboard.path, 'N-KEY', True), (wmi.path, 'Asus WMI hotkeys', False)]), \
             patch.object(hotkeys, 'set_status', side_effect=status.append), \
             patch.object(hotkeys.SessionMonitor, 'run'), \
             patch.object(hotkeys.ToggleWriter, 'run'), \
             patch.object(hotkeys.SessionMonitor, 'allowed', return_value=True), \
             patch.object(hotkeys.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(hotkeys.select, 'select', side_effect=select_ready), \
             patch.object(stop, 'wait', side_effect=lambda _: stop.set()), \
             patch.object(evdev, 'InputDevice', side_effect=open_device), \
             patch.object(hotkeys.hardware, 'log') as log:
            hotkeys.run(stop)
        self.assertIn(wmi.path, attempts)
        self.assertEqual(attempts.count(wmi.path), 1)
        self.assertEqual(attempts.count(keyboard.path), 3)
        if recover:
            log.assert_not_called()
            self.assertIn('Listening', status[-1])
        else:
            self.assertEqual(log.call_count, 1)
            self.assertIn('Permission denied', status[-1])

    def test_session_status_expires(self):
        monitor = hotkeys.SessionMonitor()
        monitor.state = True, 10
        with patch.object(hotkeys.time, 'monotonic', return_value=15):
            self.assertFalse(monitor.allowed())

class ToggleWriterTests(unittest.TestCase):
    def test_submit_never_writes_on_input_thread(self):
        writer = hotkeys.ToggleWriter()
        with patch.object(hotkeys.config, 'update_config') as save:
            writer.submit(False)
            self.assertFalse(writer.overlay({'media': True})['media'])
        save.assert_not_called()

    def test_background_save_preserves_other_fields_and_latest_toggle(self):
        import threading
        writer = hotkeys.ToggleWriter()
        writer.submit(True)
        writer.submit(False)
        stop = threading.Event()
        cfg = {'fn_lock': {'enabled': True}, 'charge_limit': 70}
        def save(callback):
            callback(cfg)
            stop.set()
        with patch.object(hotkeys.config, 'update_config', side_effect=save):
            writer.run(stop)
        self.assertFalse(cfg['fn_lock']['media'])
        self.assertTrue(cfg['fn_lock']['enabled'])
        self.assertEqual(cfg['charge_limit'], 70)
        self.assertFalse(writer.overlay({'media': True})['media'])
        self.assertIsNone(writer.pending)

class RunnerTests(unittest.TestCase):
    def test_starting_window_does_not_block_later_hardware_actions(self):
        from unittest.mock import Mock
        runner = hotkeys.ActionRunner(lambda: True)
        gui = Mock(); gui.poll.return_value = None
        action = Mock(); action.poll.return_value = None
        with patch.object(hotkeys.subprocess, 'Popen', side_effect=[gui, action]) as spawn:
            runner.execute('toggle')
            runner.execute('aura')
        self.assertEqual(spawn.call_count, 2)
        self.assertIs(runner.process, action)
        self.assertIn(gui, runner.gui_processes)

    def test_profile_requests_are_not_dropped_while_other_action_runs(self):
        from unittest.mock import Mock
        runner = hotkeys.ActionRunner(lambda: True)
        running = Mock(); running.poll.return_value = None
        with patch.object(hotkeys.subprocess, 'Popen', return_value=running) as spawn:
            runner.execute('aura')
            runner.execute('profile', origin='bindings')
            runner.execute('profile', origin='bindings')
        self.assertEqual(spawn.call_count, 3)
        self.assertEqual(spawn.call_args.args[0][-2:], ['--origin', 'bindings'])
