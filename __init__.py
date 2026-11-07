"""write-gate — deterministic pre-write content gate for Hermes Agent.

Hermes surfaces used (all public, all documented):
  * plugin.yaml + register(ctx) native plugin
  * ctx.register_tool()          -> write_gate_check
  * ctx.register_hook()          -> pre_tool_call
  * ctx.register_command()       -> /writegate
  * ctx.get_config / set_config  -> own settings namespace only
  * ctx.state                    -> profile-scoped counters

No core overrides, no private imports, no network, no subprocesses, no
credentials, no binaries, no self-updating code.
"""

from __future__ import annotations

import logging

from . import checkers, schemas, tools

logger = logging.getLogger(__name__)

# Tool names that can create or replace file content. Deliberately narrow:
# read-only tools and the terminal tool are never touched.
_WRITE_TOOLS = frozenset(
    {
        "write_file",
        "create_file",
        "file_write",
        "replace_in_file",
        "edit_file",
        "patch",
        "write",
    }
)

_CONTENT_KEYS = (
    "content",
    "new_content",
    "file_content",
    "text",
    "code",
    "new_string",
    "patch",
)

_PATH_KEYS = ("path", "file_path", "target_file", "filename", "file")

_USAGE = (
    "usage: /writegate status | on | off | enforce | observe | rules <path>"
)


def _content_of(args: dict) -> str | None:
    for key in _CONTENT_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _path_of(args: dict) -> str:
    for key in _PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def register(ctx):
    """Wire the tool, the pre_tool_call veto and the slash command."""

    def _settings():
        try:
            enabled = bool(ctx.get_config("enabled", default=True))
            mode = str(ctx.get_config("mode", default="enforce"))
            rules_file = str(ctx.get_config("rules_file", default="") or "")
        except Exception:
            return True, "enforce", ""
        if mode not in ("enforce", "observe"):
            mode = "enforce"
        return enabled, mode, rules_file

    def _stats():
        try:
            return ctx.state.get(
                "stats", default={"checked": 0, "blocked": 0, "passed": 0}
            )
        except Exception:
            return None

    def _bump(blocked: bool) -> None:
        stats = _stats()
        if not isinstance(stats, dict):
            return
        try:
            stats["checked"] = int(stats.get("checked", 0)) + 1
            key = "blocked" if blocked else "passed"
            stats[key] = int(stats.get(key, 0)) + 1
            ctx.state.set("stats", stats)
        except Exception:
            pass

    def _pre_tool_call(tool_name, args, task_id, **kwargs):
        """Veto a bad write with a precise error the model can act on.

        Hermes short-circuits the tool and returns `message` to the model as the
        tool-call error, so the model can retry with corrected content inside the
        same tool-calling loop. This is enforcement, not an instruction: a
        prompt or a skill can be ignored, this cannot.
        """
        try:
            if tool_name not in _WRITE_TOOLS:
                return None
            enabled, mode, rules_file = _settings()
            if not enabled:
                return None
            args = args if isinstance(args, dict) else {}
            content = _content_of(args)
            if content is None:
                return None  # nothing we can check deterministically
            path = _path_of(args)
            rules = checkers.load_rules(rules_file)
            issues = checkers.check(content, path, rules)
            _bump(bool(issues))
            if not issues or mode != "enforce":
                return None
            bullet = "\n".join("  - {}".format(i) for i in issues)
            message = (
                "[write-gate] refused to write {path}.\n"
                "{issues}\n"
                "The file was NOT written. Correct the content above and call "
                "{tool} again.".format(
                    path=path or "<unknown>", issues=bullet, tool=tool_name
                )
            )
            return {"action": "block", "message": message}
        except Exception:
            # Fail open. A guardrail must never take the agent down.
            logger.exception("write-gate pre_tool_call failed")
            return None

    def _command(raw_args: str):
        parts = (raw_args or "").strip().split()
        action = parts[0].lower() if parts else "status"

        if action == "status":
            enabled, mode, rules_file = _settings()
            stats = _stats() or {"checked": 0, "blocked": 0, "passed": 0}
            return (
                "write-gate v1.0.0 | {state} | mode={mode} | rules={rules}\n"
                "checked={checked} blocked={blocked} passed={passed}".format(
                    state="on" if enabled else "off",
                    mode=mode,
                    rules=rules_file or "(built-in syntax checks only)",
                    checked=stats.get("checked", 0),
                    blocked=stats.get("blocked", 0),
                    passed=stats.get("passed", 0),
                )
            )

        if action in ("on", "off"):
            try:
                ctx.set_config("enabled", action == "on")
            except Exception:
                return "write-gate: could not persist that setting."
            return "write-gate is now {}.".format(action)

        if action in ("enforce", "observe"):
            try:
                ctx.set_config("mode", action)
            except Exception:
                return "write-gate: could not persist that setting."
            return "write-gate mode is now {}.".format(action)

        if action == "rules" and len(parts) > 1:
            path = " ".join(parts[1:]).strip().strip('"').strip("'")
            rules = checkers.load_rules(path)
            if not rules:
                return "write-gate: no usable rules found at {}".format(path)
            try:
                ctx.set_config("rules_file", path)
            except Exception:
                return "write-gate: could not persist that setting."
            return "write-gate: loaded {} rule(s) from {}.".format(len(rules), path)

        return _USAGE

    ctx.register_tool(
        name="write_gate_check",
        toolset="write_gate",
        schema=schemas.CHECK,
        handler=tools.check,
    )
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_command(
        "writegate",
        _command,
        description="Inspect or configure the deterministic file-write gate.",
    )
