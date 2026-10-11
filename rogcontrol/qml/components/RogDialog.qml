import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: root
    property var colors
    property string acceptText: 'Confirm'
    property string rejectText: 'Cancel'
    property bool acceptEnabled: true
    property bool showCancel: true
    anchors.centerIn: parent
    width: Math.min(parent.width - 32, 460)
    modal: true
    focus: true
    padding: 24
    topPadding: 16
    bottomPadding: 20
    closePolicy: Popup.CloseOnEscape
    background: Rectangle {
        radius: 16
        color: root.colors.panel
        border.color: root.colors.border
    }
    Overlay.modal: Rectangle { color: '#99000000' }
    header: RowLayout {
        spacing: 10
        Rectangle { Layout.leftMargin: 24; Layout.topMargin: 24; implicitWidth: 18; implicitHeight: 3; radius: 1; color: root.colors.accent }
        Text {
            Layout.fillWidth: true; Layout.topMargin: 24; Layout.rightMargin: 24
            text: root.title; color: root.colors.text
            font.pixelSize: 18; font.weight: Font.DemiBold
            wrapMode: Text.WordWrap
        }
    }
    footer: RowLayout {
        spacing: 10
        Item { Layout.fillWidth: true }
        RogButton {
            colors: root.colors; text: root.rejectText; visible: root.showCancel
            Layout.bottomMargin: 24
            onClicked: root.reject()
        }
        RogButton {
            colors: root.colors; text: root.acceptText; primary: true
            enabled: root.acceptEnabled
            Layout.rightMargin: 24; Layout.bottomMargin: 24
            onClicked: root.accept()
        }
    }
}
