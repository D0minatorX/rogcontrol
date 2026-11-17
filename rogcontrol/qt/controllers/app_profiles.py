"""Edit executable profiles without staging changes on the global tuning pages."""
import os
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QObject, Property, QSize, Signal, Slot
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtWidgets import QFileDialog

from ... import app_profiles, config, fancurve


class AppIconProvider(QQuickImageProvider):
    """image://appicon/<executable name>: its theme icon, else a generic executable icon."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Pixmap)

    def requestPixmap(self, name, size, requested):
        edge = requested.width() if requested.width() > 0 else 48
        icon = QIcon.fromTheme(name)
        if icon.isNull():
            icon = QIcon.fromTheme('application-x-executable')
        size.setWidth(edge)
        size.setHeight(edge)
        # A null pixmap makes the QML Image report an error, which shows its glyph fallback.
        return icon.pixmap(QSize(edge, edge)) if not icon.isNull() else QPixmap()


class AppProfilesController(QObject):
    changed = Signal()
    picked = Signal()  # new executable chosen; page opens its editor

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._path = ""
        self._draft = {}
        self._dialog = None
        app.configChanged.connect(self.changed.emit)
        for page in app.tuningPages.values():
            page.stateChanged.connect(self.changed.emit)

    paths = Property('QStringList', lambda s: sorted(app_profiles.entries(s.app.config)), notify=changed)
    apps = Property('QVariantList', lambda s: [{'path': p, 'name': os.path.basename(p)}
                                               for p in sorted(app_profiles.entries(s.app.config))], notify=changed)
    path = Property(str, lambda s: s._path, notify=changed)
    rows = Property('QVariantList', lambda s: s._rows(), notify=changed)

    def _rows(self):
        base = app_profiles.effective_profile(self.app.config, [])
        rows = []
        for section, page in self.app.tuningPages.items():
            baseline = base.get(section, {})
            values = self._draft.get(section, {})
            controls = (page.channels if section == 'fans' else page.rows)
            for control in controls:
                key = control['key']
                if key not in app_profiles.FIELDS[section]:
                    continue
                saved = key in values
                inherited = app_profiles.baseline_value(section, key, baseline)
                available = self.app.demo or (inherited is not None and (
                    section != 'cpu' or ((key != 'max_freq' or baseline.get('boost') is not False) and
                    (key in ('boost', 'max_freq', 'min_freq') or baseline.get('limits_enabled') is not False))))
                if section == 'gpu' and key == 'clock_limit' and inherited == 0:
                    inherited = round(control['max'] * control.get('scale', 1))
                value = values.get(key, inherited if inherited is not None else control.get('points', control.get('default', 0)))
                rows.append(dict(control, section=section, value=value, overridden=saved,
                                 available=available))
        return rows

    @Slot()
    def choose(self):
        if self._dialog is not None or self.app.busy:
            return
        dialog = QFileDialog()
        self._dialog = dialog
        dialog.setWindowTitle('Select an executable')
        dialog.setFileMode(QFileDialog.ExistingFile)
        dialog.fileSelected.connect(self.selectExecutable)
        def finished(_):
            self._dialog = None
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()

    @Slot(str)
    def selectExecutable(self, filename):
        path = os.path.realpath(filename)
        if not Path(path).is_file() or not os.access(path, os.X_OK):
            self.app.errorOccurred.emit('Select a file with execute permission.')
            return
        self.select(path)
        self.picked.emit()

    @Slot(str)
    def select(self, path):
        if self.app.busy:
            return
        self._path = path
        self._draft = deepcopy(app_profiles.entries(self.app.config).get(path, {}))
        if not isinstance(self._draft, dict):
            self._draft = {}
        self.changed.emit()

    @Slot(str, str, bool)
    def toggle(self, section, key, enabled):
        row = next((row for row in self._rows()
                    if (row['section'], row['key']) == (section, key)), None)
        if not row or self.app.busy:
            return
        values = self._draft.setdefault(section, {})
        if enabled and row['available']:
            values[key] = deepcopy(row['value'])
        elif not enabled:
            values.pop(key, None)
        self.changed.emit()

    @Slot(str, str, str)
    def setMode(self, section, key, mode):
        """Boolean override: mode is 'inherit', 'on' or 'off'."""
        row = next((row for row in self._rows()
                    if (row['section'], row['key']) == (section, key)), None)
        if not row or row['kind'] != 'bool' or mode not in ('inherit', 'on', 'off') or self.app.busy:
            return
        values = self._draft.setdefault(section, {})
        if mode == 'inherit':
            values.pop(key, None)
        elif row['available']:
            values[key] = mode == 'on'
        self.changed.emit()

    @Slot(str, str, float)
    def stage(self, section, key, value):
        row = next((row for row in self._rows()
                    if (row['section'], row['key']) == (section, key)), None)
        if not row or not row['overridden'] or not row['available'] or self.app.busy:
            return
        if row['min'] <= value <= row['max']:
            if section == 'cpu' and value and key in ('max_freq', 'min_freq'):
                cpu = self.app.tuningPages['cpu'].caps
                minimum = (cpu['cpu_clock_floor'] if key == 'min_freq' else cpu['cpu_clock'][0])
                if value * row['scale'] < minimum:
                    self.app.errorOccurred.emit(f"Use 0 or a clock of at least {minimum / row['scale']:g} GHz.")
                    return
            self._draft[section][key] = round(value * row.get('scale', 1))
            self.changed.emit()

    @Slot(str, int, float, float)
    def movePoint(self, key, index, temperature, percent):
        points = self._draft.get('fans', {}).get(key)
        if points and 0 <= index < len(points) and 0 <= temperature <= 100 and 0 <= percent <= 100:
            self._draft['fans'][key] = fancurve.move_point(points, index, temperature, percent)
            self.changed.emit()

    @Slot(str, float, float, result=int)
    def addPoint(self, key, temperature, percent):
        points = self._draft.get('fans', {}).get(key)
        if not points or len(points) >= 8 or not 0 <= temperature <= 100 or not 0 <= percent <= 100:
            return -1
        temperature, percent = round(temperature), round(percent)
        if any(point[0] == temperature for point in points):
            return -1
        index = next((i for i, point in enumerate(points) if point[0] > temperature), len(points))
        points.insert(index, [temperature, percent])
        self.changed.emit()
        return index

    @Slot(str, int)
    def removePoint(self, key, index):
        points = self._draft.get('fans', {}).get(key)
        if points and len(points) > 2 and 0 <= index < len(points):
            points.pop(index)
            self.changed.emit()

    @Slot()
    def save(self):
        self._persist(False)

    @Slot()
    def remove(self):
        self._persist(True)

    def _persist(self, remove):
        path = self._path
        if not path or self.app.busy:
            return
        draft = {section: {key: deepcopy(value) for key, value in values.items()
                           if key in app_profiles.FIELDS[section] and app_profiles.valid_value(section, value, key)}
                 for section, values in self._draft.items()
                 if section in app_profiles.FIELDS and isinstance(values, dict) and values}
        def mutate(cfg):
            profiles = cfg['app_profiles'] = dict(app_profiles.entries(cfg))
            if remove:
                profiles.pop(path, None)
            else:
                profiles[path] = draft

        def done(result, error):
            if error is None:
                self.app.config = result
                self.select('' if remove else path)
                self.app.configChanged.emit()

        if self.app.demo:
            cfg = deepcopy(self.app.config)
            mutate(cfg)
            done(cfg, None)
        else:
            def worker():
                from ...profile_requests import selection_lock
                with selection_lock():
                    return config.update_config(mutate)
            self.app.submitWrite('app-profiles', worker, done)
