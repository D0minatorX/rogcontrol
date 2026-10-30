#!/usr/bin/env python3
"""Run the maintained Qt/backend release gate, not the obsolete GTK suite.

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
# This mixed module has three backend classes plus an obsolete GTK class.
MAINTAINED_CLASSES = (
    'test_gpu_offset_capability.OffsetCapabilityTests',
    'test_gpu_offset_capability.ClockCeilingCapabilityTests',
    'test_gpu_offset_capability.CapabilityAggregationTests',
)
LEGACY_GTK_SELECTIONS = (
    ('test_charge_once_ui', 3),
    ('test_display_refresh_ui', 8),
    ('test_gamescope_ui', 2),
    ('test_gpu_offset_capability.ClockControlVisibilityTests', 2),
    ('test_keyboard_bindings_ui', 4),
    ('test_keyboard_idle_ui', 3),
    ('test_keyboard_power_ui', 3),
    ('test_overview_layout', 3),
    ('test_page_backends', 13),
    ('test_pending_changes', 8),
    ('test_slider_entry', 7),
    ('test_system_sections', 3),
    ('test_ui', 17),
)


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
    known_modules.update(name.split('.')[0] for name in MAINTAINED_CLASSES)
    known_modules.update(name.split('.')[0] for name, _ in LEGACY_GTK_SELECTIONS)
    unclassified = sorted(p.stem for p in (ROOT / 'tests').glob('test_*.py') if p.stem not in known_modules)
    if unclassified:
        parser.error('Classify new test modules explicitly before running the gate: ' + ', '.join(unclassified))
    print('Selected maintained backend/Qt modules and classes:')
    for name in (*MAINTAINED_MODULES, *MAINTAINED_CLASSES):
        print('  ' + name)
    print('Isolated Qt UI modules: ' + ', '.join(ISOLATED_QT_MODULES))
    print('\nExcluded obsolete GTK selections (not migrated, not passing):')
    for name, count in LEGACY_GTK_SELECTIONS:
        print(f'  {name}: {count} test methods')
    print(f'Total excluded: {len(LEGACY_GTK_SELECTIONS)} selections, '
          f'{sum(count for _, count in LEGACY_GTK_SELECTIONS)} legacy test methods.', flush=True)
    suite = unittest.defaultTestLoader.loadTestsFromNames((*MAINTAINED_MODULES, *MAINTAINED_CLASSES))
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    if not result.wasSuccessful():
        return 1
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    return subprocess.run([sys.executable, '-m', 'unittest', *('tests.' + name for name in ISOLATED_QT_MODULES)],
                          cwd=ROOT, env=env, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
