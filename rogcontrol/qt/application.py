"""Qt desktop, scoped single-instance IPC, and safe fixture preview."""
import argparse
import hashlib
import os
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description='ROG Control desktop')
    parser.add_argument('--self-test', action='store_true', help='Build and refresh Qt pages using isolated fixtures, without hardware or capture')
    parser.add_argument('--demo', action='store_true', help='Use fixtures; never access config or hardware')
    parser.add_argument('--show', action='store_true')
    parser.add_argument('--hide', action='store_true')
    parser.add_argument('--quit', action='store_true')
    parser.add_argument('--toggle', action='store_true')
    parser.add_argument('--minimized', action='store_true')
    parser.add_argument('--screenshot', metavar='PATH', help='Capture demo after rendering and exit')
    parser.add_argument('--demo-width', type=int, default=1040)
    args = parser.parse_args(argv)
    if args.self_test:
        args.demo = True
    if args.screenshot and not args.demo:
        parser.error('--screenshot requires --demo')
    try:
        from PySide6.QtCore import QTimer, QLockFile, QStandardPaths
        from PySide6.QtNetwork import QLocalServer, QLocalSocket
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuickControls2 import QQuickStyle
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        print(f'ROG Control Qt requires PySide6 with Qt Quick: {exc}', file=sys.stderr)
        return 1
    from .workers import TaskRunner
    from .controllers.app import AppController
    from .controllers.overview import OverviewController
    from .controllers.quick_access import QuickAccessController
    from .controllers.cpu import CpuController
    from .controllers.gpu import GpuController
    from .controllers.fans import FansController
    from .controllers.profile import ProfileController
    from .controllers.battery import BatteryController
    from .controllers.keyboard import KeyboardController
    from .controllers.system import SystemController
    from .controllers.updates import UpdatesController

    QQuickStyle.setStyle('Basic')
    application = QApplication(['rogcontrol-qt'])
    application.setQuitOnLastWindowClosed(False)
    application.setApplicationName('ROG Control')
    application.setOrganizationName('ROG Control')
    action = next((name for name in ('quit', 'toggle', 'show', 'hide', 'minimized') if getattr(args, name)), 'show')
    # Demo runs cannot hide or quit the actual application.
    scope = hashlib.sha256((str(os.getuid()) + os.environ.get('WAYLAND_DISPLAY', os.environ.get('DISPLAY', ''))).encode()).hexdigest()[:16]
    name = 'rogcontrol-qt-' + scope
    server = lock = None
    if not args.demo:
        socket = QLocalSocket()
        socket.connectToServer(name)
        if socket.waitForConnected(300):
            socket.write(action.encode() + b'\n')
            socket.flush()
            socket.waitForBytesWritten(500)
            return 0
        if action == 'quit':
            return 0
        lock = QLockFile(str(Path(QStandardPaths.writableLocation(QStandardPaths.RuntimeLocation)) / (name + '.lock')))
        if not lock.tryLock(0):
            print('Another Qt instance is starting; try again shortly.', file=sys.stderr)
            return 1
        QLocalServer.removeServer(name)
        server = QLocalServer()
        server.setSocketOptions(QLocalServer.UserAccessOption)
        if not server.listen(name):
            print(server.errorString(), file=sys.stderr)
            return 1

    runner = TaskRunner(application)
    controller = AppController(runner, args.demo, application)
    overview = OverviewController(controller, application)
    quick = QuickAccessController(controller, application)
    cpu = CpuController(controller, application)
    gpu = GpuController(controller, application)
    fans = FansController(controller, application)
    controller.tuningPages = {'cpu': cpu, 'gpu': gpu, 'fans': fans}
    profiles = ProfileController(controller, controller.tuningPages, application)
    controller.profilesController = profiles
    battery = BatteryController(controller, application)
    keyboard = KeyboardController(controller, application)
    system = SystemController(controller, application)
    updates = UpdatesController(controller, application)
    for page in (cpu, gpu, fans):
        page.stateChanged.connect(quick.stateChanged)
    settings_pages = (battery, keyboard, system, updates, quick)
    profiles.stateChanged.connect(controller.tuningStateChanged)
    engine = QQmlApplicationEngine()
    qml_errors = []
    engine.warnings.connect(lambda warnings: qml_errors.extend(warnings))
    for key, value in [('appController', controller), ('overviewController', overview), ('quickAccessController', quick), ('cpuController', cpu), ('gpuController', gpu), ('fansController', fans), ('profileController', profiles), ('batteryController', battery), ('keyboardController', keyboard), ('systemController', system), ('updatesController', updates)]:
        engine.rootContext().setContextProperty(key, value)
    engine.load(str(Path(__file__).resolve().parents[1] / 'qml' / 'Main.qml'))
    if not engine.rootObjects():
        for warning in qml_errors:
            print(warning.toString(), file=sys.stderr)
        controller.close()
        overview.close()
        for tuning in (cpu, gpu, fans, profiles):
            tuning.close()
        for page in settings_pages:
            page.close()
        runner.shutdown()
        return 1
    window = engine.rootObjects()[0]
    if args.demo:
        window.setWidth(max(360, args.demo_width))
    geometry_restored = False
    def restore_geometry():
        nonlocal geometry_restored
        if geometry_restored or args.demo:
            return
        saved = controller.config.get('window_size')
        if not controller.config:
            return
        geometry_restored = True
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            try:
                window.setWidth(max(360, min(7680, int(saved[0]))))
                window.setHeight(max(480, min(4320, int(saved[1]))))
            except (ValueError, TypeError):
                pass
    controller.configChanged.connect(restore_geometry)
    restore_geometry()

    quitting = False
    shutdownTimer = QTimer(application)
    shutdownTimer.setInterval(50)
    def finish_shutdown():
        # The event loop keeps shared QObjects alive until a running physical
        # mutation/calibration completes its restoration. Waits are incremental;
        # queued reads and isolated network jobs are already cancelled.
        ambient_alive = any(thread.is_alive() for thread in keyboard._capture_threads)
        if not runner.active('write') and not ambient_alive:
            shutdownTimer.stop()
            application.exit(1 if args.self_test and qml_errors else 0)
    shutdownTimer.timeout.connect(finish_shutdown)

    def command(value):
        nonlocal quitting
        if value == 'quit':
            if quitting:
                return
            quitting = True
            controller.saveGeometry(window.width(), window.height())
            controller.close()
            overview.close()
            for page in (cpu, gpu, fans, profiles, *settings_pages):
                page.close()
            runner.shutdown(timeout_ms=0)
            window.hide()
            shutdownTimer.start()
            finish_shutdown()
        elif quitting:
            return
        elif value == 'minimized':
            # Legacy background startup constructs the window without showing
            # it; another --minimized invocation leaves its visibility alone.
            return
        elif value == 'hide':
            window.hide()
        elif value == 'toggle' and window.isVisible():
            window.hide()
        else:
            if not args.demo and not window.isVisible() and not runner.active('tray-open'):
                def resurrect_tray():
                    from .. import hardware
                    hardware.start_tray()
                runner.submit('tray-open', resurrect_tray)
            window.showNormal()
            window.raise_()
            window.requestActivate()
    controller.windowCommand.connect(command)
    connections = []
    if server:
        def receive():
            while server.hasPendingConnections():
                peer = server.nextPendingConnection()
                connections.append(peer)
                def read(peer=peer):
                    if peer.canReadLine():
                        value = bytes(peer.readLine()).decode().strip()
                        if value in ('show', 'hide', 'toggle', 'quit', 'minimized'):
                            command(value)
                        peer.disconnectFromServer()
                peer.readyRead.connect(read)
                def release(peer=peer):
                    if peer in connections:
                        connections.remove(peer)
                    peer.deleteLater()
                peer.disconnected.connect(release)
                read()
        server.newConnection.connect(receive)
    application.aboutToQuit.connect(lambda: controller.saveGeometry(window.width(), window.height()))
    application.aboutToQuit.connect(controller.close)
    application.aboutToQuit.connect(overview.close)
    for tuning in (cpu, gpu, fans, profiles):
        application.aboutToQuit.connect(tuning.close)
    for page in settings_pages:
        application.aboutToQuit.connect(page.close)
    application.aboutToQuit.connect(runner.shutdown)
    # Main.qml starts hidden, so background startup never flashes a window.
    command(action)
    if args.self_test:
        # UI self-test intentionally opts out of screen capture, services,
        # config and hardware; each Loader page is built using demo fixtures.
        sequence = list(controller.tuningPages) + ['battery', 'keyboard', 'system', 'overview', 'quick_access']
        def advance():
            if sequence:
                controller.navigate(sequence.pop(0))
                QTimer.singleShot(150, advance)
            else:
                command('quit')
        QTimer.singleShot(150, advance)
    if args.screenshot:
        def capture():
            saved = window.grabWindow().save(str(Path(args.screenshot).resolve()))
            application.exit(0 if saved else 2)
        QTimer.singleShot(800, capture)
    exit_code = application.exec()
    if server:
        server.close()
    if lock:
        lock.unlock()
    return exit_code
