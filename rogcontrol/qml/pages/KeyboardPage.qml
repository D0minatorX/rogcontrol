import QtQuick
import QtQuick.Layouts
import "../components"
GridLayout {
    id: root
    property var colors
    property var readings: keyboardController.data
    property var lighting: keyboardController.lighting
    property var config: keyboardController.settings
    property var power: keyboardController.powerPreferences
    property var bindings: keyboardController.inputPreferences
    property var fnlock: keyboardController.fnPreferences
    columns: root.width >= 800 ? 2 : 1
    columnSpacing: 14; rowSpacing: 14
    MetricCard {
        Layout.columnSpan: root.columns
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 1
        title: 'Keyboard lighting'; colors: root.colors; Layout.fillWidth: true
        SliderRow { colors: root.colors; Layout.fillWidth: true; title: 'Brightness'; minimum: 0; maximum: 3; value: root.lighting.brightness; onEdited: value => keyboardController.setLighting('brightness', value) }
        SettingsChoice { colors: root.colors; Layout.fillWidth: true; title: 'Effect'; choices: root.readings.modes || []; value: root.lighting.mode; onEdited: value => keyboardController.setLighting('mode', value) }
        GridLayout {
            Layout.fillWidth: true; columns: root.width > 620 ? 2 : 1; columnSpacing: 24
            ColumnLayout {
                visible: ['Static','Breathing','Pulse','Gradient Static'].indexOf(root.lighting.mode) >= 0
                Text { text: 'Colour'; color: root.colors.text }
                ColorPicker { colors: root.colors; value: root.lighting.color1; onEdited: value => keyboardController.setLighting('color1', value.toString()) }
            }
            ColumnLayout {
                visible: ['Breathing','Gradient Static'].indexOf(root.lighting.mode) >= 0
                Text { text: 'Second colour'; color: root.colors.text }
                ColorPicker { colors: root.colors; value: root.lighting.color2; onEdited: value => keyboardController.setLighting('color2', value.toString()) }
            }
        }
        SliderRow { visible: ['Breathing','Pulse','Color Cycle','Rainbow'].indexOf(root.lighting.mode) >= 0; colors: root.colors; Layout.fillWidth: true; title: 'Speed'; minimum: 1; maximum: 3; value: root.lighting.speed; onEdited: value => keyboardController.setLighting('speed', value) }
        Text { text: (root.readings.hint || '') + (root.readings.dimmed ? '\nTemporarily dimmed by idle policy; saved brightness is preserved.' : '') + (root.lighting.mode === 'Ambient' ? '\nAmbient requests permission to capture your screen. The demo uses no capture.' : ''); color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RogButton { colors: root.colors; text: 'Recheck'; onClicked: keyboardController.retry() }
    }
    MetricCard {
        Layout.columnSpan: root.columns
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 1
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
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 1
        Layout.fillHeight: true
        title: 'Backlight inactivity timeout'; colors: root.colors; Layout.fillWidth: true
        Text { text: root.readings.idle || 'Checking desktop idle detection…'; color: root.colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        NumberSetting { colors: root.colors; Layout.fillWidth: true; title: 'On external power'; unit: 's'; minimum: 0; maximum: 3600; step: 10; value: root.config.kbd_idle_timeout_ac_seconds || 0; enabled: !!root.readings.idle_supported && !appController.busy; onEdited: value => keyboardController.setPreference('kbd_idle_timeout_ac_seconds', value) }
        NumberSetting { colors: root.colors; Layout.fillWidth: true; title: 'On battery'; helpText: 'Seconds · 0 = off'; unit: 's'; minimum: 0; maximum: 3600; step: 10; value: root.config.kbd_idle_timeout_battery_seconds || 0; enabled: !!root.readings.idle_supported && !appController.busy; onEdited: value => keyboardController.setPreference('kbd_idle_timeout_battery_seconds', value) }
        RogButton { colors: root.colors; text: 'Turn off saved timeouts'; visible: root.readings.idle_supported === false && ((root.config.kbd_idle_timeout_ac_seconds || 0) > 0 || (root.config.kbd_idle_timeout_battery_seconds || 0) > 0); enabled: !appController.busy; onClicked: keyboardController.disableIdle() }
    }
    MetricCard {
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 1
        Layout.fillHeight: true
        visible: !!(root.readings.caps && root.readings.caps.kbd_rgb)
        title: 'Charger flash'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { trailing: true; colors: root.colors; title: 'Flash on charger change'; value: !!root.config.charger_flash; enabled: !appController.busy; description: 'The background service restores your lighting. Ambient cannot be restored, so flashing is skipped in Ambient mode.'; onEdited: value => keyboardController.setPreference('charger_flash', value) }
        ColorPicker { colors: root.colors; value: keyboardController.flashColor; enabled: !appController.busy; onEdited: value => keyboardController.setPreference('charger_flash_color', value.toString()) }
    }
    MetricCard {
        Layout.columnSpan: root.columns
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 1
        title: 'Lighting power policy'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { trailing: true; colors: root.colors; title: 'Manage lighting power states'; value: !!root.power.enabled; enabled: (!!root.readings.power_supported || !!root.power.enabled) && !appController.busy; onEdited: value => keyboardController.setPower('', 'enabled', value) }
        Text { visible: !root.readings.power_supported; text: 'Lighting power zones require a supported ASUS interface. Recheck after reconnecting.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        GridLayout {
            Layout.fillWidth: true; columns: root.width > 620 ? 2 : 1; columnSpacing: 32; rowSpacing: 16
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
                            trailing: true; Layout.fillWidth: true
                            required property string modelData
                            colors: root.colors; title: modelData
                            value: root.power[zoneRow.modelData] ? !!root.power[zoneRow.modelData][modelData] : false
                            enabled: !!root.readings.power_supported && !!root.power.enabled && !appController.busy
                            onEdited: value => keyboardController.setPower(zoneRow.modelData, modelData, value)
                        }
                    }
                }
            }
        }
    }
    MetricCard {
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 1
        title: 'Hardware hotkeys'; colors: root.colors; Layout.fillWidth: true
        Text { text: (root.readings.input_status || 'Checking input access…') + '\n' + (root.readings.runtime || ''); color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        SettingsSwitch { trailing: true; colors: root.colors; title: 'Enable ASUS button assignments'; value: !!root.bindings.enabled; enabled: (!!root.readings.bindings_supported || !!root.bindings.enabled) && !appController.busy; onEdited: value => keyboardController.setBinding('key_bindings', 'enabled', value) }
        Repeater {
            model: root.readings.bindings || []
            SettingsChoice {
                required property var modelData
                colors: root.colors; Layout.fillWidth: true; title: modelData.label; choices: root.readings.actions || []; labels: root.readings.action_labels || []; value: root.bindings[modelData.key]
                enabled: !!root.readings.bindings_supported && !!root.bindings.enabled && !appController.busy
                onEdited: value => keyboardController.setBinding('key_bindings', modelData.key, value)
            }
        }
    }
    MetricCard {
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 1
        title: 'Fn Lock'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { trailing: true; colors: root.colors; title: 'FN lock'; value: !!root.fnlock.enabled; enabled: (!!root.readings.fn_supported || !!root.fnlock.enabled) && !appController.busy; description: 'The background service owns input. Toggle between media keys and F keys with Fn Lock.'; onEdited: value => keyboardController.setBinding('fn_lock', 'enabled', value) }
        SettingsSwitch { trailing: true; colors: root.colors; title: 'Media actions on F1–F12'; description: 'Super+F2 toggles media actions. Modified F-key shortcuts keep their normal behaviour.'; value: root.fnlock.media !== false; enabled: !!root.readings.fn_supported && !!root.fnlock.enabled && !appController.busy; onEdited: value => keyboardController.setBinding('fn_lock', 'media', value) }
        Repeater {
            model: root.readings.fn_keys || []
            SettingsChoice {
                required property string modelData
                required property int index
                colors: root.colors; Layout.fillWidth: true; title: 'F' + (index + 1); choices: root.readings.fn_targets || []; labels: root.readings.fn_labels || []; value: root.fnlock.map ? root.fnlock.map[modelData] : ''
                enabled: !!root.readings.fn_supported && !!root.fnlock.enabled && !appController.busy
                onEdited: value => keyboardController.setBinding('fn_lock', modelData, value)
            }
        }
    }
    Text { Layout.columnSpan: root.columns; text: keyboardController.status; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
}
