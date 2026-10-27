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
        app.stateChanged.connect(self.refresh)
        app.runner.finished.connect(self._complete)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
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
        if self.app.currentPage != 'quick_access':
            return
        if not self.app.live_readings:
            self._data = {'cpu_name': 'AMD Ryzen 9 8940HX', 'gpu_name': 'NVIDIA GeForce RTX 5070 Ti',
                          'cpu_temp': 58, 'cpu_clock': 3650, 'cpu_power': 42,
                          'gpu_temp': 51, 'gpu_clock': 1620, 'gpu_power': 36,
                          'gpu_usage': 42,
                          'fan_rpm': {'1': 2400, '2': 2200, '3': 1900},
                          'battery': [80, False], 'charge_limit': 80, 'power_source': [True, 'ac'],
                          'ram': [12400, 32000], 'vram': [2200, 12000], 'curve_enabled': {'1': True, '2': True, '3': True}}
            self.stateChanged.emit()
            return
        limits = getattr(self.app, '_gpu_limits', None) or {}
        gpu_name = limits.get('name') or self._data.get('gpu_name')
        self.app.runner.submit('overview', lambda: self._sample(gpu_name))

    @staticmethod
    def _sample(gpu_name=None):
        from ... import hardware as h
        suspended = h.dgpu_is_suspended() if shutil.which('nvidia-smi') else True
        gpu = (None,) * 4 if suspended else h.read_nvidia_query(('temperature.gpu', 'power.draw', 'clocks.gr', 'utilization.gpu'))
        ram = h.read_memory()
        vram = (None, None) if suspended else h.read_vram()
        return {'cpu_name': h.read_cpu_name() or 'Processor', 'gpu_name': h.read_gpu_name() or gpu_name or ('NVIDIA GPU' if shutil.which('nvidia-smi') else 'Graphics'),
                'cpu_temp': h.read_cpu_temp(), 'cpu_clock': h.read_peak_core_clock_mhz(),
                'cpu_power': h.read_cpu_power_w(), 'gpu_temp': gpu[0], 'gpu_power': gpu[1],
                'gpu_clock': gpu[2], 'gpu_usage': gpu[3], 'dgpu_suspended': suspended,
                'fan_rpm': h.read_fan_rpms(), 'battery': h.read_battery(),
                'power_source': h.read_power_source(), 'charge_limit': h.read_charge_limit(),
                # Convert tuples returned by the readers to plain lists for
                # QML's QVariantList conversion and indexed display.
                'ram': list(ram) if ram is not None else [None, None],
                'vram': list(vram) if vram is not None else [None, None],
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
