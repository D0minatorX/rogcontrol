"""Per-app CPU boost override: load, effective profile, enforcer apply and the UI; no hardware."""
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from rogcontrol import app_profiles

ROOT = Path(__file__).resolve().parents[1]
EXE = '/usr/bin/game'


def config(override, **cpu):
    return {'current_profile': 'P', 'profiles': {'P': {'cpu': dict(cpu)}},
            'app_profiles': {EXE: {'cpu': override}}}


class BoostOverrideTests(unittest.TestCase):
    def effective(self, cfg):
        return app_profiles.effective_profile(cfg, [EXE])['cpu']

    def test_old_profile_without_key_loads_and_keeps_baseline(self):
        self.assertNotIn('boost', self.effective(config({'stapm': 20000}, stapm=30000)))
        self.assertEqual(self.effective(config({'stapm': 20000}, stapm=30000))['stapm'], 20000)

    def test_override_on_off_and_inherit(self):
        self.assertIs(self.effective(config({'boost': False}, boost=True))['boost'], False)
        self.assertIs(self.effective(config({'boost': True}, boost=False))['boost'], True)
        # Profiles that never stored the key default to boost on, so Off still applies.
        self.assertIs(self.effective(config({'boost': False}, stapm=1))['boost'], False)

    def test_override_applies_when_power_limits_are_disabled(self):
        self.assertIs(self.effective(config({'boost': False}, limits_enabled=False))['boost'], False)

    def test_limit_override_applies_when_power_limits_are_disabled(self):
        from rogcontrol.cpu_plan import cpu_apply_plan
        limits = dict(stapm=30000, fast=40000, slow=30000, temp=90, limits_enabled=False)
        cfg = config({'stapm': 20000}, **limits)
        cpu = self.effective(cfg)
        self.assertEqual(cpu['stapm'], 20000)
        self.assertEqual(cpu['fast'], 40000)  # not overridden: the profile's stored value
        self.assertEqual(cpu_apply_plan(cpu, {'ryzenadj': True}),
                         [('limits', ('cpu', 20000, 40000, 30000, 90, 0))])
        self.assertEqual(cpu_apply_plan(cfg['profiles']['P']['cpu'], {'ryzenadj': True}), [])  # app gone: nothing written

    def test_non_bool_value_is_ignored(self):
        for bad in (1, 'off', None):
            self.assertIs(self.effective(config({'boost': bad}, boost=True))['boost'], True)

    def test_plan_writes_boost_through_existing_helper(self):
        from rogcontrol.cpu_plan import cpu_apply_plan
        plan = cpu_apply_plan(self.effective(config({'boost': False}, boost=True)), {'cpu_boost': True})
        self.assertEqual(plan, [('boost', ('cpuboost', 0))])


class EnforcerBoostTests(unittest.TestCase):
    def test_pending_boost_runs_helper_then_rewrites_ceiling_and_clears(self):
        spec = importlib.util.spec_from_file_location('enforcer_app_boost', ROOT / 'rogcontrol/rogcontrol-enforcer.py')
        enforcer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(enforcer)
        calls = []
        effective = {'cpu': {'boost': True, 'max_freq': 3000000}}
        with patch.object(enforcer, 'run_helper', side_effect=lambda *a: calls.append(a) or True), \
                patch.object(enforcer, 'clock_limit_available', return_value=True), \
                patch.object(enforcer.hardware, 'cpu_is_amd', return_value=False), \
                patch.object(enforcer.hardware, 'cpu_power_limits_backend', return_value=None), \
                patch.object(enforcer.hardware, 'cpu_boost_control_available', return_value=True), \
                patch.object(enforcer.hardware, 'read_int', return_value=400000), \
                patch.object(enforcer.hardware, 'read_fan_boost', return_value=None):
            left = enforcer.apply_app_changes({}, effective, {('cpu', 'boost')})
        self.assertEqual(calls, [('cpuboost', 1), ('cpuclock', 3000000)])
        self.assertEqual(left, set())


class EnforcerLimitsOffTests(unittest.TestCase):
    def test_app_start_applies_limits_and_exit_restores_off_via_firmware_reset(self):
        spec = importlib.util.spec_from_file_location('enforcer_app_limits', ROOT / 'rogcontrol/rogcontrol-enforcer.py')
        enforcer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(enforcer)
        cfg = config({'stapm': 20000}, stapm=30000, fast=40000, slow=30000, temp=90, limits_enabled=False)
        calls = []
        def run(effective, pending):
            with patch.object(enforcer, 'run_helper', side_effect=lambda *a: calls.append(a) or True), \
                    patch.object(enforcer, 'clock_limit_available', return_value=False), \
                    patch.object(enforcer.hardware, 'cpu_is_amd', return_value=True), \
                    patch.object(enforcer.hardware, 'cpu_power_limits_backend', return_value='ryzenadj'), \
                    patch.object(enforcer.hardware, 'cpu_boost_control_available', return_value=False), \
                    patch.object(enforcer.os.path, 'exists', return_value=True), \
                    patch.object(enforcer.hardware, 'read_fan_boost', return_value=None):
                return enforcer.apply_app_changes(cfg, effective, pending)
        pending = {('cpu', key) for key in ('stapm', 'fast', 'slow')}
        self.assertEqual(run(app_profiles.effective_profile(cfg, [EXE]), pending), set())
        self.assertEqual(calls, [('cpu', 20000, 40000, 30000, 90, 0)])
        calls.clear()
        self.assertEqual(run(app_profiles.effective_profile(cfg, []), pending), set())
        self.assertEqual(calls, [('cpufwlimits',)])


class AppProfilesUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtGui import QGuiApplication
        cls.qt = QGuiApplication.instance() or QGuiApplication([])

    def test_limit_row_is_available_when_power_limits_are_disabled(self):
        from PySide6.QtCore import QObject, Signal
        from rogcontrol.qt.controllers.app_profiles import AppProfilesController

        class Page(QObject):
            stateChanged = Signal()
            rows = [dict(key='stapm', title='STAPM', kind='number', min=0, max=100, default=0)]
            caps = {}

        class App(QObject):
            configChanged = Signal()
            busy = False
            demo = False
            config = {'current_profile': 'P', 'profiles': {'P': {'cpu': {'stapm': 30000, 'limits_enabled': False}}},
                      'app_profiles': {EXE: {}}}
            tuningPages = {'cpu': Page()}

        row = AppProfilesController(App()).rows[0]
        self.assertTrue(row['available'])
        self.assertEqual(row['value'], 30000)

    def test_controller_saves_boost_and_page_renders_tile_and_dialog(self):
        from PySide6.QtCore import QObject, QUrl, Signal, Property
        from PySide6.QtQuick import QQuickView
        from rogcontrol.qt.controllers.app_profiles import AppIconProvider, AppProfilesController

        class Page(QObject):
            stateChanged = Signal()
            def __init__(self):
                super().__init__()
                self.rows = [dict(key='boost', title='Turbo boost', kind='bool', default=True),
                             dict(key='max_freq', title='Maximum core clock', kind='number', min=0, max=5,
                                  default=0, unit='GHz', scale=1000000, step=.1)]
                self.caps = {'cpu_clock': [400000, 5400000], 'cpu_clock_floor': 400000}

        class App(QObject):
            configChanged = Signal()
            errorOccurred = Signal(str)
            busy = Property(bool, lambda s: False, constant=True)
            def __init__(self):
                super().__init__()
                self.config = {'current_profile': 'P', 'profiles': {'P': {'cpu': {'boost': True}}},
                               'app_profiles': {EXE: {}}}
                self.demo = True
                self.tuningPages = {'cpu': Page()}

        app = App()
        controller = AppProfilesController(app)
        self.assertEqual(controller.apps, [{'path': EXE, 'name': 'game'}])
        controller.select(EXE)
        controller.setMode('cpu', 'boost', 'off')
        row = next(r for r in controller.rows if r['key'] == 'boost')
        self.assertTrue(row['overridden'])
        self.assertIs(row['value'], False)
        controller.save()
        self.assertEqual(app.config['app_profiles'][EXE], {'cpu': {'boost': False}})
        controller.select(EXE)
        controller.setMode('cpu', 'boost', 'inherit')
        controller.save()
        self.assertEqual(app.config['app_profiles'][EXE], {})

        view = QQuickView()
        ctx = view.engine().rootContext()
        busy = QObject()
        busy.setProperty('busy', False)
        ctx.setContextProperty('appProfilesController', controller)
        ctx.setContextProperty('appController', busy)
        view.engine().addImageProvider('appicon', AppIconProvider())
        view.setInitialProperties({'colors': {k: '#222222' for k in
                                              ('text', 'mutedText', 'border', 'panel', 'window', 'hover',
                                               'accent', 'selected', 'sidebar')}})
        view.setSource(QUrl.fromLocalFile(str(ROOT / 'rogcontrol/qml/pages/AppProfilesPage.qml')))
        self.assertEqual(view.errors(), [])
        view.resize(600, 500)
        view.show()
        self.qt.processEvents()
        def find(item, name):  # Repeater delegates are visual, not QObject, children
            if item.objectName() == name:
                return item
            return next(filter(None, (find(child, name) for child in item.childItems())), None)
        pen = find(view.rootObject(), 'editApp')
        self.assertIsNotNone(pen)
        pen.clicked.emit()
        self.qt.processEvents()
        dialog = view.rootObject().findChild(QObject, 'overridesDialog')
        self.assertTrue(dialog.property('visible'))
        self.assertEqual(controller.path, EXE)
        combo = view.rootObject().findChild(QObject, 'settingCombo')
        combo.setProperty('currentIndex', 1)
        view.rootObject().setProperty('selectedSetting', 1)
        row = controller.rows[1]
        controller.toggle(row['section'], row['key'], True)  # relabels the model
        self.qt.processEvents()
        self.assertEqual(combo.property('currentIndex'), 1)
        dialog.close()
        self.qt.processEvents()
        controller.selectExecutable('/bin/sh')  # a fresh pick has no tile yet, so the editor must open itself
        self.qt.processEvents()
        self.assertTrue(dialog.property('visible'))
        view.close()


if __name__ == '__main__':
    unittest.main()
