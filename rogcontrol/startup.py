"""Control login startup for the tray and background services."""
import os
from pathlib import Path
import subprocess

from . import config

SERVICES = (
    "rogcontrol-apply.service",
    "rogcontrol-enforcer.service",
    "rogcontrol-tray.service",
)


def is_enabled():
    """Preserve the existing enabled behavior until explicitly turned off."""
    return config.load_config().get("start_on_boot", True) is not False


def _systemctl(*arguments, check=True):
    try:
        result = subprocess.run(
            ["systemctl", "--user", *arguments], capture_output=True,
            text=True, timeout=30)
    except subprocess.TimeoutExpired as error:
        raise OSError("Timed out updating ROG Control startup services") from error
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "systemctl failed"
        raise OSError(f"Could not update ROG Control startup services: {detail}")
    return result


def _enabled_services():
    return {name: _systemctl("is-enabled", name, check=False).stdout.strip()
            in ("enabled", "enabled-runtime") for name in SERVICES}


def _restore_services(states):
    """Best effort rollback must not hide the original operation's error."""
    for enabled in (True, False):
        names = [name for name, state in states.items() if state == enabled]
        if names:
            try:
                _systemctl("enable" if enabled else "disable", *names)
            except OSError:
                pass


def _remove_legacy_autostart():
    default = Path.home() / ".config"
    configured = Path(os.environ.get("XDG_CONFIG_HOME") or default)
    if not configured.is_absolute():
        configured = default
    for directory in {default, configured}:
        for name in ("rogcontrol-window.desktop", "rogcontrol-autostart.desktop"):
            (directory / "autostart" / name).unlink(missing_ok=True)


def configure_services(enabled, *, start_now=False):
    """Apply login enablement without saving preferences.

    The installer also starts/stops the services immediately. The UI only
    changes the next login, leaving the current session running.
    """
    previous = _enabled_services()
    try:
        if enabled:
            _systemctl("enable", *SERVICES)
            if start_now:
                # The oneshot apply retries hardware writes for over a minute.
                # Queue the jobs; the installer reports running/starting state.
                _systemctl("restart", "--no-block", *SERVICES)
        else:
            options = ("--now",) if start_now else ()
            _systemctl("disable", *options, *SERVICES)
        _remove_legacy_autostart()
    except OSError:
        _restore_services(previous)
        raise


def set_enabled(enabled):
    """Persist the preference only after startup integration succeeds."""
    previous = _enabled_services()
    configure_services(bool(enabled))
    try:
        # Reload after systemctl so unrelated edits during the operation survive.
        settings = config.load_config()
        settings["start_on_boot"] = bool(enabled)
        config.save_config(settings)
    except (OSError, ValueError):
        _restore_services(previous)
        raise
