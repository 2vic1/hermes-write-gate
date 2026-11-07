"""Standalone unit tests: no Hermes install required."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "wg_checkers", _HERE / "checkers.py"
)
checkers = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(checkers)


class SyntaxChecks(unittest.TestCase):
    def test_valid_json(self):
        self.assertEqual(checkers.check('{"a": 1}', "a.json", []), [])

    def test_broken_json_reports_line_and_column(self):
        issues = checkers.check('{\n "a": 1,\n}', "a.json", [])
        self.assertEqual(len(issues), 1)
        # Exact line can differ between CPython versions (the 3.13+ decoder
        # points at the trailing comma itself). What matters is that the model
        # gets a real line/column rather than a generic "invalid".
        self.assertRegex(issues[0], r"line \d+, column \d+")
        self.assertIn("invalid JSON", issues[0])

    def test_valid_python(self):
        self.assertEqual(checkers.check("def f():\n    return 1\n", "m.py", []), [])

    def test_broken_python(self):
        issues = checkers.check("def f(:\n    pass\n", "m.py", [])
        self.assertTrue(issues)
        self.assertIn("invalid Python", issues[0])

    def test_valid_toml(self):
        self.assertEqual(checkers.check('[x]\na = 1\n', "c.toml", []), [])

    def test_broken_toml(self):
        issues = checkers.check("[x\na = 1\n", "c.toml", [])
        self.assertTrue(issues)
        self.assertIn("invalid TOML", issues[0])

    def test_unknown_extension_is_not_parsed(self):
        self.assertEqual(checkers.check("this is not json {{{", "notes.txt", []), [])


class UniversalChecks(unittest.TestCase):
    def test_nul_byte(self):
        issues = checkers.check("ok\x00bad", "a.txt", [])
        self.assertTrue(any("NUL" in i for i in issues))

    def test_oversize(self):
        issues = checkers.check("x" * (checkers.MAX_CONTENT_BYTES + 1), "a.txt", [])
        self.assertTrue(any("safety cap" in i for i in issues))


class RuleContracts(unittest.TestCase):
    def test_glob_matches_path(self):
        rules = [{"glob": "config/*.json", "required": ["\"version\""]}]
        issues = checkers.check('{"name": "x"}', "config/app.json", rules)
        self.assertTrue(any("missing required pattern" in i for i in issues))

    def test_glob_does_not_match_other_path(self):
        rules = [{"glob": "config/*.json", "required": ["\"version\""]}]
        self.assertEqual(checkers.check('{"name": "x"}', "src/app.json", rules), [])

    def test_forbidden_pattern(self):
        rules = [{"glob": "**", "forbidden": ["sk-[A-Za-z0-9]{24,}"]}]
        token = "sk-" + "a" * 30
        issues = checkers.check("key = " + token, "any.txt", rules)
        self.assertTrue(any("forbidden pattern" in i for i in issues))

    def test_max_lines(self):
        rules = [{"glob": "**", "max_lines": 2}]
        issues = checkers.check("a\nb\nc\n", "any.txt", rules)
        self.assertTrue(any("max_lines" in i for i in issues))

    def test_invalid_regex_is_reported_not_raised(self):
        rules = [{"glob": "**", "forbidden": ["["]}]
        issues = checkers.check("hello", "any.txt", rules)
        self.assertTrue(any("invalid regex" in i for i in issues))

    def test_syntax_override(self):
        rules = [{"glob": "**", "syntax": "json"}]
        issues = checkers.check("not json", "weird.dat", rules)
        self.assertTrue(any("invalid JSON" in i for i in issues))

    def test_issues_are_deduplicated_and_bounded(self):
        rules = [{"glob": "**", "forbidden": ["a"]}, {"glob": "*", "forbidden": ["a"]}]
        issues = checkers.check("a", "x.txt", rules)
        self.assertEqual(len(issues), 1)
        self.assertLessEqual(len(issues), checkers.MAX_ISSUES)


class RulesLoading(unittest.TestCase):
    def test_missing_file_is_safe(self):
        self.assertEqual(checkers.load_rules(""), [])
        self.assertEqual(checkers.load_rules("/definitely/not/here.json"), [])

    def test_malformed_shape_is_safe(self):
        import json
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write(json.dumps({"rules": "not a list"}))
            name = f.name
        self.assertEqual(checkers.load_rules(name), [])
        Path(name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
