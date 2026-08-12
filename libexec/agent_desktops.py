#!/usr/bin/env python3
"""Manage isolated Hyprland desktops for GUI automation agents."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator, Sequence


STATE_VERSION = 1
MAX_DESKTOPS = 16
COMMAND_TIMEOUT = 5.0
CAPTURE_TIMEOUT = 15.0
OUTPUT_WAIT_TIMEOUT = 5.0
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]+$")
MODE_PATTERN = re.compile(r"^(\d{3,4})x(\d{3,4})(?:@(\d{2,3}(?:\.\d+)?))?$", re.ASCII)
MONITOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$", re.ASCII)
TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class AgentDesktopError(RuntimeError):
    """A safe, user-facing command failure."""


def state_directory() -> Path:
    override = os.environ.get("AGENT_DESKTOP_STATE_DIR")
    if override:
        return Path(override).expanduser()
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return state_home / "omarchy/agent-desktops"


def screenshot_directory() -> Path:
    override = os.environ.get("AGENT_DESKTOP_SCREENSHOT_DIR")
    if override:
        return Path(override).expanduser()
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "agent-desktops"
    return Path(tempfile.gettempdir()) / f"agent-desktops-{os.getuid()}"


def default_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "desktops": []}


def validate_state(value: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(value, dict) or value.get("version") != STATE_VERSION:
        raise AgentDesktopError("the agent desktop state has an unsupported format")
    if set(value) != {"version", "desktops"}:
        raise AgentDesktopError("the agent desktop state has unknown fields")
    desktops = value.get("desktops")
    if not isinstance(desktops, list):
        raise AgentDesktopError("the agent desktop state has no desktop list")
    if len(desktops) > MAX_DESKTOPS:
        raise AgentDesktopError("the agent desktop state exceeds the desktop limit")

    result = default_state()
    seen_ids: set[str] = set()
    seen_slots: set[int] = set()
    migrated = False
    required = {
        "id",
        "label",
        "output",
        "workspace",
        "mode",
        "slot",
        "createdAt",
        "enteredMonitor",
    }
    for index, saved in enumerate(desktops):
        if not isinstance(saved, dict):
            raise AgentDesktopError(f"agent desktop state entry {index} is not an object")
        if not required.issubset(saved) or set(saved) - required:
            raise AgentDesktopError(f"agent desktop state entry {index} has invalid fields")

        desktop_id = saved["id"]
        if not isinstance(desktop_id, str):
            raise AgentDesktopError(f"agent desktop state entry {index} has an invalid ID")
        validate_id(desktop_id)
        if desktop_id in seen_ids:
            raise AgentDesktopError(f"agent desktop state has a duplicate ID: {desktop_id}")

        slot = saved["slot"]
        if type(slot) is not int or not 0 <= slot < MAX_DESKTOPS:
            raise AgentDesktopError(f"agent desktop {desktop_id} has an invalid slot")
        if slot in seen_slots:
            raise AgentDesktopError(f"agent desktop state has a duplicate slot: {slot}")

        label = saved["label"]
        if (
            not isinstance(label, str)
            or not label.strip()
            or len(label) > 160
            or any(ord(character) < 32 for character in label)
        ):
            raise AgentDesktopError(f"agent desktop {desktop_id} has an invalid label")
        mode = saved["mode"]
        if not isinstance(mode, str) or normalize_mode(mode) != mode:
            raise AgentDesktopError(f"agent desktop {desktop_id} has an invalid mode")
        workspace = saved["workspace"]
        if workspace != workspace_name(desktop_id):
            raise AgentDesktopError(f"agent desktop {desktop_id} has an invalid workspace")
        created_at = saved["createdAt"]
        if not isinstance(created_at, str) or not TIMESTAMP_PATTERN.fullmatch(created_at):
            raise AgentDesktopError(f"agent desktop {desktop_id} has an invalid creation time")
        try:
            parsed_created_at = dt.datetime.fromisoformat(
                created_at[:-1] + "+00:00" if created_at.endswith("Z") else created_at
            )
        except ValueError as error:
            raise AgentDesktopError(
                f"agent desktop {desktop_id} has an invalid creation time"
            ) from error
        if parsed_created_at.tzinfo is None:
            raise AgentDesktopError(f"agent desktop {desktop_id} has an invalid creation time")
        entered_monitor = saved["enteredMonitor"]
        if entered_monitor is not None and (
            not isinstance(entered_monitor, str)
            or not MONITOR_PATTERN.fullmatch(entered_monitor)
            or entered_monitor.startswith("AGENT-")
        ):
            raise AgentDesktopError(f"agent desktop {desktop_id} has an invalid entered monitor")

        expected_output = output_name(slot)
        output = saved["output"]
        if output == f"AGENT-{desktop_id}":
            raise AgentDesktopError(
                "legacy agent desktop state needs a Hyprland restart before migration"
            )
        elif output != expected_output:
            raise AgentDesktopError(f"agent desktop {desktop_id} has an invalid output")

        normalized = dict(saved)
        normalized["output"] = output
        result["desktops"].append(normalized)
        seen_ids.add(desktop_id)
        seen_slots.add(slot)

    result["desktops"].sort(key=lambda item: (item["slot"], item["id"]))
    return result, migrated


@contextlib.contextmanager
def locked_state() -> Iterator[tuple[dict[str, Any], Path]]:
    directory = state_directory()
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        raise AgentDesktopError(f"could not create the agent desktop state: {error}") from error
    with contextlib.suppress(OSError):
        directory.chmod(0o700)

    lock_path = directory / "state.lock"
    try:
        lock_file = lock_path.open("a+", encoding="utf-8")
    except OSError as error:
        raise AgentDesktopError(f"could not lock the agent desktop state: {error}") from error
    with lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        state_path = directory / "desktops.json"
        if state_path.exists():
            try:
                state, migrated = validate_state(
                    json.loads(state_path.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError) as error:
                raise AgentDesktopError(f"could not read the agent desktop state: {error}") from error
            if migrated:
                write_state(state, state_path)
        else:
            state = default_state()
        yield state, state_path


def write_state(state: dict[str, Any], state_path: Path) -> None:
    try:
        state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="desktops.", suffix=".tmp", dir=state_path.parent
        )
    except OSError as error:
        raise AgentDesktopError(f"could not write the agent desktop state: {error}") from error
    temporary_path = Path(temporary_name)
    try:
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.chmod(0o600)
            os.replace(temporary_path, state_path)
        except OSError as error:
            raise AgentDesktopError(f"could not write the agent desktop state: {error}") from error
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary_path.unlink()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    slug = slug[:40].rstrip("-")
    if not ID_PATTERN.fullmatch(slug):
        raise AgentDesktopError("the desktop name must contain a letter or a number")
    return slug


def validate_id(value: str) -> str:
    if not ID_PATTERN.fullmatch(value):
        raise AgentDesktopError(
            "the desktop ID must use lower-case letters, digits, and hyphens"
        )
    return value


def validate_label(value: str) -> str:
    label = value.strip()
    if (
        not label
        or len(label) > 160
        or any(ord(character) < 32 for character in label)
    ):
        raise AgentDesktopError(
            "the desktop name must contain 1 to 160 printable characters"
        )
    return label


def normalize_mode(value: str) -> str:
    match = MODE_PATTERN.fullmatch(value.strip())
    if not match:
        raise AgentDesktopError("the mode must have the form WIDTHxHEIGHT@RATE")
    width = int(match.group(1))
    height = int(match.group(2))
    rate = float(match.group(3) or "60")
    if not 640 <= width <= 7680 or not 480 <= height <= 4320:
        raise AgentDesktopError("the desktop size is outside the supported range")
    if not 24 <= rate <= 240:
        raise AgentDesktopError("the refresh rate is outside the supported range")
    rate_text = str(int(rate)) if rate.is_integer() else f"{rate:g}"
    return f"{width}x{height}@{rate_text}"


def lua_string(value: str) -> str:
    equals = ""
    while f"]{equals}]" in value:
        equals += "="
    return f"[{equals}[{value}]{equals}]"


def desktop_by_id(state: dict[str, Any], desktop_id: str) -> dict[str, Any]:
    for desktop in state["desktops"]:
        if desktop.get("id") == desktop_id:
            return desktop
    raise AgentDesktopError(f"agent desktop not found: {desktop_id}")


def workspace_name(desktop_id: str) -> str:
    return f"special:agent:{desktop_id}"


def output_name(slot: int) -> str:
    return f"AGENT-{slot}"


def process_detail(result: subprocess.CompletedProcess[Any]) -> str:
    detail = result.stderr or result.stdout or "command failed"
    if isinstance(detail, bytes):
        detail = detail.decode(errors="replace")
    return str(detail).strip()


class Hyprctl:
    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or os.environ.get("AGENT_DESKTOP_HYPRCTL", "hyprctl")

    def command(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [self.executable, *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT,
            )
        except FileNotFoundError as error:
            raise AgentDesktopError("hyprctl is not installed") from error
        except subprocess.TimeoutExpired as error:
            raise AgentDesktopError(
                f"hyprctl timed out while it ran: {' '.join(arguments)}"
            ) from error
        if check and result.returncode != 0:
            raise AgentDesktopError(process_detail(result))
        return result

    def json(self, command: str, *arguments: str) -> Any:
        result = self.command("-j", command, *arguments)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AgentDesktopError(f"hyprctl returned invalid JSON for {command}") from error

    def eval(self, code: str) -> None:
        self.command("eval", code)

    def plugin_loaded(self) -> bool:
        result = self.command("plugin", "list", check=False)
        if result.returncode != 0:
            return False
        return any(
            line == "agent-desktops-v3" or line.startswith("Plugin agent-desktops-v3 ")
            for line in (part.strip() for part in result.stdout.splitlines())
        )

    def plugin_call(self, operation: str, desktop_id: str, monitor: str) -> None:
        if operation not in {"prepare", "place", "cleanup", "remove", "abort"}:
            raise AgentDesktopError("unknown agent desktop plug-in operation")
        self.eval(
            "assert(hl.plugin.agent_desktops_v3.{}({}, {}))".format(
                operation, lua_string(desktop_id), lua_string(monitor)
            )
        )


class Controller:
    def __init__(self, hypr: Hyprctl | None = None) -> None:
        self.hypr = hypr or Hyprctl()

    def require_plugin(self) -> None:
        if not self.hypr.plugin_loaded():
            raise AgentDesktopError(
                "the agent-desktops Hyprland plug-in is not loaded; run agent-desktops-start"
            )

    def monitors(self, *, all_outputs: bool = True) -> list[dict[str, Any]]:
        value = self.hypr.json("monitors", *("all",) if all_outputs else ())
        return value if isinstance(value, list) else []

    def clients(self) -> list[dict[str, Any]]:
        value = self.hypr.json("clients")
        return value if isinstance(value, list) else []

    def workspaces(self) -> list[dict[str, Any]]:
        value = self.hypr.json("workspaces")
        return value if isinstance(value, list) else []

    @staticmethod
    def require_ok(result: subprocess.CompletedProcess[Any], action: str) -> None:
        if result.returncode != 0:
            raise AgentDesktopError(process_detail(result))
        if str(result.stdout or "").strip().lower() != "ok":
            raise AgentDesktopError(f"Hyprland did not confirm {action}")

    def configure_output(self, desktop: dict[str, Any]) -> None:
        code = "AgentDesktops.configure_output({}, {}, {})".format(
            lua_string(desktop["output"]),
            lua_string(desktop["mode"]),
            int(desktop["slot"]),
        )
        self.hypr.eval(code)

    def output_exists(self, desktop: dict[str, Any]) -> bool:
        return any(item.get("name") == desktop["output"] for item in self.monitors())

    def output_is_ready(self, desktop: dict[str, Any]) -> bool:
        mode = MODE_PATTERN.fullmatch(desktop["mode"])
        assert mode is not None
        expected_x = 100000 + int(desktop["slot"]) * 8192
        expected_y = 100000
        expected_width = int(mode.group(1))
        expected_height = int(mode.group(2))
        expected_rate = float(mode.group(3) or "60")
        for monitor in self.monitors():
            if monitor.get("name") != desktop["output"]:
                continue
            try:
                return (
                    monitor.get("disabled") is not True
                    and int(monitor["x"]) == expected_x
                    and int(monitor["y"]) == expected_y
                    and int(monitor["width"]) == expected_width
                    and int(monitor["height"]) == expected_height
                    and abs(float(monitor["refreshRate"]) - expected_rate) < 0.1
                )
            except (KeyError, TypeError, ValueError):
                return False
        return False

    def wait_for_output(self, desktop: dict[str, Any], timeout: float = OUTPUT_WAIT_TIMEOUT) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.output_is_ready(desktop):
                return
            time.sleep(0.05)
        raise AgentDesktopError(
            f"headless output did not become ready at its safe position: {desktop['output']}"
        )

    def wait_for_output_removed(
        self, desktop: dict[str, Any], timeout: float = OUTPUT_WAIT_TIMEOUT
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.output_exists(desktop):
                return
            time.sleep(0.05)
        raise AgentDesktopError(f"headless output was not removed: {desktop['output']}")

    def prepare(self, desktop: dict[str, Any]) -> None:
        self.hypr.plugin_call("prepare", desktop["id"], desktop["output"])

    def place(self, desktop: dict[str, Any], monitor: str) -> None:
        self.hypr.plugin_call("place", desktop["id"], monitor)

    def cleanup(self, desktop: dict[str, Any]) -> None:
        self.hypr.plugin_call("cleanup", desktop["id"], desktop["output"])

    def destroy_output(self, desktop: dict[str, Any]) -> None:
        self.hypr.plugin_call("remove", desktop["id"], desktop["output"])

    def abort_output(self, desktop: dict[str, Any]) -> None:
        if not self.output_exists(desktop):
            return
        try:
            self.hypr.plugin_call("abort", desktop["id"], desktop["output"])
        except AgentDesktopError:
            if not self.output_exists(desktop):
                return
            raise
        self.wait_for_output_removed(desktop)

    def rollback_output(self, desktop: dict[str, Any], cause: Exception) -> None:
        try:
            self.abort_output(desktop)
        except Exception as rollback_error:
            raise AgentDesktopError(
                f"{cause}; agent output rollback failed: {rollback_error}"
            ) from cause

    def start_output(self, desktop: dict[str, Any]) -> None:
        self.require_plugin()
        if self.output_exists(desktop):
            self.assert_output_removable(desktop, allow_initial_workspace=True)
            self.configure_output(desktop)
            self.wait_for_output(desktop)
            self.prepare(desktop)
            return

        # Register the exact output rule before the output exists. This stops
        # the fallback auto-layout from moving a physical monitor while the
        # new output is initialized.
        self.configure_output(desktop)
        try:
            result = self.hypr.command(
                "output", "create", "headless", desktop["output"], check=False
            )
            self.require_ok(result, "headless output creation")
            self.configure_output(desktop)
            self.wait_for_output(desktop)
            self.assert_output_removable(desktop, allow_initial_workspace=True)
            self.prepare(desktop)
        except Exception as error:
            self.rollback_output(desktop, error)
            raise

    def assert_output_removable(
        self,
        desktop: dict[str, Any],
        *,
        allow_initial_workspace: bool = False,
        allow_managed_elsewhere: bool = False,
    ) -> None:
        monitors = self.monitors()
        monitor = next(
            (item for item in monitors if item.get("name") == desktop["output"]), None
        )
        if monitor is None:
            return
        monitor_id = monitor.get("id")
        expected_names = {
            f"__agent-base:{desktop['id']}",
            desktop["workspace"],
        }
        output_workspaces = [
            item for item in self.workspaces() if item.get("monitor") == desktop["output"]
        ]
        unexpected_workspaces = [
            str(item.get("name") or "")
            for item in output_workspaces
            if str(item.get("name") or "") not in expected_names
        ]

        clients = self.clients()
        unexpected_windows = []
        for client in clients:
            workspace = client.get("workspace") or {}
            name = str(workspace.get("name") or "")
            on_output = monitor_id is not None and client.get("monitor") == monitor_id
            if name == f"__agent-base:{desktop['id']}" or (
                on_output and name != desktop["workspace"]
            ):
                unexpected_windows.append(str(client.get("address") or "unknown"))

        if unexpected_windows:
            raise AgentDesktopError(
                "the headless output has unexpected windows: "
                + ", ".join(unexpected_windows)
            )
        if unexpected_workspaces and not allow_initial_workspace:
            raise AgentDesktopError(
                "the headless output has unexpected workspaces: "
                + ", ".join(unexpected_workspaces)
            )
        if allow_initial_workspace and unexpected_workspaces:
            workspace_names = {str((item.get("workspace") or {}).get("name") or "") for item in clients}
            if any(name in workspace_names for name in unexpected_workspaces):
                raise AgentDesktopError("the initial headless workspace is not empty")

        for item in self.workspaces():
            name = str(item.get("name") or "")
            if (
                not allow_managed_elsewhere
                and name in expected_names
                and item.get("monitor") != desktop["output"]
            ):
                raise AgentDesktopError(
                    f"managed workspace {name} is on an unexpected output"
                )

    def ensure_workspace(self, desktop: dict[str, Any]) -> str:
        if not self.output_exists(desktop):
            raise AgentDesktopError("the agent desktop output is offline; run restore")
        if not self.output_is_ready(desktop):
            self.configure_output(desktop)
            self.wait_for_output(desktop)
        monitor = self.workspace_monitor(desktop)
        if monitor is None:
            self.assert_output_removable(
                desktop,
                allow_initial_workspace=True,
                allow_managed_elsewhere=True,
            )
            self.prepare(desktop)
            monitor = self.workspace_monitor(desktop)
        if monitor is None:
            raise AgentDesktopError("the agent desktop workspace could not be restored")
        if monitor.startswith("AGENT-") and monitor != desktop["output"]:
            raise AgentDesktopError("the agent desktop workspace is on an unknown output")
        return monitor

    def workspace_monitor(
        self, desktop: dict[str, Any], monitors: list[dict[str, Any]] | None = None
    ) -> str | None:
        for monitor in monitors if monitors is not None else self.monitors():
            special = monitor.get("specialWorkspace") or {}
            if special.get("name") == desktop["workspace"]:
                return str(monitor.get("name") or "") or None
        return None

    def desktop_windows(
        self, desktop: dict[str, Any], clients: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        result = []
        for client in clients if clients is not None else self.clients():
            workspace = client.get("workspace") or {}
            if workspace.get("name") != desktop["workspace"]:
                continue
            result.append(
                {
                    "address": str(client.get("address") or ""),
                    "class": str(client.get("class") or ""),
                    "title": str(client.get("title") or ""),
                    "pid": int(client.get("pid") or 0),
                    "mapped": bool(client.get("mapped", True)),
                    "xwayland": bool(client.get("xwayland", False)),
                    "focusHistoryID": int(client.get("focusHistoryID") or 0),
                }
            )
        result.sort(key=lambda item: (item["focusHistoryID"], item["address"]))
        return result

    def live_desktops(self, state: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
        try:
            monitors = self.monitors()
            clients = self.clients()
            available = self.hypr.plugin_loaded()
        except AgentDesktopError:
            monitors = []
            clients = []
            available = False

        monitor_names = {str(item.get("name") or "") for item in monitors}
        result = []
        for saved in state["desktops"]:
            desktop = dict(saved)
            windows = self.desktop_windows(desktop, clients)
            active_monitor = self.workspace_monitor(desktop, monitors)
            output_online = desktop["output"] in monitor_names
            entered = bool(active_monitor and active_monitor != desktop["output"])
            if entered:
                status = "entered"
            elif active_monitor == desktop["output"]:
                status = "ready"
            elif output_online:
                status = "stale"
            else:
                status = "offline"
            desktop.update(
                {
                    "activeMonitor": active_monitor,
                    "enteredMonitor": active_monitor if entered else None,
                    "entered": entered,
                    "online": output_online,
                    "status": status,
                    "windowCount": len(windows),
                    "windows": windows,
                }
            )
            result.append(desktop)
        return available, result

    def create(self, name: str, requested_id: str | None, mode: str) -> dict[str, Any]:
        label = validate_label(name)
        desktop_id = validate_id(requested_id) if requested_id else slugify(label)
        normalized_mode = normalize_mode(mode)
        with locked_state() as (state, state_path):
            if any(item.get("id") == desktop_id for item in state["desktops"]):
                raise AgentDesktopError(f"agent desktop already exists: {desktop_id}")
            if len(state["desktops"]) >= MAX_DESKTOPS:
                raise AgentDesktopError(f"the limit is {MAX_DESKTOPS} agent desktops")
            used_slots = {int(item.get("slot", -1)) for item in state["desktops"]}
            slot = next(index for index in range(MAX_DESKTOPS) if index not in used_slots)
            desktop = {
                "id": desktop_id,
                "label": label,
                "output": output_name(slot),
                "workspace": workspace_name(desktop_id),
                "mode": normalized_mode,
                "slot": slot,
                "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                "enteredMonitor": None,
            }
            self.start_output(desktop)
            try:
                state["desktops"].append(desktop)
                state["desktops"].sort(key=lambda item: (int(item["slot"]), item["id"]))
                write_state(state, state_path)
            except Exception as error:
                self.rollback_output(desktop, error)
                raise
            return desktop

    def restore(self) -> list[str]:
        self.require_plugin()
        errors = []
        with locked_state() as (state, state_path):
            changed = False
            for desktop in state["desktops"]:
                try:
                    self.start_output(desktop)
                    if desktop.get("enteredMonitor") is not None:
                        desktop["enteredMonitor"] = None
                        changed = True
                except AgentDesktopError as error:
                    errors.append(f"{desktop.get('id', 'unknown')}: {error}")
            if changed:
                write_state(state, state_path)
        return errors

    def enter(self, desktop_id: str) -> dict[str, Any]:
        with locked_state() as (state, state_path):
            desktop = desktop_by_id(state, desktop_id)
            self.ensure_workspace(desktop)
            monitors = self.monitors()
            for other in state["desktops"]:
                if other.get("id") == desktop_id:
                    continue
                active_monitor = self.workspace_monitor(other, monitors)
                if active_monitor and not active_monitor.startswith("AGENT-"):
                    raise AgentDesktopError(
                        f"leave agent desktop {other['id']} before you enter another desktop"
                    )
            focused = next(
                (
                    item
                    for item in monitors
                    if item.get("focused") is True
                    and not str(item.get("name") or "").startswith("AGENT-")
                ),
                None,
            )
            if not focused:
                raise AgentDesktopError("no focused physical monitor was found")
            focused_name = str(focused["name"])
            current_special = (focused.get("specialWorkspace") or {}).get("name") or ""
            if current_special and current_special != desktop["workspace"]:
                raise AgentDesktopError(
                    "close the current special workspace before you enter an agent desktop"
                )
            self.place(desktop, focused_name)
            desktop["enteredMonitor"] = focused_name
            write_state(state, state_path)
            return desktop

    def leave(self, desktop_id: str | None) -> dict[str, Any] | None:
        with locked_state() as (state, state_path):
            if desktop_id:
                desktop = desktop_by_id(state, desktop_id)
            else:
                desktop = None
                monitors = self.monitors()
                for candidate in state["desktops"]:
                    location = self.workspace_monitor(candidate, monitors)
                    if location and not location.startswith("AGENT-"):
                        desktop = candidate
                        break
                if desktop is None:
                    return None
            monitor = self.ensure_workspace(desktop)
            if monitor == desktop["output"]:
                desktop["enteredMonitor"] = None
                write_state(state, state_path)
                return desktop
            self.place(desktop, desktop["output"])
            desktop["enteredMonitor"] = None
            write_state(state, state_path)
            return desktop

    def run(self, desktop_id: str, command: Sequence[str]) -> None:
        if not command:
            raise AgentDesktopError("a command is required after --")
        with locked_state() as (state, _):
            desktop = desktop_by_id(state, desktop_id)
            self.ensure_workspace(desktop)
            environment = [
                "env",
                f"AGENT_DESKTOP_ID={desktop['id']}",
                f"AGENT_DESKTOP_OUTPUT={desktop['output']}",
            ]
            shell_command = shlex.join([*environment, *command])
            code = (
                "hl.dispatch(hl.dsp.exec_cmd({}, {{ workspace = {}, "
                "no_initial_focus = true, focus_on_activate = false }}))"
            ).format(
                lua_string(shell_command), lua_string(desktop["workspace"] + " silent")
            )
            self.hypr.eval(code)

    def resolve_window(
        self, desktop: dict[str, Any], address: str | None
    ) -> dict[str, Any]:
        windows = self.desktop_windows(desktop)
        if not windows:
            raise AgentDesktopError("the agent desktop has no windows")
        if address is None:
            return windows[0]
        if not ADDRESS_PATTERN.fullmatch(address):
            raise AgentDesktopError("the window address must have the form 0x1234")
        for window in windows:
            if window["address"].lower() == address.lower():
                return window
        raise AgentDesktopError("the window is not on this agent desktop")

    @staticmethod
    def guarded_window_code(
        desktop: dict[str, Any],
        window: dict[str, Any],
        action: str,
        *,
        reject_xwayland: bool = False,
    ) -> str:
        address = window["address"]
        checks = [
            f"local w = hl.get_window({lua_string('address:' + address)})",
            "if w == nil or w.address ~= {} then error({}) end".format(
                lua_string(address), lua_string("the selected window no longer exists")
            ),
            "if w.workspace == nil or w.workspace.name ~= {} then error({}) end".format(
                lua_string(desktop["workspace"]),
                lua_string("the selected window left the agent desktop"),
            ),
        ]
        if reject_xwayland:
            checks.append(
                "if w.xwayland then error({}) end".format(
                    lua_string("targeted shortcuts are not safe for XWayland windows")
                )
            )
        checks.append(action)
        return "; ".join(checks)

    def adopt(self, desktop_id: str, address: str) -> dict[str, Any]:
        if not ADDRESS_PATTERN.fullmatch(address):
            raise AgentDesktopError("the window address must have the form 0x1234")
        with locked_state() as (state, _):
            desktop = desktop_by_id(state, desktop_id)
            self.ensure_workspace(desktop)
            client = next(
                (
                    value
                    for value in self.clients()
                    if str(value.get("address") or "").lower() == address.lower()
                ),
                None,
            )
            if client is None:
                raise AgentDesktopError(f"window not found: {address}")
            address = str(client.get("address") or "").lower()
            pid = int(client.get("pid") or 0)
            code = "; ".join(
                [
                    f"local w = hl.get_window({lua_string('address:' + address)})",
                    "if w == nil or w.address ~= {} or w.pid ~= {} then error({}) end".format(
                        lua_string(address),
                        pid,
                        lua_string("the selected window changed before it could be moved"),
                    ),
                    "hl.dispatch(hl.dsp.window.move({ workspace = %s, follow = false, window = w }))"
                    % lua_string(desktop["workspace"]),
                ]
            )
            self.hypr.eval(code)
            return {
                "address": address,
                "class": str(client.get("class") or ""),
                "title": str(client.get("title") or ""),
                "xwayland": bool(client.get("xwayland", False)),
            }

    def send_key(
        self, desktop_id: str, key: str, modifiers: str, address: str | None
    ) -> dict[str, Any]:
        if not key or len(key) > 80:
            raise AgentDesktopError("the key name is not valid")
        if not re.fullmatch(r"[A-Za-z0-9_+ -]{0,80}", modifiers):
            raise AgentDesktopError("the modifier list is not valid")
        with locked_state() as (state, _):
            desktop = desktop_by_id(state, desktop_id)
            self.ensure_workspace(desktop)
            window = self.resolve_window(desktop, address)
            if window["xwayland"]:
                raise AgentDesktopError(
                    "targeted shortcuts are not safe for XWayland windows"
                )
            action = "hl.dispatch(hl.dsp.send_shortcut({ mods = %s, key = %s, window = w }))" % (
                lua_string(modifiers.strip()),
                lua_string(key),
            )
            self.hypr.eval(
                self.guarded_window_code(
                    desktop, window, action, reject_xwayland=True
                )
            )
            return window

    def close_window(self, desktop_id: str, address: str | None, *, kill: bool = False) -> dict[str, Any]:
        with locked_state() as (state, _):
            desktop = desktop_by_id(state, desktop_id)
            self.ensure_workspace(desktop)
            window = self.resolve_window(desktop, address)
            action = "kill" if kill else "close"
            dispatch = f"hl.dispatch(hl.dsp.window.{action}({{ window = w }}))"
            self.hypr.eval(self.guarded_window_code(desktop, window, dispatch))
            return window

    def close_all(self, desktop: dict[str, Any], *, kill: bool = False) -> None:
        for window in self.desktop_windows(desktop):
            action = "kill" if kill else "close"
            dispatch = f"hl.dispatch(hl.dsp.window.{action}({{ window = w }}))"
            self.hypr.eval(self.guarded_window_code(desktop, window, dispatch))

    def wait_for_windows_to_close(self, desktop: dict[str, Any], timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.desktop_windows(desktop):
                return True
            time.sleep(0.1)
        return not self.desktop_windows(desktop)

    def remove(self, desktop_id: str, *, force: bool, kill: bool) -> dict[str, Any]:
        with locked_state() as (state, state_path):
            desktop = desktop_by_id(state, desktop_id)
            monitor = self.ensure_workspace(desktop)
            windows = self.desktop_windows(desktop)
            if windows and not (force or kill):
                raise AgentDesktopError(
                    f"agent desktop {desktop_id} has {len(windows)} open window(s); use --force"
                )
            if monitor != desktop["output"]:
                self.place(desktop, desktop["output"])
                desktop["enteredMonitor"] = None
            if windows:
                self.close_all(desktop, kill=kill)
                if not kill and not self.wait_for_windows_to_close(desktop):
                    raise AgentDesktopError(
                        "some windows did not close; use --kill only if data loss is acceptable"
                    )
                if kill and not self.wait_for_windows_to_close(desktop, timeout=1.0):
                    raise AgentDesktopError("some windows did not stop")
            self.assert_output_removable(desktop)
            self.destroy_output(desktop)
            self.wait_for_output_removed(desktop)
            state["desktops"] = [
                item for item in state["desktops"] if item.get("id") != desktop_id
            ]
            write_state(state, state_path)
            return dict(desktop)

    def screenshot(
        self, desktop_id: str, destination: str | None, *, clipboard: bool = False
    ) -> Path:
        with locked_state() as (state, _):
            desktop = desktop_by_id(state, desktop_id)
            monitor = self.ensure_workspace(desktop)
            self.place(desktop, monitor)
            time.sleep(0.05)
            if destination:
                path = Path(destination).expanduser().absolute()
            else:
                directory = screenshot_directory()
                timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                path = directory / f"{desktop_id}-{timestamp}.png"
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            grim = os.environ.get("AGENT_DESKTOP_GRIM", "grim")
            try:
                result = subprocess.run(
                    [grim, "-o", monitor, str(path)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=CAPTURE_TIMEOUT,
                )
            except FileNotFoundError as error:
                raise AgentDesktopError("grim is not installed") from error
            except subprocess.TimeoutExpired as error:
                raise AgentDesktopError("grim timed out while it captured the desktop") from error
            if result.returncode != 0:
                raise AgentDesktopError(process_detail(result))
            if clipboard:
                clipboard_command = os.environ.get("AGENT_DESKTOP_WL_COPY", "wl-copy")
                try:
                    image = path.open("rb")
                except OSError as error:
                    raise AgentDesktopError(f"could not read the screenshot: {error}") from error
                with image:
                    try:
                        copied = subprocess.run(
                            [clipboard_command, "--type", "image/png"],
                            stdin=image,
                            # wl-copy forks a process that keeps the Wayland
                            # selection alive. Do not give that process pipes
                            # which keep this command and the widget busy.
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                            timeout=CAPTURE_TIMEOUT,
                        )
                    except FileNotFoundError as error:
                        raise AgentDesktopError("wl-copy is not installed") from error
                    except subprocess.TimeoutExpired as error:
                        raise AgentDesktopError("wl-copy timed out") from error
                if copied.returncode != 0:
                    raise AgentDesktopError(
                        f"wl-copy failed with exit status {copied.returncode}"
                    )
            return path


def json_print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="omarchy-agent-desktop",
        description="Manage headless Hyprland desktops for GUI automation agents.",
    )
    commands = root.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create an isolated desktop.")
    create.add_argument("name")
    create.add_argument("--id", dest="desktop_id")
    create.add_argument("--mode", default="1920x1080@60")
    create.add_argument("--json", action="store_true")

    list_command = commands.add_parser("list", help="List agent desktops.")
    list_command.add_argument("--json", action="store_true")

    status = commands.add_parser("status", help="Show one agent desktop.")
    status.add_argument("desktop_id")
    status.add_argument("--json", action="store_true")

    run = commands.add_parser("run", aliases=["exec"], help="Start an app on a desktop.")
    run.add_argument("desktop_id")
    run.add_argument("application", nargs=argparse.REMAINDER)

    windows = commands.add_parser("windows", help="List windows on a desktop.")
    windows.add_argument("desktop_id")
    windows.add_argument("--json", action="store_true")

    adopt = commands.add_parser("adopt", help="Move one exact window to a desktop.")
    adopt.add_argument("desktop_id")
    adopt.add_argument("window")

    screenshot = commands.add_parser("screenshot", help="Capture a desktop output.")
    screenshot.add_argument("desktop_id")
    screenshot.add_argument("destination", nargs="?")
    screenshot.add_argument(
        "--clipboard",
        action="store_true",
        help="Also copy image/png to the clipboard (for the widget button).",
    )
    screenshot.add_argument("--json", action="store_true")

    key = commands.add_parser("key", help="Send a shortcut to a desktop window.")
    key.add_argument("desktop_id")
    key.add_argument("key")
    key.add_argument("--mods", default="")
    key.add_argument("--window")
    key.add_argument("--json", action="store_true")

    close = commands.add_parser("close-window", help="Ask a desktop window to close.")
    close.add_argument("desktop_id")
    close.add_argument("--window")
    close.add_argument("--kill", action="store_true")

    enter = commands.add_parser("enter", help="Show a desktop on the focused monitor.")
    enter.add_argument("desktop_id")

    leave = commands.add_parser("leave", help="Return a desktop to its headless output.")
    leave.add_argument("desktop_id", nargs="?")

    remove = commands.add_parser("remove", help="Remove an agent desktop.")
    remove.add_argument("desktop_id")
    remove.add_argument("--force", action="store_true")
    remove.add_argument("--kill", action="store_true")

    commands.add_parser("restore", help="Restore saved desktops after Hyprland starts.")
    commands.add_parser("doctor", help="Check the required desktop tools.")
    return root


def normalize_application(arguments: list[str]) -> list[str]:
    if arguments and arguments[0] == "--":
        return arguments[1:]
    return arguments


def run_main(arguments: Sequence[str] | None = None, controller: Controller | None = None) -> int:
    args = parser().parse_args(arguments)
    control = controller or Controller()

    if args.command == "create":
        desktop = control.create(args.name, args.desktop_id, args.mode)
        if args.json:
            json_print(desktop)
        else:
            print(desktop["id"])
        return 0

    if args.command == "list":
        with locked_state() as (state, _):
            available, desktops = control.live_desktops(state)
        if args.json:
            json_print(
                {"version": STATE_VERSION, "backendAvailable": available, "desktops": desktops}
            )
        elif not desktops:
            print("No agent desktops.")
        else:
            for desktop in desktops:
                print(
                    f"{desktop['id']}\t{desktop['status']}\t"
                    f"{desktop['windowCount']} window(s)\t{desktop['mode']}"
                )
        return 0

    if args.command == "status":
        with locked_state() as (state, _):
            desktop_by_id(state, args.desktop_id)
            available, desktops = control.live_desktops(state)
        desktop = next(item for item in desktops if item["id"] == args.desktop_id)
        if args.json:
            json_print({"backendAvailable": available, "desktop": desktop})
        else:
            print(f"{desktop['id']}: {desktop['status']}, {desktop['windowCount']} window(s)")
        return 0

    if args.command in {"run", "exec"}:
        application = normalize_application(args.application)
        control.run(args.desktop_id, application)
        print(f"Started on agent desktop {args.desktop_id}.")
        return 0

    if args.command == "windows":
        with locked_state() as (state, _):
            desktop = desktop_by_id(state, args.desktop_id)
            windows_value = control.desktop_windows(desktop)
        if args.json:
            json_print(windows_value)
        else:
            for window in windows_value:
                print(f"{window['address']}\t{window['class']}\t{window['title']}")
        return 0

    if args.command == "adopt":
        window = control.adopt(args.desktop_id, args.window)
        print(window["address"])
        return 0

    if args.command == "screenshot":
        path = control.screenshot(
            args.desktop_id, args.destination, clipboard=args.clipboard
        )
        if args.json:
            json_print({"path": str(path)})
        else:
            print(path)
        return 0

    if args.command == "key":
        window = control.send_key(args.desktop_id, args.key, args.mods, args.window)
        if args.json:
            json_print(window)
        else:
            print(window["address"])
        return 0

    if args.command == "close-window":
        window = control.close_window(args.desktop_id, args.window, kill=args.kill)
        print(window["address"])
        return 0

    if args.command == "enter":
        desktop = control.enter(args.desktop_id)
        print(f"Agent desktop {desktop['id']} is on {desktop['enteredMonitor']}.")
        return 0

    if args.command == "leave":
        desktop = control.leave(args.desktop_id)
        if desktop:
            print(f"Agent desktop {desktop['id']} returned to {desktop['output']}.")
        else:
            print("No agent desktop is entered.")
        return 0

    if args.command == "remove":
        desktop = control.remove(args.desktop_id, force=args.force, kill=args.kill)
        print(f"Removed agent desktop {desktop['id']}.")
        return 0

    if args.command == "restore":
        errors = control.restore()
        for error in errors:
            print(error, file=sys.stderr)
        return 1 if errors else 0

    if args.command == "doctor":
        checks = {
            "hyprctl": shutil.which(os.environ.get("AGENT_DESKTOP_HYPRCTL", "hyprctl")) is not None,
            "grim": shutil.which(os.environ.get("AGENT_DESKTOP_GRIM", "grim")) is not None,
            "pluginLoaded": False,
        }
        if checks["hyprctl"]:
            with contextlib.suppress(AgentDesktopError):
                checks["pluginLoaded"] = control.hypr.plugin_loaded()
        json_print(checks)
        return 0 if all(checks.values()) else 1

    raise AgentDesktopError("unknown command")


def main() -> int:
    try:
        return run_main()
    except AgentDesktopError as error:
        print(f"omarchy-agent-desktop: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("omarchy-agent-desktop: canceled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
