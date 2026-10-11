import QtQuick
import QtQuick.Layouts
ColumnLayout {
    id: root
    property var colors
    property var readings: batteryController.data
    property var rates: readings.rates || []
    spacing: 10
    GridLayout {
        Layout.fillWidth: true
        columns: root.width < 380 ? 1 : 2
        columnSpacing: 16; rowSpacing: 10
        ColumnLayout {
            Layout.fillWidth: true
            Text { text: 'Automatic refresh switching'; color: root.colors.text; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Text { text: 'Use this rate on battery; restore the previous rate on charger.'; color: root.colors.mutedText; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            spacing: 10
            RogComboBox {
                colors: root.colors; Layout.preferredWidth: 100
                model: root.rates.map(rate => rate + ' Hz')
                currentIndex: root.rates.indexOf(batteryController.settings.battery_display_refresh_hz || 60)
                Accessible.name: 'Battery refresh rate'
                enabled: !!root.readings.display_supported && root.rates.length > 0 && !appController.busy
                onActivated: batteryController.setDisplay('battery_display_refresh_hz', root.rates[currentIndex])
            }
            RogSwitch {
                colors: root.colors; text: ''; implicitWidth: 44
                Accessible.name: 'Automatic refresh switching'
                checked: !!batteryController.settings.auto_display_refresh
                enabled: !!root.readings.display_supported && !appController.busy
                onToggled: batteryController.setDisplay('auto_display_refresh', checked)
            }
        }
    }
    Text { text: root.readings.display_reason || ''; visible: text.length > 0; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
    RogButton { visible: !root.readings.display_supported; colors: root.colors; text: 'Detect display'; enabled: !appController.busy; onClicked: batteryController.refresh() }
    Text { text: batteryController.status; visible: text.length > 0; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
}
