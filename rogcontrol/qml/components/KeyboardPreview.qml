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
    implicitHeight: Math.max(150, Math.min(300, width * .44 + 24))
    NumberAnimation on phase {
        from: 0; to: 1
        duration: preview.animationDuration
        loops: Animation.Infinite
        running: preview.animationRunning
    }
    function mix(a,b,t) { return Qt.rgba(a.r*(1-t)+b.r*t,a.g*(1-t)+b.g*t,a.b*(1-t)+b.b*t,1) }
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
        width: Math.min(parent.width-20,820)
        height: Math.min(parent.height-24, width * 7/16 + 20)
        anchors.horizontalCenter: parent.horizontalCenter
        y: 3
        radius: 12
        color: preview.colors.window
        border.color: Qt.lighter(preview.colors.border,1.6)
        Rectangle {
            anchors.fill: parent; anchors.margins: 3
            radius: 9
            gradient: Gradient {
                GradientStop { position: 0; color: preview.colors.hover }
                GradientStop { position: .2; color: preview.colors.panel }
                GradientStop { position: 1; color: preview.colors.sidebar }
            }
        }
        Item {
            id: deck
            anchors.fill: parent; anchors.margins: 14
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
                    height: modelData.h*deck.unitY-2
                    radius: modelData.power ? 7 : 4
                    color: preview.colors.window
                    Rectangle {
                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                        height: 4; radius: 3
                        color: keycap.led; opacity: preview.ledOpacity*.35
                    }
                    Rectangle {
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 1
                        height: parent.height-3
                        clip: deck.width < 560
                        radius: 3
                        gradient: Gradient {
                            GradientStop { position: 0; color: keycap.modelData.clear || keycap.modelData.space ? preview.mix(preview.colors.hover,keycap.led,.35*preview.ledOpacity) : Qt.lighter(preview.colors.hover,1.15) }
                            GradientStop { position: 1; color: preview.colors.panel }
                        }
                        border.color: preview.mix(preview.colors.border,keycap.led,.42*preview.ledOpacity)
                        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 3; height: 1; color: preview.colors.text; opacity: .08 }
                        Rectangle {
                            visible: !!keycap.modelData.clear
                            anchors.fill: parent; anchors.margins: 5
                            radius: 2
                            color: 'transparent'
                            border.color: keycap.led; opacity: .2*preview.ledOpacity
                        }
                        Rectangle {
                            visible: !!keycap.modelData.space
                            anchors.fill: parent; anchors.margins: 6
                            radius: 2
                            color: 'transparent'
                            border.color: keycap.led; opacity: .45*preview.ledOpacity
                            Rectangle { anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; width: parent.width*.63; height: 2; color: keycap.led }
                        }
                        Column {
                            anchors.centerIn: parent
                            spacing: keycap.modelData.macro ? 0 : 1
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: keycap.modelData.label
                                color: preview.mix(preview.colors.mutedText,Qt.lighter(keycap.led,1.8),preview.ledOpacity)
                                width: Math.max(0, keycap.width - 6)
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                                font.pixelSize: Math.min(keycap.modelData.macro ? 9 : (keycap.modelData.label.length>4 ? 9 : 11), deck.unitX * .42, keycap.height * .6)
                                font.weight: Font.Medium
                            }
                            Text {
                                visible: keycap.modelData.sub.length > 0 && deck.width >= 560
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: keycap.modelData.sub
                                color: preview.mix(preview.colors.mutedText,keycap.led,preview.ledOpacity)
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
