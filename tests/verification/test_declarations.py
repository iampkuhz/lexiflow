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
from tests.verification.declaration_fixtures import declaration_document


def _write_declarations(root: Path, data: dict) -> None:
    harness = root / "harness"
    harness.mkdir(exist_ok=True)
    document = declaration_document(data.get("checks", []))
    if data.get("schema_version") not in {
        None,
        "lexiflow.module-checks.v2",
    }:
        document["schema_version"] = data["schema_version"]
    if data.get("checks") is not None and len(data["checks"]) != len(
        document["checks"]
    ):
        document["checks"] = data["checks"]
    (harness / "module-checks.yaml").write_text(
        yaml.dump(document, default_flow_style=False),
        encoding="utf-8",
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
    def test_normalized_fixed_execution_contract_is_literal(self):
        check = load_declarations(Path(__file__).resolve().parents[2])["checks"][0]
        self.assertEqual(check["cwd"], ".")
        self.assertEqual(check["executable"], "node")
        self.assertEqual(
            check["result_contract"],
            {
                "type": "json-stdout",
                "required_fields": [
                    "status",
                    "checks_run",
                    "failures",
                    "errors",
                    "skipped",
                    "reason",
                ],
                "allowed_statuses": ["PASS", "BLOCKED", "FAIL"],
                "minimum": {"checks_run": 1},
                "equals": {"failures": 0, "errors": 0, "skipped": 0},
            },
        )

    def test_unknown_and_unreferenced_execution_definitions_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                {
                    "schema_version": "lexiflow.module-checks.v2",
                    "checks": [_VALID_CHECK],
                },
            )
            path = root / "harness/module-checks.yaml"
            raw = yaml.safe_load(path.read_text())
            raw["executions"]["fixture-0"]["cwd"] = "."
            path.write_text(yaml.safe_dump(raw))
            with self.assertRaises(DeclarationError):
                load_declarations(root)

            _write_declarations(
                root,
                {
                    "schema_version": "lexiflow.module-checks.v2",
                    "checks": [_VALID_CHECK],
                },
            )
            raw = yaml.safe_load(path.read_text())
            raw["executions"]["unused"] = dict(raw["executions"]["fixture-0"])
            path.write_text(yaml.safe_dump(raw))
            with self.assertRaises(DeclarationError):
                load_declarations(root)

    def test_shared_execution_normalization_isolated_per_check_and_load(self):
        data = load_declarations(Path(__file__).resolve().parents[2])
        paired = [
            item
            for item in data["checks"]
            if item["check_id"]
            in {"eng.release.version", "eng.release.version-on-change"}
        ]
        self.assertEqual(len(paired), 2)
        paired[0]["command"].append("mutation")
        paired[0]["input_paths"].append("mutation")
        paired[0]["result_contract"]["required_fields"].append("mutation")
        reread = load_declarations(Path(__file__).resolve().parents[2])
        self.assertNotIn("mutation", paired[1]["command"])
        self.assertNotIn("mutation", paired[1]["input_paths"])
        self.assertNotIn("mutation", paired[1]["result_contract"]["required_fields"])
        self.assertNotIn("mutation", reread["checks"][0]["command"])

    def test_non_string_execution_reference_is_a_declaration_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                {
                    "schema_version": "lexiflow.module-checks.v2",
                    "checks": [_VALID_CHECK],
                },
            )
            path = root / "harness/module-checks.yaml"
            raw = yaml.safe_load(path.read_text())
            raw["checks"][0]["execution"] = []
            path.write_text(yaml.safe_dump(raw))
            with self.assertRaises(DeclarationError):
                load_declarations(root)

    def test_non_string_check_id_and_scope_are_declaration_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, {"schema_version": "lexiflow.module-checks.v2", "checks": [_VALID_CHECK]})
            path = root / "harness/module-checks.yaml"
            raw = yaml.safe_load(path.read_text())
            raw["checks"][0]["check_id"] = []
            path.write_text(yaml.safe_dump(raw))
            with self.assertRaises(DeclarationError):
                load_declarations(root)
            _write_declarations(root, {"schema_version": "lexiflow.module-checks.v2", "checks": [_VALID_CHECK]})
            raw = yaml.safe_load(path.read_text())
            raw["checks"][0]["scope"] = []
            path.write_text(yaml.safe_dump(raw))
            with self.assertRaises(DeclarationError):
                load_declarations(root)

    def test_offline_reuse_is_explicit_and_runtime_remains_conservative(self):
        checks = {
            check["check_id"]: check
            for check in load_declarations(Path(__file__).resolve().parents[2])[
                "checks"
            ]
        }
        for name in ("lifecycle-install", "lifecycle", "build"):
            for suffix in ("", "-on-change"):
                self.assertIs(
                    checks["eng.release." + name + suffix]["transaction_reuse"], True
                )
        self.assertIs(checks["eng.release.docker-contract"]["transaction_reuse"], True)
        for group in ("surface", "shell", "local", "node"):
            self.assertIs(
                checks[f"eng.release.docker-{group}-on-change"]["transaction_reuse"],
                True,
            )
        for check_id, check in checks.items():
            if any(
                token in check_id
                for token in (
                    "lifecycle-runtime",
                    "candidate-runtime",
                    "python-quality",
                    "eng.backend",
                    "eng.extension",
                )
            ):
                self.assertIs(check["transaction_reuse"], False, check_id)

    def test_valid_declarations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                {
                    "schema_version": "lexiflow.module-checks.v2",
                    "checks": [_VALID_CHECK],
                },
            )
            data = load_declarations(root)
            self.assertEqual(data["schema_version"], "lexiflow.module-checks.v2")
            self.assertEqual(len(data["checks"]), 1)
            self.assertIs(data["checks"][0]["transaction_reuse"], False)

    def test_transaction_reuse_requires_boolean_and_defaults_to_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            check = {**_VALID_CHECK, "transaction_reuse": "true"}
            _write_declarations(
                root,
                {
                    "schema_version": "lexiflow.module-checks.v2",
                    "checks": [check],
                },
            )
            with self.assertRaises(DeclarationError) as ctx:
                load_declarations(root)
            self.assertEqual(ctx.exception.code, "declarations-shape")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            check = {**_VALID_CHECK, "transaction_reuse": True}
            _write_declarations(
                root,
                {
                    "checks": [check],
                },
            )
            data = load_declarations(root)
            self.assertIs(data["checks"][0]["transaction_reuse"], True)

    def test_runtime_transport_cannot_enable_transaction_reuse(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            check = {
                **_VALID_CHECK,
                "check_id": "eng.release.lifecycle-runtime",
                "command": [
                    "python3",
                    "-m",
                    "scripts.environment.release_runtime_check",
                ],
                "executable": "python3",
                "transaction_reuse": True,
                "result_contract": {
                    "type": "json-stdout",
                    "required_fields": ["status", "reason"],
                    "allowed_statuses": ["PASS", "BLOCKED", "FAIL"],
                },
            }
            _write_declarations(
                root,
                {
                    "checks": [check],
                },
            )
            with self.assertRaises(DeclarationError) as ctx:
                load_declarations(root)
            self.assertEqual(ctx.exception.code, "declarations-shape")

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(DeclarationError) as ctx:
                load_declarations(Path(tmpdir))
            self.assertEqual(ctx.exception.code, "declarations-missing")

    def test_wrong_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                {
                    "schema_version": "wrong",
                    "checks": [],
                },
            )
            with self.assertRaises(DeclarationError) as ctx:
                load_declarations(root)
            self.assertEqual(ctx.exception.code, "declarations-schema")

    def test_missing_required_field(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            check = dict(_VALID_CHECK)
            del check["command"]
            _write_declarations(
                root,
                {
                    "checks": [check],
                },
            )
            with self.assertRaises(DeclarationError) as ctx:
                load_declarations(root)
            self.assertEqual(ctx.exception.code, "declarations-shape")

    def test_duplicate_check_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                {
                    "checks": [_VALID_CHECK, _VALID_CHECK],
                },
            )
            with self.assertRaises(DeclarationError) as ctx:
                load_declarations(root)
            self.assertEqual(ctx.exception.code, "duplicate-check-id")

    def test_empty_command_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            check = dict(_VALID_CHECK)
            check["command"] = []
            _write_declarations(
                root,
                {
                    "checks": [check],
                },
            )
            with self.assertRaises(DeclarationError):
                load_declarations(root)

    def test_invalid_timeout_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            check = dict(_VALID_CHECK)
            check["timeout_seconds"] = -1
            _write_declarations(
                root,
                {
                    "checks": [check],
                },
            )
            with self.assertRaises(DeclarationError):
                load_declarations(root)

    def test_invalid_scope_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            check = dict(_VALID_CHECK)
            check["scope"] = "invalid-scope"
            _write_declarations(
                root,
                {
                    "checks": [check],
                },
            )
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
