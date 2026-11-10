import QtQuick
Canvas {
    id: panel
    property color fillColor: '#101012'
    property color lineColor: '#424247'
    property color accentColor: '#ef1745'
    property bool highlighted: false
    property bool decorated: true
    property int cut: 14
    onFillColorChanged: requestPaint()
    onLineColorChanged: requestPaint()
    onAccentColorChanged: requestPaint()
    onHighlightedChanged: requestPaint()
    onDecoratedChanged: requestPaint()
    onCutChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        const c = getContext('2d'), w = width, h = height, d = Math.min(cut, h / 3, w / 4)
        c.reset()
        c.beginPath(); c.moveTo(d, 1); c.lineTo(w-d, 1); c.lineTo(w-1, d)
        c.lineTo(w-1, h-d); c.lineTo(w-d, h-1); c.lineTo(d, h-1)
        c.lineTo(1, h-d); c.lineTo(1, d); c.closePath()
        const g = c.createLinearGradient(0, 0, w, h)
        g.addColorStop(0, Qt.lighter(fillColor, 1.5)); g.addColorStop(.15, fillColor)
        g.addColorStop(.65, Qt.darker(fillColor, 1.4)); g.addColorStop(1, Qt.lighter(fillColor, 1.12))
        c.fillStyle = g; c.fill(); c.strokeStyle = highlighted ? accentColor : lineColor; c.lineWidth = 1; c.stroke()
        c.save(); c.clip()
        if (decorated) {
            c.strokeStyle = lineColor; c.globalAlpha = .12
            for (let i = w - 150; i < w + 60; i += 4) {
                c.beginPath(); c.moveTo(i, 0); c.lineTo(i - 110, 110); c.stroke()
            }
            // Fine brushed grain and a second bevel make the surface catch light.
            c.globalAlpha = .007; c.strokeStyle = '#ffffff'
            for (let y = 3; y < h; y += 3) {
                c.beginPath(); c.moveTo(0, y); c.lineTo(w, y - w * .08); c.stroke()
            }
            c.globalAlpha = .38; c.strokeStyle = lineColor
            c.beginPath(); c.moveTo(d+4,4); c.lineTo(w-d-4,4); c.stroke()
            c.beginPath(); c.moveTo(w-90,h-5); c.lineTo(w-d-5,h-5); c.lineTo(w-5,h-d-5); c.stroke()
            c.globalAlpha = .12
            for (let i=0; i<5; i++) {
                const x = w*.43+i*10
                c.beginPath(); c.moveTo(x,h-5); c.lineTo(x+14,h-18); c.lineTo(x+20,h-18); c.lineTo(x+6,h-5); c.closePath(); c.fillStyle=lineColor; c.fill()
            }
            c.globalAlpha = 1; c.fillStyle = accentColor
            c.beginPath(); c.moveTo(w-94,1); c.lineTo(w-64,1); c.lineTo(w-54,7); c.lineTo(w-32,8); c.lineTo(w-63,6); c.lineTo(w-74,3); c.closePath(); c.fill()
            c.strokeStyle = accentColor; c.lineWidth = 1
            c.lineWidth = 1; c.beginPath(); c.moveTo(1, h-18); c.lineTo(18, h-1); c.stroke()
        }
        c.restore()
    }
}
