from __future__ import annotations
import json,unittest,uuid
from unittest.mock import patch
from scripts.acceptance.submit import SubmissionError,submit
from tests.acceptance.fixtures import AcceptanceFixture,PRODUCER_SESSION,make_mock_runtime
class TestSubmit(unittest.TestCase):
 def setUp(self):self.f=AcceptanceFixture();self.f.create_verification_report()
 def tearDown(self):self.f.cleanup()
 @patch("scripts.agents.local_codex_runtime.discover")
 def test_binds_report_scope_catalog_and_full_freeze(self,m):
  m.return_value=make_mock_runtime(PRODUCER_SESSION);r=submit(self.f.root,task_id="LF-TSK-TEST-0001",change_report_id=self.f.report_id,confirm_scope_report_id=self.f.report_id)
  from scripts.acceptance.records import load_submission
  v=load_submission(self.f.root,r["submission_id"]);self.assertEqual(v["task_requirements"]["required_check_ids"],["fixture.change"]);self.assertIn("fixture.baseline",v["verification_freeze"]["input_snapshots"]);self.assertIn("fixture.change",v["verification_freeze"]["input_snapshots"])
 @patch("scripts.agents.local_codex_runtime.discover")
 def test_wrong_confirmation_or_unknown_task_fail(self,m):
  m.return_value=make_mock_runtime(PRODUCER_SESSION)
  with self.assertRaises(SubmissionError) as c:submit(self.f.root,task_id="LF-TSK-TEST-0001",change_report_id=self.f.report_id,confirm_scope_report_id="wrong")
  self.assertEqual(c.exception.code,"scope-review-confirmation-required")
  with self.assertRaises(SubmissionError) as c:submit(self.f.root,task_id="LF-TSK-UNKNOWN-0001",change_report_id=self.f.report_id,confirm_scope_report_id=self.f.report_id)
  self.assertEqual(c.exception.code,"task-not-found")
 @patch("scripts.agents.local_codex_runtime.discover")
 def test_manually_written_minimal_pass_report_is_not_a_submission_source(self,m):
  m.return_value=make_mock_runtime(PRODUCER_SESSION);rid=str(uuid.uuid4());path=self.f.root/f"tmp/quality/verification-reports/{rid}.json";path.parent.mkdir(parents=True,exist_ok=True)
  forged={"schema_version":"lexiflow.verification-report.v1","run_id":rid,"result":"PASS","reason":"","scope":"change-targeted","checks":[],"input_fingerprint":"","configuration_fingerprint":"","coverage_gaps":[],"scope_review":{"kind":"no-context","changed_files":["src/test.py"]},"base":"HEAD"};path.write_text(json.dumps(forged))
  with self.assertRaises(SubmissionError) as c:submit(self.f.root,task_id="LF-TSK-TEST-0001",change_report_id=rid,confirm_scope_report_id=rid)
  self.assertEqual(c.exception.code,"submission-invalid")
