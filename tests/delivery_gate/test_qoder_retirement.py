"""退役来源在所有 Gate 消费边界失败关闭，原生来源仍认证附件和独立 actor。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
import uuid
from unittest.mock import patch

from scripts.acceptance.evidence.verify_candidate_acceptance_evidence import consume_candidate_pass
from scripts.acceptance.task_operations.evaluate_task_acceptance import check_conditions
from scripts.acceptance.evidence.verify_existing_acceptance_chain import consume_existing_pass
from scripts.acceptance.evidence.implementation_run_evidence import ProducerError, resolve_producer
from scripts.acceptance.evidence.acceptance_record_store import (
    content_hash,
    delivery_gate_locator,
    list_layer,
    sha256_bytes,
)
from scripts.acceptance.task_operations.record_independent_task_review import ReviewError, review
from scripts.acceptance.task_operations.submit_task_for_acceptance import SubmissionError, submit
from scripts.acceptance.task_operations.run_independent_task_validation import (
    ValidationError,
    _verify_independence,
    _verify_producer,
    validate_batch,
)
from tests.delivery_gate.fixtures import (
    PRODUCER_SESSION,
    REVIEWER_SESSION,
    TASK_ID,
    VALIDATOR_SESSION,
    DeliveryGateFixture,
    make_mock_runtime,
)
from tests.delivery_gate import test_deep_simplification, test_dependency_recovery


class RetiredProducerTests(unittest.TestCase):
    def setUp(self):
        self.f = DeliveryGateFixture()
        self.addCleanup(self.f.cleanup)

    def _retire_submission(self, submission):
        """仅改合成测试来源；重绑 receipt DAG，排除无关哈希错误造成假阳性。"""
        sid = submission["submission_id"]
        producer = {
            "kind": "qoder-work-package",
            "run_id": str(uuid.uuid4()),
            "identity": {"agent_id": "retired-worker", "client": "qoder"},
            "artifacts": {},
        }

        def rewrite(kind, record, fields):
            value = {**record, **fields}
            value["content_hash"] = content_hash(value)
            id_field = "submission_id" if kind == "submissions" else kind[:-1] + "_id"
            path = self.f.root / delivery_gate_locator(kind, value[id_field])
            path.chmod(0o600)
            path.write_text(json.dumps(value))
            path.chmod(0o400)
            return value

        submission = rewrite("submissions", submission, {"producer": producer})
        hashes = {"submission_content_hash": submission["content_hash"]}
        for kind in ("validations", "reviews", "checks"):
            for record in list_layer(self.f.root, kind, "submission_id", sid):
                bound = {key: value for key, value in hashes.items() if key in record}
                record = rewrite(kind, record, bound)
                hashes[kind[:-1] + "_content_hash"] = record["content_hash"]
        return submission

    def test_explicit_old_run_submission_never_falls_back_to_main(self):
        self.f.create_verification_report()
        run = str(uuid.uuid4())
        path = self.f.root / f"tmp/qoder-tasks/{run}/result.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(b'{"status":"PASS"}\n')
        before = path.read_bytes()
        with (
            patch(
                "scripts.agents.identity.execution_identity.discover",
                return_value=make_mock_runtime(PRODUCER_SESSION),
            ),
            self.assertRaisesRegex(SubmissionError, "producer-run-unavailable"),
        ):
            submit(
                self.f.root,
                task_id=TASK_ID,
                change_report_id=self.f.report_id,
                producer_run_id=run,
            )
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(
            (self.f.root / "tmp/quality/delivery-gate/current/submissions").exists()
        )

    def test_old_submission_cannot_validate_or_review(self):
        sub = self.f.create_submission()
        val = self.f.create_validation(sub["submission_id"])
        self._retire_submission(sub)
        with (
            patch(
                "scripts.agents.identity.execution_identity.discover",
                return_value=make_mock_runtime(VALIDATOR_SESSION),
            ),
            patch("scripts.acceptance.task_operations.run_independent_task_validation.verify_profiles") as execution,
            self.assertRaisesRegex(ValidationError, "producer-source-invalid"),
        ):
            validate_batch(self.f.root, submission_ids=[sub["submission_id"]])
        execution.assert_not_called()
        with self.assertRaisesRegex(ReviewError, "producer-source-invalid"):
            review(self.f.root, validation_id=val["validation_id"], findings=[])
        self.assertFalse(
            (self.f.root / "tmp/quality/delivery-gate/current/reviews").exists()
        )

    def test_old_pass_chain_cannot_check_consume_or_prove_candidate(self):
        sub = self.f.create_submission()
        self.f.create_validation()
        self.f.create_review()
        self.assertEqual(
            check_conditions(self.f.root, submission_id=sub["submission_id"])["result"],
            "PASS",
        )
        self._retire_submission(sub)
        with patch("scripts.acceptance.evidence.verify_candidate_acceptance_evidence._prove_candidate") as node:
            for consume in (
                check_conditions,
                consume_existing_pass,
                lambda root, **kw: consume_candidate_pass(
                    root, candidate_directory=root / "candidate", **kw
                ),
            ):
                result = consume(self.f.root, submission_id=sub["submission_id"])
                self.assertNotEqual(result["result"], "PASS", result)
                self.assertEqual(result["reason"], "producer-source-invalid", result)
                self.assertNotIn("proof", result)
        node.assert_not_called()

    def test_old_pass_dependency_blocks_even_with_unique_native_pass(self):
        self.f.cleanup()
        scenario = test_dependency_recovery.TestDependencyRecovery()
        scenario.setUp()
        self.addCleanup(scenario.tearDown)
        self.f = scenario.f
        retired = scenario._complete()
        dependent = scenario._dependent_with_receipts()
        self.assertEqual(
            check_conditions(self.f.root, submission_id=dependent["submission_id"])[
                "result"
            ],
            "PASS",
        )
        retired = self._retire_submission(retired)
        from scripts.acceptance.task_operations.evaluate_task_acceptance import _inspect_dependency

        layers = [
            list_layer(self.f.root, kind, "submission_id", retired["submission_id"])[0]
            for kind in ("validations", "reviews", "checks")
        ]
        inspection = _inspect_dependency(self.f.root, retired, *layers)
        self.assertIn("producer-source-invalid", inspection.errors)
        scenario._complete()
        with patch("scripts.acceptance.evidence.verify_candidate_acceptance_evidence._prove_candidate") as node:
            for consume in (
                check_conditions,
                consume_existing_pass,
                lambda root, **kw: consume_candidate_pass(
                    root, candidate_directory=root / "candidate", **kw
                ),
            ):
                result = consume(self.f.root, submission_id=dependent["submission_id"])
                self.assertNotEqual(result["result"], "PASS", result)
                self.assertNotIn("proof", result)
                if "conditions" in result:
                    detail = result["conditions"]["dependency_details"][0]
                    self.assertEqual(detail["status"], "receipt-invalid", result)
        node.assert_not_called()

    def test_submit_help_advertises_only_codex(self):
        from scripts.acceptance.__main__ import main

        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            main(["submit", "--help"], root=self.f.root)
        self.assertIn("Codex run", output.getvalue())
        self.assertNotIn("Qoder", output.getvalue())


class NativeProducerBoundaryTests(unittest.TestCase):
    def test_completion_raw_hash_and_derived_actor_remain_required(self):
        fixture = DeliveryGateFixture()
        self.addCleanup(fixture.cleanup)
        run = str(uuid.uuid4())
        verified = test_deep_simplification.CodexProducerViewTests()._verified(
            fixture.root, run
        )
        desc = verified["artifact_locators"]["package_completion"]
        path = fixture.root / desc["locator"]
        desc["sha256"] = sha256_bytes(path.read_bytes())
        task = {"task_id": TASK_ID, "task_version": 1, "change_version": "1.0.0"}
        identity = make_mock_runtime(PRODUCER_SESSION).context
        verified["producer_view"]["identity"] = identity
        with patch(
            "scripts.agents.work_packages.codex_work_package_publisher.CodexWorkPackagePublisher.verify",
            return_value=verified,
        ) as authenticate:
            producer = resolve_producer(fixture.root, task, run)
            submission = {"producer": producer, "task_requirements": task}
            _verify_producer(fixture.root, submission)
            runtime = {"identity": make_mock_runtime(VALIDATOR_SESSION).context}
            submission["submitter_identity"] = make_mock_runtime(
                REVIEWER_SESSION
            ).context
            _verify_independence(submission, runtime)
            with self.assertRaisesRegex(ValidationError, "self-validation-forbidden"):
                _verify_independence(submission, {"identity": identity})
            authenticate.reset_mock()
            path.write_bytes(path.read_bytes() + b" ")
            with self.assertRaisesRegex(ValidationError, "producer-source-drift"):
                _verify_producer(fixture.root, submission)
            authenticate.assert_not_called()

    def test_old_namespace_does_not_mask_invalid_native_source(self):
        fixture = DeliveryGateFixture()
        self.addCleanup(fixture.cleanup)
        run = str(uuid.uuid4())
        test_deep_simplification.CodexProducerViewTests()._verified(fixture.root, run)
        old = fixture.root / f"tmp/qoder-tasks/{run}/result.json"
        old.parent.mkdir(parents=True)
        old.write_bytes(b'{"status":"PASS"}\n')
        task = {"task_id": TASK_ID, "task_version": 1, "change_version": "1.0.0"}
        from scripts.agents.work_packages.codex_work_package_publisher import CodexWorkPackageError

        with (
            patch(
                "scripts.agents.work_packages.codex_work_package_publisher.CodexWorkPackagePublisher.verify",
                side_effect=CodexWorkPackageError("runtime-proof-drift", "fixture"),
            ),
            self.assertRaisesRegex(ProducerError, "runtime-proof-drift"),
        ):
            resolve_producer(fixture.root, task, run)
        self.assertEqual(old.read_bytes(), b'{"status":"PASS"}\n')
