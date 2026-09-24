import QtQuick
import QtQuick.Controls

// Touch-sized button for the button bar and sheets.
Button {
    id: control
    property color tint: Theme.panelHi
    font.pixelSize: Theme.body
    font.bold: true
    implicitHeight: 56 * Theme.u

    contentItem: Text {
        text: control.text
        font: control.font
        color: !control.enabled ? Theme.muted : control.highlighted ? Theme.bg : Theme.text
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        wrapMode: Text.WordWrap
        maximumLineCount: 2
    }
    background: Rectangle {
        radius: Theme.radius
        color: control.highlighted ? Theme.accent
             : control.down ? Qt.lighter(control.tint, 1.4) : control.tint
        border.color: control.highlighted ? Theme.accent : Theme.border
        border.width: Math.max(1, Theme.u)
        opacity: control.enabled ? 1 : 0.45
    }
}
