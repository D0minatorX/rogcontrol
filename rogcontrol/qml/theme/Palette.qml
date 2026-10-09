import QtQuick
QtObject {
    property string appearance: "dark"
    readonly property bool light: appearance === "light" || (appearance === "system" && Qt.styleHints.colorScheme === Qt.Light)
    readonly property color window: light ? "#f4f4f5" : "#000000"
    readonly property color panel: light ? "#ffffff" : "#080808"
    readonly property color sidebar: window
    readonly property color text: light ? "#202024" : "#eceef1"
    readonly property color mutedText: light ? "#62626a" : "#a5a7b0"
    readonly property color border: light ? "#d5d5d8" : "#242424"
    readonly property color accent: "#ee2445"
    readonly property color hover: light ? "#e6e6e9" : "#202020"
    readonly property color selected: light ? "#f9e1e6" : "#1b080d"
}
