"""A colour picker that opens at the size it actually needs.

Why this exists instead of ``Gtk.ColorDialog``
----------------------------------------------

``Gtk.ColorDialog`` builds a ``GtkColorChooserDialog``, and that dialog puts
the chooser inside a ``GtkScrolledWindow``. The window is sized once, from the
*palette* view -- 486x319 px on this machine -- and then never grows. Choosing
"Custom" swaps the palette for the editor, whose saturation/value plane alone
is 300x300 px and which needs 366 px of height in total. The window stays
319 px tall, because the scrolled window's own minimum height is 58 px and so
the *window's* minimum never rises above what it already is. The missing
112 px are handed to a scrollbar instead, and the plane is clipped: you have
to scroll inside a colour picker to reach the bottom of the colour picker.

``Gtk.ColorDialog`` has no API for the dialog's size, so there is nothing to
set. What it does have is a public chooser widget, so this module presents
that same widget in a window of our own: measured for the taller of its two
views before it is shown, and with no scrolled window anywhere inside it.
With nothing able to absorb the difference, the plane is either fully visible
or the window is too small to exist -- and since the window is sized from the
chooser rather than from the parent, neither happens.

``Gtk.ColorChooserWidget`` is deprecated in GTK 4.10 in favour of the dialog
wrapper above, and is still the widget that wrapper is made of. Everything
here goes through ``get_property``/``set_property`` rather than the
``Gtk.ColorChooser`` interface methods of the same name, which is the same
call without PyGObject's deprecation warning on every colour the user picks.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GObject, Gtk  # noqa: E402

# The swatch inside the button, in px before the button's own padding. Wider
# than it is tall, like every other colour button in GNOME -- a square reads
# as an icon, a landscape rectangle reads as a sample of a colour.
SWATCH_WIDTH, SWATCH_HEIGHT = 30, 18

# The rounded corner on that swatch, and how strongly it is outlined. The
# outline is the theme's own foreground colour at low alpha rather than a
# fixed grey, so a white swatch still has an edge on a light theme and a
# black one still has an edge on a dark one.
SWATCH_RADIUS = 4
SWATCH_BORDER_ALPHA = 0.35


def _rounded_rect(cr, x, y, width, height, radius):
    """A rounded rectangle path on ``cr``, for the swatch."""
    radius = min(radius, width / 2.0, height / 2.0)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -1.5708, 0.0)
    cr.arc(x + width - radius, y + height - radius, radius, 0.0, 1.5708)
    cr.arc(x + radius, y + height - radius, radius, 1.5708, 3.1416)
    cr.arc(x + radius, y + radius, radius, 3.1416, 4.7124)
    cr.close_path()


def present_color_picker(parent, rgba, on_chosen, title="Colour"):
    """Open the colour chooser over ``parent``, fully visible.

    ``on_chosen`` is called with the picked ``Gdk.RGBA`` when the user
    accepts, and not at all when they cancel -- the same contract as
    ``Gtk.ColorDialog.choose_rgba``.
    """
    chooser = Gtk.ColorChooserWidget()
    # The keyboard has no alpha channel, so offering one would let the user
    # set a transparency that is silently discarded on the way out.
    chooser.set_property("use-alpha", False)
    if rgba is not None:
        chooser.set_property("rgba", rgba)

    # The whole point of this module: the chooser is the window's only
    # content, with nothing between the two that could absorb a shortfall.
    # The chooser's minimum height is therefore the window's minimum height,
    # so when the editor replaces the palette and that minimum jumps from
    # 244 px to 356 px, GTK has to grow the window -- there is no scrollbar
    # for it to reach for instead. The palette view stays snug rather than
    # opening with the editor's empty space beneath it.
    #
    # The width is the one thing pinned, at the wider of the two views, so
    # that growing in height does not also shuffle the window sideways.
    chooser.set_property("show-editor", True)
    _minimum, editor = chooser.get_preferred_size()
    chooser.set_property("show-editor", False)
    _minimum, palette = chooser.get_preferred_size()
    chooser.set_size_request(max(editor.width, palette.width), -1)
    for setter in (chooser.set_margin_top, chooser.set_margin_bottom,
                   chooser.set_margin_start, chooser.set_margin_end):
        setter(12)

    window = Adw.Window(title=title, modal=True, transient_for=parent)
    window.set_resizable(True)

    header = Adw.HeaderBar()
    header.set_show_start_title_buttons(False)
    header.set_show_end_title_buttons(False)

    cancel = Gtk.Button(label="Cancel")
    select = Gtk.Button(label="Select")
    select.add_css_class("suggested-action")
    header.pack_start(cancel)
    header.pack_end(select)

    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(header)
    toolbar.set_content(chooser)
    window.set_content(toolbar)

    def accept(*_args):
        picked = chooser.get_property("rgba")
        window.close()
        on_chosen(picked)

    def dismiss(*_args):
        window.close()

    select.connect("clicked", accept)
    cancel.connect("clicked", dismiss)
    # Double-clicking a palette swatch is "this one" in GTK's own dialog too.
    chooser.connect("color-activated", lambda _c, _rgba: accept())

    # Gtk.Window has no Escape binding of its own -- GtkDialog had one, and
    # this is not a GtkDialog -- so a picker opened by accident would have to
    # be closed with the pointer.
    keys = Gtk.EventControllerKey()

    def on_key(_controller, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            dismiss()
            return True
        return False

    keys.connect("key-pressed", on_key)
    window.add_controller(keys)

    window.present()
    return window


class ColorButton(Gtk.Button):
    """A button showing a colour, which opens the picker above when pressed.

    Stands in for ``Gtk.ColorDialogButton``, whose swatch is fine and whose
    dialog is the problem: the button owns its ``Gtk.ColorDialog`` privately
    and there is no way to reach past it. ``get_rgba``/``set_rgba`` keep the
    names the button it replaces used, so the calling page reads the same.

    Emits ``color-set`` when the *user* picks a colour, and never for one put
    there by :meth:`set_rgba` -- loading a profile into a page must not look
    like the user choosing a colour, which would push it at the keyboard.
    """

    __gtype_name__ = "RogColorButton"

    __gsignals__ = {
        "color-set": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, title="Colour", **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._rgba = Gdk.RGBA()
        self._rgba.red = self._rgba.green = self._rgba.blue = 0.0
        self._rgba.alpha = 1.0

        self._swatch = Gtk.DrawingArea()
        self._swatch.set_content_width(SWATCH_WIDTH)
        self._swatch.set_content_height(SWATCH_HEIGHT)
        self._swatch.set_draw_func(self._draw_swatch)
        self.set_child(self._swatch)
        self.connect("clicked", self._on_clicked)

    # -- value ---------------------------------------------------------------

    def get_rgba(self):
        """A copy, so a caller cannot mutate what the button is showing."""
        return self._rgba.copy()

    def set_rgba(self, rgba):
        if rgba is None:
            return
        self._rgba = rgba.copy()
        self._swatch.queue_draw()

    # -- drawing -------------------------------------------------------------

    def _draw_swatch(self, area, cr, width, height):
        _rounded_rect(cr, 0.5, 0.5, width - 1, height - 1, SWATCH_RADIUS)
        cr.set_source_rgb(self._rgba.red, self._rgba.green, self._rgba.blue)
        cr.fill_preserve()
        edge = area.get_color()
        cr.set_source_rgba(edge.red, edge.green, edge.blue,
                           SWATCH_BORDER_ALPHA)
        cr.set_line_width(1.0)
        cr.stroke()

    # -- picking -------------------------------------------------------------

    def _on_clicked(self, _button):
        root = self.get_root()
        parent = root if isinstance(root, Gtk.Window) else None
        present_color_picker(parent, self._rgba, self._on_picked, self._title)

    def _on_picked(self, rgba):
        self.set_rgba(rgba)
        self.emit("color-set")
