from __future__ import annotations
import json,tempfile,unittest,uuid
from pathlib import Path
from scripts.agents.qoder import QoderFactsError,verify_qoder_work_package_facts
class TestQoderFacts(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.run_id=str(uuid.uuid4());self.run=self.root/f"tmp/qoder-tasks/{self.run_id}";self.run.mkdir(parents=True);self.tid="LF-TSK-ENG-0007"
  self.task={"run_id":self.run_id,"work_package_id":"ENG-WP-04","task_id":self.tid,"task_ids":[self.tid,"LF-TSK-ENG-0008"],"task_version":1,"change_version":"1.0.0","task_versions":{self.tid:1,"LF-TSK-ENG-0008":1},"change_versions":{self.tid:"1.0.0","LF-TSK-ENG-0008":"1.0.0"},"parent_session_id":str(uuid.uuid4()),"agent_id":"agent_qoder","session_id":str(uuid.uuid4()),"client":"qoder","parent_client":"codex"}
  self.completion={**{k:self.task[k] for k in ("run_id","work_package_id","task_id","task_ids","task_version","change_version","task_versions","change_versions","parent_session_id","agent_id","session_id","client","parent_client")},"status":"finished","exit_code":0,"qoder_exit_code":0}
  outcomes=[{"task_id":x,"status":"PASS","acceptance_evidence":[]} for x in self.task["task_ids"]]
  self.result={"schema_version":"lexiflow.qoder-work-package-result.v1","status":"PASS","work_package_id":"ENG-WP-04","task_ids":self.task["task_ids"],"task_versions":self.task["task_versions"],"change_versions":self.task["change_versions"],"run_id":self.run_id,"outcomes":outcomes,"changed_files":[],"validation":[],"acceptance_evidence":[],"effect_checks":[],"risks":[]}
  self.write()
 def tearDown(self):self.temp.cleanup()
 def write(self):
  for name,value in (("task.json",self.task),("completion.json",self.completion),("result.json",self.result)):(self.run/name).write_text(json.dumps(value))
 def test_real_finished_shape_returns_raw_facts(self):
  value=verify_qoder_work_package_facts(self.root,self.run_id);self.assertEqual(value["terminal_status"],"finished");self.assertEqual(value["result_status"],"PASS");self.assertNotIn("result",value)
 def test_completion_and_result_identity_are_exactly_bound(self):
  for target,field,bad in (("completion","task_versions",{}),("completion","run_id",str(uuid.uuid4())),("result","change_versions",{}),("result","run_id",str(uuid.uuid4()))):
   with self.subTest(target=target,field=field):
    value=getattr(self,target);old=value[field];value[field]=bad;self.write()
    with self.assertRaises(QoderFactsError):verify_qoder_work_package_facts(self.root,self.run_id)
    value[field]=old
 def test_missing_identity_failed_or_exit_zero_without_result_never_valid(self):
  del self.completion["agent_id"];self.write()
  with self.assertRaises(QoderFactsError):verify_qoder_work_package_facts(self.root,self.run_id)
  self.completion["agent_id"]=self.task["agent_id"];self.completion["status"]="failed";self.write()
  with self.assertRaisesRegex(QoderFactsError,"not-success"):verify_qoder_work_package_facts(self.root,self.run_id)
  self.completion["status"]="finished";(self.run/"result.json").unlink()
  with self.assertRaises(QoderFactsError):verify_qoder_work_package_facts(self.root,self.run_id)
 def test_internal_task_versions_and_aggregate_result_must_be_consistent(self):
  self.task["task_version"]=2;self.write()
  with self.assertRaisesRegex(QoderFactsError,"primary versions"):verify_qoder_work_package_facts(self.root,self.run_id)
  self.task["task_version"]=1;self.result["outcomes"][1]["status"]="FAIL";self.write()
  with self.assertRaisesRegex(QoderFactsError,"aggregate status"):verify_qoder_work_package_facts(self.root,self.run_id)
  self.result["outcomes"][1]["status"]="PASS";self.completion["exit_code"]=False;self.write()
  with self.assertRaisesRegex(QoderFactsError,"not-success"):verify_qoder_work_package_facts(self.root,self.run_id)
if __name__=="__main__":unittest.main()
