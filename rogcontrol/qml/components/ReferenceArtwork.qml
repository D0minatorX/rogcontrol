import QtQuick
// Display decorative regions of the supplied reference; controls are live QML.
Item {
    id: artwork
    property rect sourceClipRect: Qt.rect(0, 0, 1586, 992)
    clip: true
    Image {
        source: Qt.resolvedUrl('../artwork/design-reference.png')
        width: 1586 * artwork.width / artwork.sourceClipRect.width
        height: 992 * artwork.height / artwork.sourceClipRect.height
        x: -artwork.sourceClipRect.x * artwork.width / artwork.sourceClipRect.width
        y: -artwork.sourceClipRect.y * artwork.height / artwork.sourceClipRect.height
        smooth: true
    }
}
