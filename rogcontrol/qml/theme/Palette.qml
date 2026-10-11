import QtQuick
QtObject {
    property string appearance: "dark"
    readonly property bool light: appearance === "light" || (appearance === "system" && Qt.styleHints.colorScheme === Qt.Light)
    readonly property color window: light ? "#c9cbd0" : "#050506"
    readonly property color panel: light ? "#dde0e4" : "#101012"
    readonly property color sidebar: light ? "#bfc2c8" : "#09090b"
    readonly property color text: light ? "#202024" : "#eceef1"
    readonly property color mutedText: light ? "#50535d" : "#a5a7b0"
    readonly property color border: light ? "#aeb2bb" : "#28282d"
    readonly property color accent: "#ef1745"
    readonly property color hover: light ? "#ccd0d7" : "#1d1d22"
    readonly property color selected: light ? "#e6c7cf" : "#281018"
}
