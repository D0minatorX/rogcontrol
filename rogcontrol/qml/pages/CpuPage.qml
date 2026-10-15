import QtQuick
import QtQuick.Layouts
import '../components'
ColumnLayout {
    id: root
    property var colors
    spacing: 14
    Text { visible: appController.safetyTripped; text: 'Curve Optimizer is held at stock after repeated boot failures. Review the undervolt before enabling it again.'; color: root.colors.accent; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    RogButton { visible: appController.safetyTripped; colors: root.colors; text: 'Recover undervolt control'; enabled: !appController.busy; onClicked: cpuController.recoverSafety() }
    GridLayout {
        Layout.fillWidth: true; columns: root.width >= 760 ? 2 : 1; columnSpacing: 14; rowSpacing: 14
        ColumnLayout {
            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 14
    MetricCard {
        Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
        colors: root.colors; title: 'Power limits'; visible: controls0.hasControls
        TuningControls { id: controls0; Layout.fillWidth: true; colors: root.colors; controller: cpuController; includedKeys: ['limits_enabled', 'stapm', 'fast', 'slow', 'pl1', 'pl2'] }
    }
    MetricCard {
        Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
        colors: root.colors; title: 'Temperature and voltage'; visible: controls2.hasControls
        TuningControls { id: controls2; Layout.fillWidth: true; colors: root.colors; controller: cpuController; includedKeys: ['temp', 'coall'] }
    }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 14
    MetricCard {
        Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
        colors: root.colors; title: 'Clocks'; visible: controls1.hasControls
        TuningControls { id: controls1; Layout.fillWidth: true; colors: root.colors; controller: cpuController; includedKeys: ['max_freq', 'min_freq'] }
    }
    MetricCard {
        Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
        colors: root.colors; title: 'Energy preference'; visible: efficiency.hasControls
        TuningControls { id: efficiency; Layout.fillWidth: true; colors: root.colors; controller: cpuController; includedKeys: ['epp'] }
    }
    MetricCard {
        Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
        colors: root.colors; title: 'Firmware power mode'; visible: firmwarePolicy.hasControls
        TuningControls { id: firmwarePolicy; Layout.fillWidth: true; colors: root.colors; controller: cpuController; includedKeys: ['thermal_policy'] }
    }
        }
    }
    Text { visible: cpuController.ready && !controls0.hasControls && !controls1.hasControls && !controls2.hasControls && !efficiency.hasControls && !firmwarePolicy.hasControls; text: 'No supported tuning controls detected.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    Text { Layout.fillWidth: true; text: cpuController.status; visible: text.length > 0; color: root.colors.mutedText; wrapMode: Text.WordWrap; font.pixelSize: 12 }
}
