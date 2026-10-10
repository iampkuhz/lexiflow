"""Tests for scripts.verification.planning.check_definitions.

Covers: YAML loading, schema validation, required fields, duplicate
check IDs, invalid commands, scope filtering, check ID filtering.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.verification.planning.check_definitions import (
    DeclarationError,
    filter_checks_by_ids,
    filter_checks_by_scope,
    load_declarations,
)
from tests.verification.declaration_fixtures import declaration_document


def _write_declarations(root: Path, data: dict) -> None:
    harness = root / "config/engineering"
    harness.mkdir(parents=True, exist_ok=True)
    document = declaration_document(data.get("checks", []))
    if "schema_version" in data:
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

    def test_unknown_check_fields_and_removed_execution_layer_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                {
                    "checks": [_VALID_CHECK],
                },
            )
            path = root / "config/engineering/module-checks.yaml"
            raw = yaml.safe_load(path.read_text())
            raw["checks"][0]["cwd"] = "."
            path.write_text(yaml.safe_dump(raw))
            with self.assertRaises(DeclarationError):
                load_declarations(root)

            _write_declarations(
                root,
                {
                    "checks": [_VALID_CHECK],
                },
            )
            raw = yaml.safe_load(path.read_text())
            raw["executions"] = {"unused": dict(raw["checks"][0])}
            path.write_text(yaml.safe_dump(raw))
            with self.assertRaises(DeclarationError):
                load_declarations(root)

    def test_only_current_self_contained_schema_and_argv_are_accepted(self):
        from scripts.verification.planning.check_definitions import _parse_declarations

        document = declaration_document([_VALID_CHECK])
        for command in ("python3 -c pass", None, ["python3", 1], [""]):
            document["checks"][0]["command"] = command
            with self.subTest(command=command), self.assertRaises(DeclarationError):
                _parse_declarations(yaml.safe_dump(document).encode())
        document = declaration_document([_VALID_CHECK])
        document["schema_version"] = "lexiflow.module-checks.v3"
        with self.assertRaises(DeclarationError) as caught:
            _parse_declarations(yaml.safe_dump(document).encode())
        self.assertEqual(caught.exception.code, "declarations-shape")

    def test_input_and_trigger_paths_use_only_safe_literal_grammar(self):
        from scripts.verification.planning.check_definitions import _parse_declarations

        invalid = (
            ".",
            "a/../b",
            "a//b",
            "a/./b",
            "/a",
            "a\\b",
            "a/*",
            "a/?",
            "a/[b]",
            "a\x00",
        )
        for path in invalid:
            for field in ("input_paths", "triggers"):
                document = declaration_document([_VALID_CHECK])
                check = document["checks"][0]
                if field == "input_paths":
                    check[field] = [path]
                else:
                    check["scenarios"] = ["change-targeted"]
                    check[field] = [{"path": path}]
                with (
                    self.subTest(path=path, field=field),
                    self.assertRaises(DeclarationError),
                ):
                    _parse_declarations(yaml.safe_dump(document).encode())

    def test_real_registry_has_one_declaration_and_one_line_argv_per_check(self):
        path = Path(__file__).resolve().parents[2] / "config/engineering/module-checks.yaml"
        source = path.read_text()
        raw = yaml.safe_load(source)
        self.assertEqual(
            set(raw), {"checks", "scenario_policy"}
        )
        self.assertEqual(len(raw["checks"]), 23)
        command_lines = [
            line for line in source.splitlines() if line.startswith("  command:")
        ]
        self.assertEqual(len(command_lines), 23)
        self.assertTrue(
            all(
                line.startswith("  command: [") and line.endswith("]")
                for line in command_lines
            )
        )
        for check in raw["checks"]:
            self.assertNotIn("execution", check)
            self.assertIsInstance(check["command"], list)
            self.assertTrue(check["input_paths"])

    def test_canonical_scene_views_and_loads_are_isolated(self):
        root = Path(__file__).resolve().parents[2]
        data = load_declarations(root)
        baseline = filter_checks_by_scope(data["checks"], "repository-baseline")
        targeted = filter_checks_by_scope(data["checks"], "change-targeted")
        paired = [
            next(c for c in view if c["check_id"] == "eng.release.version")
            for view in (baseline, targeted)
        ]
        paired[0]["command"].append("mutation")
        paired[0]["triggers"].append({"path": "mutation"})
        self.assertNotIn("mutation", paired[1]["command"])
        self.assertNotIn({"path": "mutation"}, paired[1]["triggers"])
        self.assertNotIn("mutation", load_declarations(root)["checks"][0]["command"])

    def test_removed_execution_reference_is_a_declaration_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                {
                    "checks": [_VALID_CHECK],
                },
            )
            path = root / "config/engineering/module-checks.yaml"
            raw = yaml.safe_load(path.read_text())
            raw["checks"][0]["execution"] = []
            path.write_text(yaml.safe_dump(raw))
            with self.assertRaises(DeclarationError):
                load_declarations(root)

    def test_non_string_check_id_and_scope_are_declaration_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                {
                    "checks": [_VALID_CHECK],
                },
            )
            path = root / "config/engineering/module-checks.yaml"
            raw = yaml.safe_load(path.read_text())
            raw["checks"][0]["check_id"] = []
            path.write_text(yaml.safe_dump(raw))
            with self.assertRaises(DeclarationError):
                load_declarations(root)
            _write_declarations(
                root,
                {
                    "checks": [_VALID_CHECK],
                },
            )
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
            self.assertIs(checks["eng.release." + name]["transaction_reuse"], True)
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
                    "checks": [_VALID_CHECK],
                },
            )
            data = load_declarations(root)
            self.assertNotIn("schema_version", data)
            self.assertEqual(len(data["checks"]), 1)
            self.assertIs(data["checks"][0]["transaction_reuse"], False)

    def test_transaction_reuse_requires_boolean_and_defaults_to_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            check = {**_VALID_CHECK, "transaction_reuse": "true"}
            _write_declarations(
                root,
                {
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
                    "scripts.checks.release.source.check_synthetic_release_lifecycle",
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
            self.assertEqual(ctx.exception.code, "declarations-shape")

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

    def test_invalid_targeted_triggers_rejected(self):
        from scripts.verification.planning.check_definitions import _parse_declarations

        document = declaration_document([_VALID_CHECK])
        check = document["checks"][0]
        check["scenarios"] = ["change-targeted"]
        for triggers in (None, [{"path": "**"}], [{"path": "test/", "extra": True}]):
            check["triggers"] = triggers
            with self.subTest(triggers=triggers), self.assertRaises(DeclarationError):
                _parse_declarations(yaml.safe_dump(document).encode())


class TestFilterChecksByScope(unittest.TestCase):
    def test_scene_views_preserve_membership_without_duplicate_ids(self):
        checks = load_declarations(Path(__file__).resolve().parents[2])["checks"]
        self.assertEqual(len(checks), len({c["check_id"] for c in checks}))
        self.assertEqual(len(checks), 23)
        self.assertEqual(len(filter_checks_by_scope(checks, "repository-baseline")), 19)
        self.assertEqual(len(filter_checks_by_scope(checks, "change-targeted")), 22)


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
