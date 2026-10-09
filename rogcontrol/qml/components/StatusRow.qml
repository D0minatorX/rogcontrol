import QtQuick
import QtQuick.Layouts
RowLayout {
    property string label
    property string value
    property var colors
    Layout.fillWidth: true
    implicitHeight: 34
    Text { text: label; color: colors.mutedText; font.pixelSize: 11; Layout.fillWidth: true }
    Text { text: value; color: colors.text; font.pixelSize: 14 }
}
