"""Exercise brightness choices and screen-picked colour confirmation in QML."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_CONTROLS_STYLE', 'Basic')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QObject, QUrl, QMetaObject, QPointF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtQml import QQmlExpression, QQmlEngine
from PySide6.QtTest import QSignalSpy, QTest
from rogcontrol.qt.workers import TaskRunner
from rogcontrol.qt.controllers.app import AppController
from rogcontrol.qt.controllers.keyboard import KeyboardController
from rogcontrol.qt.controllers.overview import OverviewController

ROOT = Path(__file__).resolve().parents[1]
COLORS = dict(text='#eceef1', mutedText='#a5a7b0', border='#28282d',
              panel='#101012', window='#050506', hover='#1d1d22',
              accent='#ef1745', selected='#281018')


class KeyboardControlsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.runner = TaskRunner()
        self.app = AppController(self.runner, demo=True)
        self.app.navigate('keyboard')
        self.keyboard = KeyboardController(self.app)
        self.overview = OverviewController(self.app)
        self.view = QQuickView()
        context = self.view.rootContext()
        context.setContextProperty('appController', self.app)
        context.setContextProperty('keyboardController', self.keyboard)
        context.setContextProperty('overviewController', self.overview)

    def tearDown(self):
        self.keyboard.close()
        self.overview.close()
        self.app.close()
        self.runner.shutdown()
        self.view.close()
        self.view.deleteLater()
        self.qt.processEvents()

    def load(self, relative, width, height):
        self.view.setSource(QUrl.fromLocalFile(str(ROOT / relative)))
        root = self.view.rootObject()
        self.assertIsNotNone(root, self.view.errors())
        root.setProperty('colors', COLORS)
        self.view.setResizeMode(QQuickView.SizeRootObjectToView)
        self.view.resize(width, height)
        self.view.show()
        QTest.qWait(30)
        return root

    def test_all_brightness_levels_apply_and_fit_at_narrow_width(self):
        root = self.load('rogcontrol/qml/pages/KeyboardPage.qml', 360, 860)
        for index in range(4):
            repeater = root.findChild(QObject, 'brightnessLevels')
            button, _ = QQmlExpression(QQmlEngine.contextForObject(repeater), repeater, f'itemAt({index})').evaluate()
            self.assertIsNotNone(button)
            self.assertGreaterEqual(button.property('width'), 60)
            centre = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
            QTest.mouseClick(self.view, Qt.LeftButton, Qt.NoModifier, centre)
            QTest.qWait(350)
            self.assertEqual(self.app.config['kbd_brightness'], index)
            self.assertTrue(button.property('primary'))
        self.view.grabWindow().save('/tmp/rogcontrol-brightness-controls.png')

    def test_speed_choices_apply_each_level_and_hide_for_static_effect(self):
        root = self.load('rogcontrol/qml/pages/KeyboardPage.qml', 360, 1100)
        self.keyboard.setLighting('mode', 'Breathing')
        QTest.qWait(350)
        selector = root.findChild(QObject, 'animationSpeed')
        self.assertIsNotNone(selector)
        self.assertTrue(selector.property('visible'))
        for index in range(3):
            repeater = root.findChild(QObject, 'speedLevels')
            button, _ = QQmlExpression(QQmlEngine.contextForObject(repeater), repeater,
                                      f'itemAt({index})').evaluate()
            centre = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
            QTest.mouseClick(self.view, Qt.LeftButton, Qt.NoModifier, centre)
            for _ in range(60):
                QTest.qWait(10)
                if self.app.config.get('kbd_rgb', {}).get('speed') == index + 1:
                    break
            self.assertEqual(self.app.config['kbd_rgb']['speed'], index + 1)
            self.assertTrue(button.property('primary'))
        self.keyboard.setLighting('mode', 'Static')
        QTest.qWait(350)
        self.assertFalse(selector.property('visible'))

    def test_section_navigation_fits_narrow_window_and_preserves_lighting(self):
        root = self.load('rogcontrol/qml/pages/KeyboardPage.qml', 360, 1100)
        repeater = root.findChild(QObject, 'keyboardSections')
        for index in (1, 0):
            button, _ = QQmlExpression(QQmlEngine.contextForObject(repeater), repeater,
                                      f'itemAt({index})').evaluate()
            self.assertGreaterEqual(button.width(), 130)
            centre = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
            QTest.mouseClick(self.view, Qt.LeftButton, Qt.NoModifier, centre)
            QTest.qWait(40)
            self.assertEqual(root.property('section'), index)
            self.assertEqual(root.findChild(QObject, 'lightingCard').property('visible'), index == 0)
            self.assertEqual(root.findChild(QObject, 'hotkeysCard').property('visible'), index == 1)
            self.assertEqual(self.keyboard.lighting['brightness'], 3)

    def test_power_matrix_updates_only_clicked_zone_and_state(self):
        root = self.load('rogcontrol/qml/pages/KeyboardPage.qml', 1080, 1100)
        self.keyboard.setPower('', 'enabled', True)
        QTest.qWait(50)
        pending = [root]
        button = None
        while pending:
            item = pending.pop()
            if item.objectName() == 'power-keyboard-sleep':
                button = item
                break
            pending.extend(item.childItems())
        self.assertIsNotNone(button)
        centre = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(self.view, Qt.LeftButton, Qt.NoModifier, centre)
        QTest.qWait(50)
        self.assertFalse(self.app.config['keyboard_power']['keyboard']['sleep'])
        self.assertTrue(self.app.config['keyboard_power']['lightbar']['sleep'])
        self.assertTrue(self.app.config['keyboard_power']['keyboard']['awake'])

    def test_fan_navigation_selects_channels_and_keeps_live_rpm(self):
        from rogcontrol.qt.controllers.fans import FansController
        self.app.navigate('fans')
        fans = FansController(self.app)
        fans.refresh()
        self.addCleanup(fans.close)
        self.view.rootContext().setContextProperty('fansController', fans)
        root = self.load('rogcontrol/qml/pages/FansPage.qml', 360, 1000)
        repeater = root.findChild(QObject, 'fanChannels')
        self.assertIsNotNone(repeater)
        for index in range(3):
            button, _ = QQmlExpression(QQmlEngine.contextForObject(repeater), repeater,
                                      f'itemAt({index})').evaluate()
            centre = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
            QTest.mouseClick(self.view, Qt.LeftButton, Qt.NoModifier, centre)
            QTest.qWait(40)
            self.assertEqual(root.property('selectedChannel'), str(index + 1))
            self.assertTrue(button.property('primary'))
        fans._live['rpms']['3'] = 3100
        fans.detailsChanged.emit()
        QTest.qWait(40)
        navigation = root.findChild(QObject, 'fanNavigation')
        labels, _ = QQmlExpression(QQmlEngine.contextForObject(navigation), navigation,
                                  'secondaryLabels[2]').evaluate()
        self.assertEqual(labels, '3100 RPM')
        self.assertEqual(root.property('selectedChannel'), '3')

    def test_colour_dialog_fits_minimum_window_size(self):
        root = self.load('rogcontrol/qml/components/ColorPicker.qml', 360, 480)
        root.findChild(QObject, 'colorSwatch').clicked.emit()
        dialog = root.findChild(QObject, 'customColorDialog')
        QTest.qWait(30)
        self.assertLessEqual(dialog.property('height'), 456)
        dialog.findChild(QObject, 'customEditorButton').clicked.emit()
        QTest.qWait(30)
        self.assertLessEqual(dialog.property('height'), 456)

    def test_screen_colour_changes_draft_then_requires_confirmation(self):
        root = self.load('rogcontrol/qml/components/ColorPicker.qml', 520, 720)
        spy = QSignalSpy(root.edited)
        root.findChild(QObject, 'colorSwatch').clicked.emit()
        dialog = root.findChild(QObject, 'customColorDialog')
        button = dialog.findChild(QObject, 'screenEyedropper')
        with patch('rogcontrol.qt.screen_color.pick_color', return_value='#1266cc'):
            button.clicked.emit()
            for _ in range(100):
                QTest.qWait(10)
                if not self.keyboard.screenPicker.busy:
                    break
        self.assertEqual(dialog.property('draftColor').name(), '#1266cc')
        self.assertTrue(dialog.property('editorVisible'))
        self.assertEqual(spy.count(), 0)
        self.view.grabWindow().save('/tmp/rogcontrol-eyedropper-dialog.png')
        QMetaObject.invokeMethod(dialog, 'accept')
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0)[0].name(), '#1266cc')

    def test_screen_pick_failure_preserves_draft_and_allows_retry(self):
        root = self.load('rogcontrol/qml/components/ColorPicker.qml', 520, 720)
        root.findChild(QObject, 'colorSwatch').clicked.emit()
        dialog = root.findChild(QObject, 'customColorDialog')
        original = dialog.property('draftColor')
        button = dialog.findChild(QObject, 'screenEyedropper')
        with patch('rogcontrol.qt.screen_color.pick_color', side_effect=RuntimeError('Portal unavailable')):
            button.clicked.emit()
            for _ in range(100):
                QTest.qWait(10)
                if not self.keyboard.screenPicker.busy:
                    break
        self.assertEqual(dialog.property('draftColor'), original)
        self.assertEqual(dialog.property('pickError'), 'Portal unavailable')
        self.assertTrue(button.property('enabled'))

    def test_screen_pick_cancellation_preserves_draft(self):
        root = self.load('rogcontrol/qml/components/ColorPicker.qml', 520, 720)
        root.findChild(QObject, 'colorSwatch').clicked.emit()
        dialog = root.findChild(QObject, 'customColorDialog')
        original = dialog.property('draftColor')
        with patch('rogcontrol.qt.screen_color.pick_color', return_value=None):
            dialog.findChild(QObject, 'screenEyedropper').clicked.emit()
            for _ in range(100):
                QTest.qWait(10)
                if not self.keyboard.screenPicker.busy:
                    break
        self.assertEqual(dialog.property('draftColor'), original)
        self.assertFalse(dialog.property('pickPending'))


if __name__ == '__main__':
    unittest.main()
