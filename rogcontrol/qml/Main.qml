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
    title: 'ROG Control' + (appController.demoMode ? ' · Demo' : ' · Qt Preview')
    color: theme.window
    Palette { id: theme; appearance: appController.appearance }
    property bool narrow: width < 620
    property var pages: [{id:'quick_access',title:'Home',icon:'⌂'}, {id:'cpu',title:'CPU',icon:'▣'}, {id:'gpu',title:'GPU',icon:'▱'}, {id:'fans',title:'Fans',icon:'✣'}, {id:'battery',title:'Battery',icon:'▰'}, {id:'keyboard',title:'Lighting',icon:'⌨'}, {id:'system',title:'System',icon:'⚙'}, {id:'overview',title:'Overview',icon:'◉'}]
    header: Rectangle {
        height: 48; color: theme.window
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 22; anchors.rightMargin: 22
            Text { text: 'ROG'; color: theme.accent; font.pixelSize: 14; font.letterSpacing: 2 }
            Text { text: 'CONTROL'; color: theme.text; font.pixelSize: 14; font.letterSpacing: 2; Layout.fillWidth: true }
            Text { text: appController.demoMode ? 'DEMO / SAMPLE READINGS' : 'QT PREVIEW'; color: theme.mutedText; font.pixelSize: 10 }
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
            ScrollView {
                id: scroll
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: scroll.availableWidth
                    spacing: 14
                    Item { Layout.preferredHeight: 8 }
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.leftMargin: window.narrow ? 12 : 22; Layout.rightMargin: window.narrow ? 12 : 22
                        spacing: 6
                        Text { text: 'DEVICE / ' + appController.pageTitle.toUpperCase(); color: theme.mutedText; font.pixelSize: 10; font.letterSpacing: 1.6 }
                        Text { text: appController.pageTitle === 'Home' || appController.pageTitle === 'Overview' ? 'ROG CONTROL' : appController.pageTitle.toUpperCase(); color: theme.text; font.pixelSize: 25; font.letterSpacing: 1 }
                        Text { text: (overviewController.data.cpu_name || 'Detecting processor…') + ' / ' + (overviewController.data.gpu_name || 'Detecting graphics…'); color: theme.mutedText; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    }
                    MetricCard {
                        Layout.fillWidth: true; Layout.leftMargin: window.narrow ? 12 : 22; Layout.rightMargin: window.narrow ? 12 : 22
                        colors: theme; title: 'Operating profile'
                        ComboBox { Layout.fillWidth: true; model: appController.profileNames; currentIndex: appController.profileNames.indexOf(appController.activeProfile); enabled: !appController.busy; onActivated: appController.selectProfile(currentText); Accessible.name: 'Operating profile'; palette.button: theme.panel; palette.buttonText: theme.text }
                        Flow {
                            Layout.fillWidth: true; spacing: 6; implicitHeight: childrenRect.height
                            RogButton { colors: theme; text: 'New'; enabled: !appController.busy; onClicked: { profileDialog.operation = 'create'; profileDialog.open() } }
                            RogButton { colors: theme; text: 'Duplicate'; enabled: !appController.busy; onClicked: { profileDialog.operation = 'duplicate'; profileDialog.open() } }
                            RogButton { colors: theme; text: 'Delete'; enabled: !appController.busy && appController.profileNames.length > 1; onClicked: profileController.delete(appController.activeProfile) }
                            RogButton { colors: theme; text: 'Import'; enabled: !appController.busy; onClicked: profileController.chooseImport() }
                            RogButton { colors: theme; text: 'Export backup'; enabled: !appController.busy; onClicked: profileController.chooseExport() }
                            RogButton { colors: theme; text: 'Apply saved profile'; enabled: !appController.busy; onClicked: profileController.apply() }
                        }
                        Text { visible: appController.pendingChanges; text: 'Tuning changes are pending. Apply or revert them on their pages.'; color: theme.accent; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
                        Text { text: profileController.status; visible: text.length > 0; color: theme.mutedText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 }
                    }
                    Loader {
                        Layout.fillWidth: true; Layout.leftMargin: window.narrow ? 12 : 22; Layout.rightMargin: window.narrow ? 12 : 22
                        sourceComponent: appController.currentPage === 'quick_access' ? home : appController.currentPage === 'overview' ? overview : appController.currentPage === 'cpu' ? cpu : appController.currentPage === 'gpu' ? gpu : appController.currentPage === 'fans' ? fans : pending
                    }
                    Text { text: 'ROG CONTROL / LINUX'; color: theme.mutedText; font.pixelSize: 10; Layout.leftMargin: 22 }
                    Item { Layout.preferredHeight: 14 }
                }
            }
            Rectangle {
                visible: status.text.length > 0; Layout.fillWidth: true; Layout.preferredHeight: status.implicitHeight + 20; color: theme.selected
                Text { id: status; anchors.fill: parent; anchors.margins: 10; color: theme.text; font.pixelSize: 11; wrapMode: Text.WordWrap; text: appController.statusText }
            }
        }
    }
    Component { id: home; QuickAccessPage { colors: theme } }
    Component { id: overview; OverviewPage { colors: theme } }
    Component { id: cpu; CpuPage { colors: theme } }
    Component { id: gpu; GpuPage { colors: theme } }
    Component { id: fans; FansPage { colors: theme } }
    Component { id: pending; MetricCard { colors: theme; title: appController.pageTitle; Text { Layout.fillWidth: true; text: 'This page is being migrated to Qt. Use the existing desktop interface for ' + appController.pageTitle.toLowerCase() + ' controls.'; color: theme.mutedText; wrapMode: Text.WordWrap } } }
    ProfileDialog { id: profileDialog; colors: theme }
    function ask(controller, action, message) {
        confirmation.controller = controller; confirmation.action = action; confirmation.message = message; confirmation.open()
    }
    Connections { target: profileController; function onConfirmationRequested(action, message) { window.ask(profileController, action, message) } }
    Connections { target: cpuController; function onConfirmationRequested(action, message) { window.ask(cpuController, action, message) } }
    Connections { target: gpuController; function onConfirmationRequested(action, message) { window.ask(gpuController, action, message) } }
    Connections { target: fansController; function onConfirmationRequested(action, message) { window.ask(fansController, action, message) } }
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
