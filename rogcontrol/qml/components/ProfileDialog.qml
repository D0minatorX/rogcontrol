import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
RogDialog {
    id: root
    property string operation: 'create'
    anchors.centerIn: parent
    width: Math.min(parent.width - 40, 430)
    title: operation === 'create' ? 'Create profile' : 'Duplicate profile'
    modal: true
    acceptText: operation === 'create' ? 'Create' : 'Duplicate'
    acceptEnabled: name.text.trim().length > 0 && profileController.names.indexOf(name.text.trim()) < 0
    onOpened: { name.text = ''; name.forceActiveFocus() }
    onAccepted: { if (operation === 'create') profileController.create(name.text); else profileController.duplicate(name.text) }
    ColumnLayout {
        width: parent.width
        Text { text: 'Starts as a copy of the current saved settings.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        TextField {
            id: name; placeholderText: 'Unique profile name'; Layout.fillWidth: true
            implicitHeight: 42; leftPadding: 12; color: root.colors.text
            placeholderTextColor: root.colors.mutedText; selectionColor: root.colors.accent
            background: Rectangle { radius: 9; color: root.colors.window; border.color: name.activeFocus ? root.colors.accent : root.colors.border }
            Accessible.name: 'Profile name'
            onAccepted: if (root.acceptEnabled) root.accept()
        }
        Text { text: name.text.trim().length && profileController.names.indexOf(name.text.trim()) >= 0 ? 'That name already exists.' : ''; color: root.colors.accent }
    }
}
