import QtQuick
import "SettingHelp.js" as Help
import QtQuick.Layouts
ColumnLayout {
    id: root
    Layout.fillWidth: true
    property var colors
    property string helpText: Help.forTitle(title)
    property string title
    property string description: ''
    property bool value: false
    property bool trailing: true
    signal edited(bool value)
    RowLayout {
        Layout.fillWidth: true; spacing: 12
        Text { visible: root.trailing; text: root.title; color: root.colors.text; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RogSwitch {
            colors: root.colors
            Layout.fillWidth: !root.trailing
            implicitWidth: root.trailing ? 44 : contentItem.implicitWidth + 66
            text: root.trailing ? '' : root.title; checked: root.value
            Accessible.name: root.title
            onClicked: root.edited(checked)
        }
        HelpInfo { colors: root.colors; helpText: root.helpText }
    }
    Text { visible: text.length > 0; text: root.description; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
}
