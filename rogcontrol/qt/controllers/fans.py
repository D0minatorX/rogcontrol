"""Editable fan curves, paced channel writes, calibration and durable boost."""
from copy import deepcopy
import time
from PySide6.QtCore import Property, Slot
from .tuning import TuningController

DEFAULT_CURVE = [[30, 20], [45, 30], [60, 45], [75, 65], [90, 100]]


class FansController(TuningController):
    section = 'fans'

    def __init__(self, app, parent=None):
        self._calibration_confirmed = None
        self._nudge_pending = False
        self._demo_boost_until = 0
        super().__init__(app, parent)

    channels = Property('QVariantList', lambda s: s._channels(), notify=TuningController.stateChanged)
    displayUnit = Property(str, lambda s: s.app.config.get('fan_display_unit', 'percent'), notify=TuningController.stateChanged)

    def demo_caps(self):
        return {'fan_curve': True, 'fan_rpm': True}

    def demo_live(self):
        return {'rpms': {'1': 2400, '2': 2200, '3': 1900}, 'enabled': {'1': True, '2': True, '3': True}, 'boost_seconds': max(0, int(self._demo_boost_until - time.time())), 'mismatch': []}

    def build_rows(self, caps):
        return []

    def defaults(self):
        return {ch: deepcopy(DEFAULT_CURVE) for ch in ('1', '2', '3')}

    def _channels(self):
        if not self.caps.get('fan_curve'):
            return []
        if self.app.demo:
            return [dict(key=ch, title=title, points=deepcopy(self._values.get(ch, DEFAULT_CURVE)), rpm=self._live.get('rpms', {}).get(ch), calibration=[1600, 50])
                    for ch, title in [('1', 'CPU Fan'), ('2', 'GPU Fan'), ('3', 'Mid Fan')]]
        from ... import hardware, fancurve
        return [dict(key=ch, title=hardware.FAN_LABELS[ch], points=fancurve.editor_points(self._values.get(ch, DEFAULT_CURVE)),
                     rpm=self._live.get('rpms', {}).get(ch), calibration=list(fancurve.get_rpm_cal(self.app.config, ch) or (0, 1)))
                for ch in hardware.FAN_CHANNELS]

    @Slot(str, int, float, float)
    def movePoint(self, channel, index, temperature, percent):
        if not self.caps.get('fan_curve') or channel not in self._values:
            return
        if not 0 <= temperature <= 100 or not 0 <= percent <= 100:
            return
        points = self._values[channel]
        if not 0 <= index < len(points):
            return
        if self.app.demo:
            points = deepcopy(points)
            low = points[index - 1][0] + 1 if index else 0
            high = points[index + 1][0] - 1 if index + 1 < len(points) else 100
            points[index] = [max(low, min(high, round(temperature))), round(percent)]
        else:
            from ... import fancurve
            points = fancurve.move_point(fancurve.editor_points(points), index, temperature, percent)
        self._values[channel] = points
        self.stateChanged.emit()

    def sample(self, caps):
        from ... import hardware as h, fancurve, config
        cfg = config.load_config()
        fans = cfg.get('profiles', {}).get(cfg.get('current_profile'), {}).get('fans', {})
        enabled = h.read_fan_curve_enabled()
        held = {ch: h.read_fan_curve_points(ch) for ch in h.FAN_CHANNELS} if caps.get('fan_curve') else {}
        mismatch = [h.FAN_LABELS[ch] for ch in fans if enabled.get(ch) is False or held.get(ch) is None or not fancurve.curve_matches_hardware(fans[ch], held[ch])]
        boost = h.read_fan_boost()
        return {'rpms': h.read_fan_rpms() if caps.get('fan_rpm') else {}, 'enabled': enabled,
                'mismatch': mismatch, 'boost_seconds': max(0, int(boost['until'] - time.time())) if boost else 0,
                'boost': boost, 'calibration_available': bool(caps.get('fan_curve') and caps.get('fan_rpm'))}

    def after_read(self):
        boost = self._live.get('boost')
        if boost and self._live.get('boost_seconds', 0) <= 0 and not self.app.busy:
            self.stopBoost()
        cfg = self.app.config
        if self.caps.get('fan_curve') and self.caps.get('fan_rpm') and not cfg.get('fan_calibration_prompted') and not cfg.get('fan_rpm_cal') and not self._nudge_pending and not self.app.busy:
            self._nudge_pending = True
            def worker():
                from ... import config
                from ...profile_requests import selection_lock
                with selection_lock():
                    fresh = config.load_config()
                    show = not fresh.get('fan_calibration_prompted') and not fresh.get('fan_rpm_cal')
                    if show:
                        fresh = config.update_config(lambda c: c.update(fan_calibration_prompted=True))
                    return fresh, show
            def done(result, error):
                self._nudge_pending = False
                if self._closed:
                    return
                if error:
                    self._status = str(error)
                else:
                    self.app.config = result[0]
                    self.app.configChanged.emit()
                    if result[1]:
                        self.calibrate()
                self.stateChanged.emit()
            if not self.submit('fan-calibration-prompt', worker, done):
                self._nudge_pending = False

    def write(self, snapshot, baseline, caps):
        from ... import hardware as h, fancurve
        if not caps.get('fan_curve'):
            return {}, ['Fan curve interface is unavailable']
        if h.fan_boost_active(h.read_fan_boost()):
            return {}, ['Stop temporary fan boost before applying curves']
        saved, failed = {}, []
        channels = [ch for ch in h.FAN_CHANNELS if ch in snapshot]
        for i, ch in enumerate(channels):
            if i:
                time.sleep(fancurve.CHANNEL_GAP_S)
            self.progressChanged.emit('Writing ' + h.FAN_LABELS[ch] + '…')
            try:
                ok, message = h.run_helper('fan', ch, *fancurve.curve_to_flat(snapshot[ch], 8), timeout=30)
                if ok:
                    saved[ch] = snapshot[ch]
                else:
                    failed.append(h.FAN_LABELS[ch] + ': ' + str(message))
            except Exception as error:
                failed.append(h.FAN_LABELS[ch] + ': ' + str(error))
        return saved, failed

    @Slot(str)
    def setDisplayUnit(self, unit):
        if unit not in ('percent', 'rpm'):
            return
        if self.app.demo:
            self.app.config['fan_display_unit'] = unit
            self.stateChanged.emit()
            return
        def worker():
            from ... import config
            from ...profile_requests import selection_lock
            with selection_lock():
                return config.update_config(lambda c: c.update(fan_display_unit=unit))
        self.submit('fan-unit', worker, self._config_done)

    def _config_done(self, result, error):
        if self._closed:
            return
        if error:
            self._status = str(error)
        else:
            self.app.config = result
            self.app.configChanged.emit()
        self.stateChanged.emit()

    @Slot()
    def calibrate(self):
        if not self.caps.get('fan_curve') or not self.caps.get('fan_rpm'):
            return
        if self._live.get('boost_seconds', 0) > 0:
            self._status = 'Stop fan boost before calibration'
            self.stateChanged.emit()
            return
        self._calibration_confirmed = self._target
        self.confirmationRequested.emit('calibrate', 'Calibration takes several minutes and briefly drives all fans at full speed. Original curves are restored afterward. Start measuring?')

    @Slot(str, bool)
    def confirm(self, action, accepted):
        if action != 'calibrate':
            return
        target, self._calibration_confirmed = self._calibration_confirmed, None
        if not accepted or target != self._target:
            return
        if self.app.demo:
            self._status = 'Demo calibration complete'
            self.stateChanged.emit()
            return
        def worker():
            from ... import hardware as h, fancurve, config
            from ...profile_requests import hardware_apply_lock, selection_lock
            with hardware_apply_lock():
                if self._closed:
                    raise RuntimeError('Controller closed before operation')
                if h.fan_boost_active(h.read_fan_boost()):
                    raise RuntimeError('Temporary fan boost is active')
                fresh = config.load_config()
                if fresh.get('current_profile') != target:
                    raise RuntimeError('Profile changed before calibration; retry')
                channels = list(h.FAN_CHANNELS)
                originals = {ch: h.read_fan_curve_points(ch) for ch in channels}
                # Driver points are already PWM values. Require real originals
                # so cleanup can restore precisely even after a partial failure.
                if any(originals[ch] is None for ch in channels):
                    raise RuntimeError('Cannot read original curves; calibration was not started')
                samples, failures = {ch: [] for ch in channels}, []
                paused = h.set_enforcer_running(False)
                if not paused:
                    raise RuntimeError('Cannot pause enforcer; calibration was not started')
                try:
                    for step, pct in enumerate((20, 45, 70, 100), 1):
                        for i, ch in enumerate(channels):
                            if i:
                                time.sleep(fancurve.CHANNEL_GAP_S)
                            self.progressChanged.emit(f'Calibration {step}/4: {h.FAN_LABELS[ch]} at {pct}%')
                            flat = [v for temp in h.FAN_BOOST_TEMPS for v in (temp, fancurve.pct_to_pwm255(pct))]
                            ok, message = h.run_helper('fan', ch, *flat, timeout=30)
                            if not ok:
                                raise RuntimeError(h.FAN_LABELS[ch] + ': ' + str(message))
                        history, start = {ch: [] for ch in channels}, time.monotonic()
                        while True:
                            rpms = h.read_fan_rpms()
                            for ch in channels:
                                history[ch].append(rpms.get(ch))
                            elapsed = time.monotonic() - start
                            self.progressChanged.emit(f'Calibration {step}/4 at {pct}%: settling, {int(elapsed)}s')
                            settled = all(len(hist) >= 3 and all(v is not None for v in hist[-3:]) and max(hist[-3:]) - min(hist[-3:]) <= 150 for hist in history.values())
                            if settled or elapsed >= 100:
                                break
                            time.sleep(3)
                        for ch in channels:
                            samples[ch].append((pct, rpms.get(ch)))
                except Exception as error:
                    failures.append(str(error))
                finally:
                    # Every channel gets a restore attempt, including when an
                    # earlier restore fails. Resume enforcer in outer cleanup.
                    try:
                        for i, ch in enumerate(channels):
                            if i:
                                time.sleep(fancurve.CHANNEL_GAP_S)
                            try:
                                self.progressChanged.emit('Restoring ' + h.FAN_LABELS[ch] + '…')
                                ok, message = h.run_helper('fan', ch, *[v for pair in originals[ch] for v in pair], timeout=30)
                                if not ok:
                                    failures.append('Restore ' + ch + ': ' + str(message))
                            except Exception as error:
                                failures.append('Restore ' + ch + ': ' + str(error))
                    finally:
                        if not h.set_enforcer_running(True):
                            failures.append('Could not resume enforcer')
                measured = {ch: list(fit) for ch in channels if len(samples[ch]) == 4 and (fit := fancurve.fit_rpm_cal(samples[ch]))}
                with selection_lock():
                    fresh = config.update_config(lambda c: c.setdefault('fan_rpm_cal', {}).update(measured)) if measured else config.load_config()
                return fresh, failures, measured
        def done(result, error):
            if self._closed:
                return
            if error:
                self._status = str(error)
            else:
                self.app.config = result[0]
                self.app.configChanged.emit()
                self._status = '\n'.join(result[1]) or ('Calibration saved' if result[2] else 'No reliable calibration measurements')
            self.stateChanged.emit()
        self.submit('fan-calibration', worker, done)

    @Slot(int, int)
    def boost(self, percent, seconds):
        if not self.caps.get('fan_curve') or not 0 <= percent <= 100 or not 1 <= seconds <= 3600:
            return
        if self.app.demo:
            self._demo_boost_until = time.time() + seconds
            self._live['boost_seconds'] = seconds
            self._status = 'Demo boost started'
            self.stateChanged.emit()
            return
        target = self._target
        def worker():
            from ... import hardware as h, fancurve, config
            from ...profile_requests import hardware_apply_lock
            with hardware_apply_lock():
                if self._closed:
                    raise RuntimeError('Controller closed before operation')
                h.write_fan_boost(percent, seconds, target)
                state = h.read_fan_boost()
                if not state or not h.fan_boost_active(state) or state['pct'] != percent:
                    raise RuntimeError('Cannot record temporary boost; no curves were written')
                failures = []
                try:
                    for i, (ch, points) in enumerate(h.fan_boost_curves(percent).items()):
                        if i:
                            time.sleep(fancurve.CHANNEL_GAP_S)
                        ok, message = h.run_helper('fan', ch, *fancurve.curve_to_flat(points), timeout=30)
                        if not ok:
                            failures.append(ch + ': ' + str(message))
                    if failures:
                        raise RuntimeError('; '.join(failures))
                except Exception:
                    h.clear_fan_boost()
                    self._restore_saved(config.load_config())
                    raise
                return 'Temporary boost active; background enforcer restores curves at the deadline'
        self.submit('fan-boost', worker, self._operation_done)

    def _restore_saved(self, cfg):
        from ... import hardware as h, fancurve
        fans = cfg.get('profiles', {}).get(cfg.get('current_profile'), {}).get('fans', {})
        failures = []
        for i, ch in enumerate(ch for ch in h.FAN_CHANNELS if ch in fans):
            if i:
                time.sleep(fancurve.CHANNEL_GAP_S)
            try:
                ok, message = h.run_helper('fan', ch, *fancurve.curve_to_flat(fans[ch]), timeout=30)
                if not ok:
                    failures.append(ch + ': ' + str(message))
            except Exception as error:
                failures.append(ch + ': ' + str(error))
        if failures:
            raise RuntimeError('Curve restoration failed: ' + '; '.join(failures))

    @Slot()
    def stopBoost(self):
        if self.app.demo:
            self._demo_boost_until = 0
            self._live['boost_seconds'] = 0
            self._status = 'Demo boost stopped'
            self.stateChanged.emit()
            return
        def worker():
            from ... import config, hardware
            from ...profile_requests import hardware_apply_lock
            with hardware_apply_lock():
                if self._closed:
                    raise RuntimeError('Controller closed before operation')
                hardware.clear_fan_boost()
                self._restore_saved(config.load_config())
                return 'Fan boost stopped; current profile curves restored'
        self.submit('fan-boost-stop', worker, self._operation_done)

    def _operation_done(self, result, error):
        if self._closed:
            return
        self._status = str(error) if error else str(result)
        self.stateChanged.emit()
        self.refresh()
