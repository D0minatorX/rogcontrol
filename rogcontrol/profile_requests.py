"""Fast profile selection with one background writer and a replaceable target."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from . import config, hardware


def directory():
    base = os.environ.get('XDG_RUNTIME_DIR')
    if not base:
        base = str(Path.home() / '.local/state')
    path = Path(base) / 'rogcontrol-profile-requests'
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


@contextmanager
def selection_lock():
    with (directory() / 'selection.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@contextmanager
def hardware_apply_lock():
    """Serialize a direct apply with the profile drain worker."""
    with (directory() / 'worker.lock').open('a') as worker:
        fcntl.flock(worker, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(worker, fcntl.LOCK_UN)
    # A profile request can start its drain subprocess while this direct
    # apply owns worker.lock. drain() deliberately exits rather than waiting
    # when another writer is active, so wake the pending request again here.
    if (directory() / 'pending.json').exists():
        _launch()


def allowed(cfg, origin):
    if origin in ('shortcut', 'decky'):
        return True
    key = {'bindings': 'key_bindings', 'fnlock': 'fn_lock'}.get(origin)
    settings = cfg.get(key) if key else None
    return isinstance(settings, dict) and settings.get('enabled') is True


def active_session():
    from .hotkeys import session_active
    return session_active()


def _launch():
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent))
    subprocess.Popen([sys.executable, '-m', 'rogcontrol', 'profile', 'drain'],
                     env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def request_next(origin='shortcut'):
    """Advance the pending selection; publish it only when an apply starts."""
    with selection_lock():
        cfg = config.load_config()
        if not allowed(cfg, origin):
            return
        names = list(cfg.get('profiles', {}))
        if not names:
            return
        current = cfg.get('current_profile')
        path = directory() / 'pending.json'
        previous = json.loads(path.read_text()) if path.exists() else {}
        cursor = (previous.get('name')
                  if previous.get('base') == current and allowed(cfg, previous.get('origin'))
                  else current)
        index = names.index(cursor) if cursor in names else -1
        name = names[(index + 1) % len(names)]
        ticket = {'id': uuid.uuid4().hex, 'name': name, 'origin': origin, 'base': current}
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(ticket))
        temporary.replace(path)
        # Start under the selection lock so a failure cannot leave a target
        # which an already-running worker unexpectedly picks up afterward.
        try:
            _launch()
        except OSError:
            path.unlink(missing_ok=True)
            raise
    hardware.notify('ROG Control', f'Profile requested: {name}')


def request_profile(name, origin='decky'):
    """Queue a named profile using the same single-writer worker as cycling.

    The name is checked against the fresh config while holding the selection
    lock, and the ticket records the current profile as its base. If another
    profile change wins before the worker applies this one, the stale ticket
    is discarded by ``drain`` rather than selecting from an old profile.
    """
    if origin not in ('decky', 'shortcut'):
        raise ValueError('unsupported profile request origin')
    with selection_lock():
        cfg = config.load_config()
        names = list(cfg.get('profiles', {}))
        if name not in names:
            raise ValueError(f'unknown profile: {name}')
        current = cfg.get('current_profile')
        ticket = {'id': uuid.uuid4().hex, 'name': name,
                  'origin': origin, 'base': current}
        path = directory() / 'pending.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(ticket))
        temporary.replace(path)
        try:
            _launch()
        except OSError:
            path.unlink(missing_ok=True)
            raise
    hardware.notify('ROG Control', f'Profile requested: {name}')
    return {'accepted': True, 'profile': name}


def drain(apply):
    """Only one process applies hardware; newer requests replace pending ones."""
    path = directory() / 'pending.json'
    with (directory() / 'worker.lock').open('a') as worker:
        try:
            fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            with selection_lock():
                if not path.exists():
                    # Release inside selection_lock: a publisher cannot race
                    # worker exit and lose its only wakeup.
                    fcntl.flock(worker, fcntl.LOCK_UN)
                    return
                ticket = json.loads(path.read_text())
                path.unlink()
                cfg = config.load_config()
                valid = (allowed(cfg, ticket['origin'])
                         and cfg.get('current_profile') == ticket['base']
                         and ticket['name'] in cfg.get('profiles', {}))
                if valid and ticket['origin'] != 'shortcut':
                    valid = active_session()
                if valid:
                    selected = []
                    def select(latest):
                        if (latest.get('current_profile') == ticket['base']
                                and allowed(latest, ticket['origin'])
                                and ticket['name'] in latest.get('profiles', {})):
                            latest['current_profile'] = ticket['name']
                            selected.append(True)
                    cfg = config.update_config(select)
                    valid = bool(selected)
            if not valid:
                continue
            try:
                apply(cfg, ticket['name'])
            except Exception as error:
                hardware.log(f'Profile {ticket["name"]}: {error}', 'ERROR',
                             source='cycle-profile', dedupe_key='apply')
                hardware.notify('ROG Control', f'Profile {ticket["name"]} apply failed: {error}')
