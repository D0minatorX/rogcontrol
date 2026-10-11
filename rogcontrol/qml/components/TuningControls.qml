import QtQuick
import QtQuick.Controls
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
            Switch {
                visible: modelData.kind === 'bool'
                text: modelData.title; checked: !!modelData.value; enabled: modelData.enabled
                palette.windowText: root.colors.text; palette.highlight: root.colors.accent
                Accessible.name: text
                onToggled: root.controller.stageValue(modelData.key, checked)
            }
            RowLayout {
                visible: modelData.kind === 'choice'; Layout.fillWidth: true
                Text { Layout.fillWidth: true; text: modelData.title; color: root.colors.text; font.pixelSize: 12; wrapMode: Text.WordWrap }
                RogComboBox { colors: root.colors; Layout.preferredWidth: 230; Layout.maximumWidth: root.width * .6; model: modelData.choiceLabels || modelData.choices || []; currentIndex: (modelData.choices || []).indexOf(modelData.value); enabled: modelData.enabled; Accessible.name: modelData.title; onActivated: root.controller.stageValue(modelData.key, modelData.choices[currentIndex]); palette.button: root.colors.panel; palette.buttonText: root.colors.text }
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
