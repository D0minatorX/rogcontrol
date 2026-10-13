#!/usr/bin/env python3
"""Render all Qt pages with fixtures and assert capability visibility (no hardware).
Run with --unsupported to simulate missing controls; --width 360 covers compact UI.
"""
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QObject, QTimer, QMetaObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine
from rogcontrol.qt.controllers.app import AppController
from rogcontrol.qt.controllers.cpu import CpuController
from rogcontrol.qt.controllers.gpu import GpuController
from rogcontrol.qt.controllers.fans import FansController
from rogcontrol.qt.controllers.battery import BatteryController
from rogcontrol.qt.controllers.quick_access import QuickAccessController
from rogcontrol.qt.controllers.system import SystemController
from rogcontrol.qt.controllers.keyboard import KeyboardController
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--unsupported', action='store_true')
parser.add_argument('--width', type=int, default=1040)
args = parser.parse_args()
failures = []
checks = []
if args.unsupported:
    for cls in (CpuController, GpuController, FansController):
        cls.demo_caps = lambda self: {}
    GpuController.demo_live = lambda self: {'mode': '', 'modes': [], 'mode_gate': 'Unavailable', 'access': ''}
    def patch_fixture(cls, **values):
        original = cls.fixture
        cls.fixture = lambda self: dict(original(self), **values)
    patch_fixture(BatteryController, charge_supported=False, health=None, display_supported=False, rates=[])
    patch_fixture(SystemController, caps={})
    patch_fixture(QuickAccessController, gamescope=False)
    patch_fixture(KeyboardController, caps={}, power_supported=False, bindings_supported=False, fn_supported=False, idle_supported=False)
original_navigate = AppController.navigate

def verify(page):
    names = {'quick_access': ['turboBoostSetting', 'gpuModeSetting', 'powerMizerSetting', 'psrSetting', 'gamescopeSetting', 'chargingCard'],
             'gpu': ['nvidiaDisplayPowerCard']}.get(page, [])
    if not names and page != 'system':
        return
    windows = [w for w in QGuiApplication.topLevelWindows() if hasattr(w, 'contentItem')]
    def visual_items(item):
        yield item
        for child in item.childItems():
            yield from visual_items(child)
    if page == 'gpu' and not args.unsupported:
        setting = next((obj for w in windows for obj in visual_items(w.contentItem())
                        if obj.objectName() == 'nvidiaDisplayPowerSwitch'), None)
        if setting is None:
            failures.append('gpu: missing NVIDIA power switch')
        else:
            control = next(obj for obj in visual_items(setting) if obj.metaObject().indexOfProperty('checked') >= 0)
            controller = QQmlEngine.contextForObject(setting).contextProperty('gpuController')
            for accepted in (False, True):
                QMetaObject.invokeMethod(control, 'toggle', Qt.DirectConnection)
                QMetaObject.invokeMethod(control, 'clicked', Qt.DirectConnection)
                if control.property('checked'):
                    failures.append('gpu: boot setting changed visually before confirmation')
                dialog = next((obj for w in windows for obj in w.findChildren(QObject)
                               if obj.metaObject().indexOfProperty('action') >= 0
                               and obj.metaObject().property(obj.metaObject().indexOfProperty('action')).typeName() == 'QString'
                               and obj.property('action') == 'display-power'), None)
                if dialog is None:
                    failures.append('gpu: missing boot-change confirmation')
                    controller.confirm('display-power', accepted)
                else:
                    QMetaObject.invokeMethod(dialog, 'accept' if accepted else 'reject', Qt.DirectConnection)
                if bool(control.property('checked')) != accepted:
                    failures.append(f'gpu: boot toggle did not reflect accepted={accepted}')
    if page == 'system':
        card = next((obj for w in windows for obj in visual_items(w.contentItem())
                     if obj.objectName() == 'ppdServiceCard'), None)
        if card is None:
            failures.append('system: missing PPD service card')
        else:
            root = card.parentItem()
            root.setProperty('section', 'Services')
            graphics_button = next((obj for obj in visual_items(root)
                                    if obj.objectName() == 'graphicsDaemonButton'), None)
            if graphics_button is None:
                failures.append('system: missing graphics service action')
            else:
                for before, after in [('Disable graphics daemon', 'Enable graphics daemon'),
                                      ('Enable graphics daemon', 'Disable graphics daemon')]:
                    if graphics_button.property('text') != before or not graphics_button.property('enabled'):
                        failures.append(f'system: expected enabled {before} button')
                    QMetaObject.invokeMethod(graphics_button, 'clicked', Qt.DirectConnection)
                    if graphics_button.property('text') != after:
                        failures.append(f'system: graphics click did not switch to {after}')
                root.setProperty('graphics', {'has_unit': False})
                if graphics_button.property('enabled') or graphics_button.property('text') != 'Graphics daemon unavailable':
                    failures.append('system: missing graphics service must not offer an action')
            if not card.property('visible'):
                failures.append('system: PPD card not visible in Services')
            buttons = {obj.property('text'): obj for obj in visual_items(card)
                       if obj.property('text') in ('Enable PPD', 'Disable PPD', 'Install PPD…', 'Uninstall PPD…')}
            cases = (
                ({'installed': True, 'has_unit': True, 'manager': 'pacman'}, [True, True, False, True]),
                ({'installed': False, 'has_unit': False, 'manager': 'apt-get'}, [False, False, True, False]),
                ({'installed': True, 'has_unit': True, 'manager': 'dnf', 'tuned_ppd': True}, [False] * 4),
            )
            for state, expected in cases:
                root.setProperty('ppd', state)
                actual = [bool(buttons[label].property('enabled')) if label in buttons else None
                          for label in ('Enable PPD', 'Disable PPD', 'Install PPD…', 'Uninstall PPD…')]
                if actual != expected:
                    failures.append(f'system: PPD button states {actual}, expected {expected}')
        checks.append('ppdServiceCard')
    for name in names:
        item = next((obj for w in windows for obj in visual_items(w.contentItem()) if obj.objectName() == name), None)
        if item is None or bool(item.property('visible')) != (not args.unsupported):
            failures.append(f'{page}/{name}: expected visible={not args.unsupported}, got {None if item is None else item.property("visible")}')
        checks.append(name)

def navigate(self, page):
    original_navigate(self, page)
    QTimer.singleShot(80, lambda: verify(page))
AppController.navigate = navigate
from rogcontrol.qt.application import main
result = main(['--self-test', '--demo-width', str(args.width)])
if len(checks) != 8:
    failures.append(f'Expected 8 visibility checks, executed {len(checks)}')
for failure in failures:
    print(failure, file=sys.stderr)
print(f'Qt capability smoke: width={args.width}, unsupported={args.unsupported}, checks={len(checks)}, failures={len(failures)}')
raise SystemExit(result or bool(failures))
