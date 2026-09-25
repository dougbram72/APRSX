import QtQuick

// Top strip: station, link/TNC state, RX/TX lights, GPS, APRS-IS, clock, unread.
Rectangle {
    id: strip
    color: Theme.panel

    function flash(light) { light.opacity = 1; light.fade.restart() }

    Connections {
        target: core
        function onRxActivity() { strip.flash(rxLight) }
        function onTxActivity() { strip.flash(txLight) }
    }

    component Pill: Rectangle {
        property alias label: t.text
        property color tone: Theme.muted
        height: strip.height * 0.62
        width: t.implicitWidth + 2 * Theme.gap
        radius: height / 2
        color: "transparent"
        border.color: tone
        border.width: Math.max(1, Theme.u)
        Text {
            id: t
            anchors.centerIn: parent
            color: parent.tone
            font.pixelSize: Theme.small
            font.bold: true
        }
    }

    component Light: Rectangle {
        property alias fade: anim
        property color on: Theme.good
        width: lbl.implicitWidth + 2 * Theme.gap
        height: strip.height * 0.62
        radius: Theme.radius / 2
        color: on
        opacity: 0.15
        property alias label: lbl.text
        Text {
            id: lbl
            anchors.centerIn: parent
            color: Theme.text
            font.pixelSize: Theme.small
            font.bold: true
        }
        NumberAnimation on opacity { id: anim; running: false; to: 0.15; duration: 900 }
    }

    Row {
        anchors.left: parent.left
        anchors.leftMargin: Theme.gap
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.gap

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: core.status.station || "APRS-X"
            color: Theme.accent
            font.pixelSize: Theme.large
            font.bold: true
            font.family: Theme.mono
        }
        Pill {
            anchors.verticalCenter: parent.verticalCenter
            visible: !core.connected
            label: "CORE OFFLINE"
            tone: Theme.bad
        }
        Pill {
            anchors.verticalCenter: parent.verticalCenter
            visible: core.connected
            label: "TNC"
            tone: core.status.kiss_connected ? Theme.good : Theme.bad
        }
        Light { id: rxLight; anchors.verticalCenter: parent.verticalCenter; label: "RX"; on: Theme.good }
        Light { id: txLight; anchors.verticalCenter: parent.verticalCenter; label: "TX"; on: Theme.bad }
    }

    Row {
        anchors.right: parent.right
        anchors.rightMargin: Theme.gap
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.gap

        // GPS: green with a fix, amber while gpsd is up but searching.
        Pill {
            anchors.verticalCenter: parent.verticalCenter
            label: core.status.gps_fix ? "GPS " + (core.status.gps.mode === 3 ? "3D" : "2D")
                                       : "GPS –"
            tone: core.status.gps_fix ? Theme.good
                  : core.status.gps_connected ? Theme.accent : Theme.muted
        }
        Pill {
            anchors.verticalCenter: parent.verticalCenter
            label: "IS"
            tone: core.status.aprsis_connected ? Theme.good : Theme.muted
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: !!core.status.last_beacon
            text: "Bcn " + (core.status.last_beacon ? Theme.ago(core.status.last_beacon) : "")
            color: Theme.muted
            font.pixelSize: Theme.small
        }
        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            visible: core.unread > 0
            height: strip.height * 0.62
            width: Math.max(height, unreadText.implicitWidth + 2 * Theme.gap)
            radius: height / 2
            color: Theme.accent
            Text {
                id: unreadText
                anchors.centerIn: parent
                text: "✉ " + core.unread
                color: Theme.bg
                font.pixelSize: Theme.small
                font.bold: true
            }
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: Qt.formatTime(new Date(Theme.now * 1000), "hh:mm")
            color: Theme.text
            font.pixelSize: Theme.large
            font.bold: true
        }
    }
}
