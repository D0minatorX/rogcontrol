import QtQuick
import QtQuick.Controls
Button {
    id: root
    property var colors
    property bool primary: false
    implicitHeight: 34
    implicitWidth: Math.max(90, label.implicitWidth + 26)
    contentItem: Text { id: label; text: root.text; color: root.enabled ? (root.primary ? '#ffffff' : root.colors.text) : root.colors.mutedText; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
    background: Rectangle { color: root.primary ? root.colors.accent : root.hovered ? root.colors.hover : root.colors.panel; border.color: root.activeFocus ? root.colors.accent : root.colors.border; opacity: root.enabled ? 1 : .5; radius: 3 }
    Accessible.name: text
}
