"""asusd: reading its state, and what the app is allowed to do about it.

asusctl's daemon drives the same hardware this app does -- the same asus-wmi
knobs, the same three custom fan curves, the same keyboard lighting -- so the
System page has to be able to say whether it is there and what it is doing,
and to stop it. Reading that needs no privilege: systemd answers is-active
and is-enabled to any user. Only the two changes go through the helper.

The parsing is pure, which is the point: this machine has no asusctl on it,
so every state below is one that could not otherwise be exercised at all.

The last class reads the helper script itself. The one thing the app must
*not* do is run a package manager -- a removal is a transaction the user
should see, in their own terminal -- and a root-owned, passwordless "remove
this package" action is exactly what a helper like this must not carry.
"""

import unittest
from pathlib import Path

from rogcontrol import hardware, profiles

HELPER = Path(profiles.__file__).resolve().parent / "rogcontrol-helper"

# What systemctl prints for a unit that exists. The header and the trailing
# summary are real: list-unit-files is a table, not a word.
UNIT_FILES = ("UNIT FILE      STATE    PRESET\n"
              "asusd.service  enabled  enabled\n\n"
              "1 unit files listed.\n")
NO_UNIT_FILES = "UNIT FILE  STATE  PRESET\n\n0 unit files listed.\n"


class StateParsing(unittest.TestCase):

    def test_a_machine_without_asusctl_is_absent(self):
        state = hardware.parse_asusd_state(
            unit_files=NO_UNIT_FILES, is_active="inactive\n",
            is_enabled="", binary_found=False)
        self.assertEqual(state["state"], hardware.ASUSD_ABSENT)
        self.assertFalse(state["installed"])
        self.assertFalse(state["active"])

    def test_running(self):
        state = hardware.parse_asusd_state(
            unit_files=UNIT_FILES, is_active="active\n",
            is_enabled="enabled\n", binary_found=True)
        self.assertEqual(state["state"], hardware.ASUSD_RUNNING)
        self.assertTrue(state["active"])
        self.assertTrue(state["enabled"])

    def test_stopped_but_still_enabled(self):
        """The state that matters most: quiet now, back at the next boot."""
        state = hardware.parse_asusd_state(
            unit_files=UNIT_FILES, is_active="inactive\n",
            is_enabled="enabled\n", binary_found=True)
        self.assertEqual(state["state"], hardware.ASUSD_STOPPED_ENABLED)

    def test_stopped_and_disabled(self):
        state = hardware.parse_asusd_state(
            unit_files=UNIT_FILES, is_active="inactive\n",
            is_enabled="disabled\n", binary_found=True)
        self.assertEqual(state["state"], hardware.ASUSD_STOPPED_DISABLED)
        self.assertFalse(state["enabled"])

    def test_enabled_runtime_still_counts_as_enabled(self):
        state = hardware.parse_asusd_state(
            unit_files=UNIT_FILES, is_active="inactive",
            is_enabled="enabled-runtime", binary_found=True)
        self.assertEqual(state["state"], hardware.ASUSD_STOPPED_ENABLED)

    def test_static_is_not_enabled(self):
        """A static unit is not started by systemd on its own, so it is not
        something that comes back at boot."""
        state = hardware.parse_asusd_state(
            unit_files=UNIT_FILES, is_active="inactive",
            is_enabled="static", binary_found=True)
        self.assertEqual(state["state"], hardware.ASUSD_STOPPED_DISABLED)

    def test_a_failed_unit_is_not_running(self):
        state = hardware.parse_asusd_state(
            unit_files=UNIT_FILES, is_active="failed",
            is_enabled="enabled", binary_found=True)
        self.assertFalse(state["active"])
        self.assertEqual(state["state"], hardware.ASUSD_STOPPED_ENABLED)
        # Kept, so the page can say "failed" rather than "stopped", which
        # reads as deliberate.
        self.assertEqual(state["raw_active"], "failed")

    def test_a_binary_with_no_unit_still_counts_as_installed(self):
        """A hand-built asusctl in /usr/local/bin ships no unit file, and it
        can still take the hardware."""
        state = hardware.parse_asusd_state(
            unit_files=NO_UNIT_FILES, is_active="inactive",
            is_enabled="", binary_found=True)
        self.assertTrue(state["installed"])
        self.assertEqual(state["state"], hardware.ASUSD_STOPPED_DISABLED)

    def test_a_unit_with_no_binary_on_path_still_counts_as_installed(self):
        """asusd lives in /usr/bin on some distros and is not always on the
        user's PATH; the unit file is enough."""
        state = hardware.parse_asusd_state(
            unit_files=UNIT_FILES, is_active="active",
            is_enabled="enabled", binary_found=False)
        self.assertTrue(state["installed"])
        self.assertEqual(state["state"], hardware.ASUSD_RUNNING)

    def test_enabled_is_unknown_rather_than_false_without_a_unit(self):
        """"Will not start at boot" and "cannot be asked" are different
        answers, and only one of them is a fact."""
        state = hardware.parse_asusd_state(
            unit_files=NO_UNIT_FILES, is_active="", is_enabled="",
            binary_found=True)
        self.assertIsNone(state["enabled"])

    def test_no_answers_at_all_is_absent_not_an_exception(self):
        state = hardware.parse_asusd_state()
        self.assertEqual(state["state"], hardware.ASUSD_ABSENT)

    def test_another_unit_named_in_the_output_is_not_asusd(self):
        state = hardware.parse_asusd_state(
            unit_files="asusd-user.service  enabled  enabled\n",
            is_active="inactive", is_enabled="", binary_found=False)
        self.assertFalse(state["has_unit"])
        self.assertEqual(state["state"], hardware.ASUSD_ABSENT)

    def test_every_state_has_a_name_the_page_can_show(self):
        for name in (hardware.ASUSD_ABSENT, hardware.ASUSD_RUNNING,
                     hardware.ASUSD_STOPPED_ENABLED,
                     hardware.ASUSD_STOPPED_DISABLED):
            self.assertIsInstance(name, str)
        self.assertEqual(
            len({hardware.ASUSD_ABSENT, hardware.ASUSD_RUNNING,
                 hardware.ASUSD_STOPPED_ENABLED,
                 hardware.ASUSD_STOPPED_DISABLED}), 4)


class UninstallCommand(unittest.TestCase):
    """Text to show the user, never a command this app runs."""

    def test_arch(self):
        self.assertEqual(
            hardware.asusd_uninstall_command(have=lambda n: n == "pacman"),
            "sudo pacman -Rs asusctl")

    def test_fedora(self):
        self.assertEqual(
            hardware.asusd_uninstall_command(have=lambda n: n == "dnf"),
            "sudo dnf remove asusctl")

    def test_debian(self):
        self.assertEqual(
            hardware.asusd_uninstall_command(have=lambda n: n == "apt-get"),
            "sudo apt remove asusctl")

    def test_an_unrecognised_distro_gets_nothing_rather_than_a_guess(self):
        self.assertIsNone(hardware.asusd_uninstall_command(have=lambda n: False))

    def test_the_first_package_manager_found_wins(self):
        # A machine with several (a Fedora box with pacman built from source)
        # must still get one answer, deterministically.
        self.assertEqual(
            hardware.asusd_uninstall_command(have=lambda n: True),
            "sudo pacman -Rs asusctl")

    def test_every_command_names_the_package_and_nothing_else(self):
        for _tool, command in hardware.UNINSTALL_COMMANDS:
            self.assertIn(hardware.ASUSD_PACKAGE, command)
            self.assertTrue(command.startswith("sudo "), command)
            # No -y/--noconfirm anywhere: the point of handing this to the
            # user is that their package manager asks them first.
            for silent in ("--noconfirm", " -y", "--yes"):
                self.assertNotIn(silent, command)


class TheHelperStaysNarrow(unittest.TestCase):

    def setUp(self):
        self.text = HELPER.read_text(encoding="utf-8")

    def test_the_helper_has_the_two_service_actions(self):
        self.assertIn("asusd_disable)", self.text)
        self.assertIn("asusd_enable)", self.text)

    def test_they_only_ever_name_asusd_itself(self):
        """No caller-supplied unit name, in either direction."""
        self.assertIn("systemctl disable --now asusd.service", self.text)
        self.assertIn("systemctl enable --now asusd.service", self.text)

    def test_the_helper_runs_no_package_manager(self):
        """The app does not remove packages. A root-owned, passwordless
        "remove this package" action is exactly what this helper must not
        carry, even hardcoded to one package name."""
        for manager in ("pacman", "apt-get", "dnf", "zypper", "rpm ", "dpkg"):
            self.assertNotIn(manager, self.text, manager)

    def test_there_is_no_remove_action_left(self):
        self.assertNotIn("asusd_remove", self.text)

    def test_the_service_actions_refuse_arguments(self):
        """They take none, so anything extra is a caller that thinks it is
        passing a unit name."""
        for action in ("asusd_status", "asusd_disable", "asusd_enable"):
            self.assertIn(f"no_args {action}", self.text)


if __name__ == "__main__":
    unittest.main()
