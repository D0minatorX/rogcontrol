"""Asynchronous desktop-portal eyedropper; picking never writes lighting."""
import math
import threading
import uuid

from PySide6.QtCore import QObject, Property, Signal, Slot, Qt


def response_color(code, results):
    if code == 1:
        return None
    if code != 0:
        raise ValueError('The desktop could not pick a screen colour.')
    rgb = results.get('color')
    if (not isinstance(rgb, (tuple, list)) or len(rgb) != 3
            or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                   or not 0 <= v <= 1 for v in rgb)):
        raise ValueError('The desktop returned an invalid screen colour.')
    return '#%02x%02x%02x' % tuple(int(v * 255 + 0.5) for v in rgb)


def pick_color(cancelled):
    """Run a private GLib context so Qt and Ambient loops remain independent."""
    from gi.repository import Gio, GLib
    context = GLib.MainContext.new()
    context.push_thread_default()
    connection = subscription = timer = request = None
    completed = False
    result = None
    error = None
    loop = GLib.MainLoop.new(context, False)

    def response(_bus, _sender, _path, _interface, _signal, parameters):
        nonlocal completed, result, error
        completed = True
        try:
            result = response_color(*parameters.unpack())
        except ValueError as exc:
            error = exc
        loop.quit()

    try:
        # Own connection: closing it cannot interfere with Ambient capture.
        connection = Gio.DBusConnection.new_for_address_sync(
            Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None),
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        token = 'rogcontrol_color_' + uuid.uuid4().hex
        sender = connection.get_unique_name()[1:].replace('.', '_')
        expected = f'/org/freedesktop/portal/desktop/request/{sender}/{token}'
        subscription = connection.signal_subscribe(
            'org.freedesktop.portal.Desktop', 'org.freedesktop.portal.Request',
            'Response', expected, None, Gio.DBusSignalFlags.NONE, response)
        request, = connection.call_sync(
            'org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
            'org.freedesktop.portal.Screenshot', 'PickColor',
            GLib.Variant('(sa{sv})', ('', {'handle_token': GLib.Variant('s', token)})),
            GLib.VariantType.new('(o)'), Gio.DBusCallFlags.NONE, 5000, None).unpack()
        if request != expected:
            raise RuntimeError('The desktop returned an unexpected colour-picker request.')
        import time
        deadline = time.monotonic() + 120

        def check_cancel(_data=None):
            nonlocal error
            if cancelled.is_set() or time.monotonic() >= deadline:
                if not cancelled.is_set():
                    error = RuntimeError('Screen colour selection timed out. Try again.')
                loop.quit()
                return False
            return True

        timer = GLib.timeout_source_new(100)
        timer.set_callback(check_cancel)
        timer.attach(context)
        if not cancelled.is_set():
            loop.run()
        if error:
            raise error
        return None if cancelled.is_set() else result
    finally:
        if timer is not None:
            timer.destroy()
        if connection is not None:
            if request and not completed:
                try:
                    connection.call_sync(
                        'org.freedesktop.portal.Desktop', request,
                        'org.freedesktop.portal.Request', 'Close', None, None,
                        Gio.DBusCallFlags.NONE, 1000, None)
                except Exception:
                    pass
            if subscription is not None:
                connection.signal_unsubscribe(subscription)
            try:
                connection.close_sync(None)
            finally:
                context.pop_thread_default()
        else:
            context.pop_thread_default()


class ScreenColorPicker(QObject):
    changed = Signal()
    colorPicked = Signal(str)
    _finished = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._error = ''
        self._cancelled = threading.Event()
        self._finished.connect(self._complete, Qt.QueuedConnection)
        self.destroyed.connect(lambda: self._cancelled.set())

    busy = Property(bool, lambda self: self._busy, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)

    @Slot()
    def pick(self):
        if self._busy:
            return
        self._cancelled = threading.Event()
        cancelled = self._cancelled
        self._busy, self._error = True, ''
        self.changed.emit()

        def run():
            result = error = None
            try:
                result = pick_color(cancelled)
            except Exception as exc:
                error = str(exc)
            try:
                self._finished.emit(result, error)
            except RuntimeError:
                pass  # The window may have closed while the portal was open.

        threading.Thread(target=run, daemon=True, name='screen-colour-picker').start()

    @Slot()
    def cancel(self):
        self._cancelled.set()

    @Slot(object, object)
    def _complete(self, result, error):
        self._busy = False
        self._error = (error or '') if not self._cancelled.is_set() else ''
        self.changed.emit()
        if result is not None and not self._cancelled.is_set():
            self.colorPicked.emit(result)
