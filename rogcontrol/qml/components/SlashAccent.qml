import QtQuick
Canvas {
    id: slash
    property color tint: '#f00036'
    property bool doubleSlash: false
    implicitWidth: doubleSlash ? 38 : 20
    implicitHeight: doubleSlash ? 24 : 14
    onTintChanged: requestPaint()
    onDoubleSlashChanged: requestPaint()
    onPaint: {
        const c = getContext('2d'); c.reset(); c.fillStyle = tint
        const count = doubleSlash ? 2 : 1, size = width / count
        for (let i = 0; i < count; i++) {
            const x = i * size
            c.beginPath(); c.moveTo(x + size*.55, 0); c.lineTo(x + size, 0)
            c.lineTo(x + size*.45, height); c.lineTo(x, height); c.closePath(); c.fill()
        }
    }
}
