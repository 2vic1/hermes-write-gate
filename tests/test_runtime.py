"""End-to-end registration tests with a fake PluginContext.

Proves the plugin loads through the real package layout, registers exactly the
declared surfaces, and returns the documented pre_tool_call block directive.
No Hermes install is required.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "write_gate_pkg", _HERE / "__init__.py", submodule_search_locations=[str(_HERE)]
)
plugin = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["write_gate_pkg"] = plugin  # required for `from . import ...`
_spec.loader.exec_module(plugin)


class State:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakeCtx:
    def __init__(self):
        self.tools = {}
        self.hooks = {}
        self.commands = {}
        self.config = {}
        self.state = State()

    def register_tool(self, name, toolset=None, schema=None, handler=None, **kw):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_hook(self, name, callback, **kw):
        self.hooks[name] = callback

    def register_command(self, name, handler, description=None, **kw):
        self.commands[name] = handler

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value


def build():
    ctx = FakeCtx()
    plugin.register(ctx)
    return ctx


class Registration(unittest.TestCase):
    def test_exact_surfaces(self):
        ctx = build()
        self.assertEqual(list(ctx.tools), ["write_gate_check"])
        self.assertEqual(list(ctx.hooks), ["pre_tool_call"])
        self.assertEqual(list(ctx.commands), ["writegate"])

    def test_callback_accepts_kwargs(self):
        import inspect

        ctx = build()
        sig = inspect.signature(ctx.hooks["pre_tool_call"])
        self.assertTrue(
            any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        )


class Enforcement(unittest.TestCase):
    def setUp(self):
        self.ctx = build()
        self.hook = self.ctx.hooks["pre_tool_call"]

    def call(self, tool, args):
        return self.hook(tool, args, "task-1", extra="future-field")

    def test_bad_json_is_blocked_with_precise_error(self):
        result = self.call(
            "write_file", {"path": "config/app.json", "content": '{\n "a": 1,\n}'}
        )
        self.assertEqual(result["action"], "block")
        self.assertIn("refused to write config/app.json", result["message"])
        self.assertIn("invalid JSON", result["message"])
        self.assertIn("line", result["message"])
        self.assertIn("was NOT written", result["message"])

    def test_good_json_passes(self):
        result = self.call(
            "write_file", {"path": "config/app.json", "content": '{"a": 1}'}
        )
        self.assertIsNone(result)

    def test_bad_python_is_blocked(self):
        result = self.call(
            "write_file", {"path": "m.py", "content": "def f(:\n    pass\n"}
        )
        self.assertEqual(result["action"], "block")
        self.assertIn("invalid Python", result["message"])

    def test_read_only_tool_is_untouched(self):
        self.assertIsNone(self.call("read_file", {"path": "a.json", "content": "{"}))

    def test_terminal_is_untouched(self):
        self.assertIsNone(self.call("terminal", {"command": "echo hi"}))

    def test_no_content_is_untouched(self):
        self.assertIsNone(self.call("write_file", {"path": "a.json"}))

    def test_observe_mode_reports_but_does_not_block(self):
        self.ctx.set_config("mode", "observe")
        self.assertIsNone(
            self.call("write_file", {"path": "a.json", "content": "{"})
        )
        self.assertEqual(self.ctx.state.get("stats")["blocked"], 1)

    def test_disabled_is_untouched(self):
        self.ctx.set_config("enabled", False)
        self.assertIsNone(
            self.call("write_file", {"path": "a.json", "content": "{"})
        )
        self.assertEqual(self.ctx.state.get("stats"), None)

    def test_counters_accumulate(self):
        self.call("write_file", {"path": "a.json", "content": "{"})
        self.call("write_file", {"path": "a.json", "content": "{}"})
        stats = self.ctx.state.get("stats")
        self.assertEqual(stats["checked"], 2)
        self.assertEqual(stats["blocked"], 1)
        self.assertEqual(stats["passed"], 1)

    def test_bad_args_do_not_raise(self):
        self.assertIsNone(self.call("write_file", None))
        self.assertIsNone(self.call("write_file", {"path": 42, "content": "{}"}))


class ToolAndCommand(unittest.TestCase):
    def setUp(self):
        self.ctx = build()

    def test_check_tool_returns_json_string(self):
        handler = self.ctx.tools["write_gate_check"]["handler"]
        out = json.loads(handler({"path": "a.json", "content": "{"}))
        self.assertFalse(out["ok"])
        self.assertTrue(out["issues"])
        out = json.loads(handler({"path": "a.json", "content": "{}"}))
        self.assertTrue(out["ok"])

    def test_check_tool_never_raises(self):
        handler = self.ctx.tools["write_gate_check"]["handler"]
        out = json.loads(handler({}))
        self.assertFalse(out["ok"])

    def test_slash_command_status_and_toggles(self):
        cmd = self.ctx.commands["writegate"]
        self.assertIn("write-gate v1.0.0", cmd("status"))
        self.assertIn("off", cmd("off"))
        self.assertIn("off", cmd(""))
        self.assertIn("observe", cmd("observe"))
        self.assertIn("usage:", cmd("bogus"))


if __name__ == "__main__":
    unittest.main()
