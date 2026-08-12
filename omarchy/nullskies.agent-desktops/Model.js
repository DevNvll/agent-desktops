function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim()
}

function elide(value, maximumLength) {
  var text = cleanText(value)
  var maximum = Number(maximumLength) || 180
  return text.length > maximum ? text.substring(0, Math.max(0, maximum - 3)) + "..." : text
}

function normalizeDesktop(value) {
  var source = value && typeof value === "object" ? value : {}
  var windows = Array.isArray(source.windows) ? source.windows : []
  return {
    id: cleanText(source.id),
    label: cleanText(source.label) || cleanText(source.id) || "Agent desktop",
    output: cleanText(source.output),
    workspace: cleanText(source.workspace),
    mode: cleanText(source.mode),
    status: cleanText(source.status) || "offline",
    activeMonitor: cleanText(source.activeMonitor),
    entered: source.entered === true,
    online: source.online === true,
    windowCount: Math.max(0, Number(source.windowCount) || windows.length),
    windows: windows,
    slot: Math.max(0, Number(source.slot) || 0)
  }
}

function parseList(raw) {
  try {
    var value = JSON.parse(String(raw || ""))
    if (!value || value.version !== 1 || !Array.isArray(value.desktops))
      return { backendAvailable: false, desktops: [], error: "The desktop service returned an unsupported response." }
    var desktops = value.desktops.map(normalizeDesktop).filter(function(desktop) {
      return desktop.id !== ""
    })
    desktops.sort(function(left, right) {
      return left.slot !== right.slot ? left.slot - right.slot : left.id.localeCompare(right.id)
    })
    return {
      backendAvailable: value.backendAvailable === true,
      desktops: desktops,
      error: ""
    }
  } catch (error) {
    return { backendAvailable: false, desktops: [], error: "The desktop service returned invalid JSON." }
  }
}

function isLiveDesktop(desktop) {
  return !!desktop && (desktop.entered === true || desktop.status === "ready")
}

function liveDesktops(desktops) {
  return (Array.isArray(desktops) ? desktops : []).filter(isLiveDesktop)
}

function statusLabel(desktop) {
  if (!desktop) return "Unknown"
  if (desktop.entered) return "On " + (desktop.activeMonitor || "main display")
  if (desktop.status === "ready") return "Headless"
  if (desktop.status === "stale") return "Needs restore"
  return "Offline"
}

function windowLabel(count) {
  var value = Math.max(0, Number(count) || 0)
  return value === 1 ? "1 window" : String(value) + " windows"
}

if (typeof module !== "undefined") {
  module.exports = {
    cleanText: cleanText,
    elide: elide,
    normalizeDesktop: normalizeDesktop,
    parseList: parseList,
    isLiveDesktop: isLiveDesktop,
    liveDesktops: liveDesktops,
    statusLabel: statusLabel,
    windowLabel: windowLabel
  }
}
