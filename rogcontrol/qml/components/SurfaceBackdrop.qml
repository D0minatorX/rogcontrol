import QtQuick
Canvas {
    id: backdrop
    property var colors
    property color baseTone: colors ? colors.window : '#101216'
    property color lineTone: colors ? colors.border : '#414146'
    onBaseToneChanged: requestPaint()
    onLineToneChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        const c=getContext('2d'), w=width, h=height
        c.reset()
        const g=c.createLinearGradient(0,0,w,h)
        g.addColorStop(0,Qt.lighter(baseTone,1.4))
        g.addColorStop(.45,baseTone)
        g.addColorStop(1,Qt.lighter(baseTone,1.2))
        c.fillStyle=g;c.fillRect(0,0,w,h)
        c.strokeStyle=lineTone;c.globalAlpha=.055;c.lineWidth=.6
        for(let x=-h;x<w+h;x+=5){c.beginPath();c.moveTo(x,0);c.lineTo(x-h,h);c.stroke()}
        c.fillStyle=lineTone;c.globalAlpha=.035
        c.beginPath();c.moveTo(w*.55,0);c.lineTo(w*.8,0);c.lineTo(w*.8-h,h);c.lineTo(w*.55-h,h);c.closePath();c.fill()
    }
}
