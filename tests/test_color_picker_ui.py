"""The keyboard colour control offers presets and a custom picker."""
import os
from pathlib import Path
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QMetaObject, QObject, QUrl
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QSignalSpy


PICKER = Path(__file__).resolve().parents[1] / 'rogcontrol/qml/components/ColorPicker.qml'


class ColorPickerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.view = QQuickView()
        self.view.setSource(QUrl.fromLocalFile(str(PICKER)))
        self.assertEqual(self.view.errors(), [])
        self.picker = self.view.rootObject()
        self.assertIsNotNone(self.picker)
        self.picker.setProperty('colors', {
            'text': '#ffffff', 'mutedText': '#aaaaaa', 'border': '#555555',
            'panel': '#222222', 'window': '#111111', 'hover': '#333333',
            'accent': '#ef1745', 'selected': '#552233',
        })
        self.picker.setProperty('value', '#ee2445')
        self.picker.setProperty('width', 260)
        self.picker.setProperty('height', 40)
        self.view.resize(400, 350)
        self.view.show()
        self.app.processEvents()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()

    def test_preset_choice_emits_selected_color(self):
        spy = QSignalSpy(self.picker, self.picker.metaObject().method(self.picker.metaObject().indexOfSignal('edited(QColor)')))
        swatch = self.picker.findChild(QObject, 'colorSwatch')
        self.assertIsNotNone(swatch)
        swatch.clicked.emit()
        repeater = next(x for x in self.picker.findChildren(QObject) if 'Repeater' in x.metaObject().className())
        preset, _ = QQmlExpression(QQmlEngine.contextForObject(repeater), repeater, 'itemAt(5)').evaluate()
        self.assertIsNotNone(preset)
        preset.clicked.emit()
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0)[0].name().lower(), '#3478f6')

    def test_custom_choice_opens_dialog_without_emitting(self):
        spy = QSignalSpy(self.picker, self.picker.metaObject().method(self.picker.metaObject().indexOfSignal('edited(QColor)')))
        swatch = self.picker.findChild(QObject, 'colorSwatch')
        self.assertIsNotNone(swatch)
        swatch.clicked.emit()
        custom = self.picker.findChild(QObject, 'customColorButton')
        self.assertIsNotNone(custom)
        custom.clicked.emit()
        dialog = self.picker.findChild(QObject, 'customColorDialog')
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.property('visible'))
        self.assertEqual(spy.count(), 0)

    def test_palette_has_light_and_deep_shades(self):
        repeater = next(x for x in self.picker.findChildren(QObject) if 'Repeater' in x.metaObject().className())
        self.assertGreaterEqual(repeater.property('count'), 36)
        names = []
        for index in range(repeater.property('count')):
            item, _ = QQmlExpression(QQmlEngine.contextForObject(repeater), repeater, f'itemAt({index})').evaluate()
            names.append(item.objectName())
        self.assertIn('presetLightblue', names)
        self.assertIn('presetDeepblue', names)

    def test_custom_editor_has_direct_colour_controls(self):
        dialog = self.picker.findChild(QObject, 'customColorDialog')
        self.assertIsNotNone(dialog.findChild(QObject, 'saturationValueArea'))
        self.assertIsNotNone(dialog.findChild(QObject, 'hueStrip'))
        self.assertIsNotNone(dialog.findChild(QObject, 'customHexField'))

    def test_custom_editor_applies_only_when_accepted(self):
        spy = QSignalSpy(self.picker, self.picker.metaObject().method(self.picker.metaObject().indexOfSignal('edited(QColor)')))
        dialog = self.picker.findChild(QObject, 'customColorDialog')
        dialog.setProperty('draftColor', QColor('#1266cc'))
        self.assertEqual(spy.count(), 0)
        self.assertTrue(QMetaObject.invokeMethod(dialog, 'accept'))
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0)[0].name().lower(), '#1266cc')


if __name__ == '__main__':
    unittest.main()
