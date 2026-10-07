"""Internal display controls in Quick Access."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from .. import config as config_mod
from .. import display_refresh


class DisplayRefreshControls(Adw.PreferencesGroup):
    def __init__(self, window):
        super().__init__(title='Display')
        self.window = window
        self._loading = False
        self._querying = False
        self._applying = False
        self._supported = False
        self._rates = []
        self._current_rate = None
        self.rate_row = Adw.ComboRow(
            title='Refresh rate',
            subtitle='Supported rates for the internal display at its current resolution')
        self.rate_row.set_model(Gtk.StringList.new([]))
        self.add(self.rate_row)
        self.row = Adw.SwitchRow(
            title='Automatic refresh switching',
            subtitle='Highest supported on AC or USB-C; lowest on battery, at the current resolution')
        self.add(self.row)
        self.status = Adw.ActionRow(title='Desktop availability')
        self.add(self.status)
        self.recheck = Gtk.Button(label='Recheck', valign=Gtk.Align.CENTER)
        self.recheck.connect('clicked', self._refresh_rates)
        self.status.add_suffix(self.recheck)
        self.reload()
        self.row.connect('notify::active', self._changed)
        self.rate_row.connect('notify::selected', self._rate_changed)
        self.connect('map', self._refresh_rates)

    def reload(self):
        self._loading = True
        try:
            enabled = getattr(self.window, 'config', {}).get(display_refresh.CONFIG_KEY) is True
            self.row.set_active(enabled)
            self._supported, reason = display_refresh.availability()
            # Preserve a saved opt-in on another desktop, but allow turning it off.
            self.status.set_subtitle(reason)
        finally:
            self._loading = False
        self._sync_controls()
        if self.get_mapped():
            self._refresh_rates()

    def _sync_controls(self):
        busy = self._querying or self._applying
        automatic = self.row.get_active()
        self.row.set_sensitive((self._supported or automatic) and not busy)
        self.recheck.set_sensitive(not busy)
        self.rate_row.set_sensitive(
            self._supported and bool(self._rates) and not automatic and not busy)
        self.rate_row.set_subtitle(
            'Turn off automatic switching to choose a rate' if automatic else
            'Supported rates for the internal display at its current resolution')

    def _refresh_rates(self, *_args):
        if self._querying or self._applying:
            return
        self._supported, reason = display_refresh.availability()
        self.status.set_subtitle(reason)
        if not self._supported:
            self._rates = []
            self._current_rate = None
            self._render_rates()
            self._sync_controls()
            return
        self._querying = True
        self._sync_controls()
        self.window.apply_async(display_refresh.get_refresh_rates, self._rates_loaded)

    def _rates_loaded(self, result, error):
        self._querying = False
        if error is not None:
            self._rates, self._current_rate = [], None
            self.status.set_subtitle(str(error))
        else:
            self._rates, self._current_rate = result
        self._render_rates()
        self._sync_controls()

    def _render_rates(self):
        self._loading = True
        try:
            self.rate_row.set_model(Gtk.StringList.new(
                [f'{rate:.2f}'.rstrip('0').rstrip('.') + ' Hz'
                 for rate in self._rates]))
            self.rate_row.set_selected(
                self._rates.index(self._current_rate)
                if self._current_rate in self._rates else Gtk.INVALID_LIST_POSITION)
        finally:
            self._loading = False

    def _rate_changed(self, row, _param):
        if self._loading or self._applying or self._querying or self.row.get_active():
            return
        selected = row.get_selected()
        if selected >= len(self._rates):
            return
        rate = self._rates[selected]
        if rate == self._current_rate:
            return
        self._applying = True
        self._sync_controls()
        self.window.apply_async(
            lambda: display_refresh.set_refresh_rate(rate),
            lambda result, error: self._rate_applied(rate, result, error))

    def _rate_applied(self, rate, result, error):
        self._applying = False
        ok, message = result if error is None else (False, str(error))
        if ok:
            self._current_rate = rate
        else:
            self.window.toast(f'Refresh rate unchanged: {message}')
        self._render_rates()
        self._sync_controls()

    def _changed(self, row, _param):
        if self._loading:
            return
        enabled = row.get_active()
        try:
            config_mod.update_config(lambda cfg: cfg.update(
                {display_refresh.CONFIG_KEY: enabled}))
        except OSError as error:
            self.window.toast(f'Could not save automatic refresh switching: {error}')
        else:
            self.window.config[display_refresh.CONFIG_KEY] = enabled
        self.reload()
