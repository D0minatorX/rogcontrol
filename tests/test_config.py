"""The config file: migrating it forward, and getting it on and off disk.

Every test here works on a temporary directory. Nothing in this module may
read or write ~/.config/rogcontrol.json -- that is the user's real config,
with their real profiles in it. The tests that exercise the default path do
it by patching ``config.CONFIG_PATH``, which is why load/save resolve the
default at call time rather than binding it in the signature.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from rogcontrol import config
from rogcontrol import profiles


def _require_default_path_resolved_at_call_time():
    """Refuse to run if load/save ever bind CONFIG_PATH into their signature.

    The default-path tests are only harmless because they patch
    ``config.CONFIG_PATH``, and that only redirects anything while load/save
    read the name when they are called. Refactor either of them to the
    natural-looking ``def save_config(cfg, path=CONFIG_PATH)`` and the real
    ~/.config path is bound at import, the patch quietly stops having any
    effect, and the next bare ``save_config(cfg)`` writes over the user's
    actual profiles -- there is no getting them back once os.replace runs.
    So this fails closed: no test in this file runs at all.
    """
    for func in (config.save_config, config.load_config):
        if func.__defaults__ != (None,):
            raise AssertionError(
                f"rogcontrol.config.{func.__name__} no longer defaults its "
                f"path to None (__defaults__ = {func.__defaults__!r}). "
                "Patching config.CONFIG_PATH can no longer redirect it, so "
                "these tests would write to the user's real config. Restore "
                "`path=None` plus `path = CONFIG_PATH if path is None else "
                "path` in the function body."
            )


def setUpModule():
    # Before any test in this file gets to call load/save.
    _require_default_path_resolved_at_call_time()


class Migration(unittest.TestCase):
    def test_empty_config_gets_stock_profiles(self):
        out = config.migrate_config({})
        self.assertEqual(list(out["profiles"]),
                         ["Quiet", "Balanced Power",
                          "Balanced Performance", "Performance"])
        self.assertEqual(out["config_version"], 1)

    def test_user_values_are_never_touched(self):
        cfg = {"profiles": {"Mine": {"cpu": {"stapm": 12345}, "gpu": {},
                                     "fans": {"1": [[50, 5]]}}},
               "current_profile": "Mine", "charge_limit": 61}
        out = config.migrate_config(json.loads(json.dumps(cfg)))
        self.assertEqual(out["profiles"]["Mine"]["cpu"]["stapm"], 12345)
        self.assertEqual(out["profiles"]["Mine"]["fans"]["1"], [[50, 5]])
        self.assertEqual(out["charge_limit"], 61)

    def test_deleted_stock_profile_stays_deleted(self):
        cfg = config.migrate_config({})
        del cfg["profiles"]["Quiet"]
        cfg["current_profile"] = "Performance"
        out = config.migrate_config(cfg)
        self.assertNotIn("Quiet", out["profiles"])

    def test_missing_section_is_filled_from_the_stock_profile(self):
        cfg = {"profiles": {"Quiet": {"cpu": {"stapm": 1}}},
               "current_profile": "Quiet"}
        out = config.migrate_config(cfg)
        self.assertIn("fans", out["profiles"]["Quiet"])
        self.assertIn("gpu", out["profiles"]["Quiet"])

    def test_current_profile_must_exist(self):
        out = config.migrate_config({"profiles": {"A": {"cpu": {}, "gpu": {},
                                                        "fans": {}}},
                                     "current_profile": "Gone"})
        self.assertEqual(out["current_profile"], "A")

    def test_unknown_keys_survive(self):
        out = config.migrate_config({"something_from_a_newer_build": 7})
        self.assertEqual(out["something_from_a_newer_build"], 7)

    def test_idempotent(self):
        once = config.migrate_config({})
        twice = config.migrate_config(json.loads(json.dumps(once)))
        self.assertEqual(once, twice)

    # --- the same rules, pinned harder ------------------------------------

    def test_missing_top_level_keys_are_filled_from_the_defaults(self):
        out = config.migrate_config({})
        for key, value in config.DEFAULT_CONFIG.items():
            self.assertEqual(out[key], value, key)

    def test_a_present_top_level_key_is_not_overwritten_by_its_default(self):
        # Every default in turn, so a future key added to DEFAULT_CONFIG is
        # covered without anyone remembering to extend this test.
        # current_profile is excluded: it is the one value migration may
        # rewrite, because it has to name a profile that exists. Its own
        # rules are pinned by the two current_profile tests below.
        for key, value in config.DEFAULT_CONFIG.items():
            if key == "current_profile":
                continue
            mine = "mine" if isinstance(value, str) else 0
            out = config.migrate_config({key: mine})
            self.assertEqual(out[key], mine, key)

    def test_mutable_defaults_are_copied_per_config(self):
        # window_size is a list. Handing out the same list object to every
        # config would make one window's resize follow every other config.
        a = config.migrate_config({})
        b = config.migrate_config({})
        a["window_size"][0] = 1234
        self.assertEqual(b["window_size"], config.DEFAULT_CONFIG["window_size"])
        self.assertNotEqual(config.DEFAULT_CONFIG["window_size"][0], 1234)

    def test_an_existing_current_profile_is_kept(self):
        out = config.migrate_config({"current_profile": "Quiet"})
        self.assertEqual(out["current_profile"], "Quiet")

    def test_current_profile_falls_back_to_the_first_profile(self):
        # "First" means the order the profiles are written in -- the order
        # they appear in the menu -- not alphabetical and not arbitrary.
        # The name expected here is deliberately neither the alphabetically
        # first nor the alphabetically last of the three, because for the
        # stock profile names those orderings happen to coincide with this
        # one and would let a sorted() fallback pass unnoticed.
        empty = {"cpu": {}, "gpu": {}, "fans": {}}
        out = config.migrate_config(
            {"profiles": {"Middle": dict(empty), "Zed": dict(empty),
                          "Alpha": dict(empty)},
             "current_profile": "Gone"})
        self.assertEqual(out["current_profile"], "Middle")

    def test_profiles_that_are_not_a_dict_are_replaced_with_the_stock_set(self):
        for junk in ([], "", None, 0, ["Quiet"]):
            out = config.migrate_config({"profiles": junk})
            self.assertEqual(list(out["profiles"]), list(profiles.DEFAULT_PROFILES),
                             repr(junk))

    def test_an_unknown_profile_is_completed_from_balanced_performance(self):
        out = config.migrate_config({"profiles": {"Mine": {"cpu": {"stapm": 1}}}})
        base = profiles.DEFAULT_PROFILES["Balanced Performance"]
        self.assertEqual(out["profiles"]["Mine"]["fans"], base["fans"])
        self.assertEqual(out["profiles"]["Mine"]["gpu"], base["gpu"])
        self.assertEqual(out["profiles"]["Mine"]["cpu"], {"stapm": 1})

    def test_a_filled_section_is_a_copy_not_shared_with_the_stock_table(self):
        # Sharing the list objects would let editing one profile's curve in
        # the app rewrite the stock table for every profile after it.
        out = config.migrate_config({"profiles": {"Quiet": {"cpu": {}}}})
        out["profiles"]["Quiet"]["fans"]["1"][0][1] = 99
        self.assertEqual(profiles.DEFAULT_PROFILES["Quiet"]["fans"]["1"][0],
                         [40, 25])

    def test_a_profile_that_is_not_a_dict_is_left_alone(self):
        out = config.migrate_config({"profiles": {"Quiet": {"cpu": {}, "gpu": {},
                                                            "fans": {}},
                                                  "Junk": "not a profile"}})
        self.assertEqual(out["profiles"]["Junk"], "not a profile")

    def test_gpu_watts_follow_the_card_that_is_fitted(self):
        # The hardware limits arrive as arguments now; a migration that
        # ignored them would hand a 70W card the 140W stock numbers.
        out = config.migrate_config({}, gpu_min_w=5, gpu_max_w=70)
        self.assertEqual(out["profiles"]["Performance"]["gpu"]["watts"], 70)
        self.assertEqual(out["profiles"]["Quiet"]["gpu"]["watts"],
                         profiles.tailored_default_profiles(5, 70)["Quiet"]["gpu"]["watts"])

    def test_the_default_card_is_the_reference_140w_one(self):
        out = config.migrate_config({})
        for name, prof in profiles.DEFAULT_PROFILES.items():
            self.assertEqual(out["profiles"][name]["gpu"]["watts"],
                             prof["gpu"]["watts"], name)

    def test_hardware_limits_are_ignored_when_profiles_already_exist(self):
        # Tailoring is a fresh-install act. An update must never rewrite
        # wattages the user has set.
        cfg = {"profiles": {"Quiet": {"cpu": {}, "gpu": {"watts": 130}, "fans": {}}},
               "current_profile": "Quiet"}
        out = config.migrate_config(cfg, gpu_min_w=5, gpu_max_w=70)
        self.assertEqual(out["profiles"]["Quiet"]["gpu"]["watts"], 130)

    def test_config_version_is_stamped_on(self):
        self.assertEqual(config.CONFIG_VERSION, 1)
        self.assertEqual(config.migrate_config({})["config_version"], 1)

    def test_an_old_version_stamp_is_brought_forward(self):
        out = config.migrate_config({"config_version": 0})
        self.assertEqual(out["config_version"], config.CONFIG_VERSION)


class SaveAndLoad(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "rogcontrol.json")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_round_trip(self):
        cfg = config.migrate_config({})
        config.save_config(cfg, self.path)
        self.assertEqual(config.load_config(self.path)["profiles"].keys(),
                         cfg["profiles"].keys())

    def test_save_is_atomic(self):
        cfg = config.migrate_config({})
        config.save_config(cfg, self.path)
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_corrupt_config_is_preserved_not_replaced(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        config.load_config(self.path)
        backups = [n for n in os.listdir(self.dir) if ".corrupt-" in n]
        self.assertEqual(len(backups), 1)

    # --- the same rules, pinned harder ------------------------------------

    def test_round_trip_keeps_every_value(self):
        cfg = config.migrate_config({"charge_limit": 61,
                                     "something_from_a_newer_build": 7})
        config.save_config(cfg, self.path)
        self.assertEqual(config.load_config(self.path), cfg)

    def test_a_failed_save_leaves_the_previous_config_intact(self):
        # The whole point of writing to a temp file: if serialising blows up
        # (or the process dies) part way through, the file already on disk is
        # untouched. Writing in place truncates it the moment it is opened.
        good = config.migrate_config({})
        config.save_config(good, self.path)
        doomed = json.loads(json.dumps(good))
        doomed["profiles"]["Quiet"]["cpu"]["stapm"] = {1, 2}  # a set: not JSON
        with self.assertRaises(TypeError):
            config.save_config(doomed, self.path)
        with open(self.path) as f:
            self.assertEqual(json.load(f), good)

    def test_a_failed_save_does_not_leave_a_temp_file_behind(self):
        good = config.migrate_config({})
        config.save_config(good, self.path)
        doomed = json.loads(json.dumps(good))
        doomed["profiles"]["Quiet"]["cpu"]["stapm"] = {1, 2}
        with self.assertRaises(TypeError):
            config.save_config(doomed, self.path)
        self.assertEqual(os.listdir(self.dir), ["rogcontrol.json"])

    def test_saving_over_a_bigger_config_leaves_no_tail_behind(self):
        big = config.migrate_config({})
        config.save_config(big, self.path)
        config.save_config({"tiny": True}, self.path)
        with open(self.path) as f:
            self.assertEqual(json.load(f), {"tiny": True})

    def test_save_creates_the_directory(self):
        path = os.path.join(self.dir, "nested", "deeper", "rogcontrol.json")
        config.save_config({"a": 1}, path)
        with open(path) as f:
            self.assertEqual(json.load(f), {"a": 1})

    def test_a_missing_config_yields_usable_defaults_without_writing(self):
        cfg = config.load_config(self.path)
        self.assertIn(cfg["current_profile"], cfg["profiles"])
        self.assertEqual(os.listdir(self.dir), [],
                         "loading must not create files")

    def test_the_corrupt_backup_holds_the_original_bytes(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        config.load_config(self.path)
        backup, = [n for n in os.listdir(self.dir) if ".corrupt-" in n]
        with open(os.path.join(self.dir, backup)) as f:
            self.assertEqual(f.read(), "{not json")

    def test_a_corrupt_config_still_returns_a_usable_one(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        cfg = config.load_config(self.path)
        self.assertEqual(list(cfg["profiles"]), list(profiles.DEFAULT_PROFILES))
        self.assertIn(cfg["current_profile"], cfg["profiles"])

    def test_the_corrupt_config_is_moved_out_of_the_way(self):
        # It must not be left where the next save would overwrite it.
        with open(self.path, "w") as f:
            f.write("{not json")
        config.load_config(self.path)
        self.assertFalse(os.path.exists(self.path))

    def test_json_that_is_not_an_object_counts_as_corrupt(self):
        with open(self.path, "w") as f:
            f.write("[1, 2, 3]")
        cfg = config.load_config(self.path)
        self.assertEqual(len([n for n in os.listdir(self.dir)
                              if ".corrupt-" in n]), 1)
        self.assertIn("profiles", cfg)

    def test_two_corruptions_in_the_same_second_keep_both_backups(self):
        # The timestamp alone is not unique enough; a second failure inside
        # the same second must not overwrite the first rescued copy.
        with mock.patch("rogcontrol.config.time.time", return_value=1700000000):
            for text in ("{first", "{second"):
                with open(self.path, "w") as f:
                    f.write(text)
                config.load_config(self.path)
        backups = sorted(n for n in os.listdir(self.dir) if ".corrupt-" in n)
        self.assertEqual(len(backups), 2, backups)
        kept = []
        for name in backups:
            with open(os.path.join(self.dir, name)) as f:
                kept.append(f.read())
        self.assertEqual(sorted(kept), ["{first", "{second"])

    def test_a_valid_config_is_migrated_on_load(self):
        with open(self.path, "w") as f:
            json.dump({"profiles": {"Quiet": {"cpu": {"stapm": 1}}},
                       "current_profile": "Quiet"}, f)
        cfg = config.load_config(self.path)
        self.assertEqual(cfg["profiles"]["Quiet"]["cpu"]["stapm"], 1)
        self.assertIn("fans", cfg["profiles"]["Quiet"])
        self.assertEqual(cfg["charge_limit"],
                         config.DEFAULT_CONFIG["charge_limit"])


class TheDefaultPath(unittest.TestCase):
    """load/save with no path argument must use CONFIG_PATH -- resolved when
    they are called, so this can point them somewhere harmless."""

    def setUp(self):
        # Also checked per-test, not just once in setUpModule: these are the
        # tests that actually call load/save with no path, and this class
        # keeps its guard if it is ever pulled into another test module.
        _require_default_path_resolved_at_call_time()
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "rogcontrol.json")
        self.addCleanup(shutil.rmtree, self.dir, True)
        patcher = mock.patch.object(config, "CONFIG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_save_and_load_use_it(self):
        cfg = config.migrate_config({"charge_limit": 61})
        config.save_config(cfg)
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(config.load_config()["charge_limit"], 61)


class AutoSwitchPickers(unittest.TestCase):
    """The Battery page's "on AC use" / "on battery use" choices.

    The whole point of these three functions is that the no-op entry is a
    label and the stored value is null. Storing the label would name a
    profile that does not exist, and every reader would then find a target
    it cannot switch to."""

    def setUp(self):
        self.cfg = {"profiles": {"Quiet": {}, "Performance": {}},
                    "ac_profile": "Performance", "battery_profile": None}

    def test_the_no_op_comes_first(self):
        choices = config.auto_switch_choices(self.cfg)
        self.assertEqual(choices,
                         [config.NO_AUTO_SWITCH, "Quiet", "Performance"])

    def test_a_config_with_no_profiles_still_offers_the_no_op(self):
        self.assertEqual(config.auto_switch_choices({}),
                         [config.NO_AUTO_SWITCH])
        # A corrupt profiles value must not take the picker down with it.
        self.assertEqual(config.auto_switch_choices({"profiles": []}),
                         [config.NO_AUTO_SWITCH])

    def test_selection_points_at_the_stored_profile(self):
        self.assertEqual(
            config.auto_switch_selected(self.cfg, "ac_profile"), 2)

    def test_null_selects_the_no_op(self):
        self.assertEqual(
            config.auto_switch_selected(self.cfg, "battery_profile"), 0)

    def test_a_deleted_profile_falls_back_to_the_no_op(self):
        # Renamed or deleted since it was chosen. Selecting nothing would
        # leave the picker blank, which reads as a broken control.
        self.cfg["ac_profile"] = "Gone"
        self.assertEqual(
            config.auto_switch_selected(self.cfg, "ac_profile"), 0)

    def test_the_no_op_is_stored_as_null(self):
        self.assertIsNone(config.auto_switch_value(config.NO_AUTO_SWITCH))
        self.assertEqual(config.auto_switch_value("Quiet"), "Quiet")

    def test_the_keys_are_the_ones_every_other_tool_reads(self):
        self.assertEqual(config.AUTO_SWITCH_KEYS,
                         {"ac": "ac_profile", "battery": "battery_profile"})


class FollowingTheFile(unittest.TestCase):
    """The decision an open window makes when the config file moves.

    Five processes write this file. The window used to load it once and then
    write its whole in-memory copy back on every page save, so anything the
    enforcer, the tray or the hotkey cycler did was silently reverted by the
    next slider nudge."""

    def test_the_first_sample_is_not_a_change(self):
        # The window has just loaded that exact file. Calling it a change
        # would reload every page on startup for nothing.
        self.assertFalse(config.config_file_moved_on(None, 1000.0))

    def test_a_missing_file_is_not_a_change(self):
        # Nothing to re-read, and the copy in memory is the better of the two.
        self.assertFalse(config.config_file_moved_on(1000.0, None))

    def test_an_unchanged_mtime_is_not_a_change(self):
        self.assertFalse(config.config_file_moved_on(1000.0, 1000.0))

    def test_a_written_file_is_a_change(self):
        self.assertTrue(config.config_file_moved_on(1000.0, 1000.5))

    def test_a_file_restored_to_an_older_mtime_is_still_a_change(self):
        # A restored backup goes backwards in time; "different" is the
        # question, not "newer".
        self.assertTrue(config.config_file_moved_on(1000.0, 900.0))

    def test_the_enforcer_switching_profile_is_seen_as_a_profile_change(self):
        current = {"current_profile": "Performance", "profiles": {
            "Performance": {"cpu": {}}, "Quiet": {"cpu": {}}}}
        fresh = json.loads(json.dumps(current))
        fresh["current_profile"] = "Quiet"
        self.assertEqual(config.reload_decision(current, fresh), (True, False))

    def test_the_windows_own_save_is_no_change_at_all(self):
        # The window's saves move the mtime too, so this is the common case
        # and it must not reload the pages under the user's hands.
        current = {"current_profile": "Quiet", "profiles": {"Quiet": {"cpu": {}}}}
        self.assertEqual(
            config.reload_decision(current, json.loads(json.dumps(current))),
            (False, False))

    def test_the_same_profile_rewritten_elsewhere_is_a_contents_change(self):
        # The name staying put says nothing about the curves inside it.
        current = {"current_profile": "Quiet",
                   "profiles": {"Quiet": {"fans": {"1": [[40, 25]]}}}}
        fresh = {"current_profile": "Quiet",
                 "profiles": {"Quiet": {"fans": {"1": [[40, 60]]}}}}
        self.assertEqual(config.reload_decision(current, fresh), (False, True))

    def test_a_change_to_another_profile_leaves_the_open_one_alone(self):
        current = {"current_profile": "Quiet",
                   "profiles": {"Quiet": {"cpu": {"stapm": 25000}},
                                "Performance": {"cpu": {"stapm": 75000}}}}
        fresh = json.loads(json.dumps(current))
        fresh["profiles"]["Performance"]["cpu"]["stapm"] = 80000
        self.assertEqual(config.reload_decision(current, fresh), (False, False))

    def test_a_config_with_no_profiles_at_all_decides_without_raising(self):
        self.assertEqual(config.reload_decision({}, {}), (False, False))
        self.assertEqual(
            config.reload_decision({"profiles": None},
                                   {"current_profile": "Quiet",
                                    "profiles": None}),
            (True, False))


class WhereTheRealConfigLives(unittest.TestCase):
    def test_config_path_points_at_the_users_config(self):
        self.assertEqual(config.CONFIG_PATH,
                         os.path.expanduser("~/.config/rogcontrol.json"))


if __name__ == "__main__":
    unittest.main()
