from __future__ import annotations
import contextlib,io,json,unittest
from unittest.mock import patch
from scripts.acceptance import submit,validate,review,check_conditions
from scripts.acceptance.status import query_status
from tests.acceptance.fixtures import AcceptanceFixture,PRODUCER_SESSION,VALIDATOR_SESSION,REVIEWER_SESSION,make_mock_runtime
def runtimes(*sessions):
 values=[make_mock_runtime(s) for s in sessions]
 return lambda *_:values.pop(0)
class TestActualFourScenarioChain(unittest.TestCase):
 def setUp(self):self.f=AcceptanceFixture();self.f.create_verification_report()
 def tearDown(self):self.f.cleanup()
 @patch("scripts.agents.local_codex_runtime.discover")
 def test_daily_report_submit_validate_review_check(self,m):
  m.side_effect=runtimes(PRODUCER_SESSION,VALIDATOR_SESSION,REVIEWER_SESSION)
  sub=submit(self.f.root,task_id="LF-TSK-TEST-0001",change_report_id=self.f.report_id,confirm_scope_report_id=self.f.report_id)
  val=validate(self.f.root,submission_id=sub["submission_id"])
  review(self.f.root,submission_id=sub["submission_id"],validation_id=val["validation_id"],decision="PASS",findings=[{"finding_id":"f","severity":"PASS","code":"reviewed","evidence":[{"detail":"frozen diff"}]}])
  self.assertEqual(check_conditions(self.f.root,submission_id=sub["submission_id"])["result"],"PASS")
 @patch("scripts.agents.local_codex_runtime.discover")
 def test_public_cli(self,m):
  from scripts.acceptance.__main__ import main
  m.side_effect=runtimes(PRODUCER_SESSION,VALIDATOR_SESSION,REVIEWER_SESSION);findings=self.f.write("tmp/findings.json",json.dumps([{"finding_id":"f","severity":"PASS","code":"ok","evidence":[{"detail":"review"}]}]))
  def run(args):
   out=io.StringIO()
   with contextlib.redirect_stdout(out):self.assertEqual(main(args),0)
   return json.loads(out.getvalue())
  sub=run(["submit","--repo-root",str(self.f.root),"--task-id","LF-TSK-TEST-0001","--change-report-id",self.f.report_id,"--confirm-scope-report-id",self.f.report_id]);val=run(["validate","--repo-root",str(self.f.root),"--submission-id",sub["submission_id"]]);run(["review","--repo-root",str(self.f.root),"--submission-id",sub["submission_id"],"--validation-id",val["validation_id"],"--findings-json",str(findings),"--decision","PASS"]);self.assertEqual(run(["check","--repo-root",str(self.f.root),"--submission-id",sub["submission_id"]])["result"],"PASS")
