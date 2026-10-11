import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import '../components'
ColumnLayout {
    id: root
    property var colors
    property string selectedChannel: '1'
    readonly property var channel: fansController.channels.find(item => item.key === selectedChannel) || fansController.channels[0] || ({})
    spacing: 16
    Flow {
        Layout.fillWidth: true; spacing: 8
        Repeater {
            model: fansController.channels.length
            Button {
                id: channelButton
                required property int index
                property var fanData: fansController.channels[index]
                property bool selected: root.channel.key === fanData.key
                width: Math.max(100, Math.min(124, (root.width-16)/3)); height: 44
                Accessible.name: fanData.title
                onClicked: { editor.cancelDrag(); root.selectedChannel = fanData.key; editor.selectedPoint = 0 }
                contentItem: Column {
                    spacing: 2
                    Text { text: channelButton.fanData.title; color: channelButton.selected ? root.colors.accent : root.colors.text; font.pixelSize: 12; anchors.horizontalCenter: parent.horizontalCenter }
                    Text { text: channelButton.fanData.rpm === null || channelButton.fanData.rpm === undefined ? 'RPM unavailable' : channelButton.fanData.rpm + ' RPM'; color: root.colors.mutedText; font.pixelSize: 11; anchors.horizontalCenter: parent.horizontalCenter }
                }
                background: Rectangle { radius: 7; color: channelButton.selected ? root.colors.selected : channelButton.hovered ? root.colors.hover : root.colors.panel; border.color: channelButton.selected || channelButton.activeFocus ? root.colors.accent : root.colors.border }
            }
        }
    }
    Text { visible: fansController.ready && fansController.channels.length === 0; text: 'No custom fan curve interface was detected.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    MetricCard {
        visible: fansController.channels.length > 0
        Layout.fillWidth: true; colors: root.colors; title: (root.channel.title || 'Fan') + ' curve'
        RowLayout {
            Layout.fillWidth: true
            Text { text: 'Fan speed'; color: root.colors.mutedText; font.pixelSize: 11; Layout.fillWidth: true }
            RogButton { colors: root.colors; text: '%'; implicitWidth: 48; primary: fansController.displayUnit === 'percent'; enabled: !appController.busy; onClicked: fansController.setDisplayUnit('percent') }
            RogButton { colors: root.colors; text: 'RPM'; implicitWidth: 56; primary: fansController.displayUnit === 'rpm'; enabled: !appController.busy; onClicked: fansController.setDisplayUnit('rpm') }
        }
    Text { text: fansController.displayUnit === 'rpm' ? 'RPM values are estimates based on fan calibration.' : 'Adjust points, then Apply to save your curve.'; color: root.colors.mutedText; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        CurveEditor {
            id: editor
            Layout.fillWidth: true; colors: root.colors; points: root.channel.points || []
            calibration: root.channel.calibration || [0, 1]; displayUnit: fansController.displayUnit
            enabled: !appController.busy && fansController.channels.length > 0
            onPointMoved: (index, temperature, percent) => fansController.movePoint(root.channel.key, index, temperature, percent)
            onPointAdded: (temperature, percent) => {
                var added = fansController.addPoint(root.channel.key, temperature, percent)
                if (added >= 0) editor.selectedPoint = added
            }
            onPointRemoved: index => fansController.removePoint(root.channel.key, index)
        }
    }
    Text { text: (fansController.live.mismatch || []).length ? 'Driver curve mismatch or disabled channel: ' + fansController.live.mismatch.join(', ') + '. Apply to restore curves.' : ''; visible: text.length > 0; color: root.colors.accent; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    Text { Layout.fillWidth: true; text: fansController.status; visible: text.length > 0; color: root.colors.mutedText; wrapMode: Text.WordWrap; font.pixelSize: 12 }
}
