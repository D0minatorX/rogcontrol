import QtQuick
Canvas {
    id: surface
    property var colors
    property bool rail: false
    property bool selected: false
    property bool hovered: false
    property bool focused: false
    readonly property color border: colors.border
    readonly property color accent: colors.accent
    onBorderChanged: requestPaint()
    onAccentChanged: requestPaint()
    onSelectedChanged: requestPaint()
    onHoveredChanged: requestPaint()
    onFocusedChanged: requestPaint()
    onRailChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        const c=getContext('2d'), w=width, h=height
        c.reset()
        if (!rail) {
            if (!selected && !hovered && !focused) return
            c.strokeStyle = colors.accent
            c.globalAlpha = selected || focused ? 1 : .65
            c.lineWidth = selected ? 3 : 2
            c.lineCap = 'round'
            c.beginPath()
            c.moveTo(w * .38, h - 5)
            c.lineTo(w * .62, h - 5)
            c.stroke()
            return
        }
        const skew = Math.min(48, h * .65)
        c.beginPath();c.moveTo(skew,1);c.lineTo(w-8,1);c.lineTo(w-1,8)
        c.lineTo(w-skew,h-1);c.lineTo(8,h-1);c.lineTo(1,h-8)
        c.closePath()
        const g = c.createLinearGradient(0, 0, 0, h)
        g.addColorStop(0, colors.panel)
        g.addColorStop(.35, colors.sidebar)
        g.addColorStop(1, colors.sidebar)
        c.fillStyle=g;c.globalAlpha=.8;c.fill();c.globalAlpha=1
        c.strokeStyle=colors.border;c.lineWidth=1;c.stroke()
        c.save();c.clip()
        c.fillStyle=colors.panel;c.globalAlpha=.2
        c.beginPath();c.moveTo(skew+8,5);c.lineTo(450,5);c.lineTo(380,h-5);c.lineTo(8,h-5);c.closePath();c.fill()
        c.globalAlpha=.12
        for(let x=0;x<140;x+=4){c.beginPath();c.moveTo(x,0);c.lineTo(x-h,h);c.stroke()}
        c.globalAlpha=.35;c.strokeStyle=colors.border
        c.beginPath();c.moveTo(skew+6,5);c.lineTo(w-12,5);c.stroke()
        c.globalAlpha=.08;c.fillStyle=colors.border
        c.beginPath();c.moveTo(w*.30,1);c.lineTo(w*.48,1);c.lineTo(w*.48-h,h-1);c.lineTo(w*.30-h,h-1);c.closePath();c.fill()
        c.globalAlpha=.7;c.strokeStyle=colors.accent;c.shadowColor=colors.accent;c.shadowBlur=7
        c.beginPath();c.moveTo(1,h-8);c.lineTo(skew*.65,h*.35);c.stroke()
        c.beginPath();c.moveTo(w-1,8);c.lineTo(w-skew,h-1);c.stroke()
        c.shadowBlur=0
        c.restore()
    }
}
