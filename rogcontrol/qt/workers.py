"""Blocking work returns values; QML-facing callbacks run on the GUI thread."""
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot, Qt


class TaskSignals(QObject):
    finished = Signal(str, object, object)


class _Task(QRunnable):
    def __init__(self, job_id, function):
        super().__init__()
        self.job_id, self.function = job_id, function
        self.signals = TaskSignals()

    def run(self):
        result = error = None
        try:
            result = self.function()
        except Exception as exc:
            error = exc
        self.signals.finished.emit(self.job_id, result, error)


class TaskRunner(QObject):
    finished = Signal(str, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self._jobs = {}

    def active(self, job_id):
        return job_id in self._jobs

    def submit(self, job_id, function):
        if job_id in self._jobs:
            raise ValueError(f'Job already active: {job_id}')
        task = _Task(job_id, function)
        self._jobs[job_id] = task
        task.signals.finished.connect(self._complete, Qt.QueuedConnection)
        self.pool.start(task)

    @Slot(str, object, object)
    def _complete(self, job_id, result, error):
        self._jobs.pop(job_id, None)
        self.finished.emit(job_id, result, error)
