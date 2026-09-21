"""Tests for scripts.verification.declarations.

Covers: YAML loading, schema validation, required fields, duplicate
check IDs, invalid commands, scope filtering, check ID filtering.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.verification.declarations import (
    DeclarationError,
    filter_checks_by_ids,
    filter_checks_by_scope,
    load_declarations,
)


def _write_declarations(root: Path, data: dict) -> None:
    harness = root / "harness"
    harness.mkdir(exist_ok=True)
    for check in data.get("checks", []):
        check.setdefault("module_dependencies", [])
        check.setdefault("required_environment", [])
        check.setdefault("input_paths", [])
        check.setdefault("result_contract", {"type": "exit-code", "completeness_guarantee": "fixture-owned command completion"})
    (harness / "module-checks.yaml").write_text(
        yaml.dump(data, default_flow_style=False), encoding="utf-8",
    )


_VALID_CHECK = {
    "check_id": "test.check",
    "module": "test",
    "command": ["python3", "-c", "pass"],
    "cwd": ".",
    "timeout_seconds": 60,
    "scope": "repository-baseline",
    "triggers": [{"path": "test/"}],
}


class TestLoadDeclarations(unittest.TestCase):
    def test_valid_declarations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, {
                "schema_version": "lexiflow.module-checks.v1",
                "checks": [_VALID_CHECK],
            })
            data = load_declarations(root)
            self.assertEqual(data["schema_version"], "lexiflow.module-checks.v1")
            self.assertEqual(len(data["checks"]), 1)

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(DeclarationError) as ctx:
                load_declarations(Path(tmpdir))
            self.assertEqual(ctx.exception.code, "declarations-missing")

    def test_wrong_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, {
                "schema_version": "wrong",
                "checks": [],
            })
            with self.assertRaises(DeclarationError) as ctx:
                load_declarations(root)
            self.assertEqual(ctx.exception.code, "declarations-schema")

    def test_missing_required_field(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            check = dict(_VALID_CHECK)
            del check["command"]
            _write_declarations(root, {
                "schema_version": "lexiflow.module-checks.v1",
                "checks": [check],
            })
            with self.assertRaises(DeclarationError) as ctx:
                load_declarations(root)
            self.assertEqual(ctx.exception.code, "declarations-shape")

    def test_duplicate_check_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, {
                "schema_version": "lexiflow.module-checks.v1",
                "checks": [_VALID_CHECK, _VALID_CHECK],
            })
            with self.assertRaises(DeclarationError) as ctx:
                load_declarations(root)
            self.assertEqual(ctx.exception.code, "duplicate-check-id")

    def test_empty_command_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            check = dict(_VALID_CHECK)
            check["command"] = []
            _write_declarations(root, {
                "schema_version": "lexiflow.module-checks.v1",
                "checks": [check],
            })
            with self.assertRaises(DeclarationError):
                load_declarations(root)

    def test_invalid_timeout_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            check = dict(_VALID_CHECK)
            check["timeout_seconds"] = -1
            _write_declarations(root, {
                "schema_version": "lexiflow.module-checks.v1",
                "checks": [check],
            })
            with self.assertRaises(DeclarationError):
                load_declarations(root)

    def test_invalid_scope_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            check = dict(_VALID_CHECK)
            check["scope"] = "invalid-scope"
            _write_declarations(root, {
                "schema_version": "lexiflow.module-checks.v1",
                "checks": [check],
            })
            with self.assertRaises(DeclarationError):
                load_declarations(root)


class TestFilterChecksByScope(unittest.TestCase):
    def test_repository_baseline_only_matching(self):
        checks = [
            {"check_id": "a", "scope": "change-targeted"},
            {"check_id": "b", "scope": "repository-baseline"},
        ]
        result = filter_checks_by_scope(checks, "repository-baseline")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["check_id"], "b")

    def test_change_targeted_only(self):
        checks = [
            {"check_id": "a", "scope": "change-targeted"},
            {"check_id": "b", "scope": "repository-baseline"},
        ]
        result = filter_checks_by_scope(checks, "change-targeted")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["check_id"], "a")


class TestFilterChecksByIds(unittest.TestCase):
    def test_no_filter_returns_all(self):
        checks = [{"check_id": "a"}, {"check_id": "b"}]
        result = filter_checks_by_ids(checks, None)
        self.assertEqual(len(result), 2)

    def test_filter_by_ids(self):
        checks = [{"check_id": "a"}, {"check_id": "b"}, {"check_id": "c"}]
        result = filter_checks_by_ids(checks, ["a", "c"])
        self.assertEqual(len(result), 2)
        ids = {c["check_id"] for c in result}
        self.assertEqual(ids, {"a", "c"})


if __name__ == "__main__":
    unittest.main()
