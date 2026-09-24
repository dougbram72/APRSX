import QtQuick

// One message on the carousel.
Item {
    id: card
    required property int index
    required property var model
    readonly property bool incoming: model.direction === "in"
    readonly property bool unread: incoming && !model.read
    readonly property string peer: model.peer

    Rectangle {
        anchors.fill: parent
        anchors.margins: Theme.gap / 2
        radius: Theme.radius * 1.5
        color: card.incoming ? Theme.panel : Qt.darker(Theme.out, 1.6)
        border.color: card.unread ? Theme.accent : card.incoming ? Theme.border : Theme.out
        border.width: (card.unread ? 3 : 1) * Theme.u

        Column {
            anchors.fill: parent
            anchors.margins: Theme.gap * 1.5
            spacing: Theme.gap

            Item {
                width: parent.width
                height: who.implicitHeight

                Text {
                    id: who
                    anchors.left: parent.left
                    text: (card.incoming ? "From " : "To ") + card.model.peer
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
                    if (card.incoming) return card.model.parts > 1 ? card.model.parts + " parts" : ""
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
                     : card.model.state === "pending" ? Theme.accent : Theme.bad
            }
        }
    }
}
