import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import '../components'
ColumnLayout {
    id: root
    property var colors
    spacing: 14
    RowLayout {
        Layout.fillWidth: true
        Text { text: 'Curve speed units'; color: root.colors.text; Layout.fillWidth: true }
        ComboBox { model: ['percent', 'rpm']; currentIndex: model.indexOf(fansController.displayUnit); onActivated: fansController.setDisplayUnit(currentText); Accessible.name: 'Fan curve speed units'; palette.button: root.colors.panel; palette.buttonText: root.colors.text }
    }
    Text { visible: fansController.ready && fansController.channels.length === 0; text: 'No custom fan curve interface was detected.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    Repeater {
        model: fansController.channels.length
        MetricCard {
            required property int index
            property var modelData: fansController.channels[index]
            Layout.fillWidth: true; colors: root.colors; title: modelData.title + ' / ' + (modelData.rpm === null || modelData.rpm === undefined ? 'RPM unavailable' : modelData.rpm + ' RPM')
            CurveEditor { Layout.fillWidth: true; colors: root.colors; points: modelData.points; calibration: modelData.calibration; displayUnit: fansController.displayUnit; onPointMoved: (index, temperature, percent) => fansController.movePoint(modelData.key, index, temperature, percent) }
        }
    }
    Text { text: (fansController.live.mismatch || []).length ? 'Driver curve mismatch or disabled channel: ' + fansController.live.mismatch.join(', ') + '. Apply to restore curves.' : ''; visible: text.length > 0; color: root.colors.accent; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    PendingChangesBar { Layout.fillWidth: true; colors: root.colors; pending: fansController.dirty; busy: appController.busy; onApplyRequested: fansController.apply(); onRevertRequested: fansController.revert() }
    MetricCard {
        Layout.fillWidth: true; colors: root.colors; title: 'Calibration and temporary boost'
        Text { text: 'RPM estimates use this machine’s measured calibration when available. Calibration measures 20%, 45%, 70% and 100% duty.'; color: root.colors.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
        RogButton { colors: root.colors; text: 'Calibrate fans'; enabled: !appController.busy && fansController.channels.length > 0 && (appController.demoMode || !!fansController.live.calibration_available); onClicked: fansController.calibrate() }
        SliderRow { id: boostPercent; Layout.fillWidth: true; colors: root.colors; title: 'Temporary fan duty'; unit: '%'; minimum: 20; maximum: 100; value: 85; onEdited: value => boostPercent.value = value }
        SliderRow { id: boostDuration; Layout.fillWidth: true; colors: root.colors; title: 'Boost duration'; unit: 's'; minimum: 10; maximum: 600; step: 10; value: 60; onEdited: value => boostDuration.value = value }
        RowLayout {
            RogButton { colors: root.colors; text: 'Start boost'; enabled: !appController.busy && fansController.channels.length > 0; onClicked: fansController.boost(boostPercent.value, boostDuration.value) }
            RogButton { colors: root.colors; text: 'Stop boost'; enabled: !appController.busy && fansController.live.boost_seconds > 0; onClicked: fansController.stopBoost() }
        }
        Text { text: fansController.live.boost_seconds > 0 ? 'Boost active · ' + fansController.live.boost_seconds + ' seconds remaining' : 'Profile curves active'; color: root.colors.mutedText }
    }
    Text { Layout.fillWidth: true; text: fansController.status; visible: text.length > 0; color: root.colors.mutedText; wrapMode: Text.WordWrap; font.pixelSize: 12 }
}
