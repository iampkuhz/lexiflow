"""Real daily-verification fixture; only agents runtime is mocked by callers."""
from __future__ import annotations
import subprocess,tempfile,uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from datetime import UTC,datetime
from unittest.mock import patch
from scripts.delivery_gate.records import delivery_gate_locator,canonical_bytes,publish_bytes,publish_json,sha256_bytes
from scripts.delivery_gate.acceptance import acceptance_plan, binding
from scripts.verification.risk import assess
from scripts.delivery_gate.requirements import load_task_requirements
from scripts.delivery_gate.source_snapshot import review_patch
from scripts.delivery_gate.task_subject import derive_task_subject
from scripts.verification import freeze_inputs,persist_report,read_report,verify_changes,verify_profile
import yaml
PRODUCER_SESSION="11111111-1111-4111-9111-111111111111";VALIDATOR_SESSION="22222222-2222-4222-9222-222222222222";REVIEWER_SESSION="33333333-3333-4333-9333-333333333333"
PRODUCER_ACTOR=f"codex-session-{PRODUCER_SESSION}";VALIDATOR_ACTOR=f"codex-session-{VALIDATOR_SESSION}";REVIEWER_ACTOR=f"codex-session-{REVIEWER_SESSION}"
TASK_ID="LF-TSK-TEST-0001"
@dataclass
class MockRuntime: context:dict[str,str];proof:dict[str,Any]
def make_mock_runtime(session_id:str)->MockRuntime:return MockRuntime({"actor_id":f"codex-session-{session_id}","session_id":session_id,"parent_session_id":session_id,"client":"codex"},{"schema_version":"lexiflow.codex-local-session-proof.v2","thread_id":session_id,"workspace":"/test/workspace","sources":[{"thread_id":session_id,"metadata_sha256":"a"*64}]})
def mock_authority(session_id:str):
 from scripts.delivery_gate.records import canonical_bytes,sha256_bytes
 runtime=make_mock_runtime(session_id)
 return {"trust_boundary":"trusted-local-user-not-platform-cryptographic-attestation","verifier":"scripts.agents.local_codex_runtime.discover","verified_at":"2026-09-21T00:00:00Z","identity_sha256":sha256_bytes(canonical_bytes(runtime.context)),"proof_sha256":sha256_bytes(canonical_bytes(runtime.proof))}
class DeliveryGateFixture:
 def __init__(self):
  self.proof_patch=patch("scripts.agents.local_codex_runtime.verify_proof",return_value=None);self.proof_patch.start()
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.submission_id=None;self.validation_id=None;self.review_id=None;self.report_id=None
  self._write_catalog();self._write_declarations();self.write("protected.py","before\n");self.write(".gitignore","tmp/\n__pycache__/\n");self._git("init");self._git("config","user.email","fixture@example.test");self._git("config","user.name","fixture");self._git("add",".");self._git("commit","-m","base")
 def cleanup(self):self.proof_patch.stop();self.temp.cleanup()
 def _git(self,*args):return subprocess.run(["git",*args],cwd=self.root,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 def write(self,relative,content):
  p=self.root/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content.encode() if isinstance(content,str) else content);return p
 def _write_catalog(self):
  task={"id":TASK_ID,"version":1,"change_version":"1.0.0","dependencies":[],"required_check_ids":["fixture.change"],"allowed_files":["src/test.py"],"forbidden_files":["secrets/**"],"file_claims":[{"path":"src/**"}]}
  self.write("planning/workstreams.yaml",yaml.safe_dump({"workstreams":[{"id":"LF-WS-TEST","epics":[{"capabilities":[{"seed_tasks":[task]}]}]}]},sort_keys=False))
 def _write_declarations(self):
  project_root=Path(__file__).resolve().parents[2]
  self.write("harness/agent-policy.manifest.yaml",(project_root/"harness/agent-policy.manifest.yaml").read_bytes())
  self.write("harness/ci-policy.yaml",(project_root/"harness/ci-policy.yaml").read_bytes())
  self.write("fixture_check.py","import json; print(json.dumps({'status':'PASS','tests_run':1,'failures':0,'errors':0,'skipped':0}))\n")
  common={"command":["python3","fixture_check.py"],"executable":"python3","cwd":".","timeout_seconds":10,"triggers":[{"path":"src/"}],"module_dependencies":[],"required_environment":["python3"],"input_paths":["fixture_check.py","protected.py","harness/module-checks.yaml"],"result_contract":{"type":"json-stdout","required_fields":["status","tests_run","failures","errors","skipped"],"allowed_statuses":["PASS"],"minimum":{"tests_run":1},"equals":{"failures":0,"errors":0,"skipped":0}}}
  checks=[{"check_id":"fixture.baseline","module":"fixture.baseline","scope":"repository-baseline",**common},{"check_id":"fixture.change","module":"fixture.change","scope":"change-targeted",**common}]
  # Risk assessment validates every formal-only selector against declarations;
  # these fixture-only formal checks are intentionally not selected by src/ changes.
  checks.extend([
   {"check_id":"eng.release.lifecycle-runtime","module":"release-lifecycle-runtime","scope":"repository-baseline",**{**common,"triggers":[{"path":"never/fixture-lifecycle/**"}]}},
   {"check_id":"eng.release.lifecycle-runtime-on-change","module":"release-lifecycle-runtime","scope":"change-targeted",**{**common,"triggers":[{"path":"never/fixture-lifecycle/**"}]}},
   {"check_id":"eng.release.candidate-runtime","module":"release-candidate-runtime","scope":"repository-baseline",**{**common,"triggers":[{"path":"never/fixture-candidate/**"}]}},
   {"check_id":"eng.release.candidate-runtime-on-change","module":"release-candidate-runtime","scope":"change-targeted",**{**common,"triggers":[{"path":"never/fixture-candidate/**"}]}}
  ])
  self.write("harness/module-checks.yaml",yaml.safe_dump({"schema_version":"lexiflow.module-checks.v1","checks":checks},sort_keys=False))
 def create_verification_report(self,*_ ,result="PASS",changed_files=None,**__):
  self.write("src/test.py","content of src/test.py\n")
  report=verify_changes(self.root,base="HEAD")
  if result!="PASS":report={**report,"result":result}
  descriptor=persist_report(self.root,report);self.report_id=report["run_id"];return {**report,"publication":descriptor}
 def create_submission(self,**_):
  if not self.report_id:self.create_verification_report()
  requirements=load_task_requirements(self.root,TASK_ID);report,desc=read_report(self.root,self.report_id)
  full_changed=report["scope_review"]["changed_files"];subject=derive_task_subject(self.root,requirements,full_changed)
  risk=assess(self.root,base=report["base"],required_check_ids=tuple(requirements["required_check_ids"]),_subject_paths=tuple(subject["changed_files"]))
  plan=acceptance_plan(self.root,risk,requirements)
  freeze=freeze_inputs(self.root,required_check_ids=requirements["required_check_ids"],verification_scope=plan["verification_scope"],base=risk["base"],_subject_changed_files=subject["changed_files"] if plan["verification_scope"]=="development-change" else None);sid=str(uuid.uuid4());self.submission_id=sid
  changed=subject["changed_files"];snaps={x:{"state":"present","sha256":sha256_bytes((self.root/x).read_bytes())} for x in changed};patch=publish_bytes(self.root,f"tmp/quality/delivery-gate/submissions/{sid}.diff.patch",review_patch(self.root,risk["base"],changed))
  runtime=make_mock_runtime(PRODUCER_SESSION);authority=mock_authority(PRODUCER_SESSION)
  record={"schema_version":"lexiflow.delivery-gate-submission.v5","submission_id":sid,"task_requirements":requirements,"risk_assessment":risk,"acceptance_plan":plan,"change_report":desc,"scope_confirmation":self.report_id,"scope_base":"HEAD","diff":patch,"submitter_identity":runtime.context,"runtime_proof":runtime.proof,"authority":authority,"producer":{"kind":"current-codex-task","identity":runtime.context,"runtime_proof":runtime.proof,"authority":authority},"verification_freeze":freeze,"changed_file_snapshots":snaps,"task_subject":subject,"created_at":"2026-09-21T00:00:00Z"};publish_json(self.root,delivery_gate_locator("submissions",sid),record);return record
 def create_validation(self,submission_id=None,result="PASS"):
  from scripts.delivery_gate.records import load_submission
  sid=submission_id or self.submission_id;sub=load_submission(self.root,sid);vid=str(uuid.uuid4());self.validation_id=vid
  report=verify_profile(self.root,frozen_inputs=sub["verification_freeze"])
  if result!="PASS":
   report={**report,"result":result,"checks":[{**check,"status":result} for check in report.get("checks",[])]}
  desc=publish_bytes(self.root,f"tmp/quality/delivery-gate/validations/{vid}/report.json",canonical_bytes(report))
  rec={"schema_version":"lexiflow.delivery-gate-validation.v4","validation_id":vid,"submission_id":sid,"submission_content_hash":sub["content_hash"],**binding(sub),"validator_identity":make_mock_runtime(VALIDATOR_SESSION).context,"runtime_proof":make_mock_runtime(VALIDATOR_SESSION).proof,"authority":mock_authority(VALIDATOR_SESSION),"verification_report":desc,"frozen_input_fingerprint":sub["verification_freeze"]["input_fingerprint"],"gaps":[],"result":result,"created_at":"2026-09-21T00:01:00Z"};publish_json(self.root,delivery_gate_locator("validations",vid),rec);return rec
 def create_review(self,submission_id=None,validation_id=None,decision="PASS"):
  from scripts.delivery_gate.records import load_layer,load_submission
  sid,vid=submission_id or self.submission_id,validation_id or self.validation_id;sub,val=load_submission(self.root,sid),load_layer(self.root,"validations",vid);rid=str(uuid.uuid4());self.review_id=rid
  rec={"schema_version":"lexiflow.delivery-gate-review.v4","review_id":rid,"submission_id":sid,"validation_id":vid,"submission_content_hash":sub["content_hash"],**binding(sub),"validation_content_hash":val["content_hash"],"reviewer_identity":make_mock_runtime(REVIEWER_SESSION).context,"runtime_proof":make_mock_runtime(REVIEWER_SESSION).proof,"authority":mock_authority(REVIEWER_SESSION),"findings":[{"finding_id":"f","severity":decision,"code":"fixture","evidence":[{"detail":"reviewed"}]}],"result":decision,"created_at":"2026-09-21T00:02:00Z","delivery_rerun":False};publish_json(self.root,delivery_gate_locator("reviews",rid),rec);return rec
