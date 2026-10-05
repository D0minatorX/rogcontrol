"""A slider row: the numeric control every page in this app uses.

The scale gives a quick overview of the range; the adjacent numeric entry
allows precise input. Enter or leaving the field accepts a number, Escape
restores the current value. Typed numbers use the same clamp, step snapping,
and change signal as the scale. Programmatic profile loads remain silent.

The layout is deliberately vertical -- title and readout, then the (short)
subtitle, then a full-width scale beneath both:

    ┌──────────────────────────────────────────────┐
    │ STAPM limit                            35 W  │
    │ Sustained package power                       │
    │ ──────────●──────────────────────────────────│
    └──────────────────────────────────────────────┘

A slider sitting to the *right* of a four-line subtitle is what forces a
window wide, which is the thing this page has to stop doing. Below it, the
scale gets the full row every time, the subtitle is free to wrap, and every
row in a group has an identical left edge no matter what its value is.

The subtitle is a few words at most. What a setting *means* -- the paragraph
about STAPM, the warning about undervolting too far -- is the row's tooltip
instead, so a page of seven controls is a page rather than an essay. See
``tooltip`` below.

The CSS class names on the two boxes are not decoration. libadwaita styles
``row > box.header`` and ``row > box.header > box.title`` directly -- margins,
spacing and minimum height -- so building the same node names means a
SliderRow lines up with the AdwSwitchRow above it exactly, rather than being
a hand-tuned approximation that drifts the next time the stylesheet moves.
"""

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

# How long the control has to sit still before ``changed`` is emitted. Long
# enough to swallow a drag from one end of the scale to the other, short
# enough that the result still reads as a response to what you just did.
SETTLE_MS = 400

# U+2212 MINUS SIGN, not a hyphen. See format_value.
MINUS = "−"

# Trimming the row's height.
#
# libadwaita builds these rows for a title and a subtitle, and this one puts
# a full-width scale under both -- so the stock padding, sized for a row that
# is one line of text, is applied around three stacked things. Seven of them
# on the CPU page is a page that has to be scrolled to see its own Tuning
# group.
#
# Applied to a class of this widget's own rather than to ``row`` globally:
# the same padding on the ordinary rows around it is correct, and only this
# row's stacked layout makes it too much.
_ROW_CSS = b"""
.rc-slider-row { padding-top: 8px; padding-bottom: 8px; min-height: 0; }
.rc-slider-row > box.header { margin-top: 0; margin-bottom: 0; }
.rc-slider-row > box.header > box.title { margin-bottom: 0; }
.rc-slider-row .subtitle { font-size: 0.82em; }
.rc-slider-row scale { min-height: 0; margin: 0; padding-top: 0; padding-bottom: 0; }
.rc-slider-row scale trough { min-height: 4px; margin-top: 2px; margin-bottom: 2px; }
.rc-slider-row scale slider { min-width: 16px; min-height: 16px; margin: -6px; }
"""
_css_loaded = False


def _load_row_css():
    """Once per process, at first use -- there is no display at import."""
    global _css_loaded
    if _css_loaded:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_ROW_CSS)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_loaded = True


class SliderRow(Adw.PreferencesRow):
    """A titled row with a scale and an editable numeric value.

    Emits ``changed(value)`` after ``settle_ms`` for scale changes and
    accepted numeric input (immediately when zero). Never emits for a value
    put there by :meth:`set_value`: profile loading is not a user edit.
    """

    __gtype_name__ = "RogSliderRow"

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_LAST, None, (float,)),
    }

    def __init__(self, title="", subtitle="", minimum=0.0, maximum=100.0,
                 step=1.0, digits=0, unit="", settle_ms=SETTLE_MS,
                 page_step=None, tooltip=""):
        super().__init__()
        _load_row_css()
        self.add_css_class("rc-slider-row")
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
        if tooltip:
            self.set_tooltip_text(tooltip)
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

        value_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.value_entry = Gtk.Entry(xalign=1.0)
        self.value_entry.add_css_class("numeric")
        self.value_entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
        self.value_entry.update_property(
            [Gtk.AccessibleProperty.LABEL], [f"{title} ({self._unit})" if self._unit else title])
        self.value_entry.set_tooltip_text(
            "Type a number. Enter or Tab accepts it; Escape cancels.")
        self.value_entry.connect("activate", self._accept_entry)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", self._on_entry_focus_leave)
        self.value_entry.add_controller(focus)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_entry_key)
        self.value_entry.add_controller(keys)
        self.set_value_width_chars()
        value_box.append(self.value_entry)
        self._unit_label = Gtk.Label(label=self._unit)
        self._unit_label.set_visible(bool(self._unit))
        value_box.append(self._unit_label)
        line.append(value_box)
        text.append(line)
        self.input_error = self._text_label("")
        self.input_error.add_css_class("error")
        self.input_error.add_css_class("caption")
        self.input_error.set_visible(False)
        text.append(self.input_error)

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
        number_width = max(3, width_chars - (len(self._unit) + 1 if self._unit else 0))
        self.value_entry.set_width_chars(number_width)
        self.value_entry.set_max_width_chars(number_width)

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
            self._update_label()
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
        value = self.get_value()
        self.value_entry.set_text(f"{0.0 if value == 0 else value:.{self._digits}f}")
        # Keyboard level rows override format_value with names such as
        # Medium. Keep those meanings visible beside their editable index.
        display = self.get_display_value()
        suffix = self._unit if display == SliderRow.format_value(self, value) else display
        self._unit_label.set_text(suffix)
        self._unit_label.set_visible(bool(suffix))
        self.value_entry.remove_css_class("error")
        self.input_error.set_visible(False)

    def _accept_entry(self, _entry=None):
        """Commit a complete numeric draft through the existing slider path."""
        try:
            value = float(self.value_entry.get_text().strip().replace(MINUS, "-"))
            if not math.isfinite(value):
                raise ValueError("not finite")
        except ValueError:
            self.value_entry.add_css_class("error")
            self.input_error.set_text(
                f"Enter a number from {SliderRow.format_value(self, self._adj.get_lower())} "
                f"to {SliderRow.format_value(self, self._adj.get_upper())}.")
            self.input_error.set_visible(True)
            return
        self._adj.set_value(self._snap(value))
        # A same-value entry still needs normalization, even without a signal.
        self._update_label()

    def _on_entry_focus_leave(self, _controller):
        self._accept_entry()

    def _on_entry_key(self, _controller, keyval, _keycode, _state):
        if keyval != Gdk.KEY_Escape:
            return False
        self._update_label()
        return True

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
            self.value_entry.update_property(
                [Gtk.AccessibleProperty.LABEL],
                [f"{title} ({self._unit})" if self._unit else title])

    def set_tooltip_text(self, text):
        """The explanation, on hover, from anywhere on the row.

        Set on the scale as well as on the row. GTK walks up from the widget
        under the pointer until it finds a tooltip, so the row's would be
        found through the scale anyway -- but the scale is the widest thing
        in the row and the one the pointer is most often on, and this way it
        cannot depend on that walk."""
        Gtk.Widget.set_tooltip_text(self, text)
        self.scale.set_tooltip_text(text)

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
