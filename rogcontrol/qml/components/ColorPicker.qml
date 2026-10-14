import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: root
    property var colors
    property color value: '#ee2445'
    signal edited(color value)
    spacing: 8

    Button {
        id: swatch
        objectName: 'colorSwatch'
        implicitWidth: 46; implicitHeight: 38
        hoverEnabled: true
        Accessible.name: 'Choose colour'
        ToolTip.visible: hovered
        ToolTip.text: 'Open the colour palette'
        background: Rectangle {
            radius: 9
            color: root.colors.hover
            border.color: swatch.activeFocus || colourDialog.opened ? root.colors.accent : root.colors.border
            Rectangle {
                anchors.fill: parent; anchors.margins: 5
                radius: 5; color: root.value
                border.color: root.value.hslLightness > 0.85 ? root.colors.border : 'transparent'
            }
        }
        onClicked: {
            colourDialog.initialColor = root.value
            colourDialog.open()
        }
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

    CustomColorDialog {
        id: colourDialog
        objectName: 'customColorDialog'
        colors: root.colors
        onColorChosen: value => root.edited(value)
    }
}
