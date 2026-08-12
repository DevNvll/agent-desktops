import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

Item {
  id: root

  property var settings: ({})
  property bool installed: false
  property bool backendAvailable: false
  property bool refreshing: false
  property bool busy: false
  property string busyId: ""
  property string busyAction: ""
  property string lastError: ""
  property string statusText: qsTr("Checking agent desktops...")
  property var desktops: []
  property string _listOutput: ""
  property string _listError: ""
  property string _actionOutput: ""
  property string _actionError: ""

  readonly property int refreshIntervalSec: intSetting("refreshIntervalSec", 3, 2, 60)
  readonly property int enteredCount: desktops.filter(function(desktop) { return desktop.entered }).length

  function configure(nextSettings) {
    settings = nextSettings || ({})
  }

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function intSetting(name, fallback, minimum, maximum) {
    var value = parseInt(String(setting(name, fallback)), 10)
    if (!isFinite(value)) value = fallback
    return Math.max(minimum, Math.min(maximum, value))
  }

  function desktop(id) {
    for (var i = 0; i < desktops.length; i++) {
      if (desktops[i].id === id) return desktops[i]
    }
    return null
  }

  function refresh() {
    if (busy || actionProcess.running) return
    if (!installed) {
      if (!whichProcess.running) whichProcess.running = true
      return
    }
    if (listProcess.running) return
    _listOutput = ""
    _listError = ""
    refreshing = true
    listProcess.command = ["omarchy-agent-desktop", "list", "--json"]
    listProcess.running = true
  }

  function runAction(arguments, actionName, desktopId) {
    if (!installed || !backendAvailable || busy || actionProcess.running || listProcess.running) return false
    _actionOutput = ""
    _actionError = ""
    busy = true
    busyAction = actionName
    busyId = desktopId || ""
    lastError = ""
    statusText = actionName
    actionProcess.command = ["omarchy-agent-desktop"].concat(arguments)
    actionProcess.running = true
    return true
  }

  function enterDesktop(id) {
    var target = desktop(id)
    if (!target || target.entered || target.status !== "ready") return false
    return runAction(["enter", id], qsTr("Opening desktop..."), id)
  }

  function leaveDesktop(id) {
    if (id) {
      var target = desktop(id)
      if (!target || !target.entered) return false
    } else if (enteredCount === 0) {
      return false
    }
    var arguments = ["leave"]
    if (id) arguments.push(id)
    return runAction(arguments, qsTr("Returning desktop..."), id || "")
  }

  function removeDesktop(id) {
    if (!Model.isLiveDesktop(desktop(id))) return false
    return runAction(["remove", id, "--force"], qsTr("Removing desktop..."), id)
  }

  function screenshotDesktop(id) {
    if (!Model.isLiveDesktop(desktop(id))) return false
    return runAction(["screenshot", id], qsTr("Taking screenshot..."), id)
  }

  function screenshotDesktopToClipboard(id) {
    if (!Model.isLiveDesktop(desktop(id))) return false
    return runAction(["screenshot", id, "--clipboard"], qsTr("Taking screenshot..."), id)
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Timer {
    id: delayedRefresh
    interval: 250
    repeat: false
    onTriggered: root.refresh()
  }

  Process {
    id: whichProcess
    running: false
    command: ["which", "omarchy-agent-desktop"]
    onExited: function(exitCode) {
      root.installed = exitCode === 0
      if (root.installed) {
        root.refresh()
      } else {
        root.backendAvailable = false
        root.refreshing = false
        root.desktops = []
        root.lastError = ""
        root.statusText = qsTr("Agent desktop command is not installed")
      }
    }
  }

  Process {
    id: listProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: listStdout
      waitForEnd: true
      onStreamFinished: root._listOutput = text
    }
    stderr: StdioCollector {
      id: listStderr
      waitForEnd: true
      onStreamFinished: root._listError = text
    }
    onExited: function(exitCode) {
      root.refreshing = false
      var stdout = String(listStdout.text || root._listOutput || "")
      var stderr = String(listStderr.text || root._listError || "")
      if (exitCode !== 0) {
        root.backendAvailable = false
        root.desktops = []
        root.statusText = qsTr("Agent desktops are unavailable")
        root.lastError = Model.elide(stderr || stdout || qsTr("The list command failed"))
        return
      }
      var parsed = Model.parseList(stdout)
      if (parsed.error !== "") {
        root.backendAvailable = false
        root.desktops = []
        root.lastError = parsed.error
        root.statusText = qsTr("Could not read agent desktops")
        return
      }
      if (!parsed.backendAvailable) {
        root.backendAvailable = false
        root.desktops = []
        root.lastError = ""
        root.statusText = qsTr("Agent desktops are unavailable")
        return
      }
      root.backendAvailable = true
      root.desktops = parsed.desktops
      root.lastError = ""
      var liveCount = Model.liveDesktops(root.desktops).length
      root.statusText = liveCount === 0
        ? qsTr("No active agent desktops")
        : qsTr("%1 active agent desktop(s)").arg(liveCount)
    }
  }

  Process {
    id: actionProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: actionStdout
      waitForEnd: true
      onStreamFinished: root._actionOutput = text
    }
    stderr: StdioCollector {
      id: actionStderr
      waitForEnd: true
      onStreamFinished: root._actionError = text
    }
    onExited: function(exitCode) {
      var stdout = String(actionStdout.text || root._actionOutput || "")
      var stderr = String(actionStderr.text || root._actionError || "")
      root.busy = false
      root.busyId = ""
      root.busyAction = ""
      if (exitCode === 0) {
        root.lastError = ""
        root.statusText = Model.elide(stdout) || qsTr("Action complete")
      } else {
        root.statusText = qsTr("Agent desktop action failed")
        root.lastError = Model.elide(stderr || stdout || qsTr("The command failed"))
      }
      delayedRefresh.restart()
    }
  }
}
