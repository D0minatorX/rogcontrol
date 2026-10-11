import QtQuick
Canvas {
    id: root
    property string profileName
    property color tint: '#a5a7b0'
    width: 24; height: 24
    onProfileNameChanged: requestPaint()
    onTintChanged: requestPaint()
    onPaint: {
        var c = getContext('2d'); c.reset(); c.scale(width/24, height/24)
        c.strokeStyle = tint; c.lineWidth = 1.7; c.lineCap = 'round'; c.lineJoin = 'round'
        var name = profileName.toLowerCase()
        c.beginPath()
        if (name.includes('quiet')) {
            c.moveTo(19,15); c.bezierCurveTo(11,18,6,10,10,4)
            c.bezierCurveTo(3,5,2,14,7,18); c.bezierCurveTo(12,22,18,19,19,15)
        } else if (name.includes('balanced') && name.includes('power') && !name.includes('performance')) {
            c.moveTo(5,18); c.bezierCurveTo(1,8,11,4,20,4)
            c.bezierCurveTo(20,14,15,21,7,17); c.moveTo(4,21); c.lineTo(15,10)
        } else if (name.includes('balanced')) {
            c.arc(12,13,8,Math.PI*.8,Math.PI*2.2)
            c.moveTo(12,13); c.lineTo(16,8); c.moveTo(7,20); c.lineTo(17,20)
        } else if (name.includes('performance')) {
            c.moveTo(14,3); c.lineTo(5,14); c.lineTo(11,14)
            c.lineTo(10,21); c.lineTo(19,10); c.lineTo(13,10); c.closePath()
        } else {
            c.moveTo(5,7); c.lineTo(19,7); c.moveTo(5,17); c.lineTo(19,17)
            c.moveTo(9,4); c.lineTo(9,10); c.moveTo(15,14); c.lineTo(15,20)
        }
        c.stroke()
    }
}
