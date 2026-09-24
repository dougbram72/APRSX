pragma Singleton
import QtQuick

// Sizes scale with the window: u = 1 at 800x480. Never use raw pixel sizes.
QtObject {
    property real u: 1
    property double now: Date.now() / 1000   // ticked by Main.qml for "3m ago" labels

    readonly property color bg: "#0b0f14"
    readonly property color panel: "#161d27"
    readonly property color panelHi: "#202a37"
    readonly property color border: "#2c3848"
    readonly property color text: "#e8edf2"
    readonly property color muted: "#8593a3"
    readonly property color accent: "#f2a93b"
    readonly property color good: "#3fb950"
    readonly property color bad: "#f85149"
    readonly property color out: "#1f4f82"

    readonly property real small: 15 * u
    readonly property real body: 19 * u
    readonly property real large: 26 * u
    readonly property real huge: 34 * u
    readonly property real gap: 8 * u
    readonly property real radius: 8 * u
    readonly property string mono: "monospace"

    function ago(ts) {
        const s = Math.max(0, Math.round(now - ts))
        if (s < 60) return s + "s"
        if (s < 3600) return Math.floor(s / 60) + "m"
        if (s < 86400) return Math.floor(s / 3600) + "h"
        return Math.floor(s / 86400) + "d"
    }

    function clock(ts) {
        return Qt.formatTime(new Date(ts * 1000), "hh:mm")
    }

    function distance(km, units) {
        if (km === null || km === undefined) return ""
        return units === "metric" ? km.toFixed(1) + " km" : (km * 0.621371).toFixed(1) + " mi"
    }

    function compass(deg) {
        if (deg === null || deg === undefined) return ""
        return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(deg / 45) % 8]
    }
}
