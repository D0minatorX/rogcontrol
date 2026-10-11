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
from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QGuiApplication
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
    names = {'quick_access': ['turboBoostSetting', 'gpuModeSetting', 'powerMizerSetting', 'psrSetting', 'gamescopeSetting'], 'battery': ['chargingCard']}.get(page, [])
    if not names:
        return
    windows = [w for w in QGuiApplication.topLevelWindows() if hasattr(w, 'contentItem')]
    def visual_items(item):
        yield item
        for child in item.childItems():
            yield from visual_items(child)
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
if len(checks) != 6:
    failures.append(f'Expected 6 visibility checks, executed {len(checks)}')
for failure in failures:
    print(failure, file=sys.stderr)
print(f'Qt capability smoke: width={args.width}, unsupported={args.unsupported}, checks={len(checks)}, failures={len(failures)}')
raise SystemExit(result or bool(failures))
