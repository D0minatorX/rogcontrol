import QtQuick
import QtQuick.Layouts
Item {
    id: root
    property var colors
    property bool stretchBody: false
    property string title
    property int contentSpacing: 10
    property bool angular: true
    default property alias body: content.data
    clip: true
    implicitHeight: content.implicitHeight + 34
    AngularPanel {
        anchors.fill: parent
        fillColor: root.colors.panel
        lineColor: root.colors.border
        accentColor: root.colors.accent
    }
    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: root.stretchBody ? parent.bottom : undefined
        anchors.margins: 17
        spacing: root.contentSpacing
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            SlashAccent { tint: root.colors.accent }
            Text { text: root.title; color: root.colors.text; font.pixelSize: 20; font.weight: Font.Bold; font.letterSpacing: .1; Layout.fillWidth: true; elide: Text.ElideRight }
        }
    }
}
