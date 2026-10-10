"""Tests for scripts.verification.execution.check_plan_runner (verify_repository).

Covers: no declared checks, environment BLOCKED, check PASS/FAIL,
coverage gaps, no git changes, input drift, module dependency
resolution, and report schema completeness.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


from scripts.verification.execution.check_plan_runner import verify_repository as _verify_repository
from unittest.mock import patch
from tests.verification.declaration_fixtures import write_declarations


def verify_repository(root, *, runner=None, **kwargs):
    """仅测试在固定 kernel seam 捕获执行；生产不提供 runner 注入。"""
    if runner is None:
        return _verify_repository(root, **kwargs)

    def process(*args, **kw):
        kw.pop("progress_observer", None)
        result = runner(*args, **kw)
        result.setdefault("executed_argv", args[0])
        return result

    with patch("scripts.verification.execution.check_runner.run_check_process", side_effect=process):
        return _verify_repository(root, **kwargs)


def _complete_check(check: dict) -> dict:
    complete = dict(check)
    complete.setdefault("module_dependencies", [])
    complete.setdefault("required_environment", [])
    complete.setdefault("input_paths", [])
    complete.setdefault(
        "result_contract",
        {
            "type": "exit-code",
            "completeness_guarantee": "fixture-owned command completion",
        },
    )
    return complete


def _write_declarations(root: Path, checks: list[dict]) -> None:
    write_declarations(
        root / "config" / "engineering" / "module-checks.yaml",
        [_complete_check(check) for check in checks],
    )


def _init_git_repo(tmpdir: Path) -> str:
    subprocess.run(
        ["git", "init"],
        cwd=str(tmpdir),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmpdir),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmpdir),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (tmpdir / "initial.txt").write_text("initial")
    # Ignore harness/ so test declarations don't pollute git diff
    (tmpdir / ".gitignore").write_text("harness/\ntmp/\n")
    subprocess.run(
        ["git", "add", "."],
        cwd=str(tmpdir),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmpdir),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(tmpdir),
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def _pass_runner(argv, cwd, env, timeout, executable=None):
    return {
        "status": "PASS",
        "exit_code": 0,
        "exit_reason": "exited",
        "stdout": '{"status":"PASS","checks_run":1,"failures":0,"errors":0,"skipped":0,"reason":""}',
        "stderr": "",
        "duration_seconds": 0.01,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:00Z",
        "timed_out": False,
        "executable": sys.executable,
    }


def _fail_runner(argv, cwd, env, timeout, executable=None):
    return {
        "status": "FAIL",
        "exit_code": 1,
        "exit_reason": "exited",
        "stdout": "",
        "stderr": "error",
        "duration_seconds": 0.01,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:00Z",
        "timed_out": False,
        "executable": sys.executable,
    }


class TestVerifyRepository(unittest.TestCase):
    def test_no_declarations_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report = verify_repository(tmpdir)
        self.assertEqual(report["result"], "FAIL")
        self.assertIn(report["reason"], ["declarations-missing", "no-declared-checks"])

    def test_no_checks_for_scope_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                [
                    {
                        "check_id": "a",
                        "command": [
                            "python3",
                            "-c",
                            "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
                        ],
                        "cwd": ".",
                        "timeout_seconds": 10,
                        "scope": "change-targeted",
                        "triggers": [{"path": "test/"}],
                    }
                ],
            )
            report = verify_repository(tmpdir)
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(report["reason"], "no-declared-checks")

    def test_check_pass(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                [
                    {
                        "check_id": "test.pass",
                        "command": ["python3", "-c", "pass"],
                        "cwd": ".",
                        "timeout_seconds": 10,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "test/"}],
                        "required_environment": [],
                    }
                ],
            )
            report = verify_repository(tmpdir, runner=_pass_runner)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(len(report["checks"]), 1)
        self.assertEqual(report["checks"][0]["status"], "PASS")

    def test_check_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                [
                    {
                        "check_id": "test.fail",
                        "command": ["python3", "-c", "import sys; sys.exit(1)"],
                        "cwd": ".",
                        "timeout_seconds": 10,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "test/"}],
                        "required_environment": [],
                    }
                ],
            )
            report = verify_repository(tmpdir, runner=_fail_runner)
        self.assertEqual(report["result"], "FAIL")

    def test_environment_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                [
                    {
                        "check_id": "test.blocked",
                        "command": ["python3", "-c", "pass"],
                        "cwd": ".",
                        "timeout_seconds": 10,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "test/"}],
                        "required_environment": ["nonexistent-tool-xyz"],
                    }
                ],
            )
            report = verify_repository(tmpdir)
        self.assertEqual(report["result"], "BLOCKED")
        self.assertEqual(report["checks"][0]["status"], "BLOCKED")
        self.assertEqual(report["checks"][0]["reason"], "missing-environment")

    def test_delivery_does_not_run_after_missing_environment(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                [
                    {
                        "check_id": name,
                        "command": ["python3", "-c", "pass"],
                        "cwd": ".",
                        "timeout_seconds": 10,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "test/"}],
                        "required_environment": ["missing-tool-for-fixture"]
                        if name == "first"
                        else [],
                    }
                    for name in ("first", "second")
                ],
            )
            report = verify_repository(
                root,
                runner=lambda *args, **kwargs: (
                    calls.append(args) or _pass_runner(*args[:4])
                ),
            )
        self.assertEqual(calls, [])
        self.assertEqual(report["checks"][0]["reason"], "missing-environment")
        self.assertEqual(report["checks"][1]["reason"], "prior-check-blocked")
        self.assertEqual(report["checks"][1]["process"]["exit_reason"], "not-run")

    def test_report_schema_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                [
                    {
                        "check_id": "test.schema",
                        "command": ["python3", "-c", "pass"],
                        "cwd": ".",
                        "timeout_seconds": 10,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "test/"}],
                        "required_environment": [],
                    }
                ],
            )
            report = verify_repository(tmpdir, runner=_pass_runner)
        self.assertEqual(report["schema_version"], "lexiflow.verification-report.v1")
        self.assertIn("result", report)
        self.assertIn("checks", report)
        self.assertIn("input_fingerprint", report)
        self.assertIn("scope_review", report)
        self.assertIn("coverage_gaps", report)
        self.assertIn("run_id", report)

    def test_delivery_stops_after_failure_and_marks_remaining_not_run(self):
        call_count = 0

        def counting_runner(argv, cwd, env, timeout, executable=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _fail_runner(argv, cwd, env, timeout, executable)
            return _pass_runner(argv, cwd, env, timeout, executable)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                [
                    {
                        "check_id": "first",
                        "command": ["python3", "-c", "pass # first"],
                        "cwd": ".",
                        "timeout_seconds": 10,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "test/"}],
                        "required_environment": [],
                    },
                    {
                        "check_id": "second",
                        "command": ["python3", "-c", "pass # second"],
                        "cwd": ".",
                        "timeout_seconds": 10,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "test/"}],
                        "required_environment": [],
                    },
                ],
            )
            report = verify_repository(tmpdir, runner=counting_runner)
        self.assertEqual(call_count, 1)
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(report["checks"][0]["status"], "FAIL")
        self.assertEqual(report["checks"][1]["status"], "BLOCKED")
        self.assertEqual(report["checks"][1]["reason"], "prior-check-failed")
        self.assertEqual(report["checks"][1]["process"]["exit_reason"], "not-run")
        self.assertNotIn("second", report["executed_check_ids"])

    def test_delivery_stops_before_runner_when_later_check_input_drifted(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "future.txt").write_text("before", encoding="utf-8")
            checks = [
                {
                    "check_id": name,
                    "command": ["python3", "-c", f"pass # {name}"],
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "scope": "repository-baseline",
                    "triggers": [{"path": "test/"}],
                    "required_environment": [],
                    "input_paths": ["future.txt"] if name == "docker.runner" else [],
                }
                for name in ("mutator", "docker.runner", "later")
            ]
            _write_declarations(root, checks)

            def runner(argv, cwd, env, timeout, executable=None):
                calls.append(argv[-1])
                if "mutator" in argv[-1]:
                    (root / "future.txt").write_text("after", encoding="utf-8")
                return _pass_runner(argv, cwd, env, timeout, executable)

            report = verify_repository(root, runner=runner)
        by_id = {item["check_id"]: item for item in report["checks"]}
        self.assertEqual(calls, ["pass # mutator"])
        self.assertEqual(by_id["docker.runner"]["reason"], "input-drift")
        self.assertEqual(by_id["docker.runner"]["process"]["exit_reason"], "not-run")
        self.assertEqual(by_id["later"]["reason"], "prior-check-failed")
        self.assertNotIn("docker.runner", report["executed_check_ids"])


if __name__ == "__main__":
    unittest.main()
