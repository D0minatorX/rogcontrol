import QtQuick
import QtQuick.Controls
Rectangle {
    id: root
    property var colors
    color: colors.window
    implicitHeight: 96
    Rectangle { width: parent.width; height: 1; color: root.colors.border }
    ScrollView {
        id: viewport
        anchors.fill: parent; anchors.margins: 12
        clip: true; contentHeight: availableHeight; contentWidth: tiles.implicitWidth
        ScrollBar.vertical.policy: ScrollBar.AlwaysOff
        ScrollBar.horizontal.policy: ScrollBar.AsNeeded
        Row {
            id: tiles
            spacing: 8; height: viewport.availableHeight
            Repeater {
                model: appController.profileNames
                Button {
                    id: profile
                    required property string modelData
                    property bool selected: modelData === appController.activeProfile
                    width: Math.max(145, (viewport.availableWidth - 8 * (appController.profileNames.length-1)) / Math.max(1, appController.profileNames.length))
                    height: 64
                    enabled: !appController.busy
                    onClicked: appController.selectProfile(modelData)
                    Accessible.name: modelData + (selected ? ', selected' : '')
                    ToolTip.visible: hovered
                    ToolTip.text: modelData + ' · Select and apply'
                    contentItem: Column {
                        spacing: 5
                        Text { text: profile.modelData.toLowerCase().includes('quiet') ? '◌' : profile.modelData.toLowerCase().includes('performance') ? 'ϟ' : '◇'; color: profile.selected ? root.colors.accent : root.colors.mutedText; font.pixelSize: 20; anchors.horizontalCenter: parent.horizontalCenter }
                        Text { width: parent.width; text: profile.modelData; color: root.colors.text; font.pixelSize: 11; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight }
                    }
                    background: Rectangle {
                        radius: 6
                        color: profile.selected ? root.colors.selected : profile.hovered ? root.colors.hover : 'transparent'
                        border.color: profile.activeFocus ? root.colors.accent : 'transparent'
                        Rectangle { visible: profile.selected; height: 2; radius: 1; width: parent.width*.5; anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; color: root.colors.accent }
                    }
                }
            }
        }
    }
}
