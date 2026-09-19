import copy
import io
import json
import unittest
import uuid
from unittest.mock import patch

from scripts.gates import cli
from scripts.gates.independent_review import (
    IndependentReviewError,
    _verify_current_task_source,
    verify_independent_review,
)
from scripts.gates.receipt_store import canonical_json_bytes, read_bound_bytes, sha256_bytes
from tests.gates.test_gate_lifecycle import Fixture, PROCESS_ID, RUN_ID, descriptor
from tests.gates.test_evidence_packet import _set_changed_files
from tests.gates.test_gate_planner import RealPlannerFixture


REVIEW_RUN = "99999999-9999-4999-8999-999999999999"
REVIEW_PROCESS = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class ReviewFixture:
    def __init__(self):
        self.base = Fixture()
        self.root = self.base.root
        source = b"catalog source\n"
        self.source = self.base.write("planning/workstreams.yaml", source)
        self.base.plan["task"]["task_source"] = dict(self.source)
        self._refingerprint(self.base.plan)
        ids = iter((uuid.UUID(RUN_ID), uuid.UUID(PROCESS_ID)))
        self.validation = cli.run_gate(
            self.root, mode="incremental", receipt_kind="TASK_VALIDATION",
            evidence_packet=self.base.evidence["locator"], issuer_packet=self.base.issuer["locator"],
            compiler=self.base.compiler, executor=self.base.execution,
            uuid_factory=lambda: next(ids), stderr=io.StringIO(), now=lambda: "2026-09-16T00:00:02Z",
        )
        validation_locator = f"tmp/quality/runs/{RUN_ID}/receipt.json"
        validation_bytes = read_bound_bytes(self.root, validation_locator)
        self.validation_descriptor = descriptor(validation_locator, validation_bytes)
        self.review_plan = copy.deepcopy(self.base.plan)
        self.review_plan["receipt_kind"] = "INDEPENDENT_REVIEW"
        self.review_plan["execution"] = {
            "layer": "evidence-consumption",
            "checker_execution": "forbidden",
            "source": "prior-immutable-receipts",
        }
        self.review_plan["checks"] = []
        self.review_plan["issuer_packet"]["packet"].update({
            "issuer_instance_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "actor_id": "independent-reviewer",
            "parent_session_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "session_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        })
        reviewer_packet = canonical_json_bytes(self.review_plan["issuer_packet"]["packet"])
        self.review_plan["issuer_packet"].update(self.base.write("tmp/quality/issuers/reviewer.json", reviewer_packet))
        # The review plan is compiled from the reviewer's own explicit
        # evidence packet. Its frozen scope is the trusted reviewer write set,
        # separate from the subject validation scope.
        self.review_plan["scope"]["changed_files"] = []
        test_descriptor = self.review_plan["subject"]["raw_artifacts"]["tests"][0]
        diff = self.review_plan["subject"]["raw_artifacts"]["diff"]
        policy_sha = self.review_plan["consumed_inputs"][0]["sha256"]
        self.review_evidence = {
            "schema_version": "lexiflow.independent-review-evidence.v1",
            "validation_receipt": self.validation_descriptor,
            "review_scope": {
                "source_snapshot_fingerprint": self.validation["current_inputs"]["source_snapshot_fingerprint"],
                "registry_sha256": self.validation["current_inputs"]["registry"]["sha256"],
                "policy_sha256": policy_sha,
                "source": dict(self.source),
                "diff": dict(diff),
                "changed_files": self.validation["validation"]["scope_reconciliation"]["changed_files"],
            },
            "reviewer_changed_files": [],
            "findings": [{
                "finding_id": "review-complete", "severity": "PASS", "code": "review-complete",
                "evidence": [dict(test_descriptor)],
            }],
            "rerun_evidence": [dict(test_descriptor)],
            "decision": "PASS",
        }
        review_bytes = canonical_json_bytes(self.review_evidence)
        review_descriptor = self.base.write("tmp/evidence/independent-review.json", review_bytes)
        self.review_plan["subject"]["raw_artifacts"]["tests"].append(review_descriptor)
        self._refingerprint(self.review_plan)

    @staticmethod
    def _refingerprint(plan):
        payload = {key: value for key, value in plan.items() if key != "content_fingerprint"}
        plan["content_fingerprint"] = sha256_bytes(canonical_json_bytes(payload))

    def compiler(self, *args, **kwargs):
        value = copy.deepcopy(self.review_plan)
        return {"plan": value, "canonical_bytes": canonical_json_bytes(value), "content_fingerprint": value["content_fingerprint"]}

    def verification_args(self):
        packet = self.review_plan["issuer_packet"]["packet"]
        reviewer = {
            field: packet[field]
            for field in ("issuer_instance_id", "actor_type", "actor_id", "parent_session_id", "session_id", "client", "role")
        }
        evidence = self.review_evidence
        return dict(
            repo_root=str(self.root), validation_receipt=evidence["validation_receipt"],
            reviewer_identity=reviewer, review_run_id=REVIEW_RUN,
            reviewer_changed_files=evidence["reviewer_changed_files"], review_scope=evidence["review_scope"],
            findings=evidence["findings"], rerun_evidence=evidence["rerun_evidence"],
            decision=evidence["decision"], current_plan=self.review_plan,
        )


class TestIndependentReview(unittest.TestCase):
    def setUp(self):
        self.fixture = ReviewFixture()
        self.addCleanup(self.fixture.base.temp.cleanup)

    def test_publishes_new_immutable_review_without_rewriting_subject(self):
        before = read_bound_bytes(self.fixture.root, self.fixture.validation_descriptor["locator"])
        ids = iter((uuid.UUID(REVIEW_RUN), uuid.UUID(REVIEW_PROCESS)))
        receipt = cli.run_gate(
            self.fixture.root, mode="incremental", receipt_kind="INDEPENDENT_REVIEW",
            evidence_packet=self.fixture.base.evidence["locator"],
            issuer_packet=self.fixture.review_plan["issuer_packet"]["locator"],
            compiler=self.fixture.compiler, executor=self.fixture.base.execution,
            uuid_factory=lambda: next(ids), stderr=io.StringIO(), now=lambda: "2026-09-16T00:01:00Z",
        )
        after = read_bound_bytes(self.fixture.root, self.fixture.validation_descriptor["locator"])
        self.assertEqual(before, after)
        self.assertEqual(receipt["receipt_kind"], "INDEPENDENT_REVIEW")
        self.assertEqual(receipt["result"], "PASS")
        self.assertTrue(receipt["reviewer_independence"]["producer_and_reviewer_differ"])
        self.assertTrue(receipt["reviewer_independence"]["subject_snapshot_reverified"])

    def test_self_review_is_rejected(self):
        args = self.fixture.verification_args()
        args["reviewer_identity"] = copy.deepcopy(self.fixture.validation["issuer"]["actor_identity"])
        with self.assertRaisesRegex(IndependentReviewError, "reviewer-not-independent"):
            verify_independent_review(**args)

    def test_task_scoped_source_is_checked_as_a_projection(self):
        source = {"locator": "planning/workstreams.yaml", "sha256": "a" * 64}
        with patch("scripts.gates.independent_review.source_descriptor_is_current", return_value=True) as current:
            _verify_current_task_source(str(self.fixture.root), "LF-TSK-QLT-0007", source)
        current.assert_called_once_with(str(self.fixture.root), "LF-TSK-QLT-0007", source)

        with patch("scripts.gates.independent_review.source_descriptor_is_current", return_value=False):
            with self.assertRaisesRegex(IndependentReviewError, "stale-subject"):
                _verify_current_task_source(str(self.fixture.root), "LF-TSK-QLT-0007", source)

    def test_distinct_verified_actor_can_share_validation_host_session(self):
        args = self.fixture.verification_args()
        validation_actor = self.fixture.validation["issuer"]["actor_identity"]
        args["reviewer_identity"]["session_id"] = validation_actor["session_id"]
        args["reviewer_identity"]["client"] = validation_actor["client"]
        result = verify_independent_review(**args)
        self.assertTrue(result["independence"]["producer_and_reviewer_differ"])

    def test_new_issuer_and_session_do_not_make_same_actor_independent(self):
        validation = self.fixture.validation
        for actor in (validation["issuer"]["actor_identity"]["actor_id"], validation["validation"]["subject_identity"]["agent_id"]):
            with self.subTest(actor=actor):
                args = self.fixture.verification_args()
                args["reviewer_identity"]["actor_id"] = actor
                with self.assertRaisesRegex(IndependentReviewError, "reviewer-not-independent"):
                    verify_independent_review(**args)

    def test_reviewer_write_to_subject_is_rejected(self):
        args = self.fixture.verification_args()
        changed = list(self.fixture.validation["validation"]["scope_reconciliation"]["changed_files"])
        args["reviewer_changed_files"] = changed
        args["current_plan"] = copy.deepcopy(args["current_plan"])
        args["current_plan"]["scope"]["changed_files"] = changed
        with self.assertRaisesRegex(IndependentReviewError, "reviewer-not-independent"):
            verify_independent_review(**args)

    def test_self_reported_empty_write_set_cannot_hide_frozen_subject_write(self):
        args = self.fixture.verification_args()
        args["current_plan"] = copy.deepcopy(args["current_plan"])
        args["current_plan"]["scope"]["changed_files"] = list(
            self.fixture.validation["validation"]["scope_reconciliation"]["changed_files"]
        )
        self.assertEqual(args["reviewer_changed_files"], [])
        with self.assertRaisesRegex(IndependentReviewError, "reviewer-not-independent"):
            verify_independent_review(**args)

    def test_self_reported_empty_write_set_cannot_hide_current_subject_mutation(self):
        self.assertEqual(self.fixture.review_evidence["reviewer_changed_files"], [])
        (self.fixture.root / "scripts/gates/cli.py").write_bytes(b"reviewer mutation\n")
        with self.assertRaisesRegex(IndependentReviewError, "stale-subject"):
            verify_independent_review(**self.fixture.verification_args())

    def test_stale_scope_and_hash_drift_are_rejected(self):
        args = self.fixture.verification_args()
        args["review_scope"] = copy.deepcopy(args["review_scope"])
        args["review_scope"]["registry_sha256"] = "0" * 64
        with self.assertRaisesRegex(IndependentReviewError, "stale-subject"):
            verify_independent_review(**args)
        args = self.fixture.verification_args()
        args["validation_receipt"] = dict(args["validation_receipt"], sha256="0" * 64)
        with self.assertRaisesRegex(IndependentReviewError, "hash-drift"):
            verify_independent_review(**args)

    def test_blocked_and_fail_decisions_remain_typed(self):
        for decision in ("BLOCKED", "FAIL"):
            args = self.fixture.verification_args()
            args["decision"] = decision
            args["findings"] = [{
                "finding_id": decision.lower(), "severity": decision, "code": decision.lower(),
                "evidence": self.fixture.review_evidence["rerun_evidence"],
            }]
            verified = verify_independent_review(**args)
            self.assertEqual(verified["decision"], decision)

    def test_subject_artifact_drift_is_rejected(self):
        manifest_locator = self.fixture.validation["artifact_manifest"]["locator"]
        manifest = json.loads(read_bound_bytes(self.fixture.root, manifest_locator))
        target = manifest["artifacts"][0]["locator"]
        (self.fixture.root / target).write_bytes(b"changed")
        with self.assertRaisesRegex(IndependentReviewError, "hash-drift"):
            verify_independent_review(**self.fixture.verification_args())


class TestCodexReviewProjectionCompile(unittest.TestCase):
    def test_zero_write_codex_projection_reaches_real_review_plan(self):
        fixture = RealPlannerFixture()
        self.addCleanup(fixture.cleanup)
        fixture.use_codex_work_package()
        _set_changed_files(
            fixture.evidence_fixture,
            fixture.evidence_input,
            [],
        )
        fixture._materialize_evidence()
        fixture._materialize_issuer(["INDEPENDENT_REVIEW"])
        plan = fixture.compile(receipt_kind="INDEPENDENT_REVIEW")["plan"]
        self.assertEqual(plan["receipt_kind"], "INDEPENDENT_REVIEW")
        self.assertEqual(plan["scope"]["changed_files"], [])
        self.assertEqual(plan["subject"]["identity"]["client"], "codex")
        self.assertEqual(
            plan["task"]["work_package_projection"]["target_task_id"],
            "LF-TSK-QLT-0008",
        )


if __name__ == "__main__":
    unittest.main()
