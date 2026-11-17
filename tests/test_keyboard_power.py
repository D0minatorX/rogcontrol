"""Power policies use saved desired state, not invented hardware readback."""
import importlib.util
import unittest
from unittest.mock import Mock


class KeyboardPowerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('rogcontrol.keyboard_power'))
        from rogcontrol import keyboard_power
        self.module = keyboard_power
        self.cfg = {'keyboard_power': keyboard_power.preferences({})}
        self.cfg['keyboard_power']['enabled'] = True
        self.writer = Mock(return_value=(True, 'sent'))
        self.controller = keyboard_power.Controller(write=self.writer, supported=lambda: True)

    def test_battery_override_changes_only_awake_bits_and_restores_on_ac(self):
        self.cfg['keyboard_power']['keyboard']['battery'] = False
        self.cfg['keyboard_power']['lightbar']['battery'] = False
        self.controller.tick(self.cfg, True, now=0)
        self.writer.assert_called_with(15, 15)
        self.controller.tick(self.cfg, False, now=1)
        self.writer.assert_called_with(13, 13)
        self.controller.tick(self.cfg, True, now=2)
        self.writer.assert_called_with(15, 15)

    def test_zone_settings_are_independent(self):
        self.cfg['keyboard_power']['keyboard'].update(boot=False, sleep=False)
        self.cfg['keyboard_power']['lightbar'].update(shutdown=False, awake=False)
        self.controller.tick(self.cfg, True, now=0)
        self.writer.assert_called_with(10, 5)
        self.controller.tick(self.cfg, False, now=1)
        self.writer.assert_called_with(10, 7)

    def test_disabled_unknown_or_unsupported_never_write(self):
        self.controller.tick({}, True, now=0)
        self.controller.tick(self.cfg, None, now=1)
        self.controller.supported = lambda: False
        self.controller.tick(self.cfg, False, now=2)
        self.writer.assert_not_called()

    def test_failure_retries_and_unchanged_policy_does_not_spam(self):
        self.writer.side_effect = [(False, 'busy'), (True, 'sent'), (True, 'sent')]
        self.assertFalse(self.controller.tick(self.cfg, True, now=0)[0])
        self.controller.tick(self.cfg, True, now=5)
        self.controller.tick(self.cfg, True, now=10)
        self.assertEqual(self.writer.call_count, 2)
        self.controller.tick(self.cfg, True, now=66)
        self.assertEqual(self.writer.call_count, 3)

    def test_reconnect_grace_reapplies_same_policy_without_false_warning(self):
        controller = self.module.Controller(write=self.writer, supported=lambda: True,
                                            reconnect_grace=10)
        controller.tick(self.cfg, True, now=0)
        controller.supported = lambda: False
        self.assertTrue(controller.tick(self.cfg, True, now=1)[0])
        self.assertTrue(controller.tick(self.cfg, True, now=6)[0])
        controller.supported = lambda: True
        self.assertTrue(controller.tick(self.cfg, True, now=7)[0])
        self.assertEqual(self.writer.call_count, 2)

    def test_persistent_missing_device_warns_and_recovery_resets_grace(self):
        controller = self.module.Controller(write=self.writer, supported=lambda: False,
                                            reconnect_grace=10)
        self.assertTrue(controller.tick(self.cfg, True, now=0)[0])
        self.assertFalse(controller.tick(self.cfg, True, now=10)[0])
        self.writer.assert_not_called()
        controller.supported = lambda: True
        self.assertTrue(controller.tick(self.cfg, True, now=11)[0])
        controller.supported = lambda: False
        self.assertTrue(controller.tick(self.cfg, True, now=12)[0])

    def test_malformed_config_does_not_enable_management(self):
        for value in (True, 'bad', {'enabled': 'true'}, {'enabled': True, 'keyboard': []}):
            self.controller.tick({'keyboard_power': value}, True, now=0)
        self.writer.assert_not_called()

    def test_disable_reenable_and_restart_reapply(self):
        self.controller.tick(self.cfg, False, now=0)
        self.controller.tick({}, False, now=1)
        self.controller.tick(self.cfg, False, now=2)
        restarted = self.module.Controller(write=self.writer, supported=lambda: True)
        restarted.tick(self.cfg, False, now=3)
        self.assertEqual(self.writer.call_count, 3)

class BackgroundPowerTests(unittest.TestCase):
    def test_background_applies_battery_override_without_profile_switching(self):
        from unittest.mock import patch
        from rogcontrol import daily_automation, keyboard_power
        cfg = {'keyboard_power': keyboard_power.preferences({}),
               'ac_profile': None, 'battery_profile': None}
        cfg['keyboard_power']['enabled'] = True
        cfg['keyboard_power']['keyboard']['battery'] = False
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        with patch.object(daily_automation.config, 'load_config', return_value=cfg), \
                patch.object(daily_automation.charge_once, 'tick', return_value=(True, '')), \
                patch.object(daily_automation.hardware, 'read_power_source', return_value=(True, 'mains')), \
                patch.object(daily_automation.hardware, 'is_ac_connected', side_effect=[True, False]), \
                patch.object(daily_automation.display_refresh, 'tick'), \
                patch.object(keyboard_power, 'supported', return_value=True), \
                patch.object(keyboard_power, 'write_masks', return_value=(True, 'sent')) as writer:
            daily_automation.run(stop)
        self.assertEqual([call.args for call in writer.call_args_list], [(15, 15), (13, 15)])
