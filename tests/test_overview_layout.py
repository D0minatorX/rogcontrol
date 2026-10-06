"""GTK regressions for dashboard geometry and system-theme styling."""
import unittest
from types import SimpleNamespace

import cairo
from rogcontrol.pages.overview import OverviewPage
from rogcontrol.ui import Adw, Gtk, apply_appearance


class OverviewLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        Gtk.Settings.get_default().set_property('gtk-enable-animations', False)

    def setUp(self):
        self.page = OverviewPage(SimpleNamespace(
            caps={}, config={'overview_layout': 'dashboard'}))
        self.page.cpu_temp_val.set_text('57.1 °C')
        self.page.ram_val.set_text('5.0 / 30.5 GiB')
        self.page.power_val.set_text('AC (barrel charger)')
        for row in self.page.fan_rows.values():
            row.set_subtitle('curve asks 2408 rpm (14%) at 53.5 °C')
        self.addCleanup(self.page._on_destroy, None)
        self.addCleanup(apply_appearance, {})

    def test_dashboard_keeps_two_columns_at_narrow_widths(self):
        flow = self.page.dashboard_flow
        for mode in ('system', 'light', 'dark'):
            apply_appearance({'appearance': mode})
            for width in (460, 520, 640, 800):
                with self.subTest(mode=mode, width=width):
                    flow.measure(Gtk.Orientation.HORIZONTAL, -1)
                    flow.measure(Gtk.Orientation.VERTICAL, width)
                    flow.allocate(width, 1200, -1, None)
                    first = flow.get_child_at_index(0).get_allocation()
                    second = flow.get_child_at_index(1).get_allocation()
                    self.assertEqual(first.y, second.y)
                    self.assertGreater(second.x, first.x)
                    self.assertLessEqual(second.x + second.width, width)

    def test_selector_buttons_have_a_visible_gap(self):
        selector = self.page.list_button.get_parent()
        selector.allocate(300, 50, -1, None)
        first = self.page.list_button.get_allocation()
        second = self.page.dashboard_button.get_allocation()
        self.assertGreaterEqual(second.x - first.x - first.width, 6)

    def test_card_styling_survives_switching_back_to_system(self):
        card = self.page.dashboard_flow.get_child_at_index(0).get_child()
        for mode in ('light', 'dark', 'system'):
            with self.subTest(mode=mode):
                apply_appearance({'appearance': mode})
                style = card.get_style_context()
                self.assertGreaterEqual(style.get_padding().left, 12)
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 80, 80)
                Gtk.render_background(style, cairo.Context(surface), 0, 0, 80, 80)
                surface.flush()
                alpha = bytes(surface.get_data())[40 * surface.get_stride() + 40 * 4 + 3]
                self.assertGreater(alpha, 0)
