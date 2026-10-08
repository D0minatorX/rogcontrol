"""System-tab controls for installing and updating the Decky plugin."""

import os
import tempfile
import time
import webbrowser

from gi.repository import GLib

from . import decky_install, decky_release


RELEASES_URL = "https://github.com/D0minatorX/rogcontrol/releases"


class DeckyUpdateController:
    def __init__(self, page):
        self.page = page
        self.window = page.window
        self.busy = False
        self.action = None
        self._timer_id = None
        self._expected_version = None
        self._status_path = None
        self._closed = False

    def build(self, group):
        from gi.repository import Adw, Gtk

        self.row = Adw.ActionRow(title="ROG Control Decky plugin")
        self.row.set_subtitle_lines(0)
        self.button = Gtk.Button(label="Check")
        self.button.set_valign(Gtk.Align.CENTER)
        self.button.connect("clicked", self._on_button_clicked)
        self.row.add_suffix(self.button)
        self.row.set_activatable_widget(self.button)
        group.add(self.row)
        self._set_status("Checking Decky plugin…", "Check", "check")

    def close(self):
        self._closed = True
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _set_status(self, text, label=None, action=None):
        self.row.set_subtitle(text)
        if label is not None:
            self.button.set_label(label)
        if action is not None:
            self.action = action
        self.button.set_sensitive(not self.busy)

    def check(self):
        if self.busy or self._closed or not hasattr(self, "row"):
            return
        self.busy = True
        self.button.set_sensitive(False)
        self._set_status("Checking Decky plugin…")

        def work():
            installed = decky_install.detect_installation()
            root = decky_install.plugin_root().parent
            loader_present = (root.parent / "services" / "plugin_loader.service").is_file() or root.is_dir()
            release = decky_release.check_for_update(
                installed.get("version") if installed.get("installed") else None)
            return installed, loader_present, release

        self.window.apply_isolated(work, self._on_checked, timeout=15)

    def _on_checked(self, result, error):
        if self._closed:
            return
        self.busy = False
        self.button.set_sensitive(True)
        if error is not None:
            self._set_status(f"Could not check plugin status: {error}", "Check", "check")
            return
        installed, loader_present, release = result
        if release.get("error"):
            self._set_status(f"Could not check GitHub Releases: {release['error']}",
                             "Check", "check")
            return
        if not loader_present:
            self._set_status("Decky Loader was not found for this user. Install Decky first.",
                             "Install", "releases")
        elif not installed.get("installed"):
            if release.get("available"):
                self._set_status(f"Not installed. Version {release['version']} is ready.",
                                 "Install", "install")
            else:
                self._set_status("Not installed. Plugin package is not attached to the latest release yet.",
                                 "Install", "releases")
        elif release.get("available"):
            self._set_status(f"Installed v{installed['version']}; v{release['version']} is available.",
                             "Update", "update")
        elif release.get("version") == installed.get("version"):
            self._set_status(f"Installed and up to date (v{installed['version']}).",
                             "Check", "check")
        else:
            self._set_status(f"Installed v{installed['version']}. No compatible plugin package found in the latest release.",
                             "Check", "check")

    def _on_button_clicked(self, _button):
        if self.busy:
            return
        if self.action == "check":
            self.check()
        elif self.action == "releases":
            webbrowser.open(RELEASES_URL)
            self._set_status("Opened ROG Control releases. Install the Decky ZIP after Decky Loader is available.",
                             "Install", "releases")
        elif self.action in ("install", "update"):
            self._start_install()

    def _start_install(self):
        action = self.action
        self.busy = True
        self.button.set_sensitive(False)
        self._set_status("Checking the release package…")

        def work():
            installed = decky_install.detect_installation()
            release = decky_release.check_for_update(
                installed.get("version") if installed.get("installed") else None)
            if release.get("error"):
                raise RuntimeError(release["error"])
            if not release.get("available"):
                return {"no_release": True}
            archive = decky_release.download_and_stage(
                release["download_url"], release["sha256_url"], release["version"])
            try:
                installed_result = decky_install.install_archive(
                    archive, expected_version=release["version"])
            finally:
                decky_release.cleanup_staged(archive)
            status_fd, status_path = tempfile.mkstemp(
                prefix="rogcontrol-decky-install-", suffix=".status")
            os.close(status_fd)
            launch = decky_install.launch_loader_restart(status_path)
            return {"installed": installed_result, "launch": launch,
                    "status_path": status_path, "version": release["version"],
                    "action": action}

        self.window.apply_isolated(work, self._on_install_staged, timeout=135)

    def _on_install_staged(self, result, error):
        if self._closed:
            return
        if error is not None:
            self.busy = False
            self._set_status(f"Plugin install failed: {error}", "Check", "check")
            return
        if result.get("no_release"):
            self.busy = False
            self._set_status("No installable plugin package is attached to the latest release.",
                             "Install", "releases")
            return
        launch = result["launch"]
        if not launch["ok"]:
            self.busy = False
            self._set_status(f"Plugin files are installed, but Decky could not be restarted: {launch['message']}",
                             "Check", "check")
            return
        self._expected_version = result["version"]
        self._status_path = result["status_path"]
        self._set_status(f"Installed v{self._expected_version}; waiting for Decky Loader to restart…")
        self._timer_id = GLib.timeout_add_seconds(2, self._poll_restart,
                                                  time.monotonic())

    def _poll_restart(self, started):
        try:
            with open(self._status_path, encoding="utf-8") as stream:
                status = stream.read(32).strip()
        except OSError:
            status = ""
        if status == "0":
            installed = decky_install.detect_installation()
            self.busy = False
            if installed.get("installed") and installed.get("version") == self._expected_version:
                self._set_status(f"Decky plugin v{self._expected_version} installed. Open Decky to use it.",
                                 "Check", "check")
            else:
                self._set_status("Decky restarted, but the installed plugin version could not be confirmed.",
                                 "Check", "check")
            self._timer_id = None
            return GLib.SOURCE_REMOVE
        if status.startswith("running:"):
            try:
                pid = int(status.partition(":")[2])
                if pid <= 0:
                    raise ValueError("invalid Decky restart process id")
                os.kill(pid, 0)
            except ProcessLookupError:
                self.busy = False
                self._set_status("Decky restart terminal closed before completion.",
                                 "Check", "check")
                self._timer_id = None
                return GLib.SOURCE_REMOVE
            except (ValueError, PermissionError):
                pass
        if status.isdecimal() and status != "0":
            self.busy = False
            self._set_status("Decky Loader restart failed. See the terminal for details.",
                             "Check", "check")
            self._timer_id = None
            return GLib.SOURCE_REMOVE
        if not status and time.monotonic() - started > 30:
            self.busy = False
            self._set_status("Decky Loader restart did not start. Retry the check.",
                             "Check", "check")
            self._timer_id = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
