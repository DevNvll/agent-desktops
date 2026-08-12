# Agent Desktops for Omarchy

This Omarchy Quattro plug-in manages live agent desktops from the left side of the bar, beside the normal workspace buttons.

The icon is hidden when there is no live agent desktop. The panel can:

- List desktop state and window counts.
- Show a desktop on the focused physical monitor.
- Return a desktop to its headless output.
- Take a screenshot. A click on the screenshot button also copies the PNG to the clipboard.
- Remove a desktop after a confirmation.

The panel does not create desktops. Agents create them with this command:

```bash
omarchy-agent-desktop create task-name --id task-name
```

The panel shows only live desktops. These desktops have the `ready` or `entered` state. A keyboard screenshot stays file-only.

## Requirements

- Omarchy Quattro with the Quickshell plug-in interface.
- Hyprland 0.56.2 or a compatible rebuild of the native plug-in.
- `hyprctl`, `grim`, `wl-copy`, Python 3, CMake, Ninja, `pkg-config`, and matching Hyprland headers.
- The `omarchy-agent-desktop` command from this repository.

The Hyprland start script builds the native plug-in against the current ABI. It then loads the plug-in and restores saved desktops.

## Validation

Run this command from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 tests/test-agent-desktops
```
