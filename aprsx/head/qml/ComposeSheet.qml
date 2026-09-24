import QtQuick
import QtQuick.Controls

// Free-form message with the on-screen keyboard.
Sheet {
    id: sheet
    title: "New message"
    readonly property int maxLength: 67

    function openFor(peer) {
        toField.text = peer || ""
        textField.text = ""
        open()
        ;(peer ? textField : toField).forceActiveFocus()
        Qt.inputMethod.show()   // focus from code doesn't raise the keyboard; a tap would
    }
    onClosed: { toField.focus = false; textField.focus = false; Qt.inputMethod.hide() }

    headerExtra: [
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: sheet.maxLength - textField.length
            color: sheet.maxLength - textField.length < 10 ? Theme.bad : Theme.muted
            font.pixelSize: Theme.body
        },
        BigButton {
            height: 50 * Theme.u
            width: 110 * Theme.u
            text: "Send"
            highlighted: enabled
            enabled: toField.text.trim() !== "" && textField.text.trim() !== ""
            onClicked: {
                core.sendMessage(toField.text, textField.text)
                sheet.close()
            }
        }
    ]

    component Field: TextField {
        font.pixelSize: Theme.large
        color: Theme.text
        placeholderTextColor: Theme.muted
        height: 56 * Theme.u
        leftPadding: Theme.gap
        background: Rectangle {
            radius: Theme.radius
            color: Theme.panel
            border.color: parent.activeFocus ? Theme.accent : Theme.border
            border.width: Math.max(1, 2 * Theme.u)
        }
    }

    Row {
        width: parent.width
        spacing: Theme.gap
        Field {
            id: toField
            width: 170 * Theme.u
            placeholderText: "To"
            font.family: Theme.mono
            maximumLength: 9
            inputMethodHints: Qt.ImhUppercaseOnly | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
            validator: RegularExpressionValidator { regularExpression: /[A-Za-z0-9-]*/ }
            onAccepted: textField.forceActiveFocus()
        }
        Field {
            id: textField
            width: parent.width - toField.width - parent.spacing
            placeholderText: "Message"
            maximumLength: sheet.maxLength
            inputMethodHints: Qt.ImhNoPredictiveText
            // APRS message text is printable ASCII without | ~ {
            validator: RegularExpressionValidator { regularExpression: /[ -z}]*/ }
        }
    }
}
