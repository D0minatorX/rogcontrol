import QtQuick
import QtQuick.Layouts
ColumnLayout {
    id: root
    property var colors
    spacing: 14
    Text { visible: overviewController.sampleError.length > 0; text: overviewController.sampleError; color: root.colors.accent; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    GridLayout {
        columns: root.width < 640 ? 1 : 2
        columnSpacing: 14; rowSpacing: 14; Layout.fillWidth: true
        DeviceCard { device: 'cpu'; readings: overviewController.data; colors: root.colors; Layout.fillWidth: true; Layout.preferredWidth: 1 }
        DeviceCard { device: 'gpu'; readings: overviewController.data; colors: root.colors; Layout.fillWidth: true; Layout.preferredWidth: 1 }
    }
}
