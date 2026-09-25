import QtQuick
import QtQuick.Controls

ApplicationWindow {
    id: app
    visible: false          // Python shows it (windowed or full screen)
    width: 800
    height: 480
    color: Theme.bg
    title: "APRS-X"

    Binding { target: Theme; property: "u"; value: Math.min(app.width / 800, app.height / 480) }
    Timer {
        interval: 5000; running: true; repeat: true; triggeredOnStart: true
        onTriggered: Theme.now = Date.now() / 1000
    }

    Connections {
        target: core
        function onToast(text) { toast.show(text) }
        // New incoming messages jump to the front.
        function onMessageArrived(id) { carousel.currentIndex = 0 }
        function onPowering(action) { goingDown.action = action }
    }

    StatusStrip {
        id: strip
        anchors.top: parent.top
        width: parent.width
        height: 52 * Theme.u
        onPowerRequested: power.open()
        onMeshRequested: mesh.open()
    }

    // --- message carousel --------------------------------------------------

    Item {
        id: main
        anchors.top: strip.bottom
        anchors.bottom: bar.top
        width: parent.width

        readonly property real sideW: 70 * Theme.u

        BigButton {
            id: newer
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Theme.gap / 2
            width: main.sideW
            height: parent.height - Theme.gap
            text: "◀"
            font.pixelSize: Theme.huge
            enabled: carousel.currentIndex > 0
            onClicked: carousel.decrementCurrentIndex()
        }

        ListView {
            id: carousel
            objectName: "carousel"
            anchors.left: newer.right
            anchors.right: older.left
            anchors.top: parent.top
            anchors.bottom: position.top
            anchors.topMargin: Theme.gap / 2
            orientation: ListView.Horizontal
            snapMode: ListView.SnapOneItem
            highlightRangeMode: ListView.StrictlyEnforceRange
            highlightMoveDuration: 250
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            model: core.messages
            // A model reset leaves no current item; always show a message when there is one.
            onCountChanged: if (currentIndex < 0 && count > 0) currentIndex = 0
            Connections {
                target: core.messages
                // A reload (after connecting) starts again from the newest card.
                function onModelReset() { carousel.currentIndex = 0 }
            }
            delegate: MessageCard {
                width: carousel.width
                height: carousel.height
            }
        }

        Text {
            anchors.centerIn: carousel
            visible: carousel.count === 0
            text: core.connected ? "No messages yet" : "Waiting for aprsx-core…"
            color: Theme.muted
            font.pixelSize: Theme.large
        }

        Text {
            id: position
            anchors.bottom: parent.bottom
            anchors.horizontalCenter: carousel.horizontalCenter
            height: implicitHeight + Theme.gap / 2
            text: carousel.count ? (carousel.currentIndex + 1) + " / " + carousel.count
                                   + "   ◀ newer · older ▶" : ""
            color: Theme.muted
            font.pixelSize: Theme.small
        }

        BigButton {
            id: older
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Theme.gap / 2
            width: main.sideW
            height: parent.height - Theme.gap
            text: "▶"
            font.pixelSize: Theme.huge
            enabled: carousel.currentIndex < carousel.count - 1
            onClicked: carousel.incrementCurrentIndex()
        }

        // An unread message counts as read once it has been on screen a moment.
        Timer {
            interval: 1500
            running: !!carousel.currentItem && carousel.currentItem.unread
                     && !carousel.moving && app.active
            onTriggered: carousel.currentItem.markRead()
        }
    }

    // --- button bar --------------------------------------------------------

    Row {
        id: bar
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottomMargin: Theme.gap / 2
        width: parent.width - Theme.gap
        height: 72 * Theme.u
        spacing: Theme.gap / 2
        readonly property real cellW: (width - 4 * spacing) / 5

        BigButton {
            width: bar.cellW; height: bar.height
            text: "Beacon"
            enabled: !!core.status.can_beacon
            onClicked: core.beacon()
        }
        BigButton {
            width: bar.cellW; height: bar.height
            text: "Reply"
            enabled: !!carousel.currentItem
            onClicked: {
                const card = carousel.currentItem
                if (card.mesh) compose.openForMesh(card.conv, card.replyName)
                else compose.openFor(card.peer)
            }
        }
        BigButton {
            width: bar.cellW; height: bar.height
            text: "Quick Msg"
            // Quick Msg is APRS only.
            onClicked: quick.openFor(carousel.currentItem && !carousel.currentItem.mesh
                                     ? carousel.currentItem.peer : "")
        }
        BigButton {
            width: bar.cellW; height: bar.height
            text: "Keyboard"
            onClicked: compose.openFor("")
        }
        BigButton {
            width: bar.cellW; height: bar.height
            text: "Stations"
            onClicked: stations.open()
        }
    }

    Toast {
        id: toast
        z: 5
        anchors.bottom: bar.top
        anchors.bottomMargin: Theme.gap
        anchors.horizontalCenter: parent.horizontalCenter
    }

    // --- overlays ----------------------------------------------------------

    QuickMsgSheet {
        id: quick
        objectName: "quick"
        anchors.fill: parent
        z: 10
        onCustom: (to) => compose.openFor(to)
    }
    ComposeSheet {
        id: compose
        objectName: "compose"
        anchors.fill: parent
        z: 11   // also opens over the mesh sheet
    }
    StationsSheet {
        id: stations
        objectName: "stations"
        anchors.fill: parent
        z: 10
        onPicked: (name) => { stations.close(); quick.openFor(name) }
    }

    MeshSheet {
        id: mesh
        objectName: "mesh"
        anchors.fill: parent
        z: 10
        onCompose: (conv, name) => compose.openForMesh(conv, name)
    }

    PowerSheet {
        id: power
        objectName: "power"
        anchors.fill: parent
        z: 10
    }

    // Covers everything once the core has started a shutdown or reboot.
    Rectangle {
        id: goingDown
        property string action
        objectName: "goingDown"
        anchors.fill: parent
        z: 30
        visible: action !== ""
        color: Theme.bg
        MouseArea { anchors.fill: parent }   // nothing to press any more

        Column {
            anchors.centerIn: parent
            width: parent.width - 4 * Theme.gap
            spacing: Theme.gap * 2
            Text {
                width: parent.width
                text: goingDown.action === "reboot" ? "Restarting…" : "Shutting down…"
                color: Theme.accent
                font.pixelSize: Theme.huge
                font.bold: true
                horizontalAlignment: Text.AlignHCenter
            }
            Text {
                width: parent.width
                visible: goingDown.action === "shutdown"
                text: "Wait for the green light on the Pi to stop flashing before removing power."
                color: Theme.text
                font.pixelSize: Theme.body
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }

    Loader {
        objectName: "keyboard"
        z: 20
        active: vkbEnabled
        source: "KeyboardPanel.qml"
    }
}
