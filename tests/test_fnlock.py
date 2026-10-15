"""Fn Lock event pairing and lifecycle; never opens real input devices."""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class FnLockTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('rogcontrol.fnlock'), 'Fn Lock backend missing')
        from rogcontrol import fnlock
        self.mod = fnlock
        self.events, self.actions, self.toggles = [], [], []
        settings = fnlock.preferences({'fn_lock': {'enabled': True}})
        settings['bindings_enabled'] = True
        self.engine = fnlock.EventRemapper(settings,
            lambda *event: self.events.append(event), self.actions.append, self.toggles.append)

    def feed(self, code, value, typ=1):
        self.engine.feed(SimpleNamespace(type=typ, code=code, value=value))

    def test_remap_release_stays_paired_after_settings_change(self):
        self.feed(59, 1)
        self.engine.update({'enabled': True, 'media': False})
        self.feed(59, 2)
        self.feed(59, 0)
        self.assertEqual(self.events, [(1, 113, 1), (1, 113, 2), (1, 113, 0)])

    def test_modifiers_preserve_shortcut_and_release(self):
        for modifier in [29, 97, 42, 54, 56, 100, 125, 126]:
            self.events.clear()
            self.feed(modifier, 1)
            self.feed(59, 1)
            self.feed(modifier, 0)
            self.feed(59, 0)
            self.assertEqual(self.events, [(1, modifier, 1), (1, 59, 1), (1, modifier, 0), (1, 59, 0)])

    def test_toggle_eats_repeat_and_release_after_meta_release(self):
        for code, value in [(125, 1), (60, 1), (125, 0), (60, 2), (60, 0)]:
            self.feed(code, value)
        self.assertEqual(self.toggles, [False])
        self.assertEqual(self.events, [(1, 125, 1), (1, 125, 0)])

    def test_actions_fire_once_and_asus_scans_override_fallback(self):
        for value in [1, 2, 0]: self.feed(63, value)
        self.feed(4, 0x38, 4)
        for value in [1, 2, 0]: self.feed(190, value)
        self.assertEqual(self.actions, ['profile', 'binding:rog'])
        self.assertEqual(self.events, [])

    def test_binding_scans_match_asus_and_disabled_bindings_pass_through(self):
        for scan, key, name in [(0x8b, 190, 'rog'), (0xb3, 202, 'aura'), (0xae, 203, 'performance')]:
            self.feed(4, scan, 4)
            self.feed(key, 1)
            self.feed(key, 0)
        self.assertEqual(self.actions, ['binding:rog', 'binding:aura', 'binding:performance'])
        self.engine.update({'enabled': True, 'bindings_enabled': False})
        self.feed(4, 0x38, 4)
        self.feed(148, 1)
        self.feed(148, 0)
        self.assertEqual(self.events, [(4, 4, 0x38), (1, 148, 1), (1, 148, 0)])

    def test_unmapped_events_and_scan_forwarding(self):
        self.feed(4, 30, 4)
        self.feed(30, 1)
        self.feed(0, 4, 2)
        self.feed(0, 0, 0)
        self.assertEqual(self.events, [(4, 4, 30), (1, 30, 1), (2, 0, 4), (0, 0, 0)])

    def test_remapped_scan_is_discarded_and_dropped_sync_fails(self):
        self.feed(4, 59, 4)
        self.feed(59, 1)
        self.assertEqual(self.events, [(1, 113, 1)])
        with self.assertRaises(OSError): self.feed(3, 0, 0)

    def test_close_releases_forwarded_and_generated_keys(self):
        self.feed(59, 1)
        self.feed(30, 1)
        self.engine.close()
        self.assertEqual(self.events[-3:], [(1, 113, 0), (1, 30, 0), (0, 0, 0)])

    def test_preferences_reject_invalid_types_and_targets(self):
        prefs = self.mod.preferences({'fn_lock': {'enabled': 'yes', 'media': 'false',
            'map': {'59': 'action:arbitrary', '60': 'key:113', '30': 'action:toggle'}}})
        self.assertIs(prefs['enabled'], False)
        self.assertIs(prefs['media'], True)
        self.assertEqual(prefs['map']['59'], 'key:113')
        self.assertEqual(prefs['map']['60'], 'key:113')
        self.assertNotIn('30', prefs['map'])

    def test_wrapper_preserves_caps_and_grabs_after_virtual_creation(self):
        import evdev
        order = []
        class Device:
            ff_effects_count = 0
            def active_keys(self): return []
            def capabilities(self): return {1: [30, 59], 2: [0], 3: [(0, (0, 0, 100, 0, 0, 1))]}
            def grab(self): order.append('grab')
            def ungrab(self): order.append('ungrab')
        virtual = SimpleNamespace(write=lambda *args: None, close=lambda: order.append('close'))
        def create(device, **kwargs):
            order.append('create')
            caps = device.capabilities()
            self.assertIn(113, caps[1])
            self.assertEqual(caps[2], [0])
            self.assertEqual(caps[3], [(0, (0, 0, 100, 0, 0, 1))])
            return virtual
        with patch.object(evdev.UInput, 'from_device', side_effect=create):
            remapper = self.mod.Remapper(Device(), {'enabled': True}, self.actions.append, self.toggles.append)
            remapper.close()
            remapper.close()
        self.assertEqual(order, ['create', 'grab', 'ungrab', 'close'])

    def test_key_pressed_during_grab_releases_device_and_virtual(self):
        import evdev
        order = []
        states = iter([[], [], [30]])
        device = SimpleNamespace(active_keys=lambda: next(states), ff_effects_count=0,
            grab=lambda: order.append('grab'), ungrab=lambda: order.append('ungrab'))
        virtual = SimpleNamespace(write=lambda *args: None, close=lambda: order.append('close'))
        with patch.object(evdev.UInput, 'from_device', return_value=virtual):
            with self.assertRaises(OSError):
                self.mod.Remapper(device, {'enabled': True}, self.actions.append, self.toggles.append)
        self.assertEqual(order, ['grab', 'ungrab', 'close'])

    def test_cleanup_ungrabs_and_closes_when_releasing_output_fails(self):
        import evdev
        order = []
        device = SimpleNamespace(active_keys=lambda: [], ff_effects_count=0,
            grab=lambda: None, ungrab=lambda: order.append('ungrab'))
        def broken_write(*args): raise OSError('disconnected')
        virtual = SimpleNamespace(write=broken_write, close=lambda: order.append('close'))
        with patch.object(evdev.UInput, 'from_device', return_value=virtual):
            remapper = self.mod.Remapper(device, {'enabled': True}, self.actions.append, self.toggles.append)
            with self.assertRaises(OSError): remapper.close()
        self.assertEqual(order, ['ungrab', 'close'])

    def test_multiple_sources_sharing_target_release_only_after_last_source(self):
        self.engine.update({'enabled': True, 'map': {'60': 'key:113'}})
        for code, value in [(59, 1), (60, 1), (59, 0), (60, 0)]: self.feed(code, value)
        self.assertEqual(self.events, [(1, 113, 1), (1, 113, 0)])

    def test_held_keys_refuse_grab(self):
        import evdev
        device = SimpleNamespace(active_keys=lambda: [30])
        with patch.object(evdev.UInput, 'from_device') as create:
            with self.assertRaises(OSError):
                self.mod.Remapper(device, {'enabled': True}, self.actions.append, self.toggles.append)
            create.assert_not_called()

    def test_led_feedback_forwarded_without_other_output_events(self):
        import evdev
        from unittest.mock import Mock
        device = SimpleNamespace(active_keys=lambda: [], ff_effects_count=0,
            grab=Mock(), ungrab=Mock(), write=Mock(), syn=Mock())
        virtual = SimpleNamespace(write=Mock(), close=Mock(), read=lambda: [
            SimpleNamespace(type=17, code=1, value=1),
            SimpleNamespace(type=0, code=0, value=0)])
        with patch.object(evdev.UInput, 'from_device', return_value=virtual):
            remapper = self.mod.Remapper(device, {'enabled': True}, self.actions.append, self.toggles.append)
            remapper.read_feedback()
            remapper.close()
        device.write.assert_called_once_with(17,1,1)
        device.syn.assert_called_once()
