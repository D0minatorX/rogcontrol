import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ColumnLayout {
    id: root
    property var colors
    property var points: []
    property var calibration: [0, 1]
    property string displayUnit: 'percent'
    signal pointMoved(int index, real temperature, real percent)
    function ordinate(pct) { return displayUnit === 'rpm' ? (pct === 0 ? 0 : calibration[0] + calibration[1] * pct) : pct }
    function percent(value) { return displayUnit === 'rpm' ? (value === 0 ? 0 : Math.max(0, Math.min(100, (value - calibration[0]) / calibration[1]))) : value }
    Canvas {
        id: graph
        Layout.fillWidth: true; Layout.preferredHeight: 170
        onPaint: {
            var ctx = getContext('2d'); ctx.reset(); ctx.fillStyle = root.colors.window; ctx.fillRect(0, 0, width, height)
            ctx.strokeStyle = root.colors.border; ctx.lineWidth = 1
            for (var line = 1; line < 5; line++) { ctx.beginPath(); ctx.moveTo(0, line * height / 5); ctx.lineTo(width, line * height / 5); ctx.stroke() }
            ctx.strokeStyle = root.colors.accent; ctx.lineWidth = 2; ctx.beginPath()
            for (var i = 0; i < root.points.length; i++) { var x = root.points[i][0] / 100 * width; var y = (1 - root.points[i][1] / 100) * height; if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y) }
            ctx.stroke()
            ctx.fillStyle = root.colors.accent
            for (i = 0; i < root.points.length; i++) { ctx.beginPath(); ctx.arc(root.points[i][0] / 100 * width, (1 - root.points[i][1] / 100) * height, 5, 0, Math.PI * 2); ctx.fill() }
        }
        MouseArea {
            anchors.fill: parent
            property int selectedPoint: -1
            onPressed: mouse => { var distance = 400; selectedPoint = -1; for (var i = 0; i < root.points.length; i++) { var dx = mouse.x - root.points[i][0] / 100 * width; var dy = mouse.y - (1 - root.points[i][1] / 100) * height; var d = dx * dx + dy * dy; if (d < distance) { distance = d; selectedPoint = i } } }
            onPositionChanged: mouse => { if (pressed && selectedPoint >= 0) root.pointMoved(selectedPoint, Math.max(0, Math.min(100, mouse.x / width * 100)), Math.max(0, Math.min(100, (1 - mouse.y / height) * 100))) }
            onReleased: selectedPoint = -1
        }
    }
    Text { text: 'Temperature °C / ' + (root.displayUnit === 'rpm' ? 'estimated RPM from calibration' : 'fan duty %') + ' · Drag a point or edit below'; color: root.colors.mutedText; font.pixelSize: 10; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    GridLayout {
        Layout.fillWidth: true
        columns: width < 430 ? 2 : 4
        Repeater {
            model: root.points.length
            RowLayout {
                required property int index
                TextField { Layout.preferredWidth: 50; text: root.points[parent.index][0].toString(); color: root.colors.text; validator: IntValidator { bottom: 0; top: 100 } Accessible.name: 'Point ' + (parent.index + 1) + ' temperature'; onEditingFinished: if (acceptableInput) root.pointMoved(parent.index, Number(text), root.points[parent.index][1]) }
                Text { text: '°C'; color: root.colors.mutedText; font.pixelSize: 10 }
                TextField { Layout.preferredWidth: root.displayUnit === 'rpm' ? 64 : 50; text: Math.round(root.ordinate(root.points[parent.index][1])).toString(); color: root.colors.text; validator: IntValidator { bottom: 0; top: root.displayUnit === 'rpm' ? Math.ceil(root.ordinate(100)) : 100 } Accessible.name: 'Point ' + (parent.index + 1) + ' speed'; onEditingFinished: if (acceptableInput) root.pointMoved(parent.index, root.points[parent.index][0], root.percent(Number(text))) }
            }
        }
    }
    onPointsChanged: graph.requestPaint()
    onColorsChanged: graph.requestPaint()
}
