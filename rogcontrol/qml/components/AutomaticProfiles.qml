import QtQuick
import QtQuick.Layouts
ColumnLayout {
    id: root
    property var colors
    Repeater {
        model: [{key:'ac_profile', label:'AC power'}, {key:'battery_profile', label:'Battery'}, {key:'usbc_profile', label:'USB-C power'}, {key:'gamescope_profile', label:'Gamescope'}]
        SettingsChoice {
            required property var modelData
            Layout.fillWidth: true
            colors: root.colors; title: modelData.label
            choices: [''].concat(appController.profileNames)
            labels: ['No automatic switch'].concat(appController.profileNames)
            value: quickAccessController.settings[modelData.key] || ''
            enabled: !appController.busy && (modelData.key !== 'gamescope_profile' || !!quickAccessController.data.gamescope)
            onEdited: value => quickAccessController.setAutomaticProfile(modelData.key, value)
        }
    }
    Text { text: 'Choices apply when the power source or Gamescope session changes.'; color: root.colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 11 }
}
