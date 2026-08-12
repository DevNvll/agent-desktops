from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "libexec/agent_desktops.py"
SPEC = importlib.util.spec_from_file_location("agent_desktops", MODULE_PATH)
assert SPEC and SPEC.loader
agent_desktops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent_desktops)


class FakeHyprctl:
    def __init__(self) -> None:
        self.monitors_value = [
            {
                "name": "DP-1",
                "focused": True,
                "specialWorkspace": {"id": 0, "name": ""},
            }
        ]
        self.clients_value = []
        self.workspaces_value = []
        self.evaluations = []
        self.plugin_calls = []
        self.commands = []
        self.plugin_available = True

    def plugin_loaded(self) -> bool:
        return self.plugin_available

    def json(self, command: str, *arguments: str):
        if command == "monitors":
            return self.monitors_value
        if command == "clients":
            return self.clients_value
        if command == "workspaces":
            return self.workspaces_value
        if command == "cursorpos":
            return {"x": 120, "y": 240}
        raise AssertionError((command, arguments))

    def command(self, *arguments: str, check: bool = True):
        self.commands.append(arguments)
        if arguments[:3] == ("output", "create", "headless"):
            name = arguments[3]
            self.monitors_value.append(
                {
                    "name": name,
                    "id": 100 + int(name.removeprefix("AGENT-")),
                    "focused": False,
                    "disabled": False,
                    "x": 100000 + int(name.removeprefix("AGENT-")) * 8192,
                    "y": 100000,
                    "width": 1280,
                    "height": 720,
                    "refreshRate": 60.0,
                    "specialWorkspace": {"id": 0, "name": ""},
                }
            )
        elif arguments[:2] == ("output", "remove"):
            name = arguments[2]
            self.monitors_value = [item for item in self.monitors_value if item["name"] != name]
            self.workspaces_value = [
                item for item in self.workspaces_value if item.get("monitor") != name
            ]
        return subprocess.CompletedProcess(arguments, 0, "ok\n", "")

    def eval(self, code: str) -> None:
        self.evaluations.append(code)
        if "hl.dsp.window.close" in code or "hl.dsp.window.kill" in code:
            self.clients_value = []

    def plugin_call(self, operation: str, desktop_id: str, monitor_name: str) -> None:
        self.plugin_calls.append((operation, desktop_id, monitor_name))
        special_name = f"special:agent:{desktop_id}"
        if operation in {"prepare", "place"}:
            for monitor in self.monitors_value:
                if (monitor.get("specialWorkspace") or {}).get("name") == special_name:
                    monitor["specialWorkspace"] = {"id": 0, "name": ""}
            target = next(item for item in self.monitors_value if item["name"] == monitor_name)
            target["specialWorkspace"] = {"id": -2, "name": special_name}
            self.workspaces_value = [
                item
                for item in self.workspaces_value
                if item.get("name") not in {special_name, f"__agent-base:{desktop_id}"}
            ]
            self.workspaces_value.extend(
                [
                    {"name": f"__agent-base:{desktop_id}", "monitor": monitor_name},
                    {"name": special_name, "monitor": monitor_name},
                ]
            )
        elif operation in {"remove", "abort"}:
            self.monitors_value = [
                item for item in self.monitors_value if item["name"] != monitor_name
            ]
            self.workspaces_value = [
                item for item in self.workspaces_value if item.get("monitor") != monitor_name
            ]


class AgentDesktopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="agent-desktops-test-")
        self.environment = mock.patch.dict(
            os.environ,
            {
                "AGENT_DESKTOP_STATE_DIR": self.temporary.name,
                "AGENT_DESKTOP_SCREENSHOT_DIR": self.temporary.name,
            },
        )
        self.environment.start()
        self.fake = FakeHyprctl()
        self.controller = agent_desktops.Controller(self.fake)

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def create(self):
        return self.controller.create("Electron Test", None, "1280x720@60")

    def test_validation_and_lua_strings(self) -> None:
        self.assertEqual(agent_desktops.slugify(" Electron / Test "), "electron-test")
        self.assertEqual(agent_desktops.normalize_mode("1920x1080"), "1920x1080@60")
        self.assertEqual(agent_desktops.normalize_mode("1280x720@59.94"), "1280x720@59.94")
        self.assertEqual(agent_desktops.lua_string("a]]b"), "[=[a]]b]=]")
        with self.assertRaises(agent_desktops.AgentDesktopError):
            agent_desktops.validate_id("Bad ID")
        with self.assertRaises(agent_desktops.AgentDesktopError):
            agent_desktops.normalize_mode("100x100@1")
        with self.assertRaises(agent_desktops.AgentDesktopError):
            agent_desktops.validate_label("x" * 161)
        with self.assertRaises(agent_desktops.AgentDesktopError):
            agent_desktops.validate_label("bad\nname")

    def test_create_rejects_an_invalid_label_before_output_creation(self) -> None:
        with self.assertRaises(agent_desktops.AgentDesktopError):
            self.controller.create("x" * 161, "valid-id", "1280x720@60")
        self.assertFalse(self.fake.commands)

    def test_create_uses_a_headless_output_and_atomic_state(self) -> None:
        desktop = self.create()
        self.assertEqual(desktop["id"], "electron-test")
        self.assertEqual(desktop["output"], "AGENT-0")
        self.assertIn(("output", "create", "headless", "AGENT-0"), self.fake.commands)
        self.assertIn(
            ("prepare", "electron-test", "AGENT-0"),
            self.fake.plugin_calls,
        )
        create_index = self.fake.commands.index(
            ("output", "create", "headless", "AGENT-0")
        )
        self.assertTrue(self.fake.evaluations)
        self.assertIn("AgentDesktops.configure_output", self.fake.evaluations[0])
        self.assertEqual(create_index, 0)
        saved = json.loads((Path(self.temporary.name) / "desktops.json").read_text())
        self.assertEqual(saved["desktops"][0]["workspace"], "special:agent:electron-test")

    def test_list_marks_headless_and_entered_states(self) -> None:
        desktop = self.create()
        self.fake.clients_value = [
            {
                "address": "0x123",
                "class": "electron",
                "title": "Test",
                "pid": 42,
                "mapped": True,
                "xwayland": False,
                "focusHistoryID": 1,
                "workspace": {"name": desktop["workspace"]},
            }
        ]
        with agent_desktops.locked_state() as (state, _):
            available, listed = self.controller.live_desktops(state)
        self.assertTrue(available)
        self.assertEqual(listed[0]["status"], "ready")
        self.assertEqual(listed[0]["windowCount"], 1)

        self.controller.enter(desktop["id"])
        with agent_desktops.locked_state() as (state, _):
            _, listed = self.controller.live_desktops(state)
        self.assertTrue(listed[0]["entered"])
        self.assertEqual(listed[0]["activeMonitor"], "DP-1")

        self.controller.leave(desktop["id"])
        with agent_desktops.locked_state() as (state, _):
            _, listed = self.controller.live_desktops(state)
        self.assertEqual(listed[0]["status"], "ready")

    def test_list_reports_an_unavailable_plugin(self) -> None:
        desktop = self.create()
        self.fake.plugin_available = False

        with agent_desktops.locked_state() as (state, _):
            available, listed = self.controller.live_desktops(state)

        self.assertFalse(available)
        self.assertEqual(listed[0]["id"], desktop["id"])

    def test_run_preserves_arguments_and_prevents_focus(self) -> None:
        desktop = self.create()
        self.controller.run(desktop["id"], ["electron", "file name", "; touch /tmp/no"])
        code = self.fake.evaluations[-1]
        self.assertIn("no_initial_focus = true", code)
        self.assertIn("focus_on_activate = false", code)
        self.assertIn("special:agent:electron-test silent", code)
        self.assertIn("'; touch /tmp/no'", code)

    def test_window_actions_are_limited_to_the_desktop(self) -> None:
        desktop = self.create()
        self.fake.clients_value = [
            {
                "address": "0x123",
                "class": "qemu",
                "title": "VM",
                "pid": 77,
                "focusHistoryID": 0,
                "xwayland": False,
                "workspace": {"name": desktop["workspace"]},
            },
            {
                "address": "0x999",
                "class": "private",
                "title": "Main desktop",
                "pid": 88,
                "focusHistoryID": 0,
                "xwayland": False,
                "workspace": {"name": "1"},
            },
        ]
        window = self.controller.send_key(desktop["id"], "F5", "CTRL", "0x123")
        self.assertEqual(window["address"], "0x123")
        self.assertIn("address:0x123", self.fake.evaluations[-1])
        self.assertIn("w.workspace.name", self.fake.evaluations[-1])
        self.assertIn("window = w", self.fake.evaluations[-1])
        with self.assertRaises(agent_desktops.AgentDesktopError):
            self.controller.send_key(desktop["id"], "F5", "", "0x999")

        adopted = self.controller.adopt(desktop["id"], "0x999")
        self.assertEqual(adopted["address"], "0x999")
        self.assertIn("follow = false", self.fake.evaluations[-1])
        self.assertIn("special:agent:electron-test", self.fake.evaluations[-1])

    def test_remove_needs_force_when_windows_are_open(self) -> None:
        desktop = self.create()
        self.fake.clients_value = [
            {
                "address": "0x123",
                "class": "electron",
                "title": "Unsaved",
                "pid": 42,
                "focusHistoryID": 0,
                "xwayland": False,
                "workspace": {"name": desktop["workspace"]},
            }
        ]
        with self.assertRaises(agent_desktops.AgentDesktopError):
            self.controller.remove(desktop["id"], force=False, kill=False)
        self.controller.remove(desktop["id"], force=True, kill=False)
        self.assertNotIn("AGENT-0", [item["name"] for item in self.fake.monitors_value])
        self.assertIn(("remove", desktop["id"], desktop["output"]), self.fake.plugin_calls)

    def test_failed_state_write_uses_native_abort(self) -> None:
        with mock.patch.object(agent_desktops, "write_state", side_effect=OSError("full")):
            with self.assertRaises(OSError):
                self.create()
        self.assertIn(("abort", "electron-test", "AGENT-0"), self.fake.plugin_calls)
        self.assertNotIn("AGENT-0", [item["name"] for item in self.fake.monitors_value])

    def test_screenshot_targets_the_agent_output(self) -> None:
        desktop = self.create()
        completed = subprocess.CompletedProcess(["grim"], 0, "", "")
        with mock.patch.object(agent_desktops.subprocess, "run", return_value=completed) as run:
            path = self.controller.screenshot(desktop["id"], None, clipboard=False)
        self.assertEqual(Path(self.temporary.name), path.parent)
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[1:3], ["-o", "AGENT-0"])

    def test_clipboard_is_opt_in_for_widget_screenshots(self) -> None:
        desktop = self.create()
        completed = subprocess.CompletedProcess(["grim"], 0, "", "")
        copied = subprocess.CompletedProcess(["wl-copy"], 0, b"", b"")

        def run_command(arguments, **_kwargs):
            if arguments[0] == "grim":
                Path(arguments[-1]).write_bytes(b"\x89PNG\r\n\x1a\n")
                return completed
            return copied

        with mock.patch.object(
            agent_desktops.subprocess, "run", side_effect=run_command
        ) as run:
            path = self.controller.screenshot(desktop["id"], None, clipboard=True)
        self.assertTrue(path.name.endswith(".png"))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[1].args[0], ["wl-copy", "--type", "image/png"]
        )
        self.assertIs(run.call_args_list[1].kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(run.call_args_list[1].kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(run.call_args_list[1].kwargs["stdin"].closed)

    def test_state_rejects_legacy_output_until_restart(self) -> None:
        desktop = self.create()
        state_path = Path(self.temporary.name) / "desktops.json"
        saved = json.loads(state_path.read_text())
        saved["desktops"][0]["output"] = f"AGENT-{desktop['id']}"
        state_path.write_text(json.dumps(saved))

        with self.assertRaisesRegex(agent_desktops.AgentDesktopError, "restart"):
            with agent_desktops.locked_state():
                pass

    def test_state_rejects_a_tampered_workspace(self) -> None:
        self.create()
        state_path = Path(self.temporary.name) / "desktops.json"
        saved = json.loads(state_path.read_text())
        saved["desktops"][0]["workspace"] = "special:agent:other"
        state_path.write_text(json.dumps(saved))

        with self.assertRaises(agent_desktops.AgentDesktopError):
            with agent_desktops.locked_state():
                pass

    def test_stale_workspace_is_restored_before_an_action(self) -> None:
        desktop = self.create()
        output = next(
            item for item in self.fake.monitors_value if item["name"] == desktop["output"]
        )
        output["specialWorkspace"] = {"id": 0, "name": ""}
        calls_before = len(self.fake.plugin_calls)

        self.controller.run(desktop["id"], ["electron"])

        self.assertIn(
            ("prepare", desktop["id"], desktop["output"]),
            self.fake.plugin_calls[calls_before:],
        )

    def test_targeted_shortcut_rejects_xwayland(self) -> None:
        desktop = self.create()
        self.fake.clients_value = [
            {
                "address": "0x123",
                "class": "legacy",
                "title": "X11",
                "pid": 42,
                "mapped": True,
                "xwayland": True,
                "focusHistoryID": 0,
                "workspace": {"name": desktop["workspace"]},
            }
        ]
        windows = self.controller.desktop_windows(desktop)
        self.assertTrue(windows[0]["xwayland"])
        with self.assertRaisesRegex(agent_desktops.AgentDesktopError, "XWayland"):
            self.controller.send_key(desktop["id"], "F5", "", "0x123")

    def test_output_removal_rejects_an_unmanaged_workspace(self) -> None:
        desktop = self.create()
        self.fake.workspaces_value.append(
            {"name": "unexpected", "monitor": desktop["output"]}
        )
        with self.assertRaisesRegex(agent_desktops.AgentDesktopError, "unexpected workspaces"):
            self.controller.remove(desktop["id"], force=False, kill=False)
        self.assertIn(desktop["output"], [item["name"] for item in self.fake.monitors_value])

    def test_hyprctl_commands_have_a_timeout(self) -> None:
        with mock.patch.object(
            agent_desktops.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["hyprctl", "monitors"], 5),
        ) as run:
            with self.assertRaisesRegex(agent_desktops.AgentDesktopError, "timed out"):
                agent_desktops.Hyprctl("hyprctl").command("monitors")
        self.assertEqual(run.call_args.kwargs["timeout"], agent_desktops.COMMAND_TIMEOUT)

    def test_hyprctl_uses_the_versioned_plugin_namespace(self) -> None:
        completed = subprocess.CompletedProcess(
            ["hyprctl", "plugin", "list"], 0, "agent-desktops-v3\n", ""
        )
        with mock.patch.object(
            agent_desktops.subprocess, "run", return_value=completed
        ):
            hypr = agent_desktops.Hyprctl("hyprctl")
            self.assertTrue(hypr.plugin_loaded())
            with mock.patch.object(hypr, "eval") as evaluate:
                hypr.plugin_call("prepare", "test", "AGENT-0")
        self.assertIn(
            "assert(hl.plugin.agent_desktops_v3.prepare(",
            evaluate.call_args.args[0],
        )


if __name__ == "__main__":
    unittest.main()
