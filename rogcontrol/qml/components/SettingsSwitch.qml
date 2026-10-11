import QtQuick
import QtQuick.Layouts
ColumnLayout {
    id: root
    property var colors
    property string title
    property string description: ''
    property bool value: false
    signal edited(bool value)
    RogSwitch {
        colors: root.colors
        Layout.fillWidth: true
        text: root.title; checked: root.value
        palette.windowText: root.colors.text
        Accessible.name: root.title
        onClicked: root.edited(checked)
    }
    Text { visible: text.length > 0; text: root.description; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
}
