import QtQuick
import QtQuick.Layouts
import '../components'
ColumnLayout {
    id: root
    property var colors
    spacing: 14
    GridLayout {
        Layout.fillWidth: true; columns: root.width >= 760 ? 2 : 1; columnSpacing: 14; rowSpacing: 14
        ColumnLayout {
            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 14
    MetricCard {
        Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
        colors: root.colors; title: 'Power and temperature'; visible: controls0.hasControls
        TuningControls { id: controls0; Layout.fillWidth: true; colors: root.colors; controller: gpuController; includedKeys: ['watts', 'dyn_boost', 'temp_target'] }
    }
    MetricCard {
        Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
        colors: root.colors; title: 'Voltage'; visible: controls2.hasControls
        TuningControls { id: controls2; Layout.fillWidth: true; colors: root.colors; controller: gpuController; includedKeys: ['voltage_boost'] }
    }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 14
    MetricCard {
        Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
        colors: root.colors; title: 'Clock tuning'; visible: controls1.hasControls
        TuningControls { id: controls1; Layout.fillWidth: true; colors: root.colors; controller: gpuController; includedKeys: ['clock_limit', 'clock_offset', 'mem_clock_offset'] }
    }
        }
    }
    Text { visible: gpuController.ready && !controls0.hasControls && !controls1.hasControls && !controls2.hasControls; text: 'No supported tuning controls detected.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    MetricCard {
        objectName: 'nvidiaDisplayPowerCard'
        Layout.fillWidth: true; colors: root.colors; title: 'NVIDIA display power saving'
        visible: !!gpuController.displayPower.available
        SettingsSwitch {
            objectName: 'nvidiaDisplayPowerSwitch'
            colors: root.colors; title: 'Allow lower display memory clocks'
            value: !!gpuController.displayPower.enabled
            deferChanges: true
            enabled: !!gpuController.displayPower.supported && !appController.busy
            description: 'May lower idle power with multiple monitors, but can cause brief display glitches. Applies system-wide after rebuilding boot images and rebooting.'
            helpText: 'Sets NVreg_RegistryDwords="RmDisableDisplayGlitchPerfLimit=1" when on, or 0 when off. Other NVIDIA options are preserved. Requires NVIDIA 615.71.09 or newer.'
            onEdited: value => gpuController.setDisplayPower(value)
        }
        StatusRow { colors: root.colors; label: 'Running driver'; value: gpuController.displayPower.live === true ? 'On' : gpuController.displayPower.live === false ? 'Off' : 'Unknown' }
        Text { text: gpuController.displayPower.message || ''; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    }
    Text { Layout.fillWidth: true; text: gpuController.status; visible: text.length > 0; color: root.colors.mutedText; wrapMode: Text.WordWrap; font.pixelSize: 12 }
}
