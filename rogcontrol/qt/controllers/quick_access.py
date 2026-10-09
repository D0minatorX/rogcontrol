"""Saved automation choices, read through the shared fresh config state."""
from PySide6.QtCore import QObject, Property, Signal, Slot


class QuickAccessController(QObject):
    stateChanged = Signal()

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        app.configChanged.connect(self.stateChanged)

    settings = Property('QVariantMap', lambda s: {key: s.app.config.get(key) for key in
                        ('start_on_boot', 'ac_profile', 'battery_profile', 'usbc_profile', 'gamescope_profile')}, notify=stateChanged)

    @Slot()
    def refresh(self):
        self.app.refresh()
