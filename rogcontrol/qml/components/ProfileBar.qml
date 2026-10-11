import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Rectangle {
    id: root
    property int cornerRadius: 0
    radius: cornerRadius
    property var colors
    color: colors.sidebar
    implicitHeight: 72
    Rectangle { anchors.top: parent.top; width: parent.width; height: parent.height / 2; color: root.colors.sidebar }
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
                    width: Math.max(160, (viewport.availableWidth - 8 * (appController.profileNames.length-1)) / Math.max(1, appController.profileNames.length))
                    height: 46
                    enabled: !appController.busy
                    onClicked: appController.selectProfile(modelData)
                    Accessible.name: modelData + (selected ? ', selected' : '')
                    ToolTip.visible: hovered
                    ToolTip.text: modelData + ' · Select and apply'
                    contentItem: RowLayout {
                        spacing: 9
                        Item { Layout.fillWidth: true }
                        ProfileIcon { profileName: profile.modelData; tint: profile.selected ? root.colors.accent : root.colors.mutedText }
                        Text { Layout.maximumWidth: profile.width - 56; text: profile.modelData; color: profile.selected ? root.colors.text : root.colors.mutedText; font.pixelSize: 11; font.weight: profile.selected ? Font.DemiBold : Font.Normal; elide: Text.ElideRight }
                        Item { Layout.fillWidth: true }
                    }
                    background: Rectangle {
                        radius: 10
                        color: profile.selected ? root.colors.selected : profile.hovered ? root.colors.hover : 'transparent'
                        border.color: profile.activeFocus ? root.colors.accent : profile.selected ? root.colors.selected : 'transparent'
                        Rectangle { visible: profile.selected; height: 2; radius: 1; width: 30; anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; color: root.colors.accent }
                    }
                }
            }
        }
    }
}
