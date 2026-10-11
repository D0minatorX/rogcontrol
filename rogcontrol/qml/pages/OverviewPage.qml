import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
ColumnLayout {
    id: root
    property var colors
    property var sample: overviewController.data
    function format(value, unit) { return value === undefined || value === null ? "—" : Number(value).toLocaleString(Qt.locale(), 'f', 0) + " " + unit }
    function memory(value) { return !value || value[0] === null || value[1] === null ? '—' : (value[0]/1024).toFixed(1) + ' / ' + (value[1]/1024).toFixed(1) + ' GiB' }
    spacing: 14
    Text {
        visible: overviewController.sampleError.length > 0
        text: overviewController.sampleError
        color: root.colors.accent
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    SettingsChoice { visible: appController.currentPage === 'overview'; colors: root.colors; title: 'Readings layout'; choices: ['list','dashboard']; labels: ['List','Dashboard']; value: overviewController.layout; enabled: !appController.busy; Layout.fillWidth: true; onEdited: value => appController.setOverviewLayout(value) }
    MetricCard {
        visible: appController.currentPage === 'overview' && overviewController.layout === 'list'
        colors: root.colors; title: 'Live readings'; Layout.fillWidth: true
        Repeater {
            model: [{key:'cpu_temp', label:'CPU temperature', unit:'°C'}, {key:'cpu_clock', label:'CPU clock', unit:'MHz'}, {key:'cpu_power', label:'CPU power', unit:'W'}, {key:'gpu_temp', label:'GPU temperature', unit:'°C'}, {key:'gpu_clock', label:'GPU clock', unit:'MHz'}, {key:'gpu_power', label:'GPU power', unit:'W'}]
            StatusRow { required property var modelData; colors: root.colors; label: modelData.label; value: root.format(root.sample[modelData.key], modelData.unit) }
        }
        StatusRow { colors: root.colors; label: 'RAM'; value: root.memory(root.sample.ram) }
        StatusRow { colors: root.colors; label: 'VRAM'; value: root.memory(root.sample.vram) }
        Repeater { model: ['1','2','3']; StatusRow { required property string modelData; colors: root.colors; label: 'Fan ' + modelData; value: root.format((root.sample.fan_rpm || {})[modelData], 'RPM') } }
        StatusRow { colors: root.colors; label: 'Battery'; value: root.sample.battery ? root.format(root.sample.battery[0], '%') : 'Unavailable' }
    }
    GridLayout {
        visible: appController.currentPage !== 'overview' || overviewController.layout === 'dashboard'
        columns: root.width < 640 ? 1 : 2
        columnSpacing: 14; rowSpacing: 14; Layout.fillWidth: true
        DeviceCard { device: 'cpu'; readings: root.sample; colors: root.colors; Layout.fillWidth: true; Layout.preferredWidth: 1 }
        DeviceCard { device: 'gpu'; readings: root.sample; colors: root.colors; Layout.fillWidth: true; Layout.preferredWidth: 1 }
    }
    MetricCard {
        colors: root.colors; title: 'Operating profile'; Layout.fillWidth: true
        GridLayout {
            columns: root.width < 540 ? 2 : 4
            Layout.fillWidth: true
            columnSpacing: 10; rowSpacing: 10
            Repeater {
                model: appController.profileNames
                Button {
                    id: profile
                    required property string modelData
                    property bool selected: modelData === appController.activeProfile
                    Layout.fillWidth: true
                    Layout.preferredHeight: 88
                    Layout.preferredWidth: 1
                    enabled: !appController.busy
                    onClicked: appController.selectProfile(modelData)
                    Accessible.name: modelData + (selected ? ', selected' : '')
                    contentItem: Column {
                        spacing: 12
                        Text { text: profile.modelData.toLowerCase().includes('quiet') ? '◌' : profile.modelData.toLowerCase().includes('performance') ? 'ϟ' : '◇'; color: root.colors.text; font.pixelSize: 25; anchors.horizontalCenter: parent.horizontalCenter }
                        Text { text: profile.modelData; color: root.colors.text; font.pixelSize: 11; width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
                    }
                    background: Rectangle { color: profile.selected ? root.colors.selected : profile.hovered ? root.colors.hover : root.colors.panel; border.color: profile.selected ? root.colors.accent : root.colors.border; Rectangle { visible: profile.selected; height: 3; width: parent.width*.6; anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; color: root.colors.accent } }
                }
            }
        }

    }
}
