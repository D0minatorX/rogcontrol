import QtQuick
import QtQuick.Controls

Button {
    id: root
    property var colors
    property var readings: batteryController.data
    implicitWidth: 36
    implicitHeight: 32
    hoverEnabled: true
    Accessible.name: 'Battery: ' + percentText + '. Hover for battery details'

    readonly property string percentText: readings.percent === undefined || readings.percent === null
        ? '—' : Math.round(Number(readings.percent)) + '%'
    readonly property var healthData: readings.health || ({})
    readonly property string healthPercent: {
        return healthData.percent === undefined || healthData.percent === null
            ? '—' : Number(healthData.percent).toFixed(1) + '%'
    }
    readonly property string capacityText: healthData.full === undefined || healthData.full === null || healthData.design === undefined || healthData.design === null
        ? '—' : Number(healthData.full).toFixed(1) + ' / ' + Number(healthData.design).toFixed(1) + ' ' + (healthData.unit || 'Wh')
    readonly property string chargeState: readings.charging === undefined || readings.charging === null
        ? 'State unavailable' : readings.charging ? 'Charging' : 'Not charging'

    Popup {
        id: detailsPopup
        parent: root
        x: Math.max(-220, Math.min(0, (root.width - width) / 2))
        y: root.height + 8
        width: 280
        padding: 14
        closePolicy: Popup.NoAutoClose
        visible: root.hovered || popupHover.hovered

        background: Rectangle {
            radius: 12
            color: root.colors.panel
            border.width: 1
            border.color: root.colors.border
        }

        contentItem: Column {
            spacing: 12

            Row {
                spacing: 12
                width: parent.width
                Rectangle {
                    width: 38; height: 38; radius: 10
                    color: root.colors.hover
                    Item {
                        width: 27; height: 16
                        anchors.centerIn: parent
                        Rectangle {
                            x: 0; y: 1; width: 22; height: 14; radius: 3
                            color: 'transparent'
                            border.width: 1.5; border.color: root.colors.text
                            clip: true
                            Rectangle {
                                x: 2; y: 3; width: 18; height: 8; radius: 2
                                color: root.colors.border
                                Rectangle {
                                    width: parent.width * Math.max(0, Math.min(100, Number(root.readings.percent || 0))) / 100
                                    height: parent.height
                                    radius: parent.radius
                                    color: root.colors.accent
                                }
                            }
                        }
                        Rectangle {
                            x: 23; y: 5; width: 3; height: 6
                            radius: 1.5; color: root.colors.text
                        }
                    }
                }
                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 2
                    Label {
                        text: root.percentText
                        color: root.colors.text
                        font.pixelSize: 20
                        font.weight: Font.DemiBold
                    }
                    Label {
                        text: root.chargeState + ' · ' + (root.readings.source || 'Unknown source')
                        color: root.colors.mutedText
                        font.pixelSize: 12
                    }
                }
            }

            Rectangle {
                width: parent.width; height: 1
                color: root.colors.border
                opacity: 0.8
            }

            Row {
                width: parent.width
                spacing: 16
                Column {
                    width: (parent.width - parent.spacing) / 2
                    spacing: 4
                    Label { text: 'HEALTH'; color: root.colors.mutedText; font.pixelSize: 10; font.weight: Font.Medium }
                    Label { text: root.healthPercent; color: root.colors.text; font.pixelSize: 15 }
                }
                Column {
                    width: (parent.width - parent.spacing) / 2
                    spacing: 4
                    Label { text: 'FULL / DESIGN'; color: root.colors.mutedText; font.pixelSize: 10; font.weight: Font.Medium }
                    Label { text: root.capacityText; color: root.colors.text; font.pixelSize: 15; elide: Text.ElideRight; width: parent.width }
                }
            }
        }

        HoverHandler { id: popupHover }
        enter: Transition { NumberAnimation { property: 'opacity'; from: 0; to: 1; duration: 120 } }
        exit: Transition { NumberAnimation { property: 'opacity'; from: 1; to: 0; duration: 100 } }
    }

    contentItem: Item {
        width: 26
        height: 20
        Rectangle {
            x: 1; y: 3; width: 20; height: 14
            radius: 3; color: 'transparent'
            border.width: 1.5; border.color: root.colors.text
            Rectangle {
                x: 3; y: 3
                width: 14 * Math.max(0, Math.min(100, Number(root.readings.percent || 0))) / 100
                height: 8; radius: 2; color: root.colors.accent
            }
        }
        Rectangle {
            x: 22; y: 7; width: 3; height: 6
            radius: 1.5; color: root.colors.text
        }
    }
    background: Rectangle {
        radius: 7
        color: root.hovered || root.down ? root.colors.hover : 'transparent'
        border.color: root.activeFocus ? root.colors.accent : 'transparent'
    }
}
