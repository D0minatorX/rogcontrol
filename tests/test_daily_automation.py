"""Background convenience controls remain opt-in across config migration."""
import tempfile
import unittest
import importlib.util
from unittest.mock import Mock, patch
from pathlib import Path

from rogcontrol import config


class AutomationDefaultsTests(unittest.TestCase):
    def test_old_config_migrates_without_enabling_automation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'config.json')
            config.save_config({'charge_limit': 80, 'kbd_brightness': 2,
                                'kbd_timeout_ac': 1, 'kbd_timeout_battery': 1}, path)
            restored = config.load_config(path=path)
            self.assertEqual(restored.get('kbd_idle_timeout_ac_seconds'), 0)
            self.assertEqual(restored.get('kbd_idle_timeout_battery_seconds'), 0)
            self.assertIs(restored.get('auto_display_refresh'), False)
            self.assertEqual(restored['charge_limit'], 80)
            self.assertEqual(restored['kbd_brightness'], 2)

    def test_saved_preferences_survive_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'config.json')
            values = {'kbd_idle_timeout_ac_seconds': 300, 'kbd_idle_timeout_battery_seconds': 30,
                      'auto_display_refresh': True}
            config.save_config(values, path)
            restored = config.load_config(path=path)
            for key, value in values.items():
                self.assertEqual(restored[key], value)


class WorkerTests(unittest.TestCase):
    def test_charge_failure_does_not_skip_display_and_next_poll_retries(self):
        self.assertIsNotNone(importlib.util.find_spec('rogcontrol.daily_automation'))
        from rogcontrol import daily_automation
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        with patch.object(daily_automation.charge_once, 'tick',
                          side_effect=[RuntimeError('offline'), (True, '')]) as charge, \
                patch.object(daily_automation.display_refresh, 'tick') as display, \
                patch.object(daily_automation.config, 'load_config', return_value={}), \
                patch.object(daily_automation.hardware, 'read_power_source', return_value=(True, 'usb')), \
                patch.object(daily_automation.hardware, 'log'):
            daily_automation.run(stop)
        self.assertEqual(charge.call_count, 2)
        self.assertEqual(display.call_count, 2)
        display.assert_called_with({}, 'usbc')
