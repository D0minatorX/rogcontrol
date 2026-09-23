import subprocess
import unittest
from unittest import mock

from rogcontrol import hardware


CARDWIRE_GET = """Current Mode: Smart
Available Mode: integrated, hybrid, smart
"""


class CardwireTests(unittest.TestCase):
    def test_parse_status(self):
        current, available = hardware.parse_cardwire_status(CARDWIRE_GET)
        self.assertEqual(current, "Smart")
        self.assertEqual(available, ["Integrated", "Hybrid", "Smart"])

    def test_parse_status_is_tolerant_of_empty_and_unknown_lines(self):
        self.assertEqual(hardware.parse_cardwire_status(""), (None, []))
        self.assertEqual(
            hardware.parse_cardwire_status(
                "cardwired says hello\nCurrent Mode: hybrid\n"),
            ("Hybrid", []),
        )

    @mock.patch("rogcontrol.hardware.subprocess.run")
    def test_read_status_uses_one_cardwire_get(self, run):
        run.return_value = subprocess.CompletedProcess(
            ["cardwire", "get"], 0, stdout=CARDWIRE_GET, stderr="")

        self.assertEqual(
            hardware.read_cardwire_status(),
            ("Smart", ["Integrated", "Hybrid", "Smart"]),
        )
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["cardwire", "get"])

    @mock.patch("rogcontrol.hardware.subprocess.run")
    def test_set_mode_uses_lowercase_cardwire_cli_value(self, run):
        run.return_value = subprocess.CompletedProcess(
            ["cardwire", "set", "integrated"], 0,
            stdout="Mode has been set to Integrated\n", stderr="")

        ok, message = hardware.set_gpu_mode("Integrated")

        self.assertTrue(ok)
        self.assertIn("Integrated", message)
        self.assertEqual(run.call_args.args[0],
                         ["cardwire", "set", "integrated"])

    @mock.patch.dict("rogcontrol.hardware.os.environ",
                     {"XDG_SESSION_TYPE": "x11"}, clear=True)
    @mock.patch("rogcontrol.hardware.subprocess.run")
    def test_set_mode_rejects_x11_before_calling_cardwire(self, run):
        ok, message = hardware.set_gpu_mode("Hybrid")

        self.assertFalse(ok)
        self.assertEqual(message, hardware.CARDWIRE_WAYLAND_MESSAGE)
        run.assert_not_called()

    def test_unknown_session_is_left_to_non_gui_dbus_callers(self):
        self.assertTrue(hardware.cardwire_session_supported({}))
        self.assertFalse(hardware.cardwire_session_supported(
            {"XDG_SESSION_TYPE": "X11"}))
        self.assertTrue(hardware.cardwire_session_supported(
            {"XDG_SESSION_TYPE": "wayland"}))

    def test_mode_choices_hide_unconfigured_manual_mode(self):
        self.assertEqual(
            hardware.gpu_mode_choices("Manual", ["Hybrid", "Manual"]),
            ["Integrated", "Hybrid", "Smart", "Manual"],
        )
        self.assertEqual(
            hardware.gpu_mode_choices("Hybrid", ["Hybrid", "Manual"]),
            ["Integrated", "Hybrid", "Smart"],
        )

    @mock.patch("rogcontrol.hardware.nvidia_driver_loaded", return_value=True)
    @mock.patch("rogcontrol.hardware.read_gpu_mode", return_value="Smart")
    def test_smart_mode_is_reported_as_cardwire_blocked(self, _mode, _driver):
        self.assertEqual(hardware.nvidia_access_error(),
                         hardware.CARDWIRE_BLOCKED_MESSAGE)
        self.assertFalse(hardware.dgpu_available())

    @mock.patch("rogcontrol.hardware.nvidia_driver_loaded", return_value=False)
    @mock.patch("rogcontrol.hardware.read_gpu_mode", return_value="Hybrid")
    def test_hybrid_still_requires_the_driver(self, _mode, _driver):
        self.assertEqual(hardware.nvidia_access_error(),
                         hardware.NO_DRIVER_MESSAGE)

    @mock.patch("rogcontrol.hardware.subprocess.run")
    @mock.patch("rogcontrol.hardware.dgpu_available")
    def test_known_access_avoids_a_second_cardwire_check(self, available, run):
        run.return_value = subprocess.CompletedProcess(
            ["nvidia-smi"], 0, stdout="55, 42\n", stderr="")

        self.assertEqual(
            hardware.read_nvidia_stats(check_access=False), (55.0, 42.0))

        available.assert_not_called()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], "nvidia-smi")


if __name__ == "__main__":
    unittest.main()
