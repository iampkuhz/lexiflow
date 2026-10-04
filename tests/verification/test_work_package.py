"""复用内核的工作包诊断、预算和输出合同回归，不签发独立验收。"""

from __future__ import annotations

import copy
import json
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.verification.execution import Interrupted, bounded
from scripts.verification.reports import read_report
from scripts.verification.summaries import selected_result, summarize
from scripts.verification.work_package import execute, validate_request
from tests.verification.test_scenarios import _write_declarations


REQUEST = {
    "schema_version": "lexiflow.verification-work-package.v1",
    "work_package_id": "AQW-EXECUTION",
    "check_ids": ["fixture.check"],
    "budget_seconds": 10,
    "execution_mode": "delivery",
}


def fixture(root, code="print('fixture')"):
    _write_declarations(root, [{
        "check_id": "fixture.check", "module": "fixture",
        "command": [sys.executable, "-c", code], "cwd": ".",
        "timeout_seconds": 5, "scope": "repository-baseline", "triggers": [],
    }])


class WorkPackageTests(unittest.TestCase):
    def test_rejects_unbounded_config_and_injected_identity(self):
        for key, value in (("budget_seconds", True), ("budget_seconds", 7201),
                           ("check_ids", []), ("check_ids", ["x", "x"]),
                           ("agent_id", "fake"), ("command", ["anything"])):
            request = {**REQUEST, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_request(request)

    def test_fixed_kernel_logs_and_diagnostic_cannot_be_read_as_formal_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            result = execute(root, REQUEST)
            self.assertEqual(result["selected_result"], "PASS")
            self.assertFalse(result["formal_acceptance"])
            self.assertNotIn("schema_version", result["selected_report"])
            self.assertNotIn("result", result["selected_report"])
            self.assertEqual(result["summary"]["commands_executed"], 1)
            output = result["selected_report"]["checks"][0]["process"]["output_artifacts"]["stdout"]
            self.assertEqual((root / output["locator"]).read_text(), "fixture\n")
            saved = json.loads((root / result["diagnostic_artifact"]["locator"]).read_text())
            self.assertEqual(saved["selected_result"], "PASS")
            with self.assertRaises(ValueError):
                read_report(root, result["diagnostic_id"])

    def test_partial_success_is_only_selected_success_not_full_qualification(self):
        import yaml
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            path = root / "harness/module-checks.yaml"
            declaration = yaml.safe_load(path.read_text())
            other = copy.deepcopy(declaration["checks"][0])
            other["check_id"] = "other.check"
            declaration["checks"].append(other)
            path.write_text(yaml.safe_dump(declaration))
            result = execute(root, REQUEST)
            self.assertEqual(result["selected_result"], "PASS")
            self.assertEqual(result["qualification_result"], "BLOCKED")
            self.assertEqual(result["qualification_reason"], "partial-check-selection")
            self.assertFalse(result["formal_acceptance"])
            self.assertFalse(result["full_repository_executed"])
            self.assertEqual(result["summary"]["commands_executed"], 1)

    def test_unsafe_output_directory_does_not_write_outside_repository(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            root = Path(directory)
            fixture(root)
            (root / 'tmp/quality').mkdir(parents=True)
            (root / 'tmp/quality/diagnostics').symlink_to(other, target_is_directory=True)
            with self.assertRaises(ValueError):
                execute(root, REQUEST)
            self.assertEqual(list(Path(other).iterdir()), [])

    def test_failure_and_unknown_check_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root, "raise AssertionError('business fixture')")
            result = execute(root, REQUEST)
            self.assertEqual(result["selected_result"], "FAIL")
            self.assertEqual(result["summary"]["findings"][0]["failure_layer"], "check-needs-triage")
            unknown = execute(root, {**REQUEST, "check_ids": ["missing"]})
            self.assertEqual(unknown["selected_result"], "FAIL")

    def test_total_budget_interrupts_and_preserves_nonpass_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root, "import time; time.sleep(10)")
            started = time.monotonic()
            result = execute(root, {**REQUEST, "budget_seconds": 1})
            self.assertIn(result["selected_result"], {"FAIL", "BLOCKED"})
            self.assertLess(time.monotonic() - started, 4)
            self.assertTrue((root / result["diagnostic_artifact"]["locator"]).is_file())

    def test_inner_timeout_is_capped_by_remaining_whole_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            from scripts.verification.kernel import run_check_process
            with patch("scripts.verification.work_package.run_check_process", wraps=run_check_process) as run:
                execute(root, {**REQUEST, "budget_seconds": 2})
            self.assertGreater(run.call_args.args[3], 0)
            self.assertLessEqual(run.call_args.args[3], 2)


class ExecutionBudgetTests(unittest.TestCase):
    def test_restore_handlers_and_reject_nested_budget(self):
        original = signal.getsignal(signal.SIGTERM)
        with bounded(10):
            with self.assertRaises(ValueError):
                with bounded(2):
                    self.fail("nested budget replaced outer timer")
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)
        self.assertEqual(signal.getsignal(signal.SIGTERM), original)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_cancellation_is_not_ordinary_exception(self):
        with self.assertRaises(Interrupted):
            with bounded(10):
                signal.raise_signal(signal.SIGTERM)


class SummaryTests(unittest.TestCase):
    def test_partial_selection_cannot_hide_missing_failed_or_empty_checks(self):
        original = {"result": "BLOCKED", "reason": "partial-check-selection",
                    "checks": [], "scope_review": {"checks_selected": 1}}
        self.assertEqual(selected_result(original), "BLOCKED")
        for status, exit_reason in (("FAIL", "exited"), ("BLOCKED", "not-run"), ("PASS", "not-run")):
            report = {**original, "checks": [{"status": status, "process": {"exit_reason": exit_reason}}]}
            self.assertNotEqual(selected_result(report), "PASS")
        self.assertNotEqual(selected_result({**original, "scope_review": {"checks_selected": 0}}), "PASS")

    def test_alias_duration_counted_once_and_unknown_metrics_stay_unknown(self):
        original = {"check_id": "first", "status": "PASS", "process": {
            "exit_reason": "exited", "executed_argv": ["check"], "duration_seconds": 2.5}}
        alias = copy.deepcopy(original)
        alias["process"].update(exit_reason="deduplicated", executed_argv=[])
        result = summarize({"checks": [original, alias]})
        self.assertEqual(result["command_seconds"], 2.5)
        self.assertEqual(result["commands_executed"], 1)
        self.assertEqual(result["checks_reused"], 1)
        self.assertIsNone(result["token_usage"])


if __name__ == "__main__":
    unittest.main()
