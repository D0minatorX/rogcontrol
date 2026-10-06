"""ASUS hardware bindings and opt-in software Fn Lock settings."""
from gi.repository import Adw, Gtk, GLib
from .. import config, hotkeys, fnlock


class KeyboardBindingsControls(Adw.PreferencesGroup):
    def __init__(self, window):
        super().__init__(title='Key bindings and Fn Lock', description=(
            'Quit G-Helper and disable overlapping desktop shortcuts before enabling. '
            'Works through ASUS input devices on X11 and Wayland. '
            'Actions pause when your desktop session is locked or inactive.'))
        self.window = window
        self._loading = False
        self._buttons_available = self._fn_available = False
        self._timer = None
        self.enabled = Adw.SwitchRow(title='Enable ASUS button assignments',
            subtitle='ROG, Aura and Performance buttons; normal typing is unchanged.')
        self.enabled.connect('notify::active', self._changed, 'key_bindings')
        self.add(self.enabled)
        self.buttons = {}
        self.action_ids = list(hotkeys.ACTIONS)
        for key, title in hotkeys.BUTTONS.items():
            row = Adw.ComboRow(title=title, model=Gtk.StringList.new(list(hotkeys.ACTIONS.values())))
            row.connect('notify::selected', self._changed, 'key_bindings')
            self.buttons[key] = row
            self.add(row)
        self.fn_enabled = Adw.SwitchRow(title='Enable software Fn Lock', subtitle=(
            'ASUS 19b6 built-in keyboard only. Re-emits keyboard input through a virtual device.'))
        self.fn_enabled.connect('notify::active', self._changed, 'fn_lock')
        self.add(self.fn_enabled)
        self.media = Adw.SwitchRow(title='Media actions on F1–F12', subtitle=(
            'Super+F2 toggles this. Ctrl/Alt/Shift/Super+F-key shortcuts keep their normal behavior. '
            'The physical Fn modifier remains firmware-controlled.'))
        self.media.connect('notify::active', self._changed, 'fn_lock')
        self.add(self.media)
        self.mapping_group = Adw.ExpanderRow(title='F1–F12 media actions')
        self.add(self.mapping_group)
        self.target_ids = list(fnlock.TARGETS)
        self.maps = {}
        for number, code in enumerate(fnlock.DEFAULT_MAP, 1):
            row = Adw.ComboRow(title=f'F{number}', model=Gtk.StringList.new(list(fnlock.TARGETS.values())))
            row.connect('notify::selected', self._changed, 'fn_lock')
            self.mapping_group.add_row(row)
            self.maps[code] = row
        self.status = Adw.ActionRow(title='Checking input support…')
        self.recheck = Gtk.Button(label='Recheck', valign=Gtk.Align.CENTER)
        self.recheck.connect('clicked', self._check)
        self.status.add_suffix(self.recheck)
        self.add(self.status)
        self.runtime = Adw.ActionRow(title='Background listener', subtitle='Enable a feature to start listening.')
        self.add(self.runtime)
        self.connect('map', self._mapped)
        self.connect('unmap', self._unmapped)
        self.reload()
        self._check()

    def _check(self, *_args):
        self.recheck.set_sensitive(False)
        self.window.apply_async(hotkeys.support_status, self._support_done)

    def _support_done(self, result, error):
        self._buttons_available, self._fn_available, message = (
            (False, False, str(error)) if error else result)
        self.recheck.set_sensitive(True)
        self.status.set_title('Input support available' if self._buttons_available else 'Input support unavailable')
        self.status.set_subtitle(message)
        self._sensitivity()

    def _sensitivity(self):
        self.enabled.set_sensitive(self._buttons_available or self.enabled.get_active())
        self.fn_enabled.set_sensitive(self._fn_available or self.fn_enabled.get_active())
        for row in self.buttons.values():
            row.set_sensitive(self._buttons_available and self.enabled.get_active())
        self.media.set_sensitive(self._fn_available and self.fn_enabled.get_active())
        self.mapping_group.set_sensitive(self._fn_available and self.fn_enabled.get_active())

    def reload(self):
        self._loading = True
        try:
            buttons = hotkeys.preferences(self.window.config)
            fn = fnlock.preferences(self.window.config)
            self.enabled.set_active(buttons['enabled'])
            self.fn_enabled.set_active(fn['enabled'])
            self.media.set_active(fn['media'])
            for key, row in self.buttons.items():
                row.set_selected(self.action_ids.index(buttons[key]))
            for code, row in self.maps.items():
                row.set_selected(self.target_ids.index(fn['map'][str(code)]))
        finally:
            self._loading = False
        self._sensitivity()

    def _changed(self, _row, _param, section):
        if self._loading:
            return
        getter = hotkeys.preferences if section == 'key_bindings' else fnlock.preferences
        if _row in (self.enabled, self.fn_enabled):
            field, value = 'enabled', _row.get_active()
        elif _row is self.media:
            field, value = 'media', _row.get_active()
        elif section == 'key_bindings':
            field = next(key for key, row in self.buttons.items() if row is _row)
            value = self.action_ids[_row.get_selected()]
        else:
            field = str(next(code for code, row in self.maps.items() if row is _row))
            value = self.target_ids[_row.get_selected()]
        is_map = section == 'fn_lock' and field not in ('enabled', 'media')
        current = getter(self.window.config)
        if (current['map'][field] if is_map else current[field]) == value:
            return
        saved = {}
        def update(cfg):
            desired = getter(cfg)
            if is_map:
                desired['map'][field] = value
            else:
                desired[field] = value
            cfg[section] = desired
            saved.update(desired)
        try:
            config.update_config(update)
        except OSError as error:
            self.window.toast(f'Key bindings could not be saved: {error}')
            self.reload()
            return
        self.window.config[section] = saved
        self.reload()
        self.runtime.set_subtitle('Saved; waiting for the background listener.')

    def _mapped(self, *_args):
        if self._timer is None:
            self._timer = GLib.timeout_add_seconds(2, self._refresh)
        self._refresh()

    def _unmapped(self, *_args):
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    def _refresh(self):
        self.runtime.set_subtitle(hotkeys.runtime_status())
        # A keyboard toggle changes this value outside the GTK process.
        try:
            latest = config.load_config()
            fn = fnlock.preferences(latest)
            self._loading = True
            self.media.set_active(fn['media'])
            self.window.config['fn_lock'] = latest.get('fn_lock')
        finally:
            self._loading = False
        return GLib.SOURCE_CONTINUE
