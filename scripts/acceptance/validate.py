"""Validate an immutable submission with the verification public API."""
from __future__ import annotations
from datetime import UTC,datetime
from pathlib import Path
from typing import Any
import uuid
from scripts.acceptance.authority import discover_authority,verify_authority
from scripts.acceptance.records import RecordError,acceptance_locator,canonical_bytes,content_hash,list_layer,load_submission,publish_bytes,publish_json,read_bound_bytes,read_optional_bytes,sha256_bytes
from scripts.acceptance.requirements import requirements_are_current
from scripts.verification import freeze_inputs,verify_repository
class ValidationError(ValueError):
 def __init__(self,code:str,detail:str)->None:self.code,self.detail=code,detail;super().__init__(f"{code}: {detail}")
def _discover_runtime(repo:Path)->dict[str,Any]:return discover_authority(repo,ValidationError)
def _identity_values(identity:dict[str,Any])->set[str]:return {x for x in (identity.get("actor_id"),identity.get("agent_id"),identity.get("session_id")) if isinstance(x,str) and x}
def _verify_independence(submission:dict[str,Any],runtime:dict[str,Any])->None:
 validator=_identity_values(runtime["identity"]);subjects=[]
 for label,identity in (("submitter",submission.get("submitter_identity")),("producer",submission.get("producer",{}).get("identity"))):
  if not isinstance(identity,dict) or not _identity_values(identity):raise ValidationError(f"{label}-identity-invalid",label)
  subjects.extend(_identity_values(identity))
 if validator.intersection(subjects):raise ValidationError("self-validation-forbidden","validator identity overlaps producer/submitter")
def _verify_producer(repo:Path,submission:dict[str,Any])->None:
 producer=submission.get("producer")
 if not isinstance(producer,dict):raise ValidationError("producer-source-invalid","producer missing")
 if producer.get("kind")=="current-codex-task":
  synthetic={"producer_identity":producer.get("identity"),"runtime_proof":producer.get("runtime_proof"),"authority":producer.get("authority")}
  error=verify_authority(repo,synthetic,"producer_identity")
  if error:raise ValidationError(error,"producer")
 elif producer.get("kind")=="codex-work-package":
  desc=producer.get("completion",{})
  try:read_bound_bytes(repo,desc["locator"],desc["sha256"])
  except (KeyError,RecordError) as exc:raise ValidationError("producer-source-drift",str(exc)) from None
 elif producer.get("kind")=="qoder-work-package":
  artifacts=producer.get("artifacts")
  if not isinstance(artifacts,dict) or set(artifacts)!={"task","completion","result"}:raise ValidationError("producer-source-invalid","qoder artifacts")
  for desc in artifacts.values():
   try:read_bound_bytes(repo,desc["locator"],desc["sha256"])
   except (KeyError,RecordError) as exc:raise ValidationError("producer-source-drift",str(exc)) from None
 else:raise ValidationError("producer-source-invalid",str(producer.get("kind")))
def _verify_frozen(repo:Path,submission:dict[str,Any])->dict[str,Any]:
 auth=verify_authority(repo,submission,"submitter_identity")
 if auth:raise ValidationError(auth,"submission")
 _verify_producer(repo,submission)
 freeze=submission.get("verification_freeze")
 if not isinstance(freeze,dict) or freeze.get("result")!="PASS":raise ValidationError("frozen-inputs-invalid","submission has no complete verification freeze")
 if not requirements_are_current(repo,submission.get("task_requirements")):raise ValidationError("task-requirements-drift","task source/version/dependencies changed")
 required=submission["task_requirements"].get("required_check_ids")
 if not isinstance(required,list):raise ValidationError("frozen-inputs-invalid","task required checks missing")
 current_freeze=freeze_inputs(repo,required_check_ids=required)
 if current_freeze.get("result")!="PASS" or current_freeze.get("input_fingerprint")!=freeze.get("input_fingerprint"):raise ValidationError("frozen-input-drift",str(current_freeze.get("reason","input closure changed")))
 for label in ("change_report","diff"):
  desc=submission.get(label)
  try:read_bound_bytes(repo,desc["locator"],desc["sha256"])
  except (KeyError,TypeError,RecordError) as exc:raise ValidationError("frozen-artifact-drift",label) from None
 snapshots=submission.get("changed_file_snapshots")
 if not isinstance(snapshots,dict):raise ValidationError("snapshot-invalid","changed_file_snapshots")
 for relative,expected in snapshots.items():
  if not isinstance(relative,str) or not isinstance(expected,dict):raise ValidationError("snapshot-invalid",str(relative))
  try:data=read_optional_bytes(repo,relative)
  except RecordError as exc:raise ValidationError(exc.code,exc.detail) from None
  if expected.get("state")=="absent":
   if data is not None:raise ValidationError("file-tampered",relative)
  elif expected.get("state")=="present":
   if data is None or sha256_bytes(data)!=expected.get("sha256"):raise ValidationError("file-tampered",relative)
  else:raise ValidationError("snapshot-invalid",relative)
 return freeze
def _gaps(report:dict[str,Any])->list[dict[str,str]]:
 checks=report.get("checks")
 if not isinstance(checks,list) or not checks:return [{"check_id":"verification","kind":"not-run","reason":"no-checks"}]
 values=[]
 for check in checks:
  if not isinstance(check,dict):values.append({"check_id":"unknown","kind":"invalid","reason":"non-object"});continue
  status=check.get("status");process=check.get("process",{})
  if status!="PASS":values.append({"check_id":str(check.get("check_id","unknown")),"kind":str(status or "invalid").lower(),"reason":str(check.get("reason","not-pass"))})
  if not isinstance(process,dict) or process.get("exit_reason") in {"not-run","skipped","queued","unavailable"}:values.append({"check_id":str(check.get("check_id","unknown")),"kind":"not-run","reason":str(process.get("exit_reason","missing")) if isinstance(process,dict) else "invalid"})
 for gap in report.get("coverage_gaps",[]):values.append({"check_id":str(gap),"kind":"coverage-gap","reason":str(gap)})
 return values
def validate(root:str|Path,*,submission_id:str)->dict[str,Any]:
 repo=Path(root).resolve()
 try:submission=load_submission(repo,submission_id)
 except RecordError as exc:raise ValidationError(exc.code,exc.detail) from None
 runtime=_discover_runtime(repo);_verify_independence(submission,runtime);freeze=_verify_frozen(repo,submission)
 try:
  if list_layer(repo,"validations","submission_id",submission_id):raise ValidationError("validation-already-published",submission_id)
 except RecordError as exc:raise ValidationError(exc.code,exc.detail) from None
 report=verify_repository(repo,required_check_ids=submission["task_requirements"]["required_check_ids"],frozen_inputs=freeze)
 if report.get("frozen_input_fingerprint")!=freeze["input_fingerprint"]:raise ValidationError("frozen-input-mismatch","verification did not consume submitted closure")
 # Recheck submission artifacts and producer source after command execution.
 _verify_frozen(repo,submission)
 gaps=_gaps(report);result="PASS" if report.get("result")=="PASS" and not gaps else ("FAIL" if report.get("result")=="FAIL" else "BLOCKED")
 validation_id=str(uuid.uuid4());created=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00","Z")
 try:
  report_descriptor=publish_bytes(repo,f"tmp/quality/acceptance/validations/{validation_id}/report.json",canonical_bytes(report))
  record={"schema_version":"lexiflow.acceptance-validation.v3","validation_id":validation_id,"submission_id":submission_id,"submission_content_hash":submission["content_hash"],"validator_identity":runtime["identity"],"runtime_proof":runtime["proof"],"authority":runtime["authority"],"verification_report":report_descriptor,"frozen_input_fingerprint":freeze["input_fingerprint"],"gaps":gaps,"result":result,"created_at":created}
  published=publish_json(repo,acceptance_locator("validations",validation_id),record)
 except RecordError as exc:raise ValidationError(exc.code,exc.detail) from None
 return {"validation_id":validation_id,"submission_id":submission_id,"result":result,"content_hash":content_hash(record),"record_sha256":published["sha256"],"gaps":gaps}
