from __future__ import annotations
import json, unittest
from scripts.acceptance.check import check_conditions
from tests.acceptance.fixtures import AcceptanceFixture
class TestCheck(unittest.TestCase):
 def setUp(self):self.f=AcceptanceFixture();self.f.create_submission();self.f.create_validation();self.f.create_review()
 def tearDown(self):self.f.cleanup()
 def test_hash_tampering_is_blocked(self):
  p=self.f.root/"tmp/quality/acceptance/validations"/self.f.validation_id/"record.json";v=json.loads(p.read_text());v["result"]="FAIL";p.chmod(0o600);p.write_text(json.dumps(v));p.chmod(0o400)
  r=check_conditions(self.f.root,submission_id=self.f.submission_id);self.assertEqual(r["result"],"BLOCKED")
 def test_missing_review_is_blocked(self):
  other=AcceptanceFixture();other.create_submission();other.create_validation();r=check_conditions(other.root,submission_id=other.submission_id);self.assertEqual(r["reason"],"review-missing");other.cleanup()
 def test_missing_bound_validation_evidence_cannot_pass(self):
  (self.f.root/"tmp/quality/acceptance/validations"/self.f.validation_id/"report.json").unlink()
  r=check_conditions(self.f.root,submission_id=self.f.submission_id);self.assertEqual((r["result"],r["reason"]),("FAIL","validation-evidence-drift"))
 def test_review_and_check_do_not_execute_delivery(self):
  r=check_conditions(self.f.root,submission_id=self.f.submission_id);self.assertFalse(r["delivery_rerun"])
 def test_unmodified_baseline_input_drift_after_review_blocks_check(self):
  self.f.write("protected.py","after review\n");r=check_conditions(self.f.root,submission_id=self.f.submission_id)
  self.assertEqual((r["result"],r["reason"]),("BLOCKED","frozen-input-drift"));self.assertFalse(r["delivery_rerun"])
