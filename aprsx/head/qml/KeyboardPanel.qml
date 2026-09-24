import QtQuick
import QtQuick.VirtualKeyboard
import QtQuick.VirtualKeyboard.Settings

// Loaded on demand so a missing VirtualKeyboard module only loses the keyboard.
InputPanel {
    id: panel
    width: Window.width
    y: active ? Window.height - height : Window.height
    Behavior on y { NumberAnimation { duration: 150 } }

    // APRS text is plain ASCII: one US layout, no language switch key.
    Component.onCompleted: {
        VirtualKeyboardSettings.locale = "en_US"
        VirtualKeyboardSettings.activeLocales = ["en_US"]
    }
}
