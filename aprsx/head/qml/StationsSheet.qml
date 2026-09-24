import QtQuick

// Stations heard, most recent first. Tap one to message it.
Sheet {
    id: sheet
    title: "Stations heard (" + core.stations.count + ")"
    signal picked(string name)

    ListView {
        id: list
        anchors.fill: parent
        clip: true
        model: core.stations
        spacing: Theme.gap / 2
        boundsBehavior: Flickable.StopAtBounds

        Text {
            anchors.centerIn: parent
            visible: list.count === 0
            text: "No stations heard yet"
            color: Theme.muted
            font.pixelSize: Theme.body
        }

        delegate: Rectangle {
            id: row
            required property var model
            width: list.width
            height: 62 * Theme.u
            radius: Theme.radius
            color: tap.pressed ? Theme.panelHi : Theme.panel

            MouseArea {
                id: tap
                anchors.fill: parent
                enabled: !row.model.is_object
                onClicked: sheet.picked(row.model.name)
            }
            AprsSymbol {
                id: symbol
                anchors.left: parent.left
                anchors.leftMargin: Theme.gap
                anchors.verticalCenter: parent.verticalCenter
                width: 44 * Theme.u
                height: width
                table: row.model.symbol_table || ""
                code: row.model.symbol || ""
            }
            Text {
                id: name
                anchors.left: symbol.right
                anchors.leftMargin: Theme.gap
                anchors.top: parent.top
                anchors.topMargin: Theme.gap / 2
                text: row.model.name
                color: row.model.is_object ? Theme.muted : Theme.accent
                font.pixelSize: Theme.large
                font.bold: true
                font.family: Theme.mono
            }
            Text {
                anchors.left: name.left
                anchors.right: stats.left
                anchors.rightMargin: Theme.gap
                anchors.bottom: parent.bottom
                anchors.bottomMargin: Theme.gap / 2
                text: row.model.comment || ""
                color: Theme.muted
                font.pixelSize: Theme.small
                elide: Text.ElideRight
            }
            Column {
                id: stats
                anchors.right: parent.right
                anchors.rightMargin: Theme.gap * 1.5
                anchors.verticalCenter: parent.verticalCenter
                Text {
                    anchors.right: parent.right
                    text: [Theme.distance(row.model.distance_km, core.status.units),
                           Theme.compass(row.model.bearing), Theme.ago(row.model.last_heard)]
                          .filter(s => s).join("  ·  ")
                    color: Theme.text
                    font.pixelSize: Theme.body
                }
                Text {
                    anchors.right: parent.right
                    text: row.model.channel === "is" ? "via APRS-IS"
                          : row.model.heard_direct ? "direct" : (row.model.path || "")
                    color: row.model.heard_direct ? Theme.good : Theme.muted
                    font.pixelSize: Theme.small
                }
            }
        }
    }
}
