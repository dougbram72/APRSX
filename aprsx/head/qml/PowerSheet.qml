import QtQuick

// Shut down or restart the Pi. Opening this sheet is the confirmation step.
Sheet {
    id: sheet
    title: "Power"

    Column {
        anchors.centerIn: parent
        width: Math.min(parent.width, 520 * Theme.u)
        spacing: Theme.gap * 2

        Text {
            width: parent.width
            text: "The radio and all services stop cleanly before the Pi turns off."
            color: Theme.muted
            font.pixelSize: Theme.body
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
        }
        BigButton {
            width: parent.width
            height: 84 * Theme.u
            text: "Shut down"
            font.pixelSize: Theme.large
            tint: Theme.bad
            onClicked: { sheet.close(); core.power("shutdown") }
        }
        BigButton {
            width: parent.width
            height: 72 * Theme.u
            text: "Restart"
            onClicked: { sheet.close(); core.power("reboot") }
        }
    }
}
