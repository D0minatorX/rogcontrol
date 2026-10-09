import QtQuick
import QtQuick.Layouts
import "../components"
ColumnLayout {
    id: root
    property var colors
    property var readings: batteryController.data
    spacing: 14
    MetricCard {
        title: 'Battery state'; colors: root.colors; Layout.fillWidth: true
        StatusRow { colors: root.colors; label: 'Charge'; value: root.readings.percent === undefined || root.readings.percent === null ? 'Unavailable' : root.readings.percent + '%' }
        StatusRow { colors: root.colors; label: 'State'; value: root.readings.charging ? 'Charging' : 'Not charging' }
        StatusRow { colors: root.colors; label: 'Power source'; value: root.readings.source || 'Unknown' }
        ColumnLayout {
            visible: !!root.readings.health; Layout.fillWidth: true
            StatusRow { colors: root.colors; label: 'Health'; value: root.readings.health ? Number(root.readings.health.percent).toFixed(1) + '%' : '' }
            StatusRow { colors: root.colors; label: 'Full / design capacity'; value: root.readings.health ? Number(root.readings.health.full).toFixed(1) + ' / ' + Number(root.readings.health.design).toFixed(1) + ' ' + root.readings.health.unit : '' }
            StatusRow { visible: !!root.readings.health && !!root.readings.health.cycles; colors: root.colors; label: 'Cycles'; value: root.readings.health ? String(root.readings.health.cycles || '') : '' }
            Text { visible: !!root.readings.health && root.readings.health.percent > 100; text: 'Firmware may report a new battery above 100% design capacity.'; color: root.colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        }
    }
    MetricCard {
        title: 'Charge protection'; colors: root.colors; Layout.fillWidth: true
        SliderRow { Layout.fillWidth: true; colors: root.colors; title: 'Normal charge cap'; unit: '%'; value: batteryController.settings.charge_limit === undefined ? 100 : batteryController.settings.charge_limit; enabled: !!root.readings.charge_supported && !appController.busy; onEdited: value => batteryController.setLimit(Math.round(value)) }
        StatusRow { colors: root.colors; label: 'Firmware charge cap'; value: root.readings.limit === undefined || root.readings.limit === null ? 'Unsupported' : root.readings.limit + '%' }
        RogButton { colors: root.colors; text: root.readings.once ? 'Cancel one full charge' : 'Charge to 100% once'; enabled: !!root.readings.charge_supported && !appController.busy; onClicked: batteryController.toggleOnce() }
        Text { text: root.readings.once ? 'One full charge is active. The normal cap is restored on full charge or unplugging.' : 'The normal charge cap is global across profiles.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    }
    MetricCard { title: 'Automatic profile switching'; colors: root.colors; Layout.fillWidth: true; AutomaticProfiles { colors: root.colors; Layout.fillWidth: true } }
    MetricCard {
        title: 'Internal display'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { colors: root.colors; title: 'Reduce refresh rate on battery'; value: !!batteryController.settings.auto_display_refresh; enabled: !!root.readings.display_supported && !appController.busy; description: 'Restores the previous AC refresh rate when power returns.'; onEdited: value => batteryController.setDisplay('auto_display_refresh', value) }
        SettingsChoice { colors: root.colors; title: 'Battery refresh rate'; choices: root.readings.rates || []; labels: choices.map(rate => rate + ' Hz'); value: batteryController.settings.battery_display_refresh_hz || 60; enabled: choices.length > 0 && !appController.busy; onEdited: value => batteryController.setDisplay('battery_display_refresh_hz', value); Layout.fillWidth: true }
        SettingsChoice { colors: root.colors; title: 'Current refresh rate'; choices: root.readings.rates || []; labels: choices.map(rate => rate + ' Hz'); value: root.readings.current_rate; enabled: choices.length > 0 && !appController.busy; onEdited: value => batteryController.setRefreshRate(value); Layout.fillWidth: true }
        Text { text: root.readings.display_reason || ''; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RogButton { colors: root.colors; text: 'Recheck display'; onClicked: batteryController.refresh() }
    }
    Text { text: batteryController.status; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
}
