from __future__ import annotations
import unittest
from unittest.mock import patch
from scripts.acceptance.review import ReviewError, review
from tests.acceptance.fixtures import AcceptanceFixture, PRODUCER_SESSION, VALIDATOR_SESSION, REVIEWER_SESSION, make_mock_runtime
class TestReview(unittest.TestCase):
 def setUp(self):self.f=AcceptanceFixture();self.f.create_submission();self.f.create_validation()
 def tearDown(self):self.f.cleanup()
 @patch("scripts.agents.local_codex_runtime.discover")
 def test_producer_and_validator_cannot_review(self,m):
  for session,expected in ((PRODUCER_SESSION,"self-review-forbidden"),(VALIDATOR_SESSION,"reviewer-not-independent")):
   m.return_value=make_mock_runtime(session)
   with self.assertRaises(ReviewError) as c:review(self.f.root,submission_id=self.f.submission_id,validation_id=self.f.validation_id,decision="PASS",findings=[{"finding_id":"a","severity":"PASS","code":"ok","evidence":[{"detail":"review"}]}])
   self.assertEqual(c.exception.code,expected)
 @patch("scripts.agents.local_codex_runtime.discover")
 def test_no_real_opinion_or_wrong_decision_rejected(self,m):
  m.return_value=make_mock_runtime(REVIEWER_SESSION)
  with self.assertRaises(ReviewError):review(self.f.root,submission_id=self.f.submission_id,validation_id=self.f.validation_id,decision="PASS",findings=[])
  with self.assertRaises(ReviewError) as c:review(self.f.root,submission_id=self.f.submission_id,validation_id=self.f.validation_id,decision="PASS",findings=[{"finding_id":"a","severity":"FAIL","code":"bug","evidence":[{"detail":"review"}]}])
  self.assertEqual(c.exception.code,"decision-mismatch")
 @patch("scripts.agents.local_codex_runtime.discover")
 def test_unmodified_baseline_input_drift_after_validation_blocks_review_without_rerun(self,m):
  m.return_value=make_mock_runtime(REVIEWER_SESSION);self.f.write("protected.py","after validation\n")
  with self.assertRaises(ReviewError) as c:review(self.f.root,submission_id=self.f.submission_id,validation_id=self.f.validation_id,decision="PASS",findings=[{"finding_id":"a","severity":"PASS","code":"ok","evidence":[{"detail":"review"}]}])
  self.assertEqual(c.exception.code,"frozen-input-drift")

class TestAuthorityFreshness(unittest.TestCase):
 def setUp(self):self.f=AcceptanceFixture();self.f.create_verification_report();self.f.create_submission();self.f.create_validation()
 def tearDown(self):self.f.cleanup()
 @patch("scripts.agents.local_codex_runtime.discover")
 def test_caller_fabricated_expiry_is_not_a_valid_authority_contract(self,m):
  import json
  p=self.f.root/"tmp/quality/acceptance/validations"/self.f.validation_id/"record.json";v=json.loads(p.read_text());v["authority"]["expires_at"]="2000-01-01T00:00:00Z";from scripts.acceptance.records import content_hash;v["content_hash"]=content_hash(v)
  # direct overwrite models external tampering; the hash check must fail before freshness.
  p.chmod(0o600);p.write_text(json.dumps(v));p.chmod(0o400);m.return_value=make_mock_runtime(REVIEWER_SESSION)
  with self.assertRaises(ReviewError) as c:review(self.f.root,submission_id=self.f.submission_id,validation_id=self.f.validation_id,decision="PASS",findings=[{"finding_id":"a","severity":"PASS","code":"ok","evidence":[{"detail":"review"}]}])
  self.assertEqual(c.exception.code,"validator_identity-authority-invalid")
