"""Blocking work and bounded isolated jobs with main-thread completions."""
import threading
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot, Qt, QTimer


class TaskSignals(QObject):
    finished = Signal(str, object, object)


class _Task(QRunnable):
    def __init__(self, token, function):
        super().__init__()
        self.token, self.function = token, function
        self.signals = TaskSignals()

    def run(self):
        result = error = None
        try:
            result = self.function()
        except Exception as exc:
            error = exc
        try:
            self.signals.finished.emit(self.token, result, error)
        except RuntimeError:
            # A timed-out isolated job may finish after Qt has been disposed.
            pass


class TaskRunner(QObject):
    finished = Signal(str, object, object)

    def __init__(self, parent=None, isolated=False):
        super().__init__(parent)
        self.isolated = isolated
        self._closed = False
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(3)
        self._jobs = {}
        self._tokens = {}
        self._cancellations = {}
        self._serial = 0

    def active(self, job_id):
        return job_id in self._jobs

    def submit(self, job_id, function, timeout_ms=None, cancelled=None):
        if self._closed:
            return False
        if job_id in self._jobs:
            raise ValueError(f'Job already active: {job_id}')
        self._serial += 1
        token = str(self._serial)
        task = _Task(token, function)
        self._jobs[job_id] = task
        self._tokens[token] = job_id
        if cancelled is not None:
            self._cancellations[token] = cancelled
        task.signals.finished.connect(self._complete, Qt.QueuedConnection)
        if self.isolated:
            # DNS/network calls cannot consume hardware workers or hold quit.
            threading.Thread(target=task.run, daemon=True).start()
            timeout_ms = timeout_ms or (15000 if job_id.startswith('check-') else 135000)
            QTimer.singleShot(timeout_ms, lambda: self._timeout(token))
        else:
            self.pool.start(task)
        return True

    def _timeout(self, token):
        job_id = self._tokens.pop(token, None)
        if job_id is None or self._closed:
            return
        self._jobs.pop(job_id, None)
        event = self._cancellations.pop(token, None)
        if event is not None:
            event.set()
        self.finished.emit(job_id, None, TimeoutError('Operation timed out; retry.'))

    @Slot(str, object, object)
    def _complete(self, token, result, error):
        job_id = self._tokens.pop(token, None)
        if job_id is None:
            return  # A previous timed-out generation cannot overwrite a retry.
        self._jobs.pop(job_id, None)
        self._cancellations.pop(token, None)
        if not self._closed:
            self.finished.emit(job_id, result, error)

    def shutdown(self, timeout_ms=3000):
        """Cancel queued reads and bound waiting, retaining active cleanup.

        The global hardware pool outlives controllers. Already-started fan
        calibration retains its objects and restores curves in its finally
        block; Qt's pool termination waits for those workers. Network jobs
        use daemon threads and cannot extend that final wait.
        """
        self._closed = True
        for event in self._cancellations.values():
            event.set()
        if not self.isolated:
            for job_id, task in list(self._jobs.items()):
                if job_id != 'write' and self.pool.tryTake(task):
                    self._jobs.pop(job_id, None)
                    self._tokens.pop(task.token, None)
            self.pool.waitForDone(timeout_ms)
