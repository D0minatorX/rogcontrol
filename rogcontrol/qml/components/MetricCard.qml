import QtQuick
import QtQuick.Layouts
Rectangle {
    id: root
    property var colors
    property bool stretchBody: false
    property string title
    property int contentSpacing: 10
    default property alias body: content.data
    radius: 10
    color: colors.panel
    border.width: 1
    border.color: colors.border
    implicitHeight: content.implicitHeight + 28
    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: root.stretchBody ? parent.bottom : undefined
        anchors.margins: 14
        spacing: root.contentSpacing
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Rectangle { implicitWidth: 22; implicitHeight: 3; radius: 2; color: root.colors.accent }
            Text { text: root.title; color: root.colors.text; font.pixelSize: 18; font.weight: Font.Medium; font.letterSpacing: .1; Layout.fillWidth: true; elide: Text.ElideRight }
        }
    }
}
