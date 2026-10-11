import QtQuick
import QtQuick.Controls
Button {
    id: root
    property var colors
    property string helpText: ''
    visible: helpText.length > 0
    implicitWidth: 20; implicitHeight: 20
    hoverEnabled: true
    Accessible.name: 'Information'
    Accessible.description: helpText
    contentItem: Text { text: '!'; font.pixelSize: 12; font.bold: true; color: root.hovered ? root.colors.text : root.colors.mutedText; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
    background: Rectangle { radius: 10; color: 'transparent'; border.color: root.hovered ? root.colors.accent : root.colors.border }
    onClicked: tip.open()
    ToolTip {
        id: tip
        visible: root.hovered || root.activeFocus
        delay: 250; timeout: 12000
        text: root.helpText
        width: 280
        contentItem: Text { text: tip.text; color: root.colors.text; wrapMode: Text.WordWrap; font.pixelSize: 12 }
        background: Rectangle { radius: 6; color: root.colors.panel; border.color: root.colors.border }
    }
}
