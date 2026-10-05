"""System page behavior for Cardwire."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from .. import hardware
from .system_base import SystemPageBase, UPDATE_AUTO_INTERVALS_S

CARDWIRE_SUBTITLE = (
    "The daemon that applies Integrated, Hybrid and Smart GPU policies live. "
    "The picker itself is on the GPU page."
)

CARDWIRE_ABSENT = (
    "Cardwire is not installed, so GPU access mode cannot be read or "
    "changed. Install Cardwire and enable its cardwired service."
)

CARDWIRE_SILENT = (
    "Cardwire is installed but cardwired is not answering, so the mode "
    "cannot be read and nothing can be switched. Check it with "
    "systemctl status cardwired."
)

CARDWIRE_OK = "The GPU page can switch modes live without logging out."

CARDWIRE_STOPPED = (
    "Cardwire is installed but its cardwired service is not running, so "
    "the mode cannot be read and nothing can be switched. Enabling it starts "
    "it now and brings it back at every boot."
)

CARDWIRE_ENABLE_SUBTITLE = (
    "Runs systemctl enable --now cardwired. The GPU access picker on the "
    "GPU page needs this daemon; without it the mode can be neither read nor "
    "changed."
)

CARDWIRE_ENABLE_TOOLTIP = (
    "Some distributions install Cardwire without switching its service "
    "on. This turns it on and starts it, and it stays on across reboots."
)

# The one switch on this page that does not take effect when it is moved, so
# the row says which way round it is before it is touched rather than only
# afterwards in a toast.
PSR_SUBTITLE = (
    "Saves battery on the internal panel. Turn it off if the machine freezes "
    "after login while the panel is driven by AMD graphics. Takes effect at "
    "the next boot")

PSR_TOOLTIP = (
    "Panel self-refresh lets the display controller stop sending frames to a "
    "still screen and lets the panel redraw itself from its own memory, which "
    "saves power while nothing is moving.\n\n"
    "Turn it off if the machine freezes a few seconds after the login screen "
    "while the built-in panel is driven by AMD graphics. Some kernel versions "
    "crash in the panel self-refresh code on that path. Cardwire changes GPU "
    "access policy, not the physical display MUX.\n\n"
    "This one is not an ASUS firmware knob like the two above: it is a kernel "
    "boot parameter, so it is written into the bootloader's configuration and "
    "only takes effect after a reboot. The old configuration is backed up "
    "first, and put back automatically if any part of the change fails."
)


class SystemPage(SystemPageBase):
    """Cardwire live GPU access policy."""

    psr_subtitle = PSR_SUBTITLE
    psr_tooltip = PSR_TOOLTIP
    graphics_package = "Cardwire"
    graphics_capability = "cardwire"

    def _init_backend(self):
        self._cardwire_busy = False

    def _build_graphics_daemon(self):
        self._build_cardwire()

    def _build_cardwire(self):
        """Whether the daemon the GPU access picker needs is answering.

        The picker itself is on the GPU page. This row is here because it is
        the same question as the asusd row below it -- is the daemon this app
        depends on present and talking -- and because when the answer is no,
        the GPU page's picker is greyed out and the reason belongs somewhere
        a user looking for "why can I not switch" will find it."""
        group = Adw.PreferencesGroup(title="GPU access daemon")
        self.add(group)
        self.cardwire_row, self.cardwire_value = self._value_row(
            group, "cardwired", CARDWIRE_SUBTITLE, strong=True)

        # Only ever shown when there is something to do with it: the package
        # is here and the daemon is not running. Offering "Enable" on a
        # machine with no Cardwire would be a button that cannot work, and
        # offering it while the daemon already answers would be a button that
        # does nothing.
        self.cardwire_enable_row = Adw.ActionRow(
            title="Enable and start cardwired",
            subtitle=CARDWIRE_ENABLE_SUBTITLE)
        self.cardwire_enable_row.set_subtitle_lines(0)
        self.cardwire_enable_row.set_tooltip_text(CARDWIRE_ENABLE_TOOLTIP)
        self.cardwire_enable_button = Gtk.Button(label="Enable")
        self.cardwire_enable_button.set_valign(Gtk.Align.CENTER)
        self.cardwire_enable_button.connect("clicked",
                                            self._on_cardwire_enable)
        self.cardwire_enable_row.add_suffix(self.cardwire_enable_button)
        self.cardwire_enable_row.set_activatable_widget(
            self.cardwire_enable_button)
        self.cardwire_enable_row.set_visible(False)
        group.add(self.cardwire_enable_row)

    def _sample_graphics(self):
        """Worker thread: a handful of subprocesses, no widgets."""
        # Sampled every cycle, like the asusd state below: cardwired can be
        # started or stopped under a running window, and a row latched on the
        # answer it gave at startup would be wrong for the rest of the
        # session.
        gpu_mode = (hardware.read_gpu_mode()
                    if self.caps.get("cardwire") else None)
        # Asked ONLY when the mode did not come back. It is three more
        # subprocesses per tick, and the one thing it is used for -- telling
        # "installed but switched off" apart from "installed and broken" --
        # cannot arise while the daemon is answering.
        cardwired = (hardware.read_cardwired_state()
                     if self.caps.get("cardwire") and gpu_mode is None
                     else None)
        return {
            "gpu_mode": gpu_mode,
            "cardwired": cardwired,
        }

    def _render_graphics(self, data):
        self._render_cardwire(data.get("gpu_mode"),
                              data.get("gpu_mode_error"),
                              data.get("cardwired"))

    def _render_cardwire(self, mode, error, state=None):
        """Four states, said apart: absent, installed but switched off,
        present but silent, working.

        "Not installed", "installed but not answering" and "installed and
        simply not switched on" are three different problems with three
        different fixes, and collapsing them into one dash is what makes a
        greyed-out picker look like a missing feature. Only the third of them
        is something this window can fix by pressing a button, so only that
        one shows the button."""
        if not self.caps.get("cardwire"):
            self.cardwire_enable_row.set_visible(False)
            self.cardwire_value.set_text("not installed")
            self.cardwire_row.set_subtitle(CARDWIRE_ABSENT)
            self._cardwire_css("warning")
            return
        if not self.caps.get("cardwire_wayland", True):
            self.cardwire_enable_row.set_visible(False)
            self.cardwire_value.set_text("X11 session")
            self.cardwire_row.set_subtitle(
                "Installed, but Cardwire mode switching requires Wayland. "
                "Log out and choose a Wayland desktop session.")
            self._cardwire_css("warning")
            return
        if mode is None:
            # The daemon is not answering. Whether that is because it was
            # never switched on or because it is broken decides both what
            # the row says and whether there is anything to press.
            # has_unit, not installed: without a unit file there is nothing
            # for systemctl to enable, and a button whose command is certain
            # to fail is worse than no button. That case -- the binary built
            # by hand, no unit installed -- falls through to "not answering",
            # which is what it is.
            stopped = bool(state and state.get("has_unit")
                           and not state.get("active"))
            self.cardwire_enable_row.set_visible(stopped)
            self.cardwire_enable_button.set_sensitive(
                stopped and not self._cardwire_busy)
            self.cardwire_value.set_text(
                "stopped" if stopped else "not answering")
            self.cardwire_row.set_subtitle(
                (CARDWIRE_STOPPED if stopped else CARDWIRE_SILENT)
                + (f"\n\n{error}" if error else ""))
            self._cardwire_css("warning")
            return
        self.cardwire_enable_row.set_visible(False)
        self.cardwire_value.set_text("running")
        self.cardwire_row.set_subtitle(
            f"Answering, and reporting {mode}. " + CARDWIRE_OK)
        self._cardwire_css("success")

    def _cardwire_css(self, name):
        for css in ("success", "warning"):
            self.cardwire_value.remove_css_class(css)
        self.cardwire_value.add_css_class(name)

    def _on_cardwire_enable(self, _button):
        """Switch cardwired on, through the helper.

        Off the main loop: this is systemctl enable --now, which does not
        return until the daemon has actually started."""
        if self._cardwire_busy:
            return
        self._cardwire_busy = True
        self.cardwire_enable_button.set_sensitive(False)
        self.window.toast("Enabling cardwired…")
        self.window.apply_async(hardware.set_cardwired_running,
                                self._on_cardwire_enabled)

    def _on_cardwire_enabled(self, result, error):
        self._cardwire_busy = False
        ok, message = (False, str(error)) if error is not None else result
        if ok:
            self.window.toast("cardwired enabled and started — the GPU "
                              "access picker can switch now.")
            # The GPU page's picker was built against a daemon that was not
            # answering; it has to be rebuilt to become usable, which is what
            # the profile-switch path does after it moves the hardware.
            self.window.reload_pages()
        else:
            self.window.toast(f"Could not enable cardwired: {message}")
        # Ask systemd rather than assuming the button worked.
        self._refresh_now()
