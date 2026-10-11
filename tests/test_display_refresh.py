"""Refresh changes are planned from compositor fixtures; never touch a display."""
import copy
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from rogcontrol import display_refresh as refresh


def kde_state():
    def mode(mid, hz, w=2560, h=1600):
        return {'id': mid, 'size': {'width': w, 'height': h}, 'refreshRate': hz}
    return {'outputs': [
        {'id': 1, 'name': 'eDP-1', 'connected': True, 'enabled': True,
         'currentModeId': 'hi', 'scale': 1.5, 'pos': {'x': 0, 'y': 0},
         'rotation': 1, 'replicationSource': 0, 'clones': [],
         'modes': [mode('hi', 165), mode('lo', 48), mode('other', 240, 1920, 1080)]},
        {'id': 2, 'name': 'DP-1', 'connected': True, 'enabled': True,
         'currentModeId': 'external', 'modes': [mode('external', 144)]}]}


def gnome_state():
    internal = ('eDP-1', 'vendor', 'panel', 'serial')
    external = ('DP-1', 'vendor', 'external', 'serial2')
    def mode(mid, hz, current=False, scales=(1., 1.5, 2.)):
        return (mid, 2560, 1600, hz, 1., list(scales), {'is-current': current})
    return (7, [(internal, [mode('hi', 165, True), mode('lo', 48),
                           mode('incompatible-scale', 30, scales=(1.,))],
                 {'is-builtin': True, 'color-mode': 0}),
                (external, [mode('external', 144, True)], {'color-mode': 1})],
            [(0, 0, 1.5, 0, True, [internal], {}),
             (1707, 0, 1., 1, False, [external], {})], {'layout-mode': 1})


class RefreshPlanningTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = patch.object(refresh, 'STATE_PATH', str(Path(directory.name) / 'refresh.json'), create=True)
        state.start()
        self.addCleanup(state.stop)
        # Failure fixtures must never appear in the installed app's log.
        log_path = patch('rogcontrol.hardware.LOG_PATH', str(Path(directory.name) / 'test.log'))
        log_path.start()
        self.addCleanup(log_path.stop)
        log_lock = patch('rogcontrol.hardware.LOG_LOCK_PATH', str(Path(directory.name) / 'test.log.lock'))
        log_lock.start()
        self.addCleanup(log_lock.stop)

    def test_detects_only_safe_current_resolution_rates_for_both_desktops(self):
        for backend in ('kde', 'gnome'):
            with self.subTest(backend=backend), \
                 patch.object(refresh, 'availability', return_value=(True, 'Available')), \
                 patch.object(refresh, 'backend_name', return_value=backend), \
                 patch.object(refresh, '_run', return_value=json.dumps(kde_state())), \
                 patch.object(refresh, '_gnome_state', return_value=gnome_state()):
                self.assertEqual(refresh.get_refresh_rates(), ([48., 165.], 165.))

    def test_manual_selection_preserves_external_layout_and_rejects_unknown_rate(self):
        for backend, state, planner in [('kde', kde_state(), refresh.kde_command),
                                         ('gnome', gnome_state(), refresh.gnome_command)]:
            with self.subTest(backend=backend):
                self.assertEqual(planner(state, None, requested_rate=48), planner(state, 'battery'))
                self.assertIsNone(planner(state, None, requested_rate=165))
                with self.assertRaises(ValueError):
                    planner(state, None, requested_rate=60)

    def test_manual_apply_queries_fresh_state_and_never_applies_unsupported_rate(self):
        with patch.object(refresh, 'availability', return_value=(True, 'KDE')), \
             patch.object(refresh, 'backend_name', return_value='kde'), \
             patch.object(refresh, '_run', return_value=json.dumps(kde_state())) as run:
            self.assertTrue(refresh.set_refresh_rate(48)[0])
            self.assertEqual(run.call_args_list[0].args[0], ['kscreen-doctor', '--json'])
            self.assertEqual(run.call_args_list[1].args[0], ['kscreen-doctor', 'output.1.mode.lo'])
            run.reset_mock()
            self.assertFalse(refresh.set_refresh_rate(60)[0])
            self.assertEqual(run.call_count, 1)

    def test_multiple_panels_offer_common_rates_and_require_all_to_support_selection(self):
        state = kde_state()
        second = copy.deepcopy(state['outputs'][0])
        second.update(id=3, name='eDP-2', currentModeId='lo')
        second['modes'][0]['refreshRate'] = 120
        state['outputs'].append(second)
        with patch.object(refresh, 'availability', return_value=(True, 'KDE')), \
             patch.object(refresh, 'backend_name', return_value='kde'), \
             patch.object(refresh, '_run', return_value=json.dumps(state)) as run:
            self.assertEqual(refresh.get_refresh_rates(), ([48.], None))
            self.assertFalse(refresh.set_refresh_rate(165)[0])
            self.assertEqual(run.call_count, 2)

    def test_unavailable_or_mirrored_panels_do_not_offer_manual_rates(self):
        with patch.object(refresh, 'availability', return_value=(False, 'No graphical session')):
            with self.assertRaisesRegex(ValueError, 'No graphical session'):
                refresh.get_refresh_rates()
            self.assertFalse(refresh.set_refresh_rate(48)[0])
        state = gnome_state()
        state[2][0][5].append(state[1][1][0])
        with patch.object(refresh, 'availability', return_value=(True, 'GNOME')), \
             patch.object(refresh, 'backend_name', return_value='gnome'), \
             patch.object(refresh, '_gnome_state', return_value=state), \
             patch.object(refresh, '_run') as run:
            with self.assertRaises(ValueError):
                refresh.get_refresh_rates()
            self.assertFalse(refresh.set_refresh_rate(48)[0])
            run.assert_not_called()

    def test_manual_gnome_applies_full_layout_and_refuses_unsafe_layouts(self):
        state = gnome_state()
        with patch.object(refresh, 'availability', return_value=(True, 'GNOME')), \
             patch.object(refresh, 'backend_name', return_value='gnome'), \
             patch.object(refresh, '_gnome_state', return_value=state), \
             patch.object(refresh, '_run') as run:
            self.assertTrue(refresh.set_refresh_rate(48)[0])
            command = run.call_args.args[0]
            self.assertEqual(command[:4], ['gdctl', 'set', '--layout-mode', 'logical'])
            self.assertIn('lo', command)
            self.assertIn('external', command)
            run.reset_mock()
            state[1][1][2]['is-underscanning'] = True
            with self.assertRaisesRegex(ValueError, 'underscanning'):
                refresh.get_refresh_rates()
            self.assertFalse(refresh.set_refresh_rate(48)[0])
            run.assert_not_called()

    def test_manual_rate_validation_and_current_rate_require_no_display_write(self):
        with patch.object(refresh, 'availability', return_value=(True, 'GNOME')), \
             patch.object(refresh, 'backend_name', return_value='gnome'), \
             patch.object(refresh, '_gnome_state', return_value=gnome_state()), \
             patch.object(refresh, '_run') as run:
            self.assertTrue(refresh.set_refresh_rate(165)[0])
            for rate in (0, -1, float('nan'), float('inf'), 'invalid'):
                self.assertFalse(refresh.set_refresh_rate(rate)[0])
            run.assert_not_called()

    def test_kde_changes_only_internal_mode_at_current_resolution(self):
        state = kde_state()
        original = copy.deepcopy(state)
        self.assertEqual(refresh.kde_command(state, 'battery'),
                         ['kscreen-doctor', 'output.1.mode.lo'])
        self.assertEqual(state, original)
        self.assertIsNone(refresh.kde_command(state, 'ac'))
        state['outputs'][0]['currentModeId'] = 'lo'
        self.assertEqual(refresh.kde_command(state, 'usbc'),
                         ['kscreen-doctor', 'output.1.mode.hi'])

    def test_kde_disabled_unknown_and_cloned_panels_are_not_changed(self):
        for key, value in [('enabled', False), ('currentModeId', 'missing'),
                           ('replicationSource', 2)]:
            state = kde_state()
            state['outputs'][0][key] = value
            self.assertIsNone(refresh.kde_command(state, 'battery'))
        self.assertIsNone(refresh.kde_command(kde_state(), None))

    def test_kde_rejects_mode_id_command_injection_and_nonfinite_rates(self):
        state = kde_state()
        state['outputs'][0]['modes'][1]['id'] = 'lo.output.2.disable'
        with self.assertRaises(ValueError):
            refresh.kde_command(state, 'battery')
        state = kde_state()
        state['outputs'][0]['modes'][1]['refreshRate'] = float('nan')
        with self.assertRaises(ValueError):
            refresh.kde_command(state, 'battery')

    def test_gnome_preserves_layout_external_mode_scale_rotation_color(self):
        self.assertEqual(refresh.gnome_command(gnome_state(), 'battery'), [
            'gdctl', 'set', '--layout-mode', 'logical',
            '--logical-monitor', '--x', '0', '--y', '0', '--scale', '1.5',
            '--transform', 'normal', '--primary', '--monitor', 'eDP-1',
            '--mode', 'lo', '--color-mode', 'default',
            '--logical-monitor', '--x', '1707', '--y', '0', '--scale', '1.0',
            '--transform', '90', '--monitor', 'DP-1', '--mode', 'external',
            '--color-mode', 'bt2100'])
        self.assertIsNone(refresh.gnome_command(gnome_state(), 'usbc'))

    def test_gnome_refuses_ambiguous_flipped_transforms_without_applying(self):
        for transform in (6, 7):
            for output in (0, 1):
                with self.subTest(transform=transform, output=output):
                    state = gnome_state()
                    logical = list(state[2][output])
                    logical[3] = transform
                    state[2][output] = tuple(logical)
                    with patch.object(refresh, 'availability', return_value=(True, 'GNOME')), \
                         patch.object(refresh, 'backend_name', return_value='gnome'), \
                         patch.object(refresh, '_gnome_state', return_value=state), \
                         patch.object(refresh, '_run') as run, \
                         patch('rogcontrol.hardware.log'):
                        result = refresh.tick({'auto_display_refresh': True}, 'battery')
                        self.assertIn('flipped transform', result)
                        run.assert_not_called()

    def test_gnome_refuses_active_underscanning_without_applying(self):
        state = gnome_state()
        state[1][1][2]['is-underscanning'] = True
        with patch.object(refresh, 'availability', return_value=(True, 'GNOME')), \
             patch.object(refresh, 'backend_name', return_value='gnome'), \
             patch.object(refresh, '_gnome_state', return_value=state), \
             patch.object(refresh, '_run') as run, \
             patch('rogcontrol.hardware.log'):
            result = refresh.tick({'auto_display_refresh': True}, 'battery')
            self.assertIn('underscanning', result)
            run.assert_not_called()

    def test_gnome_preserves_variable_refresh_mode(self):
        state = gnome_state()
        state[1][0][1][0][6]['refresh-rate-mode'] = 'variable'
        self.assertIsNone(refresh.gnome_command(state, 'battery'))

    def test_failed_tick_logs_diagnostic(self):
        with patch.object(refresh, 'availability', return_value=(True, 'KDE')), \
             patch.object(refresh, 'backend_name', return_value='kde'), \
             patch.object(refresh, '_run', side_effect=OSError('connection lost')), \
             patch('rogcontrol.hardware.log') as log:
            refresh.tick({'auto_display_refresh': True}, 'battery')
            self.assertIn('connection lost', log.call_args.args[0])

    def test_gnome_skips_mirrored_internal_display(self):
        state = gnome_state()
        state[2][0][5].append(state[1][1][0])
        self.assertIsNone(refresh.gnome_command(state, 'battery'))

    def test_gnome_refuses_unknown_layout_and_missing_external_mode(self):
        state = gnome_state()
        state[3]['layout-mode'] = 99
        with self.assertRaises(ValueError):
            refresh.gnome_command(state, 'battery')
        state = gnome_state()
        state[1][1][1][0][6]['is-current'] = False
        with self.assertRaises(ValueError):
            refresh.gnome_command(state, 'battery')

    def test_disabled_or_unknown_power_does_not_even_query_display(self):
        with patch.object(refresh, 'availability', side_effect=AssertionError('queried')):
            self.assertIn('off', refresh.tick({}, 'battery').lower())
            self.assertIn('unknown', refresh.tick({'auto_display_refresh': True}, None).lower())

    def test_tick_applies_argv_without_shell_and_reports_failure(self):
        with patch.object(refresh, 'availability', return_value=(True, 'KDE')), \
             patch.object(refresh, 'backend_name', return_value='kde'), \
             patch.object(refresh, '_run') as run:
            import json
            run.return_value = json.dumps(kde_state())
            result = refresh.tick({'auto_display_refresh': True}, 'battery')
            self.assertIn('applied', result.lower())
            self.assertEqual(run.call_args.args[0], ['kscreen-doctor', 'output.1.mode.lo'])
            run.side_effect = OSError('cannot connect')
            self.assertIn('cannot connect', refresh.tick({'auto_display_refresh': True}, 'battery'))

    def test_availability_does_not_claim_other_desktop_support(self):
        with patch.dict('os.environ', {'XDG_CURRENT_DESKTOP': 'sway'}, clear=True):
            self.assertFalse(refresh.availability()[0])


class AutomaticRefreshPolicyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state_path = str(Path(directory.name) / 'refresh.json')
        self.state = kde_state()
        self.state['outputs'][0]['modes'].append(
            {'id': 'mid', 'size': {'width': 2560, 'height': 1600}, 'refreshRate': 120})
        self.state['outputs'][0]['currentModeId'] = 'mid'
        self.writes = []
        self.fail_apply = False
        self.cfg = {'auto_display_refresh': True, 'battery_display_refresh_hz': 48}
        for mock in (patch.object(refresh, 'availability', return_value=(True, 'KDE')),
                     patch.object(refresh, 'backend_name', return_value='kde'),
                     patch.object(refresh, '_run', side_effect=self.run_display),
                     patch('rogcontrol.hardware.log')):
            mock.start()
            self.addCleanup(mock.stop)

    def run_display(self, command):
        if command == ['kscreen-doctor', '--json']:
            return json.dumps(self.state)
        if self.fail_apply:
            raise OSError('Display temporarily unavailable')
        self.writes.append(command)
        for argument in command[1:]:
            _, output_id, _, mode_id = argument.split('.')
            for output in self.state['outputs']:
                if str(output['id']) == output_id:
                    output['currentModeId'] = mode_id
        return ''

    def tick(self, power, cfg=None):
        return refresh.tick(self.cfg if cfg is None else cfg, power, state_path=self.state_path)

    def test_ac_does_not_force_highest_and_battery_restores_original_on_both_supplies(self):
        for supply in ('ac', 'usbc'):
            self.tick(supply)
            self.assertEqual(self.state['outputs'][0]['currentModeId'], 'mid')
            self.tick('battery')
            self.assertEqual(self.state['outputs'][0]['currentModeId'], 'lo')
            self.assertTrue(Path(self.state_path).exists())
            self.tick(supply)
            self.assertEqual(self.state['outputs'][0]['currentModeId'], 'mid')
            self.assertFalse(Path(self.state_path).exists())
        self.assertEqual(len(self.writes), 4)

    def test_preference_edit_retains_original_snapshot_and_disable_restores(self):
        self.tick('battery')
        self.cfg['battery_display_refresh_hz'] = 165
        self.tick('battery')
        self.assertEqual(self.state['outputs'][0]['currentModeId'], 'hi')
        self.tick('battery', {})
        self.assertEqual(self.state['outputs'][0]['currentModeId'], 'mid')
        self.assertFalse(Path(self.state_path).exists())

    def test_saved_snapshot_restores_after_restart_and_retries_failed_restore(self):
        self.tick('battery')
        snapshot = Path(self.state_path).read_text()
        self.fail_apply = True
        self.assertIn('unchanged', self.tick('ac'))
        self.assertEqual(Path(self.state_path).read_text(), snapshot)
        self.fail_apply = False
        # tick reads disk each time; no process-owned controller state is needed.
        self.tick('usbc')
        self.assertEqual(self.state['outputs'][0]['currentModeId'], 'mid')
        self.assertFalse(Path(self.state_path).exists())

    def test_unknown_power_keeps_pending_restore_and_unsupported_rate_writes_nothing(self):
        self.cfg['battery_display_refresh_hz'] = 60
        self.assertIn('unchanged', self.tick('battery'))
        self.assertEqual(self.writes, [])
        self.assertFalse(Path(self.state_path).exists())
        self.cfg['battery_display_refresh_hz'] = None
        self.tick('battery')
        snapshot = Path(self.state_path).read_text()
        self.tick(None, {})
        self.assertEqual(Path(self.state_path).read_text(), snapshot)
        self.assertEqual(len(self.writes), 1)

    def test_differing_internal_rates_fail_without_partial_change(self):
        second = copy.deepcopy(self.state['outputs'][0])
        second.update(id=3, name='eDP-2', currentModeId='hi')
        self.state['outputs'].append(second)
        self.assertIn('unchanged', self.tick('battery'))
        self.assertEqual(self.writes, [])
        self.assertFalse(Path(self.state_path).exists())

    def test_failed_battery_write_keeps_original_and_retries(self):
        self.fail_apply = True
        self.tick('battery')
        self.assertTrue(Path(self.state_path).exists())
        self.fail_apply = False
        self.tick('battery')
        self.tick('ac')
        self.assertEqual(self.state['outputs'][0]['currentModeId'], 'mid')

    def test_partial_multi_panel_battery_write_can_restore_saved_original(self):
        second = copy.deepcopy(self.state['outputs'][0])
        second.update(id=3, name='eDP-2')
        self.state['outputs'].append(second)
        def partially_apply(command):
            if command == ['kscreen-doctor', '--json']:
                return json.dumps(self.state)
            self.state['outputs'][0]['currentModeId'] = 'lo'
            raise OSError('Second display write failed')
        with patch.object(refresh, '_run', side_effect=partially_apply):
            self.assertIn('unchanged', self.tick('battery'))
        self.assertEqual(self.state['outputs'][0]['currentModeId'], 'lo')
        self.assertEqual(second['currentModeId'], 'mid')
        self.assertTrue(Path(self.state_path).exists())
        self.tick('ac')
        self.assertEqual(self.state['outputs'][0]['currentModeId'], 'mid')
        self.assertEqual(second['currentModeId'], 'mid')
        self.assertFalse(Path(self.state_path).exists())

    def test_unsupported_restore_stays_pending_until_mode_returns(self):
        self.tick('battery')
        original = self.state['outputs'][0]['modes'].pop()
        snapshot = Path(self.state_path).read_text()
        self.assertIn('unchanged', self.tick('ac'))
        self.assertEqual(Path(self.state_path).read_text(), snapshot)
        self.assertEqual(len(self.writes), 1)
        self.state['outputs'][0]['modes'].append(original)
        self.tick('ac')
        self.assertEqual(self.state['outputs'][0]['currentModeId'], 'mid')

    def test_different_panel_does_not_receive_saved_restore_rate(self):
        self.tick('battery')
        self.state['outputs'][0]['name'] = 'eDP-2'
        self.assertIn('unchanged', self.tick('ac'))
        self.assertTrue(Path(self.state_path).exists())
        self.assertEqual(len(self.writes), 1)

    def test_snapshot_write_failure_prevents_display_change(self):
        with patch.object(refresh, '_save_restore', side_effect=OSError('Disk full')):
            self.assertIn('Disk full', self.tick('battery'))
        self.assertEqual(self.writes, [])

    def test_gnome_battery_selection_restores_previous_mode_and_preserves_external(self):
        state = gnome_state()
        writes = []
        def run_display(command):
            writes.append(command)
            mode_id = command[command.index('--mode') + 1]
            for mode in state[1][0][1]:
                mode[6]['is-current'] = mode[0] == mode_id
            return ''
        with patch.object(refresh, 'backend_name', return_value='gnome'), \
             patch.object(refresh, '_gnome_state', return_value=state), \
             patch.object(refresh, '_run', side_effect=run_display):
            self.tick('battery')
            self.assertTrue(state[1][0][1][1][6]['is-current'])
            self.tick('usbc')
            self.assertTrue(state[1][0][1][0][6]['is-current'])
        self.assertEqual(len(writes), 2)
        for command in writes:
            self.assertIn('external', command)
            self.assertIn('bt2100', command)


if __name__ == '__main__':
    unittest.main()
