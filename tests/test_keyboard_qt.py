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

    def test_preview_uses_existing_static_gradient_and_sensor_colour_math(self):
        c = self.controller
        lighting = dict(mode='Static', color1='#ff4000', color2='#0040ff')
        self.assertEqual(c.previewZoneColors(lighting, {}, {}, '#ffaa00'), ['#ff4000'] * 4)
        lighting['mode'] = 'Gradient Static'
        self.assertEqual(c.previewZoneColors(lighting, {}, {}, '#ffaa00'),
                         ['#ff4000', '#aa4055', '#5540aa', '#0040ff'])
        lighting['mode'] = 'CPU Temp Color'
        self.assertEqual(c.previewZoneColors(lighting, {'cpu_temp': 58}, {}, '#ffaa00'), ['#00b847'] * 4)
        lighting['mode'] = 'GPU Temp Color'
        self.assertEqual(c.previewZoneColors(lighting, {'gpu_temp': 51}, {}, '#ffaa00'), ['#00708f'] * 4)
        lighting['mode'] = 'Battery Level'
        self.assertEqual(c.previewZoneColors(lighting, {'battery': [80, False]}, {}, '#ffaa00'), ['#66ff00'] * 4)
        lighting['mode'] = 'Profile Color'
        self.assertEqual(c.previewZoneColors(lighting, {}, {}, '#ffaa00'), ['#ffaa00'] * 4)
        c.persist.assert_not_called()

    def test_preview_waits_for_unknown_readings_and_ignores_stale_capture_modes(self):
        lighting = dict(mode='Ambient', color1='#ff4000', color2='#0040ff')
        self.assertEqual(self.controller.previewZoneColors(lighting, {}, {}, '#ffaa00'), [])
        self.assertEqual(self.controller.previewZoneColors(lighting, {}, {'mode': 'CPU Temp Color', 'colors': ['#00ff00']}, '#ffaa00'), [])
        self.assertEqual(self.controller.previewZoneColors(lighting, {}, {'mode': 'Ambient', 'colors': ['#ff0000','#00ff00','#0000ff','#ffffff']}, '#ffaa00'), ['#ff0000','#00ff00','#0000ff','#ffffff'])
        lighting['mode'] = 'GPU Temp Color'
        self.assertEqual(self.controller.previewZoneColors(lighting, {'gpu_temp': None}, {}, '#ffaa00'), [])
        self.assertEqual(self.controller.previewZoneColors(lighting, {'gpu_temp': float('nan')}, {}, '#ffaa00'), [])
        self.assertEqual(self.controller.previewZoneColors(lighting, {}, {'mode': 'GPU Temp Color', 'colors': ['#ffffff']}, '#ffaa00'), ['#ffffff'] * 4)

    def test_capture_preview_notifies_and_clears_without_painting_hardware(self):
        c = self.controller
        notices = []
        c.previewSampleChanged.connect(lambda: notices.append(c.previewSample))
        c._ambient_mode = True
        c._last_mode = 'Ambient'
        with patch.object(c, '_paint'):
            c._on_colors((c._ambient_generation, [(255,0,0),(0,255,0),(0,0,255),(255,255,255)]))
        self.assertEqual(notices[-1], {'mode':'Ambient', 'colors':['#ff0000','#00ff00','#0000ff','#ffffff']})
        c._stop_ambient()
        self.assertEqual(notices[-1]['colors'], [])

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
