import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
ColumnLayout {
    id: root
    property var colors
    property bool gpu: false
    property var sample: overviewController.data
    property bool compact: width < 660
    function format(value, unit) { return value === undefined || value === null ? "—" : Number(value).toLocaleString(Qt.locale(), 'f', 0) + " " + unit }
    function memory(value) { return !value || value[0] === null || value[1] === null ? '—' : (value[0]/1024).toFixed(1) + ' / ' + (value[1]/1024).toFixed(1) + ' GiB' }
    function curveStatus() { var state = sample.curve_enabled || {}; var known = Object.keys(state).filter(function(key) { return state[key] !== null }); return !known.length ? 'Unavailable' : known.every(function(key) { return state[key] }) ? 'Held by EC' : 'Dropped — reapply the profile' }
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
        columns: root.compact ? 1 : 2
        columnSpacing: 14
        rowSpacing: 14
        Layout.fillWidth: true
        MetricCard {
            colors: root.colors
            title: "System monitoring"
            Layout.fillWidth: true
            Layout.preferredWidth: 530
            Layout.preferredHeight: monitor.implicitHeight + 76
            GridLayout {
                id: monitor
                columns: width < 400 ? 1 : 2
                Layout.fillWidth: true
                columnSpacing: 20
                ColumnLayout {
                    Layout.fillWidth: true
                    Item {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 210
                        Canvas {
                            id: gauge
                            anchors.centerIn: parent
                            width: 235; height: 210
                            property real frequency: Number(root.sample[root.gpu ? "gpu_clock" : "cpu_clock"] || 0)
                            onFrequencyChanged: requestPaint()
                            Connections { target: root; function onGpuChanged() { gauge.requestPaint() } }
                            onPaint: {
                                var ctx = getContext('2d'); ctx.reset()
                                var start = Math.PI * .77, end = Math.PI * 2.23
                                ctx.lineWidth = 10; ctx.strokeStyle = '#353841'
                                ctx.beginPath(); ctx.arc(117,104,91,start,end); ctx.stroke()
                                ctx.strokeStyle = root.colors.accent
                                ctx.beginPath(); ctx.arc(117,104,91,start,start+(end-start)*Math.min(frequency/(root.gpu?3000:6000),1)); ctx.stroke()
                                ctx.lineWidth = 3; ctx.strokeStyle = '#545963'
                                for(var i=0;i<43;i++){ var a=start+(end-start)*i/42;ctx.beginPath();ctx.arc(117,104,75,a,a+.012);ctx.stroke() }
                            }
                        }
                        Column {
                            anchors.centerIn: parent
                            spacing: 3
                            Text { anchors.horizontalCenter: parent.horizontalCenter; text: root.format(root.sample[root.gpu ? 'gpu_clock' : 'cpu_clock'], ''); color: root.colors.text; font.pixelSize: 39 }
                            Text { anchors.horizontalCenter: parent.horizontalCenter; text: 'MHz'; color: root.colors.mutedText; font.pixelSize: 11 }
                        }
                    }
                    Text { text: root.gpu ? "GPU FREQUENCY" : "CPU FREQUENCY"; color: root.colors.text; font.pixelSize: 10; font.letterSpacing: 1.4; Layout.alignment: Qt.AlignHCenter }
                    Row {
                        Layout.alignment: Qt.AlignHCenter
                        Repeater {
                            model: ['CPU','GPU']
                            Button { required property string modelData; text: modelData; checked: (root.gpu ? 'GPU' : 'CPU') === modelData; checkable: true; onClicked: root.gpu = modelData === 'GPU'; palette.button: checked ? root.colors.hover : root.colors.panel; palette.buttonText: root.colors.text }
                        }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    StatusRow { colors: root.colors; label: 'Temperature'; value: root.format(root.sample[root.gpu ? 'gpu_temp' : 'cpu_temp'], '°C') }
                    StatusRow { colors: root.colors; label: 'Power'; value: root.format(root.sample[root.gpu ? 'gpu_power' : 'cpu_power'], 'W') }
                    StatusRow { colors: root.colors; label: root.gpu ? 'GPU usage' : 'Memory'; value: root.gpu ? root.format(root.sample.gpu_usage, '%') : root.sample.ram && root.sample.ram[0] !== null ? (root.sample.ram[0]/1024).toFixed(1) + ' GiB' : '—' }
                    StatusRow { colors: root.colors; label: 'GPU state'; value: root.sample.dgpu_suspended === true ? 'Sleeping' : root.sample.gpu_temp !== null && root.sample.gpu_temp !== undefined ? 'Available' : '—' }
                }
                Rectangle { Layout.columnSpan: monitor.columns; Layout.fillWidth: true; height: 1; color: root.colors.border }
                RowLayout {
                    Layout.columnSpan: monitor.columns
                    Layout.fillWidth: true
                    Repeater {
                        model: ['CPU FAN','GPU FAN','SYSTEM FAN']
                        Column {
                            required property int index
                            required property string modelData
                            Layout.fillWidth: true
                            spacing: 5
                            Text { text: modelData; font.pixelSize: 10; color: root.colors.mutedText }
                            Text { text: root.format((root.sample.fan_rpm || {})[String(index+1)], 'RPM'); font.pixelSize: 17; color: root.colors.text }
                        }
                    }
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.preferredWidth: 275
            Layout.alignment: Qt.AlignTop
            spacing: 14
            MetricCard {
                colors: root.colors; title: 'Power & battery'; Layout.fillWidth: true
                StatusRow { colors: root.colors; label: 'Power source'; value: root.sample.power_source && root.sample.power_source[0] !== null ? root.sample.power_source[0] ? String(root.sample.power_source[1] || 'Connected').toUpperCase() : 'BATTERY' : '—' }
                StatusRow { colors: root.colors; label: 'Battery'; value: root.format(root.sample.battery ? root.sample.battery[0] : null, '%') }
                StatusRow { colors: root.colors; label: 'Charge limit'; value: root.format(root.sample.charge_limit, '%') }
                StatusRow { colors: root.colors; label: 'Charging'; value: root.sample.battery && root.sample.battery[1] !== null ? root.sample.battery[1] ? 'Yes' : 'No / held at limit' : '—' }
            }
            MetricCard {
                colors: root.colors; title: 'Memory & fan policy'; Layout.fillWidth: true
                StatusRow { colors: root.colors; label: 'RAM'; value: root.memory(root.sample.ram) }
                StatusRow { colors: root.colors; label: 'VRAM'; value: root.memory(root.sample.vram) }
                StatusRow { colors: root.colors; label: 'Fan curves'; value: root.curveStatus() }
            }
            MetricCard {
                colors: root.colors; title: 'System configuration'; Layout.fillWidth: true
                Text { text: "Use the device pages to edit hardware and settings."; wrapMode: Text.WordWrap; color: root.colors.mutedText; font.pixelSize: 11; Layout.fillWidth: true }
                Button { text: 'Refresh readings'; Layout.fillWidth: true; onClicked: overviewController.refresh(); palette.button: root.colors.hover; palette.buttonText: root.colors.text }
            }
        }
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
                    Layout.preferredHeight: 94
                    enabled: !appController.busy
                    onClicked: appController.selectProfile(modelData)
                    Accessible.name: modelData + (selected ? ', selected' : '')
                    contentItem: Column {
                        spacing: 12
                        Text { text: profile.modelData.toLowerCase().includes('quiet') ? '◌' : profile.modelData.toLowerCase().includes('performance') ? 'ϟ' : '◇'; color: root.colors.text; font.pixelSize: 25; anchors.horizontalCenter: parent.horizontalCenter }
                        Text { text: profile.modelData; color: root.colors.text; font.pixelSize: 11; anchors.horizontalCenter: parent.horizontalCenter }
                    }
                    background: Rectangle { color: profile.selected ? root.colors.selected : profile.hovered ? root.colors.hover : root.colors.panel; border.color: profile.selected ? root.colors.accent : root.colors.border; Rectangle { visible: profile.selected; height: 3; width: parent.width*.6; anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; color: root.colors.accent } }
                }
            }
        }
        Text { text: appController.demoMode ? 'DEMO · Fixture readings and simulated selection' : 'Active profile: ' + appController.activeProfile; color: root.colors.accent; font.pixelSize: 11 }
    }
}
