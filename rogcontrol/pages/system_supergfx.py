"""System page behavior for Supergfx."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from .. import hardware
from .system_base import SystemPageBase, UPDATE_AUTO_INTERVALS_S

SUPERGFX_SUBTITLE = (
    "The daemon that switches between integrated, hybrid and dGPU graphics. "
    "The picker itself is on the GPU page."
)

SUPERGFX_ABSENT = (
    "supergfxctl is not installed, so the graphics mode cannot be read or "
    "changed. Install supergfxctl and enable its supergfxd service."
)

SUPERGFX_SILENT = (
    "supergfxctl is installed but supergfxd is not answering, so the mode "
    "cannot be read and nothing can be switched. Check it with "
    "systemctl status supergfxd."
)

SUPERGFX_OK = "The picker on the GPU page can switch modes."

SUPERGFX_STOPPED = (
    "supergfxctl is installed but its supergfxd service is not running, so "
    "the mode cannot be read and nothing can be switched. Enabling it starts "
    "it now and brings it back at every boot."
)

SUPERGFX_ENABLE_SUBTITLE = (
    "Runs systemctl enable --now supergfxd. The graphics-mode picker on the "
    "GPU page needs this daemon; without it the mode can be neither read nor "
    "changed."
)

SUPERGFX_ENABLE_TOOLTIP = (
    "Some distributions install supergfxctl without switching its service "
    "on. This turns it on and starts it, and it stays on across reboots."
)

# The one switch on this page that does not take effect when it is moved, so
# the row says which way round it is before it is touched rather than only
# afterwards in a toast.
PSR_SUBTITLE = (
    "Saves battery on the internal panel. Turn it off if the machine freezes "
    "after login in Hybrid or Integrated graphics. Takes effect at the next "
    "boot")

PSR_TOOLTIP = (
    "Panel self-refresh lets the display controller stop sending frames to a "
    "still screen and lets the panel redraw itself from its own memory, which "
    "saves power while nothing is moving.\n\n"
    "Turn it off if switching to Hybrid or Integrated graphics leaves the "
    "machine frozen a few seconds after the login screen. Those are the modes "
    "where the built-in screen is driven by the AMD graphics rather than the "
    "NVIDIA card, and some kernel versions crash in the panel self-refresh "
    "code when it is. In AsusMuxDgpu the screen is on the NVIDIA card, that "
    "code never runs, and this setting changes nothing.\n\n"
    "This one is not an ASUS firmware knob like the two above: it is a kernel "
    "boot parameter, so it is written into the bootloader's configuration and "
    "only takes effect after a reboot. The old configuration is backed up "
    "first, and put back automatically if any part of the change fails."
)


class SystemPage(SystemPageBase):
    """Supergfx graphics mode and session transitions."""

    psr_subtitle = PSR_SUBTITLE
    psr_tooltip = PSR_TOOLTIP
    graphics_package = "supergfxctl"
    graphics_capability = "supergfxctl"

    def _init_backend(self):
        self._supergfx_busy = False

    def _build_graphics_daemon(self):
        self._build_supergfx()

    def _build_supergfx(self):
        """Whether the daemon the graphics-mode picker needs is answering.

        The picker itself is on the GPU page. This row is here because it is
        the same question as the asusd row below it -- is the daemon this app
        depends on present and talking -- and because when the answer is no,
        the GPU page's picker is greyed out and the reason belongs somewhere
        a user looking for "why can I not switch" will find it."""
        group = Adw.PreferencesGroup(title="Graphics mode daemon")
        self.add(group)
        self.supergfx_row, self.supergfx_value = self._value_row(
            group, "supergfxd", SUPERGFX_SUBTITLE, strong=True)

        # Only ever shown when there is something to do with it: the package
        # is here and the daemon is not running. Offering "Enable" on a
        # machine with no supergfxctl would be a button that cannot work, and
        # offering it while the daemon already answers would be a button that
        # does nothing.
        self.supergfx_enable_row = Adw.ActionRow(
            title="Enable and start supergfxd",
            subtitle=SUPERGFX_ENABLE_SUBTITLE)
        self.supergfx_enable_row.set_subtitle_lines(0)
        self.supergfx_enable_row.set_tooltip_text(SUPERGFX_ENABLE_TOOLTIP)
        self.supergfx_enable_button = Gtk.Button(label="Enable")
        self.supergfx_enable_button.set_valign(Gtk.Align.CENTER)
        self.supergfx_enable_button.connect("clicked",
                                            self._on_supergfx_enable)
        self.supergfx_enable_row.add_suffix(self.supergfx_enable_button)
        self.supergfx_enable_row.set_activatable_widget(
            self.supergfx_enable_button)
        self.supergfx_enable_row.set_visible(False)
        group.add(self.supergfx_enable_row)

    def _sample_graphics(self):
        """Worker thread: a handful of subprocesses, no widgets."""
        # Sampled every cycle, like the asusd state below: supergfxd can be
        # started or stopped under a running window, and a row latched on the
        # answer it gave at startup would be wrong for the rest of the
        # session.
        gpu_mode = (hardware.read_gpu_mode()
                    if self.caps.get("supergfxctl") else None)
        # Asked ONLY when the mode did not come back. It is three more
        # subprocesses per tick, and the one thing it is used for -- telling
        # "installed but switched off" apart from "installed and broken" --
        # cannot arise while the daemon is answering.
        supergfxd = (hardware.read_supergfxd_state()
                     if self.caps.get("supergfxctl") and gpu_mode is None
                     else None)
        return {
            "gpu_mode": gpu_mode,
            "supergfxd": supergfxd,
        }

    def _render_graphics(self, data):
        self._render_supergfx(data.get("gpu_mode"),
                              data.get("gpu_mode_error"),
                              data.get("supergfxd"))

    def _render_supergfx(self, mode, error, state=None):
        """Four states, said apart: absent, installed but switched off,
        present but silent, working.

        "Not installed", "installed but not answering" and "installed and
        simply not switched on" are three different problems with three
        different fixes, and collapsing them into one dash is what makes a
        greyed-out picker look like a missing feature. Only the third of them
        is something this window can fix by pressing a button, so only that
        one shows the button."""
        if not self.caps.get("supergfxctl"):
            self.supergfx_enable_row.set_visible(False)
            self.supergfx_value.set_text("not installed")
            self.supergfx_row.set_subtitle(SUPERGFX_ABSENT)
            self._supergfx_css("warning")
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
            self.supergfx_enable_row.set_visible(stopped)
            self.supergfx_enable_button.set_sensitive(
                stopped and not self._supergfx_busy)
            self.supergfx_value.set_text(
                "stopped" if stopped else "not answering")
            self.supergfx_row.set_subtitle(
                (SUPERGFX_STOPPED if stopped else SUPERGFX_SILENT)
                + (f"\n\n{error}" if error else ""))
            self._supergfx_css("warning")
            return
        self.supergfx_enable_row.set_visible(False)
        self.supergfx_value.set_text("running")
        self.supergfx_row.set_subtitle(
            f"Answering, and reporting {mode}. " + SUPERGFX_OK)
        self._supergfx_css("success")

    def _supergfx_css(self, name):
        for css in ("success", "warning"):
            self.supergfx_value.remove_css_class(css)
        self.supergfx_value.add_css_class(name)

    def _on_supergfx_enable(self, _button):
        """Switch supergfxd on, through the helper.

        Off the main loop: this is systemctl enable --now, which does not
        return until the daemon has actually started."""
        if self._supergfx_busy:
            return
        self._supergfx_busy = True
        self.supergfx_enable_button.set_sensitive(False)
        self.window.toast("Enabling supergfxd…")
        self.window.apply_async(hardware.set_supergfxd_running,
                                self._on_supergfx_enabled)

    def _on_supergfx_enabled(self, result, error):
        self._supergfx_busy = False
        ok, message = (False, str(error)) if error is not None else result
        if ok:
            self.window.toast("supergfxd enabled and started — the graphics "
                              "mode picker on the GPU page can switch now.")
            # The GPU page's picker was built against a daemon that was not
            # answering; it has to be rebuilt to become usable, which is what
            # the profile-switch path does after it moves the hardware.
            self.window.reload_pages()
        else:
            self.window.toast(f"Could not enable supergfxd: {message}")
        # Ask systemd rather than assuming the button worked.
        self._refresh_now()
