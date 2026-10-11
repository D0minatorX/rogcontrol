"""Qt state and backend routing regressions; no real hardware or disk writes."""
from contextlib import nullcontext
import unittest
from unittest.mock import Mock, patch, call

from PySide6.QtCore import QCoreApplication, QObject, Signal
from rogcontrol.qt.controllers.cpu import CpuController
from rogcontrol.qt.controllers.gpu import GpuController
from rogcontrol.qt.controllers.battery import BatteryController
from rogcontrol.qt.controllers.quick_access import QuickAccessController
from rogcontrol.qt.controllers.updates import UpdatesController


class Runner(QObject):
    finished = Signal(str, object, object)
    def active(self, job):
        return False
    def submit(self, *args):
        raise AssertionError('Unexpected background hardware read')


class App(QObject):
    configChanged = Signal()
    stateChanged = Signal()
    errorOccurred = Signal(str)
    def __init__(self):
        super().__init__()
        self.config = {'current_profile': 'Balanced', 'profiles': {
            'Balanced': {'cpu': {'stapm': 55000, 'max_freq': 3500000}, 'gpu': {}},
            'Quiet': {'cpu': {'stapm': 25000}, 'gpu': {}}}}
        self.runner = Runner()
        self.activeProfile = 'Balanced'
        self.currentPage = ''
        self.demo = False
        self.busy = False
        self._write_epoch = 0
        self.submitWrite = Mock(return_value=True)


class QtControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.app = App()

    def controller(self, kind, caps=None):
        controller = kind(self.app)
        self.addCleanup(controller.close)
        if caps is not None:
            controller.caps = caps
            controller._rows = controller.build_rows(caps)
            controller._ready = True
            controller.reload()
        return controller

    def test_cpu_preserves_original_units_and_stages_display_units(self):
        cpu = self.controller(CpuController, {'ryzenadj': True, 'cpu_clock': [400000, 5400000]})
        self.assertEqual(cpu.values['stapm'], 55000)
        self.assertEqual(cpu.values['max_freq'], 3500000)
        cpu.stageValue('stapm', 60)
        cpu.stageValue('max_freq', 4.1)
        self.assertEqual(cpu.values['stapm'], 60000)
        self.assertEqual(cpu.values['max_freq'], 4100000)
        self.assertEqual(self.app.config['profiles']['Balanced']['cpu']['stapm'], 55000)

    def test_reload_preserves_edits_only_for_same_profile(self):
        cpu = self.controller(CpuController, {'ryzenadj': True})
        cpu.stageValue('stapm', 60)
        self.app.configChanged.emit()
        self.assertEqual(cpu.values['stapm'], 60000)
        self.app.activeProfile = 'Quiet'
        self.app.configChanged.emit()
        self.assertEqual(cpu.values['stapm'], 25000)
        self.assertFalse(cpu.dirty)

    def test_cpu_capability_gates_and_safety_block(self):
        cpu = self.controller(CpuController, {'cpu_power_limits': 'rapl'})
        self.assertEqual({r['key'] for r in cpu.rows}, {'limits_enabled', 'pl1', 'pl2'})
        cpu.stageValue('stapm', 70)
        self.assertEqual(cpu.values['stapm'], 55000)
        cpu.caps = {'ryzenadj': True}
        cpu._rows = cpu.build_rows(cpu.caps)
        cpu.reload()
        self.app.config['safety_tripped'] = True
        cpu.stageValue('coall', -20)
        self.assertEqual(cpu.values['coall'], 0)

    def test_cpu_write_saves_only_successful_backend_steps(self):
        cpu = self.controller(CpuController)
        plan = [('limits', ['cpu', 60000]), ('boost', ['boost', 0])]
        with patch('rogcontrol.config.load_config', return_value={}), \
             patch('rogcontrol.hardware.cpu_apply_plan', return_value=plan) as planner, \
             patch('rogcontrol.hardware.run_helper', side_effect=[(True, ''), (False, 'denied')]) as helper:
            saved, failed = cpu.write({'stapm': 60000, 'boost': False}, {}, {'ryzenadj': True, 'cpu_boost': True})
        self.assertEqual(saved, {'stapm': 60000})
        self.assertEqual(failed, ['boost: denied'])
        self.assertEqual(helper.call_args_list, [call('cpu', 60000), call('boost', 0)])
        self.assertEqual(planner.call_args.args[0]['stapm'], 60000)

    def test_apply_rejects_profile_switch_before_hardware(self):
        cpu = self.controller(CpuController, {'ryzenadj': True})
        cpu.stageValue('stapm', 60)
        cpu.apply()
        worker = self.app.submitWrite.call_args.args[1]
        with patch('rogcontrol.profile_requests.hardware_apply_lock', return_value=nullcontext()), \
             patch('rogcontrol.config.load_config', return_value={'current_profile': 'Quiet'}), \
             patch.object(cpu, 'write') as write:
            with self.assertRaisesRegex(RuntimeError, 'Profile changed'):
                worker()
        write.assert_not_called()

    def test_gpu_controls_are_independently_gated(self):
        gpu = self.controller(GpuController, {'nv_dynamic_boost': True})
        self.assertEqual([r['key'] for r in gpu.rows], ['dyn_boost'])
        gpu._live = {'access': 'GPU is asleep'}
        self.assertTrue(gpu.rows[0]['enabled'])

    def test_sleeping_gpu_still_applies_firmware_and_blocks_direct_writes(self):
        gpu = self.controller(GpuController)
        with patch('rogcontrol.hardware.nvidia_access_error', return_value=''), \
             patch('rogcontrol.hardware.dgpu_is_suspended', return_value=True), \
             patch('rogcontrol.hardware.run_helper', return_value=(True, '')) as helper:
            saved, failed = gpu.write({'watts': 90, 'dyn_boost': 15}, {}, {'gpu_power_limit': True, 'nv_dynamic_boost': True})
        helper.assert_called_once_with('nvboost', 15)
        self.assertEqual(saved, {'dyn_boost': 15})
        self.assertEqual(failed, ['watts: GPU is asleep'])

    def test_voltage_confirmation_is_invalidated_by_new_edit(self):
        gpu = self.controller(GpuController, {'nvidia_voltage_boost': True})
        with patch.object(gpu, 'start_apply') as apply:
            gpu.stageValue('voltage_boost', 10)
            gpu.apply()
            apply.assert_not_called()
            gpu.stageValue('voltage_boost', 20)
            gpu.confirm('voltage', True)
            apply.assert_not_called()
            gpu.apply()
            gpu.confirm('voltage', True)
            self.assertEqual(apply.call_args.args[0]['voltage_boost'], 20)

    def test_battery_limit_routes_through_charge_once_backend(self):
        battery = self.controller(BatteryController)
        battery._data = {'charge_supported': True}
        battery.setLimit(80)
        with patch.object(battery, 'physical', side_effect=lambda operation: operation()) as physical, \
             patch('rogcontrol.charge_once.set_limit', return_value=(True, '')) as setter:
            battery._apply_limit()
        setter.assert_called_once_with(80)
        self.assertEqual(len(physical.call_args.args), 1)

    def test_failed_physical_write_does_not_persist_configuration(self):
        battery = self.controller(BatteryController)
        mutate = Mock()
        battery.physical(lambda: (False, 'denied'), mutate)
        worker = self.app.submitWrite.call_args.args[1]
        with patch('rogcontrol.profile_requests.hardware_apply_lock', return_value=nullcontext()), \
             patch('rogcontrol.config.update_config') as save:
            with self.assertRaisesRegex(RuntimeError, 'denied'):
                worker()
        save.assert_not_called()
        mutate.assert_not_called()


    def test_quick_powermizer_retains_choices_but_blocks_sleeping_gpu_write(self):
        gpu = self.controller(GpuController, {'powermizer_modes': [0, 1, 2]})
        self.app.tuningPages = {'gpu': gpu}
        quick = self.controller(QuickAccessController)
        gpu._live = {'access': 'GPU is asleep'}
        self.assertEqual(quick.powerMizerChoices, [0, 1, 2])
        quick.setPowerMizer(1)
        self.app.submitWrite.assert_not_called()
        gpu._live = {'access': ''}
        quick.setPowerMizer(1)
        self.app.submitWrite.assert_called_once()

    def test_update_intervals_check_once_when_due(self):
        updates = self.controller(UpdatesController)
        intervals = {'daily': 86400, '48h': 172800, 'weekly': 604800, 'monthly': 2592000}
        with patch.object(updates, 'check') as check, patch('rogcontrol.qt.controllers.updates.time.time', return_value=10000000):
            for mode, interval in intervals.items():
                with self.subTest(mode=mode):
                    self.app.config.update(update_check=mode, last_update_check=10000000 - interval + 1)
                    updates._launch_checked = False
                    updates._automatic()
                    check.assert_not_called()
                    self.app.config['last_update_check'] -= 1
                    updates._launch_checked = False
                    updates._automatic()
                    updates._automatic()
                    check.assert_called_once_with('app')
                    check.reset_mock()

    def test_update_schedule_handles_invalid_timestamp_and_off(self):
        updates = self.controller(UpdatesController)
        with patch.object(updates, 'check') as check, patch('rogcontrol.qt.controllers.updates.time.time', return_value=10000000):
            self.app.config.update(update_check='weekly', last_update_check='broken')
            updates._automatic()
            check.assert_called_once_with('app')
            check.reset_mock()
            updates._launch_checked = False
            self.app.config['update_check'] = 'off'
            updates._automatic()
            check.assert_not_called()

    def test_update_preference_preserves_original_schedule_strings(self):
        updates = self.controller(UpdatesController)
        with patch('rogcontrol.profile_requests.selection_lock', side_effect=nullcontext), \
             patch('rogcontrol.config.update_config') as save:
            for mode in ('off', 'launch', 'daily', '48h', 'weekly', 'monthly'):
                updates.setAutomatic(mode)
                worker = self.app.submitWrite.call_args.args[1]
                worker()
                config = {}
                save.call_args.args[0](config)
                self.assertEqual(config, {'update_check': mode})
            self.app.submitWrite.reset_mock()
            updates.setAutomatic('unsupported')
            self.app.submitWrite.assert_not_called()

if __name__ == '__main__':
    unittest.main()
