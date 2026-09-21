"""Tests for scripts.verification.kernel.

Covers: check execution, three-state aggregation, timeout handling,
input drift detection, continue-past-failure, virtual-environment
semantics, required-check-not-run is not PASS, empty results.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.verification.kernel import (
    aggregate_results,
    build_child_environment,
    compute_coverage_gap,
    execute_single_check,
    run_check_process,
    sha256_bytes,
    snapshot_check_inputs,
    verify_input_descriptors,
)


def _make_check(**overrides):
    base = {
        "check_id": "test.check.one",
        "module": "test",
        "command": ["python3", "-c", "import sys; sys.exit(0)"],
        "cwd": ".",
        "timeout_seconds": 10,
        "scope": "repository-baseline",
        "triggers": [{"path": "test/"}],
        "consumed_inputs": [],
        "required_environment": [],
        "input_paths": [],
        "result_contract": {"type": "exit-code", "completeness_guarantee": "test runner owns completion"},
    }
    base.update(overrides)
    return base


def _pass_runner(argv, cwd, env, timeout, executable=None):
    return {
        "status": "PASS", "exit_code": 0, "exit_reason": "exited",
        "stdout": "", "stderr": "", "duration_seconds": 0.01,
        "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:00Z",
        "timed_out": False, "executable": sys.executable,
    }


def _fail_runner(argv, cwd, env, timeout, executable=None):
    return {
        "status": "FAIL", "exit_code": 1, "exit_reason": "exited",
        "stdout": "", "stderr": "assertion error", "duration_seconds": 0.01,
        "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:00Z",
        "timed_out": False, "executable": sys.executable,
    }


def _timeout_runner(argv, cwd, env, timeout, executable=None):
    return {
        "status": "FAIL", "exit_code": None, "exit_reason": "timeout",
        "stdout": "", "stderr": "", "duration_seconds": timeout,
        "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:00Z",
        "timed_out": True, "executable": sys.executable,
    }


class TestBuildChildEnvironment(unittest.TestCase):
    def test_preserves_virtualenv_semantics(self):
        env = build_child_environment()
        self.assertIn("PYTHONDONTWRITEBYTECODE", env)
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")

    def test_excludes_secret_keys(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test", "USER": "testuser"}):
            env = build_child_environment()
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertIn("USER", env)

    def test_override_env(self):
        env = build_child_environment(override={"USER": "override"})
        self.assertEqual(env.get("USER"), "override")


class TestRunCheckProcess(unittest.TestCase):
    def test_passing_check(self):
        result = run_check_process(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
            cwd=".",
            env={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=10,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["timed_out"])

    def test_failing_check(self):
        result = run_check_process(
            [sys.executable, "-c", "import sys; sys.exit(1)"],
            cwd=".",
            env={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=10,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertNotEqual(result["exit_code"], 0)

    def test_timeout(self):
        result = run_check_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=".",
            env={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=1,
        )
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["status"], "FAIL")

    def test_spawn_error(self):
        result = run_check_process(
            ["/nonexistent/binary"],
            cwd=".",
            env={},
            timeout_seconds=5,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["exit_reason"], "spawn-error")

    def test_virtualenv_preserved(self):
        result = run_check_process(
            ["python3", "-c", "import sys; print(sys.prefix)"],
            cwd=".",
            env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
            timeout_seconds=10,
            executable="python3",
        )
        self.assertEqual(result["exit_code"], 0)
        output = result["stdout"].strip()
        self.assertIn(sys.prefix, output)


class TestExecuteSingleCheck(unittest.TestCase):
    def test_passing_check_with_runner(self):
        check = _make_check()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_single_check(check, Path(tmpdir), {}, runner=_pass_runner)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["check_id"], "test.check.one")

    def test_failing_check_with_runner(self):
        check = _make_check()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_single_check(check, Path(tmpdir), {}, runner=_fail_runner)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "non-zero-exit")

    def test_input_drift_blocks_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "input.txt").write_text("original")
            original_hash = sha256_bytes((root / "input.txt").read_bytes())
            check = _make_check(consumed_inputs=[{
                "locator": "input.txt",
                "sha256": original_hash,
            }])
            # Modify file before execution
            (root / "input.txt").write_text("modified")
            result = execute_single_check(check, root, {}, runner=_pass_runner)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "input-drift")

    def test_timeout_recorded(self):
        check = _make_check()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_single_check(check, Path(tmpdir), {}, runner=_timeout_runner)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "timeout")


class TestAggregateResults(unittest.TestCase):
    def test_empty_is_fail(self):
        status, reason = aggregate_results([])
        self.assertEqual(status, "FAIL")
        self.assertEqual(reason, "no-checks-executed")

    def test_all_pass(self):
        results = [
            {"status": "PASS", "reason": ""},
            {"status": "PASS", "reason": ""},
        ]
        status, reason = aggregate_results(results)
        self.assertEqual(status, "PASS")

    def test_fail_overrides_pass(self):
        results = [
            {"status": "PASS", "reason": ""},
            {"status": "FAIL", "reason": "assertion-failed"},
        ]
        status, reason = aggregate_results(results)
        self.assertEqual(status, "FAIL")
        self.assertEqual(reason, "assertion-failed")

    def test_blocked_overrides_pass(self):
        results = [
            {"status": "PASS", "reason": ""},
            {"status": "BLOCKED", "reason": "missing-env"},
        ]
        status, reason = aggregate_results(results)
        self.assertEqual(status, "BLOCKED")
        self.assertEqual(reason, "missing-env")

    def test_fail_overrides_blocked(self):
        results = [
            {"status": "BLOCKED", "reason": "missing-env"},
            {"status": "FAIL", "reason": "assertion-failed"},
        ]
        status, reason = aggregate_results(results)
        self.assertEqual(status, "FAIL")

    def test_unknown_status_is_fail(self):
        results = [{"status": "UNKNOWN", "reason": ""}]
        status, _ = aggregate_results(results)
        self.assertEqual(status, "FAIL")


class TestCoverageGap(unittest.TestCase):
    def test_no_gaps_when_all_executed(self):
        declared = [{"check_id": "a"}, {"check_id": "b"}]
        gaps = compute_coverage_gap(declared, {"a", "b"})
        self.assertEqual(gaps, [])

    def test_gap_when_not_executed(self):
        declared = [{"check_id": "a"}, {"check_id": "b"}]
        gaps = compute_coverage_gap(declared, {"a"})
        self.assertEqual(gaps, ["b"])

    def test_gap_when_none_executed(self):
        declared = [{"check_id": "a"}]
        gaps = compute_coverage_gap(declared, set())
        self.assertEqual(gaps, ["a"])


class TestInputDescriptors(unittest.TestCase):
    def test_source_snapshot_excludes_reproducible_local_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src" / "__pycache__").mkdir(parents=True)
            (root / "src" / "build").mkdir()
            (root / "src" / "main.py").write_text("source")
            (root / "src" / "__pycache__" / "main.pyc").write_bytes(b"cache")
            (root / "src" / "build" / "result.txt").write_text("output")
            snapshot = snapshot_check_inputs(root, _make_check(input_paths=["src"]))
        self.assertEqual(["src/main.py"], [item["locator"] for item in snapshot["files"]])

    def test_verified_when_matching(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "file.txt").write_text("content")
            expected = sha256_bytes((root / "file.txt").read_bytes())
            results = verify_input_descriptors(root, [{
                "locator": "file.txt", "sha256": expected,
            }])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["status"], "verified")

    def test_drift_when_modified(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "file.txt").write_text("content")
            results = verify_input_descriptors(root, [{
                "locator": "file.txt", "sha256": "0" * 64,
            }])
            self.assertEqual(results[0]["status"], "drift")

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            results = verify_input_descriptors(Path(tmpdir), [{
                "locator": "nonexistent.txt", "sha256": "0" * 64,
            }])
            self.assertEqual(results[0]["status"], "missing")


if __name__ == "__main__":
    unittest.main()
