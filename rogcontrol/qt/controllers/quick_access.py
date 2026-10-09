"""Editable automatic switching and startup; choices never apply a profile."""
from copy import deepcopy
from PySide6.QtCore import Slot, Property
from .settings import SettingsController


class QuickAccessController(SettingsController):
    page = 'quick_access'

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        self.refresh()

    def fixture(self):
        return {'startup': True, 'gamescope': True, 'usbc': True}

    def sample(self):
        from ... import startup, gamescope
        return {'startup': startup.is_enabled(), 'gamescope': gamescope.detect_installed(),
                'usbc': True}

    @Slot(str, str)
    def setAutomaticProfile(self, source, name):
        if source not in ('ac_profile', 'battery_profile', 'usbc_profile', 'gamescope_profile'):
            return
        if name and name not in self.app.profileNames:
            return
        def change(cfg):
            if name and name not in cfg.get('profiles', {}):
                raise ValueError('Profile was deleted; choose another profile.')
            cfg[source] = name or None
        self.persist(change)

    @Slot(bool)
    def setStartup(self, enabled):
        if self.app.demo:
            self.persist(lambda cfg: cfg.update(start_on_boot=enabled))
            return
        def change():
            from ... import startup
            startup.set_enabled(enabled)
        self.write(change)

    powerMizerChoices = Property('QVariantList', lambda s: s._power_choices(), notify=SettingsController.stateChanged)
    powerMizerLabels = Property('QStringList', lambda s: [{0: 'Adaptive', 1: 'Prefer Maximum Performance', 2: 'Driver controlled / Auto', 3: 'Prefer Consistent Performance'}.get(v, str(v)) for v in s._power_choices()], notify=SettingsController.stateChanged)

    def _power_choices(self):
        page = getattr(self.app, 'tuningPages', {}).get('gpu')
        return next((r.get('choices', []) for r in page.rows if r['key'] == 'powermizer_mode' and r['enabled']), []) if page else []

    @Slot(bool)
    def setCpuBoost(self, enabled):
        self._quick_setting('cpu', 'boost', enabled)

    @Slot(int)
    def setPowerMizer(self, mode):
        if mode in self._power_choices():
            self._quick_setting('gpu', 'powermizer_mode', mode)

    def _quick_setting(self, section, key, value):
        page = self.app.tuningPages[section]
        if not page.ready or not any(r['key'] == key and r['enabled'] for r in page.rows):
            return
        target = self.app.activeProfile
        caps = deepcopy(page.caps)
        if self.app.demo:
            self.persist(lambda cfg: cfg['profiles'][target].setdefault(section, {}).update({key: value}))
            return
        def apply():
            from ... import config
            from ...profile_requests import hardware_apply_lock, selection_lock
            with hardware_apply_lock():
                if self._closed:
                    return None
                fresh = config.load_config()
                status, profile = config.deferred_save_target(fresh, target)
                if status != config.SAVE_OK:
                    raise RuntimeError(config.deferred_save_refusal(status, target, 'Quick control', 'the hardware'))
                saved = profile.get(section, {})
                snapshot = {key: value}
                if section == 'cpu':
                    snapshot.update({k: saved.get(k, 0) for k in ('max_freq', 'min_freq')})
                    snapshot['limits_enabled'] = saved.get('limits_enabled', True)
                baseline = {k: saved.get(k) for k in snapshot}
                successful, failures = page.write(snapshot, baseline, caps)
                with selection_lock():
                    latest = config.load_config()
                    refusal = config.save_deferred(latest, target, section, successful, 'Quick control')
                if refusal or failures:
                    raise RuntimeError(refusal or '; '.join(failures))
        self.write(apply)
