"""Read-only dashboard sampling, including the sleeping NVIDIA guard."""
import shutil
from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer


class OverviewController(QObject):
    stateChanged = Signal()

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._data = {}
        self._closed = False
        self._sample_error = ''
        self._consecutive_failures = 0
        app.configChanged.connect(self.stateChanged)
        app.runner.finished.connect(self._complete)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    data = Property('QVariantMap', lambda s: s._data, notify=stateChanged)

    layout = Property(str, lambda s: s.app.config.get('overview_layout', 'list'), notify=stateChanged)

    sampleError = Property(str, lambda s: s._sample_error, notify=stateChanged)

    @Slot()
    def refresh(self):
        if self._closed or self.app.runner.active('overview'):
            return
        if self.app.currentPage not in ('quick_access', 'overview'):
            return
        if self.app.demo:
            self._data = {'cpu_name': 'AMD Ryzen 9 8940HX', 'gpu_name': 'NVIDIA GeForce RTX 5070 Ti',
                          'cpu_temp': 58, 'cpu_clock': 3650, 'cpu_power': 42,
                          'gpu_temp': 51, 'gpu_clock': 1620, 'gpu_power': 36,
                          'fan_rpm': {'1': 2400, '2': 2200, '3': 1900},
                          'battery': [80, False], 'charge_limit': 80, 'power_source': [True, 'ac'],
                          'ram': [12400, 32000], 'vram': [2200, 12000], 'curve_enabled': {'1': True, '2': True, '3': True}}
            self.stateChanged.emit()
            return
        self.app.runner.submit('overview', self._sample)

    @staticmethod
    def _sample():
        from ... import hardware as h
        suspended = h.dgpu_is_suspended() if shutil.which('nvidia-smi') else True
        gpu = (None,) * 4 if suspended else h.read_nvidia_query(('temperature.gpu', 'power.draw', 'clocks.gr', 'utilization.gpu'))
        return {'cpu_name': h.read_cpu_name() or 'Processor', 'gpu_name': 'NVIDIA GPU' if shutil.which('nvidia-smi') else 'Graphics',
                'cpu_temp': h.read_cpu_temp(), 'cpu_clock': h.read_peak_core_clock_mhz(),
                'cpu_power': h.read_cpu_power_w(), 'gpu_temp': gpu[0], 'gpu_power': gpu[1],
                'gpu_clock': gpu[2], 'gpu_usage': gpu[3], 'dgpu_suspended': suspended,
                'fan_rpm': h.read_fan_rpms(), 'battery': h.read_battery(),
                'power_source': h.read_power_source(), 'charge_limit': h.read_charge_limit(),
                'ram': h.read_memory(), 'vram': (None, None) if suspended else h.read_vram(),
                'curve_enabled': h.read_fan_curve_enabled()}

    @Slot(str, object, object)
    def _complete(self, job_id, result, error):
        if job_id != 'overview' or self._closed:
            return
        if error:
            self._consecutive_failures += 1
            self._sample_error = 'Readings are stale: ' + str(error)
            # Match SampleFailures' five-frame threshold without importing
            # its hardware dependency into the fixture-only demo path.
            if self._consecutive_failures == 5:
                self.app.errorOccurred.emit('Monitoring readings have stopped updating: ' + str(error))
        else:
            self._consecutive_failures = 0
            self._sample_error = ''
            self._data = result
        self.stateChanged.emit()

    def close(self):
        self._closed = True
        self.timer.stop()
