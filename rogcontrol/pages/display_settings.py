"""Opt-in desktop display policy controls shared by Quick Access."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from .. import config as config_mod
from .. import display_refresh


class DisplayRefreshControls(Adw.PreferencesGroup):
    def __init__(self, window):
        super().__init__(title='Automatic display refresh')
        self.window = window
        self._loading = False
        self.row = Adw.SwitchRow(
            title='Switch internal display refresh rate',
            subtitle='Highest supported on AC or USB-C; lowest on battery, at the current resolution')
        self.add(self.row)
        self.status = Adw.ActionRow(title='Desktop availability')
        self.add(self.status)
        recheck = Gtk.Button(label='Recheck', valign=Gtk.Align.CENTER)
        recheck.connect('clicked', lambda *_args: self.reload())
        self.status.add_suffix(recheck)
        self.reload()
        self.row.connect('notify::active', self._changed)

    def reload(self):
        self._loading = True
        try:
            enabled = getattr(self.window, 'config', {}).get(display_refresh.CONFIG_KEY) is True
            self.row.set_active(enabled)
            supported, reason = display_refresh.availability()
            # Preserve a saved opt-in on another desktop, but allow turning it off.
            self.row.set_sensitive(supported or enabled)
            self.status.set_subtitle(reason)
        finally:
            self._loading = False

    def _changed(self, row, _param):
        if self._loading:
            return
        enabled = row.get_active()
        config_mod.update_config(lambda cfg: cfg.update(
            {display_refresh.CONFIG_KEY: enabled}))
        self.window.config[display_refresh.CONFIG_KEY] = enabled
        self.reload()
