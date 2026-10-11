import QtQuick
import QtQuick.Layouts
Rectangle {
    id: root
    property var colors
    property string title
    default property alias body: content.data
    radius: 14
    color: colors.panel
    border.width: 0
    implicitHeight: content.implicitHeight + 36
    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 18
        spacing: 18
        RowLayout {
            Layout.fillWidth: true; spacing: 9
            Rectangle { implicitWidth: 14; implicitHeight: 2; radius: 1; color: root.colors.accent }
            Text { text: root.title; color: root.colors.text; font.pixelSize: 14; font.weight: Font.DemiBold; font.letterSpacing: .1; Layout.fillWidth: true; elide: Text.ElideRight }
        }
    }
}
