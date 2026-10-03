from __future__ import annotations

import json
import shutil
import stat
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from scripts.delivery_gate.check import check_conditions
from scripts.delivery_gate.consume import consume_existing_pass
from scripts.delivery_gate.records import content_hash, delivery_gate_locator, publish_json
from tests.delivery_gate.fixtures import DeliveryGateFixture


def _tree(root: Path):
    """Capture path, node type, mode and file bytes for read-only assertions."""
    result = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(info.st_mode)
        if path.is_symlink():
            result[relative] = ("symlink", mode, path.readlink().as_posix())
        elif path.is_dir():
            result[relative] = ("directory", mode)
        else:
            result[relative] = ("file", mode, path.read_bytes())
    return result


def _receipt_path(root: Path, kind: str, record_id: str) -> Path:
    return root / delivery_gate_locator(kind, record_id)


def _rewrite(path: Path, change):
    record = json.loads(path.read_text())
    change(record)
    record["content_hash"] = content_hash(record)
    path.chmod(0o600)
    path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")))
    path.chmod(0o400)


def _consume_readonly(test, fixture):
    before = _tree(fixture.root)
    forbidden = (
        "scripts.delivery_gate.check.publish_json",
        "scripts.delivery_gate.records.publish_json",
        "scripts.delivery_gate.records.publish_bytes",
        "scripts.delivery_gate.validate.validate",
        "scripts.delivery_gate.validate.verify_repository",
        "scripts.delivery_gate.review.review",
        "scripts.verification.verify_changes",
        "scripts.verification.verify_repository",
    )
    with ExitStack() as stack:
        mocks = [stack.enter_context(patch(name, side_effect=AssertionError(name))) for name in forbidden]
        result = consume_existing_pass(fixture.root, submission_id=fixture.submission_id)
        for mock in mocks:
            mock.assert_not_called()
    test.assertEqual(before, _tree(fixture.root))
    test.assertIs(result.get("published"), False)
    test.assertIs(result.get("delivery_rerun"), False)
    if result.get("result") != "PASS":
        test.assertNotIn("proof", result)
    return result


class TestConsumeExisting(unittest.TestCase):
    def setUp(self):
        self.f = DeliveryGateFixture()
        self.f.create_submission()
        self.validation = self.f.create_validation()
        self.validation = json.loads(_receipt_path(self.f.root, "validations", self.validation["validation_id"]).read_text())
        self.review = self.f.create_review()
        self.review = json.loads(_receipt_path(self.f.root, "reviews", self.review["review_id"]).read_text())
        self.submission = json.loads(_receipt_path(self.f.root, "submissions", self.f.submission_id).read_text())
        self.check = check_conditions(self.f.root, submission_id=self.f.submission_id)
        self.assertEqual(self.check["result"], "PASS")

    def tearDown(self):
        self.f.cleanup()

    def _consume_readonly(self):
        return self._consume_snapshot()

    def _consume_snapshot(self):
        return _consume_readonly(self, self.f)

    def test_existing_pass_and_reentry_return_exact_same_chain_proof_readonly(self):
        for result in (self._consume_readonly(), self._consume_readonly()):
            self.assertEqual(result["result"], "PASS")
            self.assertEqual(result["submission_id"], self.f.submission_id)
            proof = result["proof"]
            self.assertEqual(proof, {
                "submission_id": self.f.submission_id,
                "submission_content_hash": self.submission["content_hash"],
                "validation_id": self.validation["validation_id"],
                "validation_content_hash": self.validation["content_hash"],
                "review_id": self.review["review_id"],
                "review_content_hash": self.review["content_hash"],
                "check_id": self.check["check_id"],
                "check_content_hash": self.check["content_hash"],
                "task_id": "LF-TSK-TEST-0001",
                "task_version": 1,
                "change_version": "1.0.0",
                "frozen_input_fingerprint": self.validation["frozen_input_fingerprint"],
                "verification_report": self.validation["verification_report"],
            })
            self.assertEqual(result["check_id"], self.check["check_id"])

    def test_missing_check_never_publishes(self):
        shutil.rmtree(self.f.root / "tmp/quality/delivery-gate/checks")
        before = _tree(self.f.root)
        result = self._consume_snapshot()
        self.assertEqual(before, _tree(self.f.root))
        self.assertEqual((result["result"], result["reason"]), ("BLOCKED", "check-missing"))
        self.assertNotIn("proof", result)

    def test_missing_validation_and_review_fail_closed(self):
        shutil.rmtree(self.f.root / "tmp/quality/delivery-gate/validations")
        result = self._consume_snapshot()
        self.assertEqual((result["result"], result["reason"]), ("BLOCKED", "validation-missing"))
        self.assertNotIn("proof", result)

    def test_missing_review_fail_closed(self):
        shutil.rmtree(self.f.root / "tmp/quality/delivery-gate/reviews")
        result = self._consume_snapshot()
        self.assertEqual((result["result"], result["reason"]), ("BLOCKED", "review-missing"))
        self.assertNotIn("proof", result)

    def test_duplicate_check_and_review_are_rejected(self):
        for kind, record_id in (("checks", self.check["check_id"]), ("reviews", self.review["review_id"]), ("validations", self.validation["validation_id"])):
            source = _receipt_path(self.f.root, kind, record_id)
            duplicate_id = str(uuid.uuid4())
            target = _receipt_path(self.f.root, kind, duplicate_id)
            target.parent.mkdir(parents=True)
            shutil.copyfile(source, target)
            _rewrite(target, lambda r: r.update({kind[:-1] + "_id": duplicate_id}))
            result = self._consume_snapshot()
            self.assertEqual((result["result"], result["reason"]), ("BLOCKED", "ambiguous-receipt"))
            self.assertNotIn("proof", result)
            shutil.rmtree(target.parent)

    def test_source_and_requirements_drift_are_rejected(self):
        self.f.write("protected.py", "source drift\n")
        result = self._consume_snapshot()
        self.assertEqual(result["result"], "BLOCKED")
        self.assertNotIn("proof", result)
        self.f.write("protected.py", "before\n")
        self.f.write("planning/workstreams.yaml", "workstreams: []\n")
        result = self._consume_snapshot()
        self.assertEqual(result["result"], "BLOCKED")
        self.assertNotIn("proof", result)

    def test_record_and_report_hash_tampering_are_rejected(self):
        path = _receipt_path(self.f.root, "reviews", self.review["review_id"])
        path.chmod(0o600)
        path.write_text(path.read_text().replace('"result":"PASS"', '"result":"FAIL"'))
        path.chmod(0o400)
        result = self._consume_snapshot()
        self.assertEqual(result["result"], "BLOCKED")
        self.assertNotIn("proof", result)

    def test_validation_report_bytes_must_match_bound_descriptor(self):
        report_path = _receipt_path(self.f.root, "validations", self.validation["validation_id"]).parent / "report.json"
        report_path.chmod(0o600)
        report_path.write_bytes(report_path.read_bytes() + b"tamper")
        report_path.chmod(0o400)
        result = self._consume_snapshot()
        self.assertEqual(result["result"], "FAIL")
        self.assertNotIn("proof", result)

    def test_non_pass_and_wrong_layer_bindings_are_rejected(self):
        val_path = _receipt_path(self.f.root, "validations", self.validation["validation_id"])
        _rewrite(val_path, lambda r: r.update(result="FAIL"))
        result = self._consume_snapshot()
        self.assertEqual(result["result"], "FAIL")
        self.assertNotIn("proof", result)
        _rewrite(val_path, lambda r: r.update(
            result="PASS", submission_id=self.f.submission_id,
        ))
        review_path = _receipt_path(self.f.root, "reviews", self.review["review_id"])
        _rewrite(review_path, lambda r: r.update(validation_id=str(uuid.uuid4())))
        result = self._consume_snapshot()
        self.assertEqual(result["result"], "FAIL")
        self.assertNotIn("proof", result)
        _rewrite(val_path, lambda r: r.update(result="PASS", submission_id=str(uuid.uuid4())))
        result = self._consume_snapshot()
        self.assertEqual((result["result"], result["reason"]), ("BLOCKED", "validation-missing"))
        self.assertNotIn("proof", result)

    def test_self_validation_and_review_are_rejected_even_with_valid_authority(self):
        val_path = _receipt_path(self.f.root, "validations", self.validation["validation_id"])
        producer = self.submission["producer"]
        _rewrite(val_path, lambda r: r.update(
            validator_identity=producer["identity"],
            runtime_proof=producer["runtime_proof"],
            authority=producer["authority"],
        ))
        result = self._consume_snapshot()
        self.assertIn("self-validation-forbidden", result["conditions"]["hash_dag_errors"])
        self.assertNotIn("proof", result)
        _rewrite(val_path, lambda r: r.update(
            validator_identity=self.validation["validator_identity"],
            runtime_proof=self.validation["runtime_proof"],
            authority=self.validation["authority"],
        ))
        review_path = _receipt_path(self.f.root, "reviews", self.review["review_id"])
        _rewrite(review_path, lambda r: r.update(
            reviewer_identity=self.validation["validator_identity"],
            runtime_proof=self.validation["runtime_proof"],
            authority=self.validation["authority"],
        ))
        result = self._consume_snapshot()
        self.assertIn("reviewer-not-independent", result["conditions"]["hash_dag_errors"])
        self.assertNotIn("proof", result)

    def test_changed_dependency_requirement_is_not_pass(self):
        catalog_path = self.f.root / "planning/workstreams.yaml"
        import yaml
        catalog = yaml.safe_load(catalog_path.read_text())
        task = catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][0]
        task["depends_on"] = ["LF-TSK-TEST-0099"]
        catalog_path.write_text(yaml.safe_dump(catalog, sort_keys=False))
        result = self._consume_snapshot()
        self.assertNotEqual(result["result"], "PASS")
        self.assertNotIn("proof", result)

    def test_review_not_pass_and_existing_check_mismatch_are_rejected(self):
        check_path = _receipt_path(self.f.root, "checks", self.check["check_id"])
        _rewrite(check_path, lambda r: r.update(reason="unexpected"))
        result = self._consume_snapshot()
        self.assertEqual((result["result"], result["reason"]), ("BLOCKED", "existing-check-mismatch"))
        _rewrite(check_path, lambda r: r.update(reason=""))
        review_path = _receipt_path(self.f.root, "reviews", self.review["review_id"])
        _rewrite(review_path, lambda r: r.update(result="FAIL"))
        result = self._consume_snapshot()
        self.assertIn("review-not-pass", result["conditions"]["hash_dag_errors"])

    def test_check_publishing_and_idempotency_are_preserved(self):
        other = DeliveryGateFixture()
        try:
            other.create_submission()
            other.create_validation()
            other.create_review()
            first = check_conditions(other.root, submission_id=other.submission_id)
            again = check_conditions(other.root, submission_id=other.submission_id)
            self.assertEqual(first["result"], "PASS")
            self.assertFalse(first["idempotent"])
            self.assertTrue(again["idempotent"])
            self.assertEqual(first["check_id"], again["check_id"])
        finally:
            other.cleanup()

    def test_cli_three_exit_codes_and_rejects_actor_candidate(self):
        from scripts.delivery_gate.__main__ import main
        from contextlib import redirect_stdout
        from io import StringIO
        for status, code in (("PASS", 0), ("BLOCKED", 2), ("FAIL", 1)):
            with patch("scripts.delivery_gate.consume.consume_existing_pass", return_value={"result": status, "published": False, "delivery_rerun": False}), redirect_stdout(StringIO()):
                self.assertEqual(main(["consume-existing", "--submission-id", self.f.submission_id], root=self.f.root), code)
        for extra in (("--actor", "someone"), ("--candidate", "anything")):
            with self.assertRaises(SystemExit):
                main(["consume-existing", "--submission-id", self.f.submission_id, *extra], root=self.f.root)


class TestConsumeDependencyAndApproval(unittest.TestCase):
    def setUp(self):
        from tests.delivery_gate import test_security_contract
        self.scenario = test_security_contract.TestDependencyDagAndApproval()
        self.scenario.setUp()
        self.f = self.scenario.f
        self.first = self.scenario._submission("LF-TSK-TEST-0001")
        self.first_check = check_conditions(self.f.root, submission_id=self.first)
        self.second = self.scenario._submission("LF-TSK-TEST-0002")
        self.second_check = check_conditions(self.f.root, submission_id=self.second)
        self.assertEqual(self.first_check["result"], "PASS")
        self.assertEqual(self.second_check["result"], "PASS")

    def tearDown(self):
        self.scenario.tearDown()

    def test_transitive_dependency_check_removed_from_existing_pass_chain(self):
        self.assertEqual(_consume_readonly(self, self.f)["result"], "PASS")
        shutil.rmtree(_receipt_path(self.f.root, "checks", self.first_check["check_id"]).parent)
        result = _consume_readonly(self, self.f)
        self.assertEqual((result["result"], result["reason"]), ("BLOCKED", "dependency-not-pass"))

    def test_bound_approval_removed_from_existing_pass_chain(self):
        self.scenario._submission("LF-TSK-TEST-0003")
        approval = {
            "schema_version": "lexiflow.user-approval.v1",
            "approval_id": str(uuid.uuid4()), "gate_id": "G1", "decision": "APPROVED",
            "subject_task_id": "LF-TSK-TEST-0002", "subject_task_version": 1,
            "subject_change_version": "1.0.0",
            "subject_check_content_hash": self.second_check["content_hash"],
            "statement": "Synthetic fixture approval", "approved_at": "2026-09-21T00:00:00Z",
        }
        locator = "tmp/quality/delivery-gate/approvals/G1.json"
        publish_json(self.f.root, locator, approval)
        self.assertEqual(check_conditions(self.f.root, submission_id=self.f.submission_id)["result"], "PASS")
        self.assertEqual(_consume_readonly(self, self.f)["result"], "PASS")
        (self.f.root / locator).unlink()
        result = _consume_readonly(self, self.f)
        self.assertEqual((result["result"], result["reason"]), ("BLOCKED", "user-approval-missing"))
