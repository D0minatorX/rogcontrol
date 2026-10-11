import QtQuick
import "SettingHelp.js" as Help
import QtQuick.Layouts
ColumnLayout {
    id: root
    enabled: !appController.busy
    property var colors
    property var controller
    property var excludedKeys: []
    property var includedKeys: []
    readonly property bool hasControls: controller.rows.some(row => excludedKeys.indexOf(row.key) < 0 && (!includedKeys.length || includedKeys.indexOf(row.key) >= 0))
    function help(key) { return Help.forTuning(key, controller.rows) }
    spacing: 14
    Repeater {
        model: root.controller.rows.length
        ColumnLayout {
            required property int index
            property var modelData: root.controller.rows[index]
            Layout.fillWidth: true
            visible: root.excludedKeys.indexOf(modelData.key) < 0 && (!root.includedKeys.length || root.includedKeys.indexOf(modelData.key) >= 0)
            SettingsSwitch {
                colors: root.colors
                Layout.fillWidth: true
                visible: modelData.kind === 'bool'
                title: modelData.title; value: !!modelData.value; helpText: root.help(modelData.key); enabled: modelData.enabled
                onEdited: value => root.controller.stageValue(modelData.key, value)
            }
            SettingsChoice {
                visible: modelData.kind === 'choice'; Layout.fillWidth: true
                colors: root.colors; helpText: root.help(modelData.key)
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
                colors: root.colors; helpText: root.help(modelData.key); title: modelData.title; unit: modelData.unit || ''
                minimum: modelData.min || 0; maximum: modelData.max === undefined ? 100 : modelData.max; step: modelData.step || 1
                value: Number(modelData.value) / (modelData.scale || 1)
                onEdited: value => root.controller.stageValue(modelData.key, value)
            }
        }
    }
}
