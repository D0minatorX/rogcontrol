"""Battery override and internal-panel refresh controls."""
from PySide6.QtCore import Slot, QTimer
from .settings import SettingsController


class BatteryController(SettingsController):
    page = 'battery'

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        self._limit = None
        self._demo_once = None
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(300)
        self.debounce.timeout.connect(self._apply_limit)
        self.refresh()

    def fixture(self):
        return dict(percent=76, charging=True, source='AC power', limit=100 if self._demo_once else self.app.config.get('charge_limit', 80), once=self._demo_once,
                    health=dict(percent=102, full=91.8, design=90, unit='Wh', cycles=12),
                    charge_supported=True, usbc=True, display_supported=True,
                    display_reason='', rates=[60, 120, 165], current_rate=165)

    def sample(self):
        from ... import hardware, charge_once, display_refresh
        percent, charging = hardware.read_battery()
        supported, reason = display_refresh.availability()
        rates, current = [], None
        if supported:
            try:
                rates, current = display_refresh.get_refresh_rates()
            except ValueError as error:
                reason = str(error)
        limit = hardware.read_charge_limit()
        return dict(percent=percent, charging=charging, source=self._source_label(hardware.read_power_source()),
                    limit=limit, once=charge_once.read_state(), health=hardware.read_battery_health(),
                    charge_supported=limit is not None, usbc=True,
                    display_supported=supported, display_reason=reason, rates=rates, current_rate=current)

    @staticmethod
    def _source_label(source):
        connected, kind = source
        return 'USB-C power' if connected and kind == 'usb' else 'AC power' if connected else 'Battery' if connected is False else 'Unknown'

    @Slot(int)
    def setLimit(self, percent):
        if 0 <= percent <= 100 and (self.app.demo or self._data.get('charge_supported')):
            self._limit = percent
            self.debounce.start()

    def _apply_limit(self):
        if self._limit is None:
            return
        percent = self._limit
        if self.app.demo:
            self.persist(lambda cfg: cfg.update(charge_limit=percent))
            self._limit = None
            return
        def apply():
            from ... import charge_once
            return charge_once.set_limit(percent)
        if self.physical(apply):
            self._limit = None
        else:
            self.debounce.start()

    @Slot()
    def toggleOnce(self):
        if not self.app.demo and not self._data.get('charge_supported'):
            return
        active = bool(self._data.get('once'))
        if self.app.demo:
            self._demo_once = None if active else {'seen_ac': True}
            self.refresh()
            return
        def change():
            from ... import charge_once
            return charge_once.cancel() if active else charge_once.start()
        self.physical(change)

    @Slot(str, 'QVariant')
    def setDisplay(self, key, value):
        if key == 'auto_display_refresh':
            value = bool(value)
        elif key == 'battery_display_refresh_hz':
            value = float(value)
            if value not in self._data.get('rates', []):
                return
        else:
            return
        self.persist(lambda cfg: cfg.update({key: value}))

    @Slot(float)
    def setRefreshRate(self, rate):
        if rate not in self._data.get('rates', []):
            return
        def apply():
            from ... import display_refresh
            return display_refresh.set_refresh_rate(rate)
        self.physical(apply)

    def close(self):
        self.debounce.stop()
        super().close()
