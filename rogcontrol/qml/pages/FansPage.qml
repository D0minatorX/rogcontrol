import QtQuick
import QtQuick.Layouts
import '../components'
ColumnLayout {
    id: root
    property var colors
    spacing: 14
    RowLayout {
        Layout.fillWidth: true
        Text { text: 'Curve speed units'; color: root.colors.text; Layout.fillWidth: true }
        RogComboBox { colors: root.colors; model: ['percent', 'rpm']; currentIndex: model.indexOf(fansController.displayUnit); onActivated: fansController.setDisplayUnit(currentText); Accessible.name: 'Fan curve speed units'; palette.button: root.colors.panel; palette.buttonText: root.colors.text }
    }
    Text { visible: fansController.ready && fansController.channels.length === 0; text: 'No custom fan curve interface was detected.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    Repeater {
        model: fansController.channels.length
        MetricCard {
            required property int index
            property var modelData: fansController.channels[index]
            Layout.fillWidth: true; colors: root.colors; title: modelData.title + ' / ' + (modelData.rpm === null || modelData.rpm === undefined ? 'RPM unavailable' : modelData.rpm + ' RPM')
            CurveEditor {
                id: editor
                Layout.fillWidth: true; colors: root.colors; points: modelData.points
                calibration: modelData.calibration; displayUnit: fansController.displayUnit
                enabled: !appController.busy
                onPointMoved: (index, temperature, percent) => fansController.movePoint(modelData.key, index, temperature, percent)
                onPointAdded: (temperature, percent) => {
                    var added = fansController.addPoint(modelData.key, temperature, percent)
                    if (added >= 0) editor.selectedPoint = added
                }
                onPointRemoved: index => fansController.removePoint(modelData.key, index)
            }
        }
    }
    Text { text: (fansController.live.mismatch || []).length ? 'Driver curve mismatch or disabled channel: ' + fansController.live.mismatch.join(', ') + '. Apply to restore curves.' : ''; visible: text.length > 0; color: root.colors.accent; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    MetricCard {
        Layout.fillWidth: true; colors: root.colors; title: 'Fan calibration'
        Text { text: 'RPM estimates use this machine’s measured calibration when available. Calibration measures 20%, 45%, 70% and 100% duty.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
        RogButton { colors: root.colors; text: 'Calibrate fans'; enabled: !appController.busy && fansController.channels.length > 0 && (appController.demoMode || !!fansController.live.calibration_available); onClicked: fansController.calibrate() }

    }
    Text { Layout.fillWidth: true; text: fansController.status; visible: text.length > 0; color: root.colors.mutedText; wrapMode: Text.WordWrap; font.pixelSize: 12 }
}
