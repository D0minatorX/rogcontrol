"""Exercise the actual Wayland wire client against a local test compositor."""
import importlib.util
import os
import socket
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch


def uint(*values):
    return struct.pack('=' + 'I' * len(values), *values)


def string(value):
    value = value.encode() + b'\0'
    return uint(len(value)) + value + b'\0' * (-len(value) % 4)


def packet(object_id, opcode, payload=b''):
    return uint(object_id, ((len(payload) + 8) << 16) | opcode) + payload


class Compositor:
    def __init__(self, path, version=2):
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(path)
        self.server.listen()
        self.server.settimeout(2)
        self.version = version
        self.notifications = []
        self.closed = threading.Event()
        self.client = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def send(self, obj, opcode, payload=b''):
        self.client.sendall(packet(obj, opcode, payload))

    def run(self):
        try:
            self.client, _ = self.server.accept()
            self.client.settimeout(2)
            data = b''
            while not self.closed.is_set():
                part = self.client.recv(4096)
                if not part:
                    break
                data += part
                while len(data) >= 8:
                    obj, word = struct.unpack_from('=II', data)
                    size, opcode = word >> 16, word & 65535
                    if len(data) < size:
                        break
                    payload, data = data[8:size], data[size:]
                    if obj == 1 and opcode == 1:
                        registry = struct.unpack('=I', payload)[0]
                        self.send(registry, 0, uint(10) + string('wl_seat') + uint(9))
                        if self.version:
                            self.send(registry, 0, uint(11) + string('ext_idle_notifier_v1') + uint(self.version))
                    elif obj == 1 and opcode == 0:
                        callback = struct.unpack('=I', payload)[0]
                        self.send(callback, 0, uint(1))
                        self.send(1, 1, uint(callback))
                    elif opcode in (1, 2) and len(payload) == 12:
                        notification, milliseconds, seat = struct.unpack('=III', payload)
                        self.notifications.append((notification, milliseconds, opcode, seat))
        except (OSError, TimeoutError):
            pass

    def close(self):
        self.closed.set()
        if self.client:
            try:
                self.client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.client.close()
        self.server.close()
        self.thread.join(2)


class WaylandIdleTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('rogcontrol.wayland_idle'),
                             'Wayland idle backend missing')
        from rogcontrol.wayland_idle import WaylandIdle
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = self.temp.name + '/wayland-test'
        self.factory = WaylandIdle

    def connect(self, version=2):
        self.server = Compositor(self.path, version)
        self.addCleanup(self.server.close)
        self.client = self.factory(self.path)
        self.addCleanup(self.client.close)
        return self.client

    def flush(self):
        # A sync barrier after server events prevents timing-dependent assertions.
        self.client.sync()

    def test_idle_resume_and_changed_timeout(self):
        client = self.connect()
        self.assertEqual(client.read(15), 0)
        self.flush()
        first = self.server.notifications[-1]
        self.assertEqual(first[1:3], (15000, 2))
        self.server.send(first[0], 0)
        self.flush()
        self.assertEqual(client.read(15), 15)
        self.server.send(first[0], 1)
        self.flush()
        self.assertEqual(client.read(15), 0)
        self.assertEqual(client.read(60), 0)
        self.flush()
        self.assertEqual(self.server.notifications[-1][1], 60000)
        self.assertNotEqual(first[0], self.server.notifications[-1][0])

    def test_v1_uses_inhibitor_aware_request(self):
        client = self.connect(version=1)
        client.read(10)
        self.flush()
        self.assertEqual(self.server.notifications[-1][2], 1)

    def test_wayland_events_dim_and_restore_saved_brightness(self):
        from rogcontrol.keyboard_idle import IdleController
        level = [2]
        def write(value):
            level[0] = value
            return True, ''
        controller = IdleController(lambda: level[0], write,
                                    self.temp.name + '/brightness.json')
        cfg = {'kbd_brightness': 2, 'kbd_idle_timeout_battery_seconds': 15}
        client = self.connect()
        controller.tick(cfg, False, client.read(15))
        self.assertEqual(level[0], 2)
        self.server.send(self.server.notifications[-1][0], 0)
        self.flush()
        controller.tick(cfg, False, client.read(15))
        self.assertEqual(level[0], 0)
        self.server.send(self.server.notifications[-1][0], 1)
        self.flush()
        controller.tick(cfg, False, client.read(15))
        self.assertEqual(level[0], 2)

    def test_missing_protocol_is_unavailable(self):
        from rogcontrol.wayland_idle import Unavailable
        server = Compositor(self.path, version=0)
        self.addCleanup(server.close)
        with self.assertRaises(Unavailable):
            self.factory(self.path)

    def test_connection_loss_never_reports_idle(self):
        client = self.connect()
        client.read(5)
        self.flush()
        self.server.close()
        with self.assertRaises(OSError):
            client.read(5)

    def test_removed_seat_invalidates_connection(self):
        client = self.connect()
        client.read(5)
        self.server.send(client.registry, 1, uint(10))
        with self.assertRaises(OSError):
            self.flush()

    def test_no_socket_environment_has_clear_error(self):
        from rogcontrol.wayland_idle import Unavailable
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(Unavailable):
            self.factory()
