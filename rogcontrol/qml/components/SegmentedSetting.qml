import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: root
    property var colors
    property string title: ''
    property bool navigation: false
    property var choices: []
    property var secondaryLabels: []
    property int minimum: 0
    property int value: 0
    property string repeaterName: ''
    property string buttonPrefix: ''
    signal edited(int value)
    spacing: 8

    Text {
        visible: root.title.length > 0
        text: root.title
        color: root.colors.text
        font.pixelSize: 12
    }
    Rectangle {
        Layout.fillWidth: true
        implicitHeight: root.secondaryLabels.length > 0 ? 58 : root.navigation ? 42 : 44
        radius: 11
        color: root.navigation ? 'transparent' : root.colors.window
        border.color: root.navigation ? 'transparent' : root.colors.border
        Rectangle {
            visible: root.navigation
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 1
            color: root.colors.border
        }
        RowLayout {
            id: segments
            anchors.fill: parent
            anchors.margins: root.navigation ? 0 : 4
            spacing: 4
            Repeater {
                objectName: root.repeaterName
                model: root.choices
                RogButton {
                    required property int index
                    required property string modelData
                    objectName: root.buttonPrefix + index
                    colors: root.colors
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    implicitWidth: 0
                    implicitHeight: root.secondaryLabels.length > 0 ? 50 : 36
                    text: modelData
                    primary: root.value === index + root.minimum
                    Accessible.name: (root.title.length ? root.title + ': ' : '') + modelData + (root.secondaryLabels[index] ? ', ' + root.secondaryLabels[index] : '')
                    Accessible.checkable: true
                    Accessible.checked: primary
                    contentItem: Column {
                        spacing: 3
                        Text {
                            width: parent.width
                            text: modelData
                            color: root.navigation ? (root.value === index + root.minimum ? root.colors.text : root.colors.mutedText) : (root.value === index + root.minimum ? '#ffffff' : root.colors.text)
                            font.pixelSize: root.navigation ? 13 : 12
                            font.weight: root.value === index + root.minimum ? Font.DemiBold : Font.Medium
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                        }
                        Text {
                            visible: root.secondaryLabels.length > 0
                            width: parent.width
                            text: root.secondaryLabels[index] || ''
                            color: root.colors.mutedText
                            font.pixelSize: 11
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                        }
                    }
                    background: Rectangle {
                        radius: 7
                        color: root.navigation ? (parent.hovered ? root.colors.hover : 'transparent') : (parent.primary ? root.colors.accent : parent.hovered ? root.colors.hover : 'transparent')
                        border.color: parent.visualFocus ? root.colors.accent : 'transparent'
                        Behavior on color { ColorAnimation { duration: 120 } }
                    }
                    onClicked: root.edited(index + root.minimum)
                }
            }
        }
        Rectangle {
            visible: root.navigation && root.choices.length > 0
            readonly property real segmentWidth: (segments.width - segments.spacing * (root.choices.length - 1)) / Math.max(1, root.choices.length)
            x: (root.value - root.minimum) * (segmentWidth + segments.spacing) + 12
            y: parent.height - height
            width: Math.max(0, segmentWidth - 24)
            height: 2
            radius: 1
            color: root.colors.accent
            Behavior on x { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
        }
    }
}
