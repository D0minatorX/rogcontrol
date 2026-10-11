"""Hardware-free Qt runtime diagnostic used by the installer.

Run as a separate process: the offscreen platform is local to this diagnostic,
never an override for the desktop application or its tray.
"""
import os
import sys
import time


def main():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['QT_QUICK_BACKEND'] = 'software'
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QImageReader
        from PySide6.QtWidgets import QApplication
        from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: F401
        from PySide6.QtQuick import QQuickWindow  # noqa: F401
        from PySide6.QtQuickControls2 import QQuickStyle
        from PySide6.QtQml import QQmlEngine, QQmlComponent
    except (ImportError, OSError) as error:
        print(f'Qt runtime unavailable: {error}', file=sys.stderr)
        return 1
    QQuickStyle.setStyle('Basic')
    app = QApplication(['rogcontrol-runtime'])
    engine = QQmlEngine()
    component = QQmlComponent(engine)
    component.setData(b'''
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import QtQml.Models
import QtQml.WorkerScript
Item { RowLayout { Button { text: "ROG Control" } } }
''', QUrl())
    deadline = time.monotonic() + 5
    while component.isLoading() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    if not component.isReady():
        print('\n'.join(error.toString() for error in component.errors()) or
              'Required QML modules did not finish loading.', file=sys.stderr)
        return 1
    item = component.create()
    if item is None:
        print('\n'.join(error.toString() for error in component.errors()), file=sys.stderr)
        return 1
    if b'svg' not in [bytes(fmt) for fmt in QImageReader.supportedImageFormats()]:
        print('Qt SVG image support is missing; install the Qt 6 SVG runtime.', file=sys.stderr)
        return 1
    del item, component, engine
    print('PySide6, Qt Quick, QML controls/layouts and SVG runtime available.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
