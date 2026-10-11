import QtQuick
import QtQuick.Layouts
import "../components"
ColumnLayout {
    id: root
    property var colors
    spacing: 16
    OverviewPage { colors: root.colors; Layout.fillWidth: true }
    GridLayout {
        columns: root.width < 720 ? 1 : 2
        Layout.fillWidth: true; columnSpacing: 14; rowSpacing: 14
        MetricCard {
            title: 'Quick controls'; colors: root.colors
            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
            SettingsSwitch { colors: root.colors; title: 'CPU boost'; value: !!cpuController.values.boost; enabled: !appController.busy && cpuController.ready && cpuController.rows.some(row => row.key === 'boost'); onEdited: value => quickAccessController.setCpuBoost(value) }
            SettingsChoice { colors: root.colors; title: 'GPU mode'; choices: gpuController.live.modes || []; value: gpuController.live.mode; enabled: !appController.busy && !(gpuController.live.mode_gate || '').length; Layout.fillWidth: true; onEdited: value => gpuController.changeMode(value) }
            Text { text: gpuController.live.mode_gate || ''; visible: text.length > 0; color: root.colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 11 }
            RogButton { colors: root.colors; text: 'Enable graphics daemon'; visible: (gpuController.live.mode_gate || '').indexOf('daemon') >= 0; enabled: !appController.busy; onClicked: gpuController.enableDaemon() }
            RogButton { colors: root.colors; text: 'Reboot to finish transition'; visible: gpuController.rebootPending; enabled: !appController.busy; onClicked: gpuController.confirm('reboot', true) }
            SettingsChoice { colors: root.colors; title: 'NVIDIA PowerMizer'; description: gpuController.live.access || ''; choices: quickAccessController.powerMizerChoices; labels: quickAccessController.powerMizerLabels; value: gpuController.values.powermizer_mode; enabled: !appController.busy && choices.length > 0; Layout.fillWidth: true; onEdited: value => quickAccessController.setPowerMizer(value) }
            Text { text: gpuController.status; visible: text.length > 0; color: root.colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 11 }
            SettingsSwitch { colors: root.colors; title: 'Boot sound'; visible: !!systemController.data.caps && !!systemController.data.caps.boot_sound; value: !!systemController.data.boot_sound; enabled: !appController.busy; onEdited: value => systemController.setFirmware('boot_sound', value) }
            SettingsSwitch { colors: root.colors; title: 'Panel overdrive'; visible: !!systemController.data.caps && !!systemController.data.caps.panel_od; value: !!systemController.data.panel_od; enabled: !appController.busy; onEdited: value => systemController.setFirmware('panel_od', value) }
            Rectangle { Layout.fillWidth: true; height: 1; color: root.colors.border }
            Text { text: 'TEMPORARY FAN BOOST'; color: root.colors.mutedText; font.pixelSize: 10; font.letterSpacing: 1 }
            NumberSetting { id: boostPercent; Layout.fillWidth: true; colors: root.colors; title: 'Fan speed'; unit: '%'; minimum: 20; maximum: 100; value: 85; enabled: !appController.busy }
            NumberSetting { id: boostDuration; Layout.fillWidth: true; colors: root.colors; title: 'Duration'; unit: 's'; minimum: 10; maximum: 600; step: 10; value: 60; enabled: !appController.busy }
            Flow {
                Layout.fillWidth: true; spacing: 8
                RogButton { colors: root.colors; text: 'Start boost'; primary: true; enabled: !appController.busy && fansController.channels.length > 0; onClicked: fansController.boost(boostPercent.value, boostDuration.value) }
                RogButton { colors: root.colors; text: 'Stop'; visible: fansController.live.boost_seconds > 0; enabled: !appController.busy; onClicked: fansController.stopBoost() }
            }
            Text { visible: fansController.live.boost_seconds > 0; text: fansController.live.boost_seconds + ' seconds remaining'; color: root.colors.accent; font.pixelSize: 11 }
            Text { visible: fansController.status.length > 0; text: fansController.status; color: root.colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 11 }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 14
            MetricCard {
                title: 'Startup'; colors: root.colors; Layout.fillWidth: true
                SettingsSwitch { colors: root.colors; title: 'Start on boot'; value: !!quickAccessController.settings.start_on_boot; enabled: !appController.busy; onEdited: value => quickAccessController.setStartup(value) }
                AutomaticProfiles { colors: root.colors; Layout.fillWidth: true }
                Text { text: quickAccessController.status; visible: text.length > 0; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
            }
            MetricCard {
                title: 'Gamescope'; colors: root.colors; Layout.fillWidth: true
                Text { text: quickAccessController.data.gamescope ? 'Installed · ready for gaming sessions' : 'Gamescope is not installed'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
                SettingsChoice { colors: root.colors; title: 'Session profile'; choices: [''].concat(appController.profileNames); labels: ['No automatic switch'].concat(appController.profileNames); value: quickAccessController.settings.gamescope_profile || ''; Layout.fillWidth: true; enabled: !appController.busy && !!quickAccessController.data.gamescope; onEdited: value => quickAccessController.setAutomaticProfile('gamescope_profile', value) }
                RogButton { colors: root.colors; text: 'Recheck Gamescope'; enabled: !appController.busy; onClicked: quickAccessController.refresh() }
            }
        }
    }
}
