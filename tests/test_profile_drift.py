"""Guard against the duplicated profile tables drifting apart.

Until the GTK3 rogcontrol.py is deleted, DEFAULT_PROFILES and
PROFILE_TO_PPD_MODE live in more than one file. test_profiles.py pins
profiles.py to literals, which catches a corrupted copy there -- but nothing
in it stops someone retuning the still-live copy in rogcontrol.py and leaving
profiles.py silently stale. That is the exact failure this refactor exists to
kill, so this module reads the tables back out of the other files and demands
they match.

The other files are parsed, never imported. They pull in gi/GTK, which the
suite must not touch, and rogcontrol-enforcer.py is not an importable module
name anyway. ast.literal_eval on the parsed assignment is enough, because
every one of these tables is a plain literal.

Each check skips when the table it guards no longer has a second home -- the
file is gone, or the file now imports from profiles.py instead of redefining
it. A later task deletes rogcontrol.py; once the last duplicate is gone there
is nothing left to drift, so this guard becomes unnecessary rather than
broken, and this whole module can be deleted with it.
"""

import ast
import unittest
from pathlib import Path

from rogcontrol import profiles

PACKAGE_DIR = Path(profiles.__file__).resolve().parent
GTK3_APP = PACKAGE_DIR / "rogcontrol.py"
ENFORCER = PACKAGE_DIR / "rogcontrol-enforcer.py"


def read_table(path, name):
    """The value of a module-level ``name = {...}`` literal, without importing.

    Returns None if the file defines no such assignment, which is how a file
    that has been switched over to importing from profiles.py reads.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    return None


def key_order(value):
    """Nested key order alone, with the values themselves flattened away.

    Dicts compare equal whatever order they were written in, but order is
    load-bearing in both tables: PROFILE_TO_PPD_MODE's decides which profile
    the shared "balanced" mode resolves back to, and DEFAULT_PROFILES' is the
    order the profiles appear in the menu and the tray.
    """
    if isinstance(value, dict):
        return [(k, key_order(v)) for k, v in value.items()]
    if isinstance(value, list):
        return [key_order(v) for v in value]
    return None


class DuplicatedTablesAgree(unittest.TestCase):
    # DEFAULT_PROFILES is big enough that unittest would truncate the diff and
    # leave whoever hit this hunting for the changed number by eye.
    maxDiff = None

    def assert_still_matches(self, path, name):
        expected = getattr(profiles, name)
        if not path.exists():
            self.skipTest(f"{path.name} is gone, so {name} has only one home "
                          "left and cannot drift")
        found = read_table(path, name)
        if found is None:
            self.skipTest(f"{path.name} no longer defines its own {name}, so "
                          "there is no second copy to drift")
        drifted = (f"{name} in rogcontrol/{path.name} has drifted from "
                   f"rogcontrol/profiles.py -- reconcile the two by hand, "
                   f"these are field-tuned hardware values")
        self.assertEqual(found, expected, drifted)
        self.assertEqual(key_order(found), key_order(expected),
                         f"{drifted} (same entries, different key order)")

    def test_gtk3_app_default_profiles_have_not_drifted(self):
        self.assert_still_matches(GTK3_APP, "DEFAULT_PROFILES")

    def test_gtk3_app_ppd_mode_map_has_not_drifted(self):
        self.assert_still_matches(GTK3_APP, "PROFILE_TO_PPD_MODE")

    def test_enforcer_ppd_mode_map_has_not_drifted(self):
        self.assert_still_matches(ENFORCER, "PROFILE_TO_PPD_MODE")


class TheReaderItself(unittest.TestCase):
    """Everything above skips itself when a file goes away, so a reader that
    silently returned None for everything would leave the suite green and the
    guard doing nothing. Point it at profiles.py, whose values are known."""

    def test_reads_a_table_it_can_be_checked_against(self):
        self.assertEqual(read_table(PACKAGE_DIR / "profiles.py",
                                    "PROFILE_TO_PPD_MODE"),
                         profiles.PROFILE_TO_PPD_MODE)

    def test_returns_none_for_a_name_that_is_not_there(self):
        self.assertIsNone(read_table(PACKAGE_DIR / "profiles.py",
                                     "NO_SUCH_TABLE"))

    def test_key_order_notices_a_reordering(self):
        a = {"Balanced Performance": "balanced", "Balanced Power": "balanced"}
        b = {"Balanced Power": "balanced", "Balanced Performance": "balanced"}
        self.assertEqual(a, b, "dicts really do compare equal reordered")
        self.assertNotEqual(key_order(a), key_order(b))


if __name__ == "__main__":
    unittest.main()
