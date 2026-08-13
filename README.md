# Agent Desktops

Agent Desktops gives GUI automation agents isolated Hyprland desktops. An agent can start Electron, QEMU, browsers, development servers, and other windowed processes without changing the user's active desktop.

The repository contains:

- A native Hyprland plug-in that places and removes isolated workspaces safely.
- A Hyprland Lua module for headless output rules and safe workspace navigation.
- The `omarchy-agent-desktop` command for lifecycle, launch, capture, input, and cleanup.
- An Omarchy Quickshell widget for manual access.
- A lightweight Omarchy bar for virtual desktop screenshots.
- An opt-in Codex skill for isolated launch workflows.

The current build targets Hyprland 0.56.2. The loader rebuilds the native plug-in against the installed Hyprland ABI and matching development headers.

## Install the package links

Clone the repository, then run:

```bash
./install.sh
```

The installer creates symlinks only when the target path is empty. It does not replace files or edit shared Hyprland and Omarchy configuration.

Read [the integration guide](docs/integration.md) before you create a desktop. The safety filters in that guide prevent the headless outputs from entering normal workspace, monitor, notification, and bar flows.

## Start and check the service

```bash
~/.config/hypr/scripts/agent-desktops-start
omarchy-agent-desktop doctor
```

The loader requires `Hyprland`, matching Hyprland headers, CMake, Ninja, `pkg-config`, Lua development files, `jq`, and `hyprctl`. Capture requires `grim`. Manual widget clipboard capture also requires `wl-copy`.

## Run an Electron project

```bash
desktop_id="new-project-5"
project_dir="$HOME/Documents/New project 5"

omarchy-agent-desktop create "New project 5" \
  --id "$desktop_id" \
  --mode 1920x1080@60

omarchy-agent-desktop run "$desktop_id" -- \
  env -C "$project_dir" bun run dev

omarchy-agent-desktop windows "$desktop_id" --json
omarchy-agent-desktop screenshot "$desktop_id" ./agent-desktop.png
omarchy-agent-desktop remove "$desktop_id" --force
```

Agent screenshots write a file only. The clipboard option is reserved for a person who clicks the widget screenshot button.

## Agent policy

Install the [agent-desktops skill](skills/agent-desktops/SKILL.md) to teach Codex the complete workflow. The skill starts only when the user explicitly asks for a virtual, agent, headless, or isolated desktop. A command that can create a window does not trigger the skill by itself.

## Validate

```bash
PYTHONDONTWRITEBYTECODE=1 tests/test-agent-desktops
cmake -S . -B build -G Ninja
cmake --build build
```

The native build requires the exact Hyprland headers for the installed compositor.

## Upgrades

The loader keeps immutable ABI and source-hash builds in the cache. If an older native revision is already loaded, restart Hyprland before you use the new revision. A configuration reload cannot replace native plug-in code safely.

## Hyprland references

- [Lua configuration](https://wiki.hypr.land/Configuring/Start/)
- [Using hyprctl](https://wiki.hypr.land/Configuring/Advanced-and-Cool/Using-hyprctl/)
- [Plug-in development](https://wiki.hypr.land/Plugins/Development/)
- [Permissions](https://wiki.hypr.land/Configuring/Advanced-and-Cool/Permissions/)

The virtual desktop design was also informed by [hyprland-virtual-desktops](https://github.com/levnikmyskin/hyprland-virtual-desktops).
