import QtQuick
import QtQuick.Layouts
ColumnLayout {
    id: root
    property var colors
    property var readings: batteryController.data
    spacing: 14
    MetricCard {
        objectName: 'chargingCard'; visible: !!root.readings.charge_supported; title: 'Charging'; colors: root.colors; Layout.fillWidth: true
        SliderRow { Layout.fillWidth: true; colors: root.colors; title: 'Charge limit'; unit: '%'; value: batteryController.settings.charge_limit === undefined ? 100 : batteryController.settings.charge_limit; enabled: !!root.readings.charge_supported && !appController.busy; onEdited: value => batteryController.setLimit(Math.round(value)) }
        StatusRow { colors: root.colors; label: 'Firmware charge cap'; value: root.readings.limit === undefined || root.readings.limit === null ? 'Unsupported' : root.readings.limit + '%' }
        RogButton { colors: root.colors; text: root.readings.once ? 'Cancel one full charge' : 'Charge to 100% once'; enabled: !!root.readings.charge_supported && !appController.busy; onClicked: batteryController.toggleOnce() }
        Text { visible: !!root.readings.once; text: 'One full charge is active. The normal cap is restored on full charge or unplugging.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    }
    Text { visible: text.length > 0; text: batteryController.status; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
}
