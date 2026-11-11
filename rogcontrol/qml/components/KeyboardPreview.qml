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
    Item {
        id: chassis
        width: Math.min(parent.width-12,840)
        height: Math.min(parent.height-24, width * 7/16 + 20)
        anchors.horizontalCenter: parent.horizontalCenter
        y: 3
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
                    readonly property bool translucent: !!modelData.clear || !!modelData.space
                    property color led: preview.zoneColours[Math.min(3, Math.floor(modelData.x/4))] || preview.colors.mutedText
                    readonly property color legendColour: preview.mix(Qt.rgba(.71,.75,.81,1),led,preview.ledOpacity * .55)
                    readonly property color secondaryLegendColour: preview.mix(Qt.rgba(.57,.62,.68,1),led,preview.ledOpacity * .45)
                    x: modelData.x*deck.unitX
                    y: modelData.y*deck.unitY
                    width: modelData.w*deck.unitX-4
                    height: modelData.h*deck.unitY-3
                    radius: modelData.power ? 8 : 4
                    color: modelData.power || keycap.translucent ? 'transparent' : '#08090a'
                    Rectangle {
                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                        height: 2; radius: 1
                        color: keycap.modelData.power || keycap.translucent ? 'transparent' : '#050607'
                    }
                    Rectangle {
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 1
                        height: parent.height-2
                        clip: deck.width < 560
                        radius: 3
                        color: keycap.modelData.power || keycap.translucent ? 'transparent' : '#191b1e'
                        border.color: keycap.modelData.power ? 'transparent' : keycap.translucent ? '#727680' : '#292b2f'
                        Rectangle {
                            visible: keycap.translucent
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
                        Rectangle { visible: !keycap.modelData.power; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 3; height: 1; color: '#d9e1ec'; opacity: keycap.translucent ? .4 : .06 }
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
                        KeyboardSymbol {
                            visible: !!keycap.modelData.copilot
                            anchors.centerIn: parent
                            anchors.horizontalCenterOffset: 2
                            anchors.verticalCenterOffset: -1
                            width: Math.round(parent.width * .5)
                            height: width
                            symbol: 'copilot'
                            color: keycap.legendColour
                        }
                        KeyboardSymbol {
                            visible: keycap.modelData.label === 'Super'
                            anchors.centerIn: parent
                            anchors.horizontalCenterOffset: 1
                            width: Math.round(parent.width * .36); height: width
                            symbol: 'windows'
                            color: keycap.legendColour
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
                                border.color: keycap.secondaryLegendColour
                            }
                            Rectangle {
                                anchors.bottom: parent.bottom
                                width: parent.width; height: parent.height*.6; radius: 1
                                color: keycap.secondaryLegendColour
                                Rectangle {
                                    anchors.centerIn: parent; width: 1; height: parent.height*.55
                                    color: '#22252a'
                                }
                            }
                        }
                        Rectangle {
                            visible: !!keycap.modelData.copilot
                            anchors.left: parent.left; anchors.bottom: parent.bottom
                            anchors.leftMargin: 4; anchors.bottomMargin: 3
                            width: Math.max(6,deck.unitX*.17); height: width
                            color: 'transparent'; border.color: keycap.secondaryLegendColour; border.width: 1
                            Row {
                                anchors.centerIn: parent; spacing: 2
                                Repeater {
                                    model: 2
                                    Rectangle { width: 1; height: 5; color: keycap.secondaryLegendColour }
                                }
                            }
                        }
                        Column {
                            visible: !keycap.modelData.power && !keycap.modelData.copilot && keycap.modelData.label !== 'Super'
                            anchors.centerIn: parent
                            spacing: keycap.modelData.macro ? 0 : 1
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: keycap.modelData.label
                                color: keycap.legendColour
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
                                color: keycap.secondaryLegendColour
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
    // A continuous diffuser with a narrow luminous core and soft spill.
    Rectangle {
        id: lightbar
        objectName: 'keyboardLightbar'
        anchors.horizontalCenter: chassis.horizontalCenter
        anchors.top: chassis.bottom; anchors.topMargin: 6
        width: chassis.width - 28; height: 10; radius: 5
        color: '#080a0d'
        border.color: '#24272e'
        Gradient {
            id: diffuserGradient
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: preview.zoneColours[0] || preview.colors.mutedText }
            GradientStop { position: .33; color: preview.zoneColours[1] || preview.colors.mutedText }
            GradientStop { position: .67; color: preview.zoneColours[2] || preview.colors.mutedText }
            GradientStop { position: 1; color: preview.zoneColours[3] || preview.colors.mutedText }
        }
        Repeater {
            model: 8
            Rectangle {
                required property int index
                readonly property real spread: (8-index)*1.5
                anchors.centerIn: parent
                width: lightbar.width + spread*2
                height: 5 + spread*2
                radius: height/2
                gradient: diffuserGradient
                opacity: (.012 + index*.005) * preview.ledOpacity
            }
        }
        Rectangle {
            anchors.centerIn: parent
            width: parent.width-4; height: 6; radius: 3
            gradient: diffuserGradient
            opacity: .4*preview.ledOpacity
        }
        Rectangle {
            anchors.centerIn: parent
            width: parent.width-8; height: 3; radius: 1.5
            opacity: preview.ledOpacity
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: Qt.lighter(preview.zoneColours[0] || preview.colors.mutedText,1.35) }
                GradientStop { position: .33; color: Qt.lighter(preview.zoneColours[1] || preview.colors.mutedText,1.35) }
                GradientStop { position: .67; color: Qt.lighter(preview.zoneColours[2] || preview.colors.mutedText,1.35) }
                GradientStop { position: 1; color: Qt.lighter(preview.zoneColours[3] || preview.colors.mutedText,1.35) }
            }
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.top
                width: parent.width-4; height: 1; radius: .5
                color: '#e6f6ff'; opacity: .28
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
