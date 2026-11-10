import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "theme"
import "components"
import "pages"
ApplicationWindow {
    id: window
    width: Math.max(360, Math.min(1600, Screen.desktopAvailableWidth - 48)); height: Math.max(480, Math.min(1000, Screen.desktopAvailableHeight - 48))
    minimumWidth: 360; minimumHeight: 480
    flags: Qt.Window | Qt.FramelessWindowHint
    visible: false
    onClosing: close => { close.accepted = false; appController.command('hide') }
    title: 'ROG Control' + (appController.demoMode ? ' · Demo' : '')
    color: "transparent"
    readonly property int cornerRadius: visibility === Window.Maximized || visibility === Window.FullScreen ? 0 : 14
    background: Rectangle {
        color: theme.window; radius: window.cornerRadius
        SurfaceBackdrop { anchors.fill: parent; colors: theme }
        ReferenceArtwork {
            visible: !theme.light && window.width >= 980
            anchors.right: parent.right; y: 48
            width: Math.min(836, parent.width * .6); height: 106
            sourceClipRect: Qt.rect(750, 48, 836, 106)
            Rectangle {
                anchors.left: parent.left; width: 100; height: parent.height
                gradient: Gradient { orientation: Gradient.Horizontal; GradientStop { position: 0; color: theme.window } GradientStop { position: 1; color: 'transparent' } }
            }
            Rectangle {
                anchors.bottom: parent.bottom; width: parent.width; height: 20
                gradient: Gradient { GradientStop { position: 0; color: 'transparent' } GradientStop { position: 1; color: theme.window } }
            }
        }
    }
    Palette { id: theme; appearance: appController.appearance }
    property bool narrow: width < 620
    property var tuningPage: appController.currentPage === 'cpu' ? cpuController : appController.currentPage === 'gpu' ? gpuController : appController.currentPage === 'fans' ? fansController : null
    property var pages: [{id:'quick_access',title:'Home',icon:'⌂'}, {id:'cpu',title:'CPU',icon:'▣'}, {id:'gpu',title:'GPU',icon:'▱'}, {id:'fans',title:'Fans',icon:'✣'}, {id:'keyboard',title:'Keyboard',icon:'⌨'}, {id:'system',title:'System',icon:'⚙'}]
    header: Rectangle {
        height: 48; color: theme.sidebar; radius: window.cornerRadius
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: parent.height / 2; color: theme.sidebar }
        ReferenceArtwork {
            visible: !theme.light
            anchors.fill: parent
            sourceClipRect: Qt.rect(250, 0, 1080, 48)
        }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.border; opacity: .5 }
        MouseArea {
            anchors.fill: parent
            onPressed: window.startSystemMove()
            onDoubleClicked: window.visibility === Window.Maximized ? window.showNormal() : window.showMaximized()
        }
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 22; anchors.rightMargin: 8
            ReferenceArtwork {
                Layout.preferredWidth: window.narrow ? 56 : 224
                Layout.preferredHeight: 36
                sourceClipRect: window.narrow ? Qt.rect(22, 8, 56, 36) : Qt.rect(22, 8, 224, 36)
            }
            Item { Layout.fillWidth: true }
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
            WindowControl {
                colors: theme; glyph: 'theme'; appearance: appController.appearance
                text: 'Theme: ' + (appearance === 'system' ? 'System' : appearance === 'light' ? 'Light' : 'Dark') + ' · click to change'
                Accessible.name: text
                onClicked: systemController.setAppearance(appearance === 'system' ? 'light' : appearance === 'light' ? 'dark' : 'system')
            }
            BatteryIndicator { colors: theme }
            WindowControl { colors: theme; glyph: 'minimize'; text: 'Minimize'; Accessible.name: 'Minimize window'; onClicked: window.showMinimized() }
            WindowControl { colors: theme; glyph: window.visibility === Window.Maximized ? 'restore' : 'maximize'; text: window.visibility === Window.Maximized ? 'Restore' : 'Maximize'; Accessible.name: 'Maximize or restore window'; onClicked: window.visibility === Window.Maximized ? window.showNormal() : window.showMaximized() }
            WindowControl { colors: theme; glyph: 'close'; text: 'Hide'; Accessible.name: 'Hide window'; onClicked: window.close() }
        }
    }
    footer: ProfileBar { cornerRadius: window.cornerRadius; colors: theme; height: implicitHeight }
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
            Layout.fillHeight: true; Layout.preferredWidth: window.width >= 980 ? 164 : 82; color: 'transparent'; visible: !window.narrow
            radius: window.cornerRadius
            ReferenceArtwork {
                visible: !theme.light
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                height: Math.max(0, parent.height - 325)
                sourceClipRect: Qt.rect(0, 373, 162, 521)
                Rectangle {
                    anchors.top: parent.top; width: parent.width; height: 35
                    gradient: Gradient { GradientStop { position: 0; color: theme.window } GradientStop { position: 1; color: 'transparent' } }
                }
            }
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.border; opacity: .5 }
            Column { anchors.top: parent.top; anchors.topMargin: 22; anchors.horizontalCenter: parent.horizontalCenter; spacing: 5
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
                Layout.topMargin: 22; Layout.bottomMargin: 16
                spacing: 7
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    SlashAccent { tint: theme.accent; doubleSlash: true; Layout.rightMargin: 8 }
                    Text { text: appController.pageTitle === 'Home' ? 'ROG CONTROL' : appController.pageTitle; color: theme.text; font.pixelSize: window.narrow ? 21 : 26; font.bold: true; font.italic: true; font.letterSpacing: 1; Layout.fillWidth: true }
                    RogButton { colors: theme; visible: appController.currentPage === 'fans'; text: window.narrow ? 'Calibrate' : 'Calibrate fans'; implicitWidth: window.narrow ? 78 : 120; enabled: !appController.busy && fansController.channels.length > 0 && (appController.demoMode || !!fansController.live.calibration_available); onClicked: fansController.calibrate() }
                    RogButton { colors: theme; visible: window.tuningPage !== null; text: 'Revert'; implicitWidth: window.narrow ? 62 : 90; enabled: window.tuningPage !== null && window.tuningPage.dirty && !appController.busy; onClicked: window.tuningPage.revert() }
                    RogButton { colors: theme; visible: window.tuningPage !== null; text: 'Apply'; implicitWidth: window.narrow ? 62 : 90; primary: true; enabled: window.tuningPage !== null && window.tuningPage.ready && !appController.busy; onClicked: window.tuningPage.apply() }
                }
                Text { visible: appController.currentPage !== 'keyboard'; text: (overviewController.data.cpu_name || 'Detecting processor…') + ' / ' + (overviewController.data.gpu_name || 'Detecting graphics…'); color: theme.mutedText; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true }

            }
            ScrollView {
                id: scroll
                objectName: 'pageViewport'
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: scroll.availableWidth
                    spacing: 14

                    Loader {
                        Layout.fillWidth: true; Layout.leftMargin: window.narrow ? 12 : 22; Layout.rightMargin: window.narrow ? 12 : 22
                        sourceComponent: appController.currentPage === 'quick_access' ? home : appController.currentPage === 'cpu' ? cpu : appController.currentPage === 'gpu' ? gpu : appController.currentPage === 'fans' ? fans : appController.currentPage === 'keyboard' ? keyboard : system
                    }
                    Item { Layout.preferredHeight: 14 }
                }
            }
        }
    }
    Component { id: home; QuickAccessPage { colors: theme } }
    Component { id: cpu; CpuPage { colors: theme } }
    Component { id: gpu; GpuPage { colors: theme } }
    Component { id: fans; FansPage { colors: theme } }
    Component { id: keyboard; KeyboardPage { colors: theme } }
    Component { id: system; SystemPage { colors: theme } }
    Rectangle {
        id: notification
        objectName: 'statusNotification'
        z: 20
        anchors.top: parent.top; anchors.right: parent.right
        anchors.margins: 16
        width: Math.min(420, parent.width - 32)
        height: notificationContent.implicitHeight + 24
        radius: 10; color: theme.panel; border.color: theme.border
        property string dismissedMessage: ''
        readonly property string message: {
            let messages = [appController.statusText, profileController.status]
            const page = appController.currentPage
            if (window.tuningPage !== null) messages.push(window.tuningPage.status)
            if (page === 'keyboard') messages.push(keyboardController.status)
            if (page === 'system') messages.push(systemController.status)
            if (page === 'quick_access') messages.push(quickAccessController.status, gpuController.status, fansController.status)
            return messages.filter((value, index, all) => value.length > 0 && all.indexOf(value) === index).join('\n')
        }
        onMessageChanged: {
            dismissedMessage = ''
            if (message.length > 0) notificationExpiry.restart()
            else notificationExpiry.stop()
        }
        Timer {
            id: notificationExpiry
            interval: 2000; repeat: false
            onTriggered: notification.dismissedMessage = notification.message
        }
        visible: message.length > 0 && message !== dismissedMessage
        RowLayout {
            id: notificationContent
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.margins: 12; spacing: 12
            Rectangle { width: 3; Layout.fillHeight: true; radius: 1.5; color: theme.accent }
            Text {
                objectName: 'notificationMessage'
                Layout.fillWidth: true; text: notification.message
                color: theme.text; font.pixelSize: 12; wrapMode: Text.WordWrap
                Accessible.role: Accessible.StaticText
            }
            RogButton {
                colors: theme; text: '×'; implicitWidth: 30; implicitHeight: 30
                Accessible.name: 'Dismiss notification'
                onClicked: notification.dismissedMessage = notification.message
            }
        }
    }
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
    RogDialog {
        id: confirmation; property var controller; property string action; property string message
        colors: theme
        title: message.indexOf('diagnostic log') >= 0 ? 'Clear diagnostic log?' : 'Confirm operation'
        acceptText: message.indexOf('diagnostic log') >= 0 ? 'Clear log' : 'Confirm'
        Text { width: parent.width; text: confirmation.message; wrapMode: Text.WordWrap; color: theme.mutedText; font.pixelSize: 14; lineHeight: 1.25 }
        onAccepted: controller.confirm(action, true)
        onRejected: controller.confirm(action, false)
    }
    Connections { target: appController; function onErrorOccurred(message) { errorDialog.text = message; errorDialog.open() } }
    RogDialog { id: errorDialog; colors: theme; property string text; title: 'ROG Control'; acceptText: 'Close'; showCancel: false; Text { width: parent.width; text: errorDialog.text; wrapMode: Text.WordWrap; color: theme.mutedText; font.pixelSize: 14; lineHeight: 1.25 } }
}
