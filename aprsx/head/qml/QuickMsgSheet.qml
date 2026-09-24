import QtQuick

// Pick a recipient, then tap a canned message to send it at once.
Sheet {
    id: sheet
    title: "Quick message"
    property string to: ""
    signal custom(string to)

    function openFor(peer) {
        to = peer || (core.quickPeers.length ? core.quickPeers[0] : "")
        open()
    }

    readonly property var peers: {
        const list = core.quickPeers.slice()
        if (to && list.indexOf(to) < 0) list.unshift(to)
        return list
    }

    Column {
        anchors.fill: parent
        spacing: Theme.gap

        Text {
            text: sheet.peers.length ? "To" : "No recipients yet — use Other… to type a callsign"
            color: Theme.muted
            font.pixelSize: Theme.body
        }
        Flickable {
            width: parent.width
            height: 56 * Theme.u
            contentWidth: peerRow.width
            clip: true
            flickableDirection: Flickable.HorizontalFlick
            Row {
                id: peerRow
                spacing: Theme.gap
                Repeater {
                    model: sheet.peers
                    BigButton {
                        required property string modelData
                        text: modelData
                        font.family: Theme.mono
                        width: Math.max(120 * Theme.u, implicitContentWidth + 3 * Theme.gap)
                        highlighted: modelData === sheet.to
                        onClicked: sheet.to = modelData
                    }
                }
            }
        }

        Text {
            text: "Message"
            color: Theme.muted
            font.pixelSize: Theme.body
        }
        Grid {
            id: grid
            width: parent.width
            columns: 3
            spacing: Theme.gap
            readonly property real cellW: (width - (columns - 1) * spacing) / columns
            Repeater {
                model: (core.config.canned_messages || [])
                BigButton {
                    required property string modelData
                    width: grid.cellW
                    height: 64 * Theme.u
                    text: modelData
                    enabled: sheet.to !== ""
                    onClicked: {
                        core.sendMessage(sheet.to, modelData)
                        sheet.close()
                    }
                }
            }
            BigButton {
                width: grid.cellW
                height: 64 * Theme.u
                text: "Other…"
                tint: Theme.out
                onClicked: {
                    sheet.close()
                    sheet.custom(sheet.to)
                }
            }
        }
    }
}
