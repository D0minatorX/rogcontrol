import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ColumnLayout {
    id: root
    property var colors
    property var points: []
    property var calibration: [0, 1]
    property string displayUnit: 'percent'
    property int selectedPoint: 0
    signal pointMoved(int index, real temperature, real percent)
    signal pointAdded(real temperature, real percent)
    signal pointRemoved(int index)
    spacing: 12
    function ordinate(pct) { return displayUnit === 'rpm' ? (pct === 0 ? 0 : calibration[0] + calibration[1] * pct) : pct }
    function addPoint() {
        if (points.length >= 8) return
        var gap = 0, at = -1
        for (var i = 0; i < points.length - 1; i++) {
            var distance = points[i+1][0] - points[i][0]
            if (distance > gap && distance >= 2) { gap = distance; at = i }
        }
        if (at >= 0) pointAdded(Math.round((points[at][0]+points[at+1][0])/2), Math.round((points[at][1]+points[at+1][1])/2))
        else if (points.length && points[0][0] > 0) pointAdded(points[0][0]-1, points[0][1])
        else if (points.length && points[points.length-1][0] < 100) pointAdded(points[points.length-1][0]+1, points[points.length-1][1])
    }
    Flow {
        Layout.fillWidth: true; spacing: 8
        RogButton { colors: root.colors; text: '+ Add point'; primary: true; enabled: root.points.length < 8; onClicked: root.addPoint() }
        RogButton { colors: root.colors; text: 'Remove selected'; enabled: root.points.length > 2 && root.selectedPoint >= 0; onClicked: root.pointRemoved(root.selectedPoint) }
    }
    Rectangle {
        id: chart
        Layout.fillWidth: true; Layout.preferredHeight: 270
        color: root.colors.window; radius: 6
        border.color: activeFocus ? root.colors.accent : root.colors.border
        activeFocusOnTab: true
        Accessible.role: Accessible.Chart
        Accessible.name: 'Fan curve. Add a point with the Add point button. Select and drag points, or use arrow keys to adjust the selected point.'
        property real leftInset: root.displayUnit === 'rpm' ? 58 : 42
        property real rightInset: 20
        property real topInset: 24
        property real bottomInset: 36
        function px(t) { return leftInset + t / 100 * (width-leftInset-rightInset) }
        function py(p) { return topInset + (1-p/100) * (height-topInset-bottomInset) }
        function temperature(x) { return Math.max(0, Math.min(100, (x-leftInset)/(width-leftInset-rightInset)*100)) }
        function duty(y) { return Math.max(0, Math.min(100, (1-(y-topInset)/(height-topInset-bottomInset))*100)) }
        Keys.onPressed: event => {
            if (event.key === Qt.Key_Plus || event.key === Qt.Key_Insert) { root.addPoint(); event.accepted = true; return }
            if (root.selectedPoint < 0 || root.selectedPoint >= root.points.length) return
            var point = root.points[root.selectedPoint]
            var step = event.modifiers & Qt.ShiftModifier ? 5 : 1
            if (event.key === Qt.Key_Delete || event.key === Qt.Key_Backspace) root.pointRemoved(root.selectedPoint)
            else if (event.key === Qt.Key_Left) root.pointMoved(root.selectedPoint, Math.max(0, point[0]-step), point[1])
            else if (event.key === Qt.Key_Right) root.pointMoved(root.selectedPoint, Math.min(100, point[0]+step), point[1])
            else if (event.key === Qt.Key_Up) root.pointMoved(root.selectedPoint, point[0], Math.min(100, point[1]+step))
            else if (event.key === Qt.Key_Down) root.pointMoved(root.selectedPoint, point[0], Math.max(0, point[1]-step))
            else { event.accepted = false; return }
            event.accepted = true
        }
        Canvas {
            id: graph
            anchors.fill: parent
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                var ctx = getContext('2d'); ctx.reset()
                ctx.font = '10px sans-serif'; ctx.lineWidth = 1
                for (var tick = 0; tick <= 100; tick += 20) {
                    ctx.strokeStyle = root.colors.border; ctx.beginPath()
                    ctx.moveTo(chart.px(0), chart.py(tick)); ctx.lineTo(chart.px(100), chart.py(tick)); ctx.stroke()
                    ctx.fillStyle = root.colors.mutedText; ctx.textAlign = 'right'
                    ctx.fillText(Math.round(root.ordinate(tick)).toString(), chart.leftInset-9, chart.py(tick)+4)
                    ctx.textAlign = 'center'; ctx.fillText(tick + '°', chart.px(tick), height-12)
                }
                if (!root.points.length) return
                var first = root.points[0], last = root.points[root.points.length-1]
                var fill = ctx.createLinearGradient(0, chart.py(100), 0, chart.py(0))
                fill.addColorStop(0, '#44200f18'); fill.addColorStop(1, '#08200f18')
                ctx.fillStyle = fill; ctx.beginPath(); ctx.moveTo(chart.px(0), chart.py(0)); ctx.lineTo(chart.px(0), chart.py(first[1]))
                for (var i = 0; i < root.points.length; i++) ctx.lineTo(chart.px(root.points[i][0]), chart.py(root.points[i][1]))
                ctx.lineTo(chart.px(100), chart.py(last[1])); ctx.lineTo(chart.px(100), chart.py(0)); ctx.closePath(); ctx.fill()
                ctx.strokeStyle = root.colors.accent; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.moveTo(chart.px(0), chart.py(first[1]))
                for (i = 0; i < root.points.length; i++) ctx.lineTo(chart.px(root.points[i][0]), chart.py(root.points[i][1]))
                ctx.lineTo(chart.px(100), chart.py(last[1])); ctx.stroke()
                for (i = 0; i < root.points.length; i++) {
                    ctx.fillStyle = i === root.selectedPoint ? root.colors.text : root.colors.accent
                    ctx.strokeStyle = root.colors.window; ctx.lineWidth = 3
                    ctx.beginPath(); ctx.arc(chart.px(root.points[i][0]), chart.py(root.points[i][1]), i === root.selectedPoint ? 7 : 5.5, 0, Math.PI*2); ctx.fill(); ctx.stroke()
                }
            }
        }
        MouseArea {
            anchors.fill: parent; hoverEnabled: true
            property int dragging: -1
            cursorShape: dragging >= 0 ? Qt.ClosedHandCursor : Qt.CrossCursor
            function nearest(x, y) {
                var best = -1, distance = 18*18
                for (var i = 0; i < root.points.length; i++) {
                    var dx = x-chart.px(root.points[i][0]), dy = y-chart.py(root.points[i][1])
                    if (dx*dx+dy*dy < distance) { distance = dx*dx+dy*dy; best = i }
                }
                return best
            }
            onPressed: mouse => { chart.forceActiveFocus(); dragging = nearest(mouse.x, mouse.y); if (dragging >= 0) root.selectedPoint = dragging }
            onPositionChanged: mouse => { if (pressed && dragging >= 0) root.pointMoved(dragging, chart.temperature(mouse.x), chart.duty(mouse.y)) }
            onReleased: dragging = -1
            onCanceled: dragging = -1
            onDoubleClicked: mouse => {
                if (nearest(mouse.x, mouse.y) < 0 && mouse.x >= chart.leftInset && mouse.x <= width-chart.rightInset && mouse.y >= chart.topInset && mouse.y <= height-chart.bottomInset)
                    root.pointAdded(chart.temperature(mouse.x), chart.duty(mouse.y))
            }
        }
    }
    RowLayout {
        Layout.fillWidth: true
        Text {
            text: root.selectedPoint >= 0 && root.selectedPoint < root.points.length ? 'Point ' + (root.selectedPoint+1) + '   ·   ' + root.points[root.selectedPoint][0] + ' °C   /   ' + Math.round(root.ordinate(root.points[root.selectedPoint][1])) + (root.displayUnit === 'rpm' ? ' RPM' : ' %') : 'Select a point'
            color: root.colors.text; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap
        }
        Text { text: root.points.length + ' / 8 points'; color: root.colors.mutedText; font.pixelSize: 11 }
    }
    Text { text: 'Drag to adjust · Double-click the graph to add · Arrow keys for fine control'; color: root.colors.mutedText; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    onPointsChanged: { selectedPoint = Math.min(selectedPoint, points.length-1); graph.requestPaint() }
    onSelectedPointChanged: graph.requestPaint()
    onDisplayUnitChanged: graph.requestPaint()
    onCalibrationChanged: graph.requestPaint()
    onColorsChanged: graph.requestPaint()
}
