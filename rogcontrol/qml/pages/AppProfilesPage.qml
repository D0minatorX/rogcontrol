import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import '../components'

ColumnLayout {
    id: root
    property var colors
    readonly property var editor: appProfilesController
    property int selectedSetting: 0
    spacing: 12

    Connections {
        target: root.editor
        function onPicked() {
            root.selectedSetting = 0
            overrides.open()
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.topMargin: 10
        Text {
            Layout.fillWidth: true
            text: root.editor.apps.length ? "Hover an app and click the pen to edit its overrides."
                                          : "Choose an executable to set overrides that apply while it runs."
            wrapMode: Text.WordWrap
            color: root.colors.mutedText
        }
        RogButton {
            colors: root.colors
            text: "Add executable…"
            enabled: !appController.busy
            onClicked: root.editor.choose()
        }
    }

    Flow {
        Layout.fillWidth: true
        spacing: 12
        Repeater {
            model: root.editor.apps
            delegate: Item {
                id: tile
                required property var modelData
                width: 120
                height: 128
                HoverHandler { id: hover }
                AngularPanel {
                    anchors.fill: parent
                    cut: 10
                    decorated: false
                    fillColor: hover.hovered ? root.colors.hover : root.colors.panel
                    lineColor: hover.hovered ? root.colors.accent : root.colors.border
                    accentColor: root.colors.accent
                }
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6
                    Item {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.preferredWidth: 56
                        Layout.preferredHeight: 56
                        Image {
                            id: icon
                            anchors.fill: parent
                            source: "image://appicon/" + encodeURIComponent(tile.modelData.name)
                            sourceSize: Qt.size(56, 56)
                            fillMode: Image.PreserveAspectFit
                        }
                        Text {
                            anchors.centerIn: parent
                            visible: icon.status !== Image.Ready
                            text: "▣"
                            font.pixelSize: 40
                            color: root.colors.mutedText
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        text: tile.modelData.name
                        color: root.colors.text
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WrapAnywhere
                        elide: Text.ElideRight
                        maximumLineCount: 2
                    }
                }
                ToolTip.visible: hover.hovered
                ToolTip.text: tile.modelData.path
                ToolTip.delay: 600
                RogButton {
                    objectName: "editApp"
                    colors: root.colors
                    text: "✎"
                    implicitWidth: 30
                    implicitHeight: 30
                    anchors.top: parent.top
                    anchors.right: parent.right
                    anchors.margins: 4
                    opacity: hover.hovered || activeFocus ? 1 : 0
                    enabled: !appController.busy
                    Accessible.name: "Edit overrides for " + tile.modelData.name
                    onClicked: {
                        root.selectedSetting = 0
                        root.editor.select(tile.modelData.path)
                        overrides.open()
                    }
                }
            }
        }
    }

    RogDialog {
        id: overrides
        objectName: "overridesDialog"
        colors: root.colors
        parent: Overlay.overlay
        width: Math.min(parent.width - 32, 560)
        height: Math.min(parent.height - 32, 640)
        title: root.editor.path ? root.editor.path.split("/").pop() : ""
        acceptText: "Save profile"
        rejectText: "Cancel"
        onAccepted: root.editor.save()
        ScrollView {
            anchors.fill: parent
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: parent.width
                enabled: !appController.busy
                spacing: 14

                Text {
                    Layout.fillWidth: true
                    text: root.editor.path
                    wrapMode: Text.WrapAnywhere
                    color: root.colors.mutedText
                }
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
                    // a new model (every edit relabels rows) resets currentIndex to 0
                    onModelChanged: currentIndex = Math.min(root.selectedSetting, count - 1)
                    objectName: "settingCombo"
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
                        SettingsChoice {
                            visible: row.kind === 'bool'
                            Layout.fillWidth: true
                            colors: root.colors
                            title: row.title
                            choices: ['inherit', 'on', 'off']
                            labels: ['No override', 'On', 'Off']
                            value: row.overridden ? (row.value ? 'on' : 'off') : 'inherit'
                            enabled: row.available || row.overridden
                            onEdited: value => root.editor.setMode(row.section, row.key, value)
                        }
                        SettingsSwitch {
                            visible: row.kind !== 'bool'
                            Layout.fillWidth: true
                            colors: root.colors
                            title: row.overridden ? "Override enabled" : "Inherited"
                            value: row.overridden
                            enabled: row.available || row.overridden
                            onEdited: value => root.editor.toggle(row.section, row.key, value)
                        }
                        Text {
                            visible: !row.available
                            text: row.section === "cpu" && !["boost", "max_freq", "min_freq"].includes(row.key)
                                  ? "Power limits are turned off in the system profile. Turn them on in CPU to override this."
                                  : "Waiting for a supported control or a restorable system profile value."
                            color: root.colors.mutedText
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        Loader {
                            Layout.fillWidth: true
                            visible: row.kind !== 'bool'
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

                RogButton {
                    colors: root.colors
                    text: "Remove profile"
                    visible: root.editor.apps.some(app => app.path === root.editor.path)
                    onClicked: {
                        root.editor.remove()
                        overrides.close()
                    }
                }
            }
        }
    }
}
