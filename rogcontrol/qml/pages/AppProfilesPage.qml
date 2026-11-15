import QtQuick
import QtQuick.Layouts
import '../components'

ColumnLayout {
    id: root
    property var colors
    readonly property var editor: appProfilesController
    property int selectedSetting: 0
    spacing: 12

    RowLayout {
        Layout.fillWidth: true
        Layout.topMargin: 10
        RogComboBox {
            colors: root.colors
            Layout.fillWidth: true
            model: root.editor.paths
            currentIndex: root.editor.paths.indexOf(root.editor.path)
            onActivated: index => root.editor.select(root.editor.paths[index])
            Accessible.name: "Saved app profiles"
            enabled: !appController.busy
        }
        RogButton {
            colors: root.colors
            text: "Add executable…"
            enabled: !appController.busy
            onClicked: root.editor.choose()
        }
    }

    Text {
        Layout.fillWidth: true
        text: root.editor.path || "Choose an executable to set overrides that apply while it runs."
        wrapMode: Text.WrapAnywhere
        color: root.colors.mutedText
    }

    ColumnLayout {
        Layout.fillWidth: true
        visible: root.editor.path !== ""
        enabled: !appController.busy
        spacing: 14

        Text {
            Layout.fillWidth: true
            text: "Overrides apply while the executable runs. Inherited settings follow the system profile."
            wrapMode: Text.WordWrap
            color: root.colors.mutedText
        }

        RogComboBox {
            colors: root.colors
            Layout.fillWidth: true
            model: root.editor.rows.map(row => row.section.toUpperCase() + " · " + row.title
                                        + (row.overridden ? " · Override" : ""))
            currentIndex: Math.min(root.selectedSetting, count - 1)
            onActivated: index => root.selectedSetting = index
            Accessible.name: "Setting to edit"
        }

        Repeater {
            model: root.editor.rows.length
            MetricCard {
                required property int index
                readonly property var row: root.editor.rows[index]
                Layout.fillWidth: true
                colors: root.colors
                title: row.title
                visible: index === Math.min(root.selectedSetting, root.editor.rows.length - 1)
                SettingsSwitch {
                    Layout.fillWidth: true
                    colors: root.colors
                    title: row.overridden ? "Override enabled" : "Inherited"
                    value: row.overridden
                    enabled: row.available || row.overridden
                    onEdited: value => root.editor.toggle(row.section, row.key, value)
                }
                Text {
                    visible: !row.available
                    text: "Waiting for a supported control or a restorable system profile value."
                    color: root.colors.mutedText
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Loader {
                    Layout.fillWidth: true
                    sourceComponent: row.section === 'fans' ? curve : slider
                    Component {
                        id: slider
                        SliderRow {
                            colors: root.colors
                            title: row.title
                            unit: row.unit || ""
                            minimum: row.min || 0
                            maximum: row.max || 100
                            step: row.step || 1
                            value: Number(row.value) / (row.scale || 1)
                            enabled: row.available && row.overridden
                            onEdited: value => root.editor.stage(row.section, row.key, value)
                        }
                    }
                    Component {
                        id: curve
                        CurveEditor {
                            id: fanEditor
                            colors: root.colors
                            points: row.value || []
                            enabled: row.available && row.overridden
                            onPointMoved: (index, temperature, percent) =>
                                root.editor.movePoint(row.key, index, temperature, percent)
                            onPointAdded: (temperature, percent) => {
                                const index = root.editor.addPoint(row.key, temperature, percent)
                                if (index >= 0)
                                    fanEditor.selectedPoint = index
                            }
                            onPointRemoved: index => root.editor.removePoint(row.key, index)
                        }
                    }
                }
            }
        }

        RowLayout {
            RogButton {
                colors: root.colors
                text: "Save profile"
                primary: true
                onClicked: root.editor.save()
            }
            RogButton {
                colors: root.colors
                text: "Remove profile"
                visible: root.editor.paths.indexOf(root.editor.path) >= 0
                onClicked: root.editor.remove()
            }
        }
    }
}
