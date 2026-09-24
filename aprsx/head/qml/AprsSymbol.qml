import QtQuick

// One APRS symbol from the sprite sheets in aprsx/symbols (see its README).
// table "/" = primary, "\" = alternate, 0-9/A-Z = alternate with that overlay.
Item {
    id: sym
    property string table: "/"
    property string code: ""
    implicitWidth: 40 * Theme.u
    implicitHeight: implicitWidth

    readonly property int index: code.length === 1 ? code.charCodeAt(0) - 33 : -1
    readonly property bool valid: index >= 0 && index < 96
    readonly property bool primary: table === "/"
    readonly property string overlay: /^[0-9A-Z]$/.test(table) ? table : ""

    function cell(i) { return Qt.rect((i % 16) * 64, Math.floor(i / 16) * 64, 64, 64) }

    Image {
        anchors.fill: parent
        visible: sym.valid
        source: sym.valid ? symbolDir + (sym.primary ? "/aprs-symbols-64-0.png"
                                                     : "/aprs-symbols-64-1.png") : ""
        sourceClipRect: sym.cell(sym.index)
        smooth: true
        mipmap: true
    }
    Image {
        anchors.fill: parent
        visible: sym.valid && sym.overlay !== ""
        source: visible ? symbolDir + "/aprs-symbols-64-2.png" : ""
        sourceClipRect: sym.cell(sym.overlay.length ? sym.overlay.charCodeAt(0) - 33 : 0)
        smooth: true
        mipmap: true
    }
}
