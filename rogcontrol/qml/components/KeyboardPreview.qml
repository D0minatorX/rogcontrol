import QtQuick
import QtQuick.Layouts
import QtQuick.Window
import "RogKeyboardLayout.js" as RogLayout

Item {
    id: preview
    objectName: 'keyboardPreview'
    Accessible.role: Accessible.Graphic
    Accessible.name: 'ROG Strix G16 keyboard lighting preview'
    Accessible.description: 'G614 US-layout illustration. Keys are decorative; lighting follows the existing controls.'
    property var colors
    property var lighting: ({mode: 'Static', brightness: 0, speed: 2, color1: '#000000', color2: '#000000'})
    property color primaryColour: lighting.color1
    property color secondaryColour: lighting.color2
    property real phase: 0
    property var baseColours: []
    readonly property var keys: RogLayout.keys
    readonly property bool hasColour: baseColours.length === 4
    readonly property int animationDuration: lighting.speed === 1 ? 3600 : lighting.speed === 3 ? 1200 : 2400
    readonly property bool animationRunning: animated && enabled && visible && hasColour && lighting.brightness > 0
    && Window.window !== null && Window.window.visible && Window.window.visibility !== Window.Minimized
    readonly property var zoneColours: {
        if (!hasColour) return []
        const result = []
        for (let i = 0; i < 4; i++) result.push(ledColour(i * 4))
        return result
    }
    readonly property bool animated: ['Breathing','Pulse','Color Cycle','Rainbow'].indexOf(lighting.mode) >= 0
    implicitHeight: Math.max(150, Math.min(300, width * .43 + 24))
    NumberAnimation on phase {
        from: 0; to: 1
        duration: preview.animationDuration
        loops: Animation.Infinite
        running: preview.animationRunning
    }
    function mix(a,b,t) { return Qt.rgba(a.r*(1-t)+b.r*t,a.g*(1-t)+b.g*t,a.b*(1-t)+b.b*t,1) }
    function shiftedLegend(label) {
        const legends = {'1':'!','2':'@','3':'#','4':'$','5':'%','6':'^','7':'&','8':'*','9':'(', '0':')','−':'_','=':'+','`':'~'}
        return legends[label] || ''
    }
    function ledColour(x) {
        const mode = lighting.mode
        const zone = Math.min(3,Math.floor(x/4))/3
        if (mode === 'Breathing') return mix(primaryColour,secondaryColour,(1-Math.cos(phase*2*Math.PI))/2)
        if (mode === 'Color Cycle') return Qt.hsla(phase,.8,.56,1)
        if (mode === 'Rainbow') return Qt.hsla((phase+zone*.5)%1,.8,.56,1)
        const hex = baseColours[Math.min(3, Math.floor(x / 4))]
        return hex ? Qt.rgba(parseInt(hex.slice(1,3),16)/255, parseInt(hex.slice(3,5),16)/255, parseInt(hex.slice(5,7),16)/255, 1) : colors.mutedText
    }
    readonly property real ledOpacity: !enabled || !hasColour || lighting.brightness === 0 ? 0 : (lighting.brightness/3) * (lighting.mode === 'Pulse' ? .15+.85*Math.pow((1+Math.cos(phase*2*Math.PI))/2,2) : 1)
    Rectangle {
        id: chassis
        width: Math.min(parent.width-12,840)
        height: Math.min(parent.height-24, width * 7/16 + 20)
        anchors.horizontalCenter: parent.horizontalCenter
        y: 3
        radius: 0
        color: 'transparent'
        AngularPanel {
            anchors.fill: parent; cut: 17; decorated: false
            fillColor: preview.colors.hover
            lineColor: Qt.lighter(preview.colors.border,1.6)
            accentColor: preview.colors.accent
        }
        AngularPanel {
            anchors.fill: parent; anchors.margins: 3; cut: 15; decorated: false
            fillColor: '#35363a'
            lineColor: preview.colors.window
            accentColor: preview.colors.accent
        }
        Rectangle {
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.leftMargin: 18; anchors.rightMargin: 18; anchors.topMargin: 5
            height: 2; radius: 1; color: '#55565a'; opacity: .55
        }
        Rectangle {
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.leftMargin: chassis.width * .28; anchors.rightMargin: chassis.width * .28
            height: 1; color: '#08090b'; opacity: .7
        }
        Item {
            id: deck
            anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14
            anchors.topMargin: 20; anchors.bottomMargin: 14
            readonly property real unitX: width/16
            readonly property real unitY: height/7
            Repeater {
                model: preview.keys
                Rectangle {
                    id: keycap
                    required property var modelData
                    property color led: preview.zoneColours[Math.min(3, Math.floor(modelData.x/4))] || preview.colors.mutedText
                    x: modelData.x*deck.unitX
                    y: modelData.y*deck.unitY
                    width: modelData.w*deck.unitX-4
                    height: modelData.h*deck.unitY-3
                    radius: modelData.power ? 8 : 4
                    color: modelData.power || modelData.clear ? 'transparent' : '#08090a'
                    Rectangle {
                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                        height: 2; radius: 1
                        color: keycap.modelData.power || keycap.modelData.clear ? 'transparent' : '#050607'
                    }
                    Rectangle {
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 1
                        height: parent.height-2
                        clip: deck.width < 560
                        radius: 3
                        color: keycap.modelData.power || keycap.modelData.clear ? 'transparent' : '#191b1e'
                        border.color: keycap.modelData.power ? 'transparent' : keycap.modelData.clear ? '#727680' : '#292b2f'
                        Rectangle {
                            visible: !!keycap.modelData.clear
                            anchors.fill: parent; anchors.margins: 1
                            radius: 3
                            gradient: Gradient {
                                GradientStop { position: 0; color: Qt.rgba(.72,.79,.88,.27) }
                                GradientStop { position: .16; color: Qt.rgba(keycap.led.r,keycap.led.g,keycap.led.b,.08+.17*preview.ledOpacity) }
                                GradientStop { position: .72; color: '#32343b42' }
                                GradientStop { position: 1; color: Qt.rgba(keycap.led.r,keycap.led.g,keycap.led.b,.04+.1*preview.ledOpacity) }
                            }
                            border.color: Qt.rgba(.78,.84,.91,.18)
                            Rectangle {
                                anchors.fill: parent; anchors.margins: 4
                                radius: 3; color: '#281b2029'
                                border.color: Qt.rgba(.7,.77,.86,.15)
                                Rectangle {
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                                    anchors.margins: 1; height: 1
                                    color: keycap.led; opacity: .35*preview.ledOpacity
                                }
                            }
                        }
                        Rectangle { visible: !keycap.modelData.power; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 3; height: 1; color: '#d9e1ec'; opacity: keycap.modelData.clear ? .4 : .06 }
                        Text {
                            visible: preview.shiftedLegend(keycap.modelData.label) !== ''
                            anchors.left: parent.left; anchors.top: parent.top
                            anchors.leftMargin: 5; anchors.topMargin: 2
                            text: preview.shiftedLegend(keycap.modelData.label)
                            color: preview.mix(Qt.rgba(.67,.71,.77,1),keycap.led,preview.ledOpacity * .5)
                            font.pixelSize: Math.max(6,Math.min(9,deck.unitX*.16))
                            font.weight: Font.DemiBold
                        }
                        Rectangle {
                            visible: !!keycap.modelData.space
                            anchors.fill: parent; anchors.margins: 4
                            radius: 2
                            color: 'transparent'
                            border.color: '#25282c'; opacity: .7
                            Rectangle { anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; width: parent.width*.18; height: 2; color: keycap.led; opacity: preview.ledOpacity }
                        }
                        Image {
                            visible: !!keycap.modelData.power
                            anchors.centerIn: parent
                            width: parent.width * 1.08
                            height: width * 28 / 32
                            source: 'keyboard-power.svg'
                            fillMode: Image.PreserveAspectFit
                            sourceSize.width: 64; sourceSize.height: 64
                        }
                        Image {
                            visible: !!keycap.modelData.copilot
                            anchors.centerIn: parent
                            anchors.horizontalCenterOffset: 2
                            anchors.verticalCenterOffset: -1
                            width: parent.width * .44
                            height: width
                            source: 'keyboard-copilot.svg'
                            fillMode: Image.PreserveAspectFit
                            sourceSize.width: 48; sourceSize.height: 48
                            opacity: .35+.65*preview.ledOpacity
                        }
                        Image {
                            visible: keycap.modelData.label === 'Super'
                            anchors.centerIn: parent
                            anchors.horizontalCenterOffset: 1
                            width: parent.width * .34; height: width
                            source: 'keyboard-windows.svg'
                            sourceSize.width: 48; sourceSize.height: 48
                            opacity: .35+.65*preview.ledOpacity
                        }
                        Item {
                            visible: keycap.modelData.label === 'Super'
                            anchors.left: parent.left; anchors.bottom: parent.bottom
                            anchors.leftMargin: 4; anchors.bottomMargin: 3
                            width: Math.max(5,deck.unitX*.15); height: width*1.25
                            Rectangle {
                                anchors.horizontalCenter: parent.horizontalCenter; y: 0
                                width: parent.width*.65; height: parent.height*.65
                                radius: width*.4; color: 'transparent'
                                border.color: keycap.led
                            }
                            Rectangle {
                                anchors.bottom: parent.bottom
                                width: parent.width; height: parent.height*.6; radius: 1
                                color: keycap.led
                                Rectangle {
                                    anchors.centerIn: parent; width: 1; height: parent.height*.55
                                    color: '#22252a'
                                }
                            }
                            opacity: .35+.65*preview.ledOpacity
                        }
                        Rectangle {
                            visible: !!keycap.modelData.copilot
                            anchors.left: parent.left; anchors.bottom: parent.bottom
                            anchors.leftMargin: 4; anchors.bottomMargin: 3
                            width: Math.max(6,deck.unitX*.17); height: width
                            color: 'transparent'; border.color: '#ac12d8'; border.width: 1
                            Row {
                                anchors.centerIn: parent; spacing: 2
                                Repeater {
                                    model: 2
                                    Rectangle { width: 1; height: 5; color: '#ac12d8' }
                                }
                            }
                            opacity: .35+.65*preview.ledOpacity
                        }
                        Column {
                            visible: !keycap.modelData.power && !keycap.modelData.copilot && keycap.modelData.label !== 'Super'
                            anchors.centerIn: parent
                            spacing: keycap.modelData.macro ? 0 : 1
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: keycap.modelData.label
                                color: preview.mix(Qt.rgba(.71,.75,.81,1),keycap.led,preview.ledOpacity * .55)
                                width: Math.max(0, keycap.width - 6)
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                                font.pixelSize: Math.min(keycap.modelData.macro ? 8 : (keycap.modelData.label.length>4 ? 8 : 10), deck.unitX * .25, keycap.height * .34)
                                font.weight: Font.DemiBold
                            }
                            Text {
                                visible: keycap.modelData.sub.length > 0 && deck.width >= 560
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: keycap.modelData.sub
                                color: preview.mix(Qt.rgba(.57,.62,.68,1),keycap.led,preview.ledOpacity * .45)
                                width: Math.max(0, keycap.width - 6)
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                                font.pixelSize: 7
                            }
                        }
                    }
                }
            }
        }
    }
    // A recessed diffuser with a soft halo; all four zones share the key lighting.
    Rectangle {
        id: lightbar
        objectName: 'keyboardLightbar'
        anchors.horizontalCenter: chassis.horizontalCenter
        anchors.top: chassis.bottom; anchors.topMargin: 7
        width: chassis.width - 30; height: 9; radius: 4.5
        color: preview.colors.window
        border.color: preview.colors.border
        Rectangle {
            anchors.fill: parent; anchors.margins: -3
            radius: 7
            opacity: .16 * preview.ledOpacity
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: preview.zoneColours[0] || preview.colors.mutedText }
                GradientStop { position: .33; color: preview.zoneColours[1] || preview.colors.mutedText }
                GradientStop { position: .67; color: preview.zoneColours[2] || preview.colors.mutedText }
                GradientStop { position: 1; color: preview.zoneColours[3] || preview.colors.mutedText }
            }
        }
        Rectangle {
            anchors.fill: parent; anchors.margins: 2
            radius: 2.5
            opacity: preview.ledOpacity
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: preview.zoneColours[0] || preview.colors.mutedText }
                GradientStop { position: .33; color: preview.zoneColours[1] || preview.colors.mutedText }
                GradientStop { position: .67; color: preview.zoneColours[2] || preview.colors.mutedText }
                GradientStop { position: 1; color: preview.zoneColours[3] || preview.colors.mutedText }
            }
            Rectangle {
                anchors.left: parent.left; anchors.right: parent.right
                anchors.top: parent.top; anchors.margins: 1
                height: 1; radius: .5
                color: preview.colors.text; opacity: .35
            }
        }
    }
    Text {
        visible: preview.enabled && !preview.hasColour
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        text: preview.lighting.mode === 'Ambient' ? 'Waiting for screen capture' : 'Waiting for a colour sample'
        color: preview.colors.mutedText
        font.pixelSize: 11
    }

}
