import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
RowLayout {
    id: root
    property var colors
    property color value: '#ee2445'
    signal edited(color value)
    Rectangle { Layout.preferredWidth: 30; Layout.preferredHeight: 30; color: root.value; border.color: root.colors.border }
    TextField { id: field; text: root.value.toString(); color: root.colors.text; placeholderText: '#RRGGBB'; validator: RegularExpressionValidator { regularExpression: /#[0-9a-fA-F]{6}/ } Accessible.name: 'Hexadecimal colour'; onEditingFinished: { if (acceptableInput) root.edited(text) } }
}
