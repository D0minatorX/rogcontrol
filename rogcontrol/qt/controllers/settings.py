"""Immediate settings bridge with fresh transactions and serialized mutations."""
from copy import deepcopy
from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer


class SettingsController(QObject):
    stateChanged = Signal()
    confirmationRequested = Signal(str, str)
    page = ''

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._closed = False
        self._data = {}
        self._status = ''
        self._read_epoch = 0
        app.configChanged.connect(self.reload)
        app.stateChanged.connect(self.refresh)
        app.runner.finished.connect(self._read_done)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    data = Property('QVariantMap', lambda s: deepcopy(s._data), notify=stateChanged)
    status = Property(str, lambda s: s._status, notify=stateChanged)
    settings = Property('QVariantMap', lambda s: deepcopy(s.app.config), notify=stateChanged)

    @Slot()
    def reload(self):
        if not self._closed:
            self.stateChanged.emit()
            self.refresh()

    @Slot()
    def refresh(self):
        job = 'settings-' + self.page
        if self._closed or self.app.busy or self.app.runner.active(job):
            return
        if self.app.currentPage not in (self.page, 'quick_access'):
            return
        if self.app.demo:
            self._data = self.fixture()
            self.stateChanged.emit()
            return
        self._read_epoch = self.app._write_epoch
        self.app.runner.submit(job, self.sample)

    def fixture(self):
        return {}

    def sample(self):
        return {}

    @Slot(str, object, object)
    def _read_done(self, job, result, error):
        if self._closed or job != 'settings-' + self.page:
            return
        if self._read_epoch != self.app._write_epoch or self.app.busy:
            self.refresh()
            return
        if error:
            self._status = str(error)
        else:
            self._data = result
        self.stateChanged.emit()

    def write(self, function, done=None, *, silent=False):
        def complete(result, error):
            if self._closed:
                return
            self._status = str(error) if error else (self._status if silent else 'Saved')
            if done:
                done(result, error)
            self.stateChanged.emit()
            self.refresh()
        if not self.app.submitWrite('background' if silent else self.page, function, complete):
            self._status = 'Another operation is running; retry when it finishes.'
            self.stateChanged.emit()
            return False
        return True

    def persist(self, mutate, *, silent=False):
        if self.app.demo:
            mutate(self.app.config)
            self.app.configChanged.emit()
            return True
        def save():
            from ... import config
            from ...profile_requests import selection_lock
            with selection_lock():
                config.update_config(mutate)
        return self.write(save, silent=silent)

    def physical(self, operation, mutate=None, done=None):
        if self.app.demo:
            if mutate:
                mutate(self.app.config)
            self._status = 'Demo setting changed'
            self.app.configChanged.emit()
            if done:
                done((True, self._status), None)
            self.stateChanged.emit()
            return True
        def apply():
            from ... import config
            from ...profile_requests import hardware_apply_lock, selection_lock
            with hardware_apply_lock():
                if self._closed:
                    return None
                result = operation()
                if result is not None and not result[0]:
                    raise RuntimeError(result[1])
                if mutate:
                    with selection_lock():
                        config.update_config(mutate)
                return result
        return self.write(apply, done)

    def close(self):
        self._closed = True
        self.timer.stop()
