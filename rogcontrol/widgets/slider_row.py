"""A slider row: the numeric control every page in this app uses.

libadwaita ships ``AdwSpinRow`` but no slider equivalent, and a spin row is
the wrong shape for these settings twice over. Every number here is a
position on a range -- 15 to 150 watts, 60 to 100 degrees -- and a spin
button hides that range behind two arrows, so finding the middle of it means
clicking thirty times. Worse for a settings list, a spin button's width comes
from its digits, so a column of them has a ragged left edge: the entry beside
"3.2" is narrower than the one beside "150", and the arrows never line up.

The layout is deliberately vertical -- title and readout, then the wrapping
subtitle, then a full-width scale beneath both:

    ┌──────────────────────────────────────────────┐
    │ STAPM limit                            35 W  │
    │ Sustained package power. The ceiling the      │
    │ chip settles at.                              │
    │ ──────────●──────────────────────────────────│
    └──────────────────────────────────────────────┘

A slider sitting to the *right* of a four-line subtitle is what forces a
window wide, which is the thing this page has to stop doing. Below it, the
scale gets the full row every time, the subtitle is free to wrap, and every
row in a group has an identical left edge no matter what its value is.

The CSS class names on the two boxes are not decoration. libadwaita styles
``row > box.header`` and ``row > box.header > box.title`` directly -- margins,
spacing and minimum height -- so building the same node names means a
SliderRow lines up with the AdwSwitchRow above it exactly, rather than being
a hand-tuned approximation that drifts the next time the stylesheet moves.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, GObject, Gtk, Pango  # noqa: E402

# How long the control has to sit still before ``changed`` is emitted. Long
# enough to swallow a drag from one end of the scale to the other, short
# enough that the result still reads as a response to what you just did.
SETTLE_MS = 400

# U+2212 MINUS SIGN, not a hyphen. See format_value.
MINUS = "−"


class SliderRow(Adw.PreferencesRow):
    """A titled row holding a horizontal scale and a stable value readout.

    Emits ``changed(value)`` once the user has left the scale alone for
    ``settle_ms``. It never emits while dragging, and never for a value put
    there by :meth:`set_value` -- loading a profile into a page must not look
    like the user turning a dial.
    """

    __gtype_name__ = "RogSliderRow"

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_LAST, None, (float,)),
    }

    def __init__(self, title="", subtitle="", minimum=0.0, maximum=100.0,
                 step=1.0, digits=0, unit="", settle_ms=SETTLE_MS,
                 page_step=None):
        super().__init__()
        # No markup anywhere: these strings are hardware descriptions that
        # already contain "&" and "<" as often as not, and a stray entity is
        # a warning at best and a missing subtitle at worst.
        self.set_use_markup(False)
        self.set_title(title)
        self.set_activatable(False)
        # The row must not take focus itself: with a focusable child, Tab
        # would stop on the row *and* on the scale, so every keyboard user
        # would press it twice per setting.
        self.set_focusable(False)

        self._digits = max(0, int(digits))
        self._step = float(step)
        self._unit = unit
        self._settle_ms = int(settle_ms)
        self._settle_source = None
        # Set while a value is being written in by code rather than by the
        # user, so ``changed`` stays quiet.
        self._programmatic = False

        self._adj = Gtk.Adjustment(
            lower=float(minimum), upper=float(maximum), value=float(minimum),
            step_increment=self._step,
            page_increment=(self._step * 10 if page_step is None
                            else float(page_step)))

        self._build(title, subtitle)
        self._adj.connect("value-changed", self._on_value_changed)
        self._update_label()
        # A pending settle timer outliving the widget would emit into a
        # finalized object; the window closing mid-drag is the ordinary way
        # to get there.
        self.connect("destroy", self._on_destroy)

    # -- construction --------------------------------------------------------

    def _build(self, title, subtitle):
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header.add_css_class("header")

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.add_css_class("title")

        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._title_label = self._text_label(title)
        self._title_label.add_css_class("title")
        self._title_label.set_hexpand(True)
        line.append(self._title_label)

        self._value_label = Gtk.Label(xalign=1.0)
        # "numeric" is tabular figures: without it a 1 is narrower than a 0
        # and the readout shuffles sideways as you drag.
        self._value_label.add_css_class("numeric")
        self.set_value_width_chars()
        line.append(self._value_label)
        text.append(line)

        self._subtitle_label = self._text_label(subtitle)
        self._subtitle_label.add_css_class("subtitle")
        self._subtitle_label.set_visible(bool(subtitle))
        text.append(self._subtitle_label)
        header.append(text)

        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                               adjustment=self._adj)
        # The readout above is the value display; the scale's own would sit
        # in the middle of the row and move about as the handle does.
        self.scale.set_draw_value(False)
        # Rounding as the handle moves, so the number the user releases on is
        # the number the hardware is asked for.
        self.scale.set_round_digits(self._digits)
        self.scale.set_hexpand(True)
        # The scale is the row's control; the title label is decoration as
        # far as a screen reader is concerned.
        self.scale.update_property([Gtk.AccessibleProperty.LABEL], [title])
        header.append(self.scale)

        self.set_child(header)

    @staticmethod
    def _text_label(text):
        """A label that wraps instead of demanding width.

        Same properties AdwActionRow gives its own title and subtitle:
        word-char wrapping with no line limit, so a long subtitle costs
        height -- which the page can scroll -- rather than width, which it
        cannot."""
        label = Gtk.Label(label=text, xalign=0)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_ellipsize(Pango.EllipsizeMode.NONE)
        label.set_lines(0)
        return label

    def set_value_width_chars(self, width_chars=None):
        """Pin the readout's width so it cannot twitch while dragging.

        Both ``width-chars`` and ``max-width-chars`` are set: the first is the
        minimum, the second caps the natural size, and a label with the two
        equal requests that width whatever its text says. Sized from the two
        ends of the range, which are always the longest strings it can hold --
        the digit count only grows with magnitude."""
        if width_chars is None:
            width_chars = max(len(self.format_value(self._adj.get_lower())),
                              len(self.format_value(self._adj.get_upper())))
        self._value_label.set_width_chars(width_chars)
        self._value_label.set_max_width_chars(width_chars)

    # -- value ---------------------------------------------------------------

    def format_value(self, value):
        """The value as the user reads it, unit and all: ``35 W``.

        Negatives get a real minus sign rather than a hyphen. The readout is
        set in tabular figures, which pads every glyph to a digit's width, and
        a hyphen padded that way reads as "- 5" with a gap in the middle.
        U+2212 is drawn at digit width by design, and it is the character the
        Curve Optimizer subtitle already uses."""
        value = float(value)
        # Without this a value that rounds to zero from below prints "-0".
        if value == 0:
            value = 0.0
        text = f"{value:.{self._digits}f}".replace("-", MINUS)
        return f"{text} {self._unit}" if self._unit else text

    def get_display_value(self):
        return self.format_value(self.get_value())

    def get_value(self):
        return self._adj.get_value()

    def set_value(self, value):
        """Put a value on screen without asking anyone to apply it."""
        was = self._programmatic
        self._programmatic = True
        self._cancel_settle()
        try:
            self._adj.set_value(self._snap(value))
        finally:
            self._programmatic = was

    def get_adjustment(self):
        return self._adj

    def _snap(self, value):
        """Clamp to the range and land on a whole step.

        Snapping to the step rather than only to the decimal count is what
        keeps a step of 5 or 25 honest -- ``round-digits`` alone would let the
        handle stop anywhere between two of them."""
        lower, upper = self._adj.get_lower(), self._adj.get_upper()
        value = min(upper, max(lower, float(value)))
        if self._step > 0:
            steps = round((value - lower) / self._step)
            value = min(upper, max(lower, round(lower + steps * self._step,
                                                self._digits + 3)))
        return value

    # -- change handling -----------------------------------------------------

    def _on_value_changed(self, adj):
        value = adj.get_value()
        snapped = self._snap(value)
        if abs(snapped - value) > 1e-9:
            # Re-enters once with the snapped value, then settles: _snap is
            # idempotent, so there is no third pass.
            adj.set_value(snapped)
            return
        self._update_label()
        if self._programmatic:
            return
        self._arm_settle()

    def _update_label(self):
        self._value_label.set_text(self.get_display_value())

    def _arm_settle(self):
        self._cancel_settle()
        if self._settle_ms <= 0:
            self._emit_changed()
            return
        self._settle_source = GLib.timeout_add(self._settle_ms,
                                               self._on_settled)

    def _cancel_settle(self):
        if self._settle_source is not None:
            GLib.source_remove(self._settle_source)
            self._settle_source = None

    def _on_settled(self):
        self._settle_source = None
        self._emit_changed()
        return GLib.SOURCE_REMOVE

    def _emit_changed(self):
        self.emit("changed", self.get_value())

    def _on_destroy(self, _widget):
        self._cancel_settle()

    # -- text ----------------------------------------------------------------

    def set_title(self, title):
        Adw.PreferencesRow.set_title(self, title)
        label = getattr(self, "_title_label", None)
        if label is not None:
            label.set_text(title)
            self.scale.update_property([Gtk.AccessibleProperty.LABEL], [title])

    def set_subtitle(self, subtitle):
        subtitle = subtitle or ""
        self._subtitle_label.set_text(subtitle)
        self._subtitle_label.set_visible(bool(subtitle))

    def get_subtitle(self):
        return self._subtitle_label.get_text()


def align_value_widths(rows):
    """Give every row the same readout width, so the scales end in a column.

    Each row sizes its own readout to its own longest string, which is right
    on its own and wrong in a group: "100 °C" is a character wider than
    "150 W", so the scale beside it would stop a character short and the
    group would look ragged -- the exact complaint that retired the spin
    rows."""
    rows = [row for row in rows if isinstance(row, SliderRow)]
    if not rows:
        return
    widest = max(max(len(row.format_value(row.get_adjustment().get_lower())),
                     len(row.format_value(row.get_adjustment().get_upper())))
                 for row in rows)
    for row in rows:
        row.set_value_width_chars(widest)
