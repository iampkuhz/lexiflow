"""Tests for scripts.verification.scenarios (verify_repository, verify_changes).

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

import yaml

from scripts.verification.scenarios import verify_changes, verify_repository


def _complete_check(check: dict) -> dict:
    complete = dict(check)
    complete.setdefault("module_dependencies", [])
    complete.setdefault("required_environment", [])
    complete.setdefault("input_paths", [])
    complete.setdefault("result_contract", {"type": "exit-code", "completeness_guarantee": "fixture-owned command completion"})
    return complete


def _write_declarations(root: Path, checks: list[dict]) -> None:
    harness = root / "harness"
    harness.mkdir(exist_ok=True)
    data = {
        "schema_version": "lexiflow.module-checks.v1",
        "checks": [_complete_check(check) for check in checks],
    }
    (harness / "module-checks.yaml").write_text(
        yaml.dump(data, default_flow_style=False), encoding="utf-8",
    )


def _init_git_repo(tmpdir: Path) -> str:
    subprocess.run(["git", "init"], cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (tmpdir / "initial.txt").write_text("initial")
    # Ignore harness/ so test declarations don't pollute git diff
    (tmpdir / ".gitignore").write_text("harness/\n")
    subprocess.run(["git", "add", "."], cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(tmpdir),
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _pass_runner(argv, cwd, env, timeout, executable=None):
    return {
        "status": "PASS", "exit_code": 0, "exit_reason": "exited",
        "stdout": "", "stderr": "", "duration_seconds": 0.01,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:00Z",
        "timed_out": False, "executable": sys.executable,
    }


def _fail_runner(argv, cwd, env, timeout, executable=None):
    return {
        "status": "FAIL", "exit_code": 1, "exit_reason": "exited",
        "stdout": "", "stderr": "error", "duration_seconds": 0.01,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:00Z",
        "timed_out": False, "executable": sys.executable,
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
            _write_declarations(root, [{
                "check_id": "a",
                "module": "test",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 10,
                "scope": "change-targeted",
                "triggers": [{"path": "test/"}],
            }])
            report = verify_repository(tmpdir)
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(report["reason"], "no-declared-checks")

    def test_check_pass(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, [{
                "check_id": "test.pass",
                "module": "test",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 10,
                "scope": "repository-baseline",
                "triggers": [{"path": "test/"}],
                "required_environment": [],
            }])
            report = verify_repository(tmpdir, runner=_pass_runner)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(len(report["checks"]), 1)
        self.assertEqual(report["checks"][0]["status"], "PASS")

    def test_check_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, [{
                "check_id": "test.fail",
                "module": "test",
                "command": ["python3", "-c", "import sys; sys.exit(1)"],
                "cwd": ".",
                "timeout_seconds": 10,
                "scope": "repository-baseline",
                "triggers": [{"path": "test/"}],
                "required_environment": [],
            }])
            report = verify_repository(tmpdir, runner=_fail_runner)
        self.assertEqual(report["result"], "FAIL")

    def test_environment_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, [{
                "check_id": "test.blocked",
                "module": "test",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 10,
                "scope": "repository-baseline",
                "triggers": [{"path": "test/"}],
                "required_environment": ["nonexistent-tool-xyz"],
            }])
            report = verify_repository(tmpdir)
        self.assertEqual(report["result"], "BLOCKED")
        self.assertEqual(report["checks"][0]["status"], "BLOCKED")
        self.assertEqual(report["checks"][0]["reason"], "missing-environment")

    def test_report_schema_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, [{
                "check_id": "test.schema",
                "module": "test",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 10,
                "scope": "repository-baseline",
                "triggers": [{"path": "test/"}],
                "required_environment": [],
            }])
            report = verify_repository(tmpdir, runner=_pass_runner)
        self.assertEqual(report["schema_version"], "lexiflow.verification-report.v1")
        self.assertIn("result", report)
        self.assertIn("checks", report)
        self.assertIn("input_fingerprint", report)
        self.assertIn("scope_review", report)
        self.assertIn("coverage_gaps", report)
        self.assertIn("run_id", report)

    def test_check_ids_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, [
                {
                    "check_id": "a",
                    "module": "test",
                    "command": ["python3", "-c", "pass # first"],
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "scope": "repository-baseline",
                    "triggers": [{"path": "test/"}],
                    "required_environment": [],
                },
                {
                    "check_id": "b",
                    "module": "test",
                    "command": ["python3", "-c", "pass # second"],
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "scope": "repository-baseline",
                    "triggers": [{"path": "test/"}],
                    "required_environment": [],
                },
            ])
            report = verify_repository(tmpdir, check_ids=["a"], runner=_pass_runner)
        self.assertEqual(len(report["checks"]), 1)
        self.assertEqual(report["checks"][0]["check_id"], "a")

    def test_continue_past_failure(self):
        """Unrelated checks continue even when one fails."""
        call_count = 0

        def counting_runner(argv, cwd, env, timeout, executable=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _fail_runner(argv, cwd, env, timeout, executable)
            return _pass_runner(argv, cwd, env, timeout, executable)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, [
                {
                    "check_id": "first",
                    "module": "test",
                    "command": ["python3", "-c", "pass # first"],
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "scope": "repository-baseline",
                    "triggers": [{"path": "test/"}],
                    "required_environment": [],
                },
                {
                    "check_id": "second",
                    "module": "test",
                    "command": ["python3", "-c", "pass # second"],
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "scope": "repository-baseline",
                    "triggers": [{"path": "test/"}],
                    "required_environment": [],
                },
            ])
            report = verify_repository(tmpdir, runner=counting_runner)
        self.assertEqual(call_count, 2)
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(report["checks"][0]["status"], "FAIL")
        self.assertEqual(report["checks"][1]["status"], "PASS")


class TestVerifyChanges(unittest.TestCase):
    def test_no_declarations_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _init_git_repo(root)
            report = verify_changes(tmpdir)
        self.assertEqual(report["result"], "FAIL")

    def test_no_changes_pass(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = _init_git_repo(root)
            _write_declarations(root, [{
                "check_id": "test.check",
                "module": "test",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 10,
                "scope": "change-targeted",
                "triggers": [{"path": "test/"}],
            }])
            report = verify_changes(tmpdir, base=base)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["scope_review"]["kind"], "no-changes")

    def test_changes_no_matching_check_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = _init_git_repo(root)
            _write_declarations(root, [{
                "check_id": "test.check",
                "module": "test",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 10,
                "scope": "change-targeted",
                "triggers": [{"path": "backend/"}],
            }])
            (root / "unrelated.txt").write_text("change")
            report = verify_changes(tmpdir, base=base)
        self.assertEqual(report["result"], "BLOCKED")
        self.assertIn("coverage-gap", report.get("reason", ""))

    def test_changes_with_matching_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = _init_git_repo(root)
            _write_declarations(root, [{
                "check_id": "test.check",
                "module": "test",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 10,
                "scope": "change-targeted",
                "triggers": [{"path": "backend/"}],
                "required_environment": [],
            }])
            (root / "backend").mkdir()
            (root / "backend" / "file.py").write_text("code")
            report = verify_changes(tmpdir, base=base, runner=_pass_runner)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(len(report["checks"]), 1)
        self.assertEqual(report["checks"][0]["status"], "PASS")

    def test_expected_paths_self_review(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = _init_git_repo(root)
            _write_declarations(root, [{
                "check_id": "test.check",
                "module": "test",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 10,
                "scope": "change-targeted",
                "triggers": [{"path": "backend/"}],
                "required_environment": [],
            }])
            (root / "backend").mkdir()
            (root / "backend" / "file.py").write_text("code")
            report = verify_changes(
                tmpdir, base=base,
                expected_paths=("backend/",),
                runner=_pass_runner,
            )
        self.assertEqual(report["scope_review"]["kind"], "expected")
        self.assertEqual(len(report["scope_review"]["unexpected"]), 0)

    def test_unexpected_paths_advisory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = _init_git_repo(root)
            _write_declarations(root, [{
                "check_id": "test.check",
                "module": "test",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 10,
                "scope": "change-targeted",
                "triggers": [{"path": "backend/"}],
                "required_environment": [],
            }])
            (root / "backend").mkdir()
            (root / "backend" / "file.py").write_text("code")
            (root / "other").mkdir()
            (root / "other" / "file.py").write_text("code")
            report = verify_changes(
                tmpdir, base=base,
                expected_paths=("backend/",),
                runner=_pass_runner,
            )
        self.assertIn("other/file.py", report["scope_review"]["unexpected"])
        self.assertIn("review unexpected files", report["scope_review"]["advisories"])


if __name__ == "__main__":
    unittest.main()
