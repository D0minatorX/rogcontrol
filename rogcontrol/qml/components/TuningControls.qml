import QtQuick
import QtQuick.Layouts
ColumnLayout {
    id: root
    property var colors
    property var controller
    property var excludedKeys: []
    spacing: 12
    Repeater {
        model: root.controller.rows.length
        ColumnLayout {
            required property int index
            property var modelData: root.controller.rows[index]
            Layout.fillWidth: true
            visible: root.excludedKeys.indexOf(modelData.key) < 0
            RogSwitch {
                colors: root.colors
                Layout.fillWidth: true
                visible: modelData.kind === 'bool'
                text: modelData.title; checked: !!modelData.value; enabled: modelData.enabled
                palette.windowText: root.colors.text; palette.highlight: root.colors.accent
                Accessible.name: text
                onClicked: root.controller.stageValue(modelData.key, checked)
            }
            SettingsChoice {
                visible: modelData.kind === 'choice'; Layout.fillWidth: true
                colors: root.colors
                title: modelData.key === 'epp' ? 'Energy preference (governor: ' + (root.controller.live.governor || 'unavailable') + ')' : modelData.title
                choices: modelData.choices || []; labels: modelData.choiceLabels || modelData.choices || []
                value: modelData.value; enabled: modelData.enabled
                onEdited: value => root.controller.stageValue(modelData.key, value)
            }
            SliderRow {
                Layout.fillWidth: true
                visible: modelData.kind === 'number'
                enabled: modelData.enabled
                opacity: enabled ? 1 : .45
                colors: root.colors; title: modelData.title; unit: modelData.unit || ''
                minimum: modelData.min || 0; maximum: modelData.max === undefined ? 100 : modelData.max; step: modelData.step || 1
                value: Number(modelData.value) / (modelData.scale || 1)
                onEdited: value => root.controller.stageValue(modelData.key, value)
            }
        }
    }
}
