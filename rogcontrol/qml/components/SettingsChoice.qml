import QtQuick
import "SettingHelp.js" as Help
import QtQuick.Layouts
ColumnLayout {
    id: root
    property var colors
    property string helpText: Help.forTitle(title)
    property string title
    property string description: ''
    property var choices: []
    property var labels: choices
    property var value
    signal edited(var value)
    RowLayout {
        Layout.fillWidth: true
    Text { text: root.title; color: root.colors.text; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 12 }
        HelpInfo { colors: root.colors; helpText: root.helpText }
    }
    RogComboBox {
        colors: root.colors
        Layout.fillWidth: true
        model: root.labels
        currentIndex: root.choices.indexOf(root.value)
        Accessible.name: root.title
        palette.button: root.colors.panel; palette.buttonText: root.colors.text; palette.text: root.colors.text; palette.base: root.colors.window
        onActivated: root.edited(root.choices[currentIndex])
    }
    Text { visible: text.length > 0; text: root.description; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
}
