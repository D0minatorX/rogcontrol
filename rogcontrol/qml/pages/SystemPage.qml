import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
ColumnLayout {
    id: root
    property var colors
    property var readings: systemController.data
    spacing: 14
    MetricCard {
        title: 'Appearance and OS power'; colors: root.colors; Layout.fillWidth: true
        SettingsChoice { colors: root.colors; title: 'Appearance'; choices: ['system','dark','light']; labels: ['System','Dark','Light']; value: appController.appearance; Layout.fillWidth: true; enabled: !appController.busy; onEdited: value => systemController.setAppearance(value) }
        SettingsChoice { colors: root.colors; title: 'OS power mode'; choices: ['power-saver','balanced','performance']; value: root.readings.power_mode; Layout.fillWidth: true; enabled: !!root.readings.power_mode && !appController.busy; onEdited: value => systemController.setPowerMode(value) }
        StatusRow { colors: root.colors; label: 'Profile agreement'; value: !root.readings.power_mode || !root.readings.expected_mode ? 'Not comparable' : root.readings.power_mode === root.readings.expected_mode ? 'In sync' : 'Profile expects ' + root.readings.expected_mode }
        Text { text: 'The background service synchronizes stock profiles with the OS power mode.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    }
    MetricCard {
        title: 'Firmware'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { colors: root.colors; title: 'Boot sound'; visible: !!root.readings.caps && !!root.readings.caps.boot_sound; value: !!root.readings.boot_sound; enabled: !appController.busy; onEdited: value => systemController.setFirmware('boot_sound', value) }
        SettingsSwitch { colors: root.colors; title: 'Panel overdrive'; visible: !!root.readings.caps && !!root.readings.caps.panel_od; value: !!root.readings.panel_od; enabled: !appController.busy; onEdited: value => systemController.setFirmware('panel_od', value) }
        Text { visible: !!root.readings.caps && !root.readings.caps.boot_sound && !root.readings.caps.panel_od; text: 'No supported firmware switches detected.'; color: root.colors.mutedText }
    }
    MetricCard {
        title: 'Graphics daemon'; colors: root.colors; Layout.fillWidth: true
        StatusRow { colors: root.colors; label: root.readings.backend || 'Backend'; value: root.readings.graphics ? root.readings.graphics.state || root.readings.graphics.error || 'Unavailable' : 'Checking…' }
        Text { text: root.readings.graphics ? root.readings.graphics.message || root.readings.graphics.error || '' : ''; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RowLayout {
            RogButton { colors: root.colors; text: 'Enable graphics daemon'; enabled: !appController.busy && !!root.readings.caps && !!root.readings.caps[root.readings.backend === 'cardwire' ? 'cardwire' : 'supergfxctl']; onClicked: systemController.enableGraphics() }
            RogButton { colors: root.colors; text: 'Graphics modes'; onClicked: appController.navigate('gpu') }
        }
    }
    MetricCard {
        title: 'asusd compatibility'; colors: root.colors; Layout.fillWidth: true
        StatusRow { colors: root.colors; label: 'asusd'; value: root.readings.asusd ? root.readings.asusd.state || root.readings.asusd.error || 'Unknown' : 'Checking…' }
        Text { text: 'Running asusd can conflict with ROG Control firmware and lighting settings.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        Flow {
            Layout.fillWidth: true; spacing: 8
            RogButton { colors: root.colors; text: 'Disable asusd'; enabled: !appController.busy && !!root.readings.asusd && !!root.readings.asusd.installed; onClicked: systemController.setAsusd(false) }
            RogButton { colors: root.colors; text: 'Enable asusd'; enabled: !appController.busy && !!root.readings.asusd && !!root.readings.asusd.installed; onClicked: systemController.setAsusd(true) }
            RogButton { colors: root.colors; text: 'Copy uninstall command'; enabled: !!root.readings.uninstall; onClicked: systemController.copy('uninstall') }
        }
        Text { text: root.readings.uninstall || ''; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    }
    MetricCard {
        title: 'Kernel boot configuration'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { colors: root.colors; title: 'Disable AMD panel self-refresh'; value: !!root.readings.psr_pending; enabled: !!root.readings.caps && !!root.readings.caps.psr_toggle && !root.readings.psr_foreign && !appController.busy; description: 'Saved boot parameters take effect after reboot. Other dcdebugmask settings are preserved.'; onEdited: value => systemController.setPsrDisabled(value) }
        StatusRow { colors: root.colors; label: 'Running kernel PSR'; value: root.readings.psr_live === null || root.readings.psr_live === undefined ? 'Unknown' : root.readings.psr_live ? 'Disabled' : 'Enabled' }
        StatusRow { colors: root.colors; label: 'Next boot PSR'; value: root.readings.psr_pending === null || root.readings.psr_pending === undefined ? 'Unavailable' : root.readings.psr_pending ? 'Disabled' : 'Enabled' }
        Text { visible: !!root.readings.psr_foreign; text: 'Another configuration owns ' + (root.readings.psr_foreign || '') + '. Changes are blocked.'; color: root.colors.accent; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RogButton { colors: root.colors; text: 'Reboot…'; enabled: !appController.busy; onClicked: systemController.request('reboot') }
    }
    MetricCard {
        title: 'App and Decky updates'; colors: root.colors; Layout.fillWidth: true
        SettingsChoice { colors: root.colors; title: 'Automatic app update checks'; choices: ['off','launch','daily']; labels: ['Off','On launch','Daily']; value: systemController.settings.update_check || 'off'; Layout.fillWidth: true; enabled: !appController.busy; onEdited: value => updatesController.setAutomatic(value) }
        Repeater {
            model: ['app','decky']
            ColumnLayout {
                required property string modelData
                property var updateState: updatesController.data[modelData]
                Layout.fillWidth: true
                Text { text: modelData === 'app' ? 'ROG CONTROL' : 'DECKY PLUGIN'; color: root.colors.text }
                Text { text: updateState.status; color: root.colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                ProgressBar { visible: updateState.busy; Layout.fillWidth: true; from: 0; to: 100; value: updateState.progress; indeterminate: updateState.progress === 0 }
                Flow {
                    Layout.fillWidth: true; spacing: 8
                    RogButton { colors: root.colors; text: 'Check / retry'; enabled: !updateState.busy; onClicked: updatesController.check(modelData) }
                    RogButton { colors: root.colors; text: 'Download'; enabled: updateState.available && !updateState.busy; onClicked: updatesController.download(modelData) }
                    RogButton { colors: root.colors; text: 'Install…'; enabled: updateState.stage.length > 0 && !updateState.busy && !appController.busy; onClicked: updatesController.install(modelData) }
                }
            }
        }
    }
    MetricCard {
        title: 'About and hardware report'; colors: root.colors; Layout.fillWidth: true
        StatusRow { colors: root.colors; label: 'ROG Control'; value: root.readings.version || '' }
        RowLayout {
            RogButton { colors: root.colors; text: 'Generate report'; enabled: !appController.busy; onClicked: systemController.hardwareReport() }
            RogButton { colors: root.colors; text: 'Copy report'; enabled: !!root.readings.report; onClicked: systemController.copy('report') }
        }
        TextArea { visible: !!root.readings.report; text: root.readings.report || ''; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; color: root.colors.text; Layout.fillWidth: true; Layout.maximumHeight: 260; Accessible.name: 'Hardware report'; background: Rectangle { color: root.colors.window } }
    }
    MetricCard {
        title: 'Diagnostic log'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { colors: root.colors; title: 'Problems only'; onEdited: value => systemController.setProblemsOnly(value) }
        Flow {
            Layout.fillWidth: true; spacing: 8
            RogButton { colors: root.colors; text: 'Refresh'; onClicked: systemController.refresh() }
            RogButton { colors: root.colors; text: 'Copy'; enabled: !!root.readings.log; onClicked: systemController.copy('log') }
            RogButton { colors: root.colors; text: 'Clear…'; enabled: !appController.busy; onClicked: systemController.request('clear-log') }
        }
        TextArea { text: root.readings.log === null ? 'Log unavailable' : root.readings.log || 'No entries'; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; color: root.colors.text; Layout.fillWidth: true; Layout.maximumHeight: 300; Accessible.name: 'Diagnostic log'; background: Rectangle { color: root.colors.window } }
    }
    Text { text: systemController.status; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    RogButton { colors: root.colors; text: 'Refresh system status'; onClicked: systemController.refresh() }
}
