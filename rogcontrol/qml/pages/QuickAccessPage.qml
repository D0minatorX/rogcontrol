import QtQuick
import QtQuick.Layouts
import "../components"
ColumnLayout {
    id: root
    property var colors
    spacing: 14
    OverviewPage { colors: root.colors; Layout.fillWidth: true }
    MetricCard {
        title: 'Automatic profile switching'; colors: root.colors; Layout.fillWidth: true
        Repeater {
            model: [{key:'start_on_boot', label:'Start on boot'}, {key:'ac_profile', label:'AC power'}, {key:'battery_profile', label:'Battery'}, {key:'usbc_profile', label:'USB-C power'}, {key:'gamescope_profile', label:'Gamescope'}]
            StatusRow { required property var modelData; colors: root.colors; label: modelData.label; value: modelData.key === 'start_on_boot' ? quickAccessController.settings[modelData.key] ? 'Enabled' : 'Disabled' : quickAccessController.settings[modelData.key] || 'No automatic switch' }
        }
    }
}
