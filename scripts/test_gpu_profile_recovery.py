"""GPU/profile regressions using simulated hardware and temporary config files.

Run with /usr/bin/python3 scripts/test_gpu_profile_recovery.py.
"""
from contextlib import nullcontext
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QObject, Signal
from rogcontrol import config, feature_report, hardware, profile_requests
from rogcontrol.qt.controllers.gpu import GpuController


def load_enforcer():
    spec = importlib.util.spec_from_file_location(
        'gpu_recovery_enforcer', ROOT / 'rogcontrol/rogcontrol-enforcer.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class App(QObject):
    configChanged = Signal()
    stateChanged = Signal()
    finished = Signal(str, object, object)

    def __init__(self, cfg):
        super().__init__()
        self.config = cfg
        self.activeProfile = cfg['current_profile']
        self.currentPage = ''
        self.demo = self.busy = False
        self.runner = self
        self.submitWrite = Mock(return_value=True)


class ClockProbeRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QCoreApplication.instance() or QCoreApplication([])

    def test_normal_gpu_page_probe_preserves_saved_ceiling(self):
        cfg = {'current_profile': 'Quiet', 'profiles': {
            'Quiet': {'gpu': {'clock_limit': 1415}}}}
        app = App(cfg)
        controller = GpuController(app)
        self.addCleanup(controller.close)
        controller._ready = True
        controller.caps = {'nvidia': True}
        clock = [1415]

        def write(action, value, **_kwargs):
            self.assertEqual(action, 'gpuclocklimit')
            clock[0] = value
            return True, ''

        with patch.object(profile_requests, 'hardware_apply_lock', side_effect=nullcontext), \
                patch.object(config, 'load_config', return_value=cfg), \
                patch.object(hardware, 'dgpu_is_suspended', return_value=False), \
                patch.object(hardware, 'nvidia_access_error', return_value=None), \
                patch.object(hardware, 'have_cmd', return_value=True), \
                patch.object(hardware, 'detect_gpu_max_clock', return_value=3090), \
                patch.object(hardware, 'detect_gpu_limits', return_value={'clock_limit_max': 3090}), \
                patch.object(hardware, 'detect_nvidia_offset', return_value={'ok': False}), \
                patch.object(hardware, 'gpu_power_limit_supported', return_value=False), \
                patch.object(hardware, 'probe_nvidia_voltage_boost', return_value=None), \
                patch.object(hardware, 'detect_nvidia_powermizer_modes', return_value=[]), \
                patch.object(hardware, 'run_helper', side_effect=write):
            controller.probe()
            worker = app.submitWrite.call_args.args[1]
            worker()
        self.assertEqual(clock[0], 1415, 'A capability refresh must not unlock the active profile')

    def test_unsettable_power_limit_does_not_attempt_firmware_limit_minus_one(self):
        writes = []

        def query(fields):
            # A real laptop can expose an enforced firmware limit while
            # the user-settable limit is N/A.
            return (None if fields[0] == 'power.limit' else 140.0, 5.0, 140.0)

        with patch.object(hardware, 'read_nvidia_query', side_effect=query), \
                patch.object(hardware, 'run_helper', side_effect=lambda *a: (writes.append(a) or False, 'unsupported')):
            self.assertFalse(hardware.gpu_power_limit_supported())
        self.assertEqual(writes, [])

    def test_saved_ceiling_probe_never_writes_a_temporary_higher_limit(self):
        for succeeds in (True, False):
            with self.subTest(succeeds=succeeds):
                writes = []

                def write(action, value):
                    writes.append((action, value))
                    return succeeds, '' if succeeds else 'driver busy'

                with patch.object(hardware, 'nvidia_access_error', return_value=None), \
                        patch.object(hardware, 'have_cmd', return_value=True), \
                        patch.object(hardware, 'detect_gpu_max_clock', return_value=3090), \
                        patch.object(hardware, 'run_helper', side_effect=write):
                    self.assertEqual(hardware.gpu_clock_limit_supported(1715), succeeds)
                self.assertEqual(writes, [('gpuclocklimit', 1715)])

    def test_default_clock_probe_restores_unlocked_state_after_failed_test_write(self):
        for ceiling in (3090, None):
            with self.subTest(ceiling=ceiling):
                clock = [ceiling]

                def write(action, value):
                    clock[0] = value
                    return value != 3075, 'probe refused' if value == 3075 else ''

                with patch.object(hardware, 'nvidia_access_error', return_value=None), \
                        patch.object(hardware, 'have_cmd', return_value=True), \
                        patch.object(hardware, 'detect_gpu_max_clock', return_value=3090), \
                        patch.object(hardware, 'run_helper', side_effect=write):
                    self.assertFalse(hardware.gpu_clock_limit_supported(ceiling))
                self.assertEqual(clock[0], 'reset')

    def test_installer_probe_preserves_saved_ceiling(self):
        cfg = {'current_profile': 'Quiet', 'profiles': {'Quiet': {'gpu': {'clock_limit': 1415}}}}
        clock = [1415]

        def write(action, value):
            self.assertEqual(action, 'gpuclocklimit')
            clock[0] = value
            return True, ''

        with patch.object(profile_requests, 'hardware_apply_lock', side_effect=nullcontext), \
                patch.object(config, 'load_config', return_value=cfg), \
                patch.object(hardware, 'detect_capabilities', return_value={'nvidia': True, 'nvidia_settings': False}), \
                patch.object(hardware, 'nvidia_access_error', return_value=None), \
                patch.object(hardware, 'have_cmd', return_value=True), \
                patch.object(hardware, 'detect_gpu_max_clock', return_value=3090), \
                patch.object(hardware, 'detect_nvidia_offset', return_value={'ok': False}), \
                patch.object(hardware, 'probe_nvidia_voltage_boost', return_value=None), \
                patch.object(feature_report, 'display_refresh_row', return_value=('display_refresh', 'Display', False)), \
                patch.dict(sys.modules, {'rogcontrol.ambient': SimpleNamespace(ambient_available=lambda: False)}), \
                patch.object(hardware, 'run_helper', side_effect=write):
            feature_report.detect_feature_rows()
        self.assertEqual(clock[0], 1415)


class EnforcerGpuRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.enforcer = load_enforcer()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'config.json'
        self.cfg = {'current_profile': 'Balanced Power', 'profiles': {
            'Balanced Power': {'gpu': {'clock_limit': 1715}},
            'Custom': {'gpu': {'clock_limit': 1415}},
        }}
        self.clock = None
        self.writes = []
        self.patch(self.enforcer, 'CONFIG_PATH', str(self.path))
        self.real_apply_lock = profile_requests.hardware_apply_lock
        self.patch(profile_requests, 'hardware_apply_lock', side_effect=nullcontext)
        self.patch(hardware, 'nvidia_access_error', return_value=None)
        self.asleep = self.patch(hardware, 'dgpu_is_suspended', return_value=False)
        self.patch(hardware, 'gpu_clock_limit_max', return_value=3090)
        self.patch(hardware, 'nv_apply_settings', return_value=(True, ''))
        self.patch(hardware, 'read_fan_boost', return_value=None)
        self.patch(hardware, 'read_fan_curve_enabled', return_value={})
        self.patch(self.enforcer, 'log')
        self.patch(self.enforcer, 'run_nvidia_helper', side_effect=self.write)
        self.patch(self.enforcer, 'run_helper', side_effect=AssertionError('unexpected hardware write'))
        self.persist()

    def patch(self, obj, name, *args, **kwargs):
        patcher = patch.object(obj, name, *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def persist(self):
        self.path.write_text(json.dumps(self.cfg))

    def write(self, action, value):
        self.writes.append((action, value))
        self.assertEqual(action, 'gpuclocklimit')
        self.clock = value
        return True

    def upkeep(self, cfg=None):
        cfg = self.cfg if cfg is None else cfg
        self.enforcer.apply_full_profile(
            cfg, cfg['profiles'][cfg['current_profile']], full=False)

    def test_periodic_pass_recovers_manual_switch_without_thermal_change(self):
        self.upkeep()
        self.cfg['current_profile'] = 'Custom'
        self.persist()
        self.upkeep()
        self.assertEqual(self.clock, 1415)
        count = len(self.writes)
        self.upkeep()
        self.assertEqual(len(self.writes), count, 'Unchanged profiles must not cause repeated writes')

    def test_sleeping_gpu_receives_latest_ceiling_after_waking(self):
        self.asleep.return_value = True
        self.upkeep()
        self.assertEqual(self.writes, [])
        self.cfg['current_profile'] = 'Custom'
        self.persist()
        self.upkeep()
        self.asleep.return_value = False
        self.upkeep()
        self.assertEqual(self.clock, 1415)

    def test_failed_ceiling_remains_pending_until_success(self):
        failed = [True]

        def write(action, value):
            if failed[0]:
                return False
            return self.write(action, value)

        self.patch(self.enforcer, 'run_nvidia_helper', side_effect=write)
        self.upkeep()
        self.assertIsNone(self.clock)
        failed[0] = False
        self.upkeep()
        self.assertEqual(self.clock, 1715)

    def test_stale_cycle_snapshot_does_not_overwrite_new_profile(self):
        stale = deepcopy(self.cfg)
        self.cfg['current_profile'] = 'Custom'
        self.persist()
        self.upkeep(stale)
        self.assertEqual(self.clock, 1415)

    def test_competing_writer_blocks_reconciliation_and_latest_profile_wins(self):
        entered, finished = threading.Event(), threading.Event()
        failures = []
        stale = deepcopy(self.cfg)

        def upkeep():
            entered.set()
            try:
                self.upkeep(stale)
            except Exception as error:
                failures.append(error)
            finally:
                finished.set()

        with patch.object(profile_requests, 'hardware_apply_lock', self.real_apply_lock), \
                patch.object(profile_requests, 'directory', return_value=self.path.parent):
            with self.real_apply_lock():
                worker = threading.Thread(target=upkeep, daemon=True)
                worker.start()
                self.assertTrue(entered.wait(2))
                self.assertFalse(finished.wait(0.05), 'GPU reconciliation bypassed the active writer')
                self.assertEqual(self.writes, [])
                self.cfg['current_profile'] = 'Custom'
                self.persist()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive(), 'GPU reconciliation did not resume after unlock')
        self.assertEqual(failures, [])
        self.assertEqual(self.clock, 1415)

    def test_removed_offsets_are_not_replayed_from_old_profile(self):
        self.enforcer._pending_gpu_offsets['core'] = 150
        offsets = []
        self.patch(hardware, 'set_nvidia_clock_offset',
                   side_effect=lambda kind, value: (offsets.append((kind, value)) or True, ''))
        self.upkeep()
        self.enforcer.retry_pending_gpu_offsets()
        self.assertEqual(offsets, [])
        self.assertEqual(self.enforcer._pending_gpu_offsets, {})

    def test_offsets_are_restored_after_access_returns_to_unchanged_profile(self):
        self.cfg['profiles']['Balanced Power']['gpu']['clock_offset'] = 75
        self.persist()
        offset = [0]

        def set_offset(kind, value):
            offset[0] = value
            return True, ''

        self.patch(hardware, 'set_nvidia_clock_offset', side_effect=set_offset)
        self.upkeep()
        self.assertEqual(offset[0], 75)
        self.asleep.return_value = True
        offset[0] = 0
        self.upkeep()
        self.asleep.return_value = False
        self.upkeep()
        self.assertEqual(offset[0], 75)

    def test_charger_change_reapplies_ceiling_even_if_profile_stays_selected(self):
        self.cfg['ac_profile'] = self.cfg['usbc_profile'] = 'Balanced Power'
        self.persist()
        self.upkeep()
        self.clock = None  # Simulated firmware reset during charger transition.
        self.enforcer._last_ac_state = True
        self.enforcer._last_charger_kind = 'mains'
        self.patch(hardware, 'read_power_source', return_value=(True, 'usb'))
        self.patch(self.enforcer, 'store_last_charger_kind')
        self.patch(self.enforcer, 'charger_flash')
        self.patch(self.enforcer, 'notify')
        self.assertFalse(self.enforcer._check_ac_auto_switch(self.cfg, None, 'test'))
        self.assertTrue(self.enforcer._cycle_wakeup.is_set())
        self.upkeep()
        self.assertEqual(self.clock, 1715)
        self.assertEqual(json.loads(self.path.read_text())['current_profile'], 'Balanced Power')


class ChargerTransitionTests(unittest.TestCase):
    def test_unchanged_or_unknown_source_preserves_manual_selection(self):
        enforcer = load_enforcer()
        cfg = {'current_profile': 'Custom', 'ac_profile': 'Performance',
               'usbc_profile': 'Balanced Power', 'battery_profile': 'Quiet',
               'profiles': {name: {} for name in ('Custom', 'Performance', 'Balanced Power', 'Quiet')}}
        for old, new, old_kind, new_kind in (
                (True, True, 'usb', 'usb'), (True, True, 'mains', 'mains'),
                (False, False, None, None), (None, True, None, 'usb'),
                (True, None, 'mains', None), (True, True, None, 'usb')):
            with self.subTest(old=old, new=new, old_kind=old_kind, new_kind=new_kind):
                self.assertIsNone(enforcer.ac_switch_target(old, new, cfg, new_kind, old_kind))

    def test_connected_charger_kind_change_selects_its_profile(self):
        for previous, current, initial, expected in (
                ('mains', 'usb', 'Performance', 'Balanced Power'),
                ('usb', 'mains', 'Balanced Power', 'Performance')):
            with self.subTest(previous=previous, current=current), tempfile.TemporaryDirectory() as directory:
                enforcer = load_enforcer()
                cfg = {'current_profile': initial, 'ac_profile': 'Performance',
                       'usbc_profile': 'Balanced Power',
                       'profiles': {'Performance': {}, 'Balanced Power': {}}}
                path = Path(directory) / 'config.json'
                path.write_text(json.dumps(cfg))
                enforcer._last_ac_state = True
                enforcer._last_charger_kind = previous
                with patch.object(enforcer, 'CONFIG_PATH', str(path)), \
                        patch.object(hardware, 'read_power_source', return_value=(True, current)), \
                        patch.object(enforcer, 'store_last_charger_kind'), \
                        patch.object(enforcer, 'charger_flash'), \
                        patch.object(enforcer, 'notify'), patch.object(enforcer, 'log'), \
                        patch.object(enforcer, 'set_profile_power_mode', return_value=True), \
                        patch.object(enforcer, 'apply_full_profile'):
                    enforcer._check_ac_auto_switch(cfg, None, 'test')
                self.assertEqual(json.loads(path.read_text())['current_profile'], expected)


if __name__ == '__main__':
    unittest.main()
