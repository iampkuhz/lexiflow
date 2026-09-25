from __future__ import annotations
import unittest
from unittest.mock import patch
from scripts.delivery_gate.validate import ValidationError,validate
from tests.delivery_gate.fixtures import DeliveryGateFixture,PRODUCER_SESSION,VALIDATOR_SESSION,make_mock_runtime
class TestValidate(unittest.TestCase):
 def setUp(self):self.f=DeliveryGateFixture();self.f.create_verification_report();self.f.create_submission()
 def tearDown(self):self.f.cleanup()
 @patch("scripts.agents.local_codex_runtime.discover")
 def test_self_validation_rejected(self,m):
  m.return_value=make_mock_runtime(PRODUCER_SESSION)
  with self.assertRaises(ValidationError) as c:validate(self.f.root,submission_id=self.f.submission_id)
  self.assertEqual(c.exception.code,"self-validation-forbidden")
 @patch("scripts.agents.local_codex_runtime.discover")
 def test_unmodified_full_input_drift_rejected(self,m):
  self.f.write("protected.py","after\n");m.return_value=make_mock_runtime(VALIDATOR_SESSION)
  with self.assertRaises(ValidationError) as c:validate(self.f.root,submission_id=self.f.submission_id)
  self.assertIn(c.exception.code,{"frozen-input-drift","frozen-input-mismatch","file-tampered"})
