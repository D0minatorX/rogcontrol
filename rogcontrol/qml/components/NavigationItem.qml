import QtQuick
import QtQuick.Controls
Button {
    id: root
    property string symbol: "◉"
    property bool selected: false
    property var colors
    implicitWidth: 58
    implicitHeight: 62
    Accessible.name: text
    contentItem: Column {
        spacing: 5
        Text { text: root.symbol; color: root.selected ? root.colors.accent : root.colors.mutedText; font.pixelSize: 22; anchors.horizontalCenter: parent.horizontalCenter }
        Text { text: root.text; color: root.selected ? root.colors.accent : root.colors.mutedText; font.pixelSize: 10; anchors.horizontalCenter: parent.horizontalCenter }
    }
    background: Rectangle {
        color: root.selected ? root.colors.selected : root.hovered ? root.colors.hover : "transparent"
        Rectangle { width: 2; height: parent.height; color: root.colors.accent; visible: root.selected }
        border.color: root.activeFocus ? root.colors.text : "transparent"
    }
}
