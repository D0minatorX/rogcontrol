import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "theme"
import "components"
import "pages"
ApplicationWindow {
    id: window
    width: 1040; height: 860
    minimumWidth: 360; minimumHeight: 480
    visible: false
    onClosing: close => { close.accepted = false; appController.command('quit') }
    title: 'ROG Control' + (appController.demoMode ? ' · Demo' : '')
    color: theme.window
    Palette { id: theme; appearance: appController.appearance }
    property bool narrow: width < 620
    property var tuningPage: appController.currentPage === 'cpu' ? cpuController : appController.currentPage === 'gpu' ? gpuController : appController.currentPage === 'fans' ? fansController : null
    property var pages: [{id:'quick_access',title:'Home',icon:'⌂'}, {id:'cpu',title:'CPU',icon:'▣'}, {id:'gpu',title:'GPU',icon:'▱'}, {id:'fans',title:'Fans',icon:'✣'}, {id:'battery',title:'Battery',icon:'▰'}, {id:'keyboard',title:'Lighting',icon:'⌨'}, {id:'system',title:'System',icon:'⚙'}]
    header: Rectangle {
        height: 48; color: theme.window
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 22; anchors.rightMargin: 22
            Text { text: 'ROG'; color: theme.accent; font.pixelSize: 14; font.letterSpacing: 2 }
            Text { text: 'CONTROL'; color: theme.text; font.pixelSize: 14; font.letterSpacing: 2; Layout.fillWidth: true }
            Text { text: appController.activeProfile; visible: !window.narrow; color: theme.mutedText; font.pixelSize: 12; Layout.maximumWidth: Math.min(220, window.width * .25); elide: Text.ElideRight; Accessible.name: 'Active profile: ' + text }
            RogButton {
                id: profileMenuButton
                colors: theme; text: '☰'; implicitWidth: 38; implicitHeight: 34
                Accessible.name: 'Profile actions'
                onClicked: profileMenu.open()
                Menu {
                    id: profileMenu
                    x: profileMenuButton.width - width; y: profileMenuButton.height + 8
                    width: 232; padding: 6
                    background: Rectangle { radius: 8; color: theme.panel; border.color: theme.border }
                    delegate: MenuItem {
                        id: menuEntry
                        implicitHeight: 40
                        contentItem: Text { text: menuEntry.text; color: menuEntry.enabled ? theme.text : theme.mutedText; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { radius: 4; color: menuEntry.highlighted ? theme.hover : 'transparent' }
                    }
                    Action { text: 'New profile…'; enabled: !appController.busy; onTriggered: { profileDialog.operation = 'create'; profileDialog.open() } }
                    Action { text: 'Duplicate profile…'; enabled: !appController.busy; onTriggered: { profileDialog.operation = 'duplicate'; profileDialog.open() } }
                    Action { text: 'Delete profile…'; enabled: !appController.busy && appController.profileNames.length > 1; onTriggered: profileController.delete(appController.activeProfile) }
                    MenuSeparator { }
                    Action { text: 'Import…'; enabled: !appController.busy; onTriggered: profileController.chooseImport() }
                    Action { text: 'Export backup…'; enabled: !appController.busy; onTriggered: profileController.chooseExport() }
                }
            }
            Text { text: appController.demoMode ? (appController.liveReadings ? (window.narrow ? 'LIVE / DEMO' : 'LIVE READINGS / DEMO CONTROLS') : 'DEMO / SAMPLE READINGS') : 'LIVE READINGS'; color: theme.mutedText; font.pixelSize: 10 }
        }
        Rectangle { height: 1; width: parent.width; anchors.bottom: parent.bottom; color: theme.border }
    }
    RowLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            Layout.fillHeight: true; Layout.preferredWidth: 66; color: theme.sidebar; visible: !window.narrow
            Column { anchors.top: parent.top; anchors.topMargin: 16; anchors.horizontalCenter: parent.horizontalCenter; spacing: 5
                Repeater { model: window.pages; NavigationItem { required property var modelData; text: modelData.title; symbol: modelData.icon; selected: appController.currentPage === modelData.id; colors: theme; onClicked: appController.navigate(modelData.id) } }
            }
            Rectangle { width: 1; height: parent.height; anchors.right: parent.right; color: theme.border }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
            ScrollView {
                visible: window.narrow; Layout.fillWidth: true; Layout.preferredHeight: 62; contentWidth: compactNav.implicitWidth; clip: true
                Row { id: compactNav; spacing: 0; Repeater { model: window.pages; NavigationItem { required property var modelData; text: modelData.title; symbol: modelData.icon; selected: appController.currentPage === modelData.id; colors: theme; onClicked: appController.navigate(modelData.id) } } }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: window.narrow ? 12 : 22; Layout.rightMargin: window.narrow ? 12 : 22
                Layout.topMargin: 20; Layout.bottomMargin: 18
                spacing: 7
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Text { text: appController.pageTitle === 'Home' ? 'ROG CONTROL' : appController.pageTitle.toUpperCase(); color: theme.text; font.pixelSize: window.narrow ? 21 : 25; font.letterSpacing: 1; Layout.fillWidth: true }
                    RogButton { colors: theme; visible: appController.currentPage === 'fans'; text: window.narrow ? 'Calibrate' : 'Calibrate fans'; implicitWidth: window.narrow ? 78 : 120; enabled: !appController.busy && fansController.channels.length > 0 && (appController.demoMode || !!fansController.live.calibration_available); onClicked: fansController.calibrate() }
                    RogButton { colors: theme; visible: window.tuningPage !== null; text: 'Revert'; implicitWidth: window.narrow ? 62 : 90; enabled: window.tuningPage !== null && window.tuningPage.dirty && !appController.busy; onClicked: window.tuningPage.revert() }
                    RogButton { colors: theme; visible: window.tuningPage !== null; text: 'Apply'; implicitWidth: window.narrow ? 62 : 90; primary: true; enabled: window.tuningPage !== null && window.tuningPage.ready && !appController.busy; onClicked: window.tuningPage.apply() }
                }
                Text { text: (overviewController.data.cpu_name || 'Detecting processor…') + ' / ' + (overviewController.data.gpu_name || 'Detecting graphics…'); color: theme.mutedText; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true }
            }
            ScrollView {
                id: scroll
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: scroll.availableWidth
                    spacing: 14
                    Text {
                        text: profileController.status; visible: text.length > 0
                        color: theme.mutedText; Layout.fillWidth: true; Layout.leftMargin: 22; Layout.rightMargin: 22
                        wrapMode: Text.WordWrap; font.pixelSize: 11
                    }
                    Loader {
                        Layout.fillWidth: true; Layout.leftMargin: window.narrow ? 12 : 22; Layout.rightMargin: window.narrow ? 12 : 22
                        sourceComponent: appController.currentPage === 'quick_access' ? home : appController.currentPage === 'cpu' ? cpu : appController.currentPage === 'gpu' ? gpu : appController.currentPage === 'fans' ? fans : appController.currentPage === 'battery' ? battery : appController.currentPage === 'keyboard' ? keyboard : system
                    }
                    Text { text: 'ROG CONTROL / LINUX'; color: theme.mutedText; font.pixelSize: 10; Layout.leftMargin: 22 }
                    Item { Layout.preferredHeight: 14 }
                }
            }
            Rectangle {
                visible: status.text.length > 0; Layout.fillWidth: true; Layout.preferredHeight: status.implicitHeight + 20; color: theme.selected
                Text { id: status; anchors.fill: parent; anchors.margins: 10; color: theme.text; font.pixelSize: 11; wrapMode: Text.WordWrap; text: appController.statusText }
            }
            ProfileBar { colors: theme; Layout.fillWidth: true; Layout.preferredHeight: implicitHeight }
        }
    }
    Component { id: home; QuickAccessPage { colors: theme } }
    Component { id: cpu; CpuPage { colors: theme } }
    Component { id: gpu; GpuPage { colors: theme } }
    Component { id: fans; FansPage { colors: theme } }
    Component { id: battery; BatteryPage { colors: theme } }
    Component { id: keyboard; KeyboardPage { colors: theme } }
    Component { id: system; SystemPage { colors: theme } }
    ProfileDialog { id: profileDialog; colors: theme }
    function ask(controller, action, message) {
        confirmation.controller = controller; confirmation.action = action; confirmation.message = message; confirmation.open()
    }
    Connections { target: profileController; function onConfirmationRequested(action, message) { window.ask(profileController, action, message) } }
    Connections { target: cpuController; function onConfirmationRequested(action, message) { window.ask(cpuController, action, message) } }
    Connections { target: gpuController; function onConfirmationRequested(action, message) { window.ask(gpuController, action, message) } }
    Connections { target: fansController; function onConfirmationRequested(action, message) { window.ask(fansController, action, message) } }
    Connections { target: systemController; function onConfirmationRequested(action, message) { window.ask(systemController, action, message) } }
    Connections { target: updatesController; function onConfirmationRequested(action, message) { window.ask(updatesController, action, message) } }
    Dialog {
        id: confirmation; property var controller; property string action; property string message
        title: 'Confirm operation'; anchors.centerIn: parent; width: Math.min(window.width - 40, 460); modal: true; standardButtons: Dialog.Yes | Dialog.No
        Text { width: parent.width; text: confirmation.message; wrapMode: Text.WordWrap; color: theme.text }
        onAccepted: controller.confirm(action, true)
        onRejected: controller.confirm(action, false)
    }
    Connections { target: appController; function onErrorOccurred(message) { errorDialog.text = message; errorDialog.open() } }
    Dialog { id: errorDialog; property string text; title: 'ROG Control'; anchors.centerIn: parent; width: Math.min(window.width-40, 450); standardButtons: Dialog.Ok; modal: true; Text { width: parent.width; text: errorDialog.text; wrapMode: Text.WordWrap; color: theme.text } }
}
