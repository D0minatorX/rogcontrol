"""Pending edits remain explicit without writing hardware during editing."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rogcontrol import config, hardware
from rogcontrol.ui import Adw
from rogcontrol.pages.cpu import CpuPage
from rogcontrol.pages.gpu import GpuPage
from rogcontrol.pages.gpu_supergfx import GpuPage as SupergfxPage
from rogcontrol.pages.fans import FansPage
from rogcontrol.pages.quick_access import QuickAccessPage


class PendingChangesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        for name, value in {"dgpu_available": True, "read_nv_dynamic_boost": 10,
                            "read_nv_temp_target": 80, "read_cpu_name": "Test CPU"}.items():
            self.enterContext(patch.object(hardware, name, return_value=value))
        self.save = self.enterContext(patch.object(config, "save_config"))
        self.helper = self.enterContext(patch.object(hardware, "run_helper"))

    def page(self, cls):
        profile = {"gpu": {"watts": 90}, "cpu": {"boost": True},
                   "fans": {ch: [[40, 20], [90, 90]] for ch in hardware.FAN_CHANNELS}}
        window = SimpleNamespace(
            caps={"gpu_power_limit": True, "cpu_boost": True, "fan_curve": True},
            config={"profiles": {"Balanced": profile}},
            current_profile=lambda: profile, current_profile_name=lambda: "Balanced",
            toast=Mock(), apply_async=Mock(), claim_hardware=Mock(return_value=True),
            release_hardware=Mock())
        page = cls(window)
        self.addCleanup(page._on_destroy, None)
        return page

    def edit(self, page):
        if isinstance(page, CpuPage):
            row = page.rows["boost"]
            row.set_active(False)
        else:
            row = page.rows["watts"]
            row.get_adjustment().set_value(80)
        return row

    def test_cpu_and_both_gpu_pages_show_and_discard_pending_edits(self):
        for cls in (CpuPage, GpuPage, SupergfxPage):
            with self.subTest(page=cls.__module__):
                page = self.page(cls)
                row = self.edit(page)
                self.assertEqual(page.pending.count, 1)
                self.assertTrue(page.pending.get_reveal_child())
                self.assertTrue(row.has_css_class("pending-change"))
                self.assertIn("1 unapplied change", page.pending.label.get_text())
                page.pending.discard_button.emit("clicked")
                self.assertEqual(page.pending.count, 0)
                self.assertFalse(row.has_css_class("pending-change"))
                self.helper.assert_not_called()
                self.save.assert_not_called()

    def test_navigation_warns_and_discards_edits_as_before(self):
        for cls in (CpuPage, GpuPage, SupergfxPage):
            with self.subTest(page=cls.__module__):
                page = self.page(cls)
                self.edit(page)
                self.assertTrue(page.pending.warning.get_visible())
                self.assertIn("Leaving this page discards", page.pending.warning.get_text())
                page._on_unmap(None)
                self.assertEqual(page.pending.count, 0)

    def test_pending_actions_follow_hardware_busy_state(self):
        for cls in (CpuPage, GpuPage, SupergfxPage, FansPage):
            with self.subTest(page=cls.__name__):
                page = self.page(cls)
                if cls is FansPage:
                    self.edit_fan(page)
                else:
                    self.edit(page)
                page.set_hardware_busy(True)
                self.assertFalse(page.pending.apply_button.get_sensitive())
                self.assertFalse(page.pending.discard_button.get_sensitive())
                page.set_hardware_busy(False)
                self.assertTrue(page.pending.apply_button.get_sensitive())

    def test_pending_edits_do_not_replace_cpu_safety_warning(self):
        page = self.page(CpuPage)
        page.window.config['safety_tripped'] = True
        self.edit(page)
        self.assertTrue(page.banner.get_revealed())
        self.assertIn('boot failures', page.banner.get_title())
        self.assertEqual(page.pending.count, 1)

    def edit_fan(self, page):
        page.editors['1'].set_points([[40, 30], [90, 90]])
        page._on_curve_changed(page.editors['1'])

    def test_fan_changes_are_distinct_from_firmware_drift_and_can_be_discarded(self):
        page = self.page(FansPage)
        original = page.editors['1'].get_points()
        page._hw_enabled = {'1': False}
        page._update_banner()
        self.assertEqual(page.pending.count, 0)
        self.assertTrue(page.banner.get_revealed())
        self.edit_fan(page)
        self.assertEqual(page.pending.count, 1)
        self.assertTrue(page.fan_groups['1'].has_css_class('pending-change'))
        page.pending.discard_button.emit('clicked')
        self.assertEqual(page.pending.count, 0)
        self.assertTrue(page.banner.get_revealed())
        self.assertEqual(page.editors['1'].get_points(), original)
        self.helper.assert_not_called()

    def test_quick_access_exposes_staged_cpu_change_and_discard(self):
        cpu = self.page(CpuPage)
        quick = QuickAccessPage(cpu.window, {'cpu': cpu})
        self.edit(cpu)
        self.assertEqual(quick.cpu_pending.count, 1)
        quick.cpu_pending.discard_button.emit('clicked')
        self.assertEqual(cpu.pending.count, 0)
        self.assertEqual(quick.cpu_pending.count, 0)

    def test_quick_access_warns_and_discards_cpu_edits_when_leaving(self):
        cpu = self.page(CpuPage)
        quick = QuickAccessPage(cpu.window, {'cpu': cpu})
        self.edit(cpu)
        self.assertTrue(quick.cpu_pending.warning.get_visible())
        quick.emit('unmap')
        self.assertEqual(cpu.pending.count, 0)
        self.assertEqual(quick.cpu_pending.count, 0)
        self.helper.assert_not_called()

    def test_partial_gpu_failure_updates_pending_state_without_hiding_error(self):
        page = self.page(GpuPage)
        self.edit(page)
        with patch.object(config, 'save_deferred', return_value=None):
            page._on_applied('Balanced', [('watts', 80, False, 'refused')], None)
        self.assertEqual(page.pending.count, 0)
        self.assertTrue(page.banner.get_revealed())
        self.assertIn('not applied', page.banner.get_title())


if __name__ == '__main__':
    unittest.main()
