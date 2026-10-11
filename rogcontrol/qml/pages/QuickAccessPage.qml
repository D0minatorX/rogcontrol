import QtQuick
import QtQuick.Layouts
import "../components"
ColumnLayout {
    id: root
    property var colors
    spacing: 16
    HomeReadings { colors: root.colors; Layout.fillWidth: true }
    GridLayout {
        columns: root.width < 720 ? 1 : 2
        Layout.fillWidth: true; columnSpacing: 14; rowSpacing: 14
        ColumnLayout {
            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 14
            MetricCard {
                title: 'Quick controls'; colors: root.colors
                Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
                SettingsSwitch { colors: root.colors; objectName: 'turboBoostSetting'; title: 'Turbo boost'; visible: cpuController.rows.some(row => row.key === 'boost'); value: !!cpuController.values.boost; enabled: !appController.busy && cpuController.ready && cpuController.rows.some(row => row.key === 'boost'); onEdited: value => quickAccessController.setCpuBoost(value) }
                SettingsChoice { colors: root.colors; objectName: 'gpuModeSetting'; title: 'GPU mode'; visible: !(gpuController.live.mode_gate || '').length && (gpuController.live.modes || []).length > 0; choices: gpuController.live.modes || []; value: gpuController.live.mode; enabled: !appController.busy && !(gpuController.live.mode_gate || '').length; Layout.fillWidth: true; onEdited: value => gpuController.changeMode(value) }
                Text { text: gpuController.live.mode_gate || ''; visible: text.length > 0; color: root.colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 11 }
                RogButton { colors: root.colors; text: 'Enable graphics daemon'; visible: (gpuController.live.mode_gate || '').indexOf('daemon') >= 0; enabled: !appController.busy; onClicked: gpuController.enableDaemon() }
                RogButton { colors: root.colors; text: 'Reboot to finish transition'; visible: gpuController.rebootPending; enabled: !appController.busy; onClicked: gpuController.confirm('reboot', true) }
                SettingsChoice { colors: root.colors; objectName: 'powerMizerSetting'; title: 'GPU PowerMizer'; visible: gpuController.rows.some(row => row.key === 'powermizer_mode'); description: gpuController.live.access || ''; choices: quickAccessController.powerMizerChoices; labels: quickAccessController.powerMizerLabels; value: gpuController.values.powermizer_mode; enabled: !appController.busy && choices.length > 0 && gpuController.rows.some(row => row.key === 'powermizer_mode' && row.enabled); Layout.fillWidth: true; onEdited: value => quickAccessController.setPowerMizer(value) }
                Text { text: gpuController.status; visible: text.length > 0; color: root.colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 11 }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; Layout.topMargin: 3; Layout.bottomMargin: 3; color: root.colors.border; opacity: .55 }
                SettingsSwitch { colors: root.colors; title: 'Boot sound'; visible: !!systemController.data.caps && !!systemController.data.caps.boot_sound; value: !!systemController.data.boot_sound; enabled: !appController.busy; onEdited: value => systemController.setFirmware('boot_sound', value) }
                SettingsSwitch { colors: root.colors; title: 'Panel overdrive'; visible: !!systemController.data.caps && !!systemController.data.caps.panel_od; value: !!systemController.data.panel_od; enabled: !appController.busy; onEdited: value => systemController.setFirmware('panel_od', value) }
                SettingsSwitch { colors: root.colors; objectName: 'psrSetting'; title: 'Disable AMD panel self-refresh'; visible: !!systemController.data.caps && !!systemController.data.caps.psr_toggle; value: !!systemController.data.psr_pending; enabled: !!systemController.data.caps && !!systemController.data.caps.psr_toggle && !systemController.data.psr_foreign && !appController.busy; description: 'Saved boot parameters take effect after reboot. Other dcdebugmask settings are preserved.'; onEdited: value => systemController.setPsrDisabled(value) }
                Text { visible: !!systemController.data.psr_foreign; text: "Another configuration owns " + systemController.data.psr_foreign + ". Changes are blocked."; color: root.colors.accent; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; Layout.topMargin: 3; Layout.bottomMargin: 3; color: root.colors.border; opacity: .55 }
                DisplayControls { colors: root.colors; Layout.fillWidth: true }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; Layout.topMargin: 3; Layout.bottomMargin: 3; color: root.colors.border; opacity: .55 }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 14
                    visible: fansController.channels.length > 0
                Text { text: 'TEMPORARY FAN BOOST'; color: root.colors.mutedText; font.pixelSize: 10; font.letterSpacing: 1 }
                NumberSetting { id: boostPercent; Layout.fillWidth: true; colors: root.colors; title: 'Fan speed'; unit: '%'; minimum: 20; maximum: 100; value: (quickAccessController.settings.fan_boost_preferences || {}).percent || 85; enabled: !appController.busy; onEdited: value => quickAccessController.setFanBoostPreference('percent', value) }
                NumberSetting { id: boostDuration; Layout.fillWidth: true; colors: root.colors; title: 'Duration'; unit: 's'; minimum: 10; maximum: 600; step: 10; value: (quickAccessController.settings.fan_boost_preferences || {}).seconds || 60; enabled: !appController.busy; onEdited: value => quickAccessController.setFanBoostPreference('seconds', value) }
                Flow {
                    Layout.fillWidth: true; spacing: 8
                    RogButton { colors: root.colors; text: 'Start boost'; primary: true; enabled: !appController.busy && fansController.channels.length > 0; onClicked: fansController.boost(boostPercent.value, boostDuration.value) }
                    RogButton { colors: root.colors; text: 'Stop'; enabled: !appController.busy && fansController.live.boost_seconds > 0; onClicked: fansController.stopBoost() }
                }
                Text { visible: fansController.live.boost_seconds > 0; text: fansController.live.boost_seconds + ' seconds remaining'; color: root.colors.accent; font.pixelSize: 11 }
                Text { visible: fansController.status.length > 0; text: fansController.status; color: root.colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 11 }
                }
            }
            MetricCard {
                title: 'Gamescope'; colors: root.colors; Layout.fillWidth: true
                Text { text: quickAccessController.data.gamescope ? 'Installed · ready for gaming sessions' : 'Gamescope is not installed'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
                SettingsChoice { colors: root.colors; objectName: 'gamescopeSetting'; title: 'Gamescope profile'; visible: !!quickAccessController.data.gamescope; choices: [''].concat(appController.profileNames); labels: ['No automatic switch'].concat(appController.profileNames); value: quickAccessController.settings.gamescope_profile || ''; Layout.fillWidth: true; enabled: !appController.busy && !!quickAccessController.data.gamescope; onEdited: value => quickAccessController.setAutomaticProfile('gamescope_profile', value) }
                RogButton { colors: root.colors; text: 'Recheck for Gamescope'; enabled: !appController.busy; onClicked: quickAccessController.refresh() }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 14
            BatteryControls { colors: root.colors; Layout.fillWidth: true }
            MetricCard {
                title: 'Startup'; colors: root.colors; Layout.fillWidth: true
                SettingsSwitch { colors: root.colors; title: 'Start on boot'; value: quickAccessController.settings.start_on_boot !== false; enabled: !appController.busy; onEdited: value => quickAccessController.setStartup(value) }
                AutomaticProfiles { colors: root.colors; Layout.fillWidth: true }
                Text { text: quickAccessController.status; visible: text.length > 0; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
            }

        }
    }
}
