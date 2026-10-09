import QtQuick
import QtQuick.Layouts
Rectangle {
    id: root
    property var colors
    property bool pending: false
    property bool busy: false
    signal applyRequested()
    signal revertRequested()
    color: colors.selected
    implicitHeight: row.implicitHeight + 20
    RowLayout {
        id: row
        anchors.fill: parent; anchors.margins: 10
        Text { Layout.fillWidth: true; text: root.busy ? 'Operation in progress…' : root.pending ? 'Pending changes · Apply or revert before switching profiles' : 'Saved settings'; color: root.colors.text; font.pixelSize: 11; wrapMode: Text.WordWrap }
        RogButton { colors: root.colors; text: 'Revert'; enabled: root.pending && !root.busy; onClicked: root.revertRequested() }
        RogButton { colors: root.colors; text: 'Apply'; primary: true; enabled: !root.busy; onClicked: root.applyRequested() }
    }
}
