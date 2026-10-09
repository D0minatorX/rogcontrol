import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import '../components'
ColumnLayout {
    id: root
    property var colors
    spacing: 14
    MetricCard {
        Layout.fillWidth: true; colors: root.colors; title: 'Graphics telemetry'
        Repeater { model: [{key:'temperature', label:'Temperature', unit:'°C'}, {key:'clock', label:'Core clock', unit:'MHz'}, {key:'power', label:'Board power', unit:'W'}, {key:'fan', label:'GPU fan', unit:'RPM'}]; Text { required property var modelData; text: modelData.label + '  ' + (gpuController.live[modelData.key] === null || gpuController.live[modelData.key] === undefined ? '— / unavailable' : gpuController.live[modelData.key] + (appController.demoMode || gpuController.live[modelData.key] === 'Idle' ? '' : ' ' + modelData.unit)); color: colors.text; font.pixelSize: 12 } }
        Text { text: gpuController.live.access || ''; visible: text.length > 0; color: colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    }
    MetricCard {
        Layout.fillWidth: true; colors: root.colors; title: 'Graphics mode / ' + (gpuController.live.backend || 'detecting')
        Text { text: 'Current: ' + (gpuController.live.mode || 'Unavailable'); color: colors.text }
        ComboBox { Layout.fillWidth: true; model: gpuController.live.modes || []; currentIndex: (gpuController.live.modes || []).indexOf(gpuController.live.mode); enabled: !appController.busy && !(gpuController.live.mode_gate || '').length; onActivated: gpuController.changeMode(currentText); Accessible.name: 'Graphics mode'; palette.button: colors.panel; palette.buttonText: colors.text }
        Text { text: gpuController.live.mode_gate || ''; color: colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true; visible: text.length > 0 }
        RogButton { colors: root.colors; text: 'Enable graphics daemon'; visible: (gpuController.live.mode_gate || '').indexOf('daemon') >= 0; enabled: !appController.busy; onClicked: gpuController.enableDaemon() }
        RogButton { colors: root.colors; text: 'Reboot to finish transition'; visible: gpuController.rebootPending; enabled: !appController.busy; onClicked: gpuController.confirm('reboot', true) }
    }
    MetricCard {
        Layout.fillWidth: true; colors: root.colors; title: 'Power and tuning'
        TuningControls { Layout.fillWidth: true; colors: root.colors; controller: gpuController }
        RogButton { colors: root.colors; text: 'Refresh tuning capabilities'; enabled: !appController.busy; onClicked: gpuController.probe() }
    }
    PendingChangesBar { Layout.fillWidth: true; colors: root.colors; pending: gpuController.dirty; busy: appController.busy; onApplyRequested: gpuController.apply(); onRevertRequested: gpuController.revert() }
    Text { Layout.fillWidth: true; text: gpuController.status; visible: text.length > 0; color: colors.mutedText; wrapMode: Text.WordWrap; font.pixelSize: 12 }
}
