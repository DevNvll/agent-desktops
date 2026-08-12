const assert = require("node:assert/strict")
const Model = require("./Model.js")

const parsed = Model.parseList(JSON.stringify({
  version: 1,
  backendAvailable: true,
  desktops: [
    { id: "second", label: "Second", slot: 2, status: "offline", windows: [] },
    { id: "first", label: " First  desktop ", slot: 0, status: "ready", windowCount: 1 }
  ]
}))

assert.equal(parsed.backendAvailable, true)
assert.deepEqual(parsed.desktops.map(desktop => desktop.id), ["first", "second"])
assert.equal(parsed.desktops[0].label, "First desktop")
assert.equal(Model.statusLabel(parsed.desktops[0]), "Headless")
assert.equal(Model.statusLabel({ entered: true, activeMonitor: "DP-1" }), "On DP-1")
assert.equal(Model.windowLabel(0), "0 windows")
assert.equal(Model.windowLabel(1), "1 window")
assert.equal(Model.isLiveDesktop(parsed.desktops[0]), true)
assert.equal(Model.isLiveDesktop(parsed.desktops[1]), false)
assert.deepEqual(Model.liveDesktops(parsed.desktops).map(desktop => desktop.id), ["first"])
assert.deepEqual(Model.liveDesktops(null), [])
assert.match(Model.parseList("not-json").error, /invalid JSON/)
assert.equal(Model.elide("one\n two", 100), "one two")
