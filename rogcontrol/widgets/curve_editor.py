"""The fan curve editor: eight draggable points over temperature and rpm.

This is the GTK4 port of the GTK3 app's ``FanCurveGraph``. The drawing is
the same picture -- axes, grid, a red curve, amber handles, a label on every
point -- because that picture is what days of tuning were done against and
there is nothing wrong with it. Everything around the drawing changed:

* GTK4 has no ``draw`` signal. ``set_draw_func`` hands the Cairo context and
  the current size straight to the callback, so nothing asks the widget for
  its own allocation any more.
* Input moves from ``add_events`` plus three event handlers to controllers:
  a ``GestureDrag`` moves a point, a ``GestureClick`` selects one, an
  ``EventControllerMotion`` lights up whatever is under the pointer, and an
  ``EventControllerKey`` nudges the selection.
* The keyboard works, which it never did before. The widget takes focus, so
  Tab reaches it, arrow keys move the selected point a degree or a percent
  at a time, and Ctrl+arrow steps between points. A curve is a set of exact
  numbers and a mouse is bad at exact numbers; this is the only way to put a
  point on 68 C without hunting for the pixel.

Two rules the widget enforces rather than trusts callers with:

* **Exactly eight points, always** -- the eight the embedded controller
  stores, so every handle is one firmware slot and nothing is interpolated
  between the curve on screen and the curve the fan runs. Stock profiles ship
  four and curves saved by the old six-point editor carry six; every load
  goes through ``fancurve.editor_points``, which expands them by bisecting
  the widest temperature gaps. The user's own points come back untouched at
  the exact temperatures they were tuned to, and an already-eight-point curve
  passes through unchanged, so re-opening a profile never drifts it.
* **Points never overtake each other.** A dragged point stops one degree
  short of its neighbour. Sorting mid-drag instead -- which is what happens
  if you let them cross -- renumbers the list under the hand that is
  dragging, and the drag silently continues on a different point.

The Y axis is real rpm, not percent. Percent is a number the fan controller
uses and nobody can hear; rpm is the thing being tuned. The two are related
by this machine's own measured calibration (``fancurve.get_rpm_cal``), which
is per fan, so the three graphs on the Fans page carry three different
scales -- the mid fan reaches 7814 rpm where the CPU fan stops at 6585.
"""

import math

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GObject, Gdk, Gtk  # noqa: E402

from .. import fancurve  # noqa: E402

# Room for the axis labels. The left one holds a four digit rpm, which is
# why it is nothing like the others.
PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 46, 14, 16, 22

# Tall enough that a percent is a workable number of pixels: at 190 the plot
# area is ~150px, so one percent is 1.5px and a point can be placed exactly
# by hand. Shorter than this and the arrow keys become the only way.
DEFAULT_HEIGHT = 190

# How close the pointer has to be to a handle to grab it, in pixels. Larger
# than the handle itself on purpose -- the handle is 7px and hitting 7px
# with a trackpad is a game, not a control.
GRAB_RADIUS = 18

HANDLE_RADIUS = 6.0
HANDLE_RADIUS_ACTIVE = 8.0

TOOLTIP = (
    "Drag a point to move it.\n\n"
    "Tab to focus the graph, then:\n"
    "• arrow keys move the selected point 1 °C or 1 %\n"
    "• Shift+arrow moves it 5 at a time\n"
    "• Ctrl+left / Ctrl+right selects the previous or next point"
)


def _rgb(cr, rgb, alpha=1.0):
    cr.set_source_rgba(rgb[0], rgb[1], rgb[2], alpha)


class CurveEditor(Gtk.DrawingArea):
    """An eight point temperature/speed curve the user can drag.

    Emits ``changed`` whenever a point moves, including during a drag, so a
    page can show unsaved state the instant the curve stops matching the
    hardware. It is never emitted for :meth:`set_points` -- loading a profile
    into the editor is not the user editing it.
    """

    __gtype_name__ = "RogCurveEditor"

    __gsignals__ = {
        # No arguments: the editor owns the points and the page asks for
        # them with get_points(). Passing them through the signal would hand
        # out a list that is about to be mutated by the next drag frame.
        "changed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, rpm_cal=None, label=""):
        super().__init__()
        self._points = fancurve.editor_points([[40, 20], [90, 90]])
        self._rpm_cal = rpm_cal
        self._selected = None
        self._hover = None
        self._drag_index = None
        self._drag_origin = (0.0, 0.0)

        self.set_size_request(-1, DEFAULT_HEIGHT)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        self.set_tooltip_text(TOOLTIP)

        # Focusable so Tab reaches it, and focus-on-click so that clicking a
        # point also puts the keyboard on this graph rather than leaving it
        # wherever it was -- otherwise an arrow key after a click would nudge
        # some other widget entirely.
        self.set_focusable(True)
        self.set_focus_on_click(True)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL,
             Gtk.AccessibleProperty.DESCRIPTION],
            [label or "Fan curve",
             "Eight point fan curve. Arrow keys move the selected point, "
             "Ctrl and left or right selects another point."])

        self._install_controllers()

    # -- controllers ---------------------------------------------------------

    def _install_controllers(self):
        # GestureDrag recognises on button press, so drag-begin is where the
        # point under the pointer is picked up; there is no separate press
        # handler.
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        # Selection without movement. Neither gesture claims the sequence, so
        # a plain click reaches both: this one selects, the drag above has
        # nothing to move because the pointer never travelled.
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click_pressed)
        self.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)

        # The focus ring is drawn by hand -- nothing in the stylesheet can
        # reach a Cairo surface -- so a focus change has to repaint.
        self.connect("notify::has-focus", lambda *_a: self.queue_draw())

    # -- public API ----------------------------------------------------------

    def set_points(self, points):
        """Put a curve on screen. Does not emit ``changed``."""
        self._points = fancurve.editor_points(points)
        if self._selected is not None:
            self._selected = min(self._selected, len(self._points) - 1)
        self.queue_draw()

    def get_points(self):
        """The curve as the config stores it: a list of ``[temp, percent]``.

        A fresh list of fresh lists every call -- the caller saves this into
        a profile, and handing out the editor's own rows would let the next
        drag rewrite a profile that was already saved."""
        return [[int(t), int(p)] for t, p in self._points]

    def set_rpm_cal(self, rpm_cal):
        """Swap in a new (floor, slope), e.g. after calibration."""
        self._rpm_cal = rpm_cal
        self.queue_draw()

    def get_selected(self):
        return self._selected

    # -- geometry ------------------------------------------------------------

    def _plot(self, width, height):
        """The drawable rectangle inside the axis labels."""
        return (PAD_LEFT, PAD_TOP,
                max(1.0, width - PAD_LEFT - PAD_RIGHT),
                max(1.0, height - PAD_TOP - PAD_BOTTOM))

    def _to_pixel(self, temp, pct, width, height):
        x0, y0, w, h = self._plot(width, height)
        return x0 + (temp / 100.0) * w, y0 + h - (pct / 100.0) * h

    def _to_value(self, x, y, width, height):
        x0, y0, w, h = self._plot(width, height)
        temp = (x - x0) / w * 100.0
        pct = (y0 + h - y) / h * 100.0
        return temp, pct

    def _point_at(self, x, y):
        """Index of the handle within grabbing distance of (x, y), or None.

        Nearest wins rather than first: two points a few degrees apart have
        overlapping grab circles, and taking the first would make the left
        one impossible to let go of."""
        width = self.get_width()
        height = self.get_height()
        best, best_d2 = None, GRAB_RADIUS ** 2
        for i, (temp, pct) in enumerate(self._points):
            px, py = self._to_pixel(temp, pct, width, height)
            d2 = (px - x) ** 2 + (py - y) ** 2
            if d2 <= best_d2:
                best, best_d2 = i, d2
        return best

    # -- pointer -------------------------------------------------------------

    def _on_click_pressed(self, _gesture, _n_press, x, y):
        index = self._point_at(x, y)
        if index is not None:
            self._select(index)
        self.grab_focus()

    def _on_drag_begin(self, _gesture, x, y):
        self._drag_origin = (x, y)
        self._drag_index = self._point_at(x, y)
        if self._drag_index is not None:
            self._select(self._drag_index)
        self.grab_focus()

    def _on_drag_update(self, _gesture, offset_x, offset_y):
        self._move_to_pointer(offset_x, offset_y)

    def _on_drag_end(self, _gesture, offset_x, offset_y):
        if self._drag_index is None:
            return
        self._move_to_pointer(offset_x, offset_y)
        self._drag_index = None
        # One last emission on release: a page that only listens for the end
        # of a gesture (rather than every frame of it) still hears about the
        # final position.
        self.emit("changed")

    def _move_to_pointer(self, offset_x, offset_y):
        if self._drag_index is None:
            return
        x = self._drag_origin[0] + offset_x
        y = self._drag_origin[1] + offset_y
        temp, pct = self._to_value(x, y, self.get_width(), self.get_height())
        self._apply_move(self._drag_index, temp, pct)

    def _on_motion(self, _controller, x, y):
        hover = self._point_at(x, y)
        if hover != self._hover:
            self._hover = hover
            self.queue_draw()

    def _on_leave(self, _controller):
        if self._hover is not None:
            self._hover = None
            self.queue_draw()

    # -- keyboard ------------------------------------------------------------

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        step = 5 if shift else 1

        if keyval in (Gdk.KEY_Home, Gdk.KEY_End):
            self._select(0 if keyval == Gdk.KEY_Home else len(self._points) - 1)
            return True

        deltas = {
            Gdk.KEY_Left: (-step, 0), Gdk.KEY_KP_Left: (-step, 0),
            Gdk.KEY_Right: (step, 0), Gdk.KEY_KP_Right: (step, 0),
            Gdk.KEY_Up: (0, step), Gdk.KEY_KP_Up: (0, step),
            Gdk.KEY_Down: (0, -step), Gdk.KEY_KP_Down: (0, -step),
        }
        delta = deltas.get(keyval)
        if delta is None:
            return False

        # Arrows do nothing until something is selected, which would read as
        # a dead widget. Focusing and pressing an arrow selects instead --
        # the coolest point going right, the hottest going left, so the
        # keystroke lands where the eye already is.
        if self._selected is None:
            self._select(len(self._points) - 1 if delta[0] < 0 else 0)
            return True

        if ctrl and delta[1] == 0:
            self._select(max(0, min(len(self._points) - 1,
                                    self._selected + (1 if delta[0] > 0 else -1))))
            return True

        temp, pct = self._points[self._selected]
        self._apply_move(self._selected, temp + delta[0], pct + delta[1])
        return True

    # -- mutation ------------------------------------------------------------

    def _select(self, index):
        if index == self._selected:
            return
        self._selected = index
        self.queue_draw()

    def _apply_move(self, index, temp, pct):
        moved = fancurve.move_point(self._points, index, temp, pct)
        if moved == self._points:
            # Dragging into a wall, or a sub-degree mouse movement. Repainting
            # and telling the page the curve changed on every such frame
            # would mean a drag along the top of the graph emits hundreds of
            # no-op changes.
            return
        self._points = moved
        self.queue_draw()
        self.emit("changed")

    # -- drawing -------------------------------------------------------------

    def _palette(self):
        """Colours for the current theme.

        The plot is drawn on Cairo, not on GTK nodes, so nothing in the
        stylesheet reaches it and the colours have to be picked here. Two
        sets rather than one: the old GTK3 graph hardcoded a near-black
        background, which on a light desktop was a hole in the window.

        The curve stays ROG red and the handles stay amber in both themes --
        those are the colours the curves were tuned against, and both hold
        their contrast on either background."""
        dark = Adw.StyleManager.get_default().get_dark()
        if dark:
            return {
                "bg": (0.11, 0.11, 0.12),
                "grid": (0.22, 0.22, 0.25),
                "axis": (0.55, 0.55, 0.58),
                "text": (0.92, 0.92, 0.94),
                "curve": (0.85, 0.20, 0.22),
                "fill": (0.85, 0.20, 0.22),
                "handle": (1.0, 0.82, 0.40),
                "handle_active": (1.0, 0.95, 0.60),
                "focus": (0.45, 0.62, 0.95),
            }
        return {
            "bg": (0.98, 0.98, 0.99),
            "grid": (0.87, 0.87, 0.89),
            "axis": (0.42, 0.42, 0.45),
            "text": (0.16, 0.16, 0.18),
            "curve": (0.78, 0.13, 0.16),
            "fill": (0.78, 0.13, 0.16),
            "handle": (0.93, 0.60, 0.05),
            "handle_active": (0.99, 0.45, 0.02),
            "focus": (0.21, 0.42, 0.85),
        }

    def _rpm_text(self, pct):
        """A speed as the user reads it: real rpm where the fan has been
        calibrated, percent where it has not. Never both -- the graph is
        small, and two numbers per label is what made the old one crowded."""
        if self._rpm_cal:
            return f"{fancurve.pct_to_rpm(pct, *self._rpm_cal)}"
        return f"{int(round(pct))}%"

    @staticmethod
    def _rounded_rect(cr, x, y, w, h, radius):
        cr.new_sub_path()
        cr.arc(x + w - radius, y + radius, radius, -math.pi / 2, 0)
        cr.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
        cr.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
        cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _draw(self, _area, cr, width, height):
        colours = self._palette()

        cr.select_font_face("sans-serif")
        _rgb(cr, colours["bg"])
        self._rounded_rect(cr, 0, 0, width, height, 8)
        cr.fill()

        self._draw_grid(cr, colours, width, height)
        self._draw_curve(cr, colours, width, height)
        self._draw_handles(cr, colours, width, height)

        if self.has_focus():
            _rgb(cr, colours["focus"], 0.9)
            cr.set_line_width(2)
            self._rounded_rect(cr, 1, 1, width - 2, height - 2, 8)
            cr.stroke()

    def _draw_grid(self, cr, colours, width, height):
        x0, y0, w, h = self._plot(width, height)
        cr.set_line_width(1)
        cr.set_font_size(10)
        for i in range(5):
            fraction = i / 4.0

            # Vertical: temperature, labelled along the bottom.
            x = round(x0 + fraction * w) + 0.5
            _rgb(cr, colours["grid"])
            cr.move_to(x, y0)
            cr.line_to(x, y0 + h)
            cr.stroke()
            label = f"{round(fraction * 100)}"
            _rgb(cr, colours["axis"])
            extents = cr.text_extents(label)
            cr.move_to(min(max(x - extents.width / 2, 2), width - extents.width - 2),
                       height - 6)
            cr.show_text(label)

            # Horizontal: speed, labelled up the left in this fan's own rpm.
            y = round(y0 + fraction * h) + 0.5
            _rgb(cr, colours["grid"])
            cr.move_to(x0, y)
            cr.line_to(x0 + w, y)
            cr.stroke()
            label = self._rpm_text(round(100 - fraction * 100))
            _rgb(cr, colours["axis"])
            extents = cr.text_extents(label)
            cr.move_to(max(2, PAD_LEFT - 6 - extents.width), y + 3)
            cr.show_text(label)

        # Unit captions, once each, rather than on every gridline: "100" and
        # "6585" are unambiguous next to an axis that says what it is. Both
        # sit in a corner no tick label reaches -- the temperature one used
        # to be drawn at the right-hand end, on top of "100".
        _rgb(cr, colours["axis"])
        cr.set_font_size(9)
        cr.move_to(2, height - 6)
        cr.show_text("°C")
        cr.move_to(2, PAD_TOP - 5)
        cr.show_text("rpm" if self._rpm_cal else "fan")

    def _draw_curve(self, cr, colours, width, height):
        pixels = [self._to_pixel(t, p, width, height) for t, p in self._points]
        if len(pixels) < 2:
            return
        x0, y0, w, h = self._plot(width, height)
        baseline = y0 + h

        # Translucent fill under the line: with three graphs stacked on one
        # page it is what makes "this fan runs hotter than that one" readable
        # at a glance rather than by comparing two thin lines.
        #
        # It runs the full width of the plot, not just between the first and
        # last point. That is not decoration: below the first point the
        # controller holds the first point's speed, and above the last it
        # holds the last one's. A curve drawn only between them leaves the
        # user's real curve floating in the middle of an empty graph and says
        # nothing about what the fan does at 30 C.
        cr.move_to(x0, baseline)
        cr.line_to(x0, pixels[0][1])
        for x, y in pixels:
            cr.line_to(x, y)
        cr.line_to(x0 + w, pixels[-1][1])
        cr.line_to(x0 + w, baseline)
        cr.close_path()
        _rgb(cr, colours["fill"], 0.14)
        cr.fill()

        cr.set_line_width(2.5)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        # The held ends, dimmer than the curve itself: they are what the
        # controller does rather than points the user placed, and drawing
        # them at full strength would invite dragging them.
        _rgb(cr, colours["curve"], 0.45)
        cr.move_to(x0, pixels[0][1])
        cr.line_to(*pixels[0])
        cr.move_to(*pixels[-1])
        cr.line_to(x0 + w, pixels[-1][1])
        cr.stroke()

        _rgb(cr, colours["curve"])
        cr.move_to(*pixels[0])
        for point in pixels[1:]:
            cr.line_to(*point)
        cr.stroke()

    def _draw_handles(self, cr, colours, width, height):
        # Every handle first, then every label. Interleaved, a point drawn
        # later covers the label of the point before it -- which on a curve
        # whose points cluster within a few degrees is most of them.
        #
        # Active points come last in both passes, so the one under the
        # pointer is drawn over its neighbours rather than under them.
        order = sorted(range(len(self._points)),
                       key=lambda i: i in (self._drag_index, self._hover,
                                           self._selected))
        for i in order:
            x, y = self._to_pixel(*self._points[i], width, height)
            active = i in (self._drag_index, self._hover, self._selected)
            radius = HANDLE_RADIUS_ACTIVE if active else HANDLE_RADIUS

            _rgb(cr, colours["bg"])
            cr.arc(x, y, radius + 1.5, 0, 2 * math.pi)
            cr.fill()
            _rgb(cr, colours["handle_active"] if active else colours["handle"])
            cr.arc(x, y, radius, 0, 2 * math.pi)
            cr.fill()
            if i == self._selected:
                # A ring, not just a colour: the selected point has to be
                # findable while the pointer is elsewhere lighting up a
                # different one.
                _rgb(cr, colours["text"])
                cr.set_line_width(1.5)
                cr.arc(x, y, radius + 3, 0, 2 * math.pi)
                cr.stroke()

        placed = []
        for i in order:
            temp, pct = self._points[i]
            x, y = self._to_pixel(temp, pct, width, height)
            self._draw_point_label(
                cr, colours, x, y, temp, pct,
                i in (self._drag_index, self._hover, self._selected),
                width, placed)

    def _draw_point_label(self, cr, colours, x, y, temp, pct, active,
                          width, placed):
        """Label one point, above or below it, or not at all.

        Eight points on a curve tuned for a real machine are not evenly
        spread -- the interesting ones cluster in the ten degrees where the
        fan starts to move, and the old editor drew every label
        unconditionally and overlapped them into an unreadable smear exactly
        there. Two more points than that editor had makes this worse, not
        better, which is why the collision test below is not optional.

        So: try above the handle, then below, and if both collide with a
        label already on the graph, drop this one. Nothing is lost -- the
        point is still visibly there, and hovering or selecting it brings its
        label back, because an active point is drawn last and is allowed to
        overlap. Dropping a label beats printing two on top of each other,
        which is a number the user cannot read at all."""
        label = f"{temp}° {self._rpm_text(pct)}"
        cr.set_font_size(11 if active else 10)
        extents = cr.text_extents(label)
        label_x = min(max(x + 10, 2), width - extents.width - 2)

        for label_y in (max(y - 11, PAD_TOP + 9),
                        min(y + 19, self.get_height() - PAD_BOTTOM - 2)):
            box = (label_x, label_y - extents.height,
                   label_x + extents.width, label_y)
            if active or not any(self._overlaps(box, other) for other in placed):
                placed.append(box)
                # A wash of the background behind the text: labels are drawn
                # over the handles and the filled area, and amber digits on
                # an amber handle cannot be read.
                _rgb(cr, colours["bg"], 0.75)
                self._rounded_rect(cr, box[0] - 3, box[1] - 2,
                                   extents.width + 6, extents.height + 4, 3)
                cr.fill()
                _rgb(cr, colours["text"])
                cr.move_to(label_x, label_y)
                cr.show_text(label)
                return

    @staticmethod
    def _overlaps(a, b, margin=3):
        return not (a[2] + margin < b[0] or b[2] + margin < a[0]
                    or a[3] + margin < b[1] or b[3] + margin < a[1])
