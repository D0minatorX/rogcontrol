"""Updater regressions using local archives and a fake terminal."""

import io
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from rogcontrol import updater as hardware


class Response(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}


def archive_bytes(installer="exit 0\n"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("release/rogcontrol/install.sh", installer)
    return stream.getvalue()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="update-test-")
        self.addCleanup(self.directory.cleanup)
        self.stage = str(Path(self.directory.name) / "stage")
        os.mkdir(self.stage)
        self.stage_patch = patch.object(hardware.tempfile, "mkdtemp", return_value=self.stage)
        self.stage_patch.start()
        self.addCleanup(self.stage_patch.stop)

    def test_download_reports_progress_and_extracts(self):
        progress = []
        with patch.object(hardware.urllib.request, "urlopen",
                          return_value=Response(archive_bytes())):
            installer = hardware.download_and_stage_update(
                "https://example.test/update.zip",
                progress=lambda *args: progress.append(args))
        self.assertEqual(Path(installer).read_text(), "exit 0\n")
        self.assertTrue(progress)
        self.assertEqual(progress[-1][0], progress[-1][1])

    def test_truncated_download_cleans_stage(self):
        with patch.object(hardware.urllib.request, "urlopen",
                          return_value=Response(archive_bytes(), length=99999)):
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                hardware.download_and_stage_update("https://example.test/update.zip")
        self.assertFalse(Path(self.stage).exists())

    def test_cancelled_download_cleans_stage(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(hardware.urllib.request, "urlopen",
                          return_value=Response(archive_bytes())):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                hardware.download_and_stage_update(
                    "https://example.test/update.zip", cancelled=cancelled)
        self.assertFalse(Path(self.stage).exists())

    def test_invalid_archive_cleans_stage(self):
        with patch.object(hardware.urllib.request, "urlopen",
                          return_value=Response(b"not a zip")):
            with self.assertRaises(zipfile.BadZipFile):
                hardware.download_and_stage_update("https://example.test/update.zip")
        self.assertFalse(Path(self.stage).exists())

    def test_download_has_total_deadline(self):
        with patch.object(hardware.urllib.request, "urlopen",
                          return_value=Response(archive_bytes())), \
                patch.object(hardware.time, "monotonic", side_effect=[0, 121]):
            with self.assertRaises(TimeoutError):
                hardware.download_and_stage_update("https://example.test/update.zip")
        self.assertFalse(Path(self.stage).exists())

    def test_failed_terminal_tries_next_candidate(self):
        from unittest.mock import Mock
        first = Mock()
        first.wait.return_value = 1
        second = Mock()
        second.wait.side_effect = subprocess.TimeoutExpired("terminal", 0.3)
        with patch.object(hardware.subprocess, "Popen", side_effect=[first, second]) as launch, \
                patch.object(hardware.shutil, "which", return_value="/bin/fake"):
            self.assertEqual(hardware.launch_update_terminal("/tmp/install.sh"), (True, None))
        self.assertEqual(launch.call_count, 2)

    def test_terminal_is_detached_from_app_lifecycle(self):
        from unittest.mock import Mock
        process = Mock()
        process.wait.side_effect = subprocess.TimeoutExpired("terminal", 0.3)
        with patch.object(hardware.subprocess, "Popen", return_value=process) as launch, \
                patch.object(hardware.shutil, "which", return_value="/bin/fake"), \
                patch.object(hardware, "UPDATE_TERMINALS", (("fake", ["fake", "--"]),)):
            self.assertEqual(
                hardware.launch_update_terminal("/tmp/install.sh"),
                (True, None))
        self.assertTrue(launch.call_args.kwargs["start_new_session"])

    def test_nonexecutable_installer_runs_and_reports_exit_code(self):
        installer = Path(self.stage) / "install.sh"
        status = Path(self.stage) / "result"
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code):
                installer.write_text(f"exit {exit_code}\n")
                installer.chmod(0o644)
                commands = []

                def fake_terminal(argv):
                    commands.append(argv)
                    # Run the shell wrapper, without opening a real terminal
                    # or invoking any actual application installer.
                    return subprocess.CompletedProcess(argv, subprocess.run(
                        argv[2:], input="\n", text=True,
                        capture_output=True, timeout=5).returncode)

                class Process:
                    def __init__(self, argv, **_kwargs):
                        self.result = fake_terminal(argv)

                    def wait(self, timeout):
                        return self.result.returncode

                # subprocess.run itself uses Popen, so patch only the
                # hardware module's subprocess reference.
                from types import SimpleNamespace
                runner = SimpleNamespace(Popen=Process, TimeoutExpired=subprocess.TimeoutExpired)
                with patch.object(hardware, "subprocess", runner), \
                        patch.object(hardware.shutil, "which", return_value="/bin/fake"), \
                        patch.object(hardware, "UPDATE_TERMINALS", (("fake", ["fake", "--"]),)):
                    ok, error = hardware.launch_update_terminal(str(installer), str(status))
                self.assertTrue(ok, error)
                self.assertEqual(status.read_text().strip(), str(exit_code))
                self.assertEqual(len(commands), 1)

    def test_update_quits_old_app_before_install_and_reopens_after_exit(self):
        installer = Path(self.stage) / "install.sh"
        status = Path(self.stage) / "result"
        trace = Path(self.stage) / "trace"
        app = Path(self.directory.name) / ".local/bin/rogcontrol"
        app.parent.mkdir(parents=True)
        app.write_text('#!/bin/sh\necho "$1" >> "$UPDATE_TRACE"\n')
        app.chmod(0o755)

        class Process:
            def __init__(self, argv, **_kwargs):
                self.result = subprocess.run(argv[2:], input="\n", text=True,
                                             capture_output=True, timeout=5)

            def wait(self, timeout):
                return self.result.returncode

        from types import SimpleNamespace
        runner = SimpleNamespace(Popen=Process, TimeoutExpired=subprocess.TimeoutExpired)
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code):
                trace.unlink(missing_ok=True)
                installer.write_text(
                    f'echo install >> "$UPDATE_TRACE"\nexit {exit_code}\n')
                with patch.dict(os.environ, {"HOME": self.directory.name,
                                             "UPDATE_TRACE": str(trace)}), \
                        patch.object(hardware, "subprocess", runner), \
                        patch.object(hardware.shutil, "which", return_value="/bin/fake"), \
                        patch.object(hardware, "UPDATE_TERMINALS", (("fake", ["fake", "--"]),)):
                    ok, error = hardware.launch_update_terminal(
                        str(installer), str(status), app_pid=99999999)
                self.assertTrue(ok, error)
                for _ in range(50):
                    if trace.exists() and len(trace.read_text().splitlines()) == 3:
                        break
                    time.sleep(0.01)
                self.assertEqual(trace.read_text().splitlines(),
                                 ["--quit", "install", "--show"])
                self.assertEqual(status.read_text().strip(), str(exit_code))


if __name__ == "__main__":
    unittest.main()
