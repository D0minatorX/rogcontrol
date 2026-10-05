"""System navigation keeps backend controls and relocated rows functional."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rogcontrol import config, hardware
from rogcontrol.ui import Adw
from rogcontrol.pages import system, system_supergfx
from rogcontrol.pages.quick_access import QuickAccessPage


class SystemSectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        for name, value in (("asusd_uninstall_command", None),
                            ("read_log_tail", ""), ("read_fan_boost", None)):
            self.enterContext(patch.object(hardware, name, return_value=value))
        self.enterContext(patch.object(config, "save_config"))

    def page(self, cls):
        window = SimpleNamespace(
            caps={"boot_sound": True, "panel_od": True, "psr_toggle": True,
                  "fan_curve": True}, config={},
            current_profile_name=lambda: "Balanced", apply_async=Mock(),
            apply_isolated=Mock(), toast=Mock())
        page = cls(window)
        self.addCleanup(page._on_destroy, None)
        return page

    def test_sections_keep_controls_reachable_for_both_backends(self):
        for cls, daemon in ((system.SystemPage, "cardwire_row"),
                            (system_supergfx.SystemPage, "supergfx_row")):
            with self.subTest(backend=cls.__module__):
                page = self.page(cls)
                self.assertTrue(hasattr(page, "section_selector"),
                                "System needs section navigation")
                for index, (section, attrs) in enumerate((
                    ("general", ("appearance_row", "sync_row", "boot_sound_row")),
                    ("services", (daemon, "asusd_row")),
                    ("updates", ("update_row", "update_auto_row")),
                    ("diagnostics", ("report_button", "log_view")),
                )):
                    page.section_selector.set_selected(index)
                    visible = page.section_stack.get_visible_child()
                    self.assertEqual(page.section_stack.get_visible_child_name(), section)
                    for attr in attrs:
                        self.assertEqual(getattr(page, attr).get_ancestor(
                            Adw.PreferencesPage), visible)

    def test_updates_keep_check_and_automatic_check_behavior(self):
        for cls in (system.SystemPage, system_supergfx.SystemPage):
            with self.subTest(backend=cls.__module__):
                page = self.page(cls)
                self.assertTrue(hasattr(page, "update_auto_row"))
                page.update_auto_row.set_selected(2)
                self.assertEqual(page.window.config["update_check"], "daily")
                page.update_check_button.emit("clicked")
                self.assertFalse(page.update_check_button.get_sensitive())
                self.assertEqual(page.update_row.get_subtitle(), "Checking…")
                page.updates._on_update_checked({"available": False}, None)
                self.assertTrue(page.update_check_button.get_sensitive())
                self.assertIn("Up to date", page.update_row.get_subtitle())

    def test_quick_access_moves_original_rows_out_of_general(self):
        for cls in (system.SystemPage, system_supergfx.SystemPage):
            with self.subTest(backend=cls.__module__):
                page = self.page(cls)
                self.assertTrue(hasattr(page, "section_stack"))
                quick = QuickAccessPage(page.window, {"system": page})
                for attr in ("boot_sound_row", "panel_od_row", "psr_row",
                             "psr_pending_row", "fan_boost_row"):
                    self.assertEqual(getattr(page, attr).get_ancestor(
                        Adw.PreferencesPage), quick)
                page._render_boot_sound(True)
                self.assertTrue(page.boot_sound_row.get_active())
                self.assertFalse(page.firmware_group.get_visible())
                self.assertFalse(page.fan_boost_group.get_visible())
                self.assertEqual(page.appearance_row.get_ancestor(
                    Adw.PreferencesPage), page.section_stack.get_child_by_name("general"))


if __name__ == "__main__":
    unittest.main()
