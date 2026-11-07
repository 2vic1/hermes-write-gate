"""Deterministic content checks for write-gate.

Pure standard library. No network, no subprocesses, no credentials, no imports
from Hermes internals. This module is deliberately importable on its own so it
can be unit-tested without a running agent.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import re
import tomllib
from pathlib import Path

MAX_CONTENT_BYTES = 2_000_000
MAX_ISSUES = 8

_SYNTAX_BY_SUFFIX = {
    "json": "json",
    "jsonc": None,
    "toml": "toml",
    "py": "python",
    "pyi": "python",
}


def check_json(text: str) -> list[str]:
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return [
            "invalid JSON at line {}, column {}: {}".format(
                exc.lineno, exc.colno, exc.msg
            )
        ]
    return []


def check_toml(text: str) -> list[str]:
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return ["invalid TOML: {}".format(exc)]
    return []


def check_python(text: str) -> list[str]:
    try:
        ast.parse(text)
    except SyntaxError as exc:
        where = "line {}".format(exc.lineno or 0)
        if exc.offset:
            where += ", column {}".format(exc.offset)
        return ["invalid Python at {}: {}".format(where, exc.msg)]
    return []


_PARSERS = {
    "json": check_json,
    "toml": check_toml,
    "python": check_python,
}


def _suffix(path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def _syntax_for(path: str) -> str | None:
    return _SYNTAX_BY_SUFFIX.get(_suffix(path))


def load_rules(rules_path: str) -> list[dict]:
    """Load optional per-glob contracts. Never raises."""
    if not rules_path:
        return []
    try:
        raw = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    except Exception:
        return []
    rules = raw.get("rules") if isinstance(raw, dict) else None
    if not isinstance(rules, list):
        return []
    return [r for r in rules if isinstance(r, dict)]


def _apply_rule(text: str, rule: dict) -> list[str]:
    issues: list[str] = []
    glob = str(rule.get("glob") or "**")

    syntax = rule.get("syntax")
    if syntax and syntax in _PARSERS:
        issues.extend(_PARSERS[syntax](text))

    max_lines = rule.get("max_lines")
    if isinstance(max_lines, int) and max_lines > 0:
        count = text.count("\n") + 1
        if count > max_lines:
            issues.append(
                "{} lines exceeds max_lines {}".format(count, max_lines)
            )

    max_bytes = rule.get("max_bytes")
    if isinstance(max_bytes, int) and max_bytes > 0:
        size = len(text.encode("utf-8", "replace"))
        if size > max_bytes:
            issues.append(
                "{} bytes exceeds max_bytes {}".format(size, max_bytes)
            )

    for label, key, present in (
        ("required", "required", True),
        ("forbidden", "forbidden", False),
    ):
        patterns = rule.get(key)
        if not isinstance(patterns, list):
            continue
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern:
                continue
            try:
                found = re.search(pattern, text) is not None
            except re.error:
                issues.append("invalid regex in {} rule: {!r}".format(label, pattern))
                continue
            if present and not found:
                issues.append("missing required pattern {!r}".format(pattern))
            if not present and found:
                issues.append("contains forbidden pattern {!r}".format(pattern))

    return issues


def check(content: str, path: str, rules: list[dict] | None = None) -> list[str]:
    """Return a list of human-readable problems. Empty list means the write is safe.

    The error strings are shown verbatim to the model, so they carry the exact
    line/column and the exact failing contract. That is what makes the retry
    converge instead of guessing.
    """
    issues: list[str] = []

    if "\x00" in content:
        issues.append("content contains NUL bytes")

    size = len(content.encode("utf-8", "replace"))
    if size > MAX_CONTENT_BYTES:
        issues.append(
            "{} bytes exceeds the {} byte safety cap".format(size, MAX_CONTENT_BYTES)
        )

    syntax = _syntax_for(path)
    if syntax:
        issues.extend(_PARSERS[syntax](content))

    for rule in rules or []:
        glob = str(rule.get("glob") or "**")
        if fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(_suffix(path), glob):
            issues.extend(_apply_rule(content, rule))

    # De-duplicate while preserving order, then bound the payload size.
    seen: set[str] = set()
    unique: list[str] = []
    for issue in issues:
        if issue not in seen:
            seen.add(issue)
            unique.append(issue)
    return unique[:MAX_ISSUES]
