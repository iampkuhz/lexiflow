"""最新 Gate 合同的唯一父事实、命名空间与来源认证回归。"""

from __future__ import annotations

import copy
import inspect
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts.agents.work_packages.codex_work_package_publisher import CodexWorkPackageError
from scripts.acceptance.evidence.acceptance_actor_identity import verify_authority
from scripts.acceptance.task_operations.evaluate_task_acceptance import _EvaluationContext, check_conditions
from scripts.acceptance.evidence.implementation_run_evidence import ProducerError, _codex, producer_identity
from scripts.acceptance.evidence.acceptance_record_store import (
    RecordError,
    delivery_gate_locator,
    list_submissions,
    load_submission,
    publish_json,
)
from scripts.acceptance.task_operations.record_independent_task_review import _findings, review
from scripts.acceptance.task_operations.submit_task_for_acceptance import submit
from scripts.acceptance.task_operations.run_independent_task_validation import _verify_producer
from tests.delivery_gate.fixtures import (
    DeliveryGateFixture,
    TASK_ID,
    PRODUCER_SESSION,
    mock_authority,
    make_mock_runtime,
)


class LatestGateContractTests(unittest.TestCase):
    def setUp(self):
        self.f = DeliveryGateFixture()
        self.addCleanup(self.f.cleanup)

    def test_latest_namespace_never_decodes_or_rewrites_old_raw_evidence(self):
        rid = str(uuid.uuid4())
        old = self.f.root / f"tmp/quality/delivery-gate/submissions/{rid}.json"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"{old invalid record bytes}\n")
        before = old.read_bytes()
        record = self.f.create_submission()
        self.assertEqual(len(list_submissions(self.f.root)), 1)
        self.assertEqual(old.read_bytes(), before)
        current = self.f.root / delivery_gate_locator(
            "submissions", record["submission_id"]
        )
        current.chmod(0o600)
        current.write_bytes(b"{same namespace invalid bytes}\n")
        with self.assertRaises(RecordError):
            list_submissions(self.f.root)
        self.assertEqual(old.read_bytes(), before)

    def test_submission_has_no_same_parent_mirrors_or_confirmation_input(self):
        sub = self.f.create_submission()
        self.assertEqual(sub["producer"], {"kind": "current-codex-task"})
        self.assertEqual(
            set(sub["task_subject"]),
            {"schema_version", "task_paths", "dependency_paths"},
        )
        self.assertNotIn(
            "confirm_scope_report_id", inspect.signature(submit).parameters
        )
        for key in ("scope_confirmation", "scope_base", "changed_file_snapshots"):
            self.assertNotIn(key, sub)
        for key in ("risk_assessment_hash", "task_requirements_hash"):
            self.assertNotIn(key, sub["acceptance_plan"])
        self.assertEqual(producer_identity(sub), sub["submitter_identity"])
        _verify_producer(self.f.root, sub)
        for producer in (
            {"kind": "current-codex-task", "identity": sub["submitter_identity"]},
            {"kind": "codex-work-package"},
            {"kind": "unknown"},
        ):
            with self.assertRaises(ProducerError):
                producer_identity({**sub, "producer": producer})

    def test_authority_has_no_child_hashes_but_still_rechecks_native_source(self):
        runtime = make_mock_runtime(PRODUCER_SESSION)
        record = {
            "submitter_identity": runtime.context,
            "runtime_proof": runtime.proof,
            "authority": mock_authority(PRODUCER_SESSION),
        }
        self.assertEqual(
            set(record["authority"]), {"trust_boundary", "verifier", "verified_at"}
        )
        with patch(
            "scripts.agents.identity.codex_actor_identity.verify_proof", return_value=None
        ) as source:
            self.assertIsNone(
                verify_authority(self.f.root, record, "submitter_identity")
            )
            source.assert_called_once_with(self.f.root, runtime.proof, runtime.context)
        from scripts.agents.identity.codex_actor_identity import CodexRuntimeError

        with patch(
            "scripts.agents.identity.codex_actor_identity.verify_proof",
            side_effect=CodexRuntimeError("runtime-proof-drift", "fixture"),
        ):
            self.assertIn(
                "source-runtime-proof-drift",
                verify_authority(self.f.root, record, "submitter_identity"),
            )
        for key in ("identity_sha256", "proof_sha256"):
            extra = copy.deepcopy(record)
            extra["authority"][key] = "a" * 64
            self.assertIn(
                "authority-invalid",
                verify_authority(self.f.root, extra, "submitter_identity"),
            )

    def test_review_has_only_explicit_findings_and_no_overall_decision_input(self):
        self.assertNotIn("decision", inspect.signature(review).parameters)
        self.assertNotIn("decision", inspect.signature(_findings).parameters)
        findings = [
            {
                "finding_id": status,
                "severity": status,
                "code": "opinion",
                "evidence": [{"detail": "fixture"}],
            }
            for status in ("PASS", "BLOCKED", "FAIL")
        ]
        self.assertEqual(_findings(findings), findings)

    def test_mixed_findings_use_fail_priority_and_consumer_rejects_contradiction(self):
        from scripts.acceptance.evidence.acceptance_record_store import content_hash, load_layer
        from tests.delivery_gate.fixtures import REVIEWER_SESSION

        sub = self.f.create_submission()
        self.f.create_validation()
        findings = [
            {
                "finding_id": status,
                "severity": status,
                "code": "opinion",
                "evidence": [{"detail": "fixture frozen diff"}],
            }
            for status in ("PASS", "BLOCKED", "FAIL")
        ]
        with patch(
            "scripts.agents.identity.execution_identity.discover",
            return_value=make_mock_runtime(REVIEWER_SESSION),
        ):
            result = review(
                self.f.root, validation_id=self.f.validation_id, findings=findings
            )
        self.assertEqual(result["result"], "FAIL")
        path = self.f.root / delivery_gate_locator("reviews", result["review_id"])
        record = load_layer(self.f.root, "reviews", result["review_id"])
        record["result"] = "PASS"
        record["content_hash"] = content_hash(record)
        path.chmod(0o600)
        path.write_text(json.dumps(record))
        path.chmod(0o400)
        checked = check_conditions(self.f.root, submission_id=sub["submission_id"])
        self.assertNotEqual(checked["result"], "PASS")
        self.assertIn(
            "review-findings-result-mismatch", checked["conditions"]["hash_dag_errors"]
        )


class CodexProducerViewTests(unittest.TestCase):
    def _verified(self, root, run):
        path = root / f"tmp/quality/codex-work-packages/{run}/package-completion.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": "latest",
                    "frozen_caller_descriptor": {},
                    "runtime_binding_descriptor": {},
                    "tasks": {},
                }
            )
        )
        identity = {"agent_id": "native-worker", "run_id": run, "client": "codex"}
        descriptor = {
            "locator": str(path.relative_to(root)),
            "sha256": "a" * 64,
            "bytes": path.stat().st_size,
        }
        return {
            "status": "PASS",
            "run_id": run,
            "artifact_locators": {"package_completion": descriptor},
            "producer_view": {
                "caller_contract": {
                    "task_ids": [TASK_ID],
                    "task_versions": {TASK_ID: 1},
                    "change_versions": {TASK_ID: "1.0.0"},
                },
                "identity": identity,
                "status": "FAIL",
                "tasks": {
                    TASK_ID: {
                        "projection": {},
                        "outcome": {"status": "PASS"},
                        "artifacts": {},
                    }
                },
            },
        }

    def test_gate_uses_authenticated_derived_view_not_completion_mirrors(self):
        with tempfile.TemporaryDirectory() as directory:
            root, run = Path(directory), str(uuid.uuid4())
            verified = self._verified(root, run)
            task = {"task_id": TASK_ID, "task_version": 1, "change_version": "1.0.0"}
            with patch(
                "scripts.agents.work_packages.codex_work_package_publisher.CodexWorkPackagePublisher.verify",
                return_value=verified,
            ) as verify:
                producer = _codex(root, run, task)
                self.assertEqual(
                    producer["identity"], verified["producer_view"]["identity"]
                )
                verify.assert_called_once_with(run)
                # Another Task may fail; this target Task remains a legitimate PASS outcome.
                verified["producer_view"]["tasks"][TASK_ID]["outcome"]["status"] = (
                    "BLOCKED"
                )
                with self.assertRaisesRegex(ProducerError, "producer-result-not-pass"):
                    _codex(root, run, task)
            with patch(
                "scripts.agents.work_packages.codex_work_package_publisher.CodexWorkPackagePublisher.verify",
                side_effect=CodexWorkPackageError(
                    "runtime-proof-drift", "fixture", status="BLOCKED"
                ),
            ):
                with self.assertRaises(ProducerError) as caught:
                    _codex(root, run, task)
                self.assertEqual(caught.exception.status, "BLOCKED")


class CandidateReadClosureTests(unittest.TestCase):
    def test_unselected_candidate_sources_are_reauthenticated_after_node(self):
        """唯一 PASS 外的未完成候选同样属于影响选择的来源闭包。"""
        from scripts.agents.identity.codex_actor_identity import CodexRuntimeError
        from scripts.acceptance.evidence.verify_candidate_acceptance_evidence import consume_candidate_pass
        from tests.delivery_gate.test_dependency_recovery import TestDependencyRecovery
        from tests.delivery_gate import fixtures

        scenario = TestDependencyRecovery()
        scenario.setUp()
        self.addCleanup(scenario.tearDown)
        scenario._complete()
        alternative_session = "44444444-4444-4444-9444-444444444444"
        with patch.object(fixtures, "PRODUCER_SESSION", alternative_session):
            alternative = scenario._submit()
        target = scenario._dependent_with_receipts()
        self.assertEqual(
            check_conditions(scenario.f.root, submission_id=target["submission_id"])[
                "result"
            ],
            "PASS",
        )
        alternative = load_submission(scenario.f.root, alternative["submission_id"])
        alternative_actor = alternative["submitter_identity"]["actor_id"]
        after_node = False
        reads = []

        def authenticate(_root, _proof, identity):
            reads.append((identity["actor_id"], after_node))
            if after_node and identity["actor_id"] == alternative_actor:
                raise CodexRuntimeError(
                    "runtime-proof-drift", "alternative native metadata changed"
                )

        def node(*_args):
            nonlocal after_node
            after_node = True
            return ({}, b"fixture stdout", {})

        with (
            patch(
                "scripts.agents.identity.codex_actor_identity.verify_proof",
                side_effect=authenticate,
            ),
            patch(
                "scripts.acceptance.evidence.verify_candidate_acceptance_evidence._prove_candidate", side_effect=node
            ) as candidate,
            patch(
                "scripts.acceptance.evidence.verify_candidate_acceptance_evidence._recheck_candidate", return_value={}
            ) as final_attachment,
        ):
            result = consume_candidate_pass(
                scenario.f.root,
                submission_id=target["submission_id"],
                candidate_directory=scenario.f.root / "candidate",
            )
        self.assertNotEqual(result["result"], "PASS", result)
        self.assertIn((alternative_actor, False), reads)
        self.assertIn((alternative_actor, True), reads)
        candidate.assert_called_once()
        final_attachment.assert_not_called()

    def test_dependency_source_attachments_are_bound_in_both_read_stages(self):
        from scripts.acceptance.evidence.verify_candidate_acceptance_evidence import consume_candidate_pass
        from tests.delivery_gate.test_dependency_recovery import TestDependencyRecovery

        scenario = TestDependencyRecovery()
        scenario.setUp()
        self.addCleanup(scenario.tearDown)
        source = scenario._complete()
        alternative = scenario._submit()
        target = scenario._dependent_with_receipts()
        self.assertEqual(
            check_conditions(scenario.f.root, submission_id=target["submission_id"])[
                "result"
            ],
            "PASS",
        )
        for record in (source, alternative):
            for label in ("change_report", "diff"):
                with self.subTest(submission=record["submission_id"], label=label):
                    path = scenario.f.root / record[label]["locator"]
                    original, mode = path.read_bytes(), path.stat().st_mode & 0o777

                    def node(*_args):
                        path.chmod(0o600)
                        path.write_bytes(original + b"drift")
                        return ({}, b"fixture stdout", {})

                    try:
                        with (
                            patch(
                                "scripts.acceptance.evidence.verify_candidate_acceptance_evidence._prove_candidate",
                                side_effect=node,
                            ),
                            patch(
                                "scripts.acceptance.evidence.verify_candidate_acceptance_evidence._recheck_candidate",
                                return_value={},
                            ),
                        ):
                            result = consume_candidate_pass(
                                scenario.f.root,
                                submission_id=target["submission_id"],
                                candidate_directory=scenario.f.root / "candidate",
                            )
                        self.assertNotEqual(result["result"], "PASS", result)
                    finally:
                        path.write_bytes(original)
                        path.chmod(mode)
