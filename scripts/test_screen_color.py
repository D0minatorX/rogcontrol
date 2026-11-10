"""Screen picker decoding and asynchronous draft-only delivery."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy, QTest
from rogcontrol.qt.screen_color import ScreenColorPicker, response_color


class ScreenColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_response_is_srgb_and_cancel_is_not_an_error(self):
        self.assertEqual(response_color(0, {'color': (1., 0.5, 0.)}), '#ff8000')
        self.assertIsNone(response_color(1, {}))

    def test_invalid_response_does_not_become_a_colour(self):
        for value in (None, (0, 1), (float('nan'), 0, 1), (2, 0, 0)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                response_color(0, {'color': value})
        with self.assertRaises(ValueError):
            response_color(2, {})

    def test_async_result_and_busy_state(self):
        picker = ScreenColorPicker()
        spy = QSignalSpy(picker.colorPicked)
        with patch('rogcontrol.qt.screen_color.pick_color', return_value='#1266cc'):
            picker.pick()
            for _ in range(100):
                if spy.count():
                    break
                QTest.qWait(10)
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0)[0], '#1266cc')
        self.assertFalse(picker.busy)

    def test_cancel_drops_late_result(self):
        picker = ScreenColorPicker()
        spy = QSignalSpy(picker.colorPicked)
        def wait_for_cancel(cancelled):
            cancelled.wait(1)
            return '#ff0000'
        with patch('rogcontrol.qt.screen_color.pick_color', side_effect=wait_for_cancel):
            picker.pick()
            picker.cancel()
            for _ in range(100):
                QTest.qWait(10)
                if not picker.busy:
                    break
        self.assertFalse(picker.busy)
        self.assertEqual(spy.count(), 0)


if __name__ == '__main__':
    unittest.main()
