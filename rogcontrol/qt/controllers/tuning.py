"""Staged page state. Workers return data; Qt mutations stay on GUI thread."""
from copy import deepcopy
from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer


class TuningController(QObject):
    stateChanged = Signal()
    confirmationRequested = Signal(str, str)
    progressChanged = Signal(str)
    section = ''

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.caps = {}
        self._rows, self._saved, self._values, self._live = [], {}, {}, {}
        self._target, self._status = '', 'Detecting supported controls…'
        self._closed, self._ready, self._pending_confirmation = False, False, None
        self._caps_loading = False
        self._failures = 0
        app.configChanged.connect(self.reload)
        app.stateChanged.connect(self.refresh)
        app.runner.finished.connect(self._complete_read)
        self.progressChanged.connect(self._progress)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.reload()

    rows = Property('QVariantList', lambda s: s._present_rows(), notify=stateChanged)
    values = Property('QVariantMap', lambda s: deepcopy(s._values), notify=stateChanged)
    live = Property('QVariantMap', lambda s: deepcopy(s._live), notify=stateChanged)
    status = Property(str, lambda s: s._status, notify=stateChanged)
    dirty = Property(bool, lambda s: s._values != s._saved, notify=stateChanged)
    ready = Property(bool, lambda s: s._ready, notify=stateChanged)

    def _present_rows(self):
        return [dict(row, value=self._values.get(row['key'], row.get('default', 0)),
                     enabled=self.row_enabled(row)) for row in self._rows]

    def row_enabled(self, row):
        return True

    def defaults(self):
        return {row['key']: row.get('default', 0) for row in self._rows}

    @Slot()
    def reload(self):
        if self._closed:
            return
        target = self.app.activeProfile
        saved = self.defaults()
        saved.update(deepcopy(self.app.config.get('profiles', {}).get(target, {}).get(self.section, {})))
        for row in self._rows:
            key = row['key']
            if row['kind'] == 'number':
                try:
                    saved[key] = int(round(max(row['min'] * row['scale'], min(row['max'] * row['scale'], float(saved[key])))))
                except (TypeError, ValueError):
                    saved[key] = row['default']
            elif row['kind'] == 'choice' and saved.get(key) not in row['choices']:
                saved[key] = row['default']
        if target != self._target:
            self._target, self._saved, self._values = target, saved, deepcopy(saved)
            self._pending_confirmation = None
        else:
            edited = {k: v for k, v in self._values.items() if v != self._saved.get(k)}
            self._saved = saved
            self._values = deepcopy(saved)
            self._values.update(edited)
        self.stateChanged.emit()

    @Slot(str, 'QVariant')
    def stageValue(self, key, value):
        row = next((r for r in self._rows if r['key'] == key), None)
        if not row or not self.row_enabled(row):
            return
        try:
            if row.get('kind') == 'bool':
                value = bool(value)
            elif row.get('kind') == 'choice':
                if value not in row['choices']:
                    raise ValueError('Choose a supported value')
            else:
                value = float(value)
                if not row['min'] <= value <= row['max']:
                    raise ValueError('Value is outside the supported range')
                value = int(round(value * row.get('scale', 1)))
            self._values[key] = value
            self.couple(key)
            self._pending_confirmation = None
            self.stateChanged.emit()
        except (ValueError, TypeError) as error:
            self._status = str(error)
            self.stateChanged.emit()

    def couple(self, key):
        pass

    @Slot()
    def revert(self):
        self._values = deepcopy(self._saved)
        self._pending_confirmation = None
        self._status = ''
        self.stateChanged.emit()

    @Slot()
    def refresh(self):
        if self._closed or self.app.currentPage not in (self.section, 'quick_access') or self.app.runner.active('tuning-' + self.section):
            return
        if self.app.demo:
            if not self._ready:
                self.caps = self.demo_caps()
                self._rows = self.build_rows(self.caps)
                self._ready = True
                self.reload()
            self._live = self.demo_live()
            if self._status == 'Detecting supported controls…':
                self._status = ''
            self.stateChanged.emit()
            return
        if not self._ready:
            self.initialize()
            return
        caps = deepcopy(self.caps)
        self.app.runner.submit('tuning-' + self.section, lambda: (caps, self.sample(caps)))

    def initialize(self):
        if self._caps_loading:
            return
        self._caps_loading = True
        def read():
            from ... import hardware
            result = hardware.detect_capabilities(probe_gpu=False)
            if self.section == 'gpu' and not hardware.dgpu_is_suspended() and not hardware.nvidia_access_error():
                result['gpu_limits'] = hardware.detect_gpu_limits()
            return result
        self.app.runner.submit('tuning-capabilities-' + self.section, read)

    @Slot(str, object, object)
    def _complete_read(self, job_id, result, error):
        if self._closed:
            return
        if job_id == 'tuning-capabilities-' + self.section:
            self._caps_loading = False
            if error:
                self._status = str(error)
            else:
                self.caps = result
                self._rows = self.build_rows(result)
                self._ready = True
                self._status = ''
                self.reload()
                self.refresh()
            self.stateChanged.emit()
            return
        if job_id != 'tuning-' + self.section:
            return
        if error:
            self._failures += 1
            self._status = 'Readings are stale: ' + str(error)
            if self._failures == 5:
                self.app.errorOccurred.emit(self._status)
        else:
            self._failures = 0
            self._live = result[1]
            self._rows = self.build_rows(self.caps)
            self._ready = True
            if self._status == 'Detecting supported controls…' or self._status.startswith('Readings are stale:'):
                self._status = ''
            self.reload()
            if self.app.currentPage == self.section:
                self.after_read()
        self.stateChanged.emit()

    def after_read(self):
        pass

    @Slot(str)
    def _progress(self, text):
        if not self._closed:
            self._status = text
            self.stateChanged.emit()

    def submit(self, label, function, callback):
        if not self.app.submitWrite(label, function, callback):
            self._status = 'Another operation is running. Try again when it finishes.'
            self.stateChanged.emit()
            return False
        return True

    @Slot()
    def apply(self):
        if self._ready and self._target:
            self.start_apply(deepcopy(self._values), self._target)

    def start_apply(self, snapshot, target):
        caps, baseline = deepcopy(self.caps), deepcopy(self._saved)
        if self.app.demo:
            self.app.config['profiles'][target][self.section] = deepcopy(snapshot)
            self._saved = deepcopy(snapshot)
            self._status = 'Demo settings saved'
            self.app.configChanged.emit()
            self.stateChanged.emit()
            return
        def worker():
            from ... import config
            from ...profile_requests import hardware_apply_lock, selection_lock
            with hardware_apply_lock():
                if self._closed:
                    raise RuntimeError('Page closed before apply')
                fresh_target = config.load_config()
                if fresh_target.get('current_profile') != target or target not in fresh_target.get('profiles', {}):
                    raise RuntimeError('Profile changed before apply; refresh and retry')
                successful, failed = self.write(snapshot, baseline, caps)
                with selection_lock():
                    fresh = config.load_config()
                    refusal = config.save_deferred(fresh, target, self.section, successful, self.section.upper() + ' settings') if successful else None
                try:
                    failed.extend(self.after_write(snapshot, successful, caps, fresh) or [])
                except Exception as error:
                    failed.append('Profile restoration: ' + str(error))
                return successful, failed, refusal, fresh
        def done(result, error):
            if self._closed:
                return
            if error:
                self._status = 'Apply failed: ' + str(error)
            else:
                successful, failed, refusal, fresh = result
                if self._target == target:
                    for key in snapshot:
                        if key not in successful and self._values.get(key) == snapshot[key]:
                            if key in baseline:
                                self._values[key] = deepcopy(baseline[key])
                            else:
                                self._values.pop(key, None)
                    if not refusal:
                        self._saved.update(deepcopy(successful))
                if any(message.startswith('GPU direct settings deferred:') for message in failed):
                    self.app.tuningPages['gpu']._reapply_after_probe = True
                self.app.config = fresh
                self.app.configChanged.emit()
                self._status = '\n'.join(failed + ([refusal] if refusal else [])) or 'Settings applied and saved'
            self.stateChanged.emit()
        self.submit(self.section, worker, done)

    def after_write(self, snapshot, successful, caps, fresh):
        return []

    def demo_caps(self):
        return {}

    def demo_live(self):
        return {}

    def close(self):
        self._closed = True
        self.timer.stop()
        self._pending_confirmation = None


def number(key, title, low, high, default=0, unit='', scale=1, step=1):
    return dict(key=key, title=title, min=low, max=high, default=default,
                unit=unit, scale=scale, step=step, kind='number')


def toggle(key, title, default=True):
    return dict(key=key, title=title, default=default, kind='bool')


def apply_saved_profile(cfg, caps, progress=None, gpu_only=False):
    """Apply saved active settings while caller holds hardware_apply_lock.

    UI-neutral equivalent of MainWindow's order; no nested hardware lock and
    no GTK imports. Optional GPU writes remain independent and fan boost owns
    curves until its deadline. Return every failure for the caller to display.
    """
    import time
    from ... import hardware as h, fancurve, config
    name = cfg.get('current_profile')
    profile = deepcopy(cfg.get('profiles', {}).get(name, {}))
    failures = []
    def perform(label, operation):
        try:
            result = operation()
            if result is not None and not result[0]:
                failures.append(label + ': ' + str(result[1]))
        except Exception as error:
            failures.append(label + ': ' + str(error))
    if progress:
        progress('Applying saved profile ' + str(name) + '…')
    if not gpu_only:
        perform('OS power mode', lambda: h.set_power_mode_for_profile(name))
        perform('Keyboard colour', lambda: h.set_profile_kbd_color(cfg, name, caps))
    cpu = {} if gpu_only else profile.get('cpu', {})
    if not gpu_only and cfg.get('safety_tripped'):
        cpu = config.stock_cpu_values(cpu)
    for step, args in h.cpu_apply_plan(cpu, caps):
        perform('CPU ' + step, lambda a=args: h.run_helper(*a))
    gpu = profile.get('gpu', {})
    caps = deepcopy(caps)
    access = h.nvidia_access_error() if caps.get('nvidia') else 'No NVIDIA driver'
    suspended = not caps.get('nvidia') or h.dgpu_is_suspended()
    if any(key in gpu for key in ('watts', 'clock_limit', 'voltage_boost', 'clock_offset', 'mem_clock_offset', 'powermizer_mode')) and (access or suspended):
        failures.append('GPU direct settings deferred: ' + (access or 'GPU is asleep'))
    if caps.get('nvidia') and not access and not suspended:
        if 'watts' in gpu and not caps.get('gpu_power_limit_probed'):
            caps['gpu_power_limit'] = h.gpu_power_limit_supported()
            caps['gpu_power_limit_probed'] = True
        if any(k in gpu for k in ('clock_limit', 'clock_offset', 'mem_clock_offset')) and 'gpu_offset_limits' not in caps:
            caps.update(h.probe_gpu_tuning_capabilities(caps))
            caps['gpu_limits'] = h.detect_gpu_limits()
        if 'voltage_boost' in gpu and 'nvidia_voltage_boost' not in caps:
            caps['nvidia_voltage_boost'] = h.probe_nvidia_voltage_boost() is not None
        if 'powermizer_mode' in gpu and 'powermizer_modes' not in caps:
            caps['powermizer_modes'] = h.detect_nvidia_powermizer_modes()
    if 'watts' in gpu and caps.get('gpu_power_limit') and not access and not suspended:
        perform('GPU power', lambda: h.run_helper('gpu', gpu['watts']))
    if 'clock_limit' in gpu and caps.get('gpu_clock_limit') and not access and not suspended:
        maximum = (caps.get('gpu_limits') or {}).get('clock_limit_max', h.CLOCK_LIMIT_FALLBACK_MAX)
        perform('GPU clock ceiling', lambda: h.run_helper('gpuclocklimit', h.gpu_clock_limit_arg(gpu['clock_limit'], maximum)))
    firmware = {key: gpu[key] for key, capability in [('dyn_boost', 'nv_dynamic_boost'), ('temp_target', 'nv_temp_target')] if key in gpu and caps.get(capability)}
    if firmware:
        perform('GPU firmware', lambda: h.nv_apply_settings(firmware))
    for key, kind, capability in [('clock_offset', 'core', 'nvidia_core_clock_offset'), ('mem_clock_offset', 'memory', 'nvidia_memory_clock_offset')]:
        if key in gpu and caps.get(capability) and not access and not suspended:
            perform('GPU ' + kind + ' offset', lambda k=key, domain=kind: h.set_nvidia_clock_offset(domain, gpu[k]))
    if 'voltage_boost' in gpu and caps.get('nvidia_voltage_boost') and not access and not suspended:
        perform('GPU voltage', lambda: h.set_nvidia_voltage_boost(gpu['voltage_boost']))
    if 'powermizer_mode' in gpu and caps.get('powermizer_modes') and not access and not suspended:
        perform('PowerMizer', lambda: h.set_nvidia_powermizer_mode(gpu['powermizer_mode']))
    fans = {} if gpu_only else profile.get('fans', {})
    if not gpu_only and caps.get('fan_curve') and not h.fan_boost_active(h.read_fan_boost()):
        held = {ch: h.read_fan_curve_points(ch) for ch in h.FAN_CHANNELS}
        enabled = h.read_fan_curve_enabled()
        channels = [ch for ch in h.FAN_CHANNELS if ch in fans and not (enabled.get(ch) is not False and held.get(ch) is not None and fancurve.curve_matches_hardware(fans[ch], held[ch]))]
        for index, ch in enumerate(channels):
            if index:
                time.sleep(fancurve.CHANNEL_GAP_S)
            if progress:
                progress('Applying ' + h.FAN_LABELS[ch] + ' curve…')
            perform(h.FAN_LABELS[ch], lambda channel=ch: h.run_helper('fan', channel, *fancurve.curve_to_flat(fans[channel]), timeout=30))
    return failures
