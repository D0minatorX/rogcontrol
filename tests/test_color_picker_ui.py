"""The keyboard colour dialog keeps a full palette and custom editor."""
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

    def test_swatch_opens_full_palette(self):
        spy = QSignalSpy(self.picker, self.picker.metaObject().method(self.picker.metaObject().indexOfSignal('edited(QColor)')))
        swatch = self.picker.findChild(QObject, 'colorSwatch')
        self.assertIsNotNone(swatch)
        swatch.clicked.emit()
        dialog = self.picker.findChild(QObject, 'customColorDialog')
        self.assertTrue(dialog.property('visible'))
        repeater = dialog.findChild(QObject, 'paletteRepeater')
        self.assertEqual(repeater.property('count'), 45)
        self.assertFalse(dialog.findChild(QObject, 'colorEditor').property('visible'))
        self.assertEqual(spy.count(), 0)

    def test_palette_selection_waits_for_select(self):
        spy = QSignalSpy(self.picker, self.picker.metaObject().method(self.picker.metaObject().indexOfSignal('edited(QColor)')))
        dialog = self.picker.findChild(QObject, 'customColorDialog')
        self.picker.findChild(QObject, 'colorSwatch').clicked.emit()
        repeater = dialog.findChild(QObject, 'paletteRepeater')
        preset, _ = QQmlExpression(QQmlEngine.contextForObject(repeater), repeater, 'itemAt(0)').evaluate()
        preset.clicked.emit()
        self.assertEqual(spy.count(), 0)
        self.assertTrue(QMetaObject.invokeMethod(dialog, 'accept'))
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0)[0].name().lower(), '#9bc8f4')

    def test_custom_button_opens_editor(self):
        self.picker.findChild(QObject, 'colorSwatch').clicked.emit()
        dialog = self.picker.findChild(QObject, 'customColorDialog')
        dialog.findChild(QObject, 'customEditorButton').clicked.emit()
        self.assertTrue(dialog.findChild(QObject, 'colorEditor').property('visible'))

    def test_custom_editor_has_direct_colour_controls(self):
        dialog = self.picker.findChild(QObject, 'customColorDialog')
        self.assertIsNotNone(dialog.findChild(QObject, 'saturationValueArea'))
        self.assertIsNotNone(dialog.findChild(QObject, 'hueStrip'))
        self.assertIsNotNone(dialog.findChild(QObject, 'customHexField'))

    def test_custom_editor_applies_only_when_accepted(self):
        spy = QSignalSpy(self.picker, self.picker.metaObject().method(self.picker.metaObject().indexOfSignal('edited(QColor)')))
        dialog = self.picker.findChild(QObject, 'customColorDialog')
        self.picker.findChild(QObject, 'colorSwatch').clicked.emit()
        dialog.findChild(QObject, 'customEditorButton').clicked.emit()
        dialog.setProperty('draftColor', QColor('#1266cc'))
        self.assertEqual(spy.count(), 0)
        self.assertTrue(QMetaObject.invokeMethod(dialog, 'accept'))
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0)[0].name().lower(), '#1266cc')


if __name__ == '__main__':
    unittest.main()
