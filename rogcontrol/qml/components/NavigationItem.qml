import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Button {
    id: root
    property string symbol: "◉"
    property bool expanded: false
    property bool selected: false
    property var colors
    implicitWidth: expanded ? 152 : 66
    implicitHeight: expanded ? 46 : 64
    hoverEnabled: true
    Accessible.name: text
    contentItem: GridLayout {
        columns: root.expanded ? 2 : 1
        columnSpacing: 12; rowSpacing: 7
        NavigationIcon { name: root.symbol; tint: root.selected ? root.colors.accent : root.colors.mutedText; Layout.alignment: Qt.AlignHCenter; Layout.leftMargin: root.expanded ? 10 : 0 }
        Text { text: root.text; color: root.selected ? root.colors.text : root.colors.mutedText; font.pixelSize: root.expanded ? 14 : 10; font.weight: root.selected ? Font.DemiBold : Font.Normal; Layout.fillWidth: root.expanded; Layout.alignment: root.expanded ? Qt.AlignLeft : Qt.AlignHCenter }
    }
    background: AngularPanel {
        cut: 9
        decorated: root.selected
        fillColor: root.selected ? root.colors.selected : root.hovered ? root.colors.hover : 'transparent'
        lineColor: root.selected || root.activeFocus ? root.colors.accent : 'transparent'
        accentColor: root.colors.accent
        highlighted: root.selected
    }
}
