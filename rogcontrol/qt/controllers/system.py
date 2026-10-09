"""System diagnostics and supported firmware/service operations."""
from PySide6.QtCore import Slot
from PySide6.QtWidgets import QApplication
from .settings import SettingsController


class SystemController(SettingsController):
    page = 'system'

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        self._problems = False
        self._pending = None
        self._report = ''
        self.refresh()

    def fixture(self):
        return dict(version='Demo', caps={'boot_sound': True, 'panel_od': True, 'psr_toggle': True},
                    boot_sound=True, panel_od=True, power_mode='balanced', expected_mode='balanced',
                    asusd={'installed': True, 'state': 'stopped'}, uninstall='sudo pacman -Rns asusctl',
                    graphics={'state': 'running'}, backend='supergfxctl', psr_pending=False,
                    psr_live=False, psr_foreign=None, log='Demo log: no hardware or services accessed.', report=self._report)

    def sample(self):
        from ... import hardware, graphics_backend, APP_VERSION
        backend = graphics_backend.selected_backend()
        text = hardware.read_log_tail()
        from ...profiles import expected_ppd_mode
        return dict(version=APP_VERSION, caps=hardware.detect_capabilities(probe_gpu=False),
                    boot_sound=hardware.read_boot_sound(), panel_od=hardware.read_panel_od(),
                    power_mode=hardware.read_power_mode(), expected_mode=expected_ppd_mode(self.app.activeProfile),
                    asusd=hardware.read_asusd_state(), uninstall=hardware.asusd_uninstall_command(),
                    graphics=hardware.read_cardwired_state() if backend == 'cardwire' else hardware.read_supergfxd_state(),
                    backend=backend, psr_pending=hardware.read_psr_disabled_pending(), psr_live=hardware.read_psr_disabled_live(),
                    psr_foreign=hardware.psr_foreign_dcdebugmask(),
                    log=hardware.filter_log_problems(text) if self._problems else text, report=self._report)

    @Slot(str)
    def setAppearance(self, appearance):
        if appearance in ('system', 'dark', 'light'):
            self.persist(lambda cfg: cfg.update(appearance=appearance))

    @Slot(str, bool)
    def setFirmware(self, key, enabled):
        if key not in ('boot_sound', 'panel_od') or not self._data.get('caps', {}).get(key):
            return
        def apply():
            from ... import hardware
            return hardware.run_helper('bootsound' if key == 'boot_sound' else 'panelod', int(enabled))
        self.physical(apply, lambda cfg: cfg.update({key: int(enabled)}))

    @Slot(str)
    def setPowerMode(self, mode):
        if mode not in ('power-saver', 'balanced', 'performance'):
            return
        def apply():
            from ... import hardware
            return hardware.set_power_mode(mode)
        self.physical(apply)

    @Slot(bool)
    def setAsusd(self, running):
        if not self._data.get('asusd', {}).get('installed'):
            return
        def apply():
            from ... import hardware
            return hardware.set_asusd_running(running)
        self.physical(apply)

    @Slot()
    def enableGraphics(self):
        backend = self._data.get('backend')
        def apply():
            from ... import hardware
            return hardware.set_cardwired_running() if backend == 'cardwire' else hardware.set_supergfxd_running()
        self.physical(apply)

    @Slot(bool)
    def setPsrDisabled(self, disabled):
        if self._data.get('psr_foreign') or not self._data.get('caps', {}).get('psr_toggle'):
            return
        def apply():
            from ... import hardware
            if hardware.psr_foreign_dcdebugmask():
                return False, 'Another dcdebugmask is configured; it was preserved.'
            return hardware.set_psr_disabled(disabled)
        def done(result, error):
            if not error:
                self._status = 'Boot configuration saved. The running kernel changes after reboot.'
        self.physical(apply, done=done)

    @Slot(str)
    def copy(self, what):
        text = self._data.get({'log':'log', 'uninstall':'uninstall', 'report':'report'}.get(what, ''), '')
        if text:
            QApplication.clipboard().setText(str(text))
            self._status = 'Copied to clipboard'
            self.stateChanged.emit()

    @Slot(bool)
    def setProblemsOnly(self, enabled):
        self._problems = enabled
        self.refresh()

    @Slot()
    def hardwareReport(self):
        if self.app.demo:
            self._report = 'Demo hardware report; fixture only.'
            self._data['report'] = self._report
            self.stateChanged.emit()
            return
        def report():
            from ... import diagnostics
            from ...profile_requests import hardware_apply_lock
            from pathlib import Path
            # Capability diagnostics include a temporary GPU setter probe.
            # Keep generation and saving in one physical transaction and save
            # the exact text returned for copy/display, without probing twice.
            with hardware_apply_lock():
                if self._closed:
                    raise RuntimeError('Report cancelled on shutdown')
                text = diagnostics.hardware_report_text()
                path = diagnostics.hardware_report_path()
                Path(path).write_text(text)
            return str(path), text
        def done(result, error):
            if not error:
                self._report = result[1]
                self._status = 'Hardware report saved: ' + result[0]
        self.write(report, done)

    @Slot(str)
    def request(self, action):
        messages = {'clear-log': 'Clear the complete diagnostic log?',
                    'reboot': 'Reboot this computer now? Save your work before continuing.'}
        if action in messages:
            self._pending = action
            self.confirmationRequested.emit(action, messages[action])

    @Slot(str, bool)
    def confirm(self, action, accepted):
        if action != self._pending:
            return
        self._pending = None
        if not accepted:
            return
        if action == 'clear-log':
            def clear():
                from ... import hardware
                ok = hardware.clear_log()
                return ok, 'Log cleared' if ok else 'Cannot clear the log; check permissions.'
            self.physical(clear)
        elif action == 'reboot':
            def reboot():
                from ... import hardware
                return hardware.reboot_system()
            self.physical(reboot)
