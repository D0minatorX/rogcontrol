"""Opt-in ASUS hardware-button listener shared by X11 and Wayland sessions.

Only supported ASUS devices are opened. Ordinary key events are discarded,
never recorded. Software Fn Lock alone exclusively grabs the N-KEY keyboard.
"""
import os
import json
from pathlib import Path
import select
import subprocess
import sys
import threading
import time

from . import config, hardware

BUTTONS = {'rog': 'ROG / M5', 'aura': 'Fn+F4 (Aura)',
           'performance': 'Fn+F5 / M4 (Performance)'}
ACTIONS = {'none': 'No ROG Control action', 'toggle': 'Show / hide ROG Control',
           'profile': 'Cycle profile', 'aura': 'Cycle lighting effect',
           'brightness_up': 'Keyboard brightness up',
           'brightness_down': 'Keyboard brightness down',
           'speed_up': 'Lighting speed up', 'speed_down': 'Lighting speed down'}
DEFAULTS = {'rog': 'toggle', 'aura': 'aura', 'performance': 'profile'}
SCAN_BINDINGS = {56: 'rog', 139: 'rog', 179: 'aura', 174: 'performance'}
KEY_BINDINGS = {148: 'rog', 190: 'rog', 202: 'aura', 203: 'performance'}


def preferences(cfg):
    saved = cfg.get('key_bindings')
    saved = saved if isinstance(saved, dict) else {}
    values = {'enabled': saved.get('enabled') is True}
    for button, default in DEFAULTS.items():
        action = saved.get(button, default)
        values[button] = action if isinstance(action, str) and action in ACTIONS else 'none'
    return values


class Decoder:
    def __init__(self):
        self.scan = None
        self.dropped = False

    def feed(self, kind, code, value):
        if kind == 0:
            self.scan = None
            if code == 3:
                self.dropped = True
            elif code == 0:
                self.dropped = False
        elif not self.dropped:
            if kind == 4 and code == 4:
                self.scan = value
            elif kind == 1:
                scan, self.scan = self.scan, None
                if value == 1:
                    return SCAN_BINDINGS.get(scan) or KEY_BINDINGS.get(code)
        return None


class Deduplicator:
    def __init__(self):
        self.last = {}

    def accept(self, button, device, now):
        previous = self.last.get(button)
        if previous and previous[0] != device and now - previous[1] < .25:
            return False
        self.last[button] = device, now
        return True


def eligible(name, vendor, product):
    return (name == 'Asus WMI hotkeys' or
            (vendor == 0x0b05 and product == 0x19b6))


def discover():
    """Return supported event paths without reading keyboard events."""
    paths = []
    for event in Path('/sys/class/input').glob('event*'):
        try:
            device = event / 'device'
            name = (device / 'name').read_text().strip()
            vendor = int((device / 'id/vendor').read_text(), 16)
            product = int((device / 'id/product').read_text(), 16)
            if eligible(name, vendor, product):
                paths.append((str(Path('/dev/input') / event.name),
                              name, vendor == 0x0b05 and product == 0x19b6))
        except (OSError, ValueError):
            continue
    return sorted(paths)


def _output(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=2)
        return result.stdout.strip() if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def session_active():
    """Fail closed when logind cannot confirm an unlocked local GUI session."""
    session = _output(['loginctl', 'show-user', str(os.getuid()), '-p', 'Display', '--value'])
    if not session:
        return False
    props = dict(line.split('=', 1) for line in _output(
        ['loginctl', 'show-session', session, '-p', 'Active', '-p', 'LockedHint',
         '-p', 'Remote', '-p', 'Type', '-p', 'User', '-p', 'Seat']).splitlines() if '=' in line)
    return (props.get('Active') == 'yes' and props.get('LockedHint') == 'no'
            and props.get('Remote') == 'no' and props.get('User') == str(os.getuid())
            and props.get('Type') in ('x11', 'wayland') and bool(props.get('Seat')))


def command(action):
    args = {'toggle': ['--toggle'], 'profile': ['profile', 'next'],
            'aura': ['keyboard', 'next'],
            'brightness_up': ['keyboard', 'brightness', 'up'],
            'brightness_down': ['keyboard', 'brightness', 'down'],
            'speed_up': ['keyboard', 'speed', 'up'],
            'speed_down': ['keyboard', 'speed', 'down']}.get(action)
    return [sys.executable, '-m', 'rogcontrol', *args] if args else None


def support_status():
    try:
        import evdev  # noqa: F401
    except ImportError:
        return False, False, 'Install python-evdev using the updated installer.'
    devices = discover()
    readable = [d for d in devices if os.access(d[0], os.R_OK)]
    fn = any(d[2] for d in readable) and os.access('/dev/uinput', os.W_OK)
    if not devices:
        return False, False, 'No supported ASUS hotkey device detected.'
    if not readable:
        return False, False, 'Input access unavailable. Run the updated installer, then log in again.'
    message = 'ASUS input access available. Changes take effect within two seconds.'
    if not fn:
        message += ' Fn Lock needs an accessible ASUS 19b6 keyboard and /dev/uinput.'
    return True, fn, message


def _status_path():
    directory = os.environ.get('XDG_RUNTIME_DIR')
    return Path(directory) / 'rogcontrol-hotkeys.json' if directory else None


def set_status(message):
    path = _status_path()
    if path is not None:
        try:
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps({'time': time.time(), 'message': message}))
            temp.replace(path)
        except OSError:
            pass


def runtime_status():
    path = _status_path()
    try:
        data = json.loads(path.read_text()) if path is not None else {}
        if time.time() - data.get('time', 0) < 8:
            return str(data['message'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return 'Listener status unavailable. Run the updated installer to update the background service.'


class ToggleWriter:
    """Persist mode changes off the input thread; newest request wins."""
    def __init__(self):
        self.lock = threading.Lock()
        self.pending = None
        self.generation = 0
        self.wake = threading.Event()

    def submit(self, media):
        with self.lock:
            self.generation += 1
            self.pending = self.generation, media, False
        self.wake.set()

    def overlay(self, settings):
        with self.lock:
            if self.pending is not None:
                generation, media, completed = self.pending
                settings['media'] = media
                if completed:
                    self.pending = None
        return settings

    def run(self, stop_event):
        from . import fnlock
        while not stop_event.is_set():
            self.wake.wait(.2)
            self.wake.clear()
            with self.lock:
                pending = self.pending
            if pending is None or pending[2]:
                continue
            generation, media, _ = pending
            try:
                def update(cfg):
                    settings = fnlock.preferences(cfg)
                    settings['media'] = media
                    cfg['fn_lock'] = settings
                config.update_config(update)
            except Exception as error:
                hardware.log(f'Fn Lock mode could not be saved: {error}', 'WARN',
                             source='hotkeys', dedupe_key='fn-toggle-save')
            with self.lock:
                if self.pending is not None and self.pending[0] == generation:
                    self.pending = generation, media, True


class SessionMonitor:
    """Never block grabbed keyboard input on logind subprocesses."""
    def __init__(self):
        self.state = False, 0

    def allowed(self):
        active, checked = self.state
        return active and time.monotonic() - checked < 4

    def run(self, stop_event):
        while not stop_event.is_set():
            self.state = session_active(), time.monotonic()
            stop_event.wait(1)


class ActionRunner:
    """One action at a time; never build a backlog of expensive profile writes."""
    def __init__(self, session_check=session_active):
        self.session_check = session_check
        self.process = None
        self.gui_processes = []
        self.started = 0

    def poll(self):
        self.gui_processes = [p for p in self.gui_processes if p.poll() is None]
        if self.process is not None:
            result = self.process.poll()
            if result is not None:
                if result:
                    hardware.log('Keyboard action failed; check the action logs.', 'WARN',
                                 source='hotkeys', dedupe_key='action')
                self.process = None
            elif time.monotonic() - self.started > 120:
                self.process.terminate()

    def execute(self, action):
        self.poll()
        args = command(action)
        if (not args or (self.process is not None and action != 'toggle')
                or not self.session_check()):
            return
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent))
        process = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if action == 'toggle':
            # First invocation may own the window for its whole lifetime.
            self.gui_processes.append(process)
        else:
            self.process = process
            self.started = time.monotonic()


def run(stop_event):
    """Enforcer worker: reconnect after resume, permission changes or hotplug."""
    from . import fnlock
    opened = {}
    monitor = SessionMonitor()
    threading.Thread(target=monitor.run, args=(stop_event,), daemon=True).start()
    toggle_writer = ToggleWriter()
    threading.Thread(target=toggle_writer.run, args=(stop_event,), daemon=True).start()
    runner = ActionRunner(monitor.allowed)
    dedupe = Deduplicator()
    next_check = 0
    bindings = preferences({})
    fn = fnlock.preferences({})

    def dispatch(action, path):
        if action.startswith('binding:'):
            button = action.split(':', 1)[1]
            if not bindings['enabled'] or not dedupe.accept(button, path, time.monotonic()):
                return
            action = bindings.get(button, 'none')
        runner.execute(action)

    def close(path):
        device, decoder, remapper = opened.pop(path)
        try:
            if remapper:
                remapper.close()
        finally:
            device.close()

    try:
        while not stop_event.is_set():
            try:
                now = time.monotonic()
                runner.poll()
                if now >= next_check:
                    next_check = now + 1
                    cfg = config.load_config()
                    bindings = preferences(cfg)
                    fn = toggle_writer.overlay(fnlock.preferences(cfg))
                    fn['bindings_enabled'] = bindings['enabled']
                    active = (bindings['enabled'] or fn['enabled']) and monitor.allowed()
                    desired = {path: keyboard for path, _, keyboard in discover()} if active else {}
                    for path in list(opened):
                        device, decoder, remapper = opened[path]
                        if path not in desired or bool(remapper) != (fn['enabled'] and desired[path]):
                            close(path)
                        elif remapper:
                            remapper.update(fn)
                    for path, keyboard in desired.items():
                        if path in opened:
                            continue
                        import evdev
                        device = evdev.InputDevice(path)
                        try:
                            remapper = (fnlock.Remapper(device, fn,
                                        lambda action, p=path: dispatch(action, p), toggle_writer.submit)
                                        if keyboard and fn['enabled'] else None)
                        except Exception:
                            device.close()
                            raise
                        opened[path] = device, Decoder(), remapper
                    set_status(('Listening; Fn Lock active.' if any(v[2] for v in opened.values())
                                else 'Listening for ASUS buttons.') if opened else
                               'Paused: session locked or inactive.' if bindings['enabled'] or fn['enabled']
                               else 'Disabled.')
                if not opened:
                    stop_event.wait(.2)
                    continue
                feedback = {v[2].virtual.fd: path for path, v in opened.items() if v[2]}
                readers = [v[0] for v in opened.values()]
                readers.extend(v[2].virtual for v in opened.values() if v[2])
                ready, _, _ = select.select(readers, [], [], .1)
                for device in ready:
                    path = feedback.get(device.fd) or device.path
                    if path not in opened:
                        continue
                    decoder, remapper = opened[path][1:]
                    try:
                        if device.fd in feedback:
                            remapper.read_feedback()
                            continue
                        for event in device.read():
                            if remapper:
                                remapper.feed(event)
                            else:
                                button = decoder.feed(event.type, event.code, event.value)
                                if button:
                                    dispatch('binding:' + button, path)
                    except BlockingIOError:
                        pass
                    except OSError:
                        close(path)
            except Exception as error:
                set_status(f'Could not start input handling: {error}')
                hardware.log(f'Keyboard bindings: {error}', 'WARN', source='hotkeys',
                             dedupe_key='listener')
                for path in list(opened):
                    close(path)
                stop_event.wait(2)
    finally:
        for path in list(opened):
            close(path)
