import QtQuick
import QtQuick.Layouts
MetricCard {
    id: root
    property string device: 'cpu'
    property var readings: ({})
    title: device.toUpperCase()
    contentSpacing: 8

    function format(value, unit, decimals) {
        if (value === null || value === undefined || value === '') return '—'
        var number = Number(value)
        if (!isFinite(number)) return '—'
        return number.toLocaleString(Qt.locale('en_US'), 'f', decimals === undefined ? 0 : decimals) + (unit ? ' ' + unit : '')
    }
    function fraction(value, maxValue) {
        var number = Number(value)
        return value === null || value === undefined || !isFinite(number) ? 0.38 : Math.max(0.04, Math.min(number / maxValue, 1))
    }

    Text {
        text: root.readings[root.device + '_name'] || 'Detecting device…'
        color: root.colors.mutedText; font.pixelSize: 11
        elide: Text.ElideRight; Layout.fillWidth: true
    }

    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4
        Repeater {
            model: [
                { label: 'Frequency', key: root.device + '_clock', unit: 'MHz', max: root.device === 'gpu' ? 3000 : 6000, accent: true },
                { label: 'Power', key: root.device + '_power', unit: 'W', max: root.device === 'gpu' ? 150 : 100, accent: true },
                { label: 'Temperature', key: root.device + '_temp', unit: '°C', max: 110 },
                { label: 'Fan speed', key: 'fan', unit: 'RPM', max: 6000 },
                { label: 'Memory', key: 'memory', unit: '' }
            ]
            delegate: ColumnLayout {
                required property var modelData
                Layout.fillWidth: true
                spacing: modelData.accent === true ? 3 : 0
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: modelData.key === 'memory' ? (root.device === 'gpu' ? 'VRAM' : 'RAM') : modelData.label
                        color: root.colors.mutedText
                        font.pixelSize: 12
                        Layout.fillWidth: true
                    }
                    Text {
                        text: {
                            if (modelData.key === 'fan')
                                return root.format((root.readings.fan_rpm || {})[root.device === 'cpu' ? '1' : '2'], modelData.unit)
                            if (modelData.key === 'memory') {
                                var memory = root.readings[root.device === 'gpu' ? 'vram' : 'ram']
                                if (!memory || memory.length < 2) return '—'
                                var used = Number(memory[0]), total = Number(memory[1])
                                if (!isFinite(used) || !isFinite(total) || used < 0 || total <= 0) return '—'
                                return (used / 1024).toFixed(1) + ' / ' + (total / 1024).toFixed(1) + ' GiB'
                            }
                            return root.format(root.readings[modelData.key], modelData.unit, modelData.decimals)
                        }
                        color: root.colors.text
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignRight
                    }
                }
                Rectangle {
                    visible: modelData.accent === true
                    Layout.fillWidth: true
                    Layout.preferredHeight: 2
                    radius: 2
                    color: root.colors.border
                    Rectangle {
                        width: parent.width * root.fraction(root.readings[modelData.key], modelData.max)
                        height: parent.height
                        radius: parent.radius
                        color: root.colors.accent
                    }
                }
            }
        }
    }

    Text { visible: root.device === 'gpu' && root.readings.dgpu_suspended === true; text: 'GPU sleeping'; color: root.colors.mutedText; font.pixelSize: 11 }
}
