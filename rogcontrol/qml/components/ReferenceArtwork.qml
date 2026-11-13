import QtQuick

Canvas {
    id: artwork
    property real cornerRadius
    property real headerHeight
    property real footerHeight
    property real sidebarWidth
    readonly property url source: Qt.resolvedUrl('../artwork/design-reference.png')

    Component.onCompleted: loadImage(source)
    onImageLoaded: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onCornerRadiusChanged: requestPaint()
    onHeaderHeightChanged: requestPaint()
    onFooterHeightChanged: requestPaint()
    onSidebarWidthChanged: requestPaint()

    onPaint: {
        const c = getContext('2d')
        c.reset()
        if (!isImageLoaded(source) || width <= 0 || height <= headerHeight) return

        const r = cornerRadius
        if (r > 0) {
            c.beginPath()
            c.moveTo(r, 0)
            c.arcTo(width, 0, width, height, r)
            c.arcTo(width, height, 0, height, r)
            c.arcTo(0, height, 0, 0, r)
            c.arcTo(0, 0, width, 0, r)
            c.closePath()
            c.clip()
        }

        const bodyBottom = height - footerHeight
        const headerScale = Math.max(.62, width / 1586)
        // A shared graphite surface runs behind the cards, navigation and footer.
        const surface = c.createLinearGradient(0, headerHeight, width, height)
        surface.addColorStop(0, '#121418')
        surface.addColorStop(.55, '#0d0f13')
        surface.addColorStop(1, '#121418')
        c.fillStyle = surface
        c.fillRect(0, headerHeight, width, height - headerHeight)

        c.strokeStyle = '#ffffff'
        c.globalAlpha = .028
        c.lineWidth = .6
        c.beginPath()
        for (let x = 0; x < width + height; x += 5) {
            c.moveTo(x, headerHeight)
            c.lineTo(x - (height - headerHeight), height)
        }
        c.stroke()
        c.globalAlpha = 1

        // Run the same angled panels through the body and below the profile rail.
        const bodyHeight = height - headerHeight
        c.save()
        c.beginPath()
        c.rect(sidebarWidth, headerHeight, width - sidebarWidth, bodyHeight)
        c.clip()
        const slant = bodyHeight * .86
        const panelStart = width * .83
        const panelWidth = Math.max(64, width * .12)
        const panels = c.createLinearGradient(0, headerHeight, 0, height)
        panels.addColorStop(0, 'rgba(8, 9, 11, .32)')
        panels.addColorStop(.53, 'rgba(8, 9, 11, .18)')
        panels.addColorStop(1, 'rgba(8, 9, 11, .38)')
        c.fillStyle = panels
        c.beginPath()
        c.moveTo(panelStart, headerHeight)
        c.lineTo(panelStart + panelWidth, headerHeight)
        c.lineTo(panelStart + panelWidth - slant, height)
        c.lineTo(panelStart - slant, height)
        c.closePath()
        c.fill()
        c.strokeStyle = 'rgba(125, 130, 143, .13)'
        c.lineWidth = 1
        c.stroke()

        const accent = c.createLinearGradient(0, headerHeight, 0, height)
        accent.addColorStop(0, 'transparent')
        accent.addColorStop(.76, 'transparent')
        accent.addColorStop(.94, 'rgba(240, 0, 54, .20)')
        accent.addColorStop(1, 'rgba(240, 0, 54, .06)')
        c.strokeStyle = accent
        c.beginPath()
        c.moveTo(panelStart + panelWidth, headerHeight)
        c.lineTo(panelStart + panelWidth - slant, height)
        c.stroke()
        c.restore()

        const heroHeight = Math.min(130, bodyBottom - headerHeight)
        if (sidebarWidth >= 164 && heroHeight > 0) {
            // Use the same horizontal mapping for the reference's title and hero.
            const artworkWidth = 1586 * headerScale
            const artworkLeft = width - artworkWidth
            const heroStart = Math.max(sidebarWidth, artworkLeft + 590 * headerScale)
            const sourceLeft = (heroStart - artworkLeft) / headerScale
            c.drawImage(source, 590, 48, 1, 130,
                        sidebarWidth, headerHeight, heroStart - sidebarWidth, heroHeight)
            c.drawImage(source, sourceLeft, 48, 1586 - sourceLeft, 130,
                        heroStart, headerHeight, width - heroStart, heroHeight)

            const bottom = headerHeight + heroHeight
            const join = c.createLinearGradient(0, bottom - 54, 0, bottom)
            join.addColorStop(0, 'transparent')
            join.addColorStop(1, '#111317')
            c.fillStyle = join
            c.fillRect(sidebarWidth, bottom - 54, width - sidebarWidth, 54)
        }

        if (sidebarWidth > 0) {
            const detailHeight = Math.max(0, bodyBottom - headerHeight - 325)
            const detailTop = Math.round(bodyBottom - detailHeight)
            c.drawImage(source, 0, 373, 161, 1,
                        0, headerHeight, sidebarWidth, detailTop - headerHeight + (detailHeight > 0 ? 2 : 0))
            if (detailHeight > 0) {
                c.drawImage(source, 0, 373, 161, 521,
                            0, detailTop, sidebarWidth, bodyBottom - detailTop)
            }
            c.drawImage(source, 0, 893, 161, 1,
                        0, bodyBottom, sidebarWidth, footerHeight)
        }

        const artworkLeft = width - 1586 * headerScale
        const sourceLeft = Math.max(0, -artworkLeft / headerScale)
        c.fillStyle = '#0b0c0e'
        c.fillRect(0, 0, width, headerHeight)
        if (sourceLeft < 1320) {
            c.drawImage(source, sourceLeft, 0, 1320 - sourceLeft, 48,
                        Math.max(0, artworkLeft), 0, (1320 - sourceLeft) * headerScale, headerHeight)
        }
        // Keep the wordmark at the title bar's original size on desktop.
        if (width < 984) {
            const logoWidth = width < 620 ? 56 : 224
            c.drawImage(source, 0, 0, 22 + logoWidth, 48, 0, 0, 22 + logoWidth, headerHeight)
        }
    }
}
