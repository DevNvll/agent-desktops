# Integration guide

The package links do not change shared configuration. Complete these steps before you create an agent desktop.

## Load the Lua module

Make sure Hyprland can find `~/.config/hypr/modules/agent_desktops.lua`, then load it near the start of the Lua configuration:

```lua
local agent_desktops = require("modules.agent_desktops")
```

Start the native loader once when Hyprland starts:

```lua
hl.on("hyprland.start", function()
    hl.exec_cmd("$HOME/.config/hypr/scripts/agent-desktops-start")
end)
```

The Lua module adds fixed rules for `AGENT-0` through `AGENT-15`. It keeps these outputs away from the physical monitor layout. It also grants `grim` screencopy permission.

## Keep agent workspaces out of normal navigation

Do not use unfiltered `e+1`, `e-1`, `previous`, or monitor-direction bindings. Use the module helpers for every equivalent binding:

```lua
keys.bind("SUPER + TAB", agent_desktops.bind_relative_workspace(1, false))
keys.bind("SUPER SHIFT + TAB", agent_desktops.bind_relative_workspace(-1, false))
keys.bind("SUPER CTRL + TAB", agent_desktops.bind_previous_human_workspace())

keys.bind("SUPER + 1", agent_desktops.bind_human_workspace("1", false))
keys.bind("SUPER SHIFT + 1", agent_desktops.bind_human_workspace("1", true))

keys.bind("SUPER CTRL + left", agent_desktops.bind_monitor("left", false))
keys.bind("SUPER SHIFT CTRL + left", agent_desktops.bind_monitor("left", true))
```

Use `bind_human_special` for normal special-workspace bindings. These helpers also stop normal workspace actions while an agent special workspace is temporarily visible.

## Filter Quickshell surfaces

Do not create the normal bar, bar drag surfaces, notification surfaces, or general desktop control surfaces on an `AGENT-*` screen. Filter `Quickshell.screens` before each related `Variants` object:

```qml
readonly property var physicalScreens: {
  var result = []
  var screens = Quickshell.screens || []
  for (var i = 0; i < screens.length; i++) {
    var screen = screens[i]
    if (screen && !String(screen.name || "").startsWith("AGENT-")) result.push(screen)
  }
  return result
}
```

Use `physicalScreens` as the model for all normal bar and notification output variants. Keep the lock surface on all outputs for security. A background surface can remain on the headless output so screenshots have a background.

Guard bar workspace scrolling when the monitor shows a workspace whose name starts with `special:agent:`.

## Filter generic focus and monitor tools

Any helper that finds a window by class or title must exclude workspaces that start with `special:agent:` or `__agent-base:`. Otherwise, a notification click can focus an agent window and move the pointer to the headless output.

Any monitor list, focused-monitor helper, display panel, or scaling tool must exclude names that start with `AGENT-`.

## Enable the Omarchy widget

Enable `nullskies.agent-desktops` as a bar widget and place it directly after the normal workspace widget in the left section. The widget has zero size when there is no live agent desktop.

The widget does not create desktops. Its screenshot button is the only workflow that adds `--clipboard`.

## Verify

Before a live test:

```bash
Hyprland --verify-config
omarchy-agent-desktop doctor
```

During a short test, confirm that:

- The two physical monitor positions do not change.
- The focused physical monitor does not change.
- No normal bar or notification surface is created on `AGENT-0`.
- The agent workspace does not appear in normal workspace navigation.
- A file-only agent screenshot does not change the clipboard.
- Removing the desktop returns the monitor list to its initial state.
