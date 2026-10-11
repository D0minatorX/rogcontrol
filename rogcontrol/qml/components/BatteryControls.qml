import QtQuick
import QtQuick.Layouts
ColumnLayout {
    id: root
    property var colors
    property var readings: batteryController.data
    spacing: 14
    MetricCard {
        title: 'Battery'; colors: root.colors; Layout.fillWidth: true
        RowLayout {
            Layout.fillWidth: true; spacing: 16
            ColumnLayout {
                Layout.fillWidth: true; spacing: 8
                Text { text: root.readings.percent === undefined || root.readings.percent === null ? '—' : root.readings.percent + '%'; color: root.colors.text; font.pixelSize: 48; font.weight: Font.Medium }
                Text { text: (root.readings.charging === undefined || root.readings.charging === null ? 'State unavailable' : root.readings.charging ? 'Charging' : 'Not charging') + '  ·  ' + (root.readings.source || 'Unknown source'); color: root.colors.mutedText; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            }
            Rectangle {
                Layout.preferredWidth: 58; Layout.preferredHeight: 30; Layout.rightMargin: 4; Layout.alignment: Qt.AlignVCenter
                color: 'transparent'; radius: 6; border.color: root.colors.border; border.width: 2
                Rectangle { x: 4; y: 4; height: parent.height-8; radius: 2; width: (parent.width-8) * Math.max(0,Math.min(100,Number(root.readings.percent || 0)))/100; color: root.colors.accent }
                Rectangle { width: 3; height: 12; anchors.left: parent.right; anchors.verticalCenter: parent.verticalCenter; radius: 2; color: root.colors.border }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: root.colors.border }
        GridLayout {
            columns: root.width < 260 ? 1 : root.width < 600 ? 2 : 3; Layout.fillWidth: true; columnSpacing: 22; rowSpacing: 16
            Repeater {
                model: [
                    {label:'HEALTH', value: root.readings.health && root.readings.health.percent !== null && root.readings.health.percent !== undefined ? Number(root.readings.health.percent).toFixed(1) + '%' : '—'},
                    {label:'FULL / DESIGN CAPACITY', value: root.readings.health ? Number(root.readings.health.full).toFixed(1) + ' / ' + Number(root.readings.health.design).toFixed(1) + ' ' + root.readings.health.unit : '—'},
                    {label:'CYCLE COUNT', available: !!root.readings.health && root.readings.health.cycles > 0, value: root.readings.health && root.readings.health.cycles !== null && root.readings.health.cycles !== undefined ? String(root.readings.health.cycles) : '—'}
                ].filter(reading => reading.available !== false)
                ColumnLayout {
                    required property var modelData
                    Layout.fillWidth: true; Layout.preferredWidth: 1; spacing: 8
                    Text { text: modelData.label; color: root.colors.mutedText; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Text { text: modelData.value; color: root.colors.text; font.pixelSize: 20; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                }
            }
        }
        Text { visible: !!root.readings.health && root.readings.health.percent > 100; text: 'A new battery can report more than 100% of its design capacity.'; color: root.colors.mutedText; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    }
    MetricCard {
        objectName: 'chargingCard'; visible: !!root.readings.charge_supported; title: 'Charging'; colors: root.colors; Layout.fillWidth: true
        SliderRow { Layout.fillWidth: true; colors: root.colors; title: 'Charge limit'; unit: '%'; value: batteryController.settings.charge_limit === undefined ? 100 : batteryController.settings.charge_limit; enabled: !!root.readings.charge_supported && !appController.busy; onEdited: value => batteryController.setLimit(Math.round(value)) }
        StatusRow { colors: root.colors; label: 'Firmware charge cap'; value: root.readings.limit === undefined || root.readings.limit === null ? 'Unsupported' : root.readings.limit + '%' }
        RogButton { colors: root.colors; text: root.readings.once ? 'Cancel one full charge' : 'Charge to 100% once'; enabled: !!root.readings.charge_supported && !appController.busy; onClicked: batteryController.toggleOnce() }
        Text { visible: !!root.readings.once; text: 'One full charge is active. The normal cap is restored on full charge or unplugging.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    }
    Text { visible: text.length > 0; text: batteryController.status; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
}
