import QtQuick
Canvas {
    id: root
    property string name
    property color tint
    width: 24; height: 24
    onNameChanged: requestPaint()
    onTintChanged: requestPaint()
    onPaint: {
        var c = getContext('2d'); c.reset(); c.strokeStyle = tint; c.fillStyle = tint
        c.lineWidth = 1.6; c.lineCap = 'round'; c.lineJoin = 'round'
        function line(x,y,a,b) { c.beginPath(); c.moveTo(x,y); c.lineTo(a,b); c.stroke() }
        function box(x,y,w,h) { c.strokeRect(x,y,w,h) }
        function circle(x,y,r) { c.beginPath(); c.arc(x,y,r,0,Math.PI*2); c.stroke() }
        if (name === 'quick_access') {
            c.beginPath(); c.moveTo(3,11); c.lineTo(12,3); c.lineTo(21,11); c.moveTo(5,10); c.lineTo(5,21); c.lineTo(10,21); c.lineTo(10,15); c.lineTo(14,15); c.lineTo(14,21); c.lineTo(19,21); c.lineTo(19,10); c.stroke()
        } else if (name === 'cpu') {
            box(6,6,12,12); box(9,9,6,6)
            for(var i=8;i<=16;i+=4) { line(i,3,i,6); line(i,18,i,21); line(3,i,6,i); line(18,i,21,i) }
        } else if (name === 'gpu') {
            box(3,5,18,13); circle(10,11.5,3.5); line(16,9,18,9); line(16,13,18,13); line(6,18,6,21); line(9,18,15,18); line(2,3,2,20)
        } else if (name === 'fans') {
            circle(12,12,2)
            for(var j=0;j<3;j++) { c.save(); c.translate(12,12); c.rotate(j*Math.PI*2/3); c.beginPath(); c.moveTo(1,-3); c.bezierCurveTo(9,-13,14,-1,4,1); c.stroke(); c.restore() }
        } else if (name === 'battery') {
            box(2,6,18,12); line(22,10,22,14); line(6,10,6,14); line(10,10,10,14); line(14,10,14,14)
        } else if (name === 'keyboard') {
            box(2,5,20,14)
            for(var y=9;y<=12;y+=3) for(var x=6;x<=18;x+=4) line(x,y,x+.5,y)
            line(7,16,17,16)
        } else {
            circle(12,12,4); circle(12,12,8)
            for(var k=0;k<8;k++) { var a=k*Math.PI/4; line(12+8*Math.cos(a),12+8*Math.sin(a),12+10*Math.cos(a),12+10*Math.sin(a)) }
        }
    }
}
