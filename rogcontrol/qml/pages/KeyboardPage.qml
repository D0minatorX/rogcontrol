import QtQuick
import QtQuick.Layouts
import "../components"
ColumnLayout {
    id: root
    property var colors
    property var readings: keyboardController.data
    property var lighting: keyboardController.lighting
    property var config: keyboardController.settings
    property var power: keyboardController.powerPreferences
    property var bindings: keyboardController.inputPreferences
    property var fnlock: keyboardController.fnPreferences
    spacing: 14
    MetricCard {
        title: 'Keyboard lighting'; colors: root.colors; Layout.fillWidth: true
        SliderRow { colors: root.colors; Layout.fillWidth: true; title: 'Brightness'; minimum: 0; maximum: 3; value: root.lighting.brightness; onEdited: value => keyboardController.setLighting('brightness', value) }
        SettingsChoice { colors: root.colors; Layout.fillWidth: true; title: 'Effect'; choices: root.readings.modes || []; value: root.lighting.mode; onEdited: value => keyboardController.setLighting('mode', value) }
        ColumnLayout {
            visible: ['Static','Breathing','Pulse','Gradient Static'].indexOf(root.lighting.mode) >= 0
            Text { text: 'Primary colour'; color: root.colors.text }
            ColorPicker { colors: root.colors; value: root.lighting.color1; onEdited: value => keyboardController.setLighting('color1', value.toString()) }
        }
        ColumnLayout {
            visible: ['Breathing','Gradient Static'].indexOf(root.lighting.mode) >= 0
            Text { text: 'Secondary colour'; color: root.colors.text }
            ColorPicker { colors: root.colors; value: root.lighting.color2; onEdited: value => keyboardController.setLighting('color2', value.toString()) }
        }
        SliderRow { visible: ['Breathing','Pulse','Color Cycle','Rainbow'].indexOf(root.lighting.mode) >= 0; colors: root.colors; Layout.fillWidth: true; title: 'Animation speed'; minimum: 1; maximum: 3; value: root.lighting.speed; onEdited: value => keyboardController.setLighting('speed', value) }
        Text { text: (root.readings.hint || '') + (root.readings.dimmed ? '\nTemporarily dimmed by idle policy; saved brightness is preserved.' : '') + (root.lighting.mode === 'Ambient' ? '\nAmbient requests permission to capture your screen. The demo uses no capture.' : ''); color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RogButton { colors: root.colors; text: 'Recheck keyboard support'; onClicked: keyboardController.retry() }
    }
    MetricCard {
        title: 'Profile colours'; colors: root.colors; Layout.fillWidth: true
        visible: root.lighting.mode === 'Profile Color'
        Repeater {
            model: appController.profileNames
            ColumnLayout {
                required property string modelData
                Text { text: modelData; color: root.colors.text }
                ColorPicker { colors: root.colors; value: keyboardController.profileColors[modelData] || '#ee2445'; onEdited: value => keyboardController.setProfileColor(modelData, value.toString()) }
            }
        }
    }
    MetricCard {
        title: 'Idle dimming'; colors: root.colors; Layout.fillWidth: true
        Text { text: root.readings.idle || 'Checking desktop idle detection…'; color: root.colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        SliderRow { colors: root.colors; Layout.fillWidth: true; title: 'AC timeout'; unit: 's'; minimum: 0; maximum: 3600; step: 10; value: root.config.kbd_idle_timeout_ac_seconds || 0; enabled: !appController.busy; onEdited: value => keyboardController.setPreference('kbd_idle_timeout_ac_seconds', value) }
        SliderRow { colors: root.colors; Layout.fillWidth: true; title: 'Battery timeout'; unit: 's'; minimum: 0; maximum: 3600; step: 10; value: root.config.kbd_idle_timeout_battery_seconds || 0; enabled: !appController.busy; onEdited: value => keyboardController.setPreference('kbd_idle_timeout_battery_seconds', value) }
        RogButton { colors: root.colors; text: 'Disable both timeouts'; enabled: !appController.busy; onClicked: keyboardController.disableIdle() }
    }
    MetricCard {
        title: 'Lighting power policy'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { colors: root.colors; title: 'Manage keyboard and lightbar power'; value: !!root.power.enabled; enabled: (!!root.readings.power_supported || !!root.power.enabled) && !appController.busy; onEdited: value => keyboardController.setPower('', 'enabled', value) }
        Text { visible: !root.readings.power_supported; text: 'Lighting power zones require a supported ASUS interface. Recheck after reconnecting.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        Repeater {
            model: ['keyboard', 'lightbar']
            ColumnLayout {
                id: zoneRow
                required property string modelData
                Layout.fillWidth: true
                Text { text: zoneRow.modelData.toUpperCase(); color: root.colors.text }
                Repeater {
                    model: ['boot', 'awake', 'sleep', 'shutdown', 'battery']
                    SettingsSwitch {
                        required property string modelData
                        colors: root.colors; title: modelData
                        value: root.power[zoneRow.modelData] ? !!root.power[zoneRow.modelData][modelData] : false
                        enabled: !!root.readings.power_supported && !appController.busy
                        onEdited: value => keyboardController.setPower(zoneRow.modelData, modelData, value)
                    }
                }
            }
        }
    }
    MetricCard {
        title: 'Charger flash'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { colors: root.colors; title: 'Flash on plug and unplug'; value: !!root.config.charger_flash; enabled: !appController.busy; description: 'The background service restores your lighting. Ambient cannot be restored, so flashing is skipped in Ambient mode.'; onEdited: value => keyboardController.setPreference('charger_flash', value) }
        ColorPicker { colors: root.colors; value: keyboardController.flashColor; enabled: !appController.busy; onEdited: value => keyboardController.setPreference('charger_flash_color', value.toString()) }
    }
    MetricCard {
        title: 'Hardware hotkeys'; colors: root.colors; Layout.fillWidth: true
        Text { text: (root.readings.input_status || 'Checking input access…') + '\n' + (root.readings.runtime || ''); color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        SettingsSwitch { colors: root.colors; title: 'Enable ROG Control hotkey actions'; value: !!root.bindings.enabled; enabled: (!!root.readings.bindings_supported || !!root.bindings.enabled) && !appController.busy; onEdited: value => keyboardController.setBinding('key_bindings', 'enabled', value) }
        Repeater {
            model: root.readings.bindings || []
            SettingsChoice {
                required property var modelData
                colors: root.colors; Layout.fillWidth: true; title: modelData.label; choices: root.readings.actions || []; labels: root.readings.action_labels || []; value: root.bindings[modelData.key]
                enabled: !!root.readings.bindings_supported && !appController.busy
                onEdited: value => keyboardController.setBinding('key_bindings', modelData.key, value)
            }
        }
    }
    MetricCard {
        title: 'Fn Lock'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { colors: root.colors; title: 'Enable software Fn Lock'; value: !!root.fnlock.enabled; enabled: (!!root.readings.fn_supported || !!root.fnlock.enabled) && !appController.busy; description: 'The background service owns input. Toggle between media keys and F keys with Fn Lock.'; onEdited: value => keyboardController.setBinding('fn_lock', 'enabled', value) }
        SettingsSwitch { colors: root.colors; title: 'Media keys by default'; value: root.fnlock.media !== false; enabled: !!root.readings.fn_supported && !appController.busy; onEdited: value => keyboardController.setBinding('fn_lock', 'media', value) }
        Repeater {
            model: root.readings.fn_keys || []
            SettingsChoice {
                required property string modelData
                required property int index
                colors: root.colors; Layout.fillWidth: true; title: 'F' + (index + 1); choices: root.readings.fn_targets || []; labels: root.readings.fn_labels || []; value: root.fnlock.map ? root.fnlock.map[modelData] : ''
                enabled: !!root.readings.fn_supported && !appController.busy
                onEdited: value => keyboardController.setBinding('fn_lock', modelData, value)
            }
        }
    }
    Text { text: keyboardController.status; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
}
