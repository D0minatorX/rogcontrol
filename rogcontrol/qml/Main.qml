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
    flags: Qt.Window | Qt.FramelessWindowHint
    visible: false
    onClosing: close => { close.accepted = false; appController.command('quit') }
    title: 'ROG Control' + (appController.demoMode ? ' · Demo' : '')
    color: "transparent"
    readonly property int cornerRadius: visibility === Window.Maximized || visibility === Window.FullScreen ? 0 : 14
    background: Rectangle { color: theme.window; radius: window.cornerRadius }
    Palette { id: theme; appearance: appController.appearance }
    property bool narrow: width < 620
    property var tuningPage: appController.currentPage === 'cpu' ? cpuController : appController.currentPage === 'gpu' ? gpuController : appController.currentPage === 'fans' ? fansController : null
    property var pages: [{id:'quick_access',title:'Home',icon:'⌂'}, {id:'cpu',title:'CPU',icon:'▣'}, {id:'gpu',title:'GPU',icon:'▱'}, {id:'fans',title:'Fans',icon:'✣'}, {id:'keyboard',title:'Keyboard',icon:'⌨'}, {id:'system',title:'System',icon:'⚙'}]
    header: Rectangle {
        height: 52; color: theme.sidebar; radius: window.cornerRadius
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: parent.height / 2; color: theme.sidebar }
        MouseArea {
            anchors.fill: parent
            onPressed: window.startSystemMove()
            onDoubleClicked: window.visibility === Window.Maximized ? window.showNormal() : window.showMaximized()
        }
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 22; anchors.rightMargin: 8
            Text { text: 'ROG'; color: theme.accent; font.pixelSize: 14; font.letterSpacing: 2 }
            Text { text: 'CONTROL'; color: theme.text; font.pixelSize: 14; font.letterSpacing: 2; Layout.fillWidth: true }
            WindowControl {
                id: profileMenuButton
                colors: theme; glyph: 'menu'; text: 'Profile actions'
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
            WindowControl { colors: theme; glyph: 'minimize'; text: 'Minimize'; Accessible.name: 'Minimize window'; onClicked: window.showMinimized() }
            WindowControl { colors: theme; glyph: window.visibility === Window.Maximized ? 'restore' : 'maximize'; text: window.visibility === Window.Maximized ? 'Restore' : 'Maximize'; Accessible.name: 'Maximize or restore window'; onClicked: window.visibility === Window.Maximized ? window.showNormal() : window.showMaximized() }
            WindowControl { colors: theme; glyph: 'close'; text: 'Close'; Accessible.name: 'Close window'; onClicked: window.close() }
        }
    }
    Repeater {
        model: [Qt.TopEdge, Qt.BottomEdge, Qt.LeftEdge, Qt.RightEdge,
                Qt.TopEdge | Qt.LeftEdge, Qt.TopEdge | Qt.RightEdge,
                Qt.BottomEdge | Qt.LeftEdge, Qt.BottomEdge | Qt.RightEdge]
        MouseArea {
            required property int modelData
            parent: window.contentItem
            z: 100
            visible: window.visibility !== Window.Maximized && window.visibility !== Window.FullScreen
            property bool horizontalEdge: !!(modelData & (Qt.LeftEdge | Qt.RightEdge))
            property bool verticalEdge: !!(modelData & (Qt.TopEdge | Qt.BottomEdge))
            width: horizontalEdge ? 6 : parent.width - 12
            height: verticalEdge ? 6 : parent.height + window.header.height - 12
            x: modelData & Qt.RightEdge ? parent.width - width : horizontalEdge ? 0 : 6
            y: modelData & Qt.BottomEdge ? parent.height - height : -window.header.height + (verticalEdge ? 0 : 6)
            cursorShape: horizontalEdge && verticalEdge ? ((modelData === (Qt.TopEdge | Qt.LeftEdge) || modelData === (Qt.BottomEdge | Qt.RightEdge)) ? Qt.SizeFDiagCursor : Qt.SizeBDiagCursor) : horizontalEdge ? Qt.SizeHorCursor : Qt.SizeVerCursor
            onPressed: window.startSystemResize(modelData)
        }
    }
    RowLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            Layout.fillHeight: true; Layout.preferredWidth: window.width >= 980 ? 164 : 82; color: theme.sidebar; visible: !window.narrow
            radius: window.cornerRadius
            Rectangle { anchors.top: parent.top; width: parent.width; height: parent.height / 2; color: theme.sidebar }
            Rectangle { anchors.right: parent.right; width: parent.width / 2; height: parent.height; color: theme.sidebar }
            Column { anchors.top: parent.top; anchors.topMargin: 12; anchors.horizontalCenter: parent.horizontalCenter; spacing: 5
                Repeater { model: window.pages; NavigationItem { expanded: window.width >= 980; required property var modelData; text: modelData.title; symbol: modelData.id; selected: appController.currentPage === modelData.id; colors: theme; onClicked: appController.navigate(modelData.id) } }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
            ScrollView {
                visible: window.narrow; Layout.fillWidth: true; Layout.preferredHeight: 62; contentWidth: compactNav.implicitWidth; clip: true
                Row { id: compactNav; spacing: 0; Repeater { model: window.pages; NavigationItem { required property var modelData; text: modelData.title; symbol: modelData.id; selected: appController.currentPage === modelData.id; colors: theme; onClicked: appController.navigate(modelData.id) } } }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: window.narrow ? 12 : 22; Layout.rightMargin: window.narrow ? 12 : 22
                Layout.topMargin: 22; Layout.bottomMargin: 20
                spacing: 7
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Text { text: appController.pageTitle === 'Home' ? 'ROG CONTROL' : appController.pageTitle.toUpperCase(); color: theme.text; font.pixelSize: window.narrow ? 21 : 24; font.letterSpacing: 1; Layout.fillWidth: true }
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
                        sourceComponent: appController.currentPage === 'quick_access' ? home : appController.currentPage === 'cpu' ? cpu : appController.currentPage === 'gpu' ? gpu : appController.currentPage === 'fans' ? fans : appController.currentPage === 'keyboard' ? keyboard : system
                    }
                    Item { Layout.preferredHeight: 14 }
                }
            }
            Rectangle {
                visible: status.text.length > 0; Layout.fillWidth: true; Layout.preferredHeight: status.implicitHeight + 20; color: theme.selected
                Text { id: status; anchors.fill: parent; anchors.margins: 10; color: theme.text; font.pixelSize: 11; wrapMode: Text.WordWrap; text: appController.statusText }
            }
            ProfileBar { cornerRadius: window.cornerRadius; colors: theme; Layout.fillWidth: true; Layout.preferredHeight: implicitHeight }
        }
    }
    Component { id: home; QuickAccessPage { colors: theme } }
    Component { id: cpu; CpuPage { colors: theme } }
    Component { id: gpu; GpuPage { colors: theme } }
    Component { id: fans; FansPage { colors: theme } }
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
