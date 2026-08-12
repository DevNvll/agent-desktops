import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root

  moduleName: "nullskies.agent-desktops"
  ipcTarget: "agent-desktops"

  readonly property var manager: bar && bar.shell ? bar.shell.serviceFor(moduleName) : null
  readonly property bool backendReady: !!manager && manager.backendAvailable === true
  readonly property bool actionsReady: backendReady && !manager.busy && !manager.refreshing
  readonly property var desktops: backendReady ? Model.liveDesktops(manager.desktops) : []
  readonly property int activeDesktopCount: desktops.length
  readonly property bool widgetAvailable: backendReady && activeDesktopCount > 0
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  property var pendingRemove: null
  property bool cursorActive: false
  property int selectedIndex: 0

  function syncSettings() {
    if (manager && typeof manager.configure === "function") manager.configure(settings)
  }

  function askToRemove(desktop) {
    if (!actionsReady || !Model.isLiveDesktop(desktop)) return
    pendingRemove = desktop
    removeDialog.selectedIndex = 0
    removeDialog.opened = true
  }

  function cancelRemove() {
    removeDialog.opened = false
    pendingRemove = null
    if (opened) Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function confirmRemove() {
    var desktop = pendingRemove
    removeDialog.opened = false
    pendingRemove = null
    if (desktop && actionsReady && Model.isLiveDesktop(desktop)) manager.removeDesktop(desktop.id)
    if (opened) Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function clampCursor() {
    selectedIndex = Math.max(0, Math.min(selectedIndex, Math.max(0, desktops.length - 1)))
  }

  function selectedDesktop() {
    return desktops.length > 0 ? desktops[selectedIndex] : null
  }

  function containsDesktop(id) {
    for (var i = 0; i < desktops.length; i++) {
      if (desktops[i].id === id) return true
    }
    return false
  }

  function scrollCursorIntoView() {
    if (!desktopRepeater || !cursorActive || selectedIndex < 0 || selectedIndex >= desktopRepeater.count) return
    var item = desktopRepeater.itemAt(selectedIndex)
    if (!item) return
    Qt.callLater(function() {
      if (!item || !desktopFlick) return
      var margin = Style.space(6)
      var point = item.mapToItem(desktopFlick.contentItem, 0, 0)
      var top = point.y
      var bottom = top + item.height
      var viewTop = desktopFlick.contentY
      var viewBottom = viewTop + desktopFlick.height
      var maximum = Math.max(0, desktopFlick.contentHeight - desktopFlick.height)
      if (top < viewTop + margin) desktopFlick.contentY = Math.max(0, top - margin)
      else if (bottom > viewBottom - margin)
        desktopFlick.contentY = Math.min(maximum, bottom + margin - desktopFlick.height)
    })
  }

  function moveCursor(dy) {
    if (dy === 0 || desktops.length === 0) return
    cursorActive = true
    selectedIndex = Math.max(0, Math.min(desktops.length - 1, selectedIndex + dy))
    scrollCursorIntoView()
  }

  function activateCursor() {
    var desktop = selectedDesktop()
    if (!desktop || !actionsReady) return
    if (desktop.entered) manager.leaveDesktop(desktop.id)
    else manager.enterDesktop(desktop.id)
  }

  function deleteSelected() {
    var desktop = selectedDesktop()
    if (desktop && actionsReady) askToRemove(desktop)
  }

  function open() {
    if (widgetAvailable) controller.show()
  }

  function toggle() {
    if (opened) close()
    else open()
  }

  visible: widgetAvailable
  implicitWidth: visible ? button.implicitWidth : 0
  implicitHeight: visible ? button.implicitHeight : 0

  onManagerChanged: syncSettings()
  onSettingsChanged: syncSettings()
  onDesktopsChanged: {
    clampCursor()
    if (pendingRemove && !containsDesktop(pendingRemove.id)) cancelRemove()
    Qt.callLater(root.scrollCursorIntoView)
  }
  onActiveDesktopCountChanged: if (activeDesktopCount === 0 && opened) close()
  onOpenedChanged: {
    if (!opened) {
      cancelRemove()
      return
    }
    syncSettings()
    if (manager) manager.refresh()
    cursorActive = false
    selectedIndex = 0
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.manager && root.manager.backendAvailable && root.activeDesktopCount > 0 ? "󱂬" : ""
    active: root.manager && (root.manager.enteredCount > 0 || root.manager.busy)
    tooltipText: qsTr("%1 active agent desktop(s)").arg(root.activeDesktopCount)
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton && root.manager) root.manager.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(430))
    contentHeight: panel.fittedContentHeight(content.implicitHeight, Style.space(580))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: removeDialog.opened
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) {
          root.cursorActive = true
          root.scrollCursorIntoView()
          return
        }
        root.moveCursor(dy)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onDeleteRequested: if (root.cursorActive) root.deleteSelected()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if ((text === "r" || text === "R") && root.manager) root.manager.refresh()
        else if ((text === "s" || text === "S") && root.cursorActive && root.actionsReady && root.selectedDesktop())
          root.manager.screenshotDesktop(root.selectedDesktop().id)
      }

      Column {
        id: content
        width: parent.width
        spacing: Style.space(10)

        Item {
          width: parent.width
          implicitHeight: title.implicitHeight

          Text {
            id: title
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("Agent desktops")
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.title
          }

          Row {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(5)

            PanelActionButton {
              visible: root.manager && root.manager.enteredCount > 0
              iconText: "󰌑"
              tooltipText: qsTr("Return to the main desktop")
              foreground: root.foreground
              fontFamily: root.fontFamily
              enabled: root.actionsReady
              onClicked: root.manager.leaveDesktop("")
            }

            PanelActionButton {
              iconText: "󰑐"
              tooltipText: qsTr("Refresh")
              foreground: root.foreground
              fontFamily: root.fontFamily
              enabled: root.manager && !root.manager.refreshing && !root.manager.busy
              onClicked: root.manager.refresh()
            }
          }
        }

        Text {
          width: parent.width
          text: root.manager ? root.manager.statusText : qsTr("Starting agent desktop service...")
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        Text {
          visible: root.manager && root.manager.lastError !== ""
          width: parent.width
          text: root.manager ? root.manager.lastError : ""
          color: root.urgent
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        PanelSeparator {
          width: parent.width
          foreground: root.foreground
        }

        Flickable {
          id: desktopFlick
          visible: root.desktops.length > 0
          width: parent.width
          implicitHeight: Math.min(desktopColumn.implicitHeight, Style.space(330))
          height: implicitHeight
          contentWidth: width
          contentHeight: desktopColumn.implicitHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          flickableDirection: Flickable.VerticalFlick

          Column {
            id: desktopColumn
            width: parent.width
            spacing: Style.space(6)

            Repeater {
              id: desktopRepeater
              model: root.desktops

              DesktopRow {
                required property var modelData
                required property int index
                width: desktopColumn.width
                desktop: modelData
                rowIndex: index
              }
            }
          }
        }
      }

      ConfirmDialog {
        id: removeDialog
        anchors.fill: parent
        focus: opened
        message: root.pendingRemove
          ? qsTr("Remove %1? Its open windows will be asked to close.").arg(root.pendingRemove.label)
          : ""
        cancelText: qsTr("Cancel")
        confirmText: qsTr("Remove")
        foreground: root.foreground
        fontFamily: root.fontFamily
        onCanceled: root.cancelRemove()
        onConfirmed: root.confirmRemove()
        onOpenedChanged: if (opened) Qt.callLater(function() { removeDialog.forceActiveFocus() })
        Keys.onPressed: function(event) { event.accepted = removeDialog.handleKey(event) }
      }
    }
  }

  component DesktopRow: CursorSurface {
    id: desktopRow
    required property var desktop
    required property int rowIndex

    foreground: root.foreground
    hasCursor: root.cursorActive && root.selectedIndex === rowIndex
    current: desktop.entered
    bordered: true
    implicitHeight: rowContent.implicitHeight + Style.space(14)

    Row {
      id: rowContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(8)
      anchors.rightMargin: Style.space(8)
      spacing: Style.space(8)

      Column {
        width: Math.max(Style.space(120), parent.width - actions.implicitWidth - parent.spacing)
        spacing: Style.space(2)

        Text {
          width: parent.width
          text: desktop.label
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: desktop.entered
          elide: Text.ElideRight
        }

        Text {
          width: parent.width
          text: Model.statusLabel(desktop) + " · " + Model.windowLabel(desktop.windowCount)
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      Row {
        id: actions
        spacing: Style.space(3)
        anchors.verticalCenter: parent.verticalCenter

        PanelActionButton {
          iconText: desktop.entered ? "󰌑" : "󰍹"
          tooltipText: desktop.entered ? qsTr("Return to the main desktop") : qsTr("Open on this monitor")
          foreground: root.foreground
          fontFamily: root.fontFamily
          enabled: root.actionsReady
          onClicked: {
            if (desktop.entered) root.manager.leaveDesktop(desktop.id)
            else root.manager.enterDesktop(desktop.id)
          }
        }

        PanelActionButton {
          iconText: "󰹑"
          tooltipText: qsTr("Take screenshot and copy it")
          foreground: root.foreground
          fontFamily: root.fontFamily
          enabled: root.actionsReady
          onClicked: root.manager.screenshotDesktopToClipboard(desktop.id)
        }

        PanelActionButton {
          iconText: "󰆴"
          tooltipText: qsTr("Remove desktop")
          foreground: root.foreground
          hoverColor: root.urgent
          fontFamily: root.fontFamily
          enabled: root.actionsReady
          onClicked: root.askToRemove(desktop)
        }
      }
    }
  }
}
