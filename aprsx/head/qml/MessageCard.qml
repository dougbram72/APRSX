import QtQuick

// One message on the carousel: APRS, or MeshCore (purple, with a MESH tag).
Item {
    id: card
    required property int index
    required property var model
    readonly property bool incoming: model.direction === "in"
    readonly property bool unread: incoming && !model.read
    readonly property string peer: model.peer
    readonly property bool mesh: model.kind === "mesh"
    readonly property string conv: model.conv
    // What a mesh reply goes to: the channel, or the node of a DM.
    readonly property string replyName: model.where || model.peer

    function markRead() {
        if (mesh) core.markMeshRead(conv)
        else core.markRead(peer)
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: Theme.gap / 2
        radius: Theme.radius * 1.5
        color: card.mesh ? (card.incoming ? Theme.meshPanel : Theme.meshOut)
             : card.incoming ? Theme.panel : Qt.darker(Theme.out, 1.6)
        border.color: card.unread ? Theme.accent : card.mesh ? Theme.mesh
                    : card.incoming ? Theme.border : Theme.out
        border.width: (card.unread ? 3 : 1) * Theme.u

        Column {
            anchors.fill: parent
            anchors.margins: Theme.gap * 1.5
            spacing: Theme.gap

            Item {
                width: parent.width
                height: who.implicitHeight

                AprsSymbol {
                    id: peerSymbol
                    readonly property string sym: card.mesh ? "" : core.symbols[card.model.peer] || ""
                    visible: sym.length === 2
                    anchors.left: parent.left
                    anchors.verticalCenter: who.verticalCenter
                    width: visible ? 40 * Theme.u : 0
                    height: width
                    table: sym.charAt(0)
                    code: sym.charAt(1)
                }
                Rectangle {
                    id: meshTag
                    visible: card.mesh
                    anchors.left: peerSymbol.right
                    anchors.verticalCenter: who.verticalCenter
                    width: visible ? meshText.implicitWidth + Theme.gap * 1.5 : 0
                    height: meshText.implicitHeight + Theme.gap / 2
                    radius: Theme.radius / 2
                    color: Theme.mesh
                    Text {
                        id: meshText
                        anchors.centerIn: parent
                        text: "MESH"
                        color: Theme.bg
                        font.pixelSize: Theme.small
                        font.bold: true
                    }
                }
                Text {
                    id: who
                    anchors.left: meshTag.right
                    anchors.leftMargin: peerSymbol.visible || meshTag.visible ? Theme.gap : 0
                    text: (card.incoming ? "From " : "To ") + card.model.peer
                          + (card.incoming && card.model.where ? " on " + card.model.where : "")
                    color: card.incoming ? Theme.accent : Theme.text
                    font.pixelSize: Theme.large
                    font.bold: true
                    font.family: Theme.mono
                }
                Rectangle {
                    visible: card.unread
                    anchors.left: who.right
                    anchors.leftMargin: Theme.gap
                    anchors.verticalCenter: who.verticalCenter
                    width: newText.implicitWidth + Theme.gap * 1.5
                    height: newText.implicitHeight + Theme.gap / 2
                    radius: height / 2
                    color: Theme.accent
                    Text {
                        id: newText
                        anchors.centerIn: parent
                        text: "NEW"
                        color: Theme.bg
                        font.pixelSize: Theme.small
                        font.bold: true
                    }
                }
                Text {
                    anchors.right: parent.right
                    anchors.baseline: who.baseline
                    text: Theme.clock(card.model.ts) + "  ·  " + Theme.ago(card.model.ts)
                    color: Theme.muted
                    font.pixelSize: Theme.body
                }
            }

            Text {
                width: parent.width
                height: parent.height - who.height - footer.height - 2 * parent.spacing
                text: card.model.text
                color: Theme.text
                wrapMode: Text.Wrap
                verticalAlignment: Text.AlignVCenter
                font.pixelSize: Theme.huge
                fontSizeMode: Text.Fit
                minimumPixelSize: Theme.body
            }

            Text {
                id: footer
                width: parent.width
                horizontalAlignment: Text.AlignRight
                font.pixelSize: Theme.body
                font.bold: true
                text: {
                    if (card.mesh && card.incoming) {
                        const m = card.model
                        const snr = m.snr === null || m.snr === undefined ? "" : "SNR " + m.snr + " dB"
                        const hops = m.hops === null || m.hops === undefined ? ""
                                   : m.hops === 0 ? "direct" : m.hops + (m.hops === 1 ? " hop" : " hops")
                        return [snr, hops].filter(x => x).join("  ·  ")
                    }
                    if (card.incoming) return card.model.parts > 1 ? card.model.parts + " parts" : ""
                    if (card.mesh) {
                        switch (card.model.state) {
                        case "acked": return "✓ Acked"
                        case "failed": return "✗ No ack"
                        case "sent": return "Sent"
                        default: return card.model.tries ? "Sending " + card.model.tries + "/3…"
                                                         : "Waiting for device…"
                        }
                    }
                    switch (card.model.state) {
                    case "acked": return "✓ Acked"
                    case "rejected": return "✗ Rejected"
                    case "failed": return "✗ No ack"
                    default: return card.model.tries ? "Sending " + card.model.tries + "/5…"
                                                     : "Waiting for TNC…"
                    }
                }
                color: card.incoming ? Theme.muted
                     : card.model.state === "acked" ? Theme.good
                     : card.model.state === "sent" ? Theme.muted
                     : card.model.state === "pending" ? Theme.accent : Theme.bad
            }
        }
    }
}
