import QtQuick

// Short notice above the button bar.
Rectangle {
    id: toast
    function show(text) { label.text = text; opacity = 1; hide.restart() }
    width: label.implicitWidth + 4 * Theme.gap
    height: label.implicitHeight + 2 * Theme.gap
    radius: height / 2
    color: Theme.panelHi
    border.color: Theme.accent
    border.width: Math.max(1, Theme.u)
    opacity: 0
    visible: opacity > 0
    Behavior on opacity { NumberAnimation { duration: 200 } }
    Timer { id: hide; interval: 2500; onTriggered: toast.opacity = 0 }
    Text {
        id: label
        anchors.centerIn: parent
        color: Theme.text
        font.pixelSize: Theme.body
    }
}
