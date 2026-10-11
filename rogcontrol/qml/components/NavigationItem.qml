import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Button {
    id: root
    property string symbol: "◉"
    property bool expanded: false
    property bool selected: false
    property var colors
    implicitWidth: expanded ? 140 : 66
    implicitHeight: expanded ? 46 : 64
    hoverEnabled: true
    Accessible.name: text
    contentItem: GridLayout {
        columns: root.expanded ? 2 : 1
        columnSpacing: 12; rowSpacing: 7
        NavigationIcon { name: root.symbol; tint: root.selected ? root.colors.accent : root.colors.mutedText; Layout.alignment: Qt.AlignHCenter; Layout.leftMargin: root.expanded ? 10 : 0 }
        Text { text: root.text; color: root.selected ? root.colors.text : root.colors.mutedText; font.pixelSize: root.expanded ? 12 : 10; font.weight: root.selected ? Font.DemiBold : Font.Normal; Layout.fillWidth: root.expanded; Layout.alignment: root.expanded ? Qt.AlignLeft : Qt.AlignHCenter }
    }
    background: Rectangle {
        radius: 10
        color: root.selected ? root.colors.selected : root.hovered ? root.colors.hover : "transparent"
        Rectangle { anchors.verticalCenter: parent.verticalCenter; width: 3; height: 23; radius: 1; color: root.colors.accent; visible: root.selected }
        Behavior on color { ColorAnimation { duration: 120 } }
        border.color: root.activeFocus ? root.colors.text : "transparent"
    }
}
