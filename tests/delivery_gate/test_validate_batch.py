"""有界批量验证、独立身份和同事务证据回归。"""

from __future__ import annotations

import unittest
from unittest.mock import patch
import yaml

from scripts.delivery_gate.validate import (
    ValidationError,
    _bind_terminal_report,
    validate,
    validate_batch,
)
from scripts.delivery_gate.records import (
    canonical_bytes,
    load_layer,
    load_submission,
    parse_json,
    publish_bytes,
    read_bound_bytes,
)
from scripts.delivery_gate.acceptance import verify_validation_evidence
from scripts.verification.reports import validate_report
from scripts.verification.scenarios import _error_report
from tests.delivery_gate.fixtures import (
    DeliveryGateFixture,
    VALIDATOR_SESSION,
    make_mock_runtime,
)


class ReusableDeliveryGateFixture(DeliveryGateFixture):
    def _write_declarations(self):
        super()._write_declarations()
        declarations = yaml.safe_load(
            (self.root / "harness/module-checks.yaml").read_text()
        )
        next(
            check
            for check in declarations["checks"]
            if check["check_id"] == "fixture.change"
        )["transaction_reuse"] = True
        (self.root / "harness/module-checks.yaml").write_text(
            yaml.safe_dump(declarations, sort_keys=False)
        )


class ValidateBatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ReusableDeliveryGateFixture()
        self.first = self.fixture.create_submission()["submission_id"]
        self.second = self.fixture.create_submission()["submission_id"]
        self.third = self.fixture.create_submission()["submission_id"]

    def tearDown(self):
        self.fixture.cleanup()

    def test_cli_aggregate_failure_has_priority_over_blocked(self):
        from argparse import Namespace
        from scripts.delivery_gate.__main__ import _cmd_validate
        with patch("scripts.delivery_gate.validate.validate_batch", return_value=[
            {"result": "BLOCKED"}, {"result": "FAIL"}
        ]), patch("scripts.delivery_gate.__main__._output") as output:
            _cmd_validate(Namespace(repo_root=self.fixture.root, submission_id=["a", "b"]))
        self.assertEqual("FAIL", output.call_args.args[0]["result"])

    def test_bounds_and_duplicates_rejected_before_execution(self):
        for ids in ([], ["x"] * 17, ["x", "x"], [None]):
            with self.subTest(ids=ids), self.assertRaises(ValidationError):
                validate_batch(self.fixture.root, submission_ids=ids)

    @patch("scripts.agents.local_codex_runtime.discover")
    def test_one_non_independent_submission_blocks_entire_batch_before_checks(
        self, discover
    ):
        discover.return_value = make_mock_runtime(VALIDATOR_SESSION)
        from tests.delivery_gate import fixtures as fixture_module

        with patch.object(fixture_module, "PRODUCER_SESSION", VALIDATOR_SESSION):
            invalid = self.fixture.create_submission()["submission_id"]
        with patch(
            "scripts.verification.scenarios.execute_single_check",
            side_effect=AssertionError("business check must not start"),
        ):
            with self.assertRaisesRegex(ValidationError, "self-validation-forbidden"):
                validate_batch(self.fixture.root, submission_ids=[self.first, invalid])
        self.assertFalse(
            list(
                (self.fixture.root / "tmp/quality/delivery-gate/validations").glob(
                    "*.json"
                )
            )
        )

    def test_unrun_terminal_profile_is_bound_as_blocked_not_pass(self):
        submission = load_submission(self.fixture.root, self.first)
        freeze = submission["verification_freeze"]
        report = _error_report(
            "BLOCKED",
            "prior-profile-not-run",
            "earlier profile failed or was blocked",
            "99999999-9999-4999-8999-999999999999",
            scope=freeze["verification_scope"],
        )
        report = _bind_terminal_report(report, freeze)
        self.assertEqual(report["result"], "BLOCKED")
        self.assertEqual(
            report["frozen_input_fingerprint"], freeze["input_fingerprint"]
        )
        self.assertEqual(
            [check["process"]["exit_reason"] for check in report["checks"]],
            ["not-run"] * len(freeze["checks"]),
        )
        self.assertTrue(validate_report(self.fixture.root, report))
        descriptor = publish_bytes(
            self.fixture.root,
            "tmp/quality/delivery-gate/validations/terminal-profile/report.json",
            canonical_bytes(report),
        )
        verify_validation_evidence(
            self.fixture.root,
            submission,
            {"verification_report": descriptor, "result": "BLOCKED", "gaps": []},
        )

    @patch("scripts.agents.local_codex_runtime.discover")
    def test_first_failed_profile_stops_remaining_task_without_pass(self, discover):
        discover.return_value = make_mock_runtime(VALIDATOR_SESSION)
        import scripts.verification.scenarios as scenarios

        original = scenarios.execute_single_check
        executed = []

        def fail_first(check, *args, **kwargs):
            executed.append(check["check_id"])
            result = original(check, *args, **kwargs)
            result["status"] = "FAIL"
            result["reason"] = "fixture-forced-failure"
            return result

        with patch.object(scenarios, "execute_single_check", side_effect=fail_first):
            results = validate_batch(
                self.fixture.root, submission_ids=[self.first, self.second]
            )
        self.assertEqual(len(executed), 1)
        self.assertEqual([item["result"] for item in results], ["FAIL", "BLOCKED"])
        records = [
            load_layer(self.fixture.root, "validations", item["validation_id"])
            for item in results
        ]
        reports = [
            parse_json(
                read_bound_bytes(
                    self.fixture.root,
                    record["verification_report"]["locator"],
                    record["verification_report"]["sha256"],
                ),
                "validation report",
            )
            for record in records
        ]
        self.assertEqual(reports[1]["result"], "BLOCKED")
        self.assertTrue(
            all(check["status"] != "PASS" for check in reports[1]["checks"])
        )
        self.assertTrue(
            all(
                check["process"]["exit_reason"] == "not-run"
                for check in reports[1]["checks"]
            )
        )

    @patch("scripts.agents.local_codex_runtime.discover")
    def test_subject_drift_between_tasks_blocks_later_execution_and_pass(
        self, discover
    ):
        discover.return_value = make_mock_runtime(VALIDATOR_SESSION)
        import importlib

        validate_module = importlib.import_module("scripts.delivery_gate.validate")
        import scripts.verification.scenarios as scenarios

        original_verify_profiles = validate_module.verify_profiles
        original_execute = scenarios.execute_single_check
        executed = []

        def count_execute(*args, **kwargs):
            executed.append(args[0]["check_id"])
            return original_execute(*args, **kwargs)

        def introduce_drift(*args, **kwargs):
            original_after = kwargs["after_profile"]

            def after_profile(index, profile, report):
                result = original_after(index, profile, report)
                if index == 0:
                    (self.fixture.root / "src/test.py").write_text(
                        "drift after task one\n"
                    )
                return result

            kwargs["after_profile"] = after_profile
            return original_verify_profiles(*args, **kwargs)

        with (
            patch.object(scenarios, "execute_single_check", side_effect=count_execute),
            patch.object(
                validate_module, "verify_profiles", side_effect=introduce_drift
            ),
        ):
            results = validate_batch(
                self.fixture.root, submission_ids=[self.first, self.second]
            )
        self.assertTrue(results)
        self.assertTrue(all(item["result"] != "PASS" for item in results))
        records = [
            load_layer(self.fixture.root, "validations", item["validation_id"])
            for item in results
        ]
        reports = [
            parse_json(
                read_bound_bytes(
                    self.fixture.root,
                    record["verification_report"]["locator"],
                    record["verification_report"]["sha256"],
                ),
                "validation report",
            )
            for record in records
        ]
        self.assertEqual(
            len(executed),
            sum(
                check["process"]["exit_reason"] != "not-run"
                for check in reports[0]["checks"]
            ),
        )
        self.assertTrue(all(report["result"] != "PASS" for report in reports))
        self.assertTrue(
            all(
                check["process"]["exit_reason"] == "not-run"
                for check in reports[1]["checks"]
            )
        )

    @patch("scripts.agents.local_codex_runtime.discover")
    def test_two_submissions_get_distinct_records_and_repeat_is_rejected(
        self, discover
    ):
        discover.return_value = make_mock_runtime(VALIDATOR_SESSION)
        results = validate_batch(
            self.fixture.root, submission_ids=[self.first, self.second]
        )
        self.assertEqual(
            [x["submission_id"] for x in results], [self.first, self.second]
        )
        self.assertNotEqual(results[0]["validation_id"], results[1]["validation_id"])
        records = [
            load_layer(self.fixture.root, "validations", item["validation_id"])
            for item in results
        ]
        reports = [
            parse_json(
                read_bound_bytes(
                    self.fixture.root,
                    rec["verification_report"]["locator"],
                    rec["verification_report"]["sha256"],
                ),
                "validation report",
            )
            for rec in records
        ]
        self.assertNotEqual(reports[0]["run_id"], reports[1]["run_id"])
        self.assertTrue(
            all(validate_report(self.fixture.root, report) for report in reports)
        )
        self.assertTrue(
            any(
                check["process"]["exit_reason"] == "exited"
                for check in reports[0]["checks"]
            )
        )
        self.assertEqual(
            next(
                check
                for check in reports[1]["checks"]
                if check["check_id"] == "fixture.change"
            )["process"]["exit_reason"],
            "deduplicated",
        )
        self.assertEqual(
            next(
                check
                for check in reports[1]["checks"]
                if check["check_id"] == "fixture.baseline"
            )["process"]["exit_reason"],
            "exited",
        )
        with self.assertRaisesRegex(ValidationError, "validation-already-published"):
            validate_batch(self.fixture.root, submission_ids=[self.first, self.second])
        # 新 API 调用取得新事务；字节相同的检查也必须重新实际执行。
        rerun = validate(self.fixture.root, submission_id=self.third)
        self.assertIsInstance(rerun, dict)
        self.assertEqual(rerun["submission_id"], self.third)
        rerun_record = load_layer(
            self.fixture.root, "validations", rerun["validation_id"]
        )
        rerun_report = parse_json(
            read_bound_bytes(
                self.fixture.root,
                rerun_record["verification_report"]["locator"],
                rerun_record["verification_report"]["sha256"],
            ),
            "validation report",
        )
        self.assertNotEqual(reports[0]["run_id"], rerun_report["run_id"])
        self.assertTrue(
            all(
                check["process"]["exit_reason"] == "exited"
                for check in rerun_report["checks"]
            )
        )


if __name__ == "__main__":
    unittest.main()
