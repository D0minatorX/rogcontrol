import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
ColumnLayout {
    id: root
    property var colors
    property var readings: systemController.data
    property var ppd: readings.ppd || ({})
    property var graphics: readings.graphics || ({})
    property bool graphicsStateKnown: ['active', 'inactive', 'failed'].indexOf(graphics.raw_active) >= 0
    property string section: 'General'
    spacing: 14
    StatusRow { Layout.fillWidth: true; colors: root.colors; label: 'Installed ROG Control version'; value: root.readings.version || 'Loading…' }
    Flow {
        Layout.fillWidth: true; spacing: 8
        Repeater {
            model: ['General', 'Services', 'Updates', 'Diagnostics']
            RogButton {
                required property string modelData
                colors: root.colors; text: modelData; primary: root.section === modelData
                onClicked: root.section = modelData
            }
        }
    }
    MetricCard {
        visible: root.section === 'General'
        title: 'Appearance and OS power'; colors: root.colors; Layout.fillWidth: true
        SettingsChoice { colors: root.colors; title: 'Color scheme'; choices: ['system','dark','light']; labels: ['System','Dark','Light']; value: appController.appearance; Layout.fillWidth: true; enabled: !appController.busy; onEdited: value => systemController.setAppearance(value) }
        StatusRow { colors: root.colors; label: 'Active profile'; value: appController.activeProfile }
        StatusRow { colors: root.colors; label: 'OS power mode'; value: root.readings.power_mode || 'Unavailable' }
        StatusRow { colors: root.colors; label: 'Profile agreement'; value: !root.readings.power_mode || !root.readings.expected_mode ? 'Not comparable' : root.readings.power_mode === root.readings.expected_mode ? 'In sync' : 'Profile expects ' + root.readings.expected_mode }
        Text { text: 'The background service synchronizes stock profiles with the OS power mode.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    }
    MetricCard {
        visible: root.section === 'Services'
        title: 'Graphics daemon'; colors: root.colors; Layout.fillWidth: true
        StatusRow { colors: root.colors; label: root.readings.backend || 'Backend'; value: root.readings.graphics ? root.readings.graphics.state || root.readings.graphics.error || 'Unavailable' : 'Checking…' }
        Text { text: root.readings.graphics ? root.readings.graphics.message || root.readings.graphics.error || '' : ''; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        Text { text: 'Manages GPU mode switching. Disable stops the service and automatic startup; GPU mode switching stays unavailable until you enable it again.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RowLayout {
            RogButton {
                objectName: 'graphicsDaemonButton'; colors: root.colors
                text: !root.readings.graphics ? 'Checking graphics daemon…' : !root.graphics.has_unit ? 'Graphics daemon unavailable' : !root.graphicsStateKnown ? 'Checking graphics daemon…' : root.graphics.active ? 'Disable graphics daemon' : 'Enable graphics daemon'
                enabled: !appController.busy && !!root.graphics.has_unit && root.graphicsStateKnown && ['cardwire', 'supergfxctl'].indexOf(root.readings.backend) >= 0
                onClicked: systemController.setGraphics(!root.graphics.active)
            }
        }
    }
    MetricCard {
        visible: root.section === 'Services'
        objectName: 'ppdServiceCard'
        title: 'Power Profiles Daemon'; colors: root.colors; Layout.fillWidth: true
        StatusRow { colors: root.colors; label: 'PPD'; value: root.ppd.tuned_ppd ? 'Provided by tuned-ppd' : root.ppd.installed === false ? 'Not installed' : root.ppd.masked ? 'Disabled (masked)' : root.ppd.state || 'Checking…' }
        Text {
            text: root.ppd.tuned_ppd ? 'tuned-ppd already provides OS power modes. These controls leave it unchanged.' : 'Optional OS power-mode synchronization. Disable stops PPD and blocks automatic D-Bus startup; Enable restores startup. Actions open a terminal for authentication and package prompts. After installing, use Enable if needed.'
            color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap
        }
        Flow {
            Layout.fillWidth: true; spacing: 8
            RogButton { colors: root.colors; text: 'Enable PPD'; enabled: !appController.busy && !!root.ppd.has_unit && !root.ppd.tuned_ppd; onClicked: systemController.managePpd('enable') }
            RogButton { colors: root.colors; text: 'Disable PPD'; enabled: !appController.busy && !!root.ppd.has_unit && !root.ppd.tuned_ppd; onClicked: systemController.managePpd('disable') }
            RogButton { colors: root.colors; text: 'Install PPD…'; enabled: !appController.busy && !!root.ppd.manager && !root.ppd.installed && !root.ppd.tuned_ppd; onClicked: systemController.managePpd('install') }
            RogButton { colors: root.colors; text: 'Uninstall PPD…'; enabled: !appController.busy && !!root.ppd.manager && !!root.ppd.installed && !root.ppd.tuned_ppd; onClicked: systemController.managePpd('uninstall') }
        }
        Text { visible: root.ppd.manager === 'rpm-ostree'; text: 'Package changes require a reboot. If PPD is part of the base image, use Disable instead of Uninstall.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    }
    MetricCard {
        visible: root.section === 'Services'
        title: 'asusd compatibility'; colors: root.colors; Layout.fillWidth: true
        StatusRow { colors: root.colors; label: 'asusd'; value: root.readings.asusd ? root.readings.asusd.state || root.readings.asusd.error || 'Unknown' : 'Checking…' }
        Text { text: 'Running asusd can conflict with ROG Control firmware and lighting settings.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        Flow {
            Layout.fillWidth: true; spacing: 8
            RogButton { colors: root.colors; text: 'Check'; enabled: !appController.busy; onClicked: systemController.refresh() }
            RogButton { colors: root.colors; text: 'Disable asusd'; enabled: !appController.busy && !!root.readings.asusd && !!root.readings.asusd.installed; onClicked: systemController.setAsusd(false) }
            RogButton { colors: root.colors; text: 'Enable asusd'; enabled: !appController.busy && !!root.readings.asusd && !!root.readings.asusd.installed; onClicked: systemController.setAsusd(true) }
            RogButton { colors: root.colors; text: 'Copy uninstall command'; enabled: !!root.readings.uninstall; onClicked: systemController.copy('uninstall') }
        }
        Text { text: root.readings.uninstall || ''; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    }
    MetricCard {
        visible: root.section === 'Updates'
        title: 'Updates'; colors: root.colors; Layout.fillWidth: true
        SettingsChoice { colors: root.colors; title: 'Check automatically'; choices: ['off','launch','daily','48h','weekly','monthly']; labels: ['Never','Every launch','Daily','Every 48 hours','Weekly','Monthly']; value: systemController.settings.update_check || 'off'; Layout.fillWidth: true; enabled: !appController.busy; onEdited: value => updatesController.setAutomatic(value) }
        Repeater {
            model: ['app', 'decky']
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
                    RogButton { colors: root.colors; text: 'Download'; visible: updateState.available && !updateState.stage.length; enabled: updateState.available && !updateState.busy; onClicked: updatesController.download(modelData) }
                    RogButton { colors: root.colors; text: 'Install…'; visible: updateState.stage.length > 0; enabled: updateState.stage.length > 0 && !updateState.busy && !appController.busy; onClicked: updatesController.install(modelData) }
                }
            }
        }
    }
    MetricCard {
        visible: root.section === 'Diagnostics'
        title: 'About and hardware report'; colors: root.colors; Layout.fillWidth: true
        StatusRow { colors: root.colors; label: 'ROG Control'; value: root.readings.version || '' }
        Text { text: 'Detected hardware: ' + (overviewController.data.cpu_name || 'CPU unavailable') + ' / ' + (overviewController.data.gpu_name || 'GPU unavailable'); color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RowLayout {
            RogButton { colors: root.colors; text: 'Save hardware report'; enabled: !appController.busy; onClicked: systemController.hardwareReport() }
        }
    }
    MetricCard {
        visible: root.section === 'Diagnostics'
        title: 'Diagnostic log'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { colors: root.colors; title: 'Errors only'; onEdited: value => systemController.setProblemsOnly(value) }
        Flow {
            Layout.fillWidth: true; spacing: 8
            RogButton { colors: root.colors; text: 'Refresh'; onClicked: systemController.refresh() }
            RogButton { colors: root.colors; text: 'Clear…'; enabled: !appController.busy; onClicked: systemController.request('clear-log') }
        }
        TextArea { text: root.readings.log === null ? 'Log unavailable' : root.readings.log || 'No entries'; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; color: root.colors.text; Layout.fillWidth: true; Layout.maximumHeight: 300; Accessible.name: 'Diagnostic log'; background: Rectangle { color: root.colors.window } }
    }
    Text { text: systemController.status; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
}
