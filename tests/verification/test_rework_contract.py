"""Public regression cases for ENG-WP-01 rework completeness rules."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import yaml

from scripts.verification.kernel import execute_single_check
from scripts.verification.scenarios import verify_changes, verify_repository


def _check(check_id: str, *, scope: str = "repository-baseline", triggers: list[str] | None = None, dependencies: list[str] | None = None, contract: dict | None = None) -> dict:
    return {
        "check_id": check_id,
        "module": check_id,
        "command": ["python3", "-c", "pass"],
        "cwd": ".",
        "timeout_seconds": 10,
        "scope": scope,
        "triggers": [{"path": path} for path in (triggers or ["src/"])],
        "module_dependencies": dependencies or [],
        "required_environment": [],
        "input_paths": [],
        "result_contract": contract or {"type": "exit-code", "completeness_guarantee": "fixture command owns all required work"},
    }


def _write(root: Path, checks: list[dict]) -> None:
    (root / "harness").mkdir(exist_ok=True)
    (root / "harness" / "module-checks.yaml").write_text(yaml.safe_dump({"schema_version": "lexiflow.module-checks.v1", "checks": checks}), encoding="utf-8")


def _git(root: Path) -> str:
    for args in (("init",), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Test")):
        subprocess.run(["git", *args], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (root / "seed").write_text("seed")
    subprocess.run(["git", "add", "."], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


class TestResultProtocol(unittest.TestCase):
    def test_json_contract_rejects_empty_or_skipped_report(self):
        contract = {"type": "json-stdout", "required_fields": ["status", "tests_run", "skipped"], "allowed_statuses": ["PASS"], "minimum": {"tests_run": 1}, "equals": {"skipped": 0}}
        check = _check("result.protocol", contract=contract)
        with tempfile.TemporaryDirectory() as temp:
            empty = execute_single_check(check, Path(temp), {}, runner=lambda *_: {"exit_code": 0, "exit_reason": "exited", "timed_out": False, "stdout": "", "stderr": "", "executed_argv": ["python3"]})
            skipped = execute_single_check(check, Path(temp), {}, runner=lambda *_: {"exit_code": 0, "exit_reason": "exited", "timed_out": False, "stdout": json.dumps({"status": "PASS", "tests_run": 1, "skipped": 1}), "stderr": "", "executed_argv": ["python3"]})
        self.assertEqual(empty["reason"], "result-report-invalid")
        self.assertEqual(skipped["reason"], "result-report-incomplete")

    def test_typed_non_pass_preserves_module_diagnostic(self):
        contract = {"type": "json-stdout", "required_fields": ["status", "tests_run", "skipped", "reason"], "allowed_statuses": ["PASS", "BLOCKED", "FAIL"], "minimum": {"tests_run": 1}, "equals": {"skipped": 0}}
        check = _check("result.blocked", contract=contract)
        with tempfile.TemporaryDirectory() as temp:
            result = execute_single_check(check, Path(temp), {}, runner=lambda *_: {"exit_code": 0, "exit_reason": "exited", "timed_out": False, "stdout": json.dumps({"status": "BLOCKED", "tests_run": 0, "skipped": 0, "reason": "dependency-missing"}), "stderr": "", "executed_argv": ["python3"]})
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("dependency-missing", result["reason"])

    def test_actual_input_path_drift_and_artifact_hash_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "input.txt").write_text("before")
            check = _check("result.drift")
            check["input_paths"] = ["input.txt"]
            def mutate(*_args):
                (root / "input.txt").write_text("after")
                return {"exit_code": 0, "exit_reason": "exited", "timed_out": False, "stdout": "", "stderr": "diagnostic", "executed_argv": ["python3"]}
            result = execute_single_check(check, root, {}, runner=mutate, run_id="input-drift")
            artifact = root / result["process"]["output_artifacts"]["stderr"]["locator"]
            self.assertEqual(result["reason"], "input-drift")
            self.assertTrue(artifact.is_file())
            self.assertEqual(result["process"]["output_artifacts"]["stderr"]["sha256"], __import__("hashlib").sha256(b"diagnostic").hexdigest())

    def test_declared_non_python_argv_is_not_rewritten(self):
        check = _check("argv.exact")
        check["command"] = ["git", "--version"]
        check.pop("executable", None)
        seen: list[list[str]] = []
        def runner(argv, *_args):
            seen.append(argv)
            return {"exit_code": 0, "exit_reason": "exited", "timed_out": False, "stdout": "", "stderr": "", "executed_argv": argv}
        with tempfile.TemporaryDirectory() as temp:
            result = execute_single_check(check, Path(temp), {}, runner=runner)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(seen, [["git", "--version"]])


class TestPublicCoverageRules(unittest.TestCase):
    def test_unknown_and_partial_repository_selection_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root, [_check("one"), _check("two")])
            unknown = verify_repository(root, check_ids=["missing"])
            partial = verify_repository(root, check_ids=["one"])
        self.assertEqual(unknown["reason"], "unknown-check-id")
        self.assertEqual(partial["result"], "BLOCKED")
        self.assertEqual(partial["reason"], "partial-check-selection")

    def test_public_repository_detects_declaration_config_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root, [_check("drift")])
            def mutate(_argv, _cwd, _env, _timeout, _executable):
                path = root / "harness" / "module-checks.yaml"
                path.write_text(path.read_text(encoding="utf-8") + "# changed during run\n", encoding="utf-8")
                return {"exit_code": 0, "exit_reason": "exited", "timed_out": False, "stdout": "", "stderr": "", "executed_argv": ["python3"]}
            report = verify_repository(root, runner=mutate)
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(report["checks"][0]["reason"], "input-drift")

    def test_replacing_config_after_load_before_first_check_fails_frozen_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            checks = [_check("frozen")]
            _write(root, checks)
            before = __import__("hashlib").sha256((root / "harness" / "module-checks.yaml").read_bytes()).hexdigest()
            from scripts.verification import scenarios
            original = scenarios.load_declarations_snapshot
            calls = 0
            def load_then_replace(path: Path):
                nonlocal calls
                data, snapshot = original(path)
                replacement = _check("frozen")
                replacement["command"] = ["python3", "-c", "raise SystemExit(99)"]
                _write(root, [replacement])
                return data, snapshot
            def runner(*_args):
                nonlocal calls
                calls += 1
                return {"exit_code": 0, "exit_reason": "exited", "timed_out": False, "stdout": "", "stderr": "", "executed_argv": ["python3"]}
            with patch("scripts.verification.scenarios.load_declarations_snapshot", side_effect=load_then_replace):
                report = verify_repository(root, runner=runner)
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(report["checks"][0]["reason"], "input-drift")
        self.assertEqual(calls, 0)
        self.assertEqual(report["declaration_sha256"], before)

    def test_later_check_mutating_earlier_input_fails_full_frozen_closure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.txt").write_text("a-before")
            (root / "b.txt").write_text("b-before")
            first, second = _check("first"), _check("second")
            first["input_paths"] = ["a.txt"]
            second["input_paths"] = ["b.txt"]
            first["command"] = ["python3", "-c", "pass # first"]
            second["command"] = ["python3", "-c", "pass # second"]
            _write(root, [first, second])
            def runner(argv, *_args):
                if argv[-1] == "pass # second":
                    (root / "a.txt").write_text("a-after")
                return {"exit_code": 0, "exit_reason": "exited", "timed_out": False, "stdout": "", "stderr": "", "executed_argv": argv}
            report = verify_repository(root, runner=runner)
        checks = {item["check_id"]: item for item in report["checks"]}
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(checks["first"]["reason"], "input-drift")
        self.assertEqual(checks["second"]["status"], "PASS")

    def test_partial_changed_path_coverage_blocks_public_api(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); base = _git(root)
            _write(root, [_check("src-only", scope="change-targeted", triggers=["src/"])])
            (root / "src").mkdir(); (root / "src" / "a.py").write_text("x")
            (root / "other").mkdir(); (root / "other" / "b.py").write_text("x")
            report = verify_changes(root, base=base)
        self.assertEqual(report["result"], "BLOCKED")
        self.assertIn("uncovered-path:other/b.py", report["coverage_gaps"])

    def test_no_diff_runs_full_set_and_dependency_errors_are_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _write(root, [_check("baseline"), _check("targeted", scope="change-targeted")])
            base = _git(root)
            full = verify_changes(root, base=base)
        self.assertEqual({item["check_id"] for item in full["checks"]}, {"baseline", "targeted"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _write(root, [_check("cycle-a", dependencies=["cycle-b"]), _check("cycle-b", dependencies=["cycle-a"])])
            cycle = verify_repository(root)
        self.assertEqual(cycle["reason"], "module-dependency-cycle")


if __name__ == "__main__":
    unittest.main()

class TestControlledRuntimeInjection(unittest.TestCase):
    def test_explicit_runtime_values_reach_real_child_without_report_disclosure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            java_home = root / "jdk"
            executable = java_home / "bin" / "java"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            (java_home / "release").write_text(
                'JAVA_VERSION="25.0.4.1"\nIMPLEMENTOR="Eclipse Adoptium"\n'
                'IMPLEMENTOR_VERSION="Temurin-25.0.4.1+1"\n', encoding="utf-8"
            )
            command = [sys.executable, "-c", "import os,sys; sys.exit(0 if os.environ.get('JAVA_HOME') and os.environ.get('LEXIFLOW_POSTGRES_TEST_JDBC_URL') and os.environ.get('LEXIFLOW_REDIS_TEST_ENDPOINT') else 9)"]
            check = _check("controlled-runtime")
            check["command"] = command
            check["required_environment"] = ["java-25-temurin", "postgres-test-jdbc-url", "redis-test-endpoint"]
            _write(root, [check])
            secret_url = "jdbc:postgresql://127.0.0.1:55432/isolated?password=never-report"
            with patch.dict(os.environ, {"LEXIFLOW_JAVA_HOME": str(java_home), "LEXIFLOW_POSTGRES_TEST_JDBC_URL": secret_url, "LEXIFLOW_REDIS_TEST_ENDPOINT": "127.0.0.1:46379"}):
                report = verify_repository(root)
        self.assertEqual("PASS", report["result"])
        self.assertEqual(0, report["checks"][0]["process"]["exit_code"])
        self.assertNotIn(secret_url, json.dumps(report))

class TestAcceptancePublicContract(unittest.TestCase):
    def test_freeze_and_extra_declared_check_are_bound(self):
        from scripts.verification import freeze_inputs, verify_repository
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "harness").mkdir(); (root / "one.py").write_text("x")
            declaration = {"schema_version":"lexiflow.module-checks.v1","checks":[
                {"check_id":"base","module":"base","command":["python3","-c","print(1)"],"executable":"python3","cwd":".","timeout_seconds":5,"scope":"repository-baseline","triggers":[{"path":"one.py"}],"module_dependencies":[],"required_environment":["python3"],"input_paths":["one.py"],"result_contract":{"type":"exit-code","completeness_guarantee":"fixture"}},
                {"check_id":"extra","module":"extra","command":["python3","-c","print(1)"],"executable":"python3","cwd":".","timeout_seconds":5,"scope":"change-targeted","triggers":[{"path":"one.py"}],"module_dependencies":[],"required_environment":["python3"],"input_paths":["one.py"],"result_contract":{"type":"exit-code","completeness_guarantee":"fixture"}}]}
            (root / "harness/module-checks.yaml").write_text(yaml.safe_dump(declaration))
            freeze=freeze_inputs(root,required_check_ids=("extra",)); self.assertEqual(freeze["result"],"PASS")
            report=verify_repository(root,required_check_ids=("extra",),frozen_inputs=freeze)
            self.assertEqual(report["result"],"PASS");self.assertEqual({x["check_id"] for x in report["checks"]},{"base","extra"})
    def test_unknown_extra_and_freeze_drift_fail_closed(self):
        from scripts.verification import freeze_inputs, verify_repository
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "harness").mkdir();(root / "one.py").write_text("x")
            declaration={"schema_version":"lexiflow.module-checks.v1","checks":[{"check_id":"base","module":"base","command":["python3","-c","print(1)"],"executable":"python3","cwd":".","timeout_seconds":5,"scope":"repository-baseline","triggers":[{"path":"one.py"}],"module_dependencies":[],"required_environment":["python3"],"input_paths":["one.py"],"result_contract":{"type":"exit-code","completeness_guarantee":"fixture"}}]}
            (root / "harness/module-checks.yaml").write_text(yaml.safe_dump(declaration));freeze=freeze_inputs(root);(root/"one.py").write_text("y")
            self.assertEqual(verify_repository(root,frozen_inputs=freeze)["reason"],"frozen-input-mismatch")
            self.assertEqual(freeze_inputs(root,required_check_ids=("missing",))["reason"],"unknown-required-check-id")
