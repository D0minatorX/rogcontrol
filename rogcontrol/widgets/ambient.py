"""Ambient mode: painting the keyboard with what is on the screen.

This is the one piece of the app that has to capture the desktop, and on
Wayland there is exactly one way to do that: ask xdg-desktop-portal for a
ScreenCast session and read the resulting PipeWire stream. There is no
unsandboxed screenshot call to fall back on, so if either half is missing the
mode is withheld rather than offered and left to fail.

Two things in here look optional and are not:

* the ``videorate`` element. The compositor only hands over a frame when the
  screen actually *changes*, so on a still desktop the sampling thread would
  block forever waiting for one and the keys would freeze on whatever was
  last showing. ``videorate`` repeats the last frame at a fixed 2 fps, and
  the unchanged-colour check in the loop means those repeats cost nothing.
* the restore token. The portal hands one back after the user grants
  permission; saving it and passing it next time is what turns the
  "share your screen?" dialog into a one-time prompt instead of something
  the user sees at every launch.

Kept out of ``pages/keyboard.py`` because it is a capture pipeline rather
than a control: the page decides *that* the keyboard follows the screen, and
this decides what the screen currently looks like.
"""

import os
import threading

import gi

from gi.repository import Gio, GLib

from .. import kbdcolor

# The screen is scaled to this before averaging. Small enough to cost
# nothing, big enough that scaling averages whole regions instead of point
# sampling a few stray pixels.
AMBIENT_GRID_W, AMBIENT_GRID_H = 64, 36
# How fast the frames arrive, and how fast the keyboard is repainted. A
# keyboard write is a ~270 ms USB round trip through rogauracore, so there is
# nothing to gain from asking for more.
AMBIENT_FPS = 2
AMBIENT_INTERVAL_S = 0.5

# Everything the pipeline needs beyond GStreamer core. Checked by name rather
# than assumed, because gst-plugin-pipewire is a separate package on every
# distribution that ships it.
GST_ELEMENTS = ("pipewiresrc", "videorate", "videoconvert", "videoscale",
                "appsink")


def ambient_available():
    """True if this machine can stream its screen for Ambient mode.

    Screen capture on Wayland goes through the desktop portal -- there is no
    unsandboxed screenshot API to fall back on -- so this needs both the
    ScreenCast portal and GStreamer's PipeWire source."""
    try:
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
    except (ImportError, ValueError):
        return False
    if not Gst.is_initialized():
        Gst.init(None)
    if not all(Gst.ElementFactory.find(name) for name in GST_ELEMENTS):
        return False
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.ScreenCast", None)
        return proxy.get_cached_property("version") is not None
    except GLib.Error:
        return False


class AmbientSampler:
    """Paints the keyboard with what is on the primary monitor.

    The desktop asks for permission the first time; the portal hands back a
    restore token which is saved, so later runs reconnect to the same monitor
    without prompting again.

    Portal negotiation is asynchronous and runs on the GTK main loop, because
    every step answers on a D-Bus signal. Only the sampling loop is a thread,
    since writing the keyboard blocks for a moment and must not stutter the
    window.
    """

    def __init__(self, on_colors, on_status, restore_token=None,
                 on_token=None):
        self.on_colors = on_colors        # called with 4 (r, g, b) tuples
        self.on_status = on_status        # called with a status string
        self.on_token = on_token          # called when the portal issues one
        self.restore_token = restore_token
        self._bus = None
        self._proxy = None
        self._session = None
        self._pipeline = None
        self._appsink = None
        self._thread = None
        self._stop = threading.Event()
        self._token_counter = 0
        self._subscriptions = []

    # -- portal handshake -----------------------------------------------------

    def _unique_token(self, prefix):
        self._token_counter += 1
        # The portal builds the request object path from this, so it has to
        # be unique per call and contain only path-safe characters.
        return f"rogcontrol_{prefix}_{os.getpid()}_{self._token_counter}"

    def _await_response(self, token, callback):
        """Run callback(results) when the portal answers this request."""
        sender = self._bus.get_unique_name()[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        def on_signal(_conn, _sender, _path, _iface, _signal, params):
            code, results = params.unpack()
            for sub in self._subscriptions:
                if sub[1] == path:
                    self._bus.signal_unsubscribe(sub[0])
                    self._subscriptions.remove(sub)
                    break
            if code != 0:
                # 1 is the user cancelling the picker, which is a choice
                # rather than a failure.
                self.on_status("Ambient: screen sharing declined"
                               if code == 1 else
                               f"Ambient: portal returned {code}")
                self.stop()
                return
            callback(results)

        sub_id = self._bus.signal_subscribe(
            "org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request",
            "Response", path, None, Gio.DBusSignalFlags.NONE, on_signal)
        self._subscriptions.append((sub_id, path))

    def start(self):
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._proxy = Gio.DBusProxy.new_sync(
                self._bus, Gio.DBusProxyFlags.NONE, None,
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.ScreenCast", None)
        except GLib.Error as e:
            self.on_status(f"Ambient: no screen portal ({e.message})")
            return

        token = self._unique_token("create")
        self._await_response(token, self._on_session_created)
        self.on_status("Ambient: asking for screen access…")
        self._proxy.call(
            "CreateSession",
            GLib.Variant("(a{sv})", ({
                "handle_token": GLib.Variant("s", token),
                "session_handle_token": GLib.Variant(
                    "s", self._unique_token("session")),
            },)),
            Gio.DBusCallFlags.NONE, -1, None, self._ignore_reply)

    def _ignore_reply(self, proxy, result):
        # The real answer arrives as a Response signal; this only surfaces an
        # immediate D-Bus failure.
        try:
            proxy.call_finish(result)
        except GLib.Error as e:
            self.on_status(f"Ambient: portal call failed ({e.message})")

    def _on_session_created(self, results):
        self._session = results["session_handle"]
        token = self._unique_token("select")
        self._await_response(token, self._on_sources_selected)
        options = {
            "handle_token": GLib.Variant("s", token),
            "types": GLib.Variant("u", 1),        # monitors only
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", 1),  # cursor not drawn
            # 2 = keep permission until explicitly revoked, which is what
            # makes the picker a one-time prompt.
            "persist_mode": GLib.Variant("u", 2),
        }
        if self.restore_token:
            options["restore_token"] = GLib.Variant("s", self.restore_token)
        self._proxy.call(
            "SelectSources",
            GLib.Variant("(oa{sv})", (self._session, options)),
            Gio.DBusCallFlags.NONE, -1, None, self._ignore_reply)

    def _on_sources_selected(self, _results):
        token = self._unique_token("start")
        self._await_response(token, self._on_started)
        self._proxy.call(
            "Start",
            GLib.Variant("(osa{sv})", (
                self._session, "",
                {"handle_token": GLib.Variant("s", token)})),
            Gio.DBusCallFlags.NONE, -1, None, self._ignore_reply)

    def _on_started(self, results):
        # The portal reissues the token on every successful start, and it can
        # differ from the one that was handed in -- so it is saved every time
        # rather than only when there was none.
        new_token = results.get("restore_token")
        if new_token and self.on_token:
            self.on_token(new_token)
        streams = results.get("streams") or []
        if not streams:
            self.on_status("Ambient: no monitor was shared")
            self.stop()
            return
        node_id = streams[0][0]
        try:
            reply, fd_list = self._proxy.call_with_unix_fd_list_sync(
                "OpenPipeWireRemote",
                GLib.Variant("(oa{sv})", (self._session, {})),
                Gio.DBusCallFlags.NONE, -1, None, None)
            fd = fd_list.get(reply.unpack()[0])
        except GLib.Error as e:
            self.on_status(f"Ambient: cannot open the stream ({e.message})")
            self.stop()
            return
        self._build_pipeline(fd, node_id)

    # -- capture --------------------------------------------------------------

    def _build_pipeline(self, fd, node_id):
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        if not Gst.is_initialized():
            Gst.init(None)
        try:
            # videorate matters more than it looks: the desktop only sends a
            # frame when the screen actually changes, so on a still screen the
            # sampler would block forever waiting for one. videorate repeats
            # the last frame at a fixed rate, and the unchanged-colour check
            # in the loop means those repeats cost nothing.
            self._pipeline = Gst.parse_launch(
                f"pipewiresrc fd={fd} path={node_id} always-copy=true ! "
                "videorate ! videoconvert ! videoscale ! "
                f"video/x-raw,format=RGB,width={AMBIENT_GRID_W},"
                f"height={AMBIENT_GRID_H},framerate={AMBIENT_FPS}/1 ! "
                "appsink name=sink max-buffers=1 drop=true sync=false")
            self._appsink = self._pipeline.get_by_name("sink")
            self._pipeline.set_state(Gst.State.PLAYING)
        except GLib.Error as e:
            self.on_status(f"Ambient: capture failed ({e.message})")
            self.stop()
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        self.on_status("Ambient: following the screen")

    def _sample_loop(self):
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        last = None
        while not self._stop.is_set():
            sample = self._appsink.emit("try-pull-sample", Gst.SECOND)
            if sample is None:
                continue
            colors = self._sample_to_zones(sample)
            if colors is None:
                continue
            if kbdcolor.changed_enough(colors, last):
                last = colors
                self.on_colors(colors)
            self._stop.wait(AMBIENT_INTERVAL_S)

    def _sample_to_zones(self, sample):
        """Average each vertical band of the frame into one colour."""
        buf = sample.get_buffer()
        ok, info = buf.map(0)  # Gst.MapFlags.READ
        if not ok:
            return None
        try:
            data = bytes(info.data)
        finally:
            buf.unmap(info)
        caps = sample.get_caps().get_structure(0)
        width = caps.get_value("width")
        height = caps.get_value("height")
        # GStreamer pads each row to a 4-byte boundary, so the stride is not
        # necessarily width * 3 -- using the wrong one skews the colours
        # progressively down the frame.
        stride = len(data) // height if height else width * 3
        return kbdcolor.zones_from_frame(data, width, height, stride)

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None
        if self._pipeline is not None:
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        self._appsink = None
        for sub_id, _path in self._subscriptions:
            self._bus.signal_unsubscribe(sub_id)
        self._subscriptions = []
        if self._session:
            # Closing the session releases the capture; the granted permission
            # survives it, which is what the restore token is for.
            try:
                Gio.DBusProxy.new_sync(
                    self._bus, Gio.DBusProxyFlags.NONE, None,
                    "org.freedesktop.portal.Desktop", self._session,
                    "org.freedesktop.portal.Session", None).call_sync(
                        "Close", None, Gio.DBusCallFlags.NONE, -1, None)
            except GLib.Error:
                pass
            self._session = None
