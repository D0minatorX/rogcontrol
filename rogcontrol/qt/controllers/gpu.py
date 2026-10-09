"""Independent GPU gates, positively confirmed voltage, backend mode workflows."""
from copy import deepcopy
from PySide6.QtCore import Signal, Property, Slot, QTimer
from .tuning import TuningController, number

CAPS = {'watts': 'gpu_power_limit', 'clock_limit': 'gpu_clock_limit',
        'dyn_boost': 'nv_dynamic_boost', 'temp_target': 'nv_temp_target',
        'voltage_boost': 'nvidia_voltage_boost', 'clock_offset': 'nvidia_core_clock_offset',
        'mem_clock_offset': 'nvidia_memory_clock_offset', 'powermizer_mode': 'powermizer_modes'}
DIRECT = ('watts', 'clock_limit', 'clock_offset', 'mem_clock_offset', 'voltage_boost', 'powermizer_mode')


class GpuController(TuningController):
    detailsChanged = Signal()
    section = 'gpu'

    def __init__(self, app, parent=None):
        self._probed, self._probing, self._mode_confirmation = False, False, None
        self._reapply_after_probe = False
        self._reboot_pending = False
        self._mode_epoch = 0
        self._last_mode = None
        self._demo_mode = 'Hybrid'
        super().__init__(app, parent)
        self.stateChanged.connect(self.detailsChanged.emit)

    rebootPending = Property(bool, lambda s: s._reboot_pending, notify=detailsChanged)

    def demo_caps(self):
        return dict({v: True for v in CAPS.values()}, gpu_limits={'min_w': 35, 'max_w': 140, 'clock_limit_max': 3090},
                    powermizer_modes=[0, 1, 2])

    def demo_live(self):
        return {'temperature': '51 °C', 'clock': '1620 MHz', 'power': '36 W', 'fan': '2200 RPM',
                'mode': self._demo_mode, 'modes': ['Integrated', 'Hybrid', 'Smart', 'Manual'],
                'backend': 'cardwire', 'access': '', 'mode_gate': '', 'daemon': True}

    def sample(self, caps):
        from ... import hardware as h, graphics_backend
        cardwire = graphics_backend.using_cardwire()
        mode, modes = h.read_cardwire_status() if cardwire else (h.read_gpu_mode(), h.read_supported_gpu_modes())
        suspended = not caps.get('nvidia') or h.dgpu_is_suspended()
        access = h.nvidia_access_error() if caps.get('nvidia') and not suspended else ''
        gpu = (None, None, None) if suspended or access else h.read_nvidia_query(('temperature.gpu', 'clocks.gr', 'power.draw'))
        daemon = h.read_cardwired_state() if cardwire else h.read_supergfxd_state()
        gate = ''
        if not caps.get('cardwire' if cardwire else 'supergfxctl'):
            gate = 'Install the graphics backend to change modes.'
        elif cardwire and not caps.get('cardwire_wayland'):
            gate = 'Cardwire GPU access modes require a Wayland session.'
        elif mode is None:
            gate = 'Graphics daemon unavailable. Enable it and retry.'
        return {'temperature': 'Idle' if suspended else gpu[0], 'clock': gpu[1], 'power': gpu[2],
                'fan': h.read_fan_rpms().get('2'), 'mode': mode or '',
                'modes': h.gpu_mode_choices(mode, modes), 'backend': 'cardwire' if cardwire else 'supergfxctl',
                'access': access or ('GPU is asleep' if suspended else ''), 'mode_gate': gate,
                'daemon': daemon, 'pending_action': h.PENDING_NONE if cardwire else h.read_gpu_pending_action(),
                'firmware_boost': h.read_nv_dynamic_boost(),
                'firmware_temp': h.read_nv_temp_target()}

    @Slot()
    def refresh(self):
        super().refresh()
        if self._closed or self.app.demo or not self._reapply_after_probe or self.app.busy or self._probing or self.app.runner.active('gpu-deferred-availability'):
            return
        def read():
            from ... import hardware as h
            return not h.dgpu_is_suspended() and not h.nvidia_access_error()
        self.app.runner.submit('gpu-deferred-availability', read)

    def after_read(self):
        pending = self._live.get('pending_action')
        # Unknown readback retains the last known state; an authoritative
        # none/logout clears an obsolete reboot action on the GUI thread.
        if self._live.get('backend') == 'cardwire':
            self._reboot_pending = False
        elif pending is not None:
            self._reboot_pending = pending == 'reboot'
        mode = self._live.get('mode')
        if mode == 'Hybrid' and self._last_mode not in (None, 'Hybrid'):
            self._probed = False
            self._reapply_after_probe = True
        if mode:
            self._last_mode = mode
        if not self._probed and not self._probing and not self.app.busy and not self._live.get('access'):
            self.probe()

    @Slot()
    def probe(self):
        if self.app.demo:
            self._status = 'Demo capabilities ready'
            self.stateChanged.emit()
            return
        if self._probing or not self._ready:
            return
        caps = deepcopy(self.caps)
        reapply = getattr(self, '_reapply_after_probe', False)
        def worker():
            from ... import hardware as h
            from ...profile_requests import hardware_apply_lock
            # Some runtime probes write back the current setting to verify
            # ownership. They belong in the hardware lane, never a read job.
            with hardware_apply_lock():
                if self._closed:
                    raise RuntimeError('Controller closed before operation')
                if h.dgpu_is_suspended() or h.nvidia_access_error():
                    raise RuntimeError('GPU is asleep or direct access is blocked; retry when available.')
                result = h.probe_gpu_tuning_capabilities(caps)
                result['gpu_power_limit'] = h.gpu_power_limit_supported()
                result['gpu_power_limit_probed'] = True
                result['gpu_limits'] = h.detect_gpu_limits()
                result['nvidia_voltage_boost'] = h.probe_nvidia_voltage_boost() is not None
                modes = h.detect_nvidia_powermizer_modes()
                result['powermizer_modes'] = modes
                restoration = []
                if reapply:
                    from ... import config
                    from .tuning import apply_saved_profile
                    fresh = config.load_config()
                    gpu_only = deepcopy(fresh)
                    name = fresh.get('current_profile')
                    gpu_only['profiles'][name] = {'gpu': deepcopy(fresh['profiles'][name].get('gpu', {}))}
                    restoration = apply_saved_profile(gpu_only, dict(caps, **result), gpu_only=True)
                return result, restoration
        def done(result, error):
            self._probing = False
            if self._closed:
                return
            if error:
                self._probed = True
                self._status = str(error)
            else:
                self.caps.update(result[0])
                self._reapply_after_probe = False
                self._probed = True
                self._rows = self.build_rows(self.caps)
                self.reload()
                self._status = '\n'.join(result[1]) or 'GPU tuning capabilities refreshed'
            self.stateChanged.emit()
        self._probing = True
        if not self.submit('gpu-probe', worker, done):
            self._probing = False

    def build_rows(self, caps):
        limits = caps.get('gpu_limits') or {'min_w': 1, 'max_w': 140, 'clock_limit_max': 3090}
        rows = [number('watts', 'Board power limit', limits['min_w'], limits['max_w'], limits['max_w'], 'W'),
                number('clock_limit', 'Clock ceiling (maximum = unlimited)', 200, limits['clock_limit_max'], limits['clock_limit_max'], 'MHz', step=15),
                number('dyn_boost', 'Dynamic Boost', 5, 25, self._live.get('firmware_boost') or 5, 'W'),
                number('temp_target', 'Temperature target', 75, 87, self._live.get('firmware_temp') or 75, '°C'),
                number('voltage_boost', 'Voltage boost (0 = stock)', 0, 100, 0, '%')]
        for key, kind, title in [('clock_offset', 'core', 'Core clock offset'), ('mem_clock_offset', 'memory', 'Memory clock offset')]:
            bounds = caps.get('gpu_offset_limits', {}).get(kind, {})
            rows.append(number(key, title, max(-1000, bounds.get('minimum', -1000)), min(1000, bounds.get('maximum', 1000)), 0, 'MHz', step=15 if kind == 'core' else 50))
        modes = caps.get('powermizer_modes')
        if isinstance(modes, dict):
            modes = modes.get('modes', []) if modes.get('ok') else []
        if modes:
            # Backend modes are integer IDs paired with names; values preserve IDs.
            rows.append(dict(key='powermizer_mode', title='PowerMizer', kind='choice', choices=list(modes), choiceLabels=[{0: 'Adaptive', 1: 'Prefer Maximum Performance', 2: 'Driver controlled / Auto', 3: 'Prefer Consistent Performance'}.get(m, str(m)) for m in modes], default=2 if 2 in modes else list(modes)[0]))
        return [r for r in rows if caps.get(CAPS[r['key']])]

    def row_enabled(self, row):
        return row['key'] not in DIRECT or not self._live.get('access')

    @Slot()
    def apply(self):
        if not self._ready or not self._target:
            return
        snapshot = deepcopy(self._values)
        if snapshot.get('voltage_boost', 0) > 0 and any(r['key'] == 'voltage_boost' for r in self._rows):
            self._pending_confirmation = (self._target, snapshot)
            self.confirmationRequested.emit('voltage', 'Positive voltage boost is experimental and can increase heat and instability. Apply this voltage boost?')
        else:
            self.start_apply(snapshot, self._target)

    @Slot(str, bool)
    def confirm(self, action, accepted):
        if action == 'voltage':
            pending, self._pending_confirmation = self._pending_confirmation, None
            if accepted and pending and pending[0] == self._target and pending[1] == self._values:
                self.start_apply(pending[1], pending[0])
        elif action == 'mode':
            pending, self._mode_confirmation = self._mode_confirmation, None
            if accepted and pending:
                self._switch_mode(*pending)
        elif action == 'reboot' and accepted and self._reboot_pending:
            self._status = 'Rebooting in 5 seconds…'
            self.stateChanged.emit()
            QTimer.singleShot(5000, self._reboot)

    def write(self, snapshot, baseline, caps):
        from ... import hardware as h
        saved, failed = {}, []
        available = {key for key, capability in CAPS.items() if caps.get(capability)}
        access = h.nvidia_access_error() or ('GPU is asleep' if h.dgpu_is_suspended() else '')
        for key in CAPS:
            if key not in snapshot or key not in available:
                continue
            value = snapshot[key]
            if key in DIRECT and access:
                failed.append(key + ': ' + access)
                continue
            try:
                if key == 'watts':
                    ok, message = h.run_helper('gpu', value)
                elif key == 'clock_limit':
                    ok, message = h.run_helper('gpuclocklimit', h.gpu_clock_limit_arg(value, caps['gpu_limits']['clock_limit_max']))
                elif key in ('dyn_boost', 'temp_target'):
                    ok, message = h.run_helper('nvboost' if key == 'dyn_boost' else 'nvtemp', value)
                elif key == 'voltage_boost':
                    ok, message = h.set_nvidia_voltage_boost(value)
                elif key == 'powermizer_mode':
                    ok, message = h.set_nvidia_powermizer_mode(value)
                else:
                    ok, message = h.set_nvidia_clock_offset('core' if key == 'clock_offset' else 'memory', value)
                if ok:
                    saved[key] = value
                else:
                    failed.append(key + ': ' + str(message))
            except Exception as error:
                failed.append(key + ': ' + str(error))
        return saved, failed

    @Slot(str)
    def changeMode(self, mode):
        if self._closed or mode not in self._live.get('modes', []) or self._live.get('mode_gate'):
            return
        self._mode_confirmation = (mode, self._live.get('mode'), self._live.get('backend'))
        body = 'Change GPU access policy to ' + mode + '? Existing processes keep their access.'
        if self._live.get('backend') != 'cardwire':
            body = 'Change graphics mode to ' + mode + '? This may end your session or require a reboot. Save your work first. If the display MUX requires Hybrid first, only that first step will be requested.'
        self.confirmationRequested.emit('mode', body)

    def _switch_mode(self, target, previous, backend):
        if self.app.demo:
            self._demo_mode = target
            self._live['mode'] = target
            self._status = 'Demo graphics mode selected'
            self.stateChanged.emit()
            return
        self._mode_epoch += 1
        epoch = self._mode_epoch
        def worker():
            from ... import hardware as h
            from ...profile_requests import hardware_apply_lock
            with hardware_apply_lock():
                if self._closed:
                    raise RuntimeError('Controller closed before operation')
                current = h.read_gpu_mode()
                if current != previous:
                    raise RuntimeError('Graphics mode changed while the dialog was open; refresh and retry.')
                wanted = 'Hybrid' if backend != 'cardwire' and h.mode_needs_hybrid_first(current, target) else target
                ok, message = h.set_gpu_mode(wanted)
                pending = h.PENDING_NONE if backend == 'cardwire' else h.read_gpu_pending_action()
                reboot = ok and backend != 'cardwire' and (h.supergfxd_always_reboot() or pending == h.PENDING_REBOOT or h.mode_change_needs_reboot(current, wanted))
                return ok, message, wanted, reboot, pending
        def done(result, error):
            if self._closed:
                return
            if error:
                self._status = str(error)
            else:
                ok, message, wanted, reboot, pending = result
                self._status = str(message) or ('Request accepted' if ok else 'Request refused')
                if wanted != target:
                    self._status += '\nHybrid must take effect before selecting ' + target + ' again.'
                if ok:
                    self._reboot_pending = bool(reboot)
                elif pending is not None:
                    self._reboot_pending = pending == 'reboot'
                # A refused request with unknown readback cannot erase a
                # previous pending transition. Only accepted changes prompt.
                if reboot:
                    self.confirmationRequested.emit('reboot', 'The graphics transition needs a reboot. Restart in 5 seconds?')
                elif ok:
                    self._status += '\nVerifying daemon state…' if backend == 'cardwire' else '\nLog out to finish switching; verifying daemon state…'
                    QTimer.singleShot(2000 if backend == 'cardwire' else 20000, lambda: self._verify_mode(wanted, backend, epoch))
            self.stateChanged.emit()
        self.submit('gpu-mode', worker, done)

    def _verify_mode(self, target, backend, epoch):
        if self._closed or epoch != self._mode_epoch or self.app.runner.active('gpu-verify'):
            return
        job = 'gpu-verify'
        self._verification = (target, backend, epoch)
        from ... import hardware
        self.app.runner.submit(job, hardware.read_gpu_mode)

    @Slot(str, object, object)
    def _complete_read(self, job_id, result, error):
        if job_id == 'gpu-deferred-availability' and not self._closed:
            if not error and result and self._reapply_after_probe and not self.app.busy:
                self.probe() if self._ready else self.initialize()
            return
        if job_id == 'gpu-verify' and not self._closed:
            target, backend, epoch = self._verification
            if epoch != self._mode_epoch:
                return
            self._status = str(error) if error else ('Mode verified: ' + target if result == target else 'Daemon accepted ' + target + ' but current mode is ' + str(result) + '. Check journalctl -u ' + ('cardwired' if backend == 'cardwire' else 'supergfxd') + ' -b.')
            if not error and result == 'Hybrid':
                self._probed = False
                # Reprobe direct ownership before reapplying active GPU settings.
                self._reapply_after_probe = True
            self.stateChanged.emit()
            self.refresh()
            return
        super()._complete_read(job_id, result, error)

    @Slot()
    def enableDaemon(self):
        if self.app.demo:
            return
        backend = self._live.get('backend')
        def worker():
            from ... import hardware as h
            from ...profile_requests import hardware_apply_lock
            with hardware_apply_lock():
                if self._closed:
                    raise RuntimeError('Controller closed before operation')
                return h.set_cardwired_running() if backend == 'cardwire' else h.set_supergfxd_running()
        def done(result, error):
            if self._closed:
                return
            self._status = str(error) if error else str(result[1])
            self.stateChanged.emit()
            self.refresh()
        self.submit('gpu-daemon', worker, done)

    def _reboot(self):
        if self._closed or self.app.demo:
            return
        def worker():
            from ... import hardware
            from ...profile_requests import hardware_apply_lock
            with hardware_apply_lock():
                if self._closed:
                    raise RuntimeError('Controller closed before operation')
                return hardware.reboot_system()
        self.submit('gpu-reboot', worker, lambda result, error: self._progress(str(error) if error else str(result[1])))
