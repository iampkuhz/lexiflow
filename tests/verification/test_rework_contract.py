"""Public regression cases for ENG-WP-01 rework completeness rules."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path


from scripts.verification.execution.check_runner import execute_single_check, snapshot_check_inputs
from scripts.verification.execution.check_plan_runner import verify_repository
from tests.verification.declaration_fixtures import write_declarations

_PASS_OUTPUT = (
    '{"status":"PASS","checks_run":1,"failures":0,"errors":0,"skipped":0,"reason":""}'
)


def _execute_check(check, root, env, *, runner, run_id=None):
    """用内核测试替身执行固定接口，冻结真实快照并分配规范运行身份。"""
    with patch("scripts.verification.execution.check_runner.run_check_process", side_effect=runner):
        return execute_single_check(
            check,
            root,
            env,
            run_id=run_id or str(uuid.uuid4()),
            frozen_pre=snapshot_check_inputs(root, check),
        )


def _check(
    check_id: str,
    *,
    scope: str = "repository-baseline",
    triggers: list[str] | None = None,
    dependencies: list[str] | None = None,
    contract: dict | None = None,
) -> dict:
    return {
        "check_id": check_id,
        "command": [
            "python3",
            "-c",
            "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
        ],
        "cwd": ".",
        "timeout_seconds": 10,
        "scope": scope,
        "triggers": [{"path": path} for path in (triggers or ["src/"])],
        "module_dependencies": dependencies or [],
        "required_environment": [],
        "input_paths": [],
        "result_contract": contract
        or {
            "type": "exit-code",
            "completeness_guarantee": "fixture command owns all required work",
        },
    }


def _write(root: Path, checks: list[dict]) -> None:
    (root / "harness").mkdir(exist_ok=True)
    write_declarations(root / "config" / "engineering" / "module-checks.yaml", checks)


def _git(root: Path) -> str:
    for args in (
        ("init",),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Test"),
    ):
        subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    (root / "seed").write_text("seed")
    (root / ".gitignore").write_text("tmp/\n")
    subprocess.run(
        ["git", "add", "."],
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()


class TestResultProtocol(unittest.TestCase):
    def test_json_contract_rejects_empty_or_skipped_report(self):
        contract = {
            "type": "json-stdout",
            "required_fields": ["status", "tests_run", "skipped"],
            "allowed_statuses": ["PASS"],
            "minimum": {"tests_run": 1},
            "equals": {"skipped": 0},
        }
        check = _check("result.protocol", contract=contract)
        with tempfile.TemporaryDirectory() as temp:
            empty = _execute_check(
                check,
                Path(temp),
                {},
                runner=lambda *_, **kwargs: {
                    "exit_code": 0,
                    "exit_reason": "exited",
                    "timed_out": False,
                    "stdout": "",
                    "stderr": "",
                    "executed_argv": ["python3"],
                },
            )
            skipped = _execute_check(
                check,
                Path(temp),
                {},
                runner=lambda *_, **kwargs: {
                    "exit_code": 0,
                    "exit_reason": "exited",
                    "timed_out": False,
                    "stdout": json.dumps(
                        {"status": "PASS", "tests_run": 1, "skipped": 1}
                    ),
                    "stderr": "",
                    "executed_argv": ["python3"],
                },
            )
        self.assertEqual(empty["reason"], "result-report-invalid")
        self.assertEqual(skipped["reason"], "result-report-incomplete")

    def test_typed_non_pass_preserves_module_diagnostic(self):
        contract = {
            "type": "json-stdout",
            "required_fields": ["status", "tests_run", "skipped", "reason"],
            "allowed_statuses": ["PASS", "BLOCKED", "FAIL"],
            "minimum": {"tests_run": 1},
            "equals": {"skipped": 0},
        }
        check = _check("result.blocked", contract=contract)
        with tempfile.TemporaryDirectory() as temp:
            result = _execute_check(
                check,
                Path(temp),
                {},
                runner=lambda *_, **kwargs: {
                    "exit_code": 0,
                    "exit_reason": "exited",
                    "timed_out": False,
                    "stdout": json.dumps(
                        {
                            "status": "BLOCKED",
                            "tests_run": 0,
                            "skipped": 0,
                            "reason": "dependency-missing",
                        }
                    ),
                    "stderr": "",
                    "executed_argv": ["python3"],
                },
            )
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("dependency-missing", result["reason"])

    def test_actual_input_path_drift_and_artifact_hash_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "input.txt").write_text("before")
            check = _check("result.drift")
            check["input_paths"] = ["input.txt"]

            def mutate(*_args, **kwargs):
                (root / "input.txt").write_text("after")
                return {
                    "exit_code": 0,
                    "exit_reason": "exited",
                    "timed_out": False,
                    "stdout": "",
                    "stderr": "diagnostic",
                    "executed_argv": ["python3"],
                }

            result = _execute_check(
                check, root, {}, runner=mutate, run_id=str(uuid.uuid4())
            )
            artifact = root / result["process"]["output_artifacts"]["stderr"]["locator"]
            self.assertEqual(result["reason"], "input-drift")
            self.assertTrue(artifact.is_file())
            self.assertEqual(
                result["process"]["output_artifacts"]["stderr"]["sha256"],
                __import__("hashlib").sha256(b"diagnostic").hexdigest(),
            )

    def test_declared_non_python_argv_is_not_rewritten(self):
        check = _check("argv.exact")
        check["command"] = ["git", "--version"]
        check.pop("executable", None)
        seen: list[list[str]] = []

        def runner(argv, *_args, **kwargs):
            seen.append(argv)
            return {
                "exit_code": 0,
                "exit_reason": "exited",
                "timed_out": False,
                "stdout": "",
                "stderr": "",
                "executed_argv": argv,
            }

        with tempfile.TemporaryDirectory() as temp:
            result = _execute_check(check, Path(temp), {}, runner=runner)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(seen, [["git", "--version"]])


if __name__ == "__main__":
    unittest.main()


class TestFixedExecutionBoundary(unittest.TestCase):
    """旧调用者执行器和诊断模式不能穿透产品执行接口。"""

    def test_public_scenarios_reject_runner_and_execution_mode(self):
        from scripts.verification.execution.check_plan_runner import verify_profile, verify_profiles

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root, [_check("fixed")])
            freeze = __import__(
                "scripts.verification", fromlist=["freeze_inputs"]
            ).freeze_inputs(root)
            for execute, kwargs in (
                (verify_repository, {}),
                (verify_profile, {"frozen_inputs": freeze}),
                (verify_profiles, {"frozen_profiles": [freeze]}),
            ):
                for removed in (
                    {"runner": lambda *_: {}},
                    {"execution_mode": "diagnostic"},
                ):
                    with self.subTest(
                        execute=execute.__name__, removed=next(iter(removed))
                    ):
                        with self.assertRaises(TypeError):
                            execute(root, **kwargs, **removed)
            with self.assertRaises(TypeError):
                execute_single_check(_check("fixed"), root, {}, runner=lambda *_: {})

    def test_formal_batch_rejects_caller_progress_observer(self):
        from scripts.verification.execution.check_plan_runner import verify_profiles

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(TypeError):
                verify_profiles(
                    root, frozen_profiles=[{}], progress_observer=lambda event: None
                )

    def test_progress_observer_cannot_replace_nonzero_process_result(self):
        events = []

        def observer(event):
            events.append(event)
            return {"status": "PASS", "exit_code": 0}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            check = _check("observer.fixed")
            check["command"] = [sys.executable, "-c", "raise SystemExit(7)"]
            report = execute_single_check(
                check,
                root,
                {},
                run_id=str(uuid.uuid4()),
                frozen_pre=snapshot_check_inputs(root, check),
                progress_observer=observer,
            )
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("non-zero-exit", report["reason"])
        self.assertEqual(7, report["process"]["exit_code"])
        self.assertEqual(["started", "finished"], [event["event"] for event in events])
        self.assertEqual({"event", "argv", "timeout_seconds"}, set(events[0]))
        self.assertEqual({"event", "argv", "exit_code", "exit_reason"}, set(events[1]))

    def test_profile_batch_rejects_empty_and_more_than_sixteen_before_process(self):
        from scripts.verification.execution.check_plan_runner import verify_profiles

        with tempfile.TemporaryDirectory() as temp:
            for profiles in ([], [{}] * 17):
                with (
                    self.subTest(size=len(profiles)),
                    patch("scripts.verification.execution.check_runner.run_check_process") as process,
                ):
                    with self.assertRaisesRegex(ValueError, "one to sixteen"):
                        verify_profiles(Path(temp), frozen_profiles=profiles)
                    process.assert_not_called()

    def test_single_check_requires_uuid_and_frozen_snapshot_before_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            check = _check("fixed")
            frozen = snapshot_check_inputs(root, check)
            for kwargs in (
                {},
                {"run_id": str(uuid.uuid4())},
                {"run_id": "caller-alias", "frozen_pre": frozen},
            ):
                with (
                    self.subTest(kwargs=kwargs),
                    patch("scripts.verification.execution.check_runner.run_check_process") as process,
                ):
                    with self.assertRaises(ValueError):
                        execute_single_check(check, root, {}, **kwargs)
                    process.assert_not_called()


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
                'IMPLEMENTOR_VERSION="Temurin-25.0.4.1+1"\n',
                encoding="utf-8",
            )
            command = [
                sys.executable,
                "-c",
                "import json,os,sys; good=bool(os.environ.get('JAVA_HOME') and os.environ.get('LEXIFLOW_POSTGRES_TEST_JDBC_URL') and os.environ.get('LEXIFLOW_REDIS_TEST_ENDPOINT')); print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''})) if good else None; sys.exit(0 if good else 9)",
            ]
            check = _check("controlled-runtime")
            check["command"] = command
            check["required_environment"] = [
                "java-25-temurin",
                "postgres-test-jdbc-url",
                "redis-test-endpoint",
            ]
            _write(root, [check])
            secret_url = (
                "jdbc:postgresql://127.0.0.1:55432/isolated?password=never-report"
            )
            with patch.dict(
                os.environ,
                {
                    "LEXIFLOW_JAVA_HOME": str(java_home),
                    "LEXIFLOW_POSTGRES_TEST_JDBC_URL": secret_url,
                    "LEXIFLOW_REDIS_TEST_ENDPOINT": "127.0.0.1:46379",
                },
            ):
                report = verify_repository(root)
        self.assertEqual("PASS", report["result"])
        self.assertEqual(0, report["checks"][0]["process"]["exit_code"])
        self.assertNotIn(secret_url, json.dumps(report))


class TestDeliveryGatePublicContract(unittest.TestCase):
    def test_freeze_and_extra_declared_check_are_bound(self):
        from scripts.verification import freeze_inputs, verify_repository

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "harness").mkdir()
            (root / "one.py").write_text("x")
            command = [
                "python3",
                "-c",
                "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
            ]
            checks = [
                {
                    "check_id": "base",
                    "command": command,
                    "timeout_seconds": 5,
                    "scope": "repository-baseline",
                    "triggers": [{"path": "one.py"}],
                    "module_dependencies": [],
                    "required_environment": ["python3"],
                    "input_paths": ["one.py"],
                },
                {
                    "check_id": "extra",
                    "command": command,
                    "timeout_seconds": 5,
                    "scope": "change-targeted",
                    "triggers": [{"path": "one.py"}],
                    "module_dependencies": [],
                    "required_environment": ["python3"],
                    "input_paths": ["one.py"],
                },
            ]
            write_declarations(root / "config/engineering/module-checks.yaml", checks)
            freeze = freeze_inputs(root, required_check_ids=("extra",))
            self.assertEqual(freeze["result"], "PASS")
            report = verify_repository(
                root, required_check_ids=("extra",), frozen_inputs=freeze
            )
            self.assertEqual(report["result"], "PASS")
            self.assertEqual(
                {x["check_id"] for x in report["checks"]}, {"base", "extra"}
            )

    def test_unknown_extra_and_freeze_drift_fail_closed(self):
        from scripts.verification import freeze_inputs, verify_repository

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "harness").mkdir()
            (root / "one.py").write_text("x")
            command = [
                "python3",
                "-c",
                "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
            ]
            write_declarations(
                root / "config/engineering/module-checks.yaml",
                [
                    {
                        "check_id": "base",
                        "command": command,
                        "timeout_seconds": 5,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "one.py"}],
                        "module_dependencies": [],
                        "required_environment": ["python3"],
                        "input_paths": ["one.py"],
                    }
                ],
            )
            freeze = freeze_inputs(root)
            (root / "one.py").write_text("y")
            self.assertEqual(
                verify_repository(root, frozen_inputs=freeze)["reason"],
                "frozen-input-mismatch",
            )
            self.assertEqual(
                freeze_inputs(root, required_check_ids=("missing",))["reason"],
                "unknown-required-check-id",
            )
