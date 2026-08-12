# Command reference

The command is `omarchy-agent-desktop`.

## Lifecycle

```text
create NAME [--id ID] [--mode WIDTHxHEIGHT@RATE] [--json]
list [--json]
status ID [--json]
restore
remove ID [--force] [--kill]
doctor
```

- `create` makes one named headless output and one special workspace. It accepts up to 16 desktops.
- An ID must start with a lower-case letter or digit. It can contain lower-case letters, digits, and hyphens, and it can have up to 40 characters.
- `list` reports saved and live state.
- `ready` means the workspace is on its headless output.
- `entered` means the workspace is temporarily on a physical output.
- `stale` means the output exists but the special workspace is not active. Commands repair a recoverable stale workspace before they use it.
- `offline` means the saved output does not exist.
- `restore` recreates saved outputs and returns entered desktops to their headless outputs.
- `remove` refuses to remove a desktop with windows unless `--force` or `--kill` is present.
- `doctor` checks `hyprctl`, `grim`, and the native plug-in.

## Applications and windows

```text
run ID -- COMMAND [ARG ...]
exec ID -- COMMAND [ARG ...]
windows ID [--json]
adopt ID WINDOW_ADDRESS
close-window ID [--window WINDOW_ADDRESS] [--kill]
```

- `run` and `exec` are the same operation.
- Keep each command argument separate. The command safely quotes the final application string for Hyprland.
- The command has no working-directory option. Use `env -C DIRECTORY COMMAND...` for a direct command in a project directory.
- Use `bash -lc` only when you need shell operations, such as redirection or a pipeline. Pass directory and file paths as positional arguments.
- Hyprland redirects standard output and standard error to `/dev/null`. Redirect inside the launched command when logs are required.
- `run` returns after Hyprland accepts the process. Use `windows` and logs to detect readiness.
- The process gets `AGENT_DESKTOP_ID`, `AGENT_DESKTOP_OUTPUT`, and a Hyprland execution token.
- Descendants normally inherit these values. This lets a development command start its first Electron or other GUI window on the headless workspace.
- Hyprland consumes the execution rule after the first matching window. Inspect and adopt later windows when necessary.
- Environment clearing, existing-instance forwarding, portals, and external service managers can lose the token.
- `windows` returns only windows on the selected agent workspace.
- `adopt` moves one exact window address to the workspace without following or focusing it.
- Before `adopt`, verify the address, PID, class, and title with `hyprctl -j clients`. Never adopt by a broad class or title match.
- `close-window` selects the most recent desktop window when no address is present.
- Targeted close, kill, move, and key operations recheck the exact window and workspace inside one compositor call.

### Project path with spaces

```bash
desktop_id="new-project-5"
project_dir="$HOME/Documents/New project 5"

omarchy-agent-desktop create "New project 5" \
  --id "$desktop_id" \
  --mode 1920x1080@60

omarchy-agent-desktop run "$desktop_id" -- \
  env -C "$project_dir" bun run dev
```

### Project launch with a log

```bash
log_dir="${XDG_RUNTIME_DIR:-/tmp}/agent-desktops/$desktop_id"
log_file="$log_dir/dev.log"
mkdir -p "$log_dir"

omarchy-agent-desktop run "$desktop_id" -- \
  bash -lc 'cd "$1" && exec bun run dev >>"$2" 2>&1' \
  agent-launch "$project_dir" "$log_file"

tail -n 200 "$log_file"
```

`bash -lc` uses the first argument after the command text as `$0`. The `agent-launch` value fills that position. The next arguments become `$1` and `$2`.

## Capture and input

```text
screenshot ID [DESTINATION] [--json]
key ID KEY [--mods MODIFIERS] [--window WINDOW_ADDRESS] [--json]
```

- `screenshot` uses `grim -o` with the output that currently shows the selected workspace.
- If no destination is present, it writes a time-stamped PNG below the runtime directory.
- Agent screenshots must stay file-only. Do not add `--clipboard`.
- The private `--clipboard` switch belongs only to the Omarchy widget screenshot button that a person clicks.
- `key` uses Hyprland `send_shortcut`. It checks desktop ownership before it sends input.
- `key` rejects XWayland windows because Hyprland cannot safely target them.
- Use modifier names such as `CTRL`, `ALT`, `SHIFT`, and `SUPER`.
- Use an application control interface for text entry, pointer work, complex sequences, and XWayland applications.

## Physical display access

```text
enter ID
leave [ID]
```

- `enter` shows the special workspace on the focused physical monitor.
- `enter` refuses to replace another special workspace.
- `leave` returns the special workspace to its named headless output.
- Only one agent desktop can be entered at a time.
- Use this access only for short visible work. It is not the same as a normal active-desktop launch.

## JSON fields

`list --json` returns this top-level format:

```json
{
  "version": 1,
  "backendAvailable": true,
  "desktops": []
}
```

Each desktop can contain these fields:

```text
id, label, output, workspace, mode, slot, createdAt,
activeMonitor, entered, online, status, windowCount, windows
```

Each window contains these fields:

```text
address, class, title, pid, mapped, xwayland, focusHistoryID
```

## Safe cleanup

```bash
desktop_id="codex-qemu-check"

if omarchy-agent-desktop status "$desktop_id" --json >/dev/null 2>&1; then
  omarchy-agent-desktop leave "$desktop_id" >/dev/null 2>&1 || true
  omarchy-agent-desktop remove "$desktop_id" --force
fi
```

- Do not add `--kill` to automatic cleanup.
- `--force` asks windows to close and waits for them. It does not force-kill them.
- Use `--kill` only with user approval because it can lose data.
- A window can close while its parent development server continues. Check the task-specific process after removal.
- Stop only the exact process tree that this task started. Do not use a broad `pkill`, class match, or process-name match.
- Never clean up a desktop that another agent owns.

## Recovery and limits

- Run `restore` after Hyprland starts again.
- If the desktop is stale, normal commands try to reopen its special workspace.
- If the desktop is offline, use `restore` before launch.
- XWayland targeted keys are rejected. Use an application-native interface.
- Privacy-protected windows can be blank in a screenshot. Do not disable privacy rules.
- If `grim` permission is missing after a configuration change, restart Hyprland.
- An application that forwards to an existing main-desktop instance can open there. Start a separate instance or profile, or adopt the exact verified window.
