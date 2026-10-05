"""GTK tests for optional Gamescope Quick Access controls."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rogcontrol import config
from rogcontrol.pages.quick_access import QuickAccessPage
from rogcontrol.ui import Adw


class GamescopeQuickAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def test_gamescope_settings_hidden_until_successful_recheck(self):
        window = SimpleNamespace(
            caps={}, config={"profiles": {"Quiet": {}, "Performance": {}}},
            apply_async=Mock(), toast=Mock())
        quick = QuickAccessPage(window, {})
        self.assertFalse(quick.gamescope_row.get_visible())
        self.assertTrue(quick.gamescope_check_row.get_visible())
        with patch("rogcontrol.gamescope.detect_installed", return_value=True):
            quick.gamescope_recheck.emit("clicked")
            probe, completed = window.apply_async.call_args.args
            completed(probe(), None)
        self.assertTrue(quick.gamescope_row.get_visible())
        self.assertTrue(window.caps["gamescope"])
        self.assertEqual(quick.gamescope_row.get_model().get_string(0),
                         "Don't auto-switch")
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(config, "CONFIG_PATH", str(Path(directory) / "config.json")):
            quick.gamescope_row.set_selected(2)
            self.assertEqual(config.load_config()["gamescope_profile"], "Performance")
            config.delete_profile(window.config, "Performance")
            quick.reload()
            self.assertEqual(quick.gamescope_row.get_selected(), 0)

    def test_gamescope_installed_uses_saved_profile(self):
        window = SimpleNamespace(caps={"gamescope": True}, config={
            "profiles": {"Quiet": {}, "Performance": {}},
            "gamescope_profile": "Quiet"})
        quick = QuickAccessPage(window, {})
        self.assertTrue(quick.gamescope_row.get_visible())
        self.assertEqual(quick.gamescope_row.get_selected(), 1)


if __name__ == "__main__":
    unittest.main()
