import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Item {
    id: root
    property int cornerRadius: 0
    property var colors
    implicitHeight: 100
    readonly property int minimumTileWidth: 160
    readonly property int tileSpacing: 16
    readonly property int sideMargin: 32
    readonly property real minimumRowWidth: appController.profileNames.length * (minimumTileWidth + tileSpacing) - tileSpacing
    ProfileSurface {
        anchors.fill: parent; anchors.margins: 22; anchors.topMargin: 2
        colors: root.colors; rail: true
    }
    ScrollView {
        id: viewport
        anchors.fill: parent; anchors.leftMargin: root.sideMargin
        anchors.rightMargin: root.minimumRowWidth > root.width - 2 * root.sideMargin ? 2 * root.sideMargin : root.sideMargin; anchors.topMargin: 12; anchors.bottomMargin: 32
        clip: true; contentHeight: availableHeight; contentWidth: tiles.implicitWidth
        ScrollBar.vertical.policy: ScrollBar.AlwaysOff
        ScrollBar.horizontal.policy: ScrollBar.AsNeeded
        Row {
            id: tiles
            spacing: root.tileSpacing; height: viewport.availableHeight
            Repeater {
                model: appController.profileNames
                Button {
                    id: profile
                    required property string modelData
                    required property int index
                    property bool selected: modelData === appController.activeProfile
                    width: Math.max(root.minimumTileWidth, (viewport.availableWidth - root.tileSpacing * (appController.profileNames.length-1)) / Math.max(1, appController.profileNames.length))
                    height: Math.min(54, tiles.height)
                    anchors.verticalCenter: parent.verticalCenter
                    enabled: !appController.busy
                    onClicked: appController.selectProfile(modelData)
                    Accessible.name: modelData + (selected ? ', selected' : '')
                    ToolTip.visible: hovered
                    ToolTip.delay: 800
                    ToolTip.text: modelData + ' · Select and apply'
                    contentItem: RowLayout {
                        spacing: 9
                        Item { Layout.fillWidth: true }
                        ProfileIcon { profileName: profile.modelData; tint: profile.selected ? root.colors.accent : root.colors.mutedText }
                        Text { Layout.maximumWidth: profile.width - 56; text: profile.modelData; color: profile.selected ? root.colors.text : root.colors.mutedText; font.pixelSize: 13; font.weight: profile.selected ? Font.DemiBold : Font.Normal; elide: Text.ElideRight }
                        Item { Layout.fillWidth: true }
                    }
                    background: ProfileSurface {
                        colors: root.colors
                        selected: profile.selected
                        hovered: profile.hovered
                        focused: profile.activeFocus
                    }
                    Rectangle {
                        visible: index < appController.profileNames.length - 1
                        anchors.right: parent.right; anchors.rightMargin: -8
                        anchors.verticalCenter: parent.verticalCenter
                        width: 1; height: 18; color: root.colors.border
                    }
                }
            }
        }
    }
}
