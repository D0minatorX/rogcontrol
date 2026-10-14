import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: root
    property var colors
    property color value: '#ee2445'
    signal edited(color value)
    spacing: 8
    function choosePreset(color) {
        palette.close()
        edited(color)
    }

    readonly property var presets: [
        { name: 'Red', value: '#ef1745' },
        { name: 'Orange', value: '#ff8a32' },
        { name: 'Yellow', value: '#ffd84d' },
        { name: 'Green', value: '#42c878' },
        { name: 'Teal', value: '#23c7bd' },
        { name: 'Blue', value: '#3478f6' },
        { name: 'Purple', value: '#a271ef' },
        { name: 'Pink', value: '#ef72b5' },
        { name: 'White', value: '#ffffff' },
        { name: 'Warm white', value: '#ffe5bc' },
        { name: 'Grey', value: '#9298a2' },
        { name: 'Black', value: '#000000' },
        { name: 'Light red', value: '#ffb8c4' },
        { name: 'Light orange', value: '#ffd2a6' },
        { name: 'Light yellow', value: '#fff2a8' },
        { name: 'Light green', value: '#b6edbd' },
        { name: 'Light teal', value: '#a7e9e5' },
        { name: 'Light blue', value: '#bcd5ff' },
        { name: 'Deep red', value: '#a32940' },
        { name: 'Deep orange', value: '#a94d18' },
        { name: 'Deep yellow', value: '#a87a10' },
        { name: 'Deep green', value: '#247b48' },
        { name: 'Deep teal', value: '#176d76' },
        { name: 'Deep blue', value: '#21439b' },
        { name: 'Light purple', value: '#d6c0ff' },
        { name: 'Light pink', value: '#ffc1df' },
        { name: 'Ice blue', value: '#d7f1ff' },
        { name: 'Mint', value: '#baffdf' },
        { name: 'Cream', value: '#fff4d3' },
        { name: 'Lavender', value: '#b8a6e9' },
        { name: 'Deep purple', value: '#5d379f' },
        { name: 'Deep pink', value: '#a73270' },
        { name: 'Indigo', value: '#334ba8' },
        { name: 'Cyan', value: '#19a6de' },
        { name: 'Lime', value: '#91c927' },
        { name: 'Amber', value: '#df9e22' }
    ]

    Button {
        id: swatch
        objectName: 'colorSwatch'
        implicitWidth: 46; implicitHeight: 38
        hoverEnabled: true
        Accessible.name: 'Choose colour'
        ToolTip.visible: hovered
        ToolTip.text: 'Choose a preset or custom colour'
        background: Rectangle {
            radius: 9
            color: root.colors.hover
            border.color: swatch.activeFocus || palette.opened ? root.colors.accent : root.colors.border
            Rectangle {
                anchors.fill: parent; anchors.margins: 5
                radius: 5; color: root.value
                border.color: root.value.hslLightness > 0.85 ? root.colors.border : 'transparent'
            }
        }
        onClicked: palette.open()
    }

    TextField {
        id: field
        Layout.preferredWidth: 106
        implicitHeight: 38
        text: root.value.toString().toUpperCase()
        color: root.colors.text
        font.pixelSize: 12
        font.family: 'monospace'
        selectByMouse: true
        placeholderText: '#RRGGBB'
        validator: RegularExpressionValidator { regularExpression: /#[0-9a-fA-F]{6}/ }
        Accessible.name: 'Hexadecimal colour'
        background: Rectangle {
            radius: 9
            color: root.colors.window
            border.color: field.activeFocus ? root.colors.accent : root.colors.border
        }
        onEditingFinished: {
            if (acceptableInput)
                root.edited(text)
            else
                text = root.value.toString().toUpperCase()
        }
    }

    Popup {
        id: palette
        objectName: 'colorPalette'
        x: 0; y: swatch.height + 6
        width: 276
        padding: 16
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle {
            radius: 14; color: root.colors.panel
            border.color: root.colors.border
        }
        contentItem: ColumnLayout {
            spacing: 12
            Text { text: 'Choose a colour'; color: root.colors.text; font.pixelSize: 14; font.weight: Font.DemiBold }
            Text { text: 'PRESETS'; color: root.colors.mutedText; font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1 }
            GridLayout {
                columns: 6; rowSpacing: 10; columnSpacing: 10
                Repeater {
                    model: root.presets.length
                    delegate: Button {
                        required property int index
                        readonly property var preset: root.presets[index]
                        objectName: 'preset' + preset.name.replace(' ', '')
                        implicitWidth: 32; implicitHeight: 32
                        hoverEnabled: true
                        Accessible.name: preset.name
                        ToolTip.visible: hovered
                        ToolTip.text: preset.name
                        background: Rectangle {
                            radius: 9
                            color: preset.value
                            border.width: root.value.toString().toLowerCase() === preset.value ? 3 : 1
                            border.color: root.value.toString().toLowerCase() === preset.value ? root.colors.accent : root.colors.border
                        }
                        onClicked: root.choosePreset(preset.value)
                    }
                }
            }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: root.colors.border }
            Button {
                id: customButton
                objectName: 'customColorButton'
                Layout.fillWidth: true
                implicitHeight: 38
                hoverEnabled: true
                Accessible.name: 'Custom colour'
                contentItem: Text {
                    text: 'Custom colour…'
                    color: root.colors.text
                    font.pixelSize: 12
                    font.weight: Font.Medium
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: 9
                    color: customButton.hovered ? root.colors.hover : root.colors.window
                    border.color: customButton.activeFocus ? root.colors.accent : root.colors.border
                }
                onClicked: {
                    palette.close()
                    customDialog.initialColor = root.value
                    customDialog.open()
                }
            }
        }
    }

    CustomColorDialog {
        id: customDialog
        objectName: 'customColorDialog'
        colors: root.colors
        onColorChosen: value => root.edited(value)
    }
}
