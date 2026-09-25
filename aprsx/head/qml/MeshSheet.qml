import QtQuick

// MeshCore: war-drive start/stop and counters on the left, mesh messages on the right.
Sheet {
    id: sheet
    title: "MeshCore" + (mesh.name ? " · " + mesh.name : "")
    signal compose(string conv, string name)

    readonly property var mesh: core.status.mesh || ({})
    readonly property var wd: mesh.wardrive || ({})

    function convName(conv) {
        if (conv.startsWith("ch:")) {
            const idx = Number(conv.slice(3))
            const ch = core.meshChannels.find(c => c.idx === idx)
            return ch ? ch.name : "Channel " + idx
        }
        return core.meshNames[conv.slice(3)] || "DM " + conv.slice(3, 9)
    }

    headerExtra: [
        BigButton {
            height: 50 * Theme.u
            width: 120 * Theme.u
            text: "Advert"
            enabled: !!sheet.mesh.connected
            onClicked: core.meshAdvert(false)
        }
    ]

    // --- war-drive ---------------------------------------------------------

    Column {
        id: wdPanel
        width: parent.width * 0.44
        height: parent.height
        spacing: Theme.gap

        BigButton {
            width: parent.width
            height: 84 * Theme.u
            text: sheet.wd.active ? "Stop war-drive" : "Start war-drive"
            font.pixelSize: Theme.large
            tint: sheet.wd.active ? Theme.bad : Theme.panelHi
            highlighted: !sheet.wd.active && !!sheet.mesh.connected
            onClicked: core.wardrive(!sheet.wd.active)
        }
        Text {
            width: parent.width
            text: !sheet.mesh.connected ? (sheet.mesh.error || "Device offline")
                  : !sheet.wd.active ? "Not running"
                  : sheet.wd.paused ? "Paused: " + sheet.wd.paused
                  : sheet.wd.listening ? "Listening…" : "Running"
            color: sheet.wd.paused || !sheet.mesh.connected ? Theme.bad : Theme.muted
            font.pixelSize: Theme.small
            elide: Text.ElideRight
            maximumLineCount: 2
            wrapMode: Text.WordWrap
        }
        Row {
            width: parent.width
            spacing: Theme.gap / 2
            Repeater {
                model: ["Pings", "Heard", "Nodes", "RX"]
                Rectangle {
                    required property string modelData
                    required property int index
                    readonly property var value: [sheet.wd.pings, sheet.wd.pings_heard,
                                                  sheet.wd.nodes, sheet.wd.rx][index]
                    width: (wdPanel.width - 3 * Theme.gap / 2) / 4
                    height: 64 * Theme.u
                    radius: Theme.radius
                    color: Theme.panel
                    Column {
                        anchors.centerIn: parent
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: parent.parent.value || 0
                            color: Theme.text
                            font.pixelSize: Theme.large
                            font.bold: true
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: modelData
                            color: Theme.muted
                            font.pixelSize: Theme.small
                        }
                    }
                }
            }
        }
        // Latest answers to our pings.
        Repeater {
            model: core.meshAnswers
            Text {
                required property var modelData
                width: wdPanel.width
                text: Theme.clock(modelData.ts) + "  " + (modelData.node_name || modelData.node || "?")
                      + "  " + modelData.snr + " dB" + (modelData.kind === "echo" ? " (echo)" : "")
                color: Theme.text
                font.pixelSize: Theme.small
                elide: Text.ElideRight
            }
        }
    }

    // --- messages ------------------------------------------------------------

    Item {
        anchors.left: wdPanel.right
        anchors.leftMargin: Theme.gap * 1.5
        anchors.right: parent.right
        height: parent.height

        Flow {
            id: channels
            width: parent.width
            spacing: Theme.gap / 2
            Repeater {
                model: core.meshChannels
                BigButton {
                    required property var modelData
                    height: 50 * Theme.u
                    width: Math.max(110 * Theme.u, implicitWidth)
                    text: "✎ " + modelData.name
                    enabled: !!sheet.mesh.connected
                    onClicked: sheet.compose("ch:" + modelData.idx, modelData.name)
                }
            }
        }

        ListView {
            id: list
            anchors.top: channels.bottom
            anchors.topMargin: Theme.gap
            anchors.bottom: parent.bottom
            width: parent.width
            clip: true
            spacing: Theme.gap / 2
            model: core.meshMessages
            delegate: Rectangle {
                required property var model
                width: list.width
                height: col.implicitHeight + Theme.gap
                radius: Theme.radius
                color: model.direction === "out" ? Theme.out : Theme.panel
                Column {
                    id: col
                    x: Theme.gap / 2
                    y: Theme.gap / 2
                    width: parent.width - Theme.gap
                    Text {
                        width: parent.width
                        text: sheet.convName(model.conv)
                              + (model.sender && model.direction === "in" ? " · " + model.sender : "")
                              + "  " + Theme.ago(model.ts)
                              + (model.direction === "out" && model.state !== "sent" ? "  " + model.state : "")
                        color: Theme.muted
                        font.pixelSize: Theme.small
                        elide: Text.ElideRight
                    }
                    Text {
                        width: parent.width
                        text: model.text
                        color: Theme.text
                        font.pixelSize: Theme.body
                        wrapMode: Text.Wrap
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    enabled: !!sheet.mesh.connected
                    onClicked: sheet.compose(model.conv, sheet.convName(model.conv))
                }
            }
            Text {
                anchors.centerIn: parent
                visible: list.count === 0
                text: "No mesh messages yet"
                color: Theme.muted
                font.pixelSize: Theme.body
            }
        }
    }
}
