"""Which profile a finished apply is allowed to be written into.

THE BUG THIS PINS, which reached a user and destroyed real settings:

Every Apply in this app hands the work to a thread and answers later. The
fan page answers about sixteen seconds later, because the embedded
controller drops curve writes fired closer together than eight seconds and
there are three channels. The page then asked the window "which profile is
current?" to decide where to save -- at that point, not at the point the
button was pressed.

Sixteen seconds is long enough for the answer to have changed. The user can
pick another profile, so can the tray and the hotkey cycler, and the
enforcer switches on its own when the charger comes out or the OS power
mode moves. When that happened, the curves the user had drawn for one
profile were written over a different profile's, silently. Four profiles
deliberately set to different curves collapsed into nearly the same one.

The fix is to capture the profile NAME when Apply is pressed and write to
that profile by name; and when the active profile has moved by the time the
work finishes, to write NOTHING -- not to the profile the user left, and
above all not to the one they never touched -- and say so.

These are unit tests over the decision and the write, not the GTK pages: the
suite must not import gi. The pages are parsed rather than imported, at the
bottom, to prove they route through this logic instead of keeping their own
copy of it -- a page that quietly went back to resolving the profile late is
exactly how this bug would return.
"""

import ast
import json
import os
import tempfile
import unittest
from pathlib import Path

from rogcontrol import config

PACKAGE_DIR = Path(config.__file__).resolve().parent
PAGES = {name: PACKAGE_DIR / "pages" / f"{name}.py"
         for name in ("fans", "cpu", "gpu")}

QUIET_CURVE = [[50, 8], [90, 10]]
BALANCED_CURVE = [[50, 10], [90, 12]]
PERFORMANCE_CURVE = [[50, 16], [90, 18]]


def make_config(current="Quiet"):
    """Three profiles set to deliberately different curves -- the shape of
    the machine the damage was found on."""
    return {
        "current_profile": current,
        "profiles": {
            "Quiet": {"fans": {"1": [list(p) for p in QUIET_CURVE]},
                      "cpu": {"stapm": 25000}, "gpu": {"watts": 65}},
            "Balanced Power": {
                "fans": {"1": [list(p) for p in BALANCED_CURVE]},
                "cpu": {"stapm": 55000}, "gpu": {"watts": 100}},
            "Performance": {
                "fans": {"1": [list(p) for p in PERFORMANCE_CURVE]},
                "cpu": {"stapm": 75000}, "gpu": {"watts": 140}},
        },
    }


class DecidingWhereToSave(unittest.TestCase):
    """config.deferred_save_target: the whole decision, in isolation."""

    def test_the_captured_profile_is_still_current_so_it_may_be_written(self):
        cfg = make_config(current="Quiet")
        status, profile = config.deferred_save_target(cfg, "Quiet")
        self.assertEqual(status, config.SAVE_OK)
        self.assertIs(profile, cfg["profiles"]["Quiet"])

    def test_a_profile_switch_during_the_apply_refuses_the_write(self):
        """THE BUG. The apply started on Quiet; Performance is current now.
        Neither is written."""
        cfg = make_config(current="Performance")
        status, profile = config.deferred_save_target(cfg, "Quiet")
        self.assertEqual(status, config.SAVE_PROFILE_CHANGED)
        self.assertIsNone(profile)

    def test_the_answer_does_not_depend_on_which_profile_is_current(self):
        """Whatever the user switched to, the answer is the same refusal --
        there is no profile the result may fall back to."""
        for current in ("Balanced Power", "Performance"):
            with self.subTest(current=current):
                cfg = make_config(current=current)
                status, _ = config.deferred_save_target(cfg, "Quiet")
                self.assertEqual(status, config.SAVE_PROFILE_CHANGED)

    def test_a_profile_deleted_or_renamed_mid_apply_refuses_the_write(self):
        """current_profile can still name it while the profile itself is
        gone -- an import or a rename lands as one config write."""
        cfg = make_config(current="Quiet")
        del cfg["profiles"]["Quiet"]
        status, profile = config.deferred_save_target(cfg, "Quiet")
        self.assertEqual(status, config.SAVE_PROFILE_GONE)
        self.assertIsNone(profile)

    def test_a_profile_replaced_by_something_that_is_not_a_dict(self):
        """A hand-edited config is not allowed to turn a refusal into an
        AttributeError halfway through the save."""
        cfg = make_config(current="Quiet")
        cfg["profiles"]["Quiet"] = "not a profile"
        status, _ = config.deferred_save_target(cfg, "Quiet")
        self.assertEqual(status, config.SAVE_PROFILE_GONE)

    def test_no_profile_was_active_when_apply_was_pressed(self):
        for captured in (None, ""):
            with self.subTest(captured=captured):
                status, profile = config.deferred_save_target(
                    make_config(), captured)
                self.assertEqual(status, config.SAVE_NO_PROFILE)
                self.assertIsNone(profile)

    def test_the_profile_comes_back_by_name_not_as_a_held_reference(self):
        """Why the name is captured and not the dict.

        A window that notices another process wrote the config does
        ``config.clear()`` then ``update()``, which replaces every profile
        object in it. A reference taken before that is still writable and
        still saves -- into an orphan nothing ever reads back."""
        cfg = make_config(current="Quiet")
        stale = cfg["profiles"]["Quiet"]
        cfg.clear()
        cfg.update(make_config(current="Quiet"))
        _status, profile = config.deferred_save_target(cfg, "Quiet")
        self.assertIsNot(profile, stale)
        self.assertIs(profile, cfg["profiles"]["Quiet"])

    def test_the_returned_profile_is_live_so_a_write_to_it_sticks(self):
        cfg = make_config(current="Quiet")
        _status, profile = config.deferred_save_target(cfg, "Quiet")
        profile["fans"]["1"] = [[50, 99]]
        self.assertEqual(cfg["profiles"]["Quiet"]["fans"]["1"], [[50, 99]])


class TheRefusalIsExplained(unittest.TestCase):
    """The user is told plainly, because the settings really are on the
    hardware and really are not saved."""

    def test_a_switch_names_the_profile_that_was_not_written(self):
        text = config.deferred_save_refusal(
            config.SAVE_PROFILE_CHANGED, "Quiet", "curves",
            where="the fan controller")
        self.assertIn("Profile changed while applying", text)
        self.assertIn("written to the fan controller", text)
        self.assertIn("not saved to Quiet", text)

    def test_every_refusal_says_the_hardware_was_written(self):
        """The half that stops the user pressing Apply again in a panic."""
        for status in (config.SAVE_PROFILE_CHANGED, config.SAVE_PROFILE_GONE,
                       config.SAVE_NO_PROFILE):
            with self.subTest(status=status):
                text = config.deferred_save_refusal(
                    status, "Quiet", "CPU settings")
                self.assertIn("written to the hardware", text)
                self.assertIn("not saved", text)

    def test_no_refusal_claims_the_settings_were_saved(self):
        for status in (config.SAVE_PROFILE_CHANGED, config.SAVE_PROFILE_GONE,
                       config.SAVE_NO_PROFILE):
            text = config.deferred_save_refusal(status, "Quiet", "curves")
            self.assertNotIn("and saved", text)


class WritingTheResult(unittest.TestCase):
    """config.save_deferred against a real config file: what lands on disk,
    and -- the part that matters -- what does not."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "rogcontrol.json")
        self.cfg = make_config(current="Quiet")
        self.write(self.cfg)

    def write(self, cfg):
        with open(self.path, "w") as f:
            json.dump(cfg, f, indent=2)

    def on_disk(self):
        with open(self.path) as f:
            return json.load(f)

    def raw(self):
        with open(self.path, "rb") as f:
            return f.read()

    def apply_curves(self, captured, curve):
        """What the fan page does when an apply finishes: the curves that
        reached the controller, and the profile the button was pressed on."""
        return config.save_deferred(self.cfg, captured, "fans",
                                    {"1": curve}, "curves",
                                    where="the fan controller",
                                    path=self.path)

    # -- the profile did not move -------------------------------------------

    def test_the_curves_land_in_the_profile_apply_was_pressed_on(self):
        drawn = [[50, 30], [90, 60]]
        self.assertIsNone(self.apply_curves("Quiet", drawn))
        self.assertEqual(self.on_disk()["profiles"]["Quiet"]["fans"]["1"],
                         drawn)

    def test_the_other_profiles_are_untouched_by_a_normal_apply(self):
        """The damage was three profiles collapsing onto one curve. Saving
        Quiet may not move Balanced or Performance a single point."""
        self.apply_curves("Quiet", [[50, 30], [90, 60]])
        saved = self.on_disk()["profiles"]
        self.assertEqual(saved["Balanced Power"]["fans"]["1"], BALANCED_CURVE)
        self.assertEqual(saved["Performance"]["fans"]["1"], PERFORMANCE_CURVE)

    def test_a_channel_that_was_not_applied_keeps_its_saved_curve(self):
        """Only what reached the controller is recorded, and the rest of the
        section survives the update."""
        self.cfg["profiles"]["Quiet"]["fans"]["2"] = [[50, 7]]
        config.save_deferred(self.cfg, "Quiet", "fans", {"1": [[50, 30]]},
                             "curves", path=self.path)
        fans = self.on_disk()["profiles"]["Quiet"]["fans"]
        self.assertEqual(fans["1"], [[50, 30]])
        self.assertEqual(fans["2"], [[50, 7]])

    # -- the profile moved while the apply was running -----------------------

    def test_a_switch_mid_apply_writes_nothing_at_all(self):
        """THE BUG, end to end. Apply pressed on Quiet, the enforcer
        switched to Performance while the three channels were being written.
        The config file must not change by one byte."""
        before = self.raw()
        self.cfg["current_profile"] = "Performance"
        refused = self.apply_curves("Quiet", [[50, 30], [90, 60]])
        self.assertIsNotNone(refused)
        self.assertEqual(self.raw(), before)

    def test_a_switch_mid_apply_does_not_touch_the_new_profile(self):
        """The specific data loss: Quiet's curves written into Performance."""
        self.cfg["current_profile"] = "Performance"
        self.apply_curves("Quiet", [[50, 30], [90, 60]])
        self.assertEqual(
            self.on_disk()["profiles"]["Performance"]["fans"]["1"],
            PERFORMANCE_CURVE)
        self.assertEqual(
            self.cfg["profiles"]["Performance"]["fans"]["1"],
            PERFORMANCE_CURVE)

    def test_a_switch_mid_apply_does_not_touch_the_old_profile_either(self):
        """Not guessed at from the other end. The user left that profile;
        writing there behind their back is the same class of surprise."""
        self.cfg["current_profile"] = "Performance"
        self.apply_curves("Quiet", [[50, 30], [90, 60]])
        self.assertEqual(self.on_disk()["profiles"]["Quiet"]["fans"]["1"],
                         QUIET_CURVE)
        self.assertEqual(self.cfg["profiles"]["Quiet"]["fans"]["1"],
                         QUIET_CURVE)

    def test_the_in_memory_config_is_not_edited_before_the_refusal(self):
        """A page keeps using this dict, and the next save writes all of it,
        so an edit made and then 'not saved' would reach disk anyway."""
        self.cfg["current_profile"] = "Performance"
        before = json.dumps(self.cfg, sort_keys=True)
        self.apply_curves("Quiet", [[50, 30], [90, 60]])
        self.assertEqual(json.dumps(self.cfg, sort_keys=True), before)

    def test_a_deleted_profile_mid_apply_writes_nothing(self):
        before = self.raw()
        del self.cfg["profiles"]["Quiet"]
        self.assertIsNotNone(self.apply_curves("Quiet", [[50, 30]]))
        self.assertEqual(self.raw(), before)

    # -- the CPU and GPU pages use the same write ----------------------------

    def test_cpu_settings_go_to_the_captured_profile(self):
        self.assertIsNone(config.save_deferred(
            self.cfg, "Quiet", "cpu", {"stapm": 30000}, "CPU settings",
            path=self.path))
        saved = self.on_disk()["profiles"]
        self.assertEqual(saved["Quiet"]["cpu"]["stapm"], 30000)
        self.assertEqual(saved["Performance"]["cpu"]["stapm"], 75000)

    def test_cpu_settings_are_not_written_after_a_switch(self):
        before = self.raw()
        self.cfg["current_profile"] = "Balanced Power"
        self.assertIsNotNone(config.save_deferred(
            self.cfg, "Quiet", "cpu", {"stapm": 30000}, "CPU settings",
            path=self.path))
        self.assertEqual(self.raw(), before)

    def test_gpu_settings_go_to_the_captured_profile(self):
        self.assertIsNone(config.save_deferred(
            self.cfg, "Quiet", "gpu", {"watts": 70}, "GPU settings",
            path=self.path))
        saved = self.on_disk()["profiles"]
        self.assertEqual(saved["Quiet"]["gpu"]["watts"], 70)
        self.assertEqual(saved["Performance"]["gpu"]["watts"], 140)

    def test_gpu_settings_are_not_written_after_a_switch(self):
        before = self.raw()
        self.cfg["current_profile"] = "Balanced Power"
        self.assertIsNotNone(config.save_deferred(
            self.cfg, "Quiet", "gpu", {"watts": 70}, "GPU settings",
            path=self.path))
        self.assertEqual(self.raw(), before)

    # -- nothing to write ----------------------------------------------------

    def test_an_apply_where_every_step_failed_saves_nothing(self):
        """Empty values is not an error and is not a write: it is what an
        apply that the hardware refused outright looks like."""
        before = self.raw()
        self.assertIsNone(config.save_deferred(
            self.cfg, "Quiet", "cpu", {}, "CPU settings", path=self.path))
        self.assertEqual(self.raw(), before)


# -- the pages actually route through it --------------------------------------
#
# Parsed, never imported: they pull in gi/GTK 4, which this suite must not
# touch. What is checked is the shape of the fix, because the bug was not a
# wrong value anywhere -- it was one call made in the wrong place.


def page_tree(name):
    path = PAGES[name]
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def function(name, tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def called_names(node):
    """Every ``...foo(...)`` and ``foo(...)`` name called inside ``node``."""
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute):
                out.append(func.attr)
            elif isinstance(func, ast.Name):
                out.append(func.id)
    return out


class ThePagesResolveTheProfileAtPressTime(unittest.TestCase):

    def test_apply_captures_the_profile_name_when_the_button_is_pressed(self):
        for name in PAGES:
            with self.subTest(page=name):
                node = function("_on_apply_clicked", page_tree(name))
                self.assertIsNotNone(node, f"{name} has no _on_apply_clicked")
                self.assertIn("current_profile_name", called_names(node))

    def test_no_page_resolves_the_profile_when_the_apply_finishes(self):
        """The bug, as a shape. Anything in the completion path that asks
        the window which profile is current is asking sixteen seconds too
        late."""
        for name in PAGES:
            for method in ("_save", "_on_applied"):
                with self.subTest(page=name, method=method):
                    node = function(method, page_tree(name))
                    self.assertIsNotNone(node, f"{name} has no {method}")
                    called = called_names(node)
                    self.assertNotIn("current_profile", called)
                    self.assertNotIn("current_profile_name", called)

    def test_every_page_saves_through_the_shared_decision(self):
        """One implementation of the rule, so it cannot be fixed on one page
        and left broken on the next."""
        for name in PAGES:
            with self.subTest(page=name):
                node = function("_save", page_tree(name))
                self.assertIn("save_deferred", called_names(node))

    def test_no_page_still_calls_save_config_from_its_save(self):
        """save_deferred owns the write, including the decision not to make
        it. A page calling save_config itself would be writing the whole
        config regardless of what that decided."""
        for name in PAGES:
            with self.subTest(page=name):
                node = function("_save", page_tree(name))
                self.assertNotIn("save_config", called_names(node))


class CalibrationIsGlobalAndNeedsNoProfile(unittest.TestCase):
    """The other long-running job on the fan page -- two and a half minutes,
    so the profile can move under it just as easily.

    It is safe for a different reason rather than by the same fix: what it
    saves is fan_rpm_cal, which is one measurement of this machine's fans and
    lives at the top of the config. No profile has one, so there is no wrong
    profile to write it into. Pinned here so that "confirmed" does not decay
    into "assumed" if calibration ever grows a per-profile setting."""

    def test_the_calibration_result_is_stored_outside_the_profiles(self):
        cfg = config.migrate_config({})
        self.assertNotIn("fan_rpm_cal",
                         cfg["profiles"]["Quiet"])

    def test_no_stock_profile_carries_a_calibration(self):
        from rogcontrol import profiles as profiles_mod
        for name, profile in profiles_mod.DEFAULT_PROFILES.items():
            with self.subTest(profile=name):
                self.assertNotIn("fan_rpm_cal", profile)

    def test_calibration_reads_its_figures_from_the_top_of_the_config(self):
        from rogcontrol import fancurve
        cfg = {"fan_rpm_cal": {"1": [1000, 40.0]},
               "current_profile": "Quiet",
               "profiles": {"Quiet": {}, "Performance": {}}}
        first = fancurve.get_rpm_cal(cfg, "1")
        cfg["current_profile"] = "Performance"
        self.assertEqual(fancurve.get_rpm_cal(cfg, "1"), first)

    def test_the_calibration_save_never_resolves_a_profile(self):
        node = function("_on_calibrated", page_tree("fans"))
        self.assertIsNotNone(node)
        called = called_names(node)
        self.assertNotIn("current_profile", called)
        self.assertNotIn("current_profile_name", called)


if __name__ == "__main__":
    unittest.main()
