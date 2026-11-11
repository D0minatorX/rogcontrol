import QtQuick
import QtQuick.Shapes

Item {
    id: root
    property string symbol: 'windows'
    property color color: '#b5bfce'
    objectName: symbol + 'KeyLegend'

    // Keep the cross on physical pixels; scaling a single path can close it.
    Repeater {
        model: root.symbol === 'windows' ? 4 : 0
        Rectangle {
            required property int index
            readonly property int pane: Math.max(2,Math.floor((Math.min(root.width,root.height)-2)/2))
            x: Math.floor((root.width-(pane*2+2))/2) + (index%2)*(pane+2)
            y: Math.floor((root.height-(pane*2+2))/2) + Math.floor(index/2)*(pane+2)
            width: pane; height: pane
            color: root.color
        }
    }
    Shape {
        visible: root.symbol === 'copilot'
        width: 24; height: 24
        layer.enabled: true
        layer.smooth: true
        layer.textureSize: Qt.size(96,96)
        transform: Scale { xScale: root.width / 24; yScale: root.height / 24 }
        ShapePath {
            strokeWidth: 0
            strokeColor: 'transparent'
            fillColor: root.color
            fillRule: ShapePath.OddEvenFill
            PathSvg {
                path: 'M8.4 3.1h6.8c1.7 0 2.6 1 2.9 2.7l.2 1.3h.8c2.2 0 3.1 1.2 2.5 3.3l-1.7 6.2c-.6 2.1-1.7 3.2-3.8 3.2H9.4c-1.7 0-2.6-.9-2.9-2.6l-.2-1.3h-.8c-2.2 0-3.1-1.2-2.5-3.3l1.7-6.2c.6-2.1 1.7-3.3 3.7-3.3z M14.5 5.3l-.8 2.7h1.9l.8-2.7z M12.6 10.1l-1.3 4.6h1.9l1.3-4.6z M10.7 16.5l-.6 1.9H12l.6-1.9z'
            }
        }
    }
}
