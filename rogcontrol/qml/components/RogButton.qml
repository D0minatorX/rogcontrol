import QtQuick
import QtQuick.Controls
Button {
    id: root
    property var colors
    property bool primary: false
    implicitHeight: 36
    hoverEnabled: true
    implicitWidth: Math.max(90, label.implicitWidth + 26)
    contentItem: Text { id: label; text: root.text; color: root.enabled ? (root.primary ? '#ffffff' : root.colors.text) : root.colors.mutedText; font.pixelSize: 12; font.weight: root.primary ? Font.DemiBold : Font.Medium; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
    background: Rectangle {
        radius: 9
        color: root.down ? root.colors.selected : root.primary ? root.colors.accent : root.hovered ? root.colors.hover : root.colors.hover
        border.color: root.activeFocus ? root.colors.accent : root.primary ? 'transparent' : root.hovered ? root.colors.border : 'transparent'
        opacity: root.enabled ? 1 : .4
        Behavior on color { ColorAnimation { duration: 100 } }
    }
    Accessible.name: text
}
