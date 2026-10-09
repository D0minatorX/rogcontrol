"""Profile transactions, confirmations and native Qt file dialogs."""
from copy import deepcopy
import json
from pathlib import Path
from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl
from PySide6.QtWidgets import QFileDialog


class ProfileController(QObject):
    stateChanged = Signal()
    confirmationRequested = Signal(str, str)

    def __init__(self, app, pages, parent=None):
        super().__init__(parent)
        self.app, self.pages = app, pages
        self._closed, self._status = False, ''
        self._pending = None
        self._dialogs = []
        app.configChanged.connect(self.stateChanged)
        app.runner.finished.connect(self._read_done)
        for controller in pages.values():
            controller.stateChanged.connect(self.stateChanged)

    names = Property('QStringList', lambda s: s.app.profileNames, notify=stateChanged)
    currentProfile = Property(str, lambda s: s.app.activeProfile, notify=stateChanged)
    dirty = Property(bool, lambda s: any(p.dirty for p in s.pages.values()), notify=stateChanged)
    status = Property(str, lambda s: s._status, notify=stateChanged)
    stagedValues = Property('QVariantMap', lambda s: {name: deepcopy(page._values) for name, page in s.pages.items()}, notify=stateChanged)
    validationError = Property(str, lambda s: s._status, notify=stateChanged)

    @Slot(str, 'QVariant')
    def stageValue(self, path, value):
        parts = path.split('.', 1)
        if len(parts) == 2 and parts[0] in self.pages:
            self.pages[parts[0]].stageValue(parts[1], value)

    @Slot()
    def revert(self):
        for page in self.pages.values():
            page.revert()

    @Slot()
    def apply(self):
        page = self.pages.get(self.app.currentPage)
        if page and page.dirty:
            page.apply()
            return
        if self.dirty:
            self._status = 'Apply staged changes on each tuning page before applying the saved profile.'
            self.stateChanged.emit()
            return
        target = self.app.activeProfile
        saved_gpu = self.app.config.get('profiles', {}).get(target, {}).get('gpu', {})
        if saved_gpu.get('voltage_boost', 0) > 0:
            self._pending = ('profile-voltage', (target, saved_gpu['voltage_boost']))
            self.confirmationRequested.emit('profile-voltage', 'Apply this profile’s positive voltage boost? It is experimental and can increase heat and instability.')
            return
        self._apply_saved(target)

    def _apply_saved(self, target, confirmed_voltage=0):
        if self.app.demo:
            self._status = 'Demo profile applied'
            self.stateChanged.emit()
            return
        from .tuning import apply_saved_profile
        gpu_caps = deepcopy(self.pages['gpu'].caps)
        def worker():
            from ... import config, hardware as h
            from ...profile_requests import hardware_apply_lock
            with hardware_apply_lock():
                if self._closed:
                    raise RuntimeError('Controller closed before operation')
                fresh = config.load_config()
                if fresh.get('current_profile') != target:
                    raise RuntimeError('Active profile changed; refresh and retry')
                actual_voltage = fresh.get('profiles', {}).get(target, {}).get('gpu', {}).get('voltage_boost', 0)
                if actual_voltage > 0 and actual_voltage != confirmed_voltage:
                    raise RuntimeError('Profile voltage changed; confirm the current value before applying')
                caps = h.detect_capabilities(probe_gpu=False)
                caps.update(gpu_caps)
                return fresh, apply_saved_profile(fresh, caps)
        self._submit('profile-apply', worker, self._applied)

    def _applied(self, result, error):
        if self._closed:
            return
        if error:
            self._status = str(error)
        else:
            if any(message.startswith('GPU direct settings deferred:') for message in result[1]):
                self.pages['gpu']._reapply_after_probe = True
            self.app.config = result[0]
            self.app.configChanged.emit()
            self._status = '\n'.join(result[1]) or 'Saved profile applied'
        self.stateChanged.emit()

    def _submit(self, owner, worker, done):
        if not self.app.submitWrite(owner, worker, done):
            self._status = 'Another operation is running. Try again when it finishes.'
            self.stateChanged.emit()
            return False
        return True

    def _mutate(self, label, mutate, apply_replacement=False):
        if self.app.demo:
            try:
                result = mutate(self.app.config)
                self._status = label
                self.app.configChanged.emit()
                self.app.stateChanged.emit()
            except ValueError as error:
                self._status = str(error)
            self.stateChanged.emit()
            return
        gpu_caps = deepcopy(self.pages['gpu'].caps)
        def worker():
            from ... import config, hardware as h
            from ...profile_requests import hardware_apply_lock, selection_lock
            from .tuning import apply_saved_profile
            with hardware_apply_lock():
                if self._closed:
                    raise RuntimeError('Controller closed before operation')
                with selection_lock():
                    before = config.load_config().get('current_profile')
                    fresh = config.update_config(mutate)
                failures = []
                if apply_replacement and fresh.get('current_profile') != before:
                    caps = h.detect_capabilities(probe_gpu=False)
                    caps.update(gpu_caps)
                    failures = apply_saved_profile(fresh, caps)
                return fresh, failures
        def done(result, error):
            if self._closed:
                return
            if error:
                self._status = str(error)
            else:
                if any(message.startswith('GPU direct settings deferred:') for message in result[1]):
                    self.pages['gpu']._reapply_after_probe = True
                self.app.config = result[0]
                self.app.configChanged.emit()
                self.app.stateChanged.emit()
                self._status = '\n'.join(result[1]) or label
            self.stateChanged.emit()
        self._submit('profile-edit', worker, done)

    @Slot(str)
    def create(self, name):
        name = name.strip()
        if self.dirty:
            self._status = 'Apply or revert pending tuning changes before creating a profile.'
            self.stateChanged.emit()
            return
        if self.app.demo:
            def mutate(cfg):
                if not name or name in cfg['profiles']:
                    raise ValueError('Enter a unique profile name')
                cfg['profiles'][name] = deepcopy(cfg['profiles'][cfg['current_profile']])
                cfg['current_profile'] = name
        else:
            from ... import config
            mutate = lambda cfg: config.create_profile(cfg, name)
        self._mutate('Profile created as a copy of the current settings', mutate)

    @Slot(str)
    def duplicate(self, name):
        self.create(name)

    @Slot(str)
    def delete(self, name):
        if name not in self.app.profileNames or len(self.app.profileNames) <= 1:
            self._status = 'At least one profile must remain.'
            self.stateChanged.emit()
            return
        replacement = next(n for n in self.app.profileNames if n != name)
        voltage = self.app.config['profiles'][replacement].get('gpu', {}).get('voltage_boost', 0)
        self._pending = ('delete', (name, replacement, voltage))
        sources = [key for key in ('ac_profile', 'battery_profile', 'usbc_profile', 'gamescope_profile') if self.app.config.get(key) == name]
        body = 'Delete “' + name + '”? This cannot be undone.'
        if sources:
            body += ' Its automatic profile selections will be turned off: ' + ', '.join(sources) + '.'
        if name == self.app.activeProfile:
            body += ' The replacement profile “' + replacement + '” will be applied.'
            if voltage > 0:
                body += ' It includes experimental ' + str(voltage) + '% voltage boost, which can increase heat and instability.'
            if self.dirty:
                body += ' Pending tuning edits will be discarded.'
        self.confirmationRequested.emit('delete', body)

    def select(self, name):
        if name not in self.app.profileNames:
            return
        if self.app.config.get('profiles', {}).get(name, {}).get('gpu', {}).get('voltage_boost', 0) > 0:
            self._pending = ('select-voltage', (name, self.app.config['profiles'][name]['gpu']['voltage_boost']))
            self.confirmationRequested.emit('select-voltage', 'Switch to this profile and apply its positive voltage boost? It is experimental and can increase heat and instability.')
            return
        self._select_saved(name)

    def _select_saved(self, name, confirmed_voltage=0):
        def mutate(cfg):
            if name not in cfg.get('profiles', {}):
                raise ValueError('The selected profile no longer exists')
            voltage = cfg['profiles'][name].get('gpu', {}).get('voltage_boost', 0)
            if voltage > 0 and voltage != confirmed_voltage:
                raise ValueError('Profile voltage changed; confirm the current value before selecting')
            cfg['current_profile'] = name
        self._mutate('Profile selected and applied', mutate, True)

    def confirmSelection(self, name):
        self._pending = ('select', name)
        self.confirmationRequested.emit('select', 'Discard pending tuning changes and switch to “' + name + '”?')

    @Slot(str, bool)
    def confirm(self, action, accepted):
        pending, self._pending = self._pending, None
        if not accepted or not pending or pending[0] != action:
            return
        payload = pending[1]
        if action == 'profile-voltage':
            if payload[0] == self.app.activeProfile:
                self._apply_saved(*payload)
        elif action == 'select-voltage':
            self._select_saved(*payload)
        elif action == 'select':
            self.revert()
            self.app.selectProfile(payload)
        elif action == 'delete':
            payload, replacement, confirmed_voltage = payload
            if self.app.demo:
                def mutate(cfg):
                    if len(cfg['profiles']) <= 1 or payload not in cfg['profiles']:
                        raise ValueError('At least one profile must remain')
                    del cfg['profiles'][payload]
                    if cfg['current_profile'] == payload:
                        cfg['current_profile'] = next(iter(cfg['profiles']))
                    for key in ('ac_profile', 'battery_profile', 'usbc_profile', 'gamescope_profile'):
                        if cfg.get(key) == payload:
                            cfg[key] = None
            else:
                from ... import config
                def mutate(cfg):
                    # Preview destructive mutation before committing: a changed
                    # replacement voltage needs a new confirmation.
                    candidate = deepcopy(cfg)
                    config.delete_profile(candidate, payload)
                    if cfg.get('current_profile') == payload:
                        actual = candidate['current_profile']
                        voltage = candidate['profiles'][actual].get('gpu', {}).get('voltage_boost', 0)
                        if actual != replacement or (voltage > 0 and voltage != confirmed_voltage):
                            raise ValueError('Replacement profile changed; confirm deletion again')
                    config.delete_profile(cfg, payload)
            self._mutate('Profile deleted', mutate, True)
        elif action == 'restore':
            from ... import config
            self._mutate('Backup restored. Select or apply a profile to change hardware.', lambda cfg: config.restore_backup(cfg, payload))

    @Slot()
    def chooseImport(self):
        if self.app.demo:
            self._status = 'Demo uses fixture profiles; file imports are available outside demo.'
            self.stateChanged.emit()
            return
        self._choose(False)

    @Slot()
    def chooseExport(self):
        self._choose(True)

    def _choose(self, export):
        dialog = QFileDialog()
        dialog.setWindowTitle('Export complete backup' if export else 'Import profiles or backup')
        dialog.setNameFilters(['JSON files (*.json)', 'All files (*)'])
        dialog.setAcceptMode(QFileDialog.AcceptSave if export else QFileDialog.AcceptOpen)
        dialog.setFileMode(QFileDialog.AnyFile if export else QFileDialog.ExistingFile)
        if export:
            dialog.setDefaultSuffix('json')
            dialog.selectFile('rogcontrol-backup.json')
        dialog.fileSelected.connect(self.exportFile if export else self.importFile)
        self._dialogs.append(dialog)
        def finished(_result):
            if dialog in self._dialogs:
                self._dialogs.remove(dialog)
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()

    @staticmethod
    def _path(value):
        url = QUrl(value)
        return url.toLocalFile() if url.isLocalFile() else value

    @Slot(str)
    def importFile(self, path):
        if self.app.demo or self._closed:
            self._status = 'Demo uses fixture profiles; file imports are available outside demo.'
            self.stateChanged.emit()
            return
        if self.dirty:
            self._status = 'Apply or revert pending tuning changes before importing.'
            self.stateChanged.emit()
            return
        if self.app.runner.active('profile-import-read'):
            return
        path = self._path(path)
        def read():
            from ... import config
            with open(path, encoding='utf-8') as source:
                data = json.load(source)
            backup = config.is_backup_file(data)
            # Validate entire payload before displaying destructive confirmation.
            config.parse_backup(data) if backup else config.parse_import(data)
            return data, backup
        self.app.runner.submit('profile-import-read', read)

    @Slot(str, object, object)
    def _read_done(self, job_id, result, error):
        if self._closed or job_id != 'profile-import-read':
            return
        if error:
            self._status = 'Import failed: ' + str(error)
            self.stateChanged.emit()
            return
        data, backup = result
        if backup:
            self._pending = ('restore', data)
            self.confirmationRequested.emit('restore', 'Restore this full backup? Every profile and setting will be replaced. This cannot be undone. Hardware will be applied only when you request it.')
        else:
            from ... import config
            self._mutate('Profiles imported. Select one to apply.', lambda cfg: config.import_profiles(cfg, data))

    @Slot(str)
    def exportFile(self, path):
        if self._closed:
            return
        path = self._path(path)
        fixture = deepcopy(self.app.config) if self.app.demo else None
        def worker():
            if fixture is not None:
                payload = deepcopy(fixture)
                payload['rogcontrol_backup_version'] = 1
            else:
                from ... import config
                payload = config.export_backup(config.load_config())
            with open(path, 'w', encoding='utf-8') as target:
                json.dump(payload, target, indent=2)
                target.write('\n')
            return 'Complete backup exported to ' + Path(path).name
        def done(result, error):
            if not self._closed:
                self._status = str(error) if error else result
                self.stateChanged.emit()
        self._submit('profile-export', worker, done)

    def close(self):
        self._closed = True
        self._pending = None
        for dialog in self._dialogs[:]:
            dialog.reject()
