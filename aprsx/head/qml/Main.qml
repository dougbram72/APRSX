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
    }

    StatusStrip {
        id: strip
        anchors.top: parent.top
        width: parent.width
        height: 52 * Theme.u
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
            onTriggered: core.markRead(carousel.currentItem.peer)
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
            // Manual beaconing arrives in phase 5; the core will report when it can beacon.
            enabled: !!core.status.can_beacon
        }
        BigButton {
            width: bar.cellW; height: bar.height
            text: "Reply"
            enabled: !!carousel.currentItem
            onClicked: compose.openFor(carousel.currentItem.peer)
        }
        BigButton {
            width: bar.cellW; height: bar.height
            text: "Quick Msg"
            onClicked: quick.openFor(carousel.currentItem ? carousel.currentItem.peer : "")
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
        z: 10
    }
    StationsSheet {
        id: stations
        objectName: "stations"
        anchors.fill: parent
        z: 10
        onPicked: (name) => { stations.close(); quick.openFor(name) }
    }

    Loader {
        objectName: "keyboard"
        z: 20
        active: vkbEnabled
        source: "KeyboardPanel.qml"
    }
}
