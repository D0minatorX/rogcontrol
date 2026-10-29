"""Isolated release/download jobs; installs use the shared write lane."""
from copy import deepcopy
from pathlib import Path
import os
import threading
import time
from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer
from ..workers import TaskRunner


class UpdatesController(QObject):
    stateChanged = Signal()
    progress = Signal(str, int, int, int)
    confirmationRequested = Signal(str, str)

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.runner = TaskRunner(self, isolated=True)
        self.runner.finished.connect(self._complete)
        self._closed = False
        self._cancel = threading.Event()
        self._states = {name: {'status': 'Not checked', 'busy': False, 'available': False,
                              'progress': 0, 'version': '', 'stage': ''} for name in ('app', 'decky')}
        self._release = {}
        self._poll = {}
        self._installing = set()
        self._download_serial = 0
        self._download_generations = {}
        self._confirmation = None
        self._launch_checked = False
        self.progress.connect(self._progress)
        app.configChanged.connect(self._automatic)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    data = Property('QVariantMap', lambda s: deepcopy(s._states), notify=stateChanged)

    def _state(self, kind, **values):
        self._states[kind].update(values)
        self.stateChanged.emit()

    @Slot(str)
    def check(self, kind):
        if self._closed or kind not in self._states or self._states[kind]['busy']:
            return
        if self.app.demo:
            self._state(kind, status='Demo release v9.0 available', available=True, version='9.0', stage='fixture')
            return
        self._cleanup_stage(kind)
        self._state(kind, busy=True, status='Checking releases…', progress=0, stage='')
        def check():
            from ... import updater, decky_install, decky_release
            if kind == 'app':
                release = updater.check_for_update()
            else:
                installed = decky_install.detect_installation()
                release = decky_release.check_for_update(installed.get('version'))
                release['installation'] = installed
            return release
        self.runner.submit('check-' + kind, check)

    @Slot(str)
    def download(self, kind):
        if self._closed or kind not in self._states or self._states[kind]['busy']:
            return
        if self.app.demo:
            self._state(kind, status='Demo download staged', stage='fixture', progress=100)
            return
        release = deepcopy(self._release.get(kind, {}))
        if not release.get('available') or not release.get('download_url'):
            self._state(kind, status='Check again for a release with an installable archive.')
            return
        self._download_serial += 1
        generation = self._download_serial
        self._download_generations[kind] = generation
        self._state(kind, busy=True, status='Downloading and validating archive…', progress=0)
        cancelled = threading.Event()
        def download():
            from ... import updater, decky_release
            if kind == 'app':
                stage = updater.download_and_stage_update(release['download_url'],
                    progress=lambda received, total: self.progress.emit(kind, generation, received, total), cancelled=cancelled)
            else:
                stage = decky_release.download_and_stage(release['download_url'], release['sha256_url'], release['version'])
            if cancelled.is_set():
                self._remove_stage(kind, stage)
                raise RuntimeError('Download cancelled')
            return stage
        self.runner.submit('download-' + kind, download, cancelled=cancelled)

    @staticmethod
    def _remove_stage(kind, stage):
        if not stage or stage == 'fixture':
            return
        if kind == 'decky':
            from ... import decky_release
            decky_release.cleanup_staged(stage)
        else:
            import shutil
            path = Path(stage).resolve()
            root = next((parent for parent in path.parents if parent.name.startswith('rogcontrol-update-')), None)
            if root is not None:
                shutil.rmtree(root, ignore_errors=True)

    def _cleanup_stage(self, kind):
        stage = self._states[kind].get('stage')
        if stage and kind not in self._poll and kind not in self._installing and not self.app.demo:
            # Private staged downloads only; cleanup never touches GUI thread.
            threading.Thread(target=self._remove_stage, args=(kind, stage), daemon=True).start()

    @Slot(str)
    def install(self, kind):
        if kind not in self._states or self._states[kind]['busy'] or not self._states[kind]['stage']:
            return
        self._confirmation = kind
        self.confirmationRequested.emit(kind, 'Install the staged ' + ('application update in a terminal? The application will close during installation.' if kind == 'app' else 'Decky plugin update and restart Decky Loader? The terminal may ask for sudo.'))

    @Slot(str, bool)
    def confirm(self, kind, accepted):
        if kind != self._confirmation:
            return
        self._confirmation = None
        if not accepted:
            return
        if self.app.demo:
            self._state(kind, status='Demo install complete', busy=False, stage='', available=False)
            return
        stage = self._states[kind]['stage']
        release = deepcopy(self._release.get(kind, {}))
        self._installing.add(kind)
        self._state(kind, busy=True, status='Starting installer…')
        def install():
            from ... import updater, decky_install
            import tempfile
            status = None
            handed_off = False
            try:
                if self._closed:
                    raise RuntimeError('Installation cancelled on shutdown')
                if kind == 'app':
                    fd, status = tempfile.mkstemp(prefix='rogcontrol-update-', suffix='.status')
                    os.close(fd)
                    ok, message = updater.launch_update_terminal(stage, status, app_pid=os.getpid(), cleanup_staging=True)
                    if not ok:
                        raise RuntimeError(message)
                else:
                    fd, status = tempfile.mkstemp(prefix='rogcontrol-decky-', suffix='.status')
                    os.close(fd)
                    launched = decky_install.launch_loader_restart(
                        status, app_pid=os.getpid(), cleanup_archive=stage,
                        install_archive_path=stage, expected_version=release['version'])
                    if not launched['ok']:
                        raise RuntimeError('Could not start Decky installation: ' + str(launched['message']))
                handed_off = True
                return status
            finally:
                if not handed_off:
                    # Worker owns failed pre-handoff attempts. Release before
                    # checking close so either worker or close cleans the stage.
                    self._installing.discard(kind)
                    if status:
                        self._remove_status(status)
                    if self._closed:
                        self._remove_stage(kind, stage)
        def done(result, error):
            if self._closed:
                return
            if error:
                self._installing.discard(kind)
                self._state(kind, busy=False, status=str(error))
            else:
                self._poll[kind] = (result, time.monotonic(), release.get('version'))
                self._state(kind, status='Installer terminal opened; waiting for completion…')
        if not self.app.submitWrite('update-install', install, done):
            self._installing.discard(kind)
            self._state(kind, busy=False, status='Another operation is running; retry when it finishes.')

    @Slot(str, int, int, int)
    def _progress(self, kind, generation, received, total):
        if not self._closed and self._download_generations.get(kind) == generation and self.runner.active('download-' + kind):
            percent = round(100 * received / total) if total else 0
            self._state(kind, progress=percent, status=f'Downloading: {received // 1024} KiB' + (f' / {total // 1024} KiB' if total else ''))

    @Slot(str, object, object)
    def _complete(self, job, result, error):
        if self._closed:
            return
        action, _, kind = job.partition('-')
        if kind not in self._states:
            return
        if action == 'download':
            self._download_generations.pop(kind, None)
        if error:
            self._state(kind, busy=False, status=str(error))
        elif action == 'check':
            self._release[kind] = result
            status = str(result['error']) if result.get('error') else ('Version ' + str(result.get('version')) + (' available' if result.get('download_url') else ' available without installable archive') if result.get('available') else 'Up to date / no newer installable release')
            self._state(kind, busy=False, status=status, available=bool(result.get('available') and result.get('download_url')), version=result.get('version') or '')
            if kind == 'app':
                self._save_last_check()
        elif action == 'download':
            self._cleanup_stage(kind)
            self._state(kind, busy=False, status='Archive staged and ready to install', stage=str(result), progress=100)
        elif action == 'poll':
            status, installation = result
            _, started, version = self._poll.get(kind, ('', time.monotonic(), ''))
            if status == '0':
                verified = kind == 'app' or (installation.get('installed') and installation.get('version') == version)
                self._finish_terminal(kind, clear_stage=True)
                self._state(kind, busy=False, status='Installation completed' if verified else 'Loader restarted, but installed plugin version could not be confirmed.', stage='', available=False)
            elif status.isdecimal():
                self._finish_terminal(kind)
                self._state(kind, busy=False, status='Installer failed (exit ' + status + '). See the terminal; retry install.')
            elif status == 'closed' or (not status and time.monotonic() - started > 30):
                self._finish_terminal(kind)
                self._state(kind, busy=False, status='Installer closed or did not start. Retry install.')

    @staticmethod
    def _remove_status(path):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    def _finish_terminal(self, kind, clear_stage=False):
        poll = self._poll.pop(kind, None)
        self._installing.discard(kind)
        if clear_stage:
            self._cleanup_stage(kind)
        if poll:
            threading.Thread(target=self._remove_status, args=(poll[0],), daemon=True).start()

    def _save_last_check(self):
        if self.app.busy:
            QTimer.singleShot(500, self._save_last_check)
            return
        if not self._closed:
            def save():
                from ... import config
                from ...profile_requests import selection_lock
                with selection_lock():
                    config.update_config(lambda cfg: cfg.update(last_update_check=time.time()))
            self.app.submitWrite('update-check-time', save)

    @Slot(str)
    def setAutomatic(self, value):
        if value not in ('off', 'launch', 'daily', '48h', 'weekly', 'monthly'):
            return
        if self.app.demo:
            self.app.config['update_check'] = value
            self.app.configChanged.emit()
            return
        def save():
            from ... import config
            from ...profile_requests import selection_lock
            with selection_lock():
                config.update_config(lambda cfg: cfg.update(update_check=value))
        self.app.submitWrite('update-preference', save)

    @Slot()
    def _automatic(self):
        if self._closed or self.app.demo:
            return
        mode = self.app.config.get('update_check', 'off')
        intervals = {'daily': 86400, '48h': 172800, 'weekly': 604800, 'monthly': 2592000}
        try:
            last = float(self.app.config.get('last_update_check', 0))
        except (ValueError, TypeError):
            last = 0
        due = mode in intervals and time.time() - last >= intervals[mode]
        if not self._launch_checked and mode in ('launch', *intervals):
            self._launch_checked = True
            if mode == 'launch' or due:
                self.check('app')

    @Slot()
    def poll(self):
        if self._closed:
            return
        for kind, (path, started, version) in list(self._poll.items()):
            job = 'poll-' + kind
            if self.runner.active(job):
                continue
            def read(path=path, kind=kind):
                from ... import decky_install
                try:
                    status = Path(path).read_text()[:32].strip()
                except OSError:
                    status = ''
                if status.startswith('running:'):
                    try:
                        pid = int(status.partition(':')[2])
                        if pid > 0:
                            os.kill(pid, 0)
                    except ProcessLookupError:
                        status = 'closed'
                    except (ValueError, PermissionError):
                        pass
                return status, decky_install.detect_installation() if kind == 'decky' and status == '0' else {}
            self.runner.submit(job, read)
        self._automatic()

    def close(self):
        self._closed = True
        self._download_generations.clear()
        self._cancel.set()
        self.timer.stop()
        self.runner.shutdown()
        for kind in self._states:
            self._cleanup_stage(kind)
