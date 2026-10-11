"""Lighting, background input preferences and portal capture lifecycle."""
from copy import deepcopy
import threading
from PySide6.QtCore import Property, Signal, Slot, QTimer, Qt
from PySide6.QtGui import QColor
from .settings import SettingsController


class KeyboardController(SettingsController):
    detailsChanged = Signal()
    page = 'keyboard'
    ambientColors = Signal(object)
    ambientStatus = Signal(object)
    ambientToken = Signal(object)

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        self.stateChanged.connect(self.detailsChanged.emit)
        self._edits = {}
        self._preference_edits = {}
        self._colors = None
        self._last_colors = None
        self._ambient = None
        self._capture_threads = []
        self._ambient_stop = None
        self._ambient_mode = False
        self._ambient_generation = 0
        self._last_mode = None
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(300)
        self.debounce.timeout.connect(self._flush)
        self.preferenceTimer = QTimer(self)
        self.preferenceTimer.setSingleShot(True)
        self.preferenceTimer.setInterval(300)
        self.preferenceTimer.timeout.connect(self._flush_preferences)
        self.liveTimer = QTimer(self)
        self.liveTimer.setInterval(2000)
        self.liveTimer.timeout.connect(self._live)
        self.liveTimer.start()
        self.ambientColors.connect(self._on_colors, Qt.QueuedConnection)
        self.ambientStatus.connect(self._on_status, Qt.QueuedConnection)
        self.ambientToken.connect(self._on_token, Qt.QueuedConnection)
        app.configChanged.connect(self._sync_mode)
        app.runner.finished.connect(self._live_done)
        self.refresh()

    @staticmethod
    def hexcolor(color):
        return '#%02x%02x%02x' % tuple(color)

    settings = Property('QVariantMap', lambda s: dict(deepcopy(s.app.config), **s._preference_edits), notify=detailsChanged)

    lighting = Property('QVariantMap', lambda s: s._lighting(), notify=detailsChanged)

    profileColors = Property('QVariantMap', lambda s: s._profile_colors(), notify=detailsChanged)
    flashColor = Property(str, lambda s: s._flash_color(), notify=detailsChanged)

    powerPreferences = Property('QVariantMap', lambda s: s._power_preferences(), notify=detailsChanged)
    inputPreferences = Property('QVariantMap', lambda s: s._input_preferences('key_bindings'), notify=detailsChanged)
    fnPreferences = Property('QVariantMap', lambda s: s._input_preferences('fn_lock'), notify=detailsChanged)

    def _power_preferences(self):
        if self.app.demo:
            defaults = self.fixture()['power']
            saved = self.app.config.get('keyboard_power', {})
            defaults['enabled'] = saved.get('enabled') is True
            for zone in ('keyboard', 'lightbar'):
                defaults[zone].update(saved.get(zone, {}))
            return defaults
        from ... import keyboard_power
        return keyboard_power.preferences(self.app.config)

    def _input_preferences(self, section):
        if self.app.demo:
            saved = deepcopy(self.fixture()[section])
            supplied = self.app.config.get(section, {})
            saved.update(supplied)
            return saved
        from ... import hotkeys, fnlock
        return hotkeys.preferences(self.app.config) if section == 'key_bindings' else fnlock.preferences(self.app.config)

    def _flash_color(self):
        if self.app.demo:
            return self.hexcolor(self.app.config.get('charger_flash_color', [0, 255, 255]))
        from ... import kbdcolor
        return self.hexcolor(kbdcolor.charger_flash_color(self.app.config))

    def _profile_colors(self):
        if self.app.demo:
            return {name: self.hexcolor(profile.get('kbd_color', [238, 36, 69])) for name, profile in self.app.config.get('profiles', {}).items()}
        from ... import kbdcolor
        return {name: self.hexcolor(kbdcolor.profile_color(self.app.config, name)) for name in self.app.profileNames}

    def _lighting(self):
        if self.app.demo:
            raw = dict(mode='Static', r=255, g=0, b=0, r2=0, g2=0, b2=255, speed=2)
            raw.update(self.app.config.get('kbd_rgb', {}))
            color1 = (raw['r'], raw['g'], raw['b'])
            color2 = (raw['r2'], raw['g2'], raw['b2'])
        else:
            from ... import kbdcolor
            raw = deepcopy(self.app.config.get('kbd_rgb', {}))
            color1, color2 = kbdcolor.saved_color(raw), kbdcolor.saved_color(raw, '2')
        result = dict(mode=raw.get('mode', 'Static'), speed=raw.get('speed', 2),
                      brightness=self._data.get('brightness', self.app.config.get('kbd_brightness', 3)),
                      color1=self.hexcolor(color1), color2=self.hexcolor(color2))
        result.update(self._edits)
        return result

    def fixture(self):
        return dict(modes=['Static', 'Breathing', 'Pulse', 'Color Cycle', 'Rainbow', 'Gradient Static',
                           'GPU Temp Color', 'CPU Temp Color', 'Battery Level', 'Ambient', 'Profile Color'],
                    hint='Demo lighting fixture', idle='Idle detection available', idle_supported=True, dimmed=False,
                    power_supported=True, bindings_supported=True, fn_supported=True,
                    input_status='Background service owns keyboard input', runtime='',
                    bindings=[{'key': k, 'label': v} for k, v in [('rog', 'ROG / M5'), ('aura', 'Fn+F4'), ('performance', 'Fn+F5')]],
                    actions=['none', 'toggle', 'profile', 'aura', 'brightness_up', 'brightness_down', 'speed_up', 'speed_down'],
                    action_labels=['None', 'Show / hide', 'Cycle profile', 'Cycle lighting', 'Brightness up', 'Brightness down', 'Speed up', 'Speed down'],
                    fn_keys=[str(n) for n in list(range(59, 69)) + [87, 88]],
                    fn_targets=['action:none', 'action:toggle', 'action:profile'],
                    fn_labels=['Do nothing', 'Show / hide', 'Cycle profile'],
                    power={'enabled': False, 'keyboard': dict.fromkeys(['boot','awake','sleep','shutdown','battery'], True), 'lightbar': dict.fromkeys(['boot','awake','sleep','shutdown','battery'], True)},
                    key_bindings={'enabled': False, 'rog': 'toggle', 'aura': 'aura', 'performance': 'profile'},
                    fn_lock={'enabled': False, 'media': True, 'map': {}}, caps={'kbd_ambient': True, 'kbd_rgb': True, 'kbd_rgb_zones': True, 'kbd_backlight': True})

    def sample(self):
        from ... import hardware, keyboard_idle, keyboard_power, hotkeys, fnlock, config, kbdcolor
        caps = hardware.detect_capabilities(probe_gpu=False)
        try:
            from ...ambient import ambient_available
            caps['kbd_ambient'] = ambient_available()
        except (ImportError, ValueError):
            caps['kbd_ambient'] = False
        supported, fn_supported, message = hotkeys.support_status()
        cfg = config.load_config()
        idle = keyboard_idle.support_status()
        dimmed = keyboard_idle.is_dimmed()
        brightness = hardware.read_kbd_brightness()
        dimmed = dimmed or keyboard_idle.is_dimmed()
        return dict(brightness=brightness if brightness is not None else cfg.get('kbd_brightness', 3),
                    saved_brightness=cfg.get('kbd_brightness', 3), caps=caps, modes=kbdcolor.supported_modes(caps),
                    hint='' if caps.get('kbd_rgb') else 'RGB detection failed. Install rogauracore or reconnect the keyboard, then retry.',
                    idle=idle, idle_supported=idle != keyboard_idle.UNAVAILABLE, dimmed=dimmed,
                    power_supported=keyboard_power.supported(), bindings_supported=supported,
                    fn_supported=fn_supported, input_status=message, runtime=hotkeys.runtime_status(),
                    bindings=[{'key': k, 'label': v} for k, v in hotkeys.BUTTONS.items()],
                    actions=list(hotkeys.ACTIONS), action_labels=list(hotkeys.ACTIONS.values()),
                    fn_keys=[str(n) for n in fnlock.F_KEYS], fn_targets=list(fnlock.TARGETS), fn_labels=list(fnlock.TARGETS.values()),
                    power=keyboard_power.preferences(cfg), key_bindings=hotkeys.preferences(cfg), fn_lock=fnlock.preferences(cfg))

    @Slot(str, object, object)
    def _read_done(self, job, result, error):
        current = (not self._closed and job == 'settings-' + self.page
                   and self._read_epoch == self.app._write_epoch and not self.app.busy)
        super()._read_done(job, result, error)
        if not current or error or not result or self.app.demo or 'brightness' in self._edits:
            return
        level = result.get('brightness')
        saved = result.get('saved_brightness')
        if level is None or result.get('dimmed') or level == saved:
            return
        # Fn-key changes should survive login; idle dimming must never replace
        # the user's saved brightness. Preserve any edit made since sampling.
        def save(cfg):
            if cfg.get('kbd_brightness', 3) == saved:
                cfg['kbd_brightness'] = level
        self.persist(save)

    @Slot()
    def retry(self):
        self.refresh()
        if not self.app.demo and self.app.config.get('kbd_rgb', {}).get('mode') == 'Ambient':
            self._stop_ambient()
            self._start_ambient()

    @Slot(str, 'QVariant')
    def setLighting(self, key, value):
        if key in ('brightness', 'speed'):
            value = int(value)
            if not (0 <= value <= 3 if key == 'brightness' else 1 <= value <= 3):
                return
        elif key == 'mode':
            if value not in self._data.get('modes', []):
                return
        elif key in ('color1', 'color2'):
            color = QColor(str(value))
            if not color.isValid():
                return
            value = color.name()
        else:
            return
        self._edits[key] = value
        self.stateChanged.emit()
        self.debounce.start()

    def _flush(self):
        if self._closed or not self._edits:
            return
        if self.app.busy:
            self.debounce.start()
            return
        edits = deepcopy(self._edits)
        values = self._lighting()
        if self.app.demo:
            c1, c2 = QColor(values['color1']), QColor(values['color2'])
            self.app.config['kbd_brightness'] = values['brightness']
            self.app.config.setdefault('kbd_rgb', {}).update(mode=values['mode'], speed=values['speed'],
                r=c1.red(), g=c1.green(), b=c1.blue(), r2=c2.red(), g2=c2.green(), b2=c2.blue())
            self._edits.clear()
            self.app.configChanged.emit()
            return
        def apply():
            from ... import hardware, kbdcolor, config
            from ...profile_requests import hardware_apply_lock, selection_lock
            errors = []
            with hardware_apply_lock():
                if self._closed:
                    return []
                if 'brightness' in edits:
                    ok, message = hardware.run_helper(*kbdcolor.kbd_brightness_args(values['brightness']))
                    if ok:
                        with selection_lock():
                            config.update_config(lambda cfg: cfg.update(kbd_brightness=values['brightness']))
                    else:
                        errors.append(message)
                if any(k != 'brightness' for k in edits):
                    mode = values['mode']
                    c1, c2 = QColor(values['color1']), QColor(values['color2'])
                    color1, color2 = (c1.red(), c1.green(), c1.blue()), (c2.red(), c2.green(), c2.blue())
                    cfg = config.load_config()
                    caps = hardware.detect_capabilities(probe_gpu=False)
                    if mode == 'Ambient':
                        from ...ambient import ambient_available
                        if not ambient_available():
                            raise RuntimeError('Ambient requires ScreenCast portal and GStreamer PipeWire elements.')
                        ok, message = True, ''
                    elif mode == 'Profile Color':
                        args = kbdcolor.profile_color_args(dict(cfg, kbd_rgb={'mode': mode}), caps=caps)
                        ok, message = hardware.run_helper(*args) if args else (False, 'Profile color is unsupported.')
                    elif mode in kbdcolor.LIVE_COLOUR_MODES:
                        if mode == 'GPU Temp Color' and (hardware.dgpu_is_suspended() or hardware.nvidia_access_error()):
                            ok, message = False, 'GPU is sleeping or unavailable; choose another mode or retry.'
                        else:
                            color, reason = hardware.read_live_color(mode)
                            ok, message = hardware.run_helper(*kbdcolor.static_args(color)) if color else (False, reason)
                    else:
                        args = kbdcolor.helper_args(mode, color1, color2, values['speed'])
                        ok, message = hardware.run_helper(*args)
                    if ok:
                        with selection_lock():
                            config.update_config(lambda fresh: fresh.update(kbd_rgb=kbdcolor.merge_kbd_rgb(
                                fresh.get('kbd_rgb'), mode, color1, color2, values['speed'])))
                    else:
                        errors.append(message)
            return errors
        def done(result, error):
            for key, value in edits.items():
                if self._edits.get(key) == value:
                    self._edits.pop(key, None)
            if result:
                self._status = '; '.join(str(e) for e in result)
            if self._edits:
                self.debounce.start()
        self.write(apply, done)

    @Slot(str, 'QVariant')
    def setPreference(self, key, value):
        if key in ('kbd_idle_timeout_ac_seconds', 'kbd_idle_timeout_battery_seconds'):
            value = max(0, min(86400, int(value)))
        elif key == 'charger_flash':
            value = bool(value)
        elif key == 'charger_flash_color':
            color = QColor(str(value))
            if not color.isValid():
                return
            value = [color.red(), color.green(), color.blue()]
        else:
            return
        self._preference_edits[key] = value
        self.stateChanged.emit()
        self.preferenceTimer.start()

    def _flush_preferences(self):
        if self._closed or not self._preference_edits:
            return
        if self.app.busy:
            self.preferenceTimer.start()
            return
        edits = deepcopy(self._preference_edits)
        if self.app.demo:
            self.app.config.update(edits)
            self._preference_edits.clear()
            self.app.configChanged.emit()
            return
        def save():
            from ... import config
            from ...profile_requests import selection_lock
            with selection_lock():
                config.update_config(lambda cfg: cfg.update(edits))
        def done(result, error):
            for key, value in edits.items():
                if self._preference_edits.get(key) == value:
                    self._preference_edits.pop(key, None)
            if self._preference_edits:
                self.preferenceTimer.start()
        self.write(save, done)

    @Slot()
    def disableIdle(self):
        self._preference_edits.update(kbd_idle_timeout_ac_seconds=0, kbd_idle_timeout_battery_seconds=0)
        self.stateChanged.emit()
        self.preferenceTimer.start()

    @Slot(str, str, 'QVariant')
    def setBinding(self, section, key, value):
        if section not in ('key_bindings', 'fn_lock'):
            return
        if key in ('enabled', 'media'):
            value = bool(value)
        elif section == 'key_bindings':
            if key not in [row['key'] for row in self._data.get('bindings', [])] or value not in self._data.get('actions', []):
                return
        elif key not in self._data.get('fn_keys', []) or value not in self._data.get('fn_targets', []):
            return
        def mutate(cfg):
            from ... import hotkeys, fnlock
            block = (hotkeys.preferences(cfg) if section == 'key_bindings' else fnlock.preferences(cfg))
            if section == 'fn_lock' and key not in ('enabled', 'media'):
                block['map'][key] = value
            else:
                block[key] = value
            cfg[section] = block
        if self.app.demo:
            block = self.app.config.setdefault(section, deepcopy(self._data.get(section, {})))
            (block.setdefault('map', {}) if section == 'fn_lock' and key not in ('enabled', 'media') else block)[key] = value
            self.app.configChanged.emit()
        else:
            self.persist(mutate)

    @Slot(str, str, bool)
    def setPower(self, zone, state, enabled):
        if state not in ('enabled', 'boot', 'awake', 'sleep', 'shutdown', 'battery'):
            return
        if zone not in (('keyboard', 'lightbar', '') if state == 'enabled' else ('keyboard', 'lightbar')):
            return
        base = self._power_preferences()
        if not base:
            return
        if state == 'enabled':
            base['enabled'] = enabled
        else:
            base[zone][state] = enabled
        if self.app.demo:
            self.persist(lambda cfg: cfg.update(keyboard_power=base))
            return
        def apply():
            from ... import keyboard_power, hardware
            masks = keyboard_power.desired_masks({'keyboard_power': base}, hardware.is_ac_connected())
            return keyboard_power.write_masks(*masks) if masks else (True, 'Automatic lighting power policy disabled.')
        def save_policy(cfg):
            from ... import keyboard_power
            policy = keyboard_power.preferences(cfg)
            if state == 'enabled':
                # Opt-in explicitly chooses every displayed zone default.
                policy = deepcopy(base)
            else:
                policy[zone][state] = enabled
            cfg['keyboard_power'] = policy
        self.physical(apply, save_policy)

    @Slot(str, str)
    def setProfileColor(self, target, color):
        if target not in self.app.profileNames or not QColor(color).isValid():
            return
        qc = QColor(color)
        value = [qc.red(), qc.green(), qc.blue()]
        if self.app.demo:
            self.persist(lambda cfg: cfg['profiles'][target].update(kbd_color=value))
            return
        captured_active = self.app.activeProfile
        caps = deepcopy(self._data.get('caps'))
        def save():
            from ... import config, hardware
            from ...profile_requests import selection_lock, hardware_apply_lock
            with hardware_apply_lock():
                if self._closed:
                    return None
                fresh = config.load_config()
                status, _ = config.deferred_save_target(fresh, captured_active)
                if status != config.SAVE_OK or target not in fresh.get('profiles', {}):
                    raise RuntimeError('Profile changed or was deleted; refresh and retry.')
                candidate = deepcopy(fresh)
                candidate['profiles'][target]['kbd_color'] = value
                if target == captured_active:
                    result = hardware.set_profile_kbd_color(candidate, profile_name=target, caps=caps)
                    if result and not result[0]:
                        raise RuntimeError(result[1])
                # kbd_color is a list, whereas save_deferred accepts nested
                # mappings. Use its target guard against fresh state and retain
                # the existing list schema for the top-level profile colour.
                with selection_lock():
                    latest = config.load_config()
                    status, _ = config.deferred_save_target(latest, captured_active)
                    if status != config.SAVE_OK or target not in latest.get('profiles', {}):
                        raise RuntimeError('Profile changed or was deleted; colour was not saved.')
                    latest['profiles'][target]['kbd_color'] = value
                    config.save_config(latest)
        self.write(save)

    @Slot()
    def _sync_mode(self):
        if self._closed or self.app.demo:
            return
        mode = self.app.config.get('kbd_rgb', {}).get('mode')
        if mode != self._last_mode:
            self._last_mode = mode
            self._ambient_generation += 1
            self._colors = self._last_colors = None
        if mode == 'Ambient' and not self._ambient_mode:
            self._start_ambient()
        elif mode != 'Ambient' and self._ambient_mode:
            self._stop_ambient()

    def _start_ambient(self):
        self._last_mode = 'Ambient'
        self._ambient_mode = True
        self._ambient_generation += 1
        generation = self._ambient_generation
        stop = self._ambient_stop = threading.Event()
        token = self.app.config.get('kbd_rgb', {}).get('ambient_restore_token')
        def capture():
            context = sampler = None
            try:
                from ...ambient import AmbientSampler
                from gi.repository import GLib
                context = GLib.MainContext.new()
                context.push_thread_default()
                sampler = AmbientSampler(
                    lambda colors: self.ambientColors.emit((generation, colors)) if not stop.is_set() else None,
                    lambda status: self.ambientStatus.emit((generation, status)) if not stop.is_set() else None,
                    restore_token=token,
                    on_token=lambda new: self.ambientToken.emit((generation, new)) if not stop.is_set() else None)
                sampler.start()
                while not stop.is_set():
                    # Portal/Gio async callbacks dispatch on this private context.
                    for _ in range(20):
                        if not context.pending():
                            break
                        context.iteration(False)
                    stop.wait(0.025)
            except Exception as error:
                if not stop.is_set():
                    self.ambientStatus.emit((generation, 'Ambient: ' + str(error)))
            finally:
                try:
                    if sampler is not None:
                        sampler.stop()
                finally:
                    if context is not None:
                        context.pop_thread_default()
        self._ambient = threading.Thread(target=capture, daemon=True)
        self._capture_threads = [thread for thread in self._capture_threads if thread.is_alive()]
        self._capture_threads.append(self._ambient)
        self._ambient.start()

    def _stop_ambient(self):
        self._ambient_mode = False
        self._ambient_generation += 1
        self._colors = self._last_colors = None
        if self._ambient_stop:
            self._ambient_stop.set()

    @Slot(object)
    def _on_colors(self, payload):
        generation, colors = payload
        if not self._closed and generation == self._ambient_generation and self._ambient_mode:
            self._colors = colors
            self._paint()

    @Slot(object)
    def _on_status(self, payload):
        generation, status = payload
        if not self._closed and generation == self._ambient_generation:
            self._status = status
            self.stateChanged.emit()

    @Slot(object)
    def _on_token(self, payload):
        generation, token = payload
        if self._closed or generation != self._ambient_generation:
            return
        if self.app.busy:
            QTimer.singleShot(300, lambda: self._on_token(payload))
            return
        self.persist(lambda cfg: cfg.setdefault('kbd_rgb', {}).update(ambient_restore_token=token))

    def _paint(self):
        colors = self._colors
        if not colors or colors == self._last_colors or self.app.busy or self.app.demo or self._closed:
            return
        generation = self._ambient_generation
        expected_mode = self.app.config.get('kbd_rgb', {}).get('mode')
        zonal = bool(self._data.get('caps', {}).get('kbd_rgb_zones'))
        def apply():
            from ... import hardware, config
            from ...profile_requests import hardware_apply_lock
            with hardware_apply_lock():
                if self._closed or generation != self._ambient_generation:
                    return None
                mode = config.load_config().get('kbd_rgb', {}).get('mode')
                if mode != expected_mode:
                    return None
                if mode == 'Ambient':
                    args = ('kbdrgb', 'multi_static', *[channel for zone in colors for channel in zone]) if zonal else ('kbdrgb', 'single_static', *[round(sum(z[i] for z in colors) / len(colors)) for i in range(3)])
                elif mode in ('CPU Temp Color', 'GPU Temp Color', 'Battery Level'):
                    args = ('kbdrgb', 'single_static', *colors[0])
                else:
                    return None
                ok, message = hardware.run_helper(*args)
                if not ok:
                    raise RuntimeError(message)
                return colors
        def done(result, error):
            if not error and result and generation == self._ambient_generation:
                self._last_colors = result
        self.write(apply, done)

    def _live(self):
        if self._closed or self.app.demo or self.app.busy:
            return
        self._sync_mode()
        if self._ambient_mode:
            self._paint()
            return
        mode = self.app.config.get('kbd_rgb', {}).get('mode')
        if mode not in ('CPU Temp Color', 'GPU Temp Color', 'Battery Level') or self.app.runner.active('keyboard-live'):
            return
        def read():
            from ... import hardware
            if mode == 'GPU Temp Color' and (hardware.dgpu_is_suspended() or hardware.nvidia_access_error()):
                return mode, None, 'GPU sleeping or inaccessible'
            color, reason = hardware.read_live_color(mode)
            return mode, color, reason
        self.app.runner.submit('keyboard-live', read)

    @Slot(str, object, object)
    def _live_done(self, job, result, error):
        if self._closed or job != 'keyboard-live':
            return
        if error:
            self._status = str(error)
        elif result[0] == self.app.config.get('kbd_rgb', {}).get('mode'):
            if result[1]:
                self._colors = [result[1]]
                self._paint()
            elif result[2]:
                self._status = result[2]
        self.stateChanged.emit()

    def close(self):
        self.debounce.stop()
        self.liveTimer.stop()
        self.preferenceTimer.stop()
        self._stop_ambient()
        super().close()
