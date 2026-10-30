#!/usr/bin/env python3
"""Run the maintained Qt/backend release gate.

Usage: /usr/bin/python3 scripts/test_qt_release.py [-v]

Selection is explicit: these unit suites use mocked hardware operations, local
fixtures and fake installer/update commands. This does not exercise real device
writes, installation, network updates or interactive QML. Unexpected test/load
failures propagate as a nonzero exit; no test failures are converted to skips.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

MAINTAINED_MODULES = (
    'test_apply_efficiency',
    'test_aura_power',
    'test_cardwire_gpu_limits',
    'test_cardwire_nvidia_guard',
    'test_charge_once',
    'test_cpu_thermal_policy',
    'test_daily_automation',
    'test_decky_gpu_telemetry',
    'test_decky_v012_local',
    'test_display_refresh',
    'test_enforcer_efficiency',
    'test_fan_timeout_reporting',
    'test_feature_report',
    'test_fnlock',
    'test_gamescope',
    'test_graphics_services',
    'test_gpu_offset_capability',
    'test_gpu_power',
    'test_gpu_power_capability',
    'test_gpu_profile_recovery',
    'test_hotkeys',
    'test_installer',
    'test_keyboard_idle',
    'test_keyboard_power',
    'test_keyboard_qt',
    'test_nvidia_clocks',
    'test_nvidia_boot',
    'test_power_mode_sync',
    'test_platform_power',
    'test_ppd_service',
    'test_profile_requests',
    'test_qt_controllers',
    'test_startup',
    'test_tray',
    'test_updates',
    'test_wayland_idle',
)
ISOLATED_QT_MODULES = ('test_color_picker_ui',)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    # GPU profile recovery is maintained alongside its standalone runner.
    sys.path.insert(0, str(ROOT / 'scripts'))
    sys.path.insert(0, str(ROOT / 'tests'))
    known_modules = set(MAINTAINED_MODULES + ISOLATED_QT_MODULES)
    unclassified = sorted(p.stem for p in (ROOT / 'tests').glob('test_*.py') if p.stem not in known_modules)
    if unclassified:
        parser.error('Classify new test modules explicitly before running the gate: ' + ', '.join(unclassified))
    print('Selected maintained backend/Qt modules:')
    for name in MAINTAINED_MODULES:
        print('  ' + name)
    print('Isolated Qt UI modules: ' + ', '.join(ISOLATED_QT_MODULES))
    suite = unittest.defaultTestLoader.loadTestsFromNames(MAINTAINED_MODULES)
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    if not result.wasSuccessful():
        return 1
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    return subprocess.run([sys.executable, '-m', 'unittest', *('tests.' + name for name in ISOLATED_QT_MODULES)],
                          cwd=ROOT, env=env, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
