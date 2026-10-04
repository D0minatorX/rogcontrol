"""Persistent, non-scrolling actions for staged edits on tuning pages."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk


class PendingChanges(Gtk.Revealer):
    """Keep pending counts separate from operation/error banners."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, ())}
    count = GObject.Property(type=int, default=0, minimum=0)

    def __init__(self, on_apply, on_discard, scope="", discard_on_leave=False):
        super().__init__()
        self.scope = scope
        self.busy = False
        self.set_transition_duration(0)
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        container.add_css_class("pending-bar")
        self.set_child(container)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        container.append(box)
        self.label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.label.add_css_class("heading")
        box.append(self.label)
        self.discard_button = Gtk.Button(label="Discard", valign=Gtk.Align.CENTER)
        self.discard_button.set_tooltip_text("Discard unapplied changes")
        self.discard_button.connect("clicked", on_discard)
        box.append(self.discard_button)
        self.apply_button = Gtk.Button(label="Apply", valign=Gtk.Align.CENTER)
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.set_tooltip_text("Apply and save these settings")
        self.apply_button.connect("clicked", on_apply)
        box.append(self.apply_button)
        self.warning = Gtk.Label(
            label="Leaving this page discards these edits.", xalign=0, wrap=True)
        self.warning.add_css_class("caption")
        self.warning.set_visible(discard_on_leave)
        container.append(self.warning)
        self.set_state(0)

    def set_state(self, count, busy=False):
        self.count = count
        self.busy = busy
        scope = f"{self.scope} " if self.scope else ""
        self.label.set_text(f"{count} unapplied {scope}change{'s' if count != 1 else ''}")
        self.set_reveal_child(count > 0)
        self.apply_button.set_sensitive(count > 0 and not busy)
        self.discard_button.set_sensitive(count > 0 and not busy)
        self.emit("changed")

    def update(self, rows, dirty, busy=False):
        dirty = set(dirty)
        for key, row in rows.items():
            if key in dirty:
                row.add_css_class("pending-change")
            else:
                row.remove_css_class("pending-change")
        self.set_state(len(dirty), busy)
