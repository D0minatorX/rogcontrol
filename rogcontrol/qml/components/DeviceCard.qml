import QtQuick
import QtQuick.Layouts
MetricCard {
    id: root
    property string device: 'cpu'
    property var readings: ({})
    title: device.toUpperCase()
    function format(value, unit) { return value === null || value === undefined ? '—' : Math.round(Number(value)).toLocaleString(Qt.locale(), 'f', 0) + (unit ? ' ' + unit : '') }
    Text {
        text: root.readings[root.device + '_name'] || 'Detecting device…'
        color: root.colors.mutedText; font.pixelSize: 11
        elide: Text.ElideRight; Layout.fillWidth: true
    }
    Item {
        Layout.fillWidth: true; Layout.preferredHeight: 150
        Canvas {
            id: gauge
            anchors.centerIn: parent; width: 210; height: 164
            property real frequency: Number(root.readings[root.device + '_clock'] || 0)
            onFrequencyChanged: requestPaint()
            onPaint: {
                var ctx = getContext('2d'); ctx.reset()
                var start = Math.PI * .8, end = Math.PI * 2.2
                ctx.lineWidth = 6; ctx.lineCap = 'round'; ctx.strokeStyle = root.colors.border
                ctx.beginPath(); ctx.arc(105, 91, 75, start, end); ctx.stroke()
                ctx.strokeStyle = root.colors.accent
                ctx.beginPath(); ctx.arc(105, 91, 75, start, start + (end-start) * Math.min(frequency / (root.device === 'gpu' ? 3000 : 6000), 1)); ctx.stroke()
            }
            Connections { target: root.colors; function onAccentChanged() { gauge.requestPaint() } function onBorderChanged() { gauge.requestPaint() } }
        }
        Column {
            anchors.centerIn: parent; anchors.verticalCenterOffset: 8; spacing: 3
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: root.format(root.readings[root.device + '_clock'], ''); color: root.colors.text; font.pixelSize: 38; font.weight: Font.Medium }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: 'MHz'; color: root.colors.mutedText; font.pixelSize: 11 }
        }
    }
    RowLayout {
        Layout.fillWidth: true; spacing: 12
        Repeater {
            model: [{label: 'TEMPERATURE', value: root.readings[root.device + '_temp'], unit: '°C'}, {label: 'POWER', value: root.readings[root.device + '_power'], unit: 'W'}, {label: 'FAN', value: (root.readings.fan_rpm || {})[root.device === 'cpu' ? '1' : '2'], unit: 'RPM'}]
            ColumnLayout {
                required property var modelData
                Layout.fillWidth: true; Layout.preferredWidth: 1; spacing: 6
                Text { Layout.fillWidth: true; text: modelData.label; color: root.colors.mutedText; font.pixelSize: 9; font.letterSpacing: .6 }
                Text { Layout.fillWidth: true; text: root.format(modelData.value, modelData.unit); color: root.colors.text; font.pixelSize: 17; font.weight: Font.Medium }
            }
        }
    }
    Text { visible: root.device === 'gpu' && root.readings.dgpu_suspended === true; text: 'GPU sleeping'; color: root.colors.mutedText; font.pixelSize: 11 }
}
