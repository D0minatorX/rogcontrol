"""CPU controls use the canonical ordered backend plan and exact stored units."""
from glob import glob
from PySide6.QtCore import Slot
from .tuning import TuningController, number, toggle


class CpuController(TuningController):
    section = 'cpu'

    def demo_caps(self):
        return {'ryzenadj': True, 'cpu_power_limits': 'ryzenadj', 'cpu_boost': True,
                'cpu_epp': ['performance', 'balance_performance', 'balance_power', 'power'],
                'cpu_clock': [400000, 5400000], 'cpu_clock_floor': 400000, 'fw_power_reset': True}

    def demo_live(self):
        return {'temperature': '58 °C', 'clock': '3650 MHz', 'power': '42 W', 'fan': '2400 RPM', 'governor': 'powersave'}

    def sample(self, caps):
        from ... import hardware as h
        governors = sorted({value for path in glob('/sys/devices/system/cpu/cpufreq/policy*/scaling_governor') if (value := h.read_file(path))})
        return {'temperature': h.read_cpu_temp(), 'clock': h.read_peak_core_clock_mhz(),
                'power': h.read_cpu_power_w(), 'fan': h.read_fan_rpms().get('1'),
                'vendor': caps.get('cpu_vendor', ''), 'governor': ', '.join(governors) or 'unavailable'}

    def build_rows(self, caps):
        rows = []
        backend = caps.get('cpu_power_limits') or ('ryzenadj' if caps.get('ryzenadj') else None)
        if backend:
            rows.append(toggle('limits_enabled', 'Override firmware power limits'))
        if backend == 'ryzenadj':
            rows += [number('stapm', 'Sustained power', 15, 150, 55000, 'W', 1000),
                     number('fast', 'Short boost power', 15, 165, 65000, 'W', 1000),
                     number('slow', 'Long boost power', 15, 150, 55000, 'W', 1000),
                     number('temp', 'Temperature target', 60, 100, 90, '°C'),
                     number('coall', 'Curve Optimizer', -30, 0, 0)]
        elif backend in ('ppt', 'rapl'):
            rows += [number('pl1', 'Sustained power (PL1)', 5, 150, 55, 'W'),
                     number('pl2', 'Boost power (PL2)', 5, 150, 65, 'W')]
        if caps.get('cpu_boost'):
            rows.append(toggle('boost', 'Turbo boost'))
        if caps.get('cpu_epp'):
            rows.append(dict(key='epp', title='Energy preference', kind='choice',
                             choices=caps['cpu_epp'], default=caps['cpu_epp'][0]))
        clock = caps.get('cpu_clock')
        if clock:
            rows.append(number('max_freq', 'Clock ceiling (0 = unlimited)', 0, clock[1] / 1e6, 0, 'GHz', 1000000, .1))
        if caps.get('cpu_clock_floor') and clock:
            rows.append(number('min_freq', 'Clock floor (0 = automatic)', 0, clock[1] / 1e6, 0, 'GHz', 1000000, .1))
        return rows

    def row_enabled(self, row):
        key = row['key']
        if key in ('stapm', 'fast', 'slow', 'temp', 'coall', 'pl1', 'pl2'):
            if key == 'coall' and self.app.config.get('safety_tripped'):
                return False
            return self._values.get('limits_enabled', True) is not False
        return key != 'max_freq' or not self.caps.get('cpu_boost') or self._values.get('boost', True)

    def couple(self, key):
        clock = self.caps.get('cpu_clock')
        if clock and key in ('max_freq', 'min_freq'):
            minimum = self.caps.get('cpu_clock_floor', clock[0]) if key == 'min_freq' else clock[0]
            if self._values.get(key, 0) and self._values[key] < minimum:
                self._values[key] = minimum
        low, high = self._values.get('min_freq', 0), self._values.get('max_freq', 0)
        if low and high and low > high and self._values.get('boost', True):
            self._values['max_freq' if key == 'min_freq' else 'min_freq'] = low if key == 'min_freq' else high

    def write(self, snapshot, baseline, caps):
        from ... import hardware as h, config
        values = {r['key']: snapshot[r['key']] for r in self.build_rows(caps) if r['key'] in snapshot}
        if config.load_config().get('safety_tripped'):
            values['coall'] = 0
        if values.get('min_freq') and values.get('max_freq') and values.get('boost', True):
            values['min_freq'] = min(values['min_freq'], values['max_freq'])
        if baseline.get('limits_enabled', True) is not False and values.get('limits_enabled') is False:
            values['reset_to_firmware'] = True
        plan = h.cpu_apply_plan(values, caps)
        mapping = {'limits': ('stapm', 'fast', 'slow', 'temp', 'coall') if caps.get('ryzenadj') else ('pl1', 'pl2'),
                   'boost': ('boost',), 'epp': ('epp',), 'clock': ('max_freq',), 'minclock': ('min_freq',), 'fwreset': ()}
        saved, failed = {}, []
        for step, args in plan:
            try:
                ok, message = h.run_helper(*args)
            except Exception as error:
                ok, message = False, str(error)
            if ok:
                saved.update({key: values[key] for key in mapping[step] if key in values})
            else:
                failed.append(step + ': ' + str(message))
        if 'limits_enabled' in values and values['limits_enabled'] != baseline.get('limits_enabled', True):
            saved['limits_enabled'] = values['limits_enabled']
        self._reset_took = any(step == 'fwreset' for step, _ in plan) and not any(s.startswith('fwreset:') for s in failed)
        return saved, failed

    def after_write(self, snapshot, successful, caps, fresh):
        if getattr(self, '_reset_took', False):
            from .tuning import apply_saved_profile
            return apply_saved_profile(fresh, caps, self.progressChanged.emit)
        return []

    @Slot()
    def recoverSafety(self):
        self.confirmationRequested.emit('safety', 'Enable Curve Optimizer editing after the boot failure safeguard? The currently blocked offset will stay at stock until you explicitly change and apply it.')

    @Slot(str, bool)
    def confirm(self, action, accepted):
        if action != 'safety' or not accepted:
            return
        if self.app.demo:
            self.app.config['safety_tripped'] = False
            self._values['coall'] = 0
            self.app.configChanged.emit()
            return
        def worker():
            from ... import config
            from ...profile_requests import hardware_apply_lock, selection_lock
            with hardware_apply_lock(), selection_lock():
                if self._closed:
                    raise RuntimeError('Controller closed before operation')
                def mutate(cfg):
                    config.clear_safety_trip(cfg)
                    name = cfg.get('current_profile')
                    cfg.get('profiles', {}).get(name, {}).setdefault('cpu', {})['coall'] = 0
                return config.update_config(mutate)
        def done(result, error):
            if self._closed:
                return
            if error:
                self._status = str(error)
            else:
                self._values['coall'] = 0
                self.app.config = result
                self.app.configChanged.emit()
                self._status = 'Curve Optimizer enabled at stock'
            self.stateChanged.emit()
        self.submit('cpu-safety', worker, done)
