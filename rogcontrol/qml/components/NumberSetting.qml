import QtQuick
import "SettingHelp.js" as Help
import QtQuick.Controls
import QtQuick.Layouts
RowLayout {
    id: root
    property var colors
    property string helpText: Help.forTitle(title)
    property string title
    property string unit
    property int value: 0
    property int minimum: 0
    property int maximum: 100
    property int step: 1
    signal edited(int value)
    spacing: 12
    Text { text: root.title; color: root.colors.text; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    HelpInfo { colors: root.colors; helpText: root.helpText }
    SpinBox {
        id: spin
        from: root.minimum; to: root.maximum; stepSize: root.step; value: root.value
        onValueModified: { root.value = value; root.edited(value) }
        editable: true; implicitHeight: 38; implicitWidth: 138
        leftPadding: 38; rightPadding: 38
        Accessible.name: root.title
        opacity: enabled ? 1 : .45
        contentItem: TextInput {
            text: spin.textFromValue(spin.value, spin.locale)
            font.pixelSize: 14; font.weight: Font.Medium; color: root.colors.text
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            readOnly: !spin.editable; validator: spin.validator
            inputMethodHints: Qt.ImhDigitsOnly
            selectionColor: root.colors.accent; selectedTextColor: '#ffffff'
        }
        down.indicator: Rectangle {
            x: 3; y: 3; width: 32; height: spin.height-6; radius: 5
            color: spin.down.pressed ? root.colors.selected : spin.down.hovered ? root.colors.hover : 'transparent'
            Text { anchors.centerIn: parent; text: '−'; font.pixelSize: 20; color: spin.value > spin.from ? root.colors.text : root.colors.mutedText }
        }
        up.indicator: Rectangle {
            x: spin.width-width-3; y: 3; width: 32; height: spin.height-6; radius: 5
            color: spin.up.pressed ? root.colors.selected : spin.up.hovered ? root.colors.hover : 'transparent'
            Text { anchors.centerIn: parent; text: '+'; font.pixelSize: 18; color: spin.value < spin.to ? root.colors.text : root.colors.mutedText }
        }
        background: Rectangle { radius: 10; color: root.colors.window; border.color: spin.activeFocus ? root.colors.accent : 'transparent' }
    }
    Text { text: root.unit; color: root.colors.mutedText; font.pixelSize: 11; Layout.preferredWidth: 18 }
}
