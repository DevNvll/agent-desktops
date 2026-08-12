---
name: agent-desktops
description: Run window-creating processes on isolated Hyprland desktops, then inspect, control, capture, and close their windows without changing the user's active desktop. Use whenever an agent must start a command that opens or can spawn a GUI window, including Electron, browsers, QEMU, emulators, GUI tests, launchers, xdg-open, and Bun, npm, pnpm, or other development commands that start desktop windows. Skip this skill only when the user explicitly asks for the process or window to open normally on the active or main desktop.
---

# Agent Desktops

Run windowed processes on named headless outputs. Keep the user's active desktop, focus, pointer, and clipboard unchanged.

Read [command-reference.md](references/command-reference.md) for full command syntax, state fields, launch limits, recovery, and cleanup details.

## Default launch policy

- Use this skill before you start any process that is expected, intended, or reasonably able to create a window.
- Include direct GUI programs and indirect launchers. Examples include `electron`, `qemu-system-*`, browsers, emulators, file viewers, GUI test runners, `xdg-open`, and development scripts that start Electron, Tauri, a browser, or another desktop application.
- Use it for `bun run dev`, `npm run dev`, `pnpm dev`, or similar commands when the script can open a window.
- Do not use it for a terminal-only process that cannot create a window.
- Skip isolation only when the user clearly asks to open the window normally, on the main desktop, or on the active desktop. A request to “open,” “run,” “test,” or “start” an application is not by itself a request for a normal desktop launch.
- Do not replace an isolated launch with a normal launch because isolation is less convenient. Explain a real limitation if one blocks the requested work.

## Main workflow

1. Check the service.

   ```bash
   omarchy-agent-desktop doctor
   ```

   If the plug-in is not loaded, start it once:

   ```bash
   ~/.config/hypr/scripts/agent-desktops-start
   ```

2. Make one unique desktop ID for the task. Use lower-case letters, digits, and hyphens. Do not use an ID that another agent owns.

   ```bash
   desktop_id="codex-electron-test"
   omarchy-agent-desktop create "Electron test" \
     --id "$desktop_id" \
     --mode 1920x1080@60
   ```

3. Start the complete process tree through `omarchy-agent-desktop run`. Keep `--` before the application command.

   For a command that does not need a project directory:

   ```bash
   omarchy-agent-desktop run "$desktop_id" -- electron ./main.js
   ```

   For a project directory, including a path with spaces, use `env -C`:

   ```bash
   project_dir="$HOME/Documents/New project 5"
   omarchy-agent-desktop run "$desktop_id" -- \
     env -C "$project_dir" bun run dev
   ```

   Start the parent development command through `run`, not Electron separately. A normal Bun, npm, or pnpm child inherits the launch token, so its first Electron window opens on the isolated workspace.

4. Redirect logs when you need them. Hyprland sends the launched command's standard output and standard error to `/dev/null` unless the command redirects them.

   ```bash
   log_dir="${XDG_RUNTIME_DIR:-/tmp}/agent-desktops/$desktop_id"
   log_file="$log_dir/dev.log"
   mkdir -p "$log_dir"

   omarchy-agent-desktop run "$desktop_id" -- \
     bash -lc 'cd "$1" && exec bun run dev >>"$2" 2>&1' \
     agent-launch "$project_dir" "$log_file"

   tail -n 200 "$log_file"
   ```

   In this `bash -lc` form, `agent-launch` supplies `$0`, the project directory is `$1`, and the log file is `$2`. Pass paths as separate arguments. Do not build an unquoted command from user-controlled text.

5. Wait for the window and verify its workspace.

   ```bash
   omarchy-agent-desktop windows "$desktop_id" --json
   ```

   Poll for a short time if the list is empty. The launch command reports that Hyprland accepted the process; it does not wait for application readiness.

6. Capture the headless output.

   ```bash
   mkdir -p "$PWD/artifacts"
   omarchy-agent-desktop screenshot "$desktop_id" \
     "$PWD/artifacts/agent-desktop.png"
   ```

   Agent screenshots write a PNG file only. Never add `--clipboard`. The clipboard option belongs only to the screenshot button that a person clicks in the Omarchy widget.

7. Use a targeted shortcut only when it is sufficient.

   ```bash
   omarchy-agent-desktop key "$desktop_id" F5
   omarchy-agent-desktop key "$desktop_id" A --mods CTRL --window 0x1234
   ```

   The command verifies that the window belongs to the selected desktop. It rejects XWayland windows because Hyprland cannot safely target them. Use an application control interface, such as Chrome DevTools for Electron or QMP for QEMU, for complex or XWayland input.

8. Close the application and remove only the desktop that you created.

   ```bash
   omarchy-agent-desktop windows "$desktop_id" --json
   omarchy-agent-desktop remove "$desktop_id" --force
   ```

   `--force` asks open windows to close. It does not kill them. Use `--kill` only when the user accepts possible data loss. Check that a development server stopped after its window closed. Stop only the exact process tree that you started; never use a broad `pkill` pattern.

## Routing behavior and limits

- `run` and `exec` are the same operation.
- The command adds `AGENT_DESKTOP_ID`, `AGENT_DESKTOP_OUTPUT`, and a Hyprland execution token to the launched environment.
- The workspace rule is silent. It blocks initial focus and later activation focus.
- A normal child process inherits the token. This routes the first matching window from a Bun, npm, pnpm, or similar Electron launch.
- Hyprland consumes its execution rule after the first matching window. A later secondary window can require adoption.
- A process can escape routing if it clears its environment, sends an open request to an existing application instance, or asks an external service, portal, or broker to create the window.
- Use a separate application instance when the program normally reuses an existing process. For a browser, use an isolated profile or the application's new-instance option when available.
- If a window escapes, identify its exact address and identity. Then adopt only that window:

  ```bash
  hyprctl -j clients
  omarchy-agent-desktop adopt "$desktop_id" 0xWINDOW_ADDRESS
  ```

  Do not select a user window by a broad class or title match.

## Interaction policy

- Keep the agent desktop headless for normal work.
- Prefer screenshots, exact window addresses, targeted shortcuts, and application control interfaces.
- Do not use `wtype`, `ydotool`, or global virtual input in the main Hyprland session. These tools can send input to the user's focused window.
- Use `enter` only when visible pointer interaction is necessary. This operation intentionally shows the isolated workspace on the focused physical monitor.
- Always use `leave` immediately after visible work.
- Do not treat `enter` as a normal desktop launch. It is temporary access to an already isolated desktop.
- Never remove, close, enter, or control a desktop that another agent owns.

## Visible access

```bash
omarchy-agent-desktop enter "$desktop_id"
# Do the short visible operation.
omarchy-agent-desktop leave "$desktop_id"
```

The Omarchy bar icon gives enter, leave, screenshot, and remove controls. It is on the left beside the normal workspace buttons. It is hidden when no live agent desktop exists. The widget does not create desktops. A person who clicks its screenshot button also copies the PNG to the clipboard.

## Recovery

- Run `omarchy-agent-desktop restore` after a compositor restart.
- Run `omarchy-agent-desktop list --json` before cleanup when state is uncertain.
- Recheck `windows --json` after a reload or a new application dialog.
- Adopt an escaped window only after you verify its exact address, PID, class, and title.
- Restart Hyprland if a screenshot reports that `grim` lacks permission. The Lua permission takes effect at compositor start.
- Keep Hyprland privacy rules active. Do not disable them to capture sensitive windows.
