"""Charge override lifecycle on real config files and simulated firmware."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rogcontrol import config


class ChargeOnceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('rogcontrol.charge_once'),
                             'charge-once lifecycle is not implemented')
        self.feature = importlib.import_module('rogcontrol.charge_once')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'config.json')
        self.state_path = str(Path(self.tmp.name) / 'state.json')
        self.supply_path = Path(self.tmp.name) / 'power_supply'
        self.supply_path.mkdir()
        p = patch('rogcontrol.hardware.POWER_SUPPLY_DIR', str(self.supply_path))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(self.feature, 'STATE_PATH', self.state_path)
        p.start()
        self.addCleanup(p.stop)
        config.save_config({'charge_limit': 80}, self.path)
        self.ac = True
        self.percent = 55
        self.limit = 80
        self.fail = False
        self.during_write = None
        for name, replacement in (
            ('is_ac_connected', lambda: self.ac),
            ('read_battery', lambda: (self.percent, False)),
            ('read_charge_limit', lambda: self.limit),
            ('run_helper', self.write),
        ):
            p = patch.object(self.feature.hardware, name, replacement)
            p.start()
            self.addCleanup(p.stop)

    def write(self, command, value):
        self.assertEqual(command, 'charge')
        if self.during_write:
            action, self.during_write = self.during_write, None
            action()
        if self.fail:
            return False, 'simulated firmware failure'
        self.limit = value
        return True, ''

    def saved(self):
        return json.loads(Path(self.path).read_text())

    def tick(self):
        return self.feature.tick(config_path=self.path)

    def start(self):
        return self.feature.start(config_path=self.path)

    def test_start_preserves_normal_limit_and_restores_on_full_after_restart(self):
        self.assertTrue(self.start()[0])
        self.assertEqual(self.limit, 100)
        self.assertEqual(self.saved()['charge_limit'], 80)
        # A new process reads the same persistent state, without cached state.
        self.percent = 100
        self.assertTrue(self.tick()[0])
        self.assertEqual(self.limit, 80)
        self.assertIsNone(self.feature.read_state())

    def test_waits_for_ac_then_restores_on_unplug(self):
        self.ac = False
        self.start()
        self.tick()
        self.assertEqual(self.limit, 100)
        self.assertIsNotNone(self.feature.read_state())
        self.ac = True
        self.tick()
        self.ac = False
        self.tick()
        self.assertEqual(self.limit, 80)
        self.assertIsNone(self.feature.read_state())

    def test_failed_start_retries_and_failed_restore_survives_restart(self):
        self.fail = True
        self.assertFalse(self.start()[0])
        self.assertIsNotNone(self.feature.read_state())
        self.fail = False
        self.assertTrue(self.tick()[0])
        self.assertEqual(self.limit, 100)
        self.fail = True
        self.assertFalse(self.feature.cancel(config_path=self.path)[0])
        self.assertTrue(self.feature.read_state()['restoring'])
        self.assertEqual(self.feature.effective_limit(self.saved()), 80)
        self.fail = False
        self.assertTrue(self.tick()[0])
        self.assertEqual(self.limit, 80)
        self.assertIsNone(self.feature.read_state())

    def test_latest_limit_restored_and_unrelated_edit_during_write_preserved(self):
        self.start()
        config.update_config(lambda cfg: cfg.update(charge_limit=65), self.path)
        self.during_write = lambda: config.update_config(
            lambda cfg: cfg.update(kbd_brightness=1), self.path)
        self.feature.cancel(config_path=self.path)
        self.assertEqual(self.limit, 65)
        self.assertEqual(self.saved()['kbd_brightness'], 1)

    def test_firmware_clamping_is_failure_and_is_retried(self):
        with patch.object(self.feature.hardware, 'run_helper', lambda *args: (True, '')):
            self.assertFalse(self.start()[0])
        self.assertIsNotNone(self.feature.read_state())
        self.tick()
        self.assertEqual(self.limit, 100)

    def test_idle_tick_leaves_normal_limit_alone_without_creating_override(self):
        self.limit = 100
        self.assertTrue(self.tick()[0])
        self.assertEqual(self.limit, 100)
        self.assertIsNone(self.feature.read_state())

    def test_normal_limit_edit_during_override_applies_only_after_cancel(self):
        self.start()
        self.assertTrue(hasattr(self.feature, 'set_limit'), 'override-aware limit edit missing')
        self.assertTrue(self.feature.set_limit(65, config_path=self.path)[0])
        self.assertEqual(self.limit, 100)
        self.assertEqual(self.saved()['charge_limit'], 65)
        self.feature.cancel(config_path=self.path)
        self.assertEqual(self.limit, 65)

    def test_profile_apply_does_not_cancel_override(self):
        self.start()
        spec = importlib.util.spec_from_file_location(
            'charge_once_apply_test', Path(__file__).parents[1] / 'rogcontrol' / 'rogcontrol-apply.py')
        apply = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(apply)
        with patch.object(apply, 'CONFIG_PATH', self.path), \
                patch.object(apply, 'run_helper', self.write), \
                patch.object(apply.hardware, 'set_power_mode_for_profile'), \
                patch.object(apply.hardware, 'set_profile_kbd_color'):
            apply.apply_once({'charge_limit': 80}, profile_only=True)
        self.assertEqual(self.limit, 100)
        self.assertIsNotNone(self.feature.read_state())

    def test_failed_normal_limit_edit_is_retried_without_creating_full_charge(self):
        self.fail = True
        self.assertFalse(self.feature.set_limit(65, config_path=self.path)[0])
        self.assertEqual(self.saved()['charge_limit'], 65)
        self.fail = False
        self.tick()
        self.assertEqual(self.limit, 65)
        self.assertIsNone(self.feature.read_state())


    def test_full_status_completes_charge_below_100_percent(self):
        battery = self.supply_path / 'BAT0'
        battery.mkdir()
        (battery / 'type').write_text('Battery')
        (battery / 'capacity').write_text('98')
        (battery / 'status').write_text('Charging')
        self.percent = 98
        self.start()
        (battery / 'status').write_text('Full')
        self.tick()
        self.assertEqual(self.limit, 80)
        self.assertIsNone(self.feature.read_state())

    def test_full_status_requires_the_same_valid_battery_as_capacity_reading(self):
        for name, kind, capacity, status in [
                ('ADP0', 'Mains', '100', 'Full'),
                ('BAT0', 'Battery', 'missing', 'Full'),
                ('BAT1', 'Battery', '65', 'Charging'),
                ('BAT2', 'Battery', '98', 'Full')]:
            supply = self.supply_path / name
            supply.mkdir()
            for key, value in [('type', kind), ('capacity', capacity), ('status', status)]:
                (supply / key).write_text(value)
        self.percent = 65
        self.start()
        self.tick()
        self.assertEqual(self.limit, 100)
        self.assertIsNotNone(self.feature.read_state())

    def test_full_status_at_old_charge_cap_does_not_prevent_starting(self):
        battery = self.supply_path / 'BAT0'
        battery.mkdir()
        (battery / 'type').write_text('Battery')
        (battery / 'capacity').write_text('80')
        (battery / 'status').write_text('Full')
        self.percent = 80
        self.start()
        self.assertEqual(self.limit, 100)
        self.assertIsNotNone(self.feature.read_state())
