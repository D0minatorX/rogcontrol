import QtQuick
Canvas {
    id: surface
    property var colors
    property bool rail: false
    property bool selected: false
    property bool hovered: false
    property bool focused: false
    property color edge: selected || focused ? colors.accent : colors.border
    onEdgeChanged: requestPaint()
    onSelectedChanged: requestPaint()
    onHoveredChanged: requestPaint()
    onFocusedChanged: requestPaint()
    onRailChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        const c=getContext('2d'), w=width, h=height
        c.reset()
        if (!rail && !selected && !hovered && !focused) return
        const skew=rail ? Math.min(48,h*.65) : Math.min(22,h*.45)
        c.beginPath();c.moveTo(skew,1);c.lineTo(w-8,1);c.lineTo(w-1,8)
        c.lineTo(w-skew,h-1);c.lineTo(8,h-1);c.lineTo(1,h-8)
        if (!rail) c.lineTo(5,h*.35)
        c.closePath()
        const g=c.createLinearGradient(0,0,0,h)
        g.addColorStop(0,selected ? Qt.lighter(colors.selected,1.25) : colors.panel)
        g.addColorStop(.35,selected ? colors.selected : colors.sidebar)
        g.addColorStop(1,selected ? Qt.darker(colors.selected,1.5) : colors.sidebar)
        c.fillStyle=g;c.globalAlpha=rail ? .8 : 1;c.fill();c.globalAlpha=1
        c.strokeStyle=edge;c.lineWidth=1
        if(selected){c.shadowColor=colors.accent;c.shadowBlur=5}
        c.stroke();c.shadowBlur=0
        c.save();c.clip()
        c.globalAlpha=.1;c.strokeStyle=colors.border
        if (rail) {
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
        }
        if(selected){
            c.globalAlpha=.35;c.strokeStyle=colors.accent;c.lineWidth=.6
            for(let y=4;y<h;y+=4) for(let x=w-65+(h-y)*.7;x<w-4;x+=4) {
                c.beginPath();c.moveTo(x,y-1);c.lineTo(x+1,y);c.lineTo(x,y+1);c.lineTo(x-1,y);c.closePath();c.stroke()
            }
            c.globalAlpha=.8;c.lineWidth=2
            c.beginPath();c.moveTo(5,h*.35);c.lineTo(skew+1,2);c.lineTo(w-15,2);c.stroke()
        }
        c.restore()
    }
}
