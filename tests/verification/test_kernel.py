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
from unittest.mock import Mock, patch

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
from scripts.verification.kernel import (
    RELEASE_RUNTIME_CHILD_COMMAND,
    RELEASE_RUNTIME_CHECK_IDS,
    VERIFY_CHILD_INPUT_MAX_BYTES,
    fingerprint_json,
    is_release_runtime_child_check,
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


def _release_runtime_check(**overrides):
    check = _make_check(
        check_id="eng.release.lifecycle-runtime",
        module="release-lifecycle-runtime",
        command=list(RELEASE_RUNTIME_CHILD_COMMAND),
        executable="python3",
        result_contract={
            "type": "json-stdout",
            "required_fields": ["status", "reason"],
            "allowed_statuses": ["PASS", "BLOCKED", "FAIL"],
        },
    )
    check.update(overrides)
    return check


def _process_result(stdout="", *, exit_code=0, timed_out=False):
    return {
        "status": "FAIL" if exit_code else "PASS", "exit_code": exit_code,
        "exit_reason": "timeout" if timed_out else "exited",
        "stdout": stdout, "stderr": "", "duration_seconds": 0.01,
        "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:00Z",
        "timed_out": timed_out, "executable": sys.executable,
    }


def _correlated_report(envelope, *, status="PASS", reason=""):
    return {
        "status": status, "reason": reason,
        "run_id": envelope["run_id"], "check_id": envelope["check_id"],
        "verify_input_fingerprint": envelope["snapshot"]["fingerprint"],
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

    def test_large_stdin_and_simultaneous_stdout_stderr_are_drained(self):
        payload = b"p" * 1_000_000
        command = [
            sys.executable,
            "-c",
            "import sys,time; time.sleep(.15); "
            "sys.stdout.buffer.write(b'o'*262144); sys.stdout.flush(); "
            "sys.stderr.buffer.write(b'e'*262144); sys.stderr.flush(); "
            "data=sys.stdin.buffer.read(); sys.exit(0 if len(data)==1000000 else 9)",
        ]
        result = run_check_process(
            command, cwd=".", env={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=10, stdin_payload=payload,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(len(result["stdout"]), 262144)
        self.assertEqual(len(result["stderr"]), 262144)

    def test_timeout_while_pipe_writer_is_blocked_reaps_child(self):
        payload = b"p" * 1_000_000
        result = run_check_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=".", env={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=0.2, stdin_payload=payload,
        )
        self.assertTrue(result["timed_out"])
        self.assertEqual((result["status"], result["exit_reason"]), ("FAIL", "timeout"))

    def test_oversized_direct_stdin_is_blocked_without_spawn(self):
        with patch("scripts.verification.kernel.subprocess.Popen") as popen:
            result = run_check_process(
                [sys.executable, "-c", "pass"], cwd=".", env={}, timeout_seconds=1,
                stdin_payload=b"x" * (VERIFY_CHILD_INPUT_MAX_BYTES + 1),
            )
        self.assertEqual((result["status"], result["exit_reason"]), ("BLOCKED", "verify-context-unavailable"))
        self.assertEqual(result["executed_argv"], [])
        popen.assert_not_called()


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

    def test_only_fixed_id_command_and_python_executable_activate_transport(self):
        baseline = _release_runtime_check(check_id="eng.release.lifecycle-runtime")
        change = _release_runtime_check(check_id="eng.release.lifecycle-runtime-on-change")
        self.assertTrue(is_release_runtime_child_check(baseline))
        self.assertTrue(is_release_runtime_child_check(change))
        self.assertEqual(RELEASE_RUNTIME_CHECK_IDS, {baseline["check_id"], change["check_id"]})
        self.assertFalse(is_release_runtime_child_check({**baseline, "executable": "bash"}))
        self.assertFalse(is_release_runtime_child_check({**baseline, "command": [*baseline["command"], "extra"]}))
        self.assertFalse(is_release_runtime_child_check({**baseline, "check_id": "other.check"}))

    def test_valid_envelope_is_canonical_and_uses_exact_frozen_snapshot(self):
        check = _release_runtime_check(input_paths=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            captured = {}
            def runner(argv, cwd, env, timeout, executable=None, *, stdin_payload=None):
                captured["payload"] = stdin_payload
                envelope = json.loads(stdin_payload)
                return _process_result(json.dumps(_correlated_report(envelope)))
            result = execute_single_check(check, root, {}, runner=runner, run_id="verify-run-1")
        envelope = json.loads(captured["payload"])
        self.assertEqual(captured["payload"], json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        self.assertEqual(set(envelope), {"schema_version", "run_id", "check_id", "check_config_fingerprint", "effective_check", "snapshot"})
        self.assertEqual(envelope["schema_version"], "lexiflow.verify-child-input.v1")
        self.assertEqual(envelope["run_id"], "verify-run-1")
        self.assertEqual(envelope["check_id"], check["check_id"])
        self.assertEqual(envelope["effective_check"], check)
        self.assertEqual(envelope["check_config_fingerprint"], fingerprint_json(check))
        self.assertEqual(set(envelope["snapshot"]), {"fingerprint", "files", "missing"})
        self.assertEqual(result["status"], "PASS")

    def test_missing_run_id_and_oversized_envelope_do_not_spawn(self):
        check = _release_runtime_check(input_paths=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runner = Mock()
            missing = execute_single_check(check, root, {}, runner=runner, run_id=None)
            self.assertEqual((missing["status"], missing["reason"]), ("BLOCKED", "verify-context-unavailable"))
            self.assertEqual(missing["process"]["executed_argv"], [])
            runner.assert_not_called()
            huge = {**check, "synthetic_context_padding": "x" * VERIFY_CHILD_INPUT_MAX_BYTES}
            oversized = execute_single_check(huge, root, {}, runner=runner, run_id="verify-run-2")
        self.assertEqual((oversized["status"], oversized["reason"]), ("BLOCKED", "verify-context-unavailable"))
        self.assertEqual(oversized["process"]["executed_argv"], [])
        runner.assert_not_called()

    def test_correlation_is_fail_closed_but_does_not_override_nonzero_or_drift(self):
        check = _release_runtime_check(input_paths=["input.txt"])
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "input.txt"
            path.write_text("before")
            def bad_correlation(argv, cwd, env, timeout, executable=None, *, stdin_payload=None):
                envelope = json.loads(stdin_payload)
                report = _correlated_report(envelope)
                report["run_id"] = "wrong-run"
                return _process_result(json.dumps(report))
            mismatch = execute_single_check(check, root, {}, runner=bad_correlation, run_id="right-run")
            self.assertEqual((mismatch["status"], mismatch["reason"]), ("FAIL", "verify-context-invalid"))

            def nonzero_bad_correlation(argv, cwd, env, timeout, executable=None, *, stdin_payload=None):
                return _process_result("", exit_code=7)
            nonzero = execute_single_check(check, root, {}, runner=nonzero_bad_correlation, run_id="right-run")
            self.assertEqual((nonzero["status"], nonzero["reason"]), ("FAIL", "non-zero-exit"))

            def timeout_bad_correlation(argv, cwd, env, timeout, executable=None, *, stdin_payload=None):
                return _process_result("", exit_code=None, timed_out=True)
            timeout = execute_single_check(check, root, {}, runner=timeout_bad_correlation, run_id="right-run")
            self.assertEqual((timeout["status"], timeout["reason"]), ("FAIL", "timeout"))

            def drift_bad_correlation(argv, cwd, env, timeout, executable=None, *, stdin_payload=None):
                envelope = json.loads(stdin_payload)
                path.write_text("after")
                report = _correlated_report(envelope)
                report["check_id"] = "wrong-check"
                return _process_result(json.dumps(report))
            drift = execute_single_check(check, root, {}, runner=drift_bad_correlation, run_id="right-run")
            self.assertEqual((drift["status"], drift["reason"]), ("FAIL", "input-drift"))

    def test_active_target_exit_code_contract_cannot_pass_without_correlation(self):
        check = _release_runtime_check(
            input_paths=[],
            result_contract={"type": "exit-code", "completeness_guarantee": "not sufficient for lifecycle child"},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_single_check(check, Path(tmpdir), {}, runner=lambda *args, **kwargs: _process_result(), run_id="verify-run")
        self.assertEqual((result["status"], result["reason"]), ("FAIL", "verify-context-invalid"))

    def test_correlation_cannot_upgrade_missing_result_evidence(self):
        check = _release_runtime_check(
            input_paths=[],
            result_contract={
                "type": "json-stdout", "required_fields": ["status", "reason"],
                "allowed_statuses": ["PASS", "BLOCKED", "FAIL"], "minimum": {"checks_run": 1},
            },
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            def incomplete_but_correlated(argv, cwd, env, timeout, executable=None, *, stdin_payload=None):
                envelope = json.loads(stdin_payload)
                return _process_result(json.dumps(_correlated_report(envelope)))
            result = execute_single_check(check, Path(tmpdir), {}, runner=incomplete_but_correlated, run_id="verify-run")
        self.assertEqual((result["status"], result["reason"]), ("FAIL", "result-report-incomplete"))

    def test_missing_correlation_fails_and_valid_blocked_correlation_stays_blocked(self):
        check = _release_runtime_check(input_paths=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            def missing(argv, cwd, env, timeout, executable=None, *, stdin_payload=None):
                return _process_result(json.dumps({"status": "PASS", "reason": ""}))
            result = execute_single_check(check, root, {}, runner=missing, run_id="verify-run")
            self.assertEqual((result["status"], result["reason"]), ("FAIL", "verify-context-invalid"))

            def blocked(argv, cwd, env, timeout, executable=None, *, stdin_payload=None):
                envelope = json.loads(stdin_payload)
                return _process_result(json.dumps(_correlated_report(envelope, status="BLOCKED", reason="missing-environment")))
            result = execute_single_check(check, root, {}, runner=blocked, run_id="verify-run")
        self.assertEqual((result["status"], result["reason"]), ("BLOCKED", "missing-environment"))

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
