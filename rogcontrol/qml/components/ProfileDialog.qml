import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Dialog {
    id: root
    property var colors
    property string operation: 'create'
    anchors.centerIn: parent
    width: Math.min(parent.width - 40, 430)
    title: operation === 'create' ? 'Create profile' : 'Duplicate profile'
    modal: true
    footer: DialogButtonBox {
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: root.accept()
        onRejected: root.reject()
        Component.onCompleted: standardButton(Dialog.Ok).enabled = Qt.binding(function() { return name.text.trim().length > 0 && profileController.names.indexOf(name.text.trim()) < 0 })
    }
    onOpened: { name.text = ''; name.forceActiveFocus() }
    onAccepted: { if (operation === 'create') profileController.create(name.text); else profileController.duplicate(name.text) }
    ColumnLayout {
        width: parent.width
        Text { text: 'Starts as a copy of the current saved settings.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        TextField { id: name; placeholderText: 'Unique profile name'; Layout.fillWidth: true; Accessible.name: 'Profile name'; onAccepted: if (text.trim().length && profileController.names.indexOf(text.trim()) < 0) root.accept() }
        Text { text: name.text.trim().length && profileController.names.indexOf(name.text.trim()) >= 0 ? 'That name already exists.' : ''; color: root.colors.accent }
    }
}
