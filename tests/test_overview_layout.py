"""Overview presentation preferences and shared live readings (GTK required)."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rogcontrol import config
from rogcontrol.pages.overview import OverviewPage
from rogcontrol.ui import Adw, Gtk, apply_appearance


class OverviewLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def page(self, settings=None):
        window = SimpleNamespace(config=settings or {}, caps={},
                                 current_profile=lambda: {}, apply_async=Mock())
        page = OverviewPage(window)
        self.addCleanup(page._on_destroy, None)
        return page

    def test_existing_missing_and_invalid_preferences_open_list(self):
        for settings in ({}, {'overview_layout': 'list'},
                         {'overview_layout': 'unknown'}, {'overview_layout': []}):
            with self.subTest(settings=settings):
                page = self.page(settings)
                self.assertTrue(hasattr(page, 'list_button'), 'Overview needs a layout selector')
                self.assertTrue(page.list_button.get_active())
                self.assertFalse(page.dashboard_group.get_visible())

    def test_switch_persists_and_restores_without_hardware_work(self):
        page = self.page()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'config.json')
            with patch.object(config, 'CONFIG_PATH', path):
                self.assertTrue(hasattr(page, 'dashboard_button'), 'Dashboard selector is missing')
                page.dashboard_button.set_active(True)
                self.assertEqual(config.load_config()['overview_layout'], 'dashboard')
                restored = self.page(config.load_config())
                self.assertTrue(restored.dashboard_group.get_visible())
                restored.list_button.set_active(True)
                self.assertEqual(config.load_config()['overview_layout'], 'list')
        page.window.apply_async.assert_not_called()

    def test_both_views_keep_the_same_readings_and_status(self):
        page = self.page()
        self.assertTrue(hasattr(page, 'dashboard_values'), 'Dashboard readings are missing')
        page.window.current_profile = lambda: {'fans': {'1': [[20, 50], [100, 50]]}}
        page.window.config['fan_rpm_cal'] = {'1': [1000, 40]}
        page._render({'cpu_temp': 64.5, 'cpu_clock': 4200, 'pkg_power': 31.2,
                      'dgpu_suspended': True, 'ram': (8192, 32768),
                      'battery': (80, False), 'charge_limit': 80,
                      'power_source': (True, 'usb'), 'fan_rpm': {'1': 2400},
                      'curve_enabled': {'1': False}})
        with patch.object(config, 'save_config'):
            page.dashboard_button.set_active(True)
            for name, expected in [('cpu_temp', '64.5 °C'), ('gpu_temp', 'Idle'),
                                   ('vram', 'Idle'), ('ram', '8.0 / 32.0 GiB'),
                                   ('battery', '80%'), ('curve', 'dropped')]:
                self.assertEqual(page.dashboard_values[name].get_text(), expected)
            self.assertEqual(page.dashboard_values['fan_1'].get_text(), '2400 rpm')
            self.assertEqual(page.dashboard_details['fan_1'].get_text(),
                             'curve asks 3000 rpm (50%) at 64.5 °C')
            self.assertEqual(page.dashboard_values['limit'].get_text(), '80%')
            self.assertEqual(page.dashboard_values['power'].get_text(), 'AC (USB-C)')
            self.assertEqual(page.dashboard_details['battery'].get_text(),
                             'on battery or held at limit')
            self.assertIn('warning', page.dashboard_values['curve'].get_css_classes())
            self.assertIn('taken', page.dashboard_details['curve'].get_text())
            page.list_button.set_active(True)
            self.assertEqual(page.cpu_temp_val.get_text(), '64.5 °C')
            page._render({})
            self.assertEqual(page.dashboard_values['cpu_temp'].get_text(), '—')
            self.assertEqual(page.dashboard_values['curve'].get_text(), 'not available')
            self.assertNotIn('warning', page.dashboard_values['curve'].get_css_classes())

    def test_dashboard_wraps_to_one_column_at_360(self):
        page = self.page({'overview_layout': 'dashboard'})
        self.assertTrue(hasattr(page, 'dashboard_flow'), 'Adaptive dashboard is missing')
        flow = page.dashboard_flow
        flow.allocate(360, 1600, -1, None)
        first = flow.get_child_at_index(0).compute_bounds(flow)[1]
        second = flow.get_child_at_index(1).compute_bounds(flow)[1]
        self.assertEqual(first.get_x(), second.get_x())
        self.assertGreater(second.get_y(), first.get_y())
        flow.allocate(760, 1600, -1, None)
        first = flow.get_child_at_index(0).compute_bounds(flow)[1]
        second = flow.get_child_at_index(1).compute_bounds(flow)[1]
        self.assertEqual(first.get_y(), second.get_y())
        self.assertGreater(second.get_x(), first.get_x())

    def test_dashboard_fits_compact_three_rows_at_desktop_width(self):
        apply_appearance({})
        page = self.page({'overview_layout': 'dashboard'})
        page._render({'cpu_temp': 64.5, 'cpu_clock': 4200, 'pkg_power': 31.2,
                      'gpu_temp': 52, 'gpu_power': 18.5, 'vram': (2048, 12288),
                      'ram': (8192, 32768), 'battery': (80, True),
                      'charge_limit': 80, 'power_source': (True, 'mains'),
                      'fan_rpm': {'1': 2300, '2': 2200, '3': 2400},
                      'curve_enabled': {'1': True, '2': True, '3': True}})
        # Leave room for the window header and layout picker at 800px high.
        self.assertLessEqual(page.dashboard_flow.measure(
            Gtk.Orientation.VERTICAL, 700).natural, 660)
