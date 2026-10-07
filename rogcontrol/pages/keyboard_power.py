"""Explicit opt-in power-state lighting preferences; no fictitious readback."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk
from .. import config, keyboard_power


class KeyboardPowerControls(Adw.PreferencesGroup):
    def __init__(self, window):
        super().__init__(title='Lighting power states', description=(
            'Battery uses your current brightness and effect instead of Awake. '
            'Turning management off keeps the last settings.'))
        self.window = window
        self._loading = False
        self._busy = False
        self._supported = False
        self.enabled = Adw.SwitchRow(title='Manage lighting power states')
        self.enabled.connect('notify::active', self._changed)
        self.add(self.enabled)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12,
                        margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.checks = {}
        columns = [('awake', 'Awake\nAC/USB-C'), ('boot', 'Boot'),
                   ('sleep', 'Sleep'), ('shutdown', 'Shutdown'), ('battery', 'Battery')]
        for column, (key, label) in enumerate(columns, 1):
            heading = Gtk.Label(label=label, justify=Gtk.Justification.CENTER)
            heading.add_css_class('dim-label')
            grid.attach(heading, column, 0, 1, 1)
        for row, zone in enumerate(keyboard_power.ZONES, 1):
            grid.attach(Gtk.Label(label=zone.title(), xalign=0, hexpand=True), 0, row, 1, 1)
            for column, (key, label) in enumerate(columns, 1):
                check = Gtk.CheckButton(halign=Gtk.Align.CENTER)
                check.set_tooltip_text(f'{zone.title()}: {label.replace(chr(10), " ")}')
                check.update_property([Gtk.AccessibleProperty.LABEL],
                                      [f'{zone.title()} {key}'])
                check.connect('toggled', self._changed)
                self.checks[zone, key] = check
                grid.attach(check, column, row, 1, 1)
        self.add(grid)
        self.status = Adw.ActionRow(title='Checking keyboard power-zone support…')
        self.recheck = Gtk.Button(label='Recheck', valign=Gtk.Align.CENTER)
        self.recheck.connect('clicked', self._check_support)
        self.status.add_suffix(self.recheck)
        self.add(self.status)
        self.reload()
        self._check_support()

    def _check_support(self, *_args):
        self.recheck.set_sensitive(False)
        self.window.apply_async(keyboard_power.supported, self._support_done)

    def _support_done(self, supported, error):
        self._supported = error is None and bool(supported)
        self.recheck.set_sensitive(True)
        self.status.set_title('Keyboard and Lightbar detected' if self._supported
                              else 'Power-zone controls unavailable')
        self.status.set_subtitle('G614PR controller supported; firmware readback is unavailable.'
                                 if self._supported else
                                 'Currently supported: ASUS G614PR with the 19b6 keyboard controller.')
        self._sensitivity()

    def _sensitivity(self):
        self.enabled.set_sensitive(not self._busy and
                                   (self._supported or self.enabled.get_active()))
        for check in self.checks.values():
            check.set_sensitive(not self._busy and self._supported and self.enabled.get_active())

    def reload(self):
        self._loading = True
        try:
            desired = keyboard_power.preferences(self.window.config)
            self.enabled.set_active(desired['enabled'])
            for (zone, state), check in self.checks.items():
                check.set_active(desired[zone][state])
        finally:
            self._loading = False
        self._sensitivity()

    def _changed(self, *_args):
        if self._loading or self._busy:
            return
        desired = {'enabled': self.enabled.get_active()}
        for zone in keyboard_power.ZONES:
            desired[zone] = {state: self.checks[zone, state].get_active()
                             for state in keyboard_power.STATES}
        try:
            config.update_config(lambda cfg: cfg.update(keyboard_power=desired))
        except OSError as error:
            self.window.toast(f'Lighting settings could not be saved: {error}')
            self.reload()
            return
        self.window.config['keyboard_power'] = desired
        if not desired['enabled']:
            self.status.set_subtitle('Management off; last hardware settings left in place.')
            self._sensitivity()
            return
        self._busy = True
        self._sensitivity()
        self.window.apply_async(keyboard_power.apply_saved, self._applied)

    def _applied(self, result, error):
        self._busy = False
        self._sensitivity()
        ok, message = (False, str(error)) if error is not None else result
        self.status.set_subtitle(message if ok else f'Saved; apply pending: {message}')
        if not ok:
            self.window.toast(f'Lighting power settings pending: {message}')
