"""Keyboard Qt parity without hardware access or portal capture."""
from contextlib import nullcontext
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QObject, Signal

from rogcontrol.qt.controllers.keyboard import KeyboardController


class Runner(QObject):
    finished = Signal(str, object, object)

    def active(self, _job):
        return False


class App(QObject):
    configChanged = Signal()
    stateChanged = Signal()
    demo = True
    busy = False
    currentPage = 'keyboard'
    _write_epoch = 0

    def __init__(self):
        super().__init__()
        self.runner = Runner()
        self.config = {'kbd_rgb': {'mode': 'Static'}, 'kbd_brightness': 3}


class KeyboardQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.app = App()
        self.controller = KeyboardController(self.app)
        self.controller.refresh = Mock()
        self.app.demo = False
        self.controller.persist = Mock()

    def tearDown(self):
        self.controller.close()

    def test_recheck_refreshes_static_without_reapplying_lighting(self):
        with patch.object(self.controller, '_start_ambient') as start:
            self.controller.retry()
            self.controller.refresh.assert_called_once()
            start.assert_not_called()
            self.controller.persist.assert_not_called()

    def test_recheck_restarts_ambient_capture(self):
        self.app.config['kbd_rgb']['mode'] = 'Ambient'
        with patch.object(self.controller, '_stop_ambient') as stop, \
             patch.object(self.controller, '_start_ambient') as start:
            self.controller.retry()
            self.controller.refresh.assert_called_once()
            stop.assert_called_once()
            start.assert_called_once()

    def test_effect_change_invalidates_live_color_cache(self):
        c = self.controller
        c._last_mode = 'CPU Temp Color'
        c._colors = c._last_colors = [(100, 50, 0)]
        generation = c._ambient_generation
        c._sync_mode()
        self.assertIsNone(c._colors)
        self.assertIsNone(c._last_colors)
        self.assertGreater(c._ambient_generation, generation)

    def test_queued_ambient_paint_cannot_overwrite_new_live_mode(self):
        c = self.controller
        self.app.config['kbd_rgb']['mode'] = 'Ambient'
        c._colors = [(100, 50, 0)]
        c.write = Mock()
        c._paint()
        operation, complete = c.write.call_args.args
        with patch('rogcontrol.config.load_config', return_value={'kbd_rgb': {'mode': 'CPU Temp Color'}}), \
             patch('rogcontrol.profile_requests.hardware_apply_lock', return_value=nullcontext()), \
             patch('rogcontrol.hardware.run_helper') as helper:
            self.assertIsNone(operation())
            helper.assert_not_called()
        c._ambient_generation += 1
        complete([(100, 50, 0)], None)
        self.assertIsNone(c._last_colors)

    def test_fn_brightness_readback_persists_without_overwriting_new_edit(self):
        c = self.controller
        c._read_done('settings-keyboard', {'brightness': 1, 'saved_brightness': 3, 'dimmed': False}, None)
        save = c.persist.call_args.args[0]
        cfg = {'kbd_brightness': 3}
        save(cfg)
        self.assertEqual(cfg['kbd_brightness'], 1)
        cfg['kbd_brightness'] = 2
        save(cfg)
        self.assertEqual(cfg['kbd_brightness'], 2)

    def test_idle_brightness_is_displayed_without_persisting(self):
        c = self.controller
        c._read_done('settings-keyboard', {'brightness': 0, 'saved_brightness': 3, 'dimmed': True}, None)
        c.persist.assert_not_called()
        self.assertEqual(c.lighting['brightness'], 0)
        c.setLighting('brightness', 2)
        self.assertEqual(c.lighting['brightness'], 2)

    def test_invalid_power_zone_does_not_write(self):
        c = self.controller
        c.physical = Mock()
        c.setPower('', 'awake', True)
        c.physical.assert_not_called()


if __name__ == '__main__':
    unittest.main()
