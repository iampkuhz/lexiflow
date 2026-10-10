from __future__ import annotations
import unittest
from unittest.mock import patch
from scripts.acceptance.task_operations.record_independent_task_review import ReviewError, review
from scripts.acceptance.task_operations.record_independent_task_review import _verify_independence
from tests.delivery_gate.fixtures import (
    DeliveryGateFixture,
    PRODUCER_SESSION,
    VALIDATOR_SESSION,
    REVIEWER_SESSION,
    make_mock_runtime,
)


class TestReview(unittest.TestCase):
    def test_distinct_siblings_can_review_without_independent_host_session(self):
        producer = {"actor_id": "producer", "session_id": "shared"}
        validator = {"actor_id": "validator", "session_id": "shared"}
        submission = {
            "submitter_identity": producer,
            "producer": {"kind": "current-codex-task"},
        }
        validation = {"validator_identity": validator}
        _verify_independence(
            submission,
            validation,
            {"identity": {"actor_id": "reviewer", "session_id": "shared"}},
        )
        for identity in (producer, validator):
            with self.assertRaises(ReviewError):
                _verify_independence(
                    submission,
                    validation,
                    {"identity": {**identity, "session_id": "different"}},
                )

    def setUp(self):
        self.f = DeliveryGateFixture()
        self.f.create_submission()
        self.f.create_validation()

    def tearDown(self):
        self.f.cleanup()

    @patch("scripts.agents.identity.codex_actor_identity.discover")
    def test_producer_and_validator_cannot_review(self, m):
        for session, expected in (
            (PRODUCER_SESSION, "self-review-forbidden"),
            (VALIDATOR_SESSION, "reviewer-not-independent"),
        ):
            m.return_value = make_mock_runtime(session)
            with self.assertRaises(ReviewError) as c:
                review(
                    self.f.root,
                    validation_id=self.f.validation_id,
                    findings=[
                        {
                            "finding_id": "a",
                            "severity": "PASS",
                            "code": "ok",
                            "evidence": [{"detail": "review"}],
                        }
                    ],
                )
            self.assertEqual(c.exception.code, expected)

    @patch("scripts.agents.identity.codex_actor_identity.discover")
    def test_missing_opinion_rejected_and_fail_derived_from_findings(self, m):
        m.return_value = make_mock_runtime(REVIEWER_SESSION)
        with self.assertRaises(ReviewError):
            review(self.f.root, validation_id=self.f.validation_id, findings=[])
        result = review(
            self.f.root,
            validation_id=self.f.validation_id,
            findings=[
                {
                    "finding_id": "a",
                    "severity": "FAIL",
                    "code": "bug",
                    "evidence": [{"detail": "review"}],
                }
            ],
        )
        self.assertEqual(result["result"], "FAIL")

    @patch("scripts.agents.identity.codex_actor_identity.discover")
    def test_unmodified_baseline_input_drift_after_validation_blocks_review_without_rerun(
        self, m
    ):
        m.return_value = make_mock_runtime(REVIEWER_SESSION)
        self.f.write("protected.py", "after validation\n")
        with self.assertRaises(ReviewError) as c:
            review(
                self.f.root,
                validation_id=self.f.validation_id,
                findings=[
                    {
                        "finding_id": "a",
                        "severity": "PASS",
                        "code": "ok",
                        "evidence": [{"detail": "review"}],
                    }
                ],
            )
        self.assertEqual(c.exception.code, "risk-assessment-drift")


class TestAuthorityFreshness(unittest.TestCase):
    def setUp(self):
        self.f = DeliveryGateFixture()
        self.f.create_verification_report()
        self.f.create_submission()
        self.f.create_validation()

    def tearDown(self):
        self.f.cleanup()

    @patch("scripts.agents.identity.codex_actor_identity.discover")
    def test_caller_fabricated_expiry_is_not_a_valid_authority_contract(self, m):
        import json

        p = (
            self.f.root
            / "tmp/quality/delivery-gate/current/validations"
            / self.f.validation_id
            / "record.json"
        )
        v = json.loads(p.read_text())
        v["authority"]["expires_at"] = "2000-01-01T00:00:00Z"
        from scripts.acceptance.evidence.acceptance_record_store import content_hash

        v["content_hash"] = content_hash(v)
        # direct overwrite models external tampering; the hash check must fail before freshness.
        p.chmod(0o600)
        p.write_text(json.dumps(v))
        p.chmod(0o400)
        m.return_value = make_mock_runtime(REVIEWER_SESSION)
        with self.assertRaises(ReviewError) as c:
            review(
                self.f.root,
                validation_id=self.f.validation_id,
                findings=[
                    {
                        "finding_id": "a",
                        "severity": "PASS",
                        "code": "ok",
                        "evidence": [{"detail": "review"}],
                    }
                ],
            )
        self.assertEqual(c.exception.code, "validator_identity-authority-invalid")
