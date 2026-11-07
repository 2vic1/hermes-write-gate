"""Tool handlers for write-gate.

Handlers always return a JSON string, accept **kwargs, and never raise.
"""

from __future__ import annotations

import json

from . import checkers


def check(args: dict, **kwargs) -> str:
    try:
        args = args or {}
        path = str(args.get("path") or "")
        content = args.get("content")
        if not isinstance(content, str):
            return json.dumps({"ok": False, "error": "content must be a string"})
        issues = checkers.check(content, path)
        return json.dumps(
            {
                "ok": not issues,
                "path": path,
                "issues": issues,
                "hint": "Fix the listed problems, then call write_file again."
                if issues
                else "Content passed every active check.",
            }
        )
    except Exception as exc:  # never break the agent
        return json.dumps({"ok": False, "error": "write-gate check failed: {}".format(exc)})
