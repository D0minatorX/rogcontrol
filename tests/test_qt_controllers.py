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

    def test_graphics_service_controls_route_both_actions_and_verify_state(self):
        from rogcontrol.qt.controllers.system import SystemController
        controller = self.controller(SystemController)
        controller._data = controller.fixture()
        for backend, service in [('cardwire', 'cardwired'), ('supergfxctl', 'supergfxd')]:
            for running in (False, True):
                with self.subTest(backend=backend, running=running):
                    controller._data['backend'] = backend
                    controller.setGraphics(running)
                    worker, done = self.app.submitWrite.call_args.args[1:]
                    state = dict(controller._data['graphics'], active=running, enabled=running,
                                 raw_active='active' if running else 'inactive')
                    with patch('rogcontrol.profile_requests.hardware_apply_lock', side_effect=nullcontext), patch('rogcontrol.hardware.set_' + service + '_running', return_value=(True, '')) as setter, patch('rogcontrol.hardware.read_' + service + '_state', return_value=state):
                        result = worker()
                    setter.assert_called_once_with(running=running)
                    done(result, None)
                    self.assertEqual(controller.data['graphics']['active'], running)
                    self.assertIn('running' if running else 'stopped', controller.status)

    def test_graphics_service_refusal_and_wrong_readback_are_not_success(self):
        from rogcontrol.qt.controllers.system import SystemController
        controller = self.controller(SystemController)
        controller._data = controller.fixture()
        controller.setGraphics(False)
        worker = self.app.submitWrite.call_args.args[1]
        with patch('rogcontrol.profile_requests.hardware_apply_lock', side_effect=nullcontext), patch('rogcontrol.hardware.set_supergfxd_running', return_value=(False, 'permission denied')):
            with self.assertRaisesRegex(RuntimeError, 'permission denied'):
                worker()
        with patch('rogcontrol.profile_requests.hardware_apply_lock', side_effect=nullcontext), patch('rogcontrol.hardware.set_supergfxd_running', return_value=(True, '')), patch('rogcontrol.hardware.read_supergfxd_state', return_value=controller._data['graphics']):
            with self.assertRaisesRegex(RuntimeError, 'requested state'):
                worker()

    def test_graphics_demo_toggles_without_writes_and_unknown_state_is_blocked(self):
        from rogcontrol.qt.controllers.system import SystemController
        controller = self.controller(SystemController)
        controller._data = controller.fixture()
        self.app.demo = True
        for running in (False, True):
            controller.setGraphics(running)
            self.assertEqual(controller.data['graphics']['active'], running)
            self.assertEqual(controller.fixture()['graphics']['active'], running)
        self.app.demo = False
        controller._data['graphics']['raw_active'] = ''
        controller.setGraphics(False)
        self.app.submitWrite.assert_not_called()

    def test_nvidia_boot_toggle_requires_confirmation_and_routes_to_helper(self):
        gpu = self.controller(GpuController, {})
        gpu._live['display_power'] = {'supported': True, 'enabled': False}
        gpu.setDisplayPower(True)
        self.app.submitWrite.assert_not_called()
        gpu.confirm('display-power', False)
        self.app.submitWrite.assert_not_called()
        gpu.setDisplayPower(True)
        gpu.confirm('display-power', True)
        worker, done = self.app.submitWrite.call_args.args[1:]
        state = {'supported': True, 'enabled': True, 'live': False, 'pending': True}
        with patch('rogcontrol.profile_requests.hardware_apply_lock', side_effect=nullcontext), patch('rogcontrol.hardware.run_helper', return_value=(True, 'Reboot required')) as helper, patch('rogcontrol.nvidia_boot.read_state', return_value=state):
            result = worker()
        helper.assert_called_once_with('nvidia-display-power', 1, timeout=1400)
        done(result, None)
        self.assertEqual(gpu.displayPower, state)
        self.assertEqual(gpu.status, 'Reboot required')

    def test_nvidia_boot_toggle_prompts_for_reboot_only_after_success(self):
        gpu = self.controller(GpuController, {})
        gpu._live['display_power'] = {'supported': True, 'enabled': False}
        requested = []
        gpu.confirmationRequested.connect(lambda action, message: requested.append((action, message)))
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                requested.clear()
                gpu.setDisplayPower(enabled)
                gpu.confirm('display-power', True)
                _, done = self.app.submitWrite.call_args.args[1:]
                self.assertEqual(len(requested), 1)
                done(('Boot images rebuilt', {'supported': True, 'enabled': enabled}), None)
                self.assertEqual(len(requested), 2)
                self.assertEqual(requested[-1][0], 'display-power-reboot')
                self.assertIn('Restart', requested[-1][1])
                gpu.confirm('display-power-reboot', False)
        requested.clear()
        gpu.setDisplayPower(True)
        gpu.confirm('display-power', True)
        _, done = self.app.submitWrite.call_args.args[1:]
        done(None, RuntimeError('Rebuild failed'))
        self.assertEqual([action for action, _ in requested], ['display-power'])

    def test_nvidia_boot_reboot_choice_only_schedules_after_success(self):
        gpu = self.controller(GpuController, {})
        gpu._live['display_power'] = {'supported': True, 'enabled': False}
        with patch('rogcontrol.qt.controllers.gpu.QTimer.singleShot') as schedule:
            gpu.confirm('display-power-reboot', True)
            schedule.assert_not_called()
            gpu.setDisplayPower(True)
            gpu.confirm('display-power', True)
            _, done = self.app.submitWrite.call_args.args[1:]
            done(('Boot images rebuilt', {'supported': True, 'enabled': True}), None)
            gpu.confirm('display-power-reboot', True)
            schedule.assert_called_once_with(5000, gpu._reboot)
            gpu.confirm('display-power-reboot', True)
            schedule.assert_called_once()

    def test_nvidia_boot_toggle_demo_is_isolated_and_unsupported_is_blocked(self):
        gpu = self.controller(GpuController, {})
        gpu.setDisplayPower(True)
        self.assertIsNone(gpu._display_confirmation)
        self.app.demo = True
        gpu._live = gpu.demo_live()
        gpu.setDisplayPower(True)
        gpu.confirm('display-power', True)
        self.assertTrue(gpu.displayPower['enabled'])
        self.app.submitWrite.assert_not_called()

    def test_ppd_actions_launch_through_worker_and_surface_errors(self):
        from rogcontrol.qt.controllers.system import SystemController
        controller = self.controller(SystemController)
        controller._data = controller.fixture()
        controller.managePpd('disable')
        worker, done = self.app.submitWrite.call_args.args[1:]
        with patch('rogcontrol.ppd_service.launch_action', return_value=(True, 'Terminal opened')) as launch:
            result = worker()
            done(result, None)
            launch.assert_called_once_with('disable')
            self.assertEqual(controller.status, 'Terminal opened')
        with patch('rogcontrol.ppd_service.launch_action', return_value=(False, 'No terminal')):
            with self.assertRaisesRegex(RuntimeError, 'No terminal'):
                worker()

    def test_ppd_demo_and_tuned_provider_do_not_write(self):
        from rogcontrol.qt.controllers.system import SystemController
        controller = self.controller(SystemController)
        controller._data = controller.fixture()
        self.app.demo = True
        controller.managePpd('disable')
        self.app.demo = False
        controller._data['ppd']['tuned_ppd'] = True
        for action in ('enable', 'disable', 'install', 'uninstall', 'invalid'):
            controller.managePpd(action)
        self.app.submitWrite.assert_not_called()

    def controller(self, kind, caps=None):
        controller = kind(self.app)
        self.addCleanup(controller.close)
        if caps is not None:
            controller.caps = caps
            controller._rows = controller.build_rows(caps)
            controller._ready = True
            controller.reload()
        return controller

    def test_gpu_name_comes_from_driver_without_querying_gpu(self):
        import tempfile
        from pathlib import Path
        from rogcontrol import hardware
        with tempfile.TemporaryDirectory() as directory:
            info = Path(directory) / 'proc/driver/nvidia/gpus/0000:01:00.0/information'
            info.parent.mkdir(parents=True)
            info.write_text('Model: NVIDIA GeForce RTX 5070 Ti Laptop GPU\nIRQ: 79\n')
            with patch('rogcontrol.hardware.subprocess.run') as run:
                self.assertTrue(hasattr(hardware, 'read_gpu_name'))
                self.assertEqual(hardware.read_gpu_name(root=directory), 'NVIDIA GeForce RTX 5070 Ti Laptop GPU')
                run.assert_not_called()

    def test_fan_boost_preferences_persist_without_starting_boost(self):
        import tempfile
        from pathlib import Path
        from rogcontrol import config
        quick = self.controller(QuickAccessController)
        self.assertTrue(hasattr(quick, 'setFanBoostPreference'))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, 'CONFIG_PATH', str(Path(directory) / 'config.json')), \
             patch('rogcontrol.profile_requests.selection_lock', side_effect=nullcontext), \
             patch('rogcontrol.hardware.write_fan_boost') as start:
            config.save_config(self.app.config)
            for key, value in [('percent', 85), ('seconds', 120)]:
                quick.setFanBoostPreference(key, value)
                self.app.submitWrite.call_args.args[1]()
            saved = config.load_config()
            self.assertEqual(saved['fan_boost_preferences'], {'percent': 85, 'seconds': 120})
            start.assert_not_called()
        self.app.submitWrite.reset_mock()
        quick.setFanBoostPreference('seconds', 0)
        self.app.submitWrite.assert_not_called()

    def test_start_boost_records_duration_and_writes_fan_curves(self):
        from rogcontrol.qt.controllers.fans import FansController
        fans = self.controller(FansController, {'fan_curve': True})
        fans.boost(85, 120)
        worker = self.app.submitWrite.call_args.args[1]
        with patch('rogcontrol.profile_requests.hardware_apply_lock', side_effect=nullcontext), \
             patch('rogcontrol.hardware.write_fan_boost') as record, \
             patch('rogcontrol.hardware.read_fan_boost', return_value={'pct': 85, 'until': 9999999999}), \
             patch('rogcontrol.hardware.run_helper', return_value=(True, '')) as helper, \
             patch('rogcontrol.qt.controllers.fans.time.sleep'):
            worker()
            record.assert_called_once_with(85, 120, 'Balanced')
            self.assertEqual(helper.call_count, 3)
            self.assertTrue(all(c.args[0] == 'fan' for c in helper.call_args_list))

    def test_stop_boost_clears_override_and_restores_current_profile(self):
        from rogcontrol.qt.controllers.fans import FansController
        fans = self.controller(FansController, {'fan_curve': True})
        fans.stopBoost()
        worker = self.app.submitWrite.call_args.args[1]
        with patch('rogcontrol.profile_requests.hardware_apply_lock', side_effect=nullcontext), \
             patch('rogcontrol.hardware.clear_fan_boost') as clear, \
             patch('rogcontrol.config.load_config', return_value=self.app.config), \
             patch.object(fans, '_restore_saved') as restore:
            worker()
            clear.assert_called_once()
            restore.assert_called_once_with(self.app.config)

    def test_cpu_clock_range_keeps_boost_maximum_when_boost_is_off(self):
        import tempfile
        from pathlib import Path
        from rogcontrol import hardware
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / 'sys/devices/system/cpu/cpufreq/policy0'
            policy.mkdir(parents=True)
            for name, value in {'cpuinfo_min_freq': '421798', 'cpuinfo_max_freq': '2401000',
                                'amd_pstate_max_freq': '5386028', 'boost': '0'}.items():
                (policy / name).write_text(value)
            self.assertEqual(hardware.read_cpu_clock_range(root=directory), (421798, 5386028))
            (policy / 'amd_pstate_max_freq').unlink()
            self.assertEqual(hardware.read_cpu_clock_range(root=directory), (421798, 2401000))

    def test_cpu_saved_boost_clock_is_preserved_while_boost_disabled(self):
        self.app.config['profiles']['Balanced']['cpu'].update(boost=False, max_freq=5000000)
        cpu = self.controller(CpuController, {'cpu_boost': True, 'cpu_clock': [421798, 5386028]})
        self.assertEqual(cpu.values['max_freq'], 5000000)
        self.assertFalse(next(r for r in cpu.rows if r['key'] == 'max_freq')['enabled'])
        cpu.stageValue('boost', True)
        cpu.stageValue('max_freq', 5.3)
        self.assertEqual(cpu.values['max_freq'], 5300000)

    def test_battery_cycles_use_firmware_and_do_not_invent_zero(self):
        import tempfile
        from pathlib import Path
        from rogcontrol import hardware
        with tempfile.TemporaryDirectory() as directory:
            battery = Path(directory) / 'sys/class/power_supply/BAT0'
            battery.mkdir(parents=True)
            for name, value in {'type': 'Battery', 'energy_full_design': '90000000', 'energy_full': '87000000', 'cycle_count': '42'}.items():
                (battery / name).write_text(value)
            self.assertEqual(hardware.read_battery_health(root=directory)['cycles'], 42)
            (battery / 'cycle_count').write_text('0')
            self.assertIsNone(hardware.read_battery_health(root=directory)['cycles'])

    def test_epp_failed_write_does_not_save_preference(self):
        cpu = self.controller(CpuController)
        with patch('rogcontrol.config.load_config', return_value={}), \
             patch('rogcontrol.hardware.run_helper', return_value=(False, 'denied')):
            saved, failed = cpu.write({'epp': 'power'}, {}, {'cpu_epp': ['power']})
        self.assertEqual(saved, {})
        self.assertEqual(failed, ['epp: denied'])

    def test_epp_choices_refresh_when_governor_changes(self):
        cpu = self.controller(CpuController, {'cpu_epp': ['performance']})
        cpu._live = {'epp_choices': ['performance', 'balance_power', 'power']}
        cpu.after_read()
        cpu.stageValue('epp', 'power')
        self.assertEqual(cpu.values['epp'], 'power')

    def test_epp_saved_preference_survives_performance_governor(self):
        self.app.config['profiles']['Balanced']['cpu']['epp'] = 'balance_power'
        cpu = self.controller(CpuController, {'cpu_epp': ['performance']})
        self.assertEqual(cpu.values['epp'], 'balance_power')
        self.assertFalse(cpu.dirty)

    def test_epp_apply_persists_only_target_profile_and_reloads(self):
        import tempfile
        from pathlib import Path
        from rogcontrol import config
        cpu = self.controller(CpuController, {'cpu_epp': ['performance', 'balance_power', 'power']})
        cpu.stageValue('epp', 'balance_power')
        cpu.apply()
        _, worker, done = self.app.submitWrite.call_args.args
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, 'CONFIG_PATH', str(Path(directory) / 'config.json')), \
             patch('rogcontrol.profile_requests.hardware_apply_lock', side_effect=nullcontext), \
             patch('rogcontrol.profile_requests.selection_lock', side_effect=nullcontext), \
             patch('rogcontrol.hardware.run_helper', return_value=(True, '')) as helper:
            config.save_config(self.app.config)
            result = worker()
            done(result, None)
            saved = config.load_config()
            self.assertEqual(saved['profiles']['Balanced']['cpu']['epp'], 'balance_power')
            self.assertNotEqual(saved['profiles']['Quiet']['cpu'].get('epp'), 'balance_power')
            helper.assert_called_once_with('cpuepp', 'balance_power')
        self.assertFalse(cpu.dirty)
        self.app.activeProfile = 'Quiet'
        cpu.reload()
        self.app.activeProfile = 'Balanced'
        cpu.reload()
        self.assertEqual(cpu.values['epp'], 'balance_power')

    def test_success_status_expires_and_geometry_stays_silent(self):
        from rogcontrol.qt.controllers.app import AppController
        from PySide6.QtTest import QTest
        app = AppController(Runner(), demo=True)
        self.addCleanup(app.close)
        app.runner.submit = Mock()
        app.submitWrite('settings', lambda: None)
        app._complete('write', None, None)
        self.assertEqual(app.statusText, 'Saved')
        self.assertEqual(app.statusTimer.interval(), 1500)
        # Shorten the real timer so the test exercises event-loop expiry.
        self.assertTrue(hasattr(app, 'statusTimer'))
        app.statusTimer.start(1)
        QTest.qWait(20)
        self.assertEqual(app.statusText, '')
        app.submitWrite('geometry', lambda: None)
        self.assertEqual(app.statusText, '')
        app._complete('write', None, None)
        self.assertEqual(app.statusText, '')

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
