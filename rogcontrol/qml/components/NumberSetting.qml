import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
RowLayout {
    id: root
    property var colors
    property string title
    property string unit
    property int value: 0
    property int minimum: 0
    property int maximum: 100
    property int step: 1
    Text { text: root.title; color: root.colors.text; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    SpinBox {
        id: spin
        from: root.minimum; to: root.maximum; stepSize: root.step; value: root.value
        onValueModified: root.value = value
        editable: true; implicitHeight: 38; implicitWidth: 116
        Accessible.name: root.title
        palette.text: root.colors.text; palette.base: root.colors.window
        palette.button: root.colors.hover; palette.buttonText: root.colors.text; palette.highlight: root.colors.accent
        background: Rectangle { radius: 5; color: root.colors.window; border.color: spin.activeFocus ? root.colors.accent : root.colors.border }
    }
    Text { text: root.unit; color: root.colors.mutedText; font.pixelSize: 11; Layout.preferredWidth: 18 }
}
