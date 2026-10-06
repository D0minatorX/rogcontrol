"""Persistent one-charge override, independent of the normal charge setting.

The state file survives app/service restarts. A separate advisory lock serializes
GUI and enforcer transitions, including hardware writes, without holding the
configuration lock across a helper invocation.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile

from . import config as config_mod
from . import hardware

STATE_PATH = os.path.expanduser('~/.local/state/rogcontrol/charge-once.json')


def read_state(state_path=None):
    try:
        with open(state_path or STATE_PATH) as source:
            value = json.load(source)
        if isinstance(value, dict):
            return value
    except (OSError, ValueError):
        pass
    return None


@contextmanager
def _locked(state_path):
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    with open(state_path + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _save(state, state_path):
    if state is None:
        Path(state_path).unlink(missing_ok=True)
        return
    fd, temporary = tempfile.mkstemp(dir=str(Path(state_path).parent))
    try:
        with os.fdopen(fd, 'w') as target:
            json.dump(state, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, state_path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def effective_limit(config, state_path=None):
    state = read_state(state_path)
    return (100 if state and not state.get('restoring')
            else config.get('charge_limit', 100))


def start(config_path=None, state_path=None):
    state_path = state_path or STATE_PATH
    with _locked(state_path):
        _save({'seen_ac': bool(hardware.is_ac_connected()), 'restoring': False},
              state_path)
        return _tick(config_path, state_path)


def cancel(config_path=None, state_path=None):
    state_path = state_path or STATE_PATH
    with _locked(state_path):
        state = read_state(state_path)
        if state:
            state['restoring'] = True
            _save(state, state_path)
        return _tick(config_path, state_path)


def set_limit(percent, config_path=None, state_path=None):
    """Save the normal limit without interrupting an active full charge."""
    state_path = state_path or STATE_PATH
    with _locked(state_path):
        if not read_state(state_path):
            # A failed normal-limit write must also get a later retry.
            _save({'restoring': True, 'seen_ac': False}, state_path)
        config_mod.update_config(lambda cfg: cfg.update(charge_limit=int(percent)),
                                 path=config_path)
        return _tick(config_path, state_path)


def tick(config=None, config_path=None, state_path=None, enforce_normal=False):
    """Reconcile firmware with fresh disk settings; retry failures next tick.

    The optional config argument supports enforcer callers, but is deliberately
    not used: a profile apply can carry a snapshot predating a user's edit.
    """
    state_path = state_path or STATE_PATH
    with _locked(state_path):
        return _tick(config_path, state_path, enforce_normal=enforce_normal)


def _battery_full():
    """Read Full from the same first usable battery as read_battery().

    Some packs stop at a learned full capacity below a rounded 100%. Do not
    mistake a charger's status or a second battery for the sampled pack.
    """
    try:
        for entry in sorted(os.listdir(hardware.POWER_SUPPLY_DIR)):
            path = os.path.join(hardware.POWER_SUPPLY_DIR, entry)
            if hardware.read_file(os.path.join(path, 'type')) != 'Battery':
                continue
            if hardware.read_int(os.path.join(path, 'capacity')) is None:
                continue
            return hardware.read_file(os.path.join(path, 'status')) == 'Full'
    except OSError:
        pass
    return False


def _tick(config_path, state_path, enforce_normal=True):
    state = read_state(state_path)
    if not state and not enforce_normal:
        return True, ""
    current = hardware.read_charge_limit()
    if state and not state.get('restoring'):
        ac = hardware.is_ac_connected()
        percent, _charging = hardware.read_battery()
        changed = dict(state)
        if ac:
            changed['seen_ac'] = True
        if ((percent is not None and (percent >= 100
                                      or (current == 100 and _battery_full())))
                or (ac is False and state.get('seen_ac'))):
            changed['restoring'] = True
        if changed != state:
            _save(changed, state_path)
            state = changed
    cfg = config_mod.load_config(path=config_path)
    target = 100 if state and not state.get('restoring') else cfg.get('charge_limit', 100)
    if current != target:
        ok, message = hardware.run_helper('charge', target)
        if not ok:
            return False, message
        if hardware.read_charge_limit() != target:
            return False, f'Firmware has not confirmed the {target}% charge limit'
    if state and state.get('restoring'):
        _save(None, state_path)
    return True, ''
