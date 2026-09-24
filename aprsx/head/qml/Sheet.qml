import QtQuick

// Full-window overlay with a title bar and a close button. Put content in `body`.
Rectangle {
    id: sheet
    property string title
    default property alias body: content.data
    property alias headerExtra: extra.data
    signal closed()

    visible: false
    color: Theme.bg
    function open() { visible = true }
    function close() { visible = false; closed() }

    MouseArea { anchors.fill: parent }   // swallow touches meant for the screen below

    Rectangle {
        id: header
        width: parent.width
        height: 58 * Theme.u
        color: Theme.panel
        Text {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: Theme.gap * 1.5
            text: sheet.title
            color: Theme.text
            font.pixelSize: Theme.large
            font.bold: true
        }
        Row {
            id: extra
            anchors.right: closeButton.left
            anchors.rightMargin: Theme.gap
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.gap
        }
        BigButton {
            id: closeButton
            anchors.right: parent.right
            anchors.rightMargin: Theme.gap
            anchors.verticalCenter: parent.verticalCenter
            height: parent.height - Theme.gap
            width: 110 * Theme.u
            text: "Close"
            onClicked: sheet.close()
        }
    }

    Item {
        id: content
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: Theme.gap
    }
}
