"""Optional Gamescope session detection and restart-safe profile restoration.

The enforcer owns transitions and hardware application. This module only
probes systemd and updates the named profile; it never starts Gamescope.
"""

import json
import os
import shutil
import subprocess

from . import config

SESSION_UNITS = ("gamescope-session.target", "gamescope-session.service")
STATE_PATH = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "rogcontrol", "gamescope-profile.json")


def _unit_values(prop):
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", *SESSION_UNITS,
             "--property=" + prop, "--value"],
            capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode:
        return []
    return result.stdout.split()


def detect_installed():
    """A Gamescope executable or installed session unit enables the UI."""
    return bool(shutil.which("gamescope") or
                "loaded" in _unit_values("LoadState"))


def session_active():
    """True/False for a running/ended session; None for an uncertain probe."""
    values = _unit_values("ActiveState")
    if "active" in values:
        return True
    if len(values) != len(SESSION_UNITS) or "activating" in values:
        return None
    if all(value in ("inactive", "failed", "deactivating") for value in values):
        return False
    return None


def has_saved_profile(state_path=None):
    return os.path.exists(STATE_PATH if state_path is None else state_path)


def complete_transition(state_path=None):
    """Called only after the enforcer finishes applying a pending transition."""
    state_path = STATE_PATH if state_path is None else state_path
    with open(state_path) as stream:
        state = json.load(stream)
    if state.get("restoring"):
        os.unlink(state_path)
    else:
        state["applied"] = True
        config.save_config(state, path=state_path)


def reconcile(active, config_path=None, state_path=None):
    """Return the profile needing full application, or None.

    Caller serializes transitions and acknowledges completed hardware applies.
    Save the original before changing the config. Retain it until restoration
    succeeds, including across logout, enforcer restart, and power loss. A
    repeated active sample never overwrites it with the gaming profile.
    """
    if active is None:
        return None
    config_path = config.CONFIG_PATH if config_path is None else config_path
    state_path = STATE_PATH if state_path is None else state_path
    with open(config_path) as stream:
        cfg = json.load(stream)
    try:
        with open(state_path) as stream:
            state = json.load(stream)
    except FileNotFoundError:
        state = None
    if state is not None and not isinstance(state, dict):
        raise ValueError("Invalid Gamescope profile restore record")

    profiles = cfg.get("profiles") or {}
    target = cfg.get("gamescope_profile")
    if active and target in profiles:
        if state is None:
            state = {"previous": cfg.get("current_profile"),
                     "target": target, "applied": False}
        elif state.get("target") == target and state.get("applied"):
            return None  # Preserve manual choices made within this session.
        state.update(target=target, applied=False, restoring=False)
        config.save_config(state, path=state_path)
        changed = cfg.get("current_profile") != target
        if changed:
            cfg["current_profile"] = target
            config.save_config(cfg, path=config_path)
        # Even when the config already names the target, a pending journal
        # means an interrupted hardware apply must be retried in full.
        return target

    if state is None:
        return None
    previous = state.get("previous")
    if previous in profiles:
        state.update(restoring=True, applied=False)
        config.save_config(state, path=state_path)
        if cfg.get("current_profile") != previous:
            cfg["current_profile"] = previous
            config.save_config(cfg, path=config_path)
        return previous
    # A deleted original cannot be restored. Keep the current valid profile.
    os.unlink(state_path)
    return None
