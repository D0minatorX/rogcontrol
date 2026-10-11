import QtQuick
import QtQuick.Layouts
ColumnLayout {
    id: root
    property var colors
    Repeater {
        model: [{key:'ac_profile', label:'On AC power'}, {key:'battery_profile', label:'On battery'}, {key:'usbc_profile', label:'On Type-C charger'}]
        SettingsChoice {
            required property var modelData
            Layout.fillWidth: true
            colors: root.colors; title: modelData.label
            choices: [''].concat(appController.profileNames)
            labels: ['Don’t auto-switch'].concat(appController.profileNames)
            value: quickAccessController.settings[modelData.key] || ''
            enabled: !appController.busy && (modelData.key !== 'gamescope_profile' || !!quickAccessController.data.gamescope)
            onEdited: value => quickAccessController.setAutomaticProfile(modelData.key, value)
        }
    }
    Text { text: 'Choices apply when the power source changes.'; color: root.colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 11 }
}
