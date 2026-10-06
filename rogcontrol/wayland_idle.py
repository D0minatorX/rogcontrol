"""A private, bounded Wayland connection for ext-idle-notify notifications.

Only registry, sync, seat and idle objects are used. No surfaces, input-event
objects or file descriptors are requested. This small wire subset avoids a
new Python binding/compiler dependency for the existing GTK application.

Protocol references:
https://gitlab.freedesktop.org/wayland/wayland/-/blob/main/protocol/wayland.xml
https://gitlab.freedesktop.org/wayland/wayland-protocols/-/blob/main/staging/ext-idle-notify/ext-idle-notify-v1.xml
"""
import os
import select
import socket
import struct
import time


class Unavailable(OSError):
    """The compositor connection or required idle protocol is unavailable."""


def _uint(*values):
    return struct.pack('=' + 'I' * len(values), *values)


def _string(value):
    value = value.encode() + b'\0'
    return _uint(len(value)) + value + b'\0' * (-len(value) % 4)


class WaylandIdle:
    """One worker owns this connection; read returns a threshold, not idle age."""
    def __init__(self, path=None):
        self.sock = None
        self.buffer = b''
        self.next_id = 2
        self.globals = {}
        self.callbacks = set()
        self.bound_globals = set()
        self.notification = None
        self.timeout = None
        self.idled = False
        if path is None:
            display = os.environ.get('WAYLAND_DISPLAY')
            runtime = os.environ.get('XDG_RUNTIME_DIR')
            if not display or (not os.path.isabs(display) and not runtime):
                raise Unavailable('No Wayland display socket in this session')
            path = display if os.path.isabs(display) else os.path.join(runtime, display)
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(0.5)
            self.sock.connect(path)
            self.registry = self._id()
            self._send(1, 1, _uint(self.registry))  # wl_display.get_registry
            self.sync()
            seats = [(name, version) for name, (interface, version) in self.globals.items()
                     if interface == 'wl_seat']
            managers = [(name, version) for name, (interface, version) in self.globals.items()
                        if interface == 'ext_idle_notifier_v1']
            if len(seats) != 1 or not managers:
                raise Unavailable('Wayland requires one seat and ext-idle-notify')
            self.version = min(managers[0][1], 2)
            self.seat = self._bind(seats[0][0], 'wl_seat', 1)
            self.manager = self._bind(managers[0][0], 'ext_idle_notifier_v1', self.version)
            self.sync()
        except Exception:
            self.close()
            raise

    def _id(self):
        result = self.next_id
        self.next_id += 1
        return result

    def _send(self, obj, opcode, payload=b''):
        self.sock.sendall(_uint(obj, ((len(payload) + 8) << 16) | opcode) + payload)

    def _bind(self, name, interface, version):
        obj = self._id()
        self.bound_globals.add(name)
        self._send(self.registry, 0, _uint(name) + _string(interface) + _uint(version, obj))
        return obj

    def _event(self, obj, opcode, payload):
        if obj == 1 and opcode == 0:
            raise Unavailable('Wayland compositor rejected the idle connection')
        if obj in self.callbacks and opcode == 0:
            self.callbacks.remove(obj)
        elif obj == self.registry and opcode == 0:
            if len(payload) < 12:
                raise Unavailable('Invalid registry event')
            name, length = struct.unpack_from('=II', payload)
            end = 8 + ((length + 3) & ~3)
            if not length or len(payload) != end + 4 or payload[8 + length - 1] != 0:
                raise Unavailable('Invalid registry interface')
            interface = payload[8:8 + length - 1].decode('ascii')
            version = struct.unpack_from('=I', payload, end)[0]
            self.globals[name] = interface, version
            # Refuse newly attached second seats rather than ignoring their activity.
            if self.bound_globals and interface == 'wl_seat' and name not in self.bound_globals:
                raise Unavailable('Wayland seat configuration changed')
        elif obj == self.registry and opcode == 1:
            name, = struct.unpack('=I', payload)
            if name in self.bound_globals:
                raise Unavailable('Wayland idle manager or seat removed')
            self.globals.pop(name, None)
        elif obj == self.notification:
            if opcode == 0:
                self.idled = True
            elif opcode == 1:
                self.idled = False

    def _receive(self):
        data = self.sock.recv(65536)
        if not data:
            raise Unavailable('Wayland compositor disconnected')
        self.buffer += data
        while len(self.buffer) >= 8:
            obj, word = struct.unpack_from('=II', self.buffer)
            size, opcode = word >> 16, word & 65535
            if size < 8 or size % 4:
                raise Unavailable('Invalid Wayland event size')
            if len(self.buffer) < size:
                break
            payload, self.buffer = self.buffer[8:size], self.buffer[size:]
            self._event(obj, opcode, payload)

    def sync(self):
        """Bounded roundtrip; establishes that subscriptions reached the server."""
        callback = self._id()
        self.callbacks.add(callback)
        self._send(1, 0, _uint(callback))
        deadline = time.monotonic() + 0.75
        while callback in self.callbacks:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.sock], [], [], remaining)[0]:
                raise Unavailable('Wayland idle response timed out')
            self._receive()

    def read(self, timeout):
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError('Idle timeout must be 1–3600 seconds')
        if timeout != self.timeout:
            if self.notification is not None:
                self._send(self.notification, 0)  # destroy old subscription
            self.notification = self._id()
            self.timeout = timeout
            self.idled = False
            # v2 input notifications ignore video/presentation idle inhibitors.
            self._send(self.manager, 2 if self.version >= 2 else 1,
                       _uint(self.notification, timeout * 1000, self.seat))
            self.sync()
        # No waits on the polling path, and a bound against an event flood.
        for _ in range(16):
            if not select.select([self.sock], [], [], 0)[0]:
                break
            self._receive()
        else:
            raise Unavailable('Wayland event queue did not settle')
        return timeout if self.idled else 0

    def close(self):
        if self.sock is not None:
            self.sock.close()  # compositor destroys this connection's objects
            self.sock = None
