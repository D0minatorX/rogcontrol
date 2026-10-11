import QtQuick
import QtQuick.Layouts
import '../components'
ColumnLayout {
    id: root
    property var colors
    spacing: 14
    MetricCard {
        Layout.fillWidth: true; colors: root.colors; title: 'Power and clocks'
        Text { visible: appController.safetyTripped; text: 'Curve Optimizer is held at stock after repeated boot failures. Review the undervolt before enabling it again.'; color: colors.accent; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        RogButton { visible: appController.safetyTripped; colors: root.colors; text: 'Recover undervolt control'; enabled: !appController.busy; onClicked: cpuController.recoverSafety() }
        Text { visible: cpuController.ready && cpuController.rows.length === 0; text: 'No supported CPU tuning interface was detected.'; color: colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        TuningControls { Layout.fillWidth: true; colors: root.colors; controller: cpuController }
    }
    PendingChangesBar { Layout.fillWidth: true; colors: root.colors; pending: cpuController.dirty; busy: appController.busy; onApplyRequested: cpuController.apply(); onRevertRequested: cpuController.revert() }
    Text { Layout.fillWidth: true; text: cpuController.status; visible: text.length > 0; color: colors.mutedText; wrapMode: Text.WordWrap; font.pixelSize: 12 }
}
