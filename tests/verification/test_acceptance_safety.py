from __future__ import annotations
import json,os,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
import yaml
from scripts.verification import freeze_inputs,persist_report,read_report,verify_repository

def check(cid,scope="repository-baseline"):
 return {"check_id":cid,"module":cid,"command":["python3","-c","pass"],"executable":"python3","cwd":".","timeout_seconds":5,"scope":scope,"triggers":[{"path":"x"}],"module_dependencies":[],"required_environment":[],"input_paths":["input.txt"],"result_contract":{"type":"exit-code","completeness_guarantee":"fixture"}}
def repo(root,checks):
 (root/"harness").mkdir();(root/"input.txt").write_text("x");(root/"harness/module-checks.yaml").write_text(yaml.safe_dump({"schema_version":"lexiflow.module-checks.v1","checks":checks}))
class TestReportPublicationSafety(unittest.TestCase):
 def report(self):return {"schema_version":"lexiflow.verification-report.v1","run_id":str(uuid.uuid4()),"result":"FAIL","reason":"fixture-diagnostic","scope":"change-targeted","checks":[],"input_fingerprint":"","configuration_fingerprint":"","coverage_gaps":["fixture"],"scope_review":{"kind":"error","checks_declared":0,"checks_executed":0}}
 def test_atomic_immutable_publish_and_duplicate_json_read(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);value=self.report();descriptor=persist_report(root,value)
   with self.assertRaisesRegex(ValueError,"already published"):persist_report(root,value)
   path=root/descriptor["locator"];path.chmod(0o600);path.write_text('{"run_id":"%s","run_id":"%s","result":"PASS"}'%(value["run_id"],value["run_id"]));path.chmod(0o400)
   with self.assertRaisesRegex(ValueError,"duplicate"):read_report(root,value["run_id"])
 def test_minimal_fabricated_pass_cannot_be_persisted_or_read(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);rid=str(uuid.uuid4());value={"schema_version":"lexiflow.verification-report.v1","run_id":rid,"result":"PASS","reason":"","scope":"change-targeted","checks":[],"input_fingerprint":"","configuration_fingerprint":"","coverage_gaps":[],"scope_review":{"kind":"no-context","changed_files":[]},"base":"HEAD"}
   with self.assertRaisesRegex(ValueError,"declaration|coverage"):persist_report(root,value)
   path=root/f"tmp/quality/verification-reports/{rid}.json";path.parent.mkdir(parents=True);path.write_text(json.dumps(value))
   with self.assertRaisesRegex(ValueError,"invalid"):read_report(root,rid)
 def test_dangling_symlink_and_fifo_are_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/"tmp/quality").mkdir(parents=True);(root/"tmp/quality/verification-reports").symlink_to(root/"missing")
   with self.assertRaisesRegex(ValueError,"unsafe"):persist_report(root,self.report())
  if hasattr(os,"mkfifo"):
   with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);directory=root/"tmp/quality/verification-reports";directory.mkdir(parents=True);rid=str(uuid.uuid4());os.mkfifo(directory/f"{rid}.json")
    with self.assertRaisesRegex(ValueError,"unsafe"):read_report(root,rid)
 def test_publication_does_not_follow_existing_parent_symlink(self):
  with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as outside:
   root=Path(tmp);(root/"tmp").symlink_to(Path(outside))
   with self.assertRaisesRegex(ValueError,"unsafe"):persist_report(root,self.report())
   self.assertEqual(list(Path(outside).iterdir()),[])
 def test_link_side_effect_error_rolls_back_owned_final(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/"tmp/quality/verification-reports").mkdir(parents=True);value=self.report();path=root/f"tmp/quality/verification-reports/{value['run_id']}.json";real_link=os.link
   def link_then_error(*args,**kwargs):real_link(*args,**kwargs);raise OSError("injected after link")
   with patch("scripts.verification.reports.os.link",side_effect=link_then_error):
    with self.assertRaisesRegex(ValueError,"publication failed"):persist_report(root,value)
   self.assertFalse(path.exists());persist_report(root,value)
class TestFrozenAndDedupSafety(unittest.TestCase):
 def test_malformed_freeze_returns_typed_failure(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);repo(root,[check("base")])
   for malformed in ([],{"schema_version":"lexiflow.verification-freeze.v1","result":"PASS","checks":{}},{"schema_version":"lexiflow.verification-freeze.v1","result":"PASS","checks":[None]}):
    report=verify_repository(root,frozen_inputs=malformed)
    self.assertEqual((report["result"],report["reason"]),("FAIL","frozen-input-mismatch"))
 def test_equivalent_baseline_and_required_check_execute_once(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);base=check("base");extra=check("extra","change-targeted");repo(root,[base,extra]);freeze=freeze_inputs(root,required_check_ids=["extra"]);calls=[]
   def runner(*args):
    calls.append(args);return {"exit_code":0,"exit_reason":"exited","timed_out":False,"stdout":"","stderr":"","executed_argv":["python3"]}
   report=verify_repository(root,required_check_ids=["extra"],frozen_inputs=freeze,runner=runner)
   self.assertEqual(report["result"],"PASS");self.assertEqual(len(calls),1);self.assertEqual(report["checks"][1]["process"]["exit_reason"],"deduplicated")
if __name__=="__main__":unittest.main()
