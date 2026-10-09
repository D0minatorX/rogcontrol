import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ColumnLayout {
    id: root
    property var colors
    property string title
    property string unit: ''
    property real minimum: 0
    property real maximum: 100
    property real step: 1
    property real value: 0
    property string inputError: ''
    signal edited(real value)
    spacing: 2
    function formatted(v) { return step < 1 ? Number(v).toFixed(2).replace(/0+$/, '').replace(/\.$/, '') : Math.round(v).toString() }
    function acceptInput() {
        var v = Number(entry.text)
        if (entry.text.trim().length === 0 || !isFinite(v) || v < minimum || v > maximum) {
            inputError = 'Enter ' + minimum + '–' + maximum
            entry.text = Qt.binding(function() { return root.formatted(root.value) })
        } else { inputError = ''; edited(v) }
    }
    RowLayout {
        Layout.fillWidth: true
        Text { text: root.title; color: root.colors.text; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        TextField {
            id: entry
            Layout.preferredWidth: 78
            text: root.formatted(root.value)
            color: root.colors.text
            horizontalAlignment: Text.AlignRight
            selectByMouse: true
            Accessible.name: root.title + ' ' + root.unit
            background: Rectangle { color: root.colors.window; border.color: entry.activeFocus ? root.colors.accent : root.colors.border; radius: 3 }
            onEditingFinished: root.acceptInput()
            Keys.onEscapePressed: { text = Qt.binding(function() { return root.formatted(root.value) }); root.inputError = ''; focus = false }
        }
        Text { text: root.unit; color: root.colors.mutedText; font.pixelSize: 11; Layout.preferredWidth: 32 }
    }
    Slider {
        id: slider
        Layout.fillWidth: true
        from: root.minimum; to: root.maximum; stepSize: root.step; value: root.value
        Accessible.name: root.title
        onMoved: { root.inputError = ''; root.edited(value) }
        background: Rectangle { x: slider.leftPadding; y: slider.topPadding + slider.availableHeight / 2 - 2; width: slider.availableWidth; height: 4; color: root.colors.border; Rectangle { height: parent.height; width: slider.visualPosition * parent.width; color: root.colors.accent } }
        handle: Rectangle { x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width); y: slider.topPadding + slider.availableHeight / 2 - height / 2; width: 12; height: 12; radius: 2; color: root.colors.accent; border.color: slider.activeFocus ? root.colors.text : root.colors.accent }
    }
    Text { visible: root.inputError.length > 0; text: root.inputError; color: root.colors.accent; font.pixelSize: 11 }
}
