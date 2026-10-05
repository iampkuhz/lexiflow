"""结构化摘要只转述有证据的失败层和位置，不猜测产品或夹具责任。"""

from __future__ import annotations

import unittest

from scripts.verification.summaries import failure_layer, summarize


class SummaryLocationTest(unittest.TestCase):
    def test_known_layers_and_ambiguous_assertions(self):
        for reason, expected in (
            ("input-drift", "evidence"),
            ("environment-drift", "evidence"),
            ("result-report-incomplete", "evidence"),
            ("validator-authority-invalid", "evidence"),
            ("missing-environment", "environment"),
            ("timeout", "executor"),
            ("user-approval-missing", "authorization"),
            ("python-docstrings-failed", "static-check"),
            ("documentation-governance-failed", "static-check"),
            ("prior-check-failed", "prerequisite"),
            ("non-zero-exit", "check-needs-triage"),
            ("fixture failed product defect", "check-needs-triage"),
        ):
            with self.subTest(reason=reason):
                self.assertEqual(expected, failure_layer({"status": "FAIL", "reason": reason}))

    def test_adapter_test_ids_and_counts_are_not_lost_or_guessed(self):
        summary = summarize({"result": "FAIL", "reason": "tests-failed", "checks": [{
            "check_id": "fixture.test", "status": "FAIL", "reason": "tests-failed",
            "process": {"output_artifacts": {"stderr": {"locator": "tmp/test.log"}}},
            "result_contract": {"report": {
                "checks_run": 2, "failures": 1, "errors": 1, "skipped": 0,
                "detail": {"failed_tests": ["test.product_case"], "error_tests": ["test.setup_case"]},
            }},
        }]})
        finding = summary["findings"][0]
        self.assertEqual(["test.product_case"], finding["failed_tests"])
        self.assertEqual(["test.setup_case"], finding["errored_tests"])
        self.assertEqual(2, finding["reported_counts"]["checks_run"])
        self.assertEqual("check-needs-triage", finding["failure_layer"])
        self.assertFalse(finding["automatic_redispatch"])
        self.assertEqual("locate-root-cause-before-redispatch", finding["next_action"])
        self.assertEqual("tmp/test.log", finding["artifacts"]["stderr"]["locator"])

    def test_python_aggregate_preserves_layer_and_bounded_positions_only(self):
        check = {
            "status": "FAIL", "reason": "python-quality-incomplete",
            "result_contract": {"report": {"detail": {
                "ruff": {"status": "PASS"},
                "docstrings": {"status": "FAIL", "reason": "python-docstrings-failed", "detail": [
                    {"path": "scripts/example.py", "line": i, "message-id": "C9001",
                     "message": "raw business text must not be copied"} for i in range(40)
                ]},
            }}},
        }
        self.assertEqual("static-check", failure_layer(check))
        finding = summarize({"checks": [check]})["findings"][0]
        self.assertEqual(20, len(finding["locations"]))
        self.assertEqual(0, finding["locations"][0]["line"])
        self.assertNotIn("message", finding["locations"][0])
        self.assertIsNone(finding["reported_counts"]["checks_run"])

    def test_partial_qualification_is_not_a_fake_report_failure(self):
        report = {
            "result": "BLOCKED", "reason": "partial-check-selection",
            "scope_review": {"checks_selected": 1},
            "checks": [{"check_id": "static", "status": "PASS", "process": {"exit_reason": "exited"}}],
        }
        summary = summarize(report)
        self.assertIsNone(summary["report_failure"])
        self.assertEqual({"result": "BLOCKED", "reason": "partial-check-selection"}, summary["qualification"])
        self.assertEqual([], summary["findings"])
        report["checks"][0].update(status="FAIL", reason="non-zero-exit")
        summary = summarize(report)
        self.assertIsNone(summary["report_failure"])
        self.assertEqual("check-needs-triage", summary["findings"][0]["failure_layer"])

    def test_report_preflight_failure_has_reason_without_fake_test_failure(self):
        summary = summarize({"result": "FAIL", "reason": "frozen-input-mismatch", "checks": []})
        self.assertEqual("evidence", summary["report_failure"]["failure_layer"])
        self.assertEqual([], summary["findings"])
        self.assertEqual(0, summary["commands_executed"])


if __name__ == "__main__":
    unittest.main()
