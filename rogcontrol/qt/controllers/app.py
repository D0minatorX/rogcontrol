"""Shared state and sole desktop write lane. All state changes use the GUI thread."""
from copy import deepcopy
from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer

PAGES = {'quick_access': 'Home', 'cpu': 'CPU', 'gpu': 'GPU', 'fans': 'Fans',
         'battery': 'Battery', 'keyboard': 'Lighting', 'system': 'System',
         'overview': 'Overview'}
DEMO_CONFIG = {'current_profile': 'Balanced Performance', 'appearance': 'dark',
               'profiles': {name: {} for name in ('Quiet', 'Balanced Power', 'Balanced Performance', 'Performance')},
               'start_on_boot': True, 'ac_profile': 'Balanced Performance',
               'battery_profile': 'Quiet', 'usbc_profile': None, 'gamescope_profile': None}


class AppController(QObject):
    stateChanged = Signal()
    errorOccurred = Signal(str)
    windowCommand = Signal(str)
    configChanged = Signal()

    def __init__(self, runner, demo=False, parent=None):
        super().__init__(parent)
        self.runner, self.demo = runner, demo
        self.config = deepcopy(DEMO_CONFIG) if demo else {}
        self._page, self._status, self._write = 'quick_access', '', None
        self._closed = False
        self._gpu_limits = None
        self._write_epoch = 0
        self._config_read_epoch = 0
        runner.finished.connect(self._complete)
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    currentPage = Property(str, lambda s: s._page, notify=stateChanged)
    pageTitle = Property(str, lambda s: PAGES[s._page], notify=stateChanged)
    activeProfile = Property(str, lambda s: s.config.get('current_profile', ''), notify=stateChanged)
    profileNames = Property('QStringList', lambda s: list(s.config.get('profiles', {})), notify=stateChanged)
    appearance = Property(str, lambda s: s.config.get('appearance', 'system'), notify=stateChanged)
    busy = Property(bool, lambda s: s._write is not None, notify=stateChanged)
    pendingChanges = Property(bool, lambda s: False, notify=stateChanged)
    statusText = Property(str, lambda s: s._status, notify=stateChanged)
    demoMode = Property(bool, lambda s: s.demo, constant=True)

    @Slot(str)
    def navigate(self, page_id):
        if page_id in PAGES:
            self._page = page_id
            self.stateChanged.emit()

    @Slot()
    def refresh(self):
        if self._closed or self.runner.active('config') or self.busy:
            return
        if self.demo:
            self.configChanged.emit()
            self.stateChanged.emit()
            return
        from ... import config
        self._config_read_epoch = self._write_epoch
        def load():
            if self._gpu_limits is None:
                from ... import hardware
                self._gpu_limits = hardware.detect_gpu_limits()
            low, high = self._gpu_limits['min_w'], self._gpu_limits['max_w']
            return config.load_config(gpu_min_w=low, gpu_max_w=high)
        self.runner.submit('config', load)

    def submitWrite(self, owner, function, callback=None):
        """Other controllers must use this lane for writes; return False while busy.

        Function runs in a worker and must reload/update fresh config through the
        backend transaction API. Physical writes must also hold
        profile_requests.hardware_apply_lock(): a queued profile apply outlives
        its submission job. Callback(result, error) runs on the GUI thread.
        """
        if self._closed or self._write is not None:
            return False
        self._write_epoch += 1
        self._write = (owner, callback)
        self._status = 'Working…'
        self.stateChanged.emit()
        self.runner.submit('write', function)
        return True

    @Slot(str)
    def selectProfile(self, name):
        if name not in self.profileNames:
            return
        if self.demo:
            self.config['current_profile'] = name
            self._status = 'Demo profile selected'
            self.configChanged.emit()
            self.stateChanged.emit()
            return
        from ...profile_requests import request_profile
        self.submitWrite('profile', lambda: request_profile(name, origin='decky'))

    @Slot()
    def applyChanges(self):
        self._status = 'Profile editing is available in the existing desktop interface.'
        self.stateChanged.emit()

    @Slot()
    def revertChanges(self):
        self.refresh()

    @Slot(str)
    def command(self, action):
        self.windowCommand.emit(action)

    @Slot(str, object, object)
    def _complete(self, job_id, result, error):
        if self._closed:
            return
        if job_id == 'config':
            if self._config_read_epoch != self._write_epoch or self.busy:
                self.refresh()
                return
            if error is None:
                self.config = result
                self.configChanged.emit()
                self.stateChanged.emit()
            else:
                self.errorOccurred.emit(str(error))
        elif job_id == 'write':
            owner, callback = self._write
            self._write = None
            self._status = str(error) if error else ('Profile request queued' if owner == 'profile' else 'Saved')
            self.stateChanged.emit()
            if error:
                self.errorOccurred.emit(str(error))
            if callback:
                callback(result, error)
            self.refresh()

    def close(self):
        self._closed = True
        self.timer.stop()
