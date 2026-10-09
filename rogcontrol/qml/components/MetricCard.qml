import QtQuick
import QtQuick.Layouts
Rectangle {
    id: root
    property var colors
    property string title
    default property alias body: content.data
    color: colors.panel
    border.color: colors.border
    implicitHeight: content.implicitHeight + 36
    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: 17
        spacing: 13
        Text { text: root.title.toUpperCase(); color: colors.text; font.pixelSize: 12; font.letterSpacing: 1.3; Layout.fillWidth: true }
    }
}
