"""Battery refresh policy controls in Quick Access."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk

from .. import config as config_mod
from .. import display_refresh


def _rate_label(rate):
    return f'{rate:.2f}'.rstrip('0').rstrip('.') + ' Hz'


class DisplayRefreshControls(Adw.PreferencesGroup):
    def __init__(self, window):
        super().__init__(title='Display')
        self.window = window
        self._loading = False
        self._querying = False
        self._supported = False
        self._rates = []
        self._error = None
        self.row = Adw.ActionRow(
            title='Automatic refresh switching',
            subtitle='Use this rate on battery; restore the previous rate on charger')
        self.rate_dropdown = Gtk.DropDown(
            model=Gtk.StringList.new([]), valign=Gtk.Align.CENTER)
        self.rate_dropdown.set_tooltip_text('Refresh rate to use on battery')
        self.rate_dropdown.update_property(
            [Gtk.AccessibleProperty.LABEL], ['Battery refresh rate'])
        self.toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.toggle.update_property(
            [Gtk.AccessibleProperty.LABEL], ['Automatic refresh switching'])
        self.row.add_suffix(self.rate_dropdown)
        self.row.add_suffix(self.toggle)
        self.row.set_activatable_widget(self.toggle)
        self.add(self.row)
        self.status = Adw.ActionRow(title='Desktop availability')
        self.add(self.status)
        self.recheck = Gtk.Button(label='Recheck', valign=Gtk.Align.CENTER)
        self.recheck.connect('clicked', self._refresh_rates)
        self.status.add_suffix(self.recheck)
        self.reload()
        self.toggle.connect('notify::active', self._changed)
        self.rate_dropdown.connect('notify::selected', self._rate_changed)
        self.connect('map', self._refresh_rates)

    def reload(self):
        self._supported, reason = display_refresh.availability()
        if not self._supported:
            self._rates = []
            self._error = reason
        self._render_preferences()
        if self.get_mapped():
            self._refresh_rates()

    def _sync_controls(self):
        valid_selection = self.rate_dropdown.get_selected() < len(self._rates)
        self.toggle.set_sensitive(not self._querying and (
            (self._supported and valid_selection) or self.toggle.get_active()))
        self.rate_dropdown.set_sensitive(
            self._supported and bool(self._rates) and not self._querying)
        self.recheck.set_sensitive(not self._querying)
        self.status.set_visible(self._error is not None)
        if self._error is not None:
            self.status.set_subtitle(self._error)

    def _refresh_rates(self, *_args):
        if self._querying:
            return
        self._supported, reason = display_refresh.availability()
        if not self._supported:
            self._rates = []
            self._error = reason
            self._render_preferences()
            return
        self._querying = True
        self._sync_controls()
        self.window.apply_async(display_refresh.get_refresh_rates, self._rates_loaded)

    def _rates_loaded(self, result, error):
        self._querying = False
        if error is not None:
            self._rates = []
            self._error = str(error)
        else:
            self._rates, _current_rate = result
            self._error = None if self._rates else 'No supported internal display rates.'
        self._render_preferences()

    def _render_preferences(self):
        settings = getattr(self.window, 'config', {})
        self._loading = True
        try:
            self.toggle.set_active(settings.get(display_refresh.CONFIG_KEY) is True)
            saved = settings.get(display_refresh.BATTERY_RATE_KEY)
            labels = [_rate_label(rate) for rate in self._rates]
            selected = Gtk.INVALID_LIST_POSITION
            if self._rates:
                if saved is None:
                    selected = 0
                elif saved in self._rates:
                    selected = self._rates.index(saved)
                else:
                    # Keep a stored choice visible when docking or resolution
                    # changes make it unavailable; never silently replace it.
                    try:
                        label = _rate_label(float(saved))
                    except (ValueError, TypeError):
                        label = str(saved)
                    selected = len(labels)
                    labels.append(f'{label} (unavailable)')
            model = self.rate_dropdown.get_model()
            if ([model.get_string(i) for i in range(model.get_n_items())]
                    != labels):
                self.rate_dropdown.set_model(Gtk.StringList.new(labels))
            self.rate_dropdown.set_selected(selected)
        finally:
            self._loading = False
        self._sync_controls()

    def _save_preferences(self, changes):
        try:
            config_mod.update_config(lambda cfg: cfg.update(changes))
        except (OSError, ValueError) as error:
            self.window.toast(f'Could not save automatic refresh switching: {error}')
            self._render_preferences()
        else:
            self.window.config.update(changes)
            self._sync_controls()
            # Gtk.DropDown is still notifying its selection here. Replacing
            # its model (to remove an unavailable choice) during that signal
            # can invalidate the selection object used by GTK's own handler.
            if self.rate_dropdown.get_model().get_n_items() != len(self._rates):
                GLib.idle_add(self._render_preferences)

    def _rate_changed(self, dropdown, _param):
        if self._loading or self._querying:
            return
        selected = dropdown.get_selected()
        if selected < len(self._rates):
            # The background policy applies this only on battery. Changing the
            # preference must not alter the current charger refresh rate.
            rate = self._rates[selected]
            if self.window.config.get(display_refresh.BATTERY_RATE_KEY) != rate:
                self._save_preferences({display_refresh.BATTERY_RATE_KEY: rate})

    def _changed(self, toggle, _param):
        if self._loading:
            return
        enabled = toggle.get_active()
        if enabled == (self.window.config.get(display_refresh.CONFIG_KEY) is True):
            return
        changes = {display_refresh.CONFIG_KEY: enabled}
        if enabled:
            selected = self.rate_dropdown.get_selected()
            if selected >= len(self._rates):
                self._render_preferences()
                return
            changes[display_refresh.BATTERY_RATE_KEY] = self._rates[selected]
        self._save_preferences(changes)
