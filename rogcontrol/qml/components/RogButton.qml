import QtQuick
import QtQuick.Controls
Button {
    id: root
    property var colors
    property bool primary: false
    implicitHeight: 36
    hoverEnabled: true
    implicitWidth: Math.max(90, label.implicitWidth + 26)
    contentItem: Text { id: label; text: root.text; color: root.enabled ? (root.primary ? '#ffffff' : root.colors.text) : root.colors.mutedText; font.pixelSize: 13; font.weight: root.primary ? Font.DemiBold : Font.Medium; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
    background: AngularPanel {
        cut: 7; decorated: false
        fillColor: root.primary ? root.colors.accent : root.down ? root.colors.selected : root.hovered ? root.colors.hover : root.colors.sidebar
        lineColor: root.activeFocus || root.primary ? root.colors.accent : root.colors.border
        accentColor: root.colors.accent
        opacity: root.enabled ? 1 : .4
    }
    Accessible.name: text
}
