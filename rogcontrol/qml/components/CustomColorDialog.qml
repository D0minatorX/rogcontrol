import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    property var colors
    property color initialColor: '#ee2445'
    property real hue: 0
    property real saturation: 1
    property real brightness: 1
    property color draftColor: Qt.hsva(hue, saturation, brightness, 1)
    signal colorChosen(color value)

    function setColor(value) {
        const color = Qt.color(value)
        hue = color.hsvHue < 0 ? 0 : color.hsvHue
        saturation = color.hsvSaturation
        brightness = color.hsvValue
    }

    function updatePlane(x, y) {
        saturation = Math.max(0, Math.min(1, x / colourArea.width))
        brightness = 1 - Math.max(0, Math.min(1, y / colourArea.height))
    }

    function updateHue(y) {
        hue = Math.max(0, Math.min(1, y / hueStrip.height))
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(510, parent.width - 24)
    modal: true
    focus: true
    padding: 20
    closePolicy: Popup.CloseOnEscape
    onAboutToShow: setColor(initialColor)
    onAccepted: colorChosen(draftColor)
    background: Rectangle {
        radius: 16
        color: dialog.colors.panel
        border.color: dialog.colors.border
    }
    Overlay.modal: Rectangle { color: '#99000000' }

    header: RowLayout {
        spacing: 12
        Text {
            text: 'Custom colour'
            color: dialog.colors.text
            font.pixelSize: 18
            font.weight: Font.DemiBold
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.topMargin: 20
        }
        Button {
            id: closeButton
            implicitWidth: 30; implicitHeight: 30
            Layout.rightMargin: 20
            Layout.topMargin: 18
            Accessible.name: 'Close colour picker'
            contentItem: Text {
                text: '×'; color: dialog.colors.mutedText
                font.pixelSize: 22
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                radius: 8
                color: closeButton.hovered ? dialog.colors.hover : 'transparent'
                border.color: closeButton.activeFocus ? dialog.colors.accent : 'transparent'
            }
            onClicked: dialog.reject()
        }
    }

    contentItem: ColumnLayout {
        spacing: 14
        RowLayout {
            spacing: 12
            Rectangle {
                Layout.preferredWidth: 64; Layout.preferredHeight: 40
                radius: 9; color: dialog.draftColor
                border.color: dialog.colors.border
            }
            TextField {
                id: hexField
                objectName: 'customHexField'
                Layout.fillWidth: true
                implicitHeight: 40
                text: dialog.draftColor.toString().toUpperCase()
                color: dialog.colors.text
                font.family: 'monospace'; font.pixelSize: 14
                selectByMouse: true
                validator: RegularExpressionValidator { regularExpression: /#[0-9a-fA-F]{6}/ }
                Accessible.name: 'Custom hexadecimal colour'
                background: Rectangle {
                    radius: 9
                    color: dialog.colors.window
                    border.color: hexField.activeFocus ? dialog.colors.accent : dialog.colors.border
                }
                onEditingFinished: {
                    if (acceptableInput)
                        dialog.setColor(text)
                    else
                        text = dialog.draftColor.toString().toUpperCase()
                }
            }
        }

        RowLayout {
            spacing: 12
            Item {
                id: colourArea
                objectName: 'saturationValueArea'
                Layout.fillWidth: true
                Layout.preferredHeight: 270
                clip: true
                Rectangle { anchors.fill: parent; color: Qt.hsva(dialog.hue, 1, 1, 1) }
                Rectangle {
                    anchors.fill: parent
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0; color: '#ffffff' }
                        GradientStop { position: 1; color: 'transparent' }
                    }
                }
                Rectangle {
                    anchors.fill: parent
                    gradient: Gradient {
                        GradientStop { position: 0; color: 'transparent' }
                        GradientStop { position: 1; color: '#000000' }
                    }
                }
                Rectangle { anchors.fill: parent; color: 'transparent'; border.color: dialog.colors.border }
                Rectangle {
                    x: Math.max(1, Math.min(parent.width - width - 1, dialog.saturation * parent.width - width / 2))
                    y: Math.max(1, Math.min(parent.height - height - 1, (1 - dialog.brightness) * parent.height - height / 2))
                    width: 18; height: 18; radius: 9
                    color: 'transparent'; border.width: 2; border.color: '#ffffff'
                    Rectangle { anchors.fill: parent; anchors.margins: 3; radius: 6; color: 'transparent'; border.color: '#44000000' }
                }
                MouseArea {
                    anchors.fill: parent
                    onPressed: mouse => dialog.updatePlane(mouse.x, mouse.y)
                    onPositionChanged: mouse => { if (pressed) dialog.updatePlane(mouse.x, mouse.y) }
                }
            }
            Item {
                id: hueStrip
                objectName: 'hueStrip'
                Layout.preferredWidth: 30
                Layout.preferredHeight: colourArea.height
                Rectangle {
                    anchors.fill: parent; radius: 7
                    gradient: Gradient {
                        GradientStop { position: 0; color: '#ff0000' }
                        GradientStop { position: 0.167; color: '#ffff00' }
                        GradientStop { position: 0.333; color: '#00ff00' }
                        GradientStop { position: 0.5; color: '#00ffff' }
                        GradientStop { position: 0.667; color: '#0000ff' }
                        GradientStop { position: 0.833; color: '#ff00ff' }
                        GradientStop { position: 1; color: '#ff0000' }
                    }
                }
                Rectangle {
                    x: -3
                    y: Math.max(0, Math.min(parent.height - height, dialog.hue * parent.height - height / 2))
                    width: parent.width + 6; height: 8; radius: 4
                    color: '#ffffff'; border.color: '#66000000'
                }
                MouseArea {
                    anchors.fill: parent
                    onPressed: mouse => dialog.updateHue(mouse.y)
                    onPositionChanged: mouse => { if (pressed) dialog.updateHue(mouse.y) }
                }
            }
        }
        Text {
            text: 'Drag in the square for shade, or use the hue strip.'
            color: dialog.colors.mutedText
            font.pixelSize: 11
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }

    footer: RowLayout {
        spacing: 10
        Item { Layout.fillWidth: true }
        Button {
            id: cancelButton
            implicitWidth: 90; implicitHeight: 38
            Layout.bottomMargin: 20
            Accessible.name: 'Cancel colour selection'
            contentItem: Text {
                text: 'Cancel'; color: dialog.colors.text
                font.pixelSize: 12; font.weight: Font.Medium
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                radius: 9
                color: cancelButton.hovered ? dialog.colors.hover : dialog.colors.window
                border.color: cancelButton.activeFocus ? dialog.colors.accent : dialog.colors.border
            }
            onClicked: dialog.reject()
        }
        Button {
            id: selectButton
            implicitWidth: 90; implicitHeight: 38
            Layout.rightMargin: 20; Layout.bottomMargin: 20
            Accessible.name: 'Select colour'
            contentItem: Text {
                text: 'Select'; color: '#ffffff'
                font.pixelSize: 12; font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                radius: 9
                color: selectButton.down ? dialog.colors.selected : dialog.colors.accent
                border.color: selectButton.activeFocus ? dialog.colors.text : 'transparent'
            }
            onClicked: dialog.accept()
        }
    }
}
