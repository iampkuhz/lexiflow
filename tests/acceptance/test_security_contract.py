from __future__ import annotations
import json,os,tempfile,unittest,uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import yaml
from scripts.acceptance.authority import verify_authority
from scripts.acceptance.check import check_conditions
from scripts.acceptance.records import RecordError,acceptance_locator,content_hash,list_layer,list_submissions,load_layer,load_submission,publish_json,read_json
from scripts.acceptance.producer import resolve_producer
from scripts.acceptance.validate import ValidationError, _verify_producer
from tests.acceptance.fixtures import AcceptanceFixture,PRODUCER_SESSION,mock_authority,make_mock_runtime
class TestRecordSafety(unittest.TestCase):
 def test_concurrent_publication_has_one_winner(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);rid=str(uuid.uuid4());locator=acceptance_locator("submissions",rid)
   def write(i):
    try:publish_json(root,locator,{"writer":i});return "PASS"
    except RecordError as exc:return exc.code
   with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(write,(1,2)))
   self.assertEqual(results.count("PASS"),1);self.assertEqual(results.count("immutable-record-exists"),1)
 def test_duplicate_keys_symlink_and_fifo_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);path=root/"x.json";path.write_text('{"a":1,"a":2}')
   with self.assertRaisesRegex(RecordError,"duplicate JSON key"):read_json(root,"x.json","fixture",require_hash=False)
   path.unlink();outside=root/"outside";outside.write_text("x");path.symlink_to(outside)
   with self.assertRaisesRegex(RecordError,"unsafe-path"):read_json(root,"x.json","fixture",require_hash=False)
   path.unlink()
   if hasattr(os,"mkfifo"):
    os.mkfifo(path)
    with self.assertRaisesRegex(RecordError,"unsafe-path"):read_json(root,"x.json","fixture",require_hash=False)
 def test_dangling_record_directories_are_not_treated_as_empty(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);base=root/"tmp/quality/acceptance";base.mkdir(parents=True)
   (base/"submissions").symlink_to(root/"missing-submissions")
   with self.assertRaisesRegex(RecordError,"unsafe-path"):list_submissions(root)
   (base/"validations").symlink_to(root/"missing-validations")
   with self.assertRaisesRegex(RecordError,"unsafe-path"):list_layer(root,"validations","submission_id",str(uuid.uuid4()))
 def test_publication_does_not_follow_existing_parent_symlink(self):
  with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as outside:
   root=Path(tmp);(root/"tmp").symlink_to(Path(outside));rid=str(uuid.uuid4())
   with self.assertRaisesRegex(RecordError,"unsafe-path"):publish_json(root,acceptance_locator("submissions",rid),{"writer":1})
   self.assertEqual(list(Path(outside).iterdir()),[])
 def test_link_side_effect_error_rolls_back_owned_final(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/"tmp/quality/acceptance/submissions").mkdir(parents=True);rid=str(uuid.uuid4());locator=acceptance_locator("submissions",rid);real_link=os.link
   def link_then_error(*args,**kwargs):real_link(*args,**kwargs);raise OSError("injected after link")
   with patch("scripts.acceptance.records.os.link",side_effect=link_then_error):
    with self.assertRaisesRegex(RecordError,"publication-failed"):publish_json(root,locator,{"writer":1})
   self.assertFalse((root/locator).exists());publish_json(root,locator,{"writer":1})
class TestAuthoritySemantics(unittest.TestCase):
 def test_historical_receipt_has_no_synthetic_ttl_but_source_must_reverify(self):
  runtime=make_mock_runtime(PRODUCER_SESSION);record={"producer_identity":runtime.context,"runtime_proof":runtime.proof,"authority":mock_authority(PRODUCER_SESSION)}
  with patch("scripts.agents.local_codex_runtime.verify_proof",return_value=None):self.assertIsNone(verify_authority(Path("/tmp"),record,"producer_identity"))
  from scripts.agents.local_codex_runtime import CodexRuntimeError
  with patch("scripts.agents.local_codex_runtime.verify_proof",side_effect=CodexRuntimeError("runtime-proof-drift","stale source")):
   self.assertEqual(verify_authority(Path("/tmp"),record,"producer_identity"),"producer_identity-source-runtime-proof-drift")
class TestProducerFacts(unittest.TestCase):
 def test_qoder_run_reference_preserves_worker_identity_and_is_hash_bound(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);run_id=str(uuid.uuid4());base=root/f"tmp/qoder-tasks/{run_id}";base.mkdir(parents=True)
   task={"run_id":run_id,"task_ids":["LF-TSK-TEST-0001"],"task_versions":{"LF-TSK-TEST-0001":1},"change_versions":{"LF-TSK-TEST-0001":"1.0.0"},"parent_session_id":"parent","agent_id":"qoder-worker","session_id":"qoder-session","client":"qoder","parent_client":"codex"}
   task.update({"task_id":"LF-TSK-TEST-0001","task_version":1,"change_version":"1.0.0","work_package_id":"TEST-WP"})
   completion={**{k:task[k] for k in ("run_id","work_package_id","task_id","task_ids","task_version","change_version","task_versions","change_versions","parent_session_id","agent_id","session_id","client","parent_client")},"status":"finished","exit_code":0,"qoder_exit_code":0}
   result={"schema_version":"lexiflow.qoder-work-package-result.v1","run_id":run_id,"work_package_id":"TEST-WP","task_ids":task["task_ids"],"task_versions":task["task_versions"],"change_versions":task["change_versions"],"status":"PASS","outcomes":[{"task_id":"LF-TSK-TEST-0001","status":"PASS","acceptance_evidence":[]}],"changed_files":[],"validation":[],"acceptance_evidence":[],"effect_checks":[],"risks":[]}
   for name,value in (("task.json",task),("completion.json",completion),("result.json",result)):(base/name).write_text(json.dumps(value))
   req={"task_id":"LF-TSK-TEST-0001","task_version":1,"change_version":"1.0.0"};submitter={"identity":make_mock_runtime(PRODUCER_SESSION).context,"proof":{},"authority":{}}
   producer=resolve_producer(root,req,submitter,run_id);self.assertEqual(producer["identity"]["agent_id"],"qoder-worker")
   submission={"producer":producer};(base/"result.json").write_text(json.dumps({**result,"status":"FAIL"}))
   with self.assertRaisesRegex(ValidationError,"producer-source-drift"):_verify_producer(root,submission)
class TestDependencyDagAndApproval(unittest.TestCase):
 def setUp(self):
  self.f=AcceptanceFixture();self.f.create_verification_report();self._catalog()
 def tearDown(self):self.f.cleanup()
 def _catalog(self):
  def task(tid,deps=None,approval=None):
   value={"id":tid,"version":1,"change_version":"1.0.0","dependencies":deps or [],"required_check_ids":["fixture.change"],"allowed_files":["src/test.py"],"forbidden_files":[],"file_claims":[{"path":"src/**"}]}
   if approval:value["phase_entry_prerequisite"]={"previous_gate_id":"G1","previous_gate_exit_task_id":approval,"required_user_approval":"APPROVED"}
   return value
  tasks=[task("LF-TSK-TEST-0001"),task("LF-TSK-TEST-0002",["LF-TSK-TEST-0001"]),task("LF-TSK-TEST-0003",["LF-TSK-TEST-0002"],"LF-TSK-TEST-0002")]
  self.f.write("planning/workstreams.yaml",yaml.safe_dump({"workstreams":[{"id":"LF-WS-TEST","epics":[{"capabilities":[{"seed_tasks":tasks}]}]}]},sort_keys=False))
 def _submission(self,tid):
  from scripts.acceptance.records import publish_bytes,sha256_bytes
  from scripts.acceptance.requirements import load_task_requirements
  from scripts.verification import freeze_inputs,read_report
  req=load_task_requirements(self.f.root,tid);report,desc=read_report(self.f.root,self.f.report_id);freeze=freeze_inputs(self.f.root,required_check_ids=req["required_check_ids"]);sid=str(uuid.uuid4());runtime=make_mock_runtime(PRODUCER_SESSION);auth=mock_authority(PRODUCER_SESSION);patch_desc=publish_bytes(self.f.root,f"tmp/quality/acceptance/submissions/{sid}.diff.patch",b"x")
  snaps={x:{"state":"present","sha256":sha256_bytes((self.f.root/x).read_bytes())} for x in report["scope_review"]["changed_files"]}
  record={"schema_version":"lexiflow.acceptance-submission.v4","submission_id":sid,"task_requirements":req,"change_report":desc,"scope_confirmation":self.f.report_id,"scope_base":"HEAD","diff":patch_desc,"submitter_identity":runtime.context,"runtime_proof":runtime.proof,"authority":auth,"producer":{"kind":"current-codex-task","identity":runtime.context,"runtime_proof":runtime.proof,"authority":auth},"verification_freeze":freeze,"changed_file_snapshots":snaps,"created_at":"2026-09-21T00:00:00Z"}
  publish_json(self.f.root,acceptance_locator("submissions",sid),record);self.f.submission_id=sid;self.f.create_validation();self.f.create_review();return sid
 def test_multilevel_hash_update_and_explicit_approval(self):
  a=self._submission("LF-TSK-TEST-0001");ca=check_conditions(self.f.root,submission_id=a);self.assertEqual(ca["result"],"PASS")
  b=self._submission("LF-TSK-TEST-0002");cb=check_conditions(self.f.root,submission_id=b);self.assertEqual(cb["result"],"PASS")
  c1=self._submission("LF-TSK-TEST-0003");missing=check_conditions(self.f.root,submission_id=c1);self.assertEqual((missing["result"],missing["reason"],missing["published"]),("BLOCKED","user-approval-missing",False));self.assertEqual(list_layer(self.f.root,"checks","submission_id",c1),[])
  approval={"schema_version":"lexiflow.user-approval.v1","approval_id":str(uuid.uuid4()),"gate_id":"G1","decision":"APPROVED","subject_task_id":"LF-TSK-TEST-0002","subject_task_version":1,"subject_change_version":"1.0.0","subject_check_content_hash":cb["content_hash"],"statement":"User explicitly approved G1","approved_at":"2026-09-21T00:00:00Z"}
  publish_json(self.f.root,"tmp/quality/acceptance/approvals/G1.json",approval)
  approved=check_conditions(self.f.root,submission_id=c1);self.assertEqual(approved["result"],"PASS");again=check_conditions(self.f.root,submission_id=c1);self.assertEqual((again["check_id"],again["idempotent"]),(approved["check_id"],True))
  # A validly re-hashed mutation changes the transitive edge and invalidates B.
  path=self.f.root/acceptance_locator("checks",ca["check_id"]);value=json.loads(path.read_text());value["reason"]="changed";value["content_hash"]=content_hash(value);path.chmod(0o600);path.write_text(json.dumps(value,separators=(",",":"),sort_keys=True));path.chmod(0o400)
  self.assertEqual(check_conditions(self.f.root,submission_id=c1)["reason"],"dependency-not-pass")
 def test_missing_dependency_can_be_added_then_same_submission_rechecked(self):
  dependent=self._submission("LF-TSK-TEST-0002");first=check_conditions(self.f.root,submission_id=dependent);self.assertEqual((first["result"],first["reason"],first["published"]),("BLOCKED","dependency-not-pass",False));self.assertEqual(list_layer(self.f.root,"checks","submission_id",dependent),[])
  dependency=self._submission("LF-TSK-TEST-0001");self.assertEqual(check_conditions(self.f.root,submission_id=dependency)["result"],"PASS")
  self.assertEqual(check_conditions(self.f.root,submission_id=dependent)["result"],"PASS")
if __name__=="__main__":unittest.main()
