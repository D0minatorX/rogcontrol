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
    property int section: 0
    columns: root.width >= 800 ? 2 : 1
    columnSpacing: 14; rowSpacing: 14
    SegmentedSetting {
        Layout.columnSpan: root.columns
        Layout.fillWidth: true
        Layout.preferredWidth: 360
        Layout.maximumWidth: 360
        colors: root.colors
        navigation: true
        repeaterName: 'keyboardSections'
        choices: ['Lighting', 'Key assignments']
        value: root.section
        onEdited: value => root.section = value
    }
    MetricCard {
        Layout.columnSpan: root.columns
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 1
        visible: root.section === 0
        objectName: 'lightingCard'
        title: 'Keyboard lighting'; colors: root.colors; Layout.fillWidth: true
        GridLayout {
            Layout.fillWidth: true
            columns: root.width >= 760 ? 2 : 1
            columnSpacing: 28
            rowSpacing: 20
            SegmentedSetting {
                colors: root.colors
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: 'Brightness'
                choices: ['Off', 'Low', 'Medium', 'High']
                value: root.lighting.brightness
                repeaterName: 'brightnessLevels'
                buttonPrefix: 'brightnessLevel'
                onEdited: value => keyboardController.setLighting('brightness', value)
            }
            SettingsChoice {
                colors: root.colors
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: 'Effect'
                choices: root.readings.modes || []
                value: root.lighting.mode
                onEdited: value => keyboardController.setLighting('mode', value)
            }
            GridLayout {
                visible: ['Static','Breathing','Pulse','Gradient Static'].indexOf(root.lighting.mode) >= 0
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                columns: root.width >= 980 ? 2 : 1
                columnSpacing: 20
                rowSpacing: 12
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Text { text: 'Primary colour'; color: root.colors.text; font.pixelSize: 12 }
                    ColorPicker { colors: root.colors; value: root.lighting.color1; onEdited: value => keyboardController.setLighting('color1', value.toString()) }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    visible: ['Breathing','Gradient Static'].indexOf(root.lighting.mode) >= 0
                    Text { text: 'Secondary colour'; color: root.colors.text; font.pixelSize: 12 }
                    ColorPicker { colors: root.colors; value: root.lighting.color2; onEdited: value => keyboardController.setLighting('color2', value.toString()) }
                }
            }
            SegmentedSetting {
                objectName: 'animationSpeed'
                visible: ['Breathing','Pulse','Color Cycle','Rainbow'].indexOf(root.lighting.mode) >= 0
                colors: root.colors
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: 'Animation speed'
                choices: ['Slow', 'Medium', 'Fast']
                minimum: 1
                value: root.lighting.speed
                repeaterName: 'speedLevels'
                buttonPrefix: 'speedLevel'
                onEdited: value => keyboardController.setLighting('speed', value)
            }
        }
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: root.colors.border }
        RowLayout {
            Layout.fillWidth: true
            spacing: 16
            Text {
                text: [root.readings.hint || '',
                       root.readings.dimmed ? 'Temporarily dimmed by idle policy; saved brightness is preserved.' : '',
                       root.lighting.mode === 'Ambient' ? 'Ambient follows your screen and requests permission to capture it.' : '']
                      .filter(function(line) { return line.length > 0 }).join('\n')
                color: root.colors.mutedText
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                font.pixelSize: 12
            }
            RogButton { colors: root.colors; text: 'Recheck'; onClicked: keyboardController.retry() }
        }
    }
    MetricCard {
        Layout.columnSpan: root.columns
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 1
        title: 'Profile colours'; colors: root.colors; Layout.fillWidth: true
        visible: root.section === 0 && root.lighting.mode === 'Profile Color'
        GridLayout {
            Layout.fillWidth: true
            columns: root.width >= 800 ? 3 : (root.width > 620 ? 2 : 1)
            columnSpacing: 24
            rowSpacing: 12
            Repeater {
                model: appController.profileNames
                ColumnLayout {
                    required property string modelData
                    Text { text: modelData; color: root.colors.text }
                    ColorPicker { colors: root.colors; value: keyboardController.profileColors[modelData] || '#ee2445'; onEdited: value => keyboardController.setProfileColor(modelData, value.toString()) }
                }
            }
        }
    }
    GridLayout {
        Layout.columnSpan: root.columns
        Layout.fillWidth: true
        columns: root.columns
        columnSpacing: 14
        rowSpacing: 14
        visible: root.section === 0
        ColumnLayout {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.alignment: Qt.AlignTop
            spacing: 14
            MetricCard {
                Layout.alignment: Qt.AlignTop
                Layout.preferredWidth: 1
                visible: root.section === 0
                title: 'Backlight inactivity timeout'; colors: root.colors; Layout.fillWidth: true
                Text { text: root.readings.idle || 'Checking desktop idle detection…'; color: root.colors.mutedText; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                NumberSetting { colors: root.colors; Layout.fillWidth: true; title: 'On external power'; unit: 's'; minimum: 0; maximum: 3600; step: 10; value: root.config.kbd_idle_timeout_ac_seconds || 0; enabled: !!root.readings.idle_supported && !appController.busy; onEdited: value => keyboardController.setPreference('kbd_idle_timeout_ac_seconds', value) }
                NumberSetting { colors: root.colors; Layout.fillWidth: true; title: 'On battery'; helpText: 'Seconds · 0 = off'; unit: 's'; minimum: 0; maximum: 3600; step: 10; value: root.config.kbd_idle_timeout_battery_seconds || 0; enabled: !!root.readings.idle_supported && !appController.busy; onEdited: value => keyboardController.setPreference('kbd_idle_timeout_battery_seconds', value) }
                RogButton { colors: root.colors; text: 'Turn off saved timeouts'; visible: root.readings.idle_supported === false && ((root.config.kbd_idle_timeout_ac_seconds || 0) > 0 || (root.config.kbd_idle_timeout_battery_seconds || 0) > 0); enabled: !appController.busy; onClicked: keyboardController.disableIdle() }
            }
            MetricCard {
                Layout.alignment: Qt.AlignTop
                Layout.preferredWidth: 1
                visible: root.section === 0 && !!(root.readings.caps && root.readings.caps.kbd_rgb)
                title: 'Charger flash'; colors: root.colors; Layout.fillWidth: true
                SettingsSwitch { trailing: true; colors: root.colors; title: 'Flash on charger change'; value: !!root.config.charger_flash; enabled: !appController.busy; description: 'The background service restores your lighting. Ambient cannot be restored, so flashing is skipped in Ambient mode.'; onEdited: value => keyboardController.setPreference('charger_flash', value) }
                ColorPicker { colors: root.colors; value: keyboardController.flashColor; enabled: !appController.busy; onEdited: value => keyboardController.setPreference('charger_flash_color', value.toString()) }
            }
        }
        MetricCard {
            Layout.alignment: Qt.AlignTop
            Layout.fillHeight: true
            Layout.preferredWidth: 1
            visible: root.section === 0
            title: 'Lighting power policy'; colors: root.colors; Layout.fillWidth: true
            SettingsSwitch { trailing: true; colors: root.colors; title: 'Manage lighting power states'; value: !!root.power.enabled; enabled: (!!root.readings.power_supported || !!root.power.enabled) && !appController.busy; onEdited: value => keyboardController.setPower('', 'enabled', value) }
            Text { visible: !root.readings.power_supported; text: 'Lighting power zones require a supported ASUS interface. Recheck after reconnecting.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            RowLayout {
                Layout.fillWidth: true
                Text { text: 'POWER STATE'; color: root.colors.mutedText; font.pixelSize: 10; Layout.fillWidth: true }
                Text { text: 'KEYBOARD'; color: root.colors.mutedText; font.pixelSize: 10; Layout.preferredWidth: 90; horizontalAlignment: Text.AlignHCenter }
                Text { text: 'LIGHTBAR'; color: root.colors.mutedText; font.pixelSize: 10; Layout.preferredWidth: 90; horizontalAlignment: Text.AlignHCenter }
            }
            Repeater {
                model: ['boot', 'awake', 'sleep', 'shutdown', 'battery']
                RowLayout {
                    id: powerRow
                    required property string modelData
                    Layout.fillWidth: true
                    Text {
                        text: ({boot: 'During boot', awake: 'While awake', sleep: 'While asleep', shutdown: 'After shutdown', battery: 'On battery'})[powerRow.modelData]
                        color: root.colors.text
                        font.pixelSize: 12
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                    }
                    Repeater {
                        model: ['keyboard', 'lightbar']
                        Item {
                            required property string modelData
                            Layout.preferredWidth: 90
                            implicitHeight: 30
                            RogSwitch {
                                anchors.centerIn: parent
                                objectName: 'power-' + parent.modelData + '-' + powerRow.modelData
                                implicitWidth: 44
                                implicitHeight: 30
                                colors: root.colors
                                checked: root.power[parent.modelData] ? !!root.power[parent.modelData][powerRow.modelData] : false
                                enabled: !!root.readings.power_supported && !!root.power.enabled && !appController.busy
                                Accessible.name: parent.modelData + ' ' + powerRow.modelData
                                onClicked: keyboardController.setPower(parent.modelData, powerRow.modelData, checked)
                            }
                        }
                    }
                }
            }
        }
    }
    MetricCard {
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 1
        Layout.columnSpan: root.columns
        visible: root.section === 1
        objectName: 'hotkeysCard'
        title: 'Hardware hotkeys'; colors: root.colors; Layout.fillWidth: true
        Text { text: [root.readings.input_status || 'Checking input access…', root.readings.runtime || ''].filter(function(line) { return line.length > 0 }).join('\n'); font.pixelSize: 12; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        SettingsSwitch { trailing: true; colors: root.colors; title: 'Enable ASUS button assignments'; value: !!root.bindings.enabled; enabled: (!!root.readings.bindings_supported || !!root.bindings.enabled) && !appController.busy; onEdited: value => keyboardController.setBinding('key_bindings', 'enabled', value) }
        GridLayout {
            Layout.fillWidth: true
            columns: root.width >= 1000 ? 3 : (root.width >= 620 ? 2 : 1)
            columnSpacing: 24
            rowSpacing: 14
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
    }
    MetricCard {
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 1
        Layout.columnSpan: root.columns
        visible: root.section === 1
        title: 'Fn Lock'; colors: root.colors; Layout.fillWidth: true
        SettingsSwitch { trailing: true; colors: root.colors; title: 'FN lock'; value: !!root.fnlock.enabled; enabled: (!!root.readings.fn_supported || !!root.fnlock.enabled) && !appController.busy; description: 'The background service owns input. Toggle between media keys and F keys with Fn Lock.'; onEdited: value => keyboardController.setBinding('fn_lock', 'enabled', value) }
        SettingsSwitch { trailing: true; colors: root.colors; title: 'Media actions on F1–F12'; description: 'Super+F2 toggles media actions. Modified F-key shortcuts keep their normal behaviour.'; value: root.fnlock.media !== false; enabled: !!root.readings.fn_supported && !!root.fnlock.enabled && !appController.busy; onEdited: value => keyboardController.setBinding('fn_lock', 'media', value) }
        GridLayout {
            Layout.fillWidth: true
            columns: root.width >= 1000 ? 3 : (root.width >= 620 ? 2 : 1)
            columnSpacing: 24
            rowSpacing: 14
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
    }
    Text { visible: text.length > 0; Layout.columnSpan: root.columns; text: keyboardController.status; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
}
