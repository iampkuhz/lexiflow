"""Tests for the frozen-plan Gate checker executor (LF-GATE-CHECK-001)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import yaml

from scripts.gates.executor import (
    CHECK_OUTCOME_SCHEMA,
    DEFAULT_STDERR_LIMIT,
    DEFAULT_STDOUT_LIMIT,
    OUTCOME_SCHEMA,
    PLAN_SCHEMA,
    ExecutableBinding,
    ExternalInterruption,
    ProcessRequest,
    ProcessResult,
    _adapt_controlled_runtime,
    _adapt_planning_validator,
    _adapt_task_contract,
    _adapt_unittest,
    _aggregate,
    _build_child_environment,
    _environment_fingerprint,
    _execute_single_check,
    _fingerprint,
    _not_run_check,
    _read_executable_binding,
    _resolve_registered_executable,
    _resolve_cwd,
    _safe_read_file,
    _select_adapter,
    _validate_plan_projection,
    _verify_consumed_inputs,
    default_process_runner,
    execute_checks,
    sha256_bytes,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _python_binding() -> ExecutableBinding:
    return _read_executable_binding(os.path.realpath(sys.executable))


def _make_check(
    check_id="qlt.test.check",
    command_id="qlt.executor.validate.v1",
    argv=None,
    cwd=".",
    timeout=60,
    consumed_inputs=None,
    required=True,
    owner="LF-WS-QLT",
    subject_task_id="LF-TSK-QLT-0009",
    subject_task_version=1,
    subject_change_version="1.0.0",
    subject_owner="LF-WS-QLT",
    selection_reasons=None,
):
    if argv is None:
        argv = ["python3", "-m", "unittest", "discover", "-s", "tests/gates", "-p", "test_gate_executor.py"]
    if consumed_inputs is None:
        consumed_inputs = []
    return {
        "check_id": check_id,
        "check_version": 1,
        "owner": owner,
        "subject_task": {
            "task_id": subject_task_id,
            "task_version": subject_task_version,
            "change_version": subject_change_version,
            "owner": subject_owner,
        },
        "required": required,
        "command_id": command_id,
        "fixed_argv": argv,
        "cwd": cwd,
        "timeout_seconds": timeout,
        "consumed_inputs": consumed_inputs,
        "outcome_contract": {
            "schema": CHECK_OUTCOME_SCHEMA,
            "required_fields": ["exit_code", "stdout_locator", "stderr_locator", "typed_result"],
        },
        "selection_reasons": selection_reasons or [{"kind": "full-mode"}],
    }


def _make_plan(checks=None, content_fingerprint="a" * 64):
    if checks is None:
        checks = [_make_check()]
    return {
        "schema_version": PLAN_SCHEMA,
        "mode": "incremental",
        "receipt_kind": "TASK_VALIDATION",
        "content_fingerprint": content_fingerprint,
        "task": {
            "task_id": "LF-TSK-QLT-0009",
            "task_version": 1,
            "change_version": "1.0.0",
        },
        "checks": checks,
    }


def _make_process_result(
    return_code=0,
    stdout=b"",
    stderr=b"",
    exit_reason="EXITED",
    signal_number=None,
    stdout_truncated=False,
    stderr_truncated=False,
    child_pid=12345,
    duration=0.1,
    argv=None,
    cwd="/repo",
    timeout=60,
):
    now = "2026-09-16T10:00:00.000Z"
    return ProcessResult(
        return_code=return_code,
        signal_number=signal_number,
        exit_reason=exit_reason,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        started_at=now,
        finished_at=now,
        duration_seconds=duration,
        child_pid=child_pid,
        timeout_seconds=timeout,
        argv=tuple(argv) if argv else (),
        cwd=cwd,
    )


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.results = {}
        self.default_result = None
        self.raise_interruption_after = None

    def __call__(self, request):
        self.calls.append(request)
        if self.raise_interruption_after is not None and len(self.calls) > self.raise_interruption_after:
            raise ExternalInterruption()
        key = tuple(request.argv)
        if key in self.results:
            r = self.results[key]
            if callable(r):
                return r(request)
            if isinstance(r, ProcessResult):
                return replace(
                    r, argv=request.argv, cwd=request.cwd,
                    timeout_seconds=request.timeout_seconds,
                )
            return r
        if self.default_result:
            if isinstance(self.default_result, ProcessResult):
                return replace(
                    self.default_result, argv=request.argv, cwd=request.cwd,
                    timeout_seconds=request.timeout_seconds,
                )
            return self.default_result
        return _make_process_result(argv=list(request.argv), cwd=request.cwd, timeout=request.timeout_seconds)


class TestPlanValidation(unittest.TestCase):
    def test_rejects_wrong_schema(self):
        plan = _make_plan()
        plan["schema_version"] = "wrong"
        result = execute_checks(plan, repo_root="/tmp")
        self.assertEqual(result["run_status"], "FAIL")
        self.assertEqual(result["run_reason"], "schema-mismatch")

    def test_rejects_empty_checks(self):
        plan = _make_plan(checks=[])
        result = execute_checks(plan, repo_root="/tmp")
        self.assertEqual(result["run_status"], "FAIL")
        self.assertEqual(result["run_reason"], "empty-checks")

    def test_rejects_duplicate_check_id(self):
        checks = [_make_check(check_id="same"), _make_check(check_id="same", subject_task_id="LF-TSK-QLT-0008")]
        plan = _make_plan(checks=checks)
        result = execute_checks(plan, repo_root="/tmp")
        self.assertEqual(result["run_status"], "FAIL")
        self.assertEqual(result["run_reason"], "duplicate-check-id")

    def test_rejects_duplicate_subject_task(self):
        checks = [
            _make_check(check_id="a", subject_task_id="LF-TSK-QLT-0009"),
            _make_check(check_id="b", subject_task_id="LF-TSK-QLT-0009"),
        ]
        plan = _make_plan(checks=checks)
        result = execute_checks(plan, repo_root="/tmp")
        self.assertEqual(result["run_status"], "FAIL")
        self.assertEqual(result["run_reason"], "duplicate-subject-task")

    def test_rejects_missing_required_fields(self):
        check = _make_check()
        del check["fixed_argv"]
        plan = _make_plan(checks=[check])
        result = execute_checks(plan, repo_root="/tmp")
        self.assertEqual(result["run_status"], "FAIL")
        self.assertEqual(result["run_reason"], "invalid-check-projection")

    def test_rejects_invalid_task_id(self):
        check = _make_check()
        check["subject_task"]["task_id"] = "INVALID"
        plan = _make_plan(checks=[check])
        result = execute_checks(plan, repo_root="/tmp")
        self.assertEqual(result["run_status"], "FAIL")
        self.assertEqual(result["run_reason"], "subject-identity-mismatch")

    def test_rejects_subject_version_mismatch(self):
        check = _make_check()
        check["subject_task"]["task_version"] = 0
        plan = _make_plan(checks=[check])
        result = execute_checks(plan, repo_root="/tmp")
        self.assertEqual(result["run_status"], "FAIL")
        self.assertEqual(result["run_reason"], "subject-identity-mismatch")

    def test_rejects_owner_mismatch(self):
        check = _make_check()
        check["owner"] = "not-valid!"
        plan = _make_plan(checks=[check])
        result = execute_checks(plan, repo_root="/tmp")
        self.assertEqual(result["run_status"], "FAIL")
        self.assertEqual(result["run_reason"], "owner-mismatch")


class TestFixedArgvAndCwd(unittest.TestCase):
    def test_exact_argv_passed_with_shell_disabled(self):
        runner = FakeRunner()
        argv = ["python3", "-m", "unittest", "discover", "-s", "tests"]
        check = _make_check(argv=argv)
        plan = _make_plan(checks=[check])

        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(list(runner.calls[0].argv), argv)
        self.assertEqual(result["checks"][0]["process"]["argv"], argv)

    def test_cwd_resolved_relative_to_repo_root(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / "sub"
            subdir.mkdir()
            check = _make_check(cwd="sub")
            plan = _make_plan(checks=[check])
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(runner.calls[0].cwd, str(subdir))

    def test_dot_cwd_resolves_to_repo_root(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            check = _make_check(cwd=".")
            plan = _make_plan(checks=[check])
            execute_checks(plan, repo_root=tmpdir, process_runner=runner)
            self.assertEqual(runner.calls[0].cwd, os.path.abspath(tmpdir))

    def test_symlink_cwd_is_rejected_before_spawn(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as outside:
            (Path(tmpdir) / "escape").symlink_to(outside, target_is_directory=True)
            check = _make_check(cwd="escape")
            result = execute_checks(_make_plan(checks=[check]), repo_root=tmpdir, process_runner=runner)
        self.assertEqual(runner.calls, [])
        self.assertEqual(result["run_status"], "FAIL")
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "invalid-cwd")

    def test_rejects_absolute_cwd(self):
        check = _make_check(cwd="/absolute/path")
        plan = _make_plan(checks=[check])
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(plan, repo_root=tmpdir, process_runner=FakeRunner())
        self.assertEqual(result["checks"][0]["outcome"]["status"], "FAIL")
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "invalid-cwd")

    def test_rejects_cwd_escaping_root(self):
        check = _make_check(cwd="../../etc")
        plan = _make_plan(checks=[check])
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(plan, repo_root=tmpdir, process_runner=FakeRunner())
        self.assertEqual(result["checks"][0]["outcome"]["status"], "FAIL")
        self.assertIn(result["checks"][0]["outcome"]["reason"], ("invalid-cwd",))

    def test_rejects_nonexistent_cwd(self):
        check = _make_check(cwd="nonexistent_dir")
        plan = _make_plan(checks=[check])
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(plan, repo_root=tmpdir, process_runner=FakeRunner())
        self.assertEqual(result["checks"][0]["outcome"]["status"], "FAIL")
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "invalid-cwd")

    def test_argv_fingerprint_is_deterministic(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            check = _make_check()
            plan = _make_plan(checks=[check])
            r1 = execute_checks(plan, repo_root=tmpdir, process_runner=runner)
            r2 = execute_checks(plan, repo_root=tmpdir, process_runner=runner)
        self.assertEqual(
            r1["checks"][0]["process"]["argv_fingerprint"],
            r2["checks"][0]["process"]["argv_fingerprint"],
        )


class TestCheckerOwnerSubjectSeparation(unittest.TestCase):
    def test_checker_owner_and_subject_owner_are_independent(self):
        check = _make_check(owner="LF-WS-QLT", subject_owner="LF-WS-ARCH")
        plan = _make_plan(checks=[check])
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(plan, repo_root=tmpdir, process_runner=FakeRunner())
        self.assertEqual(result["checks"][0]["owner"], "LF-WS-QLT")
        self.assertEqual(result["checks"][0]["subject_task"]["owner"], "LF-WS-ARCH")

    def test_subject_identity_mismatch_rejected(self):
        check = _make_check()
        check["subject_task"]["task_id"] = "NOT-A-TASK"
        plan = _make_plan(checks=[check])
        result = execute_checks(plan, repo_root="/tmp")
        self.assertEqual(result["run_status"], "FAIL")
        self.assertEqual(result["run_reason"], "subject-identity-mismatch")

    def test_subject_version_mismatch_rejected(self):
        check = _make_check()
        check["subject_task"]["change_version"] = "2.0.0"
        plan = _make_plan(checks=[check])
        result = execute_checks(plan, repo_root="/tmp")
        self.assertEqual(result["run_status"], "FAIL")


class TestConsumedInputVerification(unittest.TestCase):
    def _setup_repo(self, tmpdir):
        root = Path(tmpdir)
        (root / "input.txt").write_bytes(b"hello world")
        return root

    def _input_descriptor(self, locator, content):
        return {"locator": locator, "state": "present", "sha256": _sha(content)}

    def test_pre_and_post_verification_pass(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = self._setup_repo(tmpdir)
            content = b"hello world"
            desc = self._input_descriptor("input.txt", content)
            check = _make_check(consumed_inputs=[desc])
            plan = _make_plan(checks=[check])
            result = execute_checks(plan, repo_root=str(root), process_runner=FakeRunner())

        self.assertEqual(result["checks"][0]["outcome"]["status"], "FAIL")
        pre = result["checks"][0]["consumed_input_verification"]["pre_execution"]
        post = result["checks"][0]["consumed_input_verification"]["post_execution"]
        self.assertEqual(pre[0]["status"], "verified")
        self.assertEqual(post[0]["status"], "verified")

    def test_missing_input_fails_before_process(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            desc = {"locator": "missing.txt", "state": "present", "sha256": "a" * 64}
            check = _make_check(consumed_inputs=[desc])
            plan = _make_plan(checks=[check])
            runner = FakeRunner()
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(len(runner.calls), 0)
        self.assertEqual(result["checks"][0]["outcome"]["status"], "FAIL")
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "input-drift")
        self.assertEqual(result["checks"][0]["process"]["exit_reason"], "INPUT_DRIFT")
        self.assertIsNone(result["checks"][0]["process"]["child_pid"])

    def test_changed_input_pre_exec_blocks_process(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "input.txt").write_bytes(b"original")
            desc = {"locator": "input.txt", "state": "present", "sha256": _sha(b"different")}
            check = _make_check(consumed_inputs=[desc])
            plan = _make_plan(checks=[check])
            runner = FakeRunner()
            result = execute_checks(plan, repo_root=str(root), process_runner=runner)

        self.assertEqual(len(runner.calls), 0)
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "input-drift")

    def test_symlink_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "target.txt"
            target.write_bytes(b"data")
            link = root / "link.txt"
            link.symlink_to(target)
            desc = {"locator": "link.txt", "state": "present", "sha256": _sha(b"data")}
            check = _make_check(consumed_inputs=[desc])
            plan = _make_plan(checks=[check])
            result = execute_checks(plan, repo_root=str(root), process_runner=FakeRunner())

        self.assertEqual(result["checks"][0]["outcome"]["reason"], "input-drift")
        pre = result["checks"][0]["consumed_input_verification"]["pre_execution"]
        self.assertIn(pre[0]["status"], ("input-symlink", "drift"))

    def test_fifo_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fifo = root / "myfifo"
            try:
                os.mkfifo(str(fifo))
            except OSError:
                self.skipTest("cannot create FIFO on this platform")
            desc = {"locator": "myfifo", "state": "present", "sha256": "a" * 64}
            check = _make_check(consumed_inputs=[desc])
            plan = _make_plan(checks=[check])
            result = execute_checks(plan, repo_root=str(root), process_runner=FakeRunner())

        self.assertEqual(result["checks"][0]["outcome"]["status"], "FAIL")

    def test_no_process_on_precheck_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            desc = {"locator": "nonexistent.txt", "state": "present", "sha256": "a" * 64}
            checks = [
                _make_check(check_id="a", consumed_inputs=[desc], subject_task_id="LF-TSK-QLT-0009"),
                _make_check(check_id="b", consumed_inputs=[], subject_task_id="LF-TSK-QLT-0008"),
            ]
            plan = _make_plan(checks=checks)
            runner = FakeRunner()
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "input-drift")
        self.assertEqual(result["checks"][1]["outcome"]["status"], "FAIL")


class TestEnvironmentSanitization(unittest.TestCase):
    def test_secrets_excluded(self):
        with mock.patch.dict(os.environ, {
            "CODEX_API_KEY": "secret",
            "QODER_TOKEN": "secret",
            "CLAUDE_SECRET": "secret",
            "OPENAI_API_KEY": "secret",
            "PATH": "/usr/bin",
            "HOME": "/home/user",
        }, clear=False):
            env = _build_child_environment()
        self.assertNotIn("CODEX_API_KEY", env)
        self.assertNotIn("QODER_TOKEN", env)
        self.assertNotIn("CLAUDE_SECRET", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("PATH", env)
        self.assertNotIn("HOME", env)

    def test_python_and_dynamic_library_injection_paths_are_excluded(self):
        with mock.patch.dict(os.environ, {
            "PATH": "/usr/bin",
            "PYTHONPATH": "/tmp/inject",
            "LD_LIBRARY_PATH": "/tmp/inject",
        }, clear=True):
            env = _build_child_environment()
        self.assertNotIn("PATH", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("LD_LIBRARY_PATH", env)

    def test_environment_fingerprint_excludes_secrets(self):
        with mock.patch.dict(os.environ, {"CODEX_KEY": "x", "PATH": "/usr/bin"}, clear=False):
            env = _build_child_environment()
            fp = _environment_fingerprint(env)
        self.assertIsInstance(fp, str)
        self.assertEqual(len(fp), 64)
        self.assertNotIn("x", fp)

    def test_fingerprint_is_deterministic(self):
        env = {"PATH": "/usr/bin", "HOME": "/home"}
        fp1 = _environment_fingerprint(env)
        fp2 = _environment_fingerprint(env)
        self.assertEqual(fp1, fp2)


class TestProcessFactsVsTypedOutcome(unittest.TestCase):
    def test_process_facts_separate_from_outcome(self):
        runner = FakeRunner()
        unittest_pass = b"Ran 3 tests in 0.001s\n\nOK\n"
        runner.default_result = _make_process_result(
            return_code=0, stdout=unittest_pass, stderr=b"",
            argv=["python3", "-m", "unittest"], cwd="/repo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = _make_plan()
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        check = result["checks"][0]
        process = check["process"]
        outcome = check["outcome"]
        self.assertIn("return_code", process)
        self.assertIn("exit_reason", process)
        self.assertIn("stdout_sha256", process)
        self.assertIn("stderr_sha256", process)
        self.assertEqual(process["executable_locator"], os.path.realpath(sys.executable))
        self.assertEqual(process["executable_sha256"], _sha(Path(os.path.realpath(sys.executable)).read_bytes()))
        self.assertEqual(process["executable_verification"], {"pre_execution": "verified", "post_execution": "verified"})
        self.assertEqual(process["executed_argv"][0], os.path.realpath(sys.executable))
        self.assertIn("status", outcome)
        self.assertIn("reason", outcome)
        self.assertIn("assertions", outcome)
        self.assertEqual(outcome["schema"], CHECK_OUTCOME_SCHEMA)

    def test_exit_zero_without_proof_is_fail(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=0, stdout=b"some random output\n", stderr=b"",
            argv=["python3", "-m", "unittest"], cwd="/repo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = _make_plan()
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(result["checks"][0]["outcome"]["status"], "FAIL")
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "unproved-exit-zero")

    def test_exit_zero_with_proof_is_pass(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=0,
            stdout=b"Ran 5 tests in 0.01s\n\nOK\n",
            stderr=b"",
            argv=["python3", "-m", "unittest"], cwd="/repo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = _make_plan()
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(result["checks"][0]["outcome"]["status"], "PASS")


class TestUnittestAdapter(unittest.TestCase):
    def _result(self, rc=0, stdout=b"", stderr=b"", exit_reason="EXITED"):
        return _make_process_result(return_code=rc, stdout=stdout, stderr=stderr, exit_reason=exit_reason, argv=["python3", "-m", "unittest"])

    def test_pass_exact_ok_no_parenthetical(self):
        r = self._result(stdout=b"Ran 3 tests in 0.001s\n\nOK\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "PASS")
        self.assertEqual(outcome["assertions"], 3)

    def test_ok_with_skip_is_not_pass(self):
        r = self._result(stdout=b"Ran 3 tests in 0.001s\n\nOK (skipped=1)\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "malformed-output")

    def test_ok_with_expected_failures_is_not_pass(self):
        r = self._result(stdout=b"Ran 3 tests in 0.001s\n\nOK (expected_failures=1)\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")

    def test_failures_zero_errors_is_blocked(self):
        r = self._result(rc=1, stdout=b"Ran 3 tests in 0.001s\n\nFAILED (failures=2)\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "BLOCKED")
        self.assertEqual(outcome["reason"], "assertion-failure")
        self.assertEqual(outcome["failures"], 2)
        self.assertEqual(outcome["errors"], 0)

    def test_errors_present_is_fail(self):
        r = self._result(rc=1, stdout=b"Ran 3 tests in 0.001s\n\nFAILED (failures=1, errors=1)\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "invocation-error")

    def test_only_errors_is_fail(self):
        r = self._result(rc=1, stdout=b"Ran 3 tests in 0.001s\n\nFAILED (errors=2)\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")

    def test_zero_tests_is_fail(self):
        r = self._result(stdout=b"Ran 0 tests in 0.000s\n\nOK\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "zero-suite")

    def test_duplicate_ran_markers_is_fail(self):
        r = self._result(stdout=b"Ran 3 tests\nRan 5 tests\n\nOK\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "duplicate-marker")

    def test_ran_in_both_streams_is_duplicate(self):
        r = self._result(stdout=b"Ran 3 tests\n", stderr=b"Ran 3 tests\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "duplicate-marker")

    def test_missing_ran_marker_is_fail(self):
        r = self._result(stdout=b"some output\n", stderr=b"")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "unproved-exit-zero")


    def test_exit_zero_without_ok_is_fail(self):
        r = self._result(rc=0, stdout=b"Ran 3 tests\n", stderr=b"")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "unproved-exit-zero")

    def test_ok_with_nonzero_exit_is_fail(self):
        r = self._result(rc=1, stdout=b"Ran 3 tests\n\nOK\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "status-exit-mismatch")

    def test_failed_with_zero_exit_is_fail(self):
        r = self._result(rc=0, stdout=b"Ran 3 tests\n\nFAILED (failures=1)\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "status-exit-mismatch")

    def test_failed_with_exit_two_is_fail(self):
        r = self._result(rc=2, stdout=b"Ran 3 tests\nFAILED (failures=1)\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "status-exit-mismatch")

    def test_failed_summary_unknown_duplicate_or_impossible_fields_fail(self):
        summaries = (
            b"FAILED (failures=1, bananas=1)",
            b"FAILED (failures=1, failures=1)",
            b"FAILED (failures=4)",
        )
        for summary in summaries:
            with self.subTest(summary=summary):
                outcome = _adapt_unittest(self._result(rc=1, stdout=b"Ran 3 tests\n" + summary + b"\n"), {})
                self.assertEqual(outcome["status"], "FAIL")
                self.assertEqual(outcome["reason"], "malformed-output")

    def test_failed_summary_with_skips_is_fail(self):
        outcome = _adapt_unittest(
            self._result(rc=1, stdout=b"Ran 3 tests\nFAILED (failures=1, skipped=1)\n"),
            {},
        )
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "skipped-tests")

    def test_exit_2_is_fail(self):
        r = self._result(rc=2, stdout=b"Ran 3 tests\n", stderr=b"")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")

    def test_truncated_proof_is_fail(self):
        r = _make_process_result(stdout=b"Ran 3 tests", stdout_truncated=True, argv=["python3", "-m", "unittest"])
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")

    def test_generic_warning_not_treated_as_proof(self):
        r = self._result(rc=0, stdout=b"WARNING: something\nRan 3 tests\n\nOK\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "PASS")

    def test_all_skipped_is_still_ran(self):
        r = self._result(rc=0, stdout=b"Ran 3 tests in 0.001s\n\nOK (skipped=3)\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "malformed-output")

    def test_partial_skipped_with_ok(self):
        r = self._result(rc=0, stdout=b"Ran 5 tests in 0.01s\n\nOK (skipped=2)\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")

    def test_conflicting_ok_and_failed(self):
        r = self._result(rc=1, stdout=b"Ran 3 tests\n\nOK\n\nFAILED (failures=1)\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "conflicting-marker")

    def test_duplicate_terminal_markers_fail_without_crashing(self):
        for terminal in (b"OK\nOK\n", b"FAILED (failures=1)\nFAILED (failures=1)\n"):
            with self.subTest(terminal=terminal):
                r = self._result(rc=0 if terminal.startswith(b"OK") else 1, stdout=b"Ran 3 tests\n" + terminal)
                outcome = _adapt_unittest(r, {})
                self.assertEqual(outcome["status"], "FAIL")
                self.assertEqual(outcome["reason"], "duplicate-marker")

    def test_malformed_ran_suffix_is_not_proof(self):
        r = self._result(stdout=b"Ran 3 tests attacker-controlled\nOK\n")
        outcome = _adapt_unittest(r, {})
        self.assertEqual(outcome["status"], "FAIL")

    def test_invalid_utf8_is_not_structured_proof(self):
        outcome = _adapt_unittest(self._result(stdout=b"\xffRan 3 tests\nOK\n"), {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "malformed-output")


class TestControlledRuntimeAdapter(unittest.TestCase):
    def _result(self, status, return_code):
        return _make_process_result(
            return_code=return_code,
            stdout=json.dumps({"status": status}, sort_keys=True).encode() + b"\n",
            stderr=b"",
            argv=["python3", "scripts/toolchain/postgres_test.py", "verify", "--scope", "all"],
        )

    def test_blocked_runtime_is_preserved(self):
        outcome = _adapt_controlled_runtime(self._result("BLOCKED", 1), {})

        self.assertEqual("BLOCKED", outcome["status"])

    def test_pass_runtime_requires_zero_exit(self):
        outcome = _adapt_controlled_runtime(self._result("PASS", 0), {})

        self.assertEqual("PASS", outcome["status"])


class TestPlanningValidatorAdapter(unittest.TestCase):
    def _result(self, rc=0, stdout=b"", stderr=b""):
        return _make_process_result(return_code=rc, stdout=stdout, stderr=stderr, argv=["python3", "-m", "scripts.gates.planning", "--root", "."])

    def test_pass_positive(self):
        out = b"Planning validator: PASS\nTasks validated: 5\nChecks executed: 3 (a, b, c)\n"
        r = self._result(rc=0, stdout=out)
        outcome = _adapt_planning_validator(r, {})
        self.assertEqual(outcome["status"], "PASS")
        self.assertEqual(outcome["evidence"]["tasks_validated"], 5)
        self.assertEqual(outcome["evidence"]["checks_executed"], 3)

    def test_blocked_positive(self):
        out = b"Planning validator: BLOCKED\nTasks validated: 5\nChecks executed: 3 (a, b, c)\n"
        r = self._result(rc=1, stdout=out)
        outcome = _adapt_planning_validator(r, {})
        self.assertEqual(outcome["status"], "BLOCKED")

    def test_fail_positive(self):
        out = b"Planning validator: FAIL\nTasks validated: 0\nChecks executed: 0 ()\n"
        r = self._result(rc=1, stdout=out)
        outcome = _adapt_planning_validator(r, {})
        self.assertEqual(outcome["status"], "FAIL")

    def test_pass_with_zero_tasks_is_fail(self):
        out = b"Planning validator: PASS\nTasks validated: 0\nChecks executed: 3 (a, b, c)\n"
        r = self._result(rc=0, stdout=out)
        outcome = _adapt_planning_validator(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "count-exit-mismatch")

    def test_pass_with_zero_checks_is_fail(self):
        out = b"Planning validator: PASS\nTasks validated: 5\nChecks executed: 0 ()\n"
        r = self._result(rc=0, stdout=out)
        outcome = _adapt_planning_validator(r, {})
        self.assertEqual(outcome["status"], "FAIL")

    def test_pass_with_nonzero_exit_is_fail(self):
        out = b"Planning validator: PASS\nTasks validated: 5\nChecks executed: 3 (a, b, c)\n"
        r = self._result(rc=1, stdout=out)
        outcome = _adapt_planning_validator(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "status-exit-mismatch")

    def test_blocked_with_exit_0_is_fail(self):
        out = b"Planning validator: BLOCKED\nTasks validated: 5\nChecks executed: 3 (a, b, c)\n"
        r = self._result(rc=0, stdout=out)
        outcome = _adapt_planning_validator(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "status-exit-mismatch")

    def test_duplicate_status_marker_is_fail(self):
        out = b"Planning validator: PASS\nPlanning validator: FAIL\nTasks validated: 5\nChecks executed: 3 (a, b, c)\n"
        r = self._result(rc=0, stdout=out)
        outcome = _adapt_planning_validator(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "duplicate-marker")

    def test_missing_tasks_marker_is_fail(self):
        out = b"Planning validator: PASS\nChecks executed: 3 (a, b, c)\n"
        r = self._result(rc=0, stdout=out)
        outcome = _adapt_planning_validator(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "missing-proof")

    def test_malformed_tasks_marker_is_fail(self):
        out = b"Planning validator: PASS\nTasks validated: abc\nChecks executed: 3 (a, b, c)\n"
        r = self._result(rc=0, stdout=out)
        outcome = _adapt_planning_validator(r, {})
        self.assertEqual(outcome["status"], "FAIL")

    def test_exit_2_with_fail_status(self):
        out = b"Planning validator: FAIL\nTasks validated: 0\nChecks executed: 0 ()\n"
        r = self._result(rc=2, stdout=out)
        outcome = _adapt_planning_validator(r, {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "status-exit-mismatch")

    def test_checks_marker_requires_complete_line(self):
        out = b"Planning validator: PASS\nTasks validated: 5\nChecks executed: 3 attacker\n"
        outcome = _adapt_planning_validator(self._result(rc=0, stdout=out), {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "missing-proof")

    def test_invalid_utf8_is_fail(self):
        outcome = _adapt_planning_validator(self._result(rc=0, stdout=b"\xffPlanning validator: PASS\n"), {})
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "malformed-output")


class TestUnknownAdapter(unittest.TestCase):
    def test_unknown_command_is_fail(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=0, stdout=b"ok\n", stderr=b"",
            argv=["bash", "-c", "echo ok"], cwd="/repo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            check = _make_check(argv=["bash", "-c", "echo ok"])
            plan = _make_plan(checks=[check])
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(result["checks"][0]["outcome"]["status"], "FAIL")
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "unknown-adapter")
        self.assertEqual(len(runner.calls), 0)

    def test_select_adapter_returns_none_for_unknown(self):
        self.assertIsNone(_select_adapter(["bash", "-c", "echo"], "unknown.v1"))
        self.assertIsNone(_select_adapter([], "unknown.v1"))

    def test_select_adapter_unittest(self):
        adapter = _select_adapter(["python3", "-m", "unittest", "discover"], "qlt.executor.validate.v1")
        self.assertIs(adapter, _adapt_unittest)

    def test_current_registry_unittest_commands_have_an_adapter(self):
        registry = yaml.safe_load(
            (Path(__file__).resolve().parents[2] / "harness/gate-check-registry.yaml").read_text()
        )
        entries = registry["entries"]
        commands = [entry for entry in entries if entry["fixed_argv"][:3] == ["python3", "-m", "unittest"]]
        self.assertGreater(len(commands), 0)
        for entry in commands:
            with self.subTest(command_id=entry["command_id"]):
                self.assertIs(_select_adapter(entry["fixed_argv"], entry["command_id"]), _adapt_unittest)

    def test_unregistered_unittest_versions_are_rejected(self):
        for command_id in ("qlt.evidence.validate.v2", "qlt.issuer.validate.v99"):
            with self.subTest(command_id=command_id):
                self.assertIsNone(_select_adapter(["python3", "-m", "unittest"], command_id))

    def test_select_adapter_planning(self):
        adapter = _select_adapter(["python3", "-m", "scripts.gates.planning", "--root", "."], "qlt.planning.validate.v1")
        self.assertIs(adapter, _adapt_planning_validator)

    def test_select_adapter_task_contract(self):
        adapter = _select_adapter(
            ["python3", "-m", "scripts.gates.task_contracts", "--task-id", "LF-TSK-ARCH-0001"],
            "g1.arch.0001.contract.v1",
        )
        self.assertIs(adapter, _adapt_task_contract)

    def test_adapter_rejects_command_identity_mismatch(self):
        self.assertIsNone(_select_adapter(["python3", "-m", "unittest", "discover"], "qlt.planning.validate.v1"))
        self.assertIsNone(_select_adapter(["python3", "-m", "scripts.gates.planning", "--root", "."], "qlt.executor.validate.v1"))
        self.assertIsNone(_select_adapter([sys.executable, "-m", "unittest", "discover"], "qlt.executor.validate.v1"))


class TestTaskContractAdapter(unittest.TestCase):
    @staticmethod
    def _result(status="PASS", return_code=0, task_id="LF-TSK-ARCH-0001"):
        value = {
            "schema_version": "lexiflow.task-contract-check.v1",
            "task_id": task_id,
            "status": status,
            "assertions": [{
                "id": "contract-present",
                "status": status,
                "required_terms": ["contract"],
                "missing_terms": [] if status == "PASS" else ["contract"],
            }],
            "inputs": [{"locator": "docs/example.md", "sha256": "a" * 64, "bytes": 1}],
        }
        stdout = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        return _make_process_result(return_code=return_code, stdout=stdout)

    def test_pass_and_blocked_are_typed(self):
        check = _make_check(subject_task_id="LF-TSK-ARCH-0001")
        passed = _adapt_task_contract(self._result(), check)
        blocked = _adapt_task_contract(self._result(status="BLOCKED", return_code=1), check)
        self.assertEqual(passed["status"], "PASS")
        self.assertEqual(passed["assertions"], 1)
        self.assertEqual(blocked["status"], "BLOCKED")

    def test_subject_or_exit_mismatch_is_fail(self):
        check = _make_check(subject_task_id="LF-TSK-ARCH-0001")
        wrong_subject = _adapt_task_contract(self._result(task_id="LF-TSK-ARCH-0002"), check)
        wrong_exit = _adapt_task_contract(self._result(return_code=1), check)
        self.assertEqual(wrong_subject["reason"], "subject-identity-mismatch")
        self.assertEqual(wrong_exit["reason"], "malformed-output")

    def test_adapter_exception_becomes_typed_fail(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=0, stdout=b"Ran 1 test\nOK\n",
            argv=["python3", "-m", "unittest"], cwd="/repo",
        )
        def broken_adapter(result, check):
            raise RuntimeError("untrusted detail")
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "scripts.gates.executor._select_adapter", return_value=broken_adapter,
        ):
            result = execute_checks(_make_plan(), repo_root=tmpdir, process_runner=runner)
        outcome = result["checks"][0]["outcome"]
        self.assertEqual(outcome["status"], "FAIL")
        self.assertEqual(outcome["reason"], "adapter-failure")
        self.assertEqual(outcome["evidence"], {"error_type": "RuntimeError"})


class TestOutputLimitAndCapture(unittest.TestCase):
    def test_stdout_overflow_terminates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "test_big_output.py"
            script.write_text(
                "import unittest\n"
                "print('x' * 500000)\n"
                "class BigOutputTest(unittest.TestCase):\n"
                "    def test_ok(self): self.assertTrue(True)\n"
            )
            argv = ["python3", "-m", "unittest", "discover", "-s", ".", "-p", "test_big_output.py"]
            check = _make_check(argv=argv)
            plan = _make_plan(checks=[check])
            result = execute_checks(plan, repo_root=tmpdir, stdout_limit=10000, stderr_limit=10000)

        check_result = result["checks"][0]
        self.assertEqual(check_result["outcome"]["status"], "FAIL")
        self.assertEqual(check_result["outcome"]["reason"], "output-limit-exceeded")
        self.assertEqual(check_result["process"]["exit_reason"], "OUTPUT_LIMIT")
        self.assertTrue(check_result["process"]["stdout_truncated"])
        self.assertFalse(check_result["process"]["stderr_truncated"])

    def test_bounded_capture_preserves_partial(self):
        runner = FakeRunner()
        big_stdout = b"x" * 100
        runner.default_result = _make_process_result(
            return_code=0, stdout=big_stdout, stderr=b"err",
            stdout_truncated=True,
            argv=["python3", "-m", "unittest"], cwd="/repo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = _make_plan()
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        process = result["checks"][0]["process"]
        self.assertEqual(process["stdout_bytes"], 100)
        self.assertEqual(process["stderr_bytes"], 3)
        self.assertTrue(process["stdout_truncated"])

    def test_concurrent_streams_captured(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=0,
            stdout=b"stdout data\nRan 1 test in 0.001s\n\nOK\n",
            stderr=b"stderr data\n",
            argv=["python3", "-m", "unittest"],
            cwd="/repo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            check = _make_check()
            plan = _make_plan(checks=[check])
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        process = result["checks"][0]["process"]
        self.assertGreater(process["stdout_bytes"], 0)
        self.assertGreater(process["stderr_bytes"], 0)
        self.assertEqual(result["checks"][0]["outcome"]["status"], "PASS")

    def test_capture_failure_is_typed_fail(self):
        runner = FakeRunner()
        base = _make_process_result(
            return_code=0,
            stdout=b"Ran 1 test in 0.001s\nOK\n",
            stderr=b"",
            argv=["python3", "-m", "unittest"],
            cwd="/repo",
        )
        runner.default_result = ProcessResult(
            **{**base.__dict__, "exit_reason": "CAPTURE_ERROR", "capture_error": True}
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(_make_plan(), repo_root=tmpdir, process_runner=runner)
        self.assertEqual(result["checks"][0]["outcome"]["status"], "FAIL")
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "capture-failure")
        self.assertTrue(result["checks"][0]["process"]["capture_error"])


class TestTimeoutAndSignal(unittest.TestCase):
    def test_timeout_terminates_process(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "test_slow.py"
            script.write_text(
                "import time\nimport unittest\n"
                "time.sleep(30)\n"
                "class SlowTest(unittest.TestCase):\n"
                "    def test_ok(self): self.assertTrue(True)\n"
            )
            argv = ["python3", "-m", "unittest", "discover", "-s", ".", "-p", "test_slow.py"]
            check = _make_check(argv=argv, timeout=1)
            plan = _make_plan(checks=[check])
            result = execute_checks(plan, repo_root=tmpdir)

        check_result = result["checks"][0]
        self.assertEqual(check_result["outcome"]["status"], "FAIL")
        self.assertEqual(check_result["outcome"]["reason"], "timeout")
        self.assertEqual(check_result["process"]["exit_reason"], "TIMEOUT")

    def test_signal_death_is_fail(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=-9, signal_number=9, exit_reason="SIGNAL",
            argv=["python3", "-m", "unittest"], cwd="/repo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = _make_plan()
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(result["checks"][0]["outcome"]["status"], "FAIL")
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "signal-death")
        self.assertEqual(result["checks"][0]["process"]["signal"], 9)

    def test_spawn_missing_executable(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=None, exit_reason="SPAWN_ERROR",
            argv=["python3", "-m", "unittest"], cwd="/repo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            check = _make_check(argv=["python3", "-m", "unittest"])
            plan = _make_plan(checks=[check])
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(result["checks"][0]["outcome"]["status"], "FAIL")
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "spawn-error")

    def test_unknown_process_state_cannot_reuse_success_markers(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=0,
            stdout=b"Ran 1 test\n\nOK\n",
            exit_reason="QUEUED",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(
                _make_plan(), repo_root=tmpdir, process_runner=runner,
            )

        self.assertEqual(result["run_status"], "FAIL")
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "unknown-process-state")

    def test_malformed_process_result_is_retained_as_fail(self):
        runner = FakeRunner()
        runner.default_result = {"return_code": 0}
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(
                _make_plan(), repo_root=tmpdir, process_runner=runner,
            )

        self.assertEqual(result["run_status"], "FAIL")
        self.assertEqual(len(result["checks"]), 1)
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "malformed-process-result")
        self.assertEqual(
            result["checks"][0]["process"]["exit_reason"],
            "MALFORMED_PROCESS_RESULT",
        )

    def test_process_result_cannot_claim_different_fixed_argv(self):
        def drifted_result(_request):
            return _make_process_result(
                return_code=0,
                stdout=b"Ran 1 test\n\nOK\n",
                argv=["python3", "-m", "unittest", "forged"],
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(
                _make_plan(), repo_root=tmpdir, process_runner=drifted_result,
            )

        self.assertEqual(result["run_status"], "FAIL")
        self.assertEqual(result["checks"][0]["outcome"]["reason"], "malformed-process-result")
        self.assertEqual(result["checks"][0]["outcome"]["evidence"]["problem"], "request-binding")


class TestContinuationAfterFailure(unittest.TestCase):
    def test_continues_after_blocked(self):
        runner = FakeRunner()
        blocked_out = b"Ran 3 tests\n\nFAILED (failures=1)\n"
        pass_out = b"Ran 2 tests\n\nOK\n"
        runner.results = {
            ("python3", "-m", "unittest", "a"): _make_process_result(return_code=1, stdout=blocked_out, argv=["python3", "-m", "unittest", "a"]),
            ("python3", "-m", "unittest", "b"): _make_process_result(return_code=0, stdout=pass_out, argv=["python3", "-m", "unittest", "b"]),
        }
        checks = [
            _make_check(check_id="a", argv=["python3", "-m", "unittest", "a"], subject_task_id="LF-TSK-QLT-0009"),
            _make_check(check_id="b", argv=["python3", "-m", "unittest", "b"], subject_task_id="LF-TSK-QLT-0008"),
        ]
        plan = _make_plan(checks=checks)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(len(result["checks"]), 2)
        self.assertEqual(result["checks"][0]["outcome"]["status"], "BLOCKED")
        self.assertEqual(result["checks"][1]["outcome"]["status"], "PASS")
        self.assertEqual(result["run_status"], "BLOCKED")

    def test_continues_after_per_check_fail(self):
        runner = FakeRunner()
        runner.results = {
            ("python3", "-m", "unittest", "a"): _make_process_result(return_code=0, stdout=b"no proof", argv=["python3", "-m", "unittest", "a"]),
            ("python3", "-m", "unittest", "b"): _make_process_result(return_code=0, stdout=b"Ran 1 test\n\nOK\n", argv=["python3", "-m", "unittest", "b"]),
        }
        checks = [
            _make_check(check_id="a", argv=["python3", "-m", "unittest", "a"], subject_task_id="LF-TSK-QLT-0009"),
            _make_check(check_id="b", argv=["python3", "-m", "unittest", "b"], subject_task_id="LF-TSK-QLT-0008"),
        ]
        plan = _make_plan(checks=checks)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(len(result["checks"]), 2)
        self.assertEqual(result["checks"][0]["outcome"]["status"], "FAIL")
        self.assertEqual(result["checks"][1]["outcome"]["status"], "PASS")
        self.assertEqual(result["run_status"], "FAIL")

    def test_external_interruption_marks_remaining_not_run(self):
        runner = FakeRunner()
        runner.results = {
            ("python3", "-m", "unittest", "a"): _make_process_result(return_code=0, stdout=b"Ran 1 test\n\nOK\n", argv=["python3", "-m", "unittest", "a"]),
        }
        runner.raise_interruption_after = 1

        checks = [
            _make_check(check_id="a", argv=["python3", "-m", "unittest", "a"], subject_task_id="LF-TSK-QLT-0009"),
            _make_check(check_id="b", argv=["python3", "-m", "unittest", "b"], subject_task_id="LF-TSK-QLT-0008"),
            _make_check(check_id="c", argv=["python3", "-m", "unittest", "c"], subject_task_id="LF-TSK-QLT-0002"),
        ]
        plan = _make_plan(checks=checks)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(len(result["checks"]), 3)
        self.assertEqual(result["checks"][0]["outcome"]["status"], "PASS")
        self.assertEqual(result["checks"][1]["outcome"]["status"], "FAIL")
        self.assertEqual(result["checks"][1]["outcome"]["reason"], "not-run")
        self.assertEqual(result["checks"][1]["process"]["exit_reason"], "NOT_RUN")
        self.assertEqual(result["checks"][2]["outcome"]["status"], "FAIL")
        self.assertEqual(result["checks"][2]["outcome"]["reason"], "not-run")

    def test_all_members_retained(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=0, stdout=b"Ran 1 test\n\nOK\n",
            argv=["python3", "-m", "unittest"], cwd="/repo",
        )
        checks = [
            _make_check(check_id=f"c{i}", subject_task_id=f"LF-TSK-QLT-000{min(i+2, 9)}")
            for i in range(5)
        ]
        for i, c in enumerate(checks):
            c["subject_task"]["task_id"] = f"LF-TSK-QLT-{9+i:04d}"
        plan = _make_plan(checks=checks)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(len(result["checks"]), 5)
        for check_result in result["checks"]:
            self.assertIn("process", check_result)
            self.assertIn("outcome", check_result)


class TestRequiredAdvisoryMatrix(unittest.TestCase):
    def test_required_and_advisory_both_participate(self):
        runner = FakeRunner()
        runner.results = {
            ("python3", "-m", "unittest", "req"): _make_process_result(return_code=0, stdout=b"Ran 1 test\n\nOK\n", argv=["python3", "-m", "unittest", "req"]),
            ("python3", "-m", "unittest", "adv"): _make_process_result(return_code=1, stdout=b"Ran 1 test\n\nFAILED (failures=1)\n", argv=["python3", "-m", "unittest", "adv"]),
        }
        checks = [
            _make_check(check_id="req", argv=["python3", "-m", "unittest", "req"], required=True, subject_task_id="LF-TSK-QLT-0009"),
            _make_check(check_id="adv", argv=["python3", "-m", "unittest", "adv"], required=False, subject_task_id="LF-TSK-QLT-0008"),
        ]
        plan = _make_plan(checks=checks)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(result["checks"][0]["required"], True)
        self.assertEqual(result["checks"][1]["required"], False)
        self.assertEqual(result["run_status"], "BLOCKED")

    def test_advisory_non_pass_not_hidden(self):
        runner = FakeRunner()
        runner.results = {
            ("python3", "-m", "unittest", "req"): _make_process_result(return_code=0, stdout=b"Ran 1 test\n\nOK\n", argv=["python3", "-m", "unittest", "req"]),
            ("python3", "-m", "unittest", "adv"): _make_process_result(return_code=0, stdout=b"no proof", argv=["python3", "-m", "unittest", "adv"]),
        }
        checks = [
            _make_check(check_id="req", argv=["python3", "-m", "unittest", "req"], required=True, subject_task_id="LF-TSK-QLT-0009"),
            _make_check(check_id="adv", argv=["python3", "-m", "unittest", "adv"], required=False, subject_task_id="LF-TSK-QLT-0008"),
        ]
        plan = _make_plan(checks=checks)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(result["checks"][1]["outcome"]["status"], "FAIL")
        self.assertEqual(result["run_status"], "FAIL")


class TestAggregation(unittest.TestCase):
    def test_fail_over_blocked_over_pass(self):
        checks = [
            {"outcome": {"status": "PASS", "reason": ""}},
            {"outcome": {"status": "BLOCKED", "reason": "assertion-failure"}},
            {"outcome": {"status": "FAIL", "reason": "timeout"}},
        ]
        status, reason = _aggregate(checks)
        self.assertEqual(status, "FAIL")
        self.assertEqual(reason, "timeout")

    def test_blocked_over_pass(self):
        checks = [
            {"outcome": {"status": "PASS", "reason": ""}},
            {"outcome": {"status": "BLOCKED", "reason": "assertion-failure"}},
        ]
        status, reason = _aggregate(checks)
        self.assertEqual(status, "BLOCKED")

    def test_all_pass(self):
        checks = [
            {"outcome": {"status": "PASS", "reason": ""}},
            {"outcome": {"status": "PASS", "reason": ""}},
        ]
        status, reason = _aggregate(checks)
        self.assertEqual(status, "PASS")

    def test_empty_is_fail(self):
        status, reason = _aggregate([])
        self.assertEqual(status, "FAIL")
        self.assertEqual(reason, "empty-checks")

    def test_unknown_status_is_fail(self):
        checks = [{"outcome": {"status": "UNKNOWN", "reason": ""}}]
        status, reason = _aggregate(checks)
        self.assertEqual(status, "FAIL")
        self.assertEqual(reason, "unknown-state")

    def test_full_cartesian_matrix(self):
        for s1 in ("PASS", "BLOCKED", "FAIL"):
            for s2 in ("PASS", "BLOCKED", "FAIL"):
                checks = [
                    {"outcome": {"status": s1, "reason": f"r-{s1}"}},
                    {"outcome": {"status": s2, "reason": f"r-{s2}"}},
                ]
                status, _ = _aggregate(checks)
                statuses = {s1, s2}
                if "FAIL" in statuses:
                    self.assertEqual(status, "FAIL", f"({s1}, {s2}) should be FAIL")
                elif "BLOCKED" in statuses:
                    self.assertEqual(status, "BLOCKED", f"({s1}, {s2}) should be BLOCKED")
                else:
                    self.assertEqual(status, "PASS", f"({s1}, {s2}) should be PASS")

    def test_single_fail_prevents_pass(self):
        checks = [
            {"outcome": {"status": "PASS", "reason": ""}},
            {"outcome": {"status": "PASS", "reason": ""}},
            {"outcome": {"status": "FAIL", "reason": "timeout"}},
        ]
        status, _ = _aggregate(checks)
        self.assertEqual(status, "FAIL")


class TestDefaultProcessRunner(unittest.TestCase):
    def test_real_profile_backed_task_contract_pass_and_user_gate_blocked(self):
        source = Path(__file__).resolve().parents[2]
        registry = yaml.safe_load((source / "harness/gate-check-registry.yaml").read_text())
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name)
        selected = {"LF-TSK-PRD-0001", "LF-TSK-ARCH-0008"}
        locators = {"scripts/gates/receipt_store.py"}
        for entry in registry["entries"]:
            if entry["subject_task_id"] in selected:
                locators.update(entry["consumed_inputs"])
        for locator in locators:
            target = repo / locator
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / locator, target)
        decision = repo / "docs/roadmap/phase-1-status.md"
        decision.write_text(decision.read_text().replace("G1 user decision: APPROVED", "G1 user decision: PENDING"))
        for task_id, expected in (
            ("LF-TSK-PRD-0001", "PASS"),
            ("LF-TSK-ARCH-0008", "BLOCKED"),
        ):
            with self.subTest(task_id=task_id):
                entry = next(item for item in registry["entries"] if item["subject_task_id"] == task_id)
                consumed = [
                    {"locator": locator, "state": "present", "sha256": _sha((repo / locator).read_bytes())}
                    for locator in entry["consumed_inputs"]
                ]
                check = _make_check(
                    check_id=entry["check_id"],
                    command_id=entry["command_id"],
                    argv=entry["fixed_argv"],
                    timeout=entry["timeout_seconds"],
                    consumed_inputs=consumed,
                    subject_task_id=task_id,
                    subject_task_version=entry["subject_task_version"],
                    subject_change_version=entry["subject_change_version"],
                    subject_owner=entry["owner"],
                )
                result = execute_checks(_make_plan(checks=[check]), repo_root=str(repo))
                self.assertEqual(result["run_status"], expected)
                self.assertEqual(result["checks"][0]["outcome"]["status"], expected)

    def test_real_subprocess_pass(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "test_pass.py"
            script.write_text(
                "import sys\n"
                "sys.stdout.write('Ran 2 tests in 0.001s\\n\\nOK\\n')\n"
            )
            request = ProcessRequest(
                argv=(sys.executable, str(script)),
                cwd=tmpdir,
                env=(("PYTHONDONTWRITEBYTECODE", "1"),),
                timeout_seconds=10,
                stdout_limit=DEFAULT_STDOUT_LIMIT,
                stderr_limit=DEFAULT_STDERR_LIMIT,
                executable=_python_binding(),
            )
            result = default_process_runner(request)

        self.assertEqual(result.exit_reason, "EXITED")
        self.assertEqual(result.return_code, 0)
        self.assertIn(b"OK", result.stdout)

    def test_real_subprocess_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "test_fail.py"
            script.write_text(
                "import sys\n"
                "sys.stdout.write('Ran 2 tests in 0.001s\\n\\nFAILED (failures=1)\\n')\n"
                "sys.exit(1)\n"
            )
            request = ProcessRequest(
                argv=(sys.executable, str(script)),
                cwd=tmpdir,
                env=(("PYTHONDONTWRITEBYTECODE", "1"),),
                timeout_seconds=10,
                stdout_limit=DEFAULT_STDOUT_LIMIT,
                stderr_limit=DEFAULT_STDERR_LIMIT,
                executable=_python_binding(),
            )
            result = default_process_runner(request)

        self.assertEqual(result.return_code, 1)
        self.assertIn(b"FAILED", result.stdout)

    def test_real_subprocess_timeout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "slow.py"
            script.write_text("import time\ntime.sleep(30)\n")
            request = ProcessRequest(
                argv=(sys.executable, str(script)),
                cwd=tmpdir,
                env=(),
                timeout_seconds=1,
                stdout_limit=DEFAULT_STDOUT_LIMIT,
                stderr_limit=DEFAULT_STDERR_LIMIT,
                executable=_python_binding(),
            )
            result = default_process_runner(request)

        self.assertEqual(result.exit_reason, "TIMEOUT")
        self.assertIsNotNone(result.return_code)
        self.assertNotEqual(result.return_code, 0)
        self.assertIsNotNone(result.signal_number)

    def test_real_subprocess_missing_executable(self):
        request = ProcessRequest(
            argv=("nonexistent_binary_xyz_12345",),
            cwd="/tmp",
            env=(),
            timeout_seconds=5,
            stdout_limit=DEFAULT_STDOUT_LIMIT,
            stderr_limit=DEFAULT_STDERR_LIMIT,
            executable=ExecutableBinding(
                locator="/tmp/nonexistent_binary_xyz_12345", sha256="0" * 64,
                device=0, inode=0, size=0,
            ),
        )
        result = default_process_runner(request)
        self.assertEqual(result.exit_reason, "SPAWN_ERROR")

    def test_real_subprocess_output_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "big.py"
            script.write_text("import sys\nfor i in range(100):\n    sys.stdout.write('x' * 1000 + '\\n')\n")
            request = ProcessRequest(
                argv=(sys.executable, str(script)),
                cwd=tmpdir,
                env=(),
                timeout_seconds=10,
                stdout_limit=100,
                stderr_limit=100,
                executable=_python_binding(),
            )
            result = default_process_runner(request)

        self.assertEqual(result.exit_reason, "OUTPUT_LIMIT")

    def test_ambient_path_cannot_substitute_registered_python(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_python = fake_bin / "python3"
            fake_python.write_text("#!/bin/sh\nprintf 'Ran 999 tests\\n\\nOK\\n'\n")
            fake_python.chmod(0o755)
            fake_python_sha256 = _sha(fake_python.read_bytes())
            (root / "test_probe.py").write_text(
                "import unittest\n"
                "class Probe(unittest.TestCase):\n"
                "    def test_real_interpreter(self): self.assertTrue(True)\n"
            )
            check = _make_check(
                argv=["python3", "-m", "unittest", "discover", "-s", ".", "-p", "test_probe.py"]
            )
            with mock.patch.dict(os.environ, {"PATH": str(fake_bin)}, clear=False):
                result = execute_checks(_make_plan(checks=[check]), repo_root=tmpdir)

        process = result["checks"][0]["process"]
        self.assertEqual(result["run_status"], "PASS")
        self.assertEqual(result["checks"][0]["outcome"]["assertions"], 1)
        self.assertEqual(process["executable_locator"], os.path.realpath(sys.executable))
        self.assertNotEqual(process["executable_sha256"], fake_python_sha256)
        self.assertNotIn("PATH", _build_child_environment())

    def test_registered_executable_binding_rejects_non_python_command(self):
        with self.assertRaisesRegex(Exception, "invalid-executable"):
            _resolve_registered_executable("sh")


class TestSchemaAndSerialization(unittest.TestCase):
    def test_output_schema_is_correct(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=0, stdout=b"Ran 1 test\n\nOK\n",
            argv=["python3", "-m", "unittest"], cwd="/repo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(_make_plan(), repo_root=tmpdir, process_runner=runner)

        self.assertEqual(result["schema_version"], OUTCOME_SCHEMA)
        self.assertIn("plan_content_fingerprint", result)
        self.assertIn("started_at", result)
        self.assertIn("finished_at", result)
        self.assertIn("duration_seconds", result)
        self.assertIn("checks", result)
        self.assertIn("run_status", result)
        self.assertIn("run_reason", result)
        self.assertIn("aggregation", result)

    def test_deterministic_serialization(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=0, stdout=b"Ran 1 test\n\nOK\n",
            argv=["python3", "-m", "unittest"], cwd="/repo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            r1 = execute_checks(_make_plan(), repo_root=tmpdir, process_runner=runner)
            r2 = execute_checks(_make_plan(), repo_root=tmpdir, process_runner=runner)

        s1 = json.dumps(r1, sort_keys=True)
        s2 = json.dumps(r2, sort_keys=True)
        for key in ("run_status", "run_reason", "aggregation"):
            self.assertEqual(r1[key], r2[key])

    def test_injected_time_sources_are_used(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=0, stdout=b"Ran 1 test\nOK\n",
            argv=["python3", "-m", "unittest"], cwd="/repo",
        )
        times = iter((10.0, 12.5))
        stamps = iter(("2026-09-16T00:00:00.000Z", "2026-09-16T00:00:02.500Z"))
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(
                _make_plan(), repo_root=tmpdir, process_runner=runner,
                clock=lambda: next(times), now=lambda: next(stamps),
            )
        self.assertEqual(result["started_at"], "2026-09-16T00:00:00.000Z")
        self.assertEqual(result["finished_at"], "2026-09-16T00:00:02.500Z")
        self.assertEqual(result["duration_seconds"], 2.5)

    def test_aggregation_counts_correct(self):
        runner = FakeRunner()
        runner.results = {
            ("python3", "-m", "unittest", "a"): _make_process_result(return_code=0, stdout=b"Ran 1 test\n\nOK\n", argv=["python3", "-m", "unittest", "a"]),
            ("python3", "-m", "unittest", "b"): _make_process_result(return_code=1, stdout=b"Ran 1 test\n\nFAILED (failures=1)\n", argv=["python3", "-m", "unittest", "b"]),
        }
        checks = [
            _make_check(check_id="a", argv=["python3", "-m", "unittest", "a"], required=True, subject_task_id="LF-TSK-QLT-0009"),
            _make_check(check_id="b", argv=["python3", "-m", "unittest", "b"], required=True, subject_task_id="LF-TSK-QLT-0008"),
        ]
        plan = _make_plan(checks=checks)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        agg = result["aggregation"]
        self.assertEqual(agg["total"], 2)
        self.assertEqual(agg["passed"], 1)
        self.assertEqual(agg["blocked"], 1)
        self.assertEqual(agg["failed"], 0)
        self.assertEqual(agg["required_total"], 2)
        self.assertEqual(agg["required_passed"], 1)


class TestCwdResolution(unittest.TestCase):
    def test_dot_resolves_to_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved = _resolve_cwd(tmpdir, ".")
            self.assertEqual(resolved, os.path.abspath(tmpdir))

    def test_relative_subdir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = Path(tmpdir) / "sub"
            sub.mkdir()
            resolved = _resolve_cwd(tmpdir, "sub")
            self.assertEqual(resolved, str(sub))

    def test_absolute_rejected(self):
        with self.assertRaises(Exception):
            _resolve_cwd("/tmp", "/absolute")

    def test_escaping_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(Exception):
                _resolve_cwd(tmpdir, "../../etc")


class TestSafeFileRead(unittest.TestCase):
    def test_reads_regular_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "test.txt").write_bytes(b"hello")
            content = _safe_read_file(tmpdir, "test.txt")
            self.assertEqual(content, b"hello")

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "target.txt"
            target.write_bytes(b"data")
            link = Path(tmpdir) / "link.txt"
            link.symlink_to(target)
            with self.assertRaises(Exception) as ctx:
                _safe_read_file(tmpdir, "link.txt")
            self.assertIn("symlink", str(ctx.exception).lower())

    def test_rejects_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(Exception):
                _safe_read_file(tmpdir, "nonexistent.txt")

    def test_reads_nested_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = Path(tmpdir) / "a" / "b"
            sub.mkdir(parents=True)
            (sub / "file.txt").write_bytes(b"nested")
            content = _safe_read_file(tmpdir, "a/b/file.txt")
            self.assertEqual(content, b"nested")


class TestNoLifecycleOrReceipt(unittest.TestCase):
    def test_no_receipt_created(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=0, stdout=b"Ran 1 test\n\nOK\n",
            argv=["python3", "-m", "unittest"], cwd="/repo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            before = set(Path(tmpdir).rglob("*"))
            execute_checks(_make_plan(), repo_root=tmpdir, process_runner=runner)
            after = set(Path(tmpdir).rglob("*"))

        self.assertEqual(before, after)

    def test_no_run_id_generated(self):
        runner = FakeRunner()
        runner.default_result = _make_process_result(
            return_code=0, stdout=b"Ran 1 test\n\nOK\n",
            argv=["python3", "-m", "unittest"], cwd="/repo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_checks(_make_plan(), repo_root=tmpdir, process_runner=runner)

        self.assertNotIn("run_id", result)


class TestOverrideRejection(unittest.TestCase):
    def test_no_caller_argv_override(self):
        runner = FakeRunner()
        frozen_argv = ["python3", "-m", "unittest", "discover"]
        check = _make_check(argv=frozen_argv)
        plan = _make_plan(checks=[check])
        with tempfile.TemporaryDirectory() as tmpdir:
            execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(list(runner.calls[0].argv), frozen_argv)

    def test_no_caller_cwd_override(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            check = _make_check(cwd=".")
            plan = _make_plan(checks=[check])
            execute_checks(plan, repo_root=tmpdir, process_runner=runner)

        self.assertEqual(runner.calls[0].cwd, os.path.abspath(tmpdir))


if __name__ == "__main__":
    unittest.main()
