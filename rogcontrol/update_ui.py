"""Update workflow and progress, separate from System page construction."""

import os
import threading
import time
import gi
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib
from . import APP_VERSION, updater


class UpdateController:
    def __init__(self, page):
        self.page = page
        self.window = page.window
        self._update_busy = False
        self._update_install_timer = None

    def close(self):
        if self._update_install_timer is not None:
            GLib.source_remove(self._update_install_timer)
            self._update_install_timer = None

    def _set_update_status(self, text):
        self.page.update_row.set_subtitle(text)

    def _on_check_update_clicked(self, _button):
        self.check_for_update()

    def check_for_update(self):
        """Ask GitHub for a newer release. Shared by the button and the
        app's own launch/daily auto-check (see app.py), so there is exactly
        one place that decides what "available" means and how the dialog is
        offered."""
        if self._update_busy:
            return
        self._update_busy = True
        self.page.update_check_button.set_sensitive(False)
        self._set_update_status("Checking…")
        self.window.apply_isolated(updater.check_for_update,
                                   self._on_update_checked, timeout=15)

    def _on_update_checked(self, result, error):
        self._update_busy = False
        self.page.update_check_button.set_sensitive(True)
        if error is not None:
            self._set_update_status(f"Could not check for updates: {error}")
            return
        if result.get("error"):
            self._set_update_status(
                f"Could not check for updates: {result['error']}")
            return
        if not result.get("available"):
            self._set_update_status(f"Up to date (v{APP_VERSION})")
            return
        version = result.get("version")
        download_url = result.get("download_url")
        if not download_url:
            # Newer, but this release has nothing this app knows how to
            # fetch automatically -- see UPDATE_ASSET_PREFIX. Said plainly
            # rather than offering a button that can only fail.
            self._set_update_status(
                f"v{version} available, but no matching download was found "
                f"on the release page.")
            return
        self._set_update_status(f"v{version} available")
        self._offer_update(version, download_url)

    def _offer_update(self, version, download_url):
        dialog = Adw.AlertDialog(
            heading=f"Update to v{version}?",
            body="This downloads the release and opens a terminal running "
                "its installer -- the same install.sh you would run by "
                "hand, so it still asks for your sudo password there. "
                "ROG Control closes for installation and reopens afterward. "
                "Your settings, profiles and fan calibration are kept.")
        dialog.add_response("later", "Later")
        dialog.add_response("update", "Update")
        dialog.set_response_appearance("update",
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("update")
        dialog.set_close_response("later")
        dialog.connect("response", self._on_update_dialog_response,
                       version, download_url)
        dialog.present(self.page)

    def _on_update_dialog_response(self, _dialog, response, version,
                                   download_url):
        if response != "update" or self._update_busy:
            return
        self._update_busy = True
        self.page.update_check_button.set_sensitive(False)
        self._update_cancelled = cancelled = threading.Event()
        self._set_update_status(f"Downloading v{version}…")
        # apply_isolated, not apply_async: this is the one hardware call
        # that reaches an outside server rather than the laptop itself, so
        # it is the one that can hang on a stalled DNS lookup or a
        # connection that never completes. Running it on the shared
        # worker pool would let that hang freeze every other page's live
        # stats for the rest of the session.
        self.window.apply_isolated(
            lambda: updater.download_and_stage_update(
                download_url, cancelled=cancelled,
                progress=lambda received, total: GLib.idle_add(
                    self._on_download_progress, version, cancelled, received, total)),
            lambda result, error: self._on_update_staged(
                version, result, error),
            timeout=135)

    def _on_download_progress(self, version, cancelled, received, total):
        if not cancelled.is_set():
            amount = f"{received / (1024 * 1024):.1f} MiB"
            if total:
                amount = f"{received * 100 // total}% ({amount})"
            self._set_update_status(f"Downloading v{version}: {amount}")
        return GLib.SOURCE_REMOVE

    def _finish_update(self, text):
        self._update_busy = False
        self.page.update_check_button.set_sensitive(True)
        self._set_update_status(text)

    def _on_update_staged(self, version, install_sh_path, error):
        self._update_cancelled.set()
        if error is not None:
            self._finish_update(f"Update failed: {error}")
            self.window.toast(f"Could not download v{version}: {error}")
            return
        self._set_update_status(f"Opening installer for v{version}…")
        status_path = os.path.join(os.path.dirname(install_sh_path),
                                   ".rogcontrol-update-status")
        self.window.apply_isolated(
            lambda: updater.launch_update_terminal(
                install_sh_path, status_path, app_pid=os.getpid()),
            lambda result, error: self._on_installer_launched(
                version, status_path, result, error), timeout=15)

    def _on_installer_launched(self, version, status_path, result, error):
        if error is not None:
            self._finish_update(f"Could not open installer: {error}")
            return
        ok, message = result
        if ok:
            self._set_update_status(
                f"Installing v{version} in a terminal window…")
            self.window.toast("Opened a terminal to install the update -- "
                              "follow the prompts there.")
            self._update_install_timer = GLib.timeout_add_seconds(
                2, self._poll_update_install, version, status_path,
                time.monotonic())
        else:
            self._finish_update(f"Could not open a terminal: {message}")
            self.window.toast(message)

    def _poll_update_install(self, version, status_path, started):
        try:
            with open(status_path) as stream:
                status = stream.read(32).strip()
        except OSError:
            status = ""
        if status.startswith("running:"):
            try:
                pid = int(status.partition(":")[2])
                if pid <= 0:
                    raise ValueError("Invalid installer process")
                os.kill(pid, 0)
            except ProcessLookupError:
                self._finish_update("Installer closed before completing. Please retry.")
                self._update_install_timer = None
                return GLib.SOURCE_REMOVE
            except (ValueError, PermissionError):
                pass
        if status == "0":
            self._finish_update(f"v{version} installed. ROG Control is reopening.")
        elif status.isdecimal():
            self._finish_update(
                f"Installation failed (exit {status}). See the installer terminal.")
        elif not status and time.monotonic() - started > 20:
            self._finish_update("Installer did not start. Please retry.")
        else:
            return GLib.SOURCE_CONTINUE
        self._update_install_timer = None
        return GLib.SOURCE_REMOVE
