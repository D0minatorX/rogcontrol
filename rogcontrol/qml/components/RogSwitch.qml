import QtQuick
import QtQuick.Controls
Switch {
    id: control
    property var colors
    implicitHeight: Math.max(36, contentItem.implicitHeight + 8)
    implicitWidth: label.implicitWidth + 66
    leftPadding: 0; rightPadding: 0; spacing: 13
    opacity: enabled ? 1 : .4
    hoverEnabled: true
    indicator: Rectangle {
        implicitWidth: 44; implicitHeight: 24
        x: 0; y: (control.height-height)/2
        radius: 12
        color: control.checked ? control.colors.accent : control.colors.hover
        border.color: control.activeFocus ? control.colors.text : control.checked ? control.colors.accent : control.colors.border
        Behavior on color { ColorAnimation { duration: 120 } }
        Rectangle {
            width: 18; height: 18; radius: 9
            x: control.checked ? 23 : 3; y: 3
            color: control.enabled ? '#ffffff' : control.colors.mutedText
            Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
        }
    }
    contentItem: Text {
        id: label
        text: control.text; color: control.colors.text; font.pixelSize: 12
        leftPadding: control.indicator.width + control.spacing
        verticalAlignment: Text.AlignVCenter; wrapMode: Text.WordWrap
    }
    Accessible.name: text
}
