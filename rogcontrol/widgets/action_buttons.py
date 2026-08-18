"""The per-page action buttons that live in the window's header bar.

These have moved twice, and the reasons are worth keeping.

They began as ``AdwActionRow``s at the *bottom* of each page, one row per
button, each with a title and a subtitle explaining what it did. The prose
was noise -- a button labelled Apply on a page of settings needs no sentence
saying it applies them -- and the position was worse: at the bottom of a page
that scrolls, the buttons were off screen exactly when a control had just
been moved.

Moving them to the top of the page fixed the scrolling but cost a full-width
card of empty space above every page. So they are in the header bar now,
beside the page title, where they take no page height at all and stay put
however far the page is scrolled.

The header is built once and the pages outlive any single visit, so each page
builds its own box and the window shows the one belonging to the visible page
-- see ``MainWindow._show_page_actions``. Re-parenting single buttons between
pages would work too, but a widget has one parent and swapping it on every
navigation is a good way to end up with a button on the wrong page.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

DASH = "—"


def make_action_buttons(specs):
    """A header box holding one button per spec, and the buttons themselves.

    ``specs`` is a sequence of ``(label, callback, tooltip, suggested)``.
    Returns ``(box, [button, ...])`` in the same order, so a caller can name
    them however it likes -- the fans page's pair is Calibrate and Apply, not
    Revert and Apply.

    The suggested action goes last, on the right of the group, which is where
    a confirming action sits everywhere else in GNOME.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    buttons = []
    for label, callback, tooltip, suggested in specs:
        button = Gtk.Button(label=label)
        button.set_valign(Gtk.Align.CENTER)
        if suggested:
            button.add_css_class("suggested-action")
        if tooltip:
            button.set_tooltip_text(tooltip)
        button.connect("clicked", callback)
        box.append(button)
        buttons.append(button)
    return box, buttons


def apply_revert_buttons(on_apply, on_revert, apply_tooltip=None,
                         revert_tooltip=None):
    """The ordinary pair: Revert then Apply. Returns ``(box, apply, revert)``."""
    box, (revert, apply_) = make_action_buttons((
        ("Revert", on_revert, revert_tooltip, False),
        ("Apply", on_apply, apply_tooltip, True),
    ))
    return box, apply_, revert
