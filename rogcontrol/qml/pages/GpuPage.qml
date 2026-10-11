import QtQuick
import QtQuick.Layouts
import '../components'
ColumnLayout {
    id: root
    property var colors
    spacing: 14
    MetricCard {
        Layout.fillWidth: true; colors: root.colors; title: 'Power and tuning'
        TuningControls { Layout.fillWidth: true; colors: root.colors; controller: gpuController; excludedKeys: ['powermizer_mode'] }
        RogButton { colors: root.colors; text: 'Refresh tuning capabilities'; enabled: !appController.busy; onClicked: gpuController.probe() }
    }
    Text { Layout.fillWidth: true; text: gpuController.status; visible: text.length > 0; color: colors.mutedText; wrapMode: Text.WordWrap; font.pixelSize: 12 }
}
