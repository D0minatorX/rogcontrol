import QtQuick
import QtQuick.Layouts
import "../components"
ColumnLayout {
    id: root
    property var colors
    spacing: 14
    OverviewPage { colors: root.colors; Layout.fillWidth: true }
    MetricCard {
        title: 'Startup and automatic profiles'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { colors: root.colors; title: 'Start on boot'; value: !!quickAccessController.settings.start_on_boot; enabled: !appController.busy; description: 'Enables the existing startup services. Preferences are saved after service changes succeed.'; onEdited: value => quickAccessController.setStartup(value) }
        AutomaticProfiles { colors: root.colors; Layout.fillWidth: true }
        RogButton { colors: root.colors; text: 'Recheck Gamescope and startup'; onClicked: quickAccessController.refresh() }
        Text { text: quickAccessController.data.gamescope ? 'Gamescope is installed' : 'Gamescope was not found; install it and recheck.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        Text { text: quickAccessController.status; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    }
    MetricCard {
        title: 'Quick controls'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { colors: root.colors; title: 'CPU boost'; value: !!cpuController.values.boost; enabled: !appController.busy && cpuController.ready && cpuController.rows.some(row => row.key === 'boost'); onEdited: value => quickAccessController.setCpuBoost(value) }
        SettingsChoice { colors: root.colors; title: 'GPU mode'; choices: gpuController.live.modes || []; value: gpuController.live.mode; enabled: !appController.busy; Layout.fillWidth: true; onEdited: value => gpuController.changeMode(value) }
        SettingsChoice { colors: root.colors; title: 'NVIDIA PowerMizer'; choices: quickAccessController.powerMizerChoices; labels: quickAccessController.powerMizerLabels; value: gpuController.values.powermizer_mode; enabled: !appController.busy && choices.length > 0; Layout.fillWidth: true; onEdited: value => quickAccessController.setPowerMizer(value) }
        SettingsSwitch { colors: root.colors; title: 'Boot sound'; visible: !!systemController.data.caps && !!systemController.data.caps.boot_sound; value: !!systemController.data.boot_sound; enabled: !appController.busy; onEdited: value => systemController.setFirmware('boot_sound', value) }
        SettingsSwitch { colors: root.colors; title: 'Panel overdrive'; visible: !!systemController.data.caps && !!systemController.data.caps.panel_od; value: !!systemController.data.panel_od; enabled: !appController.busy; onEdited: value => systemController.setFirmware('panel_od', value) }
        SliderRow { id: boostPercent; Layout.fillWidth: true; colors: root.colors; title: 'Temporary fan duty'; unit: '%'; minimum: 20; maximum: 100; value: 85; onEdited: value => boostPercent.value = value }
        SliderRow { id: boostDuration; Layout.fillWidth: true; colors: root.colors; title: 'Boost duration'; unit: 's'; minimum: 10; maximum: 600; step: 10; value: 60; onEdited: value => boostDuration.value = value }
        RowLayout {
            RogButton { colors: root.colors; text: 'Start fan boost'; enabled: !appController.busy && fansController.channels.length > 0; onClicked: fansController.boost(boostPercent.value, boostDuration.value) }
            RogButton { colors: root.colors; text: 'Stop boost'; enabled: !appController.busy && fansController.live.boost_seconds > 0; onClicked: fansController.stopBoost() }
        }
        Text { text: fansController.live.boost_seconds > 0 ? 'Boost active · ' + fansController.live.boost_seconds + ' seconds remaining' : 'Profile fan curves active'; color: root.colors.mutedText }
        Flow {
            spacing: 8; Layout.fillWidth: true; implicitHeight: childrenRect.height
            RogButton { colors: root.colors; text: 'CPU settings'; onClicked: appController.navigate('cpu') }
            RogButton { colors: root.colors; text: 'GPU settings'; onClicked: appController.navigate('gpu') }
            RogButton { colors: root.colors; text: 'Fan settings'; onClicked: appController.navigate('fans') }
        }
    }
}
