"""依赖 receipt 历史恢复和 fail-closed 回归。"""

from __future__ import annotations

import json
import unittest
import uuid
from unittest.mock import patch

import yaml

from scripts.delivery_gate.check import check_conditions
from scripts.delivery_gate.source_snapshot import review_patch
from scripts.delivery_gate.records import (
    RecordError,
    content_hash,
    delivery_gate_locator,
    load_submission,
    publish_json,
)
from tests.delivery_gate.fixtures import TASK_ID, DeliveryGateFixture


class TestDependencyRecovery(unittest.TestCase):
    """验证旧历史不遮蔽唯一成功链，receipt 异常仍阻断。"""

    def setUp(self) -> None:
        self.f = DeliveryGateFixture()
        path = self.f.root / "planning/workstreams.yaml"
        catalog = yaml.safe_load(path.read_text())
        tasks = catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"]
        tasks[0]["dependencies"] = []
        tasks.append({**tasks[0], "id": "LF-TSK-TEST-0002", "dependencies": [TASK_ID]})
        path.write_text(yaml.safe_dump(catalog, sort_keys=False))
        self.f._git("add", "planning/workstreams.yaml")
        self.f._git("commit", "-m", "dependency recovery fixture catalog")

    def tearDown(self) -> None:
        self.f.cleanup()

    def _submit(self, task_id: str = TASK_ID, report_result: str = "PASS") -> dict:
        self.f.create_verification_report(result=report_result)
        if task_id == TASK_ID:
            raw = self.f.create_submission()
            return load_submission(self.f.root, raw["submission_id"])
        return self._dependent_submission()

    def _dependent_submission(self) -> dict:
        """Build a fixture submission with the task-2 requirements snapshot."""
        from scripts.delivery_gate.records import publish_bytes, sha256_bytes
        from scripts.delivery_gate.requirements import load_task_requirements
        from scripts.verification import freeze_inputs, read_report

        base = self.f.create_submission()
        requirements = load_task_requirements(self.f.root, "LF-TSK-TEST-0002")
        sid = str(uuid.uuid4())
        report, descriptor = read_report(self.f.root, self.f.report_id)
        diff = publish_bytes(
            self.f.root,
            f"tmp/quality/delivery-gate/submissions/{sid}.diff.patch",
            review_patch(
                self.f.root,
                base["risk_assessment"]["base"],
                report["scope_review"]["changed_files"],
            ),
        )
        snapshots = {
            name: {
                "state": "present",
                "sha256": sha256_bytes((self.f.root / name).read_bytes()),
            }
            for name in report["scope_review"]["changed_files"]
        }
        from scripts.delivery_gate.acceptance import acceptance_plan

        plan = acceptance_plan(self.f.root, base["risk_assessment"], requirements)
        record = {
            **base,
            "acceptance_plan": plan,
            "submission_id": sid,
            "task_requirements": requirements,
            "change_report": descriptor,
            "verification_freeze": freeze_inputs(
                self.f.root,
                required_check_ids=requirements["required_check_ids"],
                verification_scope=plan["verification_scope"],
                base=base["risk_assessment"]["base"],
            ),
            "changed_file_snapshots": snapshots,
            "diff": diff,
        }
        record.pop("content_hash", None)
        publish_json(
            self.f.root,
            delivery_gate_locator("submissions", sid),
            record,
        )
        return load_submission(self.f.root, sid)

    def _complete(self, result: str = "PASS") -> dict:
        submission = self._submit()
        self.f.create_validation(submission["submission_id"], result=result)
        self.f.create_review(
            submission["submission_id"],
            decision="PASS" if result == "PASS" else "FAIL",
        )
        if result == "PASS":
            outcome = check_conditions(
                self.f.root, submission_id=submission["submission_id"]
            )
            self.assertEqual(outcome["result"], "PASS", outcome)
        return submission

    def _dependent_with_receipts(self) -> dict:
        dependent = self._submit("LF-TSK-TEST-0002")
        validation = self.f.create_validation(dependent["submission_id"])
        self.f.create_review(dependent["submission_id"], validation["validation_id"])
        return dependent

    def _check_dependent(self) -> dict:
        dependent = self._dependent_with_receipts()
        return check_conditions(self.f.root, submission_id=dependent["submission_id"])

    def _mutate(self, kind: str, record_id: str, change) -> dict:
        path = self.f.root / delivery_gate_locator(kind, record_id)
        value = json.loads(path.read_text())
        change(value)
        value["content_hash"] = content_hash(value)
        path.chmod(0o600)
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
        path.chmod(0o400)
        return value

    def test_old_failed_and_unfinished_submissions_do_not_hide_unique_pass(
        self,
    ) -> None:
        old = self._submit()
        self.f.create_validation(old["submission_id"], result="FAIL")
        # 另一个历史 submission 尚未验证，缺失 receipt 合法且不遮蔽成功链。
        self._submit()
        blocked = self._submit()
        self.f.create_validation(blocked["submission_id"], result="BLOCKED")
        reviewed = self._submit()
        self.f.create_validation(reviewed["submission_id"])
        self.f.create_review(reviewed["submission_id"], decision="BLOCKED")
        passed = self._complete()
        outcome = self._check_dependent()
        self.assertEqual(outcome["result"], "PASS", outcome)
        receipt = outcome["conditions"]["dependency_receipts"][0]
        self.assertEqual(receipt["submission_content_hash"], passed["content_hash"])

    def test_zero_or_two_pass_chains_fail_closed(self) -> None:
        self._complete("FAIL")
        zero = self._check_dependent()
        self.assertEqual(zero["result"], "BLOCKED")
        self.assertEqual(
            zero["conditions"]["dependency_details"][0]["status"], "not-pass"
        )

        self._complete()
        two = self._complete()
        ambiguous = self._check_dependent()
        self.assertEqual(ambiguous["result"], "BLOCKED")
        self.assertEqual(
            ambiguous["conditions"]["dependency_details"][0]["status"], "ambiguous"
        )
        self.assertIsNotNone(two)

    def test_corrupt_submission_blocks_before_candidate_filtering(self) -> None:
        self._complete()
        dependent = self._dependent_with_receipts()
        from scripts.delivery_gate.records import list_submissions

        source = next(
            record
            for record in list_submissions(self.f.root)
            if record["task_requirements"]["task_id"] == TASK_ID
        )
        first = self.f.root / delivery_gate_locator(
            "submissions", source["submission_id"]
        )
        first.chmod(0o600)
        first.write_text("{broken")
        first.chmod(0o400)
        result = check_conditions(self.f.root, submission_id=dependent["submission_id"])
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["reason"], "dependency-record-invalid")

    def test_mocked_candidate_scan_is_reached_with_valid_dependent(self) -> None:
        dependent = self._dependent_with_receipts()
        with patch(
            "scripts.delivery_gate.check.list_submissions",
            side_effect=RecordError("record-tampered", "fixture"),
        ) as mocked:
            # Public check catches candidate scan failure and fails closed.
            result = check_conditions(
                self.f.root, submission_id=dependent["submission_id"]
            )
        self.assertTrue(mocked.called, result)
        self.assertEqual(result["result"], "BLOCKED")

    def test_corrupt_pass_check_and_duplicate_receipts_block(self) -> None:
        passed = self._complete()
        from scripts.delivery_gate.records import list_layer

        check = list_layer(
            self.f.root, "checks", "submission_id", passed["submission_id"]
        )[0]
        self._mutate(
            "checks",
            check["check_id"],
            lambda record: record.update(validation_id=str(uuid.uuid4())),
        )
        result = self._check_dependent()
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(
            result["conditions"]["dependency_details"][0]["status"], "receipt-invalid"
        )

        # 重置夹具后为同一送验增加有效格式的重复 validation，不得忽略。
        self.f.cleanup()
        self.setUp()
        passed = self._complete()
        original = self.f.validation_id
        from scripts.delivery_gate.records import load_layer

        duplicate = dict(load_layer(self.f.root, "validations", original))
        duplicate["validation_id"] = str(uuid.uuid4())
        duplicate.pop("content_hash")
        publish_json(
            self.f.root,
            delivery_gate_locator("validations", duplicate["validation_id"]),
            {key: value for key, value in duplicate.items() if key != "content_hash"},
        )
        result = self._check_dependent()
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(
            result["conditions"]["dependency_details"][0]["status"], "receipt-invalid"
        )
        self.assertIsNotNone(passed)

    def test_existing_failed_receipts_still_require_valid_identity_and_bindings(
        self,
    ) -> None:
        submission = self._submit()
        validation = self.f.create_validation(
            submission["submission_id"], result="FAIL"
        )
        self._mutate(
            "validations",
            validation["validation_id"],
            lambda record: record.update(submission_content_hash="0" * 64),
        )
        result = self._check_dependent()
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(
            result["conditions"]["dependency_details"][0]["status"], "receipt-invalid"
        )

        self.f.cleanup()
        self.setUp()
        submission = self._submit()
        validation = self.f.create_validation(
            submission["submission_id"], result="FAIL"
        )
        review = self.f.create_review(
            submission["submission_id"], validation["validation_id"], decision="FAIL"
        )
        self._mutate(
            "reviews",
            review["review_id"],
            lambda record: record.update(
                validation_content_hash="0" * 64,
                reviewer_identity={"actor_id": "forged"},
            ),
        )
        result = self._check_dependent()
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(
            result["conditions"]["dependency_details"][0]["status"], "receipt-invalid"
        )

    def test_invalid_result_cannot_disguise_corrupt_evidence_as_failed_history(
        self,
    ) -> None:
        from scripts.delivery_gate.records import list_layer

        for layer, value in (
            ("validations", "UNKNOWN"),
            ("reviews", "UNKNOWN"),
            ("checks", "UNKNOWN"),
            ("checks", "FAIL"),
            ("checks", "BLOCKED"),
        ):
            with self.subTest(layer=layer, value=value):
                self.f.cleanup()
                self.setUp()
                if layer == "checks":
                    old = self._complete()
                else:
                    old = self._submit()
                    self.f.create_validation(old["submission_id"])
                    if layer == "reviews":
                        self.f.create_review(old["submission_id"])
                record = list_layer(
                    self.f.root, layer, "submission_id", old["submission_id"]
                )[0]
                id_field = {
                    "validations": "validation_id",
                    "reviews": "review_id",
                    "checks": "check_id",
                }[layer]
                self._mutate(
                    layer, record[id_field], lambda item: item.update(result=value)
                )
                self._complete()
                result = self._check_dependent()
                self.assertEqual(result["result"], "BLOCKED", result)
                self.assertEqual(
                    result["conditions"]["dependency_details"][0]["status"],
                    "receipt-invalid",
                )

    def test_failed_validation_attachment_drift_is_not_filtered_out(self) -> None:
        old = self._submit()
        validation = self.f.create_validation(old["submission_id"], result="BLOCKED")
        self._complete()
        report = self.f.root / validation["verification_report"]["locator"]
        report.chmod(0o600)
        report.write_text("tampered")
        result = self._check_dependent()
        self.assertEqual(result["result"], "BLOCKED", result)
        self.assertEqual(
            result["conditions"]["dependency_details"][0]["status"], "receipt-invalid"
        )

    def test_failed_validation_identity_is_not_filtered_out(self) -> None:
        old = self._submit()
        validation = self.f.create_validation(old["submission_id"], result="FAIL")
        self._mutate(
            "validations",
            validation["validation_id"],
            lambda record: record.update(validator_identity={"actor_id": "forged"}),
        )
        self._complete()
        result = self._check_dependent()
        self.assertEqual(result["result"], "BLOCKED", result)
        self.assertEqual(
            result["conditions"]["dependency_details"][0]["status"], "receipt-invalid"
        )

    def test_nested_dependency_receipt_drift_blocks_pass_candidate(self) -> None:
        passed = self._complete()
        from scripts.delivery_gate.records import list_layer

        check = list_layer(
            self.f.root, "checks", "submission_id", passed["submission_id"]
        )[0]

        def drift_nested(record: dict) -> None:
            record["conditions"]["dependency_receipts"] = [
                {
                    "task_id": "LF-TSK-TEST-9999",
                    "submission_content_hash": "0" * 64,
                    "check_content_hash": "0" * 64,
                }
            ]

        self._mutate("checks", check["check_id"], drift_nested)
        result = self._check_dependent()
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(
            result["conditions"]["dependency_details"][0]["status"], "receipt-invalid"
        )

    def test_version_and_nested_requirement_drift_fail_closed(self) -> None:
        catalog_path = self.f.root / "planning/workstreams.yaml"
        catalog = yaml.safe_load(catalog_path.read_text())
        task2 = catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][
            1
        ]
        task2["dependencies"] = [{"task_id": TASK_ID, "required_task_version": 9}]
        catalog_path.write_text(yaml.safe_dump(catalog, sort_keys=False))
        self.f._git("add", "planning/workstreams.yaml")
        self.f._git("commit", "-m", "version constraint fixture")
        dependent = self._dependent_with_receipts()
        outcome = check_conditions(
            self.f.root, submission_id=dependent["submission_id"]
        )
        self.assertEqual(outcome["result"], "BLOCKED", outcome)
        self.assertEqual(
            outcome["conditions"]["dependency_details"][0]["status"], "missing"
        )

        self.f.cleanup()
        self.setUp()
        self._complete()
        dependent = self._dependent_with_receipts()
        catalog_path = self.f.root / "planning/workstreams.yaml"
        catalog = yaml.safe_load(catalog_path.read_text())
        task1 = catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][
            0
        ]
        task1["dependencies"] = ["LF-TSK-TEST-9999"]
        catalog_path.write_text(yaml.safe_dump(catalog, sort_keys=False))
        outcome = check_conditions(
            self.f.root, submission_id=dependent["submission_id"]
        )
        self.assertEqual(outcome["result"], "BLOCKED", outcome)


if __name__ == "__main__":
    unittest.main()
