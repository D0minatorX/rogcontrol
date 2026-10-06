"""Gamescope transitions use real config/state files, without hardware writes."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rogcontrol import config


class GamescopeTests(unittest.TestCase):
    def setUp(self):
        from rogcontrol import gamescope
        self.gamescope = gamescope
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_path = Path(self.temp.name) / "config.json"
        self.state_path = Path(self.temp.name) / "state.json"
        self.cfg = {"current_profile": "Balanced Power",
                    "gamescope_profile": "Performance",
                    "profiles": {"Balanced Power": {}, "Performance": {},
                                 "Quiet": {}}, "charge_limit": 80}
        config.save_config(self.cfg, str(self.config_path))

    def tick(self, active, acknowledge=True):
        target = self.gamescope.reconcile(
            active, config_path=str(self.config_path),
            state_path=str(self.state_path))
        if target is not None and acknowledge:
            self.gamescope.complete_transition(state_path=str(self.state_path))
        return target

    def saved(self):
        return json.loads(self.config_path.read_text())

    def test_entry_switches_and_exit_restores_exact_profile(self):
        self.assertEqual(self.tick(True), "Performance")
        self.assertEqual(self.saved()["current_profile"], "Performance")
        self.assertIsNone(self.tick(True))
        self.assertEqual(self.tick(False), "Balanced Power")
        self.assertEqual(self.saved()["charge_limit"], 80)
        self.assertFalse(self.state_path.exists())

    def test_interrupted_hardware_apply_retries_entry_and_exit(self):
        self.assertEqual(self.tick(True, acknowledge=False), "Performance")
        self.assertEqual(self.saved()["current_profile"], "Performance")
        self.assertEqual(self.tick(True), "Performance")
        self.assertIsNone(self.tick(True))
        self.assertEqual(self.tick(False, acknowledge=False), "Balanced Power")
        self.assertTrue(self.state_path.exists())
        self.assertEqual(self.tick(False), "Balanced Power")
        self.assertFalse(self.state_path.exists())

    def test_restart_and_manual_change_do_not_replace_original_profile(self):
        self.tick(True)
        cfg = self.saved()
        cfg["current_profile"] = "Quiet"
        cfg["charge_limit"] = 65
        config.save_config(cfg, str(self.config_path))
        self.assertIsNone(self.tick(True))
        self.assertEqual(self.tick(False), "Balanced Power")
        self.assertEqual(self.saved()["charge_limit"], 65)

    def test_disabled_or_deleted_target_does_not_switch(self):
        for target in (None, "Deleted"):
            self.cfg["gamescope_profile"] = target
            config.save_config(self.cfg, str(self.config_path))
            self.assertIsNone(self.tick(True))
            self.assertEqual(self.saved()["current_profile"], "Balanced Power")
            self.assertFalse(self.state_path.exists())

    def test_disabling_during_session_restores_previous_profile(self):
        self.tick(True)
        cfg = self.saved()
        cfg["gamescope_profile"] = None
        config.save_config(cfg, str(self.config_path))
        self.assertEqual(self.tick(True), "Balanced Power")
        self.assertFalse(self.state_path.exists())

    def test_changing_session_selection_preserves_original(self):
        self.tick(True)
        cfg = self.saved()
        cfg["gamescope_profile"] = "Quiet"
        config.save_config(cfg, str(self.config_path))
        self.assertEqual(self.tick(True), "Quiet")
        self.assertEqual(self.tick(False), "Balanced Power")

    def test_failed_session_probe_does_not_restore(self):
        self.tick(True)
        self.assertIsNone(self.tick(None))
        self.assertEqual(self.saved()["current_profile"], "Performance")
        self.assertTrue(self.state_path.exists())

    def test_login_restores_before_first_hardware_apply(self):
        spec = importlib.util.spec_from_file_location(
            "gamescope_test_apply", Path(__file__).parents[1] /
            "rogcontrol" / "rogcontrol-apply.py")
        login = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(login)
        for active, args, expected in (
                (False, [], "Balanced Power"),
                (True, [], "Performance"),
                (None, [], "Performance"),
                (False, ["--profile-only"], "Performance")):
            with self.subTest(active=active, args=args):
                config.save_config(self.cfg, str(self.config_path))
                self.state_path.unlink(missing_ok=True)
                self.tick(True)  # Power off without an exit transition.
                applied = []
                with patch.object(login, "CONFIG_PATH", str(self.config_path)), \
                        patch.object(config, "CONFIG_PATH", str(self.config_path)), \
                        patch.object(self.gamescope, "STATE_PATH", str(self.state_path)), \
                        patch.object(self.gamescope, "session_active", return_value=active), \
                        patch.object(config, "record_boot_attempt", return_value=False), \
                        patch.object(login, "_spawn_survival_watchdog"), \
                        patch.object(login, "RETRIES", 1), \
                        patch.object(login, "apply_once", side_effect=lambda cfg, **kw:
                                     applied.append(cfg["current_profile"])):
                    login.main(args)
                self.assertEqual(applied, [expected])
                self.assertEqual(self.saved()["current_profile"], expected)
                # Keep recovery pending until the enforcer acknowledges it.
                self.assertTrue(self.state_path.exists())

    def test_deleted_previous_profile_keeps_valid_current_profile(self):
        self.tick(True)
        cfg = self.saved()
        del cfg["profiles"]["Balanced Power"]
        config.save_config(cfg, str(self.config_path))
        self.assertIsNone(self.tick(False))
        self.assertEqual(self.saved()["current_profile"], "Performance")
        self.assertFalse(self.state_path.exists())

    def test_failed_config_save_keeps_restore_record_for_retry(self):
        self.tick(True)
        with patch.object(config, "save_config", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.tick(False)
        self.assertTrue(self.state_path.exists())
        self.assertEqual(self.tick(False), "Balanced Power")

    def test_deleting_profile_clears_gamescope_selection(self):
        config.delete_profile(self.cfg, "Performance")
        self.assertIsNone(self.cfg["gamescope_profile"])

    def test_detection_accepts_binary_or_installed_session(self):
        with patch.object(self.gamescope.shutil, "which", return_value="/usr/bin/gamescope"):
            self.assertTrue(self.gamescope.detect_installed())
        for output, expected in (("loaded\nnot-found\n", True),
                                 ("not-found\nnot-found\n", False)):
            with patch.object(self.gamescope.shutil, "which", return_value=None), \
                    patch.object(self.gamescope.subprocess, "run", return_value=
                                 subprocess.CompletedProcess([], 0, output, "")):
                self.assertEqual(self.gamescope.detect_installed(), expected)

    def test_session_detection_distinguishes_inactive_from_probe_failure(self):
        for output, expected in (("active\ninactive\n", True),
                                 ("inactive\nactive\n", True),
                                 ("inactive\ninactive\n", False),
                                 ("deactivating\ninactive\n", False),
                                 ("activating\ninactive\n", None),
                                 ("", None)):
            with patch.object(self.gamescope.subprocess, "run", return_value=
                              subprocess.CompletedProcess([], 0, output, "")):
                self.assertIs(self.gamescope.session_active(), expected)
        with patch.object(self.gamescope.subprocess, "run", side_effect=
                          subprocess.TimeoutExpired("systemctl", 3)):
            self.assertIsNone(self.gamescope.session_active())

    def test_enforcer_prioritizes_session_and_restores_before_power_switch(self):
        spec = importlib.util.spec_from_file_location(
            "gamescope_test_enforcer", Path(__file__).parents[1] /
            "rogcontrol" / "rogcontrol-enforcer.py")
        enforcer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(enforcer)
        self.cfg.update(ac_profile="Performance", battery_profile="Quiet")
        config.save_config(self.cfg, str(self.config_path))
        enforcer._last_ac_state = True
        enforcer._last_charger_kind = "mains"
        with patch.object(enforcer, "CONFIG_PATH", str(self.config_path)), \
                patch.object(self.gamescope, "STATE_PATH", str(self.state_path)), \
                patch.object(self.gamescope, "session_active", return_value=True) as active, \
                patch.object(enforcer.hardware, "read_power_source", return_value=(False, None)) as power, \
                patch.object(enforcer, "store_last_ac_state"), \
                patch.object(enforcer, "store_last_charger_kind"), \
                patch.object(enforcer, "charger_flash"), \
                patch.object(enforcer, "notify"), \
                patch.object(enforcer, "log"), \
                patch.object(enforcer, "apply_full_profile") as apply, \
                patch.object(enforcer, "set_ppd_active_profile") as ppd:
            self.assertTrue(enforcer.check_ac_auto_switch(self.cfg, "ppd"))
            self.assertEqual(self.saved()["current_profile"], "Performance")
            apply.assert_called_once_with(self.cfg, {}, force_fan_reapply=True, full=True)
            ppd.assert_called_once_with("ppd", "performance")
            self.cfg["current_profile"] = "Balanced Power"  # Read before another thread switched.
            power.return_value = (True, "mains")
            self.assertFalse(enforcer.check_ac_auto_switch(self.cfg, "ppd"))
            self.assertEqual(self.cfg["current_profile"], "Performance")
            self.assertEqual(self.saved()["current_profile"], "Performance")
            active.return_value = False
            power.return_value = (False, None)
            self.assertTrue(enforcer.check_ac_auto_switch(self.cfg, "ppd"))
            self.assertEqual(self.saved()["current_profile"], "Balanced Power")


if __name__ == "__main__":
    unittest.main()
