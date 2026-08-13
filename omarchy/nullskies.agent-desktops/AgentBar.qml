import Quickshell
import Quickshell.Wayland
import QtQuick
import qs.Commons

// A lightweight shell bar for AGENT-* outputs. The normal Omarchy bar owns a
// complete widget tree for each physical screen. Repeating that tree on every
// headless output can exhaust process pipes and file descriptors. This surface
// keeps the expected bar in screenshots without starting per-output commands.
PanelWindow {
  id: root

  property var hostBar: null
  property var desktopService: null
  property var targetScreen: null

  readonly property bool ready: hostBar !== null && targetScreen !== null
  readonly property bool vertical: ready && hostBar.vertical === true
  readonly property int barSize: ready
    ? Number(hostBar.barSize)
    : (vertical ? Style.bar.sizeVertical : Style.bar.sizeHorizontal)
  readonly property var desktop: {
    var values = desktopService && Array.isArray(desktopService.desktops)
      ? desktopService.desktops : []
    var outputName = targetScreen ? String(targetScreen.name || "") : ""
    for (var i = 0; i < values.length; i++) {
      if (values[i] && String(values[i].output || "") === outputName) return values[i]
    }
    return null
  }
  readonly property string desktopLabel: desktop && desktop.label
    ? String(desktop.label) : qsTr("Virtual desktop")
  readonly property int windowCount: desktop ? Math.max(0, Number(desktop.windowCount) || 0) : 0
  readonly property string windowText: windowCount === 1
    ? qsTr("1 window") : qsTr("%1 windows").arg(windowCount)
  readonly property string horizontalClockFormat: {
    if (!ready || typeof hostBar.layoutEntries !== "function" || typeof hostBar.entryId !== "function")
      return "ddd d MMM  HH:mm"
    var entries = hostBar.layoutEntries("center")
    for (var i = 0; i < entries.length; i++) {
      if (hostBar.entryId(entries[i]) !== "omarchy.clock") continue
      var settings = typeof hostBar.entrySettings === "function" ? hostBar.entrySettings(entries[i]) : entries[i]
      return String(settings && settings.format ? settings.format : "ddd d MMM  HH:mm")
    }
    return "ddd d MMM  HH:mm"
  }
  readonly property string clockText: Qt.formatDateTime(
    clock.date, vertical ? "HH\nmm" : horizontalClockFormat)
  readonly property color foreground: ready ? hostBar.barForeground : Color.bar.text
  readonly property color backgroundColor: ready ? hostBar.background : Color.bar.background

  screen: targetScreen
  visible: ready && hostBar.barHidden !== true
  color: ready && hostBar.transparent === true ? "transparent" : backgroundColor
  implicitWidth: vertical ? barSize : 0
  implicitHeight: vertical ? 0 : barSize
  surfaceFormat.opaque: false

  anchors {
    top: ready && (hostBar.position === "top" || vertical)
    bottom: ready && (hostBar.position === "bottom" || vertical)
    left: ready && (hostBar.position === "left" || !vertical)
    right: ready && (hostBar.position === "right" || !vertical)
  }

  WlrLayershell.namespace: "omarchy-agent-bar"
  WlrLayershell.layer: WlrLayer.Top
  WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

  SystemClock {
    id: clock
    precision: SystemClock.Minutes
  }

  Loader {
    anchors.fill: parent
    sourceComponent: root.vertical ? verticalContent : horizontalContent
  }

  Component {
    id: horizontalContent

    Item {
      anchors.fill: parent

      Row {
        anchors.left: parent.left
        anchors.leftMargin: Style.space(12)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(7)

        Text {
          text: "󱂬"
          color: root.foreground
          font.family: root.ready ? root.hostBar.fontFamily : Style.font.family
          font.pixelSize: Style.font.title
        }

        Text {
          text: root.desktopLabel
          color: root.foreground
          font.family: root.ready ? root.hostBar.fontFamily : Style.font.family
          font.pixelSize: Style.font.body
          font.bold: true
          elide: Text.ElideRight
        }
      }

      Text {
        anchors.centerIn: parent
        text: root.clockText
        color: root.foreground
        font.family: root.ready ? root.hostBar.fontFamily : Style.font.family
        font.pixelSize: Style.font.body
      }

      Row {
        anchors.right: parent.right
        anchors.rightMargin: Style.space(12)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(7)

        Text {
          text: root.windowText
          color: root.foreground
          opacity: 0.72
          font.family: root.ready ? root.hostBar.fontFamily : Style.font.family
          font.pixelSize: Style.font.bodySmall
        }

        Text {
          text: qsTr("HEADLESS")
          color: root.foreground
          opacity: 0.72
          font.family: root.ready ? root.hostBar.fontFamily : Style.font.family
          font.pixelSize: Style.font.caption
          font.bold: true
        }
      }
    }
  }

  Component {
    id: verticalContent

    Item {
      anchors.fill: parent

      Text {
        anchors.top: parent.top
        anchors.topMargin: Style.space(10)
        anchors.horizontalCenter: parent.horizontalCenter
        text: "󱂬"
        color: root.foreground
        font.family: root.ready ? root.hostBar.fontFamily : Style.font.family
        font.pixelSize: Style.font.title
      }

      Text {
        anchors.centerIn: parent
        text: root.clockText
        color: root.foreground
        horizontalAlignment: Text.AlignHCenter
        font.family: root.ready ? root.hostBar.fontFamily : Style.font.family
        font.pixelSize: Style.font.bodySmall
      }

      Text {
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Style.space(10)
        anchors.horizontalCenter: parent.horizontalCenter
        text: String(root.windowCount)
        color: root.foreground
        opacity: 0.72
        font.family: root.ready ? root.hostBar.fontFamily : Style.font.family
        font.pixelSize: Style.font.caption
      }
    }
  }
}
