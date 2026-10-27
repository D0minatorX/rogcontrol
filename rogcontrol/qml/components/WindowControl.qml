import QtQuick
import QtQuick.Controls
Button {
    id: root
    property var colors
    property string glyph
    property string appearance: 'system'
    implicitWidth: 34; implicitHeight: 32
    hoverEnabled: true
    Accessible.name: text
    ToolTip.visible: hovered
    ToolTip.delay: 600
    ToolTip.text: text
    contentItem: Canvas {
        id: icon
        onPaint: {
            var c = getContext('2d'); c.reset()
            c.strokeStyle = root.glyph === 'close' && root.hovered ? '#ffffff' : root.colors.text
            c.lineWidth = 1.4; c.lineCap = 'round'; c.lineJoin = 'round'
            var x = width/2, y = height/2
            c.beginPath()
            if (root.glyph === 'menu') {
                for(var i=-4;i<=4;i+=4) { c.moveTo(x-5,y+i); c.lineTo(x+5,y+i) }
            } else if (root.glyph === 'minimize') { c.moveTo(x-5,y+3); c.lineTo(x+5,y+3) }
            else if (root.glyph === 'close') { c.moveTo(x-4,y-4); c.lineTo(x+4,y+4); c.moveTo(x+4,y-4); c.lineTo(x-4,y+4) }
            else if (root.glyph === 'restore') { c.rect(x-5,y-2,7,7); c.moveTo(x-2,y-4); c.lineTo(x+5,y-4); c.lineTo(x+5,y+3) }
            else if (root.glyph === 'theme' && root.appearance === 'system') { c.rect(x-5,y-4,10,8); c.moveTo(x,y+4); c.lineTo(x,y+6); c.moveTo(x-3,y+6); c.lineTo(x+3,y+6) }
            else if (root.glyph === 'theme' && root.appearance === 'light') {
                c.arc(x,y,3,0,Math.PI*2)
                for (var a=0;a<8;a++) { var angle=a*Math.PI/4; c.moveTo(x+5*Math.cos(angle),y+5*Math.sin(angle)); c.lineTo(x+6.5*Math.cos(angle),y+6.5*Math.sin(angle)) }
            }
            else if (root.glyph === 'theme') {
                c.fillStyle = root.colors.text
                c.beginPath()
                c.arc(x,y,5.5,0,Math.PI*2)
                c.fill()
                c.globalCompositeOperation = 'destination-out'
                c.beginPath()
                c.arc(x+3.2,y-2.1,5.1,0,Math.PI*2)
                c.fill()
                c.globalCompositeOperation = 'source-over'
            }
            else { c.rect(x-4.5,y-4.5,9,9) }
            if (!(root.glyph === 'theme' && root.appearance === 'dark')) c.stroke()
        }
        Connections { target: root; function onHoveredChanged() { icon.requestPaint() } function onGlyphChanged() { icon.requestPaint() } function onAppearanceChanged() { icon.requestPaint() } }
        Connections { target: root.colors; function onTextChanged() { icon.requestPaint() } }
    }
    background: Rectangle {
        radius: 7
        color: root.hovered || root.down ? (root.glyph === 'close' ? root.colors.accent : root.colors.hover) : 'transparent'
        border.color: root.activeFocus ? root.colors.accent : 'transparent'
        Behavior on color { ColorAnimation { duration: 100 } }
    }
}
