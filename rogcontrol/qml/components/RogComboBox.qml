import QtQuick
import QtQuick.Controls
ComboBox {
    id: control
    property var colors
    implicitHeight: 38
    implicitWidth: 230
    leftPadding: 13
    rightPadding: 34
    hoverEnabled: true
    opacity: enabled ? 1 : .45
    contentItem: Text {
        text: control.displayText || 'Select…'
        color: control.colors.text
        font.pixelSize: 12
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Text {
        x: control.width - width - 13; anchors.verticalCenter: parent.verticalCenter
        text: control.popup.visible ? '⌃' : '⌄'; font.pixelSize: 19; color: control.colors.mutedText
    }
    background: Rectangle {
        radius: 10
        color: control.hovered ? control.colors.hover : control.colors.sidebar
        border.color: control.activeFocus || control.popup.visible ? control.colors.accent : 'transparent'
    }
    delegate: ItemDelegate {
        id: entry
        required property int index
        required property var modelData
        width: control.width - 10; height: 40
        highlighted: control.highlightedIndex === index
        contentItem: Text {
            text: (control.currentIndex === entry.index ? '✓  ' : '    ') + String(entry.modelData)
            color: control.currentIndex === entry.index ? control.colors.accent : control.colors.text
            font.pixelSize: 12; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
        }
        background: Rectangle { radius: 7; color: entry.highlighted ? control.colors.hover : 'transparent' }
    }
    popup: Popup {
        y: control.height + 6; width: control.width
        padding: 5; margins: 8
        implicitHeight: Math.min(contentItem.implicitHeight + 10, 280)
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator { }
        }
        background: Rectangle { radius: 12; color: control.colors.panel; border.color: control.colors.border }
    }
}
