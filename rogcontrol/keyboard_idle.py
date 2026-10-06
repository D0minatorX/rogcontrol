"""Opt-in keyboard backlight idle policy, independent of the GTK window.

Only desktop-provided idle durations are queried; no input events are read.
Mutter works on Wayland and X11. KDE Wayland uses ext-idle-notify because
KDE's GetSessionIdleTime supports X11 only:
https://github.com/KDE/kscreenlocker/blob/master/interface.cpp
Temporary dimming is recorded in a private runtime file, never in config.
"""
import json
import os
from pathlib import Path
import tempfile
import time

from . import config, hardware, wayland_idle

UNAVAILABLE = ('Idle detection unavailable. Requires GNOME, KDE X11, or a Wayland '
               'compositor exposing ext-idle-notify (including modern KDE Plasma). '
               'The backlight is left unchanged.')


def state_path():
    return Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'rogcontrol-keyboard-idle.json'


def _read_state(path):
    try:
        value = json.loads(Path(path).read_text())
        if (isinstance(value, dict) and type(value.get('brightness')) is int
                and 1 <= value['brightness'] <= 3):
            return value
    except (OSError, ValueError, TypeError):
        pass
    return None


def is_dimmed(path=None):
    return _read_state(path if path is not None else state_path()) is not None


def timeout_seconds(cfg, ac):
    if ac is None:
        return 0
    value = cfg.get('kbd_idle_timeout_ac_seconds' if ac else 'kbd_idle_timeout_battery_seconds', 0)
    return value if type(value) is int and 0 <= value <= 3600 else 0


class DesktopIdle:
    """Bounded session-bus queries, safe to call from a worker thread."""
    BACKENDS = (
        ('GNOME', 'org.gnome.Mutter.IdleMonitor',
         '/org/gnome/Mutter/IdleMonitor/Core', 'org.gnome.Mutter.IdleMonitor',
         'GetIdletime'),
        ('KDE X11', 'org.kde.screensaver', '/ScreenSaver',
         'org.freedesktop.ScreenSaver', 'GetSessionIdleTime'),
    )

    def __init__(self):
        self.backend = None
        self.wayland = None
        self.retry_at = 0

    def close(self):
        if self.wayland is not None:
            self.wayland.close()
            self.wayland = None
        self.backend = None

    def _read_wayland(self, timeout):
        try:
            if self.wayland is None:
                self.wayland = wayland_idle.WaylandIdle()
            result = self.wayland.read(timeout)
            suffix = ('input idle' if self.wayland.version >= 2
                      else 'idle inhibitors respected')
            self.backend = (f'Wayland ({suffix})',)
            return result
        except (OSError, ValueError):
            self.close()
            self.retry_at = time.monotonic() + 15
            return None

    def read(self, timeout=1):
        if self.wayland is not None:
            return self._read_wayland(timeout)
        if time.monotonic() < self.retry_at:
            return None
        return self._read_desktop(timeout)

    def _read_desktop(self, timeout):
        try:
            from gi.repository import Gio
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            return self._read_wayland(timeout)
        candidates = ([self.backend] if self.backend else []) + [
            b for b in self.BACKENDS if b != self.backend]
        for backend in candidates:
            _, destination, path, interface, method = backend
            try:
                reply = connection.call_sync(
                    destination, path, interface, method, None, None,
                    Gio.DBusCallFlags.NO_AUTO_START, 750, None)
                value = reply.unpack()[0]
                if type(value) is int and value >= 0:
                    self.backend = backend
                    return value / 1000.0
            except Exception:
                continue
        self.backend = None
        return self._read_wayland(timeout)


def support_status():
    idle = DesktopIdle()
    try:
        if idle.read() is None:
            return UNAVAILABLE
        return (f'Idle detection: {idle.backend[0]}. Keyboard or pointer activity '
                'restores the backlight. 0 seconds disables the timeout.')
    finally:
        idle.close()


class IdleController:
    def __init__(self, read_brightness=None, write_brightness=None, state_path=None):
        self.read_brightness = read_brightness or hardware.read_kbd_brightness
        self.write_brightness = write_brightness or (lambda level: hardware.run_helper('kbd', level, timeout=2))
        self.path = Path(state_path) if state_path is not None else globals()['state_path']()
        self.saved = _read_state(self.path)

    def _clear(self):
        self.path.unlink(missing_ok=True)
        self.saved = None

    def _record(self, value):
        # Record BEFORE dimming so a concurrent UI refresh cannot persist zero.
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + '.')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(value, stream)
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        self.saved = value

    def restore(self, cfg):
        if self.saved is None:
            return
        current = self.read_brightness()
        if current is None:
            return
        target = self.saved['brightness']
        if cfg.get('kbd_brightness') != self.saved.get('configured'):
            value = cfg.get('kbd_brightness')
            if type(value) is int and 0 <= value <= 3:
                target = value
        # A hardware hotkey or another writer has already taken ownership.
        if current != 0 or target == 0 or self.write_brightness(target)[0]:
            self._clear()

    def tick(self, cfg, ac, idle_seconds):
        timeout = timeout_seconds(cfg, ac)
        if not timeout or idle_seconds is None or idle_seconds < timeout:
            self.restore(cfg)
            return
        if self.saved is not None:
            if cfg.get('kbd_brightness') != self.saved.get('configured'):
                self.restore(cfg)
            return
        level = self.read_brightness()
        if type(level) is not int or not 1 <= level <= 3:
            return
        self._record({'brightness': level, 'configured': cfg.get('kbd_brightness')})
        if not self.write_brightness(0)[0]:
            # A timed-out helper may already have written zero. Keep the
            # restoration marker unless readback proves the light stayed on.
            current = self.read_brightness()
            if current is not None and current > 0:
                self._clear()


def run(stop_event):
    """Run in the enforcer's own thread; stop_event also restores on exit."""
    controller = IdleController()
    idle = DesktopIdle()
    cfg = {}
    try:
        while not stop_event.is_set():
            try:
                cfg = config.load_config()
                ac = hardware.is_ac_connected()
                timeout = timeout_seconds(cfg, ac)
                if timeout:
                    seconds = idle.read(timeout)
                else:
                    idle.close()
                    seconds = None
                controller.tick(cfg, ac, seconds)
            except Exception as exc:
                # A missing runtime directory or helper must not kill the service.
                print(f'rogcontrol: keyboard idle: {exc}', flush=True)
            stop_event.wait(1)
    finally:
        idle.close()
        try:
            controller.restore(config.load_config())
        except Exception as exc:
            print(f'rogcontrol: keyboard idle restore: {exc}', flush=True)
