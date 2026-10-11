import QtQuick
import QtQuick.Layouts
ColumnLayout {
    id: root
    property var colors
    property var readings: batteryController.data
    spacing: 12
    RowLayout {
        Layout.fillWidth: true
        Text { text: 'INTERNAL DISPLAY'; color: root.colors.mutedText; font.pixelSize: 10; font.letterSpacing: 1; Layout.fillWidth: true }
        Text { text: root.readings.current_rate === null || root.readings.current_rate === undefined ? 'Unavailable' : root.readings.current_rate + ' Hz'; color: root.colors.text; font.pixelSize: 12 }
    }
    Flow {
        Layout.fillWidth: true; spacing: 6
        Repeater {
            model: root.readings.rates || []
            RogButton {
                required property var modelData
                colors: root.colors; text: modelData + ' Hz'; primary: modelData === root.readings.current_rate
                implicitWidth: 78
                enabled: !!root.readings.display_supported && !appController.busy
                onClicked: batteryController.setRefreshRate(Number(modelData))
            }
        }
    }
    SettingsSwitch { colors: root.colors; title: 'Lower refresh rate on battery'; value: !!batteryController.settings.auto_display_refresh; enabled: !!root.readings.display_supported && !appController.busy; description: 'Restores your AC refresh rate when plugged in.'; onEdited: value => batteryController.setDisplay('auto_display_refresh', value) }
    SettingsChoice { visible: !!batteryController.settings.auto_display_refresh; colors: root.colors; title: 'On battery'; choices: root.readings.rates || []; labels: choices.map(rate => rate + ' Hz'); value: batteryController.settings.battery_display_refresh_hz || 60; enabled: !!root.readings.display_supported && choices.length > 0 && !appController.busy; onEdited: value => batteryController.setDisplay('battery_display_refresh_hz', value); Layout.fillWidth: true }
    Text { text: root.readings.display_reason || ''; visible: text.length > 0; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
    RogButton { colors: root.colors; text: 'Detect display'; enabled: !appController.busy; onClicked: batteryController.refresh() }
    Text { text: batteryController.status; visible: text.length > 0; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
}
