from __future__ import annotations
import contextlib,io,json,os,unittest
from unittest.mock import patch
from scripts.delivery_gate import submit,validate,review,check_conditions
from scripts.delivery_gate.status import query_status
from tests.delivery_gate.fixtures import DeliveryGateFixture,PRODUCER_SESSION,VALIDATOR_SESSION,REVIEWER_SESSION,make_mock_runtime
class TestActualFourScenarioChain(unittest.TestCase):
 def setUp(self):self.f=DeliveryGateFixture();self.f.create_verification_report()
 def tearDown(self):self.f.cleanup()
 def test_shared_session_native_metadata_full_chain_without_authority_mock(self):
  self.f.proof_patch.stop()
  root=self.f.root.resolve();home=root/'tmp/codex';sources=home/'sessions';sources.mkdir(parents=True)
  for thread,role in ((PRODUCER_SESSION,None),(VALIDATOR_SESSION,'validator'),(REVIEWER_SESSION,'reviewer')):
   meta={'id':thread,'session_id':PRODUCER_SESSION,'cwd':str(root),'originator':'fixture','source':'vscode','thread_source':'user'}
   if role:
    path='/root/'+role
    meta.update(thread_source='subagent',parent_thread_id=PRODUCER_SESSION,agent_path=path,source={'subagent':{'thread_spawn':{'parent_thread_id':PRODUCER_SESSION,'agent_path':path,'depth':1}}})
   p=sources/f'rollout-{thread}.jsonl';p.write_text(json.dumps({'type':'session_meta','payload':meta})+'\n');p.chmod(0o600)
  env={'CODEX_HOME':str(home),'CODEX_SESSION_ID':PRODUCER_SESSION}
  with patch.dict(os.environ,{**env,'CODEX_THREAD_ID':PRODUCER_SESSION}):
   sub=submit(root,task_id='LF-TSK-TEST-0001',change_report_id=self.f.report_id,confirm_scope_report_id=self.f.report_id)
  with patch.dict(os.environ,{**env,'CODEX_THREAD_ID':VALIDATOR_SESSION}):
   val=validate(root,submission_id=sub['submission_id'])
  with patch.dict(os.environ,{**env,'CODEX_THREAD_ID':REVIEWER_SESSION}):
   review(root,submission_id=sub['submission_id'],validation_id=val['validation_id'],decision='PASS',findings=[{'finding_id':'f','severity':'PASS','code':'reviewed','evidence':[{'detail':'synthetic frozen diff'}]}])
   self.assertEqual(check_conditions(root,submission_id=sub['submission_id'])['result'],'PASS')
 @patch("scripts.agents.local_codex_runtime.discover")
 def test_daily_report_submit_validate_review_check(self,m):
  m.return_value=make_mock_runtime(PRODUCER_SESSION)
  sub=submit(self.f.root,task_id="LF-TSK-TEST-0001",change_report_id=self.f.report_id,confirm_scope_report_id=self.f.report_id)
  m.return_value=make_mock_runtime(VALIDATOR_SESSION)
  val=validate(self.f.root,submission_id=sub["submission_id"])
  m.return_value=make_mock_runtime(REVIEWER_SESSION)
  review(self.f.root,submission_id=sub["submission_id"],validation_id=val["validation_id"],decision="PASS",findings=[{"finding_id":"f","severity":"PASS","code":"reviewed","evidence":[{"detail":"frozen diff"}]}])
  self.assertEqual(check_conditions(self.f.root,submission_id=sub["submission_id"])["result"],"PASS")
 @patch("scripts.agents.local_codex_runtime.discover")
 def test_public_cli(self,m):
  from scripts.delivery_gate.__main__ import main
  m.return_value=make_mock_runtime(PRODUCER_SESSION);findings=self.f.write("tmp/findings.json",json.dumps([{"finding_id":"f","severity":"PASS","code":"ok","evidence":[{"detail":"review"}]}]))
  def run(args):
   out=io.StringIO()
   with contextlib.redirect_stdout(out):self.assertEqual(main(args,root=self.f.root),0)
   return json.loads(out.getvalue())
  sub=run(["submit","--task-id","LF-TSK-TEST-0001","--change-report-id",self.f.report_id,"--confirm-scope-report-id",self.f.report_id]);m.return_value=make_mock_runtime(VALIDATOR_SESSION);val=run(["validate","--submission-id",sub["submission_id"]]);m.return_value=make_mock_runtime(REVIEWER_SESSION);run(["review","--submission-id",sub["submission_id"],"--validation-id",val["validation_id"],"--findings-json",str(findings),"--decision","PASS"]);self.assertEqual(run(["check","--submission-id",sub["submission_id"]])["result"],"PASS")
 def test_public_cli_rejects_repo_root_override(self):
  from scripts.delivery_gate.__main__ import main
  with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
   main(["status","--submission-id","11111111-2222-4333-8444-555555555555","--repo-root",str(self.f.root)])
