"""Optional Qt shell, scoped single-instance IPC, and safe fixture preview."""
import argparse
import hashlib
import os
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description='ROG Control Qt desktop preview')
    parser.add_argument('--demo', action='store_true', help='Use fixtures; never access config or hardware')
    parser.add_argument('--show', action='store_true')
    parser.add_argument('--hide', action='store_true')
    parser.add_argument('--quit', action='store_true')
    parser.add_argument('--toggle', action='store_true')
    parser.add_argument('--minimized', action='store_true')
    parser.add_argument('--screenshot', metavar='PATH', help='Capture demo after rendering and exit')
    parser.add_argument('--demo-width', type=int, default=1040)
    args = parser.parse_args(argv)
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

    QQuickStyle.setStyle('Basic')
    application = QApplication(['rogcontrol-qt'])
    application.setApplicationName('ROG Control')
    application.setOrganizationName('ROG Control')
    action = next((name for name in ('quit', 'hide', 'toggle', 'show') if getattr(args, name)), 'show')
    # Separate from GTK IPC; demo runs cannot hide or quit the actual application.
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
        if action in ('quit', 'hide'):
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
    engine = QQmlApplicationEngine()
    for key, value in [('appController', controller), ('overviewController', overview), ('quickAccessController', quick)]:
        engine.rootContext().setContextProperty(key, value)
    engine.load(str(Path(__file__).resolve().parents[1] / 'qml' / 'Main.qml'))
    if not engine.rootObjects():
        controller.close()
        overview.close()
        return 1
    window = engine.rootObjects()[0]
    window.setWidth(max(360, args.demo_width))

    def command(value):
        if value == 'quit':
            application.quit()
        elif value == 'hide':
            window.hide()
        elif value == 'toggle' and window.isVisible():
            window.hide()
        else:
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
                        if value in ('show', 'hide', 'toggle', 'quit'):
                            command(value)
                        peer.disconnectFromServer()
                peer.readyRead.connect(read)
                peer.disconnected.connect(peer.deleteLater)
                read()
        server.newConnection.connect(receive)
    application.aboutToQuit.connect(controller.close)
    application.aboutToQuit.connect(overview.close)
    if args.minimized:
        window.showMinimized()
    elif args.hide:
        window.hide()
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
