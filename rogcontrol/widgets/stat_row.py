"""Two live readings side by side on one row.

The CPU and GPU pages each show a temperature and the speed of the fan
answering it. They were an ``AdwActionRow`` each, stacked, which spent two
full rows -- and on the CPU page a two-line subtitle as well -- on two
numbers of four characters. Side by side they take one row and read as the
pair they are: the fan speed only means anything next to the temperature
that caused it.

Each cell is the label above the value rather than side by side within the
cell, because at half the window's width a title and a right-aligned value
collide the moment the window is narrowed. Stacked, the cell shrinks to the
width of its longest line and neither ever truncates.

The explanation that used to be a subtitle is a tooltip here. It is
description, not status -- what k10temp Tctl is does not change, and it was
costing a permanent two lines above the controls the page exists for. The one
thing that genuinely is status, a reading this machine cannot take at all,
stays visible as the value itself (see ``StatCell.set_note``).
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

DASH = "—"


class StatCell:
    """One reading: a dim title, and the value under it."""

    def __init__(self, title, tooltip=None):
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.box.set_hexpand(True)

        self.title_label = Gtk.Label(label=title)
        self.title_label.set_xalign(0.0)
        self.title_label.add_css_class("dim-label")
        self.title_label.add_css_class("caption")
        self.box.append(self.title_label)

        self.value = Gtk.Label(label=DASH)
        self.value.set_xalign(0.0)
        # "numeric" is tabular figures, so a value changing width does not
        # shuffle the column sideways twice a second. "heading" rather than a
        # title size: the reading should still be the thing the eye lands on,
        # but this row is a readout above the controls the page is for, and a
        # display-sized number made it the loudest thing on the page.
        self.value.add_css_class("numeric")
        self.value.add_css_class("heading")
        self.box.append(self.value)

        self._tooltip = tooltip or ""
        if self._tooltip:
            self.box.set_tooltip_text(self._tooltip)

    def set_note(self, text):
        """Say this machine cannot take this reading.

        The note goes in front of the description rather than replacing it:
        why the value is a dash is the more useful half, but what the
        reading would have been is still worth having."""
        self.value.add_css_class("dim-label")
        self.box.set_tooltip_text(
            text + (f"\n\n{self._tooltip}" if self._tooltip else ""))


def build_stat_row(group, cells):
    """Add one row holding every cell, evenly spread. Returns the row.

    ``cells`` is a sequence of StatCell. Homogeneous, so two readings each
    get half the width and the second does not start at a different place on
    the CPU page than on the GPU page.
    """
    row = Adw.PreferencesRow()
    # Neither clickable nor focusable: there is nothing to activate, and a
    # focus stop on a pair of labels is a Tab press that appears to do
    # nothing.
    row.set_activatable(False)
    row.set_focusable(False)

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
    box.set_homogeneous(True)
    # Tighter than a normal row's padding: two short numbers do not need the
    # height of a row built to hold a title and two lines of subtitle.
    box.set_margin_top(6)
    box.set_margin_bottom(6)
    box.set_margin_start(12)
    box.set_margin_end(12)
    for cell in cells:
        box.append(cell.box)
    row.set_child(box)
    group.add(row)
    return row
