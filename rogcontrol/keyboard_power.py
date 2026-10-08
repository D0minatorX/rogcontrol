"""Saved lighting power policies, including a real battery awake override."""
import time
from . import config, hardware

ZONES = ('keyboard', 'lightbar')
STATES = ('boot', 'awake', 'sleep', 'shutdown', 'battery')


def preferences(cfg):
    """Editable defaults only; they do not imply firmware state or opt-in."""
    stored = cfg.get('keyboard_power')
    stored = stored if isinstance(stored, dict) else {}
    result = {'enabled': stored.get('enabled') is True}
    for zone in ZONES:
        values = stored.get(zone)
        values = values if isinstance(values, dict) else {}
        result[zone] = {key: values[key] if type(values.get(key)) is bool else True
                        for key in STATES}
    return result


def desired_masks(cfg, ac):
    stored = cfg.get('keyboard_power')
    if ac is not True and ac is not False:
        return None
    if not isinstance(stored, dict) or stored.get('enabled') is not True:
        return None
    # Corrupt/imported partial policies must not silently enable unchosen zones.
    for zone in ZONES:
        if (not isinstance(stored.get(zone), dict)
                or any(type(stored[zone].get(key)) is not bool for key in STATES)):
            return None
    return tuple(sum(1 << bit for bit, key in enumerate(STATES[:4])
                     if stored[zone]['battery' if key == 'awake' and not ac else key])
                 for zone in ZONES)


def supported():
    from . import aura_power
    return aura_power.detect_device() is not None


def write_masks(keyboard, lightbar):
    return hardware.run_helper('kbdpower', keyboard, lightbar, timeout=10)


class Controller:
    """Reconcile on source/settings changes, retry errors, recover firmware resets."""
    def __init__(self, write=None, supported=None, reconnect_grace=0):
        self.write = write or write_masks
        self.supported = supported or globals()['supported']
        self.last = None
        self.applied_at = 0
        self.reconnect_grace = reconnect_grace
        self.unavailable_since = None

    def tick(self, cfg, ac, now=None):
        masks = desired_masks(cfg, ac)
        if masks is None:
            self.last = None
            self.unavailable_since = None
            return True, 'Lighting power management is off or awaiting valid settings/power source.'
        now = time.monotonic() if now is None else now
        # A reconnect can reset firmware even when the desired masks are unchanged.
        # Probe before the cache check so recovery reapplies the saved policy.
        if not self.supported():
            self.last = None
            if self.unavailable_since is None:
                self.unavailable_since = now
            if now - self.unavailable_since < self.reconnect_grace:
                return True, 'Waiting for the ASUS lighting interface to reconnect.'
            return False, ('ASUS lighting interface unavailable; retrying. '
                           'Power zones require the supported G614PR keyboard.')
        self.unavailable_since = None
        if masks == self.last and now - self.applied_at < 60:
            return True, 'Saved lighting policy already sent.'
        ok, message = self.write(*masks)
        if ok:
            self.last = masks
            self.applied_at = now
            return True, 'Lighting power settings sent; firmware readback is unavailable.'
        return False, message


def apply_saved():
    """UI uses fresh disk preferences and live AC state, not a stale snapshot."""
    return Controller().tick(config.load_config(), hardware.is_ac_connected())
