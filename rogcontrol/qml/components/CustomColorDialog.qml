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
    property bool editorVisible: false
    property var customColors: ['#39ee00', '#25d500', '#ee19dc', '#ce16cc', '#69d8ce', '#8ce5dc']
    readonly property var paletteColors: [
        '#9bc8f4', '#8dea9f', '#fff170', '#ffbd78', '#f65d56', '#d586d8', '#c8a990', '#ffffff', '#78777c',
        '#639fea', '#55dc87', '#f8e85e', '#ffa344', '#ec393c', '#bc62c3', '#b48462', '#f1f1f1', '#5e5c63',
        '#3987df', '#2bca75', '#f6d932', '#ff8000', '#e2222d', '#9345aa', '#9b704e', '#dadad8', '#403b48',
        '#2472d1', '#21b885', '#f7c818', '#e65a00', '#b51b2b', '#79388c', '#7c573a', '#bdbdbb', '#25202e',
        '#1c5cad', '#229d6d', '#e2aa08', '#bd4300', '#971a2b', '#5d3373', '#624832', '#9f9f9e', '#000000'
    ]
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
    width: Math.min(690, parent.width - 24)
    modal: true
    focus: true
    padding: 20
    closePolicy: Popup.CloseOnEscape
    onAboutToShow: {
        setColor(initialColor)
        editorVisible = false
    }
    onAccepted: {
        if (editorVisible) {
            const hex = draftColor.toString()
            customColors = [hex].concat(customColors.filter(value => value !== hex)).slice(0, 6)
        }
        colorChosen(draftColor)
    }
    background: Rectangle {
        radius: 16
        color: dialog.colors.panel
        border.color: dialog.colors.border
    }
    Overlay.modal: Rectangle { color: '#99000000' }

    header: RowLayout {
        spacing: 12
        Text {
            text: dialog.editorVisible ? 'Custom colour' : 'Choose a colour'
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
        ColumnLayout {
            id: palettePage
            objectName: 'palettePage'
            visible: !dialog.editorVisible
            Layout.fillWidth: true
            spacing: 12
            Text {
                text: 'PRESETS'
                color: dialog.colors.mutedText
                font.pixelSize: 10; font.weight: Font.DemiBold
                font.letterSpacing: 1
            }
            GridLayout {
                Layout.fillWidth: true
                columns: 9
                columnSpacing: 5; rowSpacing: 5
                Repeater {
                    id: paletteRepeater
                    objectName: 'paletteRepeater'
                    model: dialog.paletteColors.length
                    delegate: Button {
                        id: paletteSwatch
                        required property int index
                        readonly property string swatchColor: dialog.paletteColors[index]
                        Layout.preferredWidth: Math.max(26, (dialog.width - 40 - 8 * 5) / 9)
                        Layout.preferredHeight: Math.max(31, Math.min(48, (dialog.width - 40) / 12))
                        hoverEnabled: true
                        Accessible.name: 'Palette colour ' + (index + 1)
                        contentItem: Text {
                            text: '✓'
                            visible: dialog.draftColor.toString().toLowerCase() === paletteSwatch.swatchColor
                            color: dialog.draftColor.hslLightness > 0.6 ? '#202024' : '#ffffff'
                            font.pixelSize: 20
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            radius: 7
                            color: paletteSwatch.swatchColor
                            border.width: paletteSwatch.activeFocus || paletteSwatch.hovered ? 2 : 1
                            border.color: paletteSwatch.activeFocus ? dialog.colors.accent : paletteSwatch.hovered ? dialog.colors.text : dialog.colors.border
                        }
                        onClicked: dialog.setColor(swatchColor)
                    }
                }
            }
            Text {
                text: 'CUSTOM'
                color: dialog.colors.mutedText
                font.pixelSize: 10; font.weight: Font.DemiBold
                font.letterSpacing: 1
                Layout.topMargin: 4
            }
            RowLayout {
                spacing: 5
                Button {
                    id: customEditorButton
                    objectName: 'customEditorButton'
                    Layout.preferredWidth: Math.min(58, (dialog.width - 40 - 6 * 5) / 7)
                    Layout.preferredHeight: 42
                    hoverEnabled: true
                    Accessible.name: 'Create custom colour'
                    contentItem: Text {
                        text: '+'; color: dialog.colors.text
                        font.pixelSize: 24
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 8
                        color: customEditorButton.hovered ? dialog.colors.hover : dialog.colors.window
                        border.color: customEditorButton.activeFocus ? dialog.colors.accent : dialog.colors.border
                    }
                    onClicked: dialog.editorVisible = true
                }
                Repeater {
                    model: dialog.customColors.length
                    delegate: Button {
                        required property int index
                        readonly property string swatchColor: dialog.customColors[index]
                        Layout.preferredWidth: Math.min(58, (dialog.width - 40 - 6 * 5) / 7)
                        Layout.preferredHeight: 42
                        hoverEnabled: true
                        Accessible.name: 'Custom colour ' + (index + 1)
                        background: Rectangle {
                            radius: 8; color: swatchColor
                            border.color: dialog.colors.border
                        }
                        onClicked: dialog.setColor(swatchColor)
                    }
                }
            }
        }
        ColumnLayout {
            id: colorEditor
            objectName: 'colorEditor'
            visible: dialog.editorVisible
            Layout.fillWidth: true
            spacing: 14
            Button {
                id: backButton
                implicitHeight: 28
                Accessible.name: 'Back to palette'
                contentItem: Text {
                    text: '‹  Back to palette'
                    color: dialog.colors.text
                    font.pixelSize: 12
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: 7
                    color: backButton.hovered ? dialog.colors.hover : 'transparent'
                }
                onClicked: dialog.editorVisible = false
            }
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
