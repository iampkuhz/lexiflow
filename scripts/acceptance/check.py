"""Read-only acceptance condition evaluation; it never runs delivery commands."""
from __future__ import annotations
import re,uuid
from datetime import UTC,datetime
from pathlib import Path
from typing import Any
from scripts.acceptance.authority import verify_authority
from scripts.acceptance.records import RecordError,acceptance_locator,canonical_bytes,content_hash,list_layer,list_submissions,load_submission,publish_json,read_bound_bytes,read_json,sha256_bytes
from scripts.acceptance.requirements import load_task_requirements
class CheckError(ValueError):
 def __init__(self,code:str,detail:str)->None:self.code,self.detail=code,detail;super().__init__(f"{code}: {detail}")
def _one(records:list[dict[str,Any]],kind:str,subject:str)->dict[str,Any]|None:
 if not records:return None
 if len(records)!=1:raise CheckError("ambiguous-receipt",f"{kind} has {len(records)} records for {subject}")
 return records[0]
def _bound_chain(repo:Path,submission:dict[str,Any],validation:dict[str,Any],review:dict[str,Any])->list[str]:
 errors=[]
 if validation.get("submission_content_hash")!=submission.get("content_hash"):errors.append("validation-submission-hash")
 if review.get("submission_content_hash")!=submission.get("content_hash"):errors.append("review-submission-hash")
 if review.get("validation_content_hash")!=validation.get("content_hash"):errors.append("review-validation-hash")
 if validation.get("frozen_input_fingerprint")!=submission.get("verification_freeze",{}).get("input_fingerprint"):errors.append("validation-input-fingerprint")
 for record,key in ((validation,"validator_identity"),(review,"reviewer_identity")):
  error=verify_authority(repo,record,key)
  if error:errors.append(error)
 try:
  descriptor=validation["verification_report"];read_bound_bytes(repo,descriptor["locator"],descriptor["sha256"])
 except (KeyError,TypeError,RecordError):errors.append("validation-evidence-drift")
 if validation.get("result")!="PASS":errors.append("validation-not-pass")
 if review.get("result")!="PASS":errors.append("review-not-pass")
 return errors
def _submissions(repo:Path,task_id:str,version:int|None,change:str|None)->list[dict[str,Any]]:
 result=[]
 try:submissions=list_submissions(repo)
 except RecordError as exc:raise CheckError("dependency-record-invalid",str(exc)) from None
 for submission in submissions:
  req=submission.get("task_requirements",{})
  if req.get("task_id")==task_id and (version is None or req.get("task_version")==version) and (change is None or req.get("change_version")==change):result.append(submission)
 return result
def _records(repo:Path,submission:dict[str,Any])->tuple[dict[str,Any]|None,dict[str,Any]|None,dict[str,Any]|None]:
 sid=submission["submission_id"]
 validation=_one(list_layer(repo,"validations","submission_id",sid),"validation",sid)
 review=_one(list_layer(repo,"reviews","submission_id",sid),"review",sid)
 check=_one(list_layer(repo,"checks","submission_id",sid),"check",sid)
 return validation,review,check
def _dependency_status(repo:Path,submission:dict[str,Any],visiting:set[str]|None=None)->tuple[bool,list[dict[str,Any]]]:
 visiting=set() if visiting is None else visiting;requirements=submission.get("task_requirements",{});task_id=requirements.get("task_id","")
 if task_id in visiting:return False,[{"task_id":task_id,"status":"dependency-cycle"}]
 try:current=load_task_requirements(repo,task_id)
 except RecordError as exc:return False,[{"task_id":task_id,"status":exc.code}]
 if current!=requirements:return False,[{"task_id":task_id,"status":"task-requirements-drift"}]
 details=[]
 for edge in requirements.get("dependencies",[]):
  dep_id=edge["task_id"] if isinstance(edge,dict) else edge;version=edge.get("required_task_version") if isinstance(edge,dict) else None;change=edge.get("required_change_version") if isinstance(edge,dict) else None
  candidates=_submissions(repo,dep_id,version,change)
  if len(candidates)!=1:details.append({"task_id":dep_id,"status":"missing" if not candidates else "ambiguous"});continue
  dep=candidates[0]
  try:validation,review,check=_records(repo,dep)
  except (RecordError,CheckError):details.append({"task_id":dep_id,"status":"receipt-invalid"});continue
  child_ok,child_details=_dependency_status(repo,dep,visiting|{task_id})
  current_errors=_bound_chain(repo,dep,validation,review) if validation and review else ["receipt-missing"]
  expected_nested=(check or {}).get("conditions",{}).get("dependency_receipts")
  actual_nested=[{"task_id":x["task_id"],"submission_content_hash":x["submission_content_hash"],"check_content_hash":x["check_content_hash"]} for x in child_details if x.get("status")=="PASS"]
  if not validation or not review or not check or check.get("result")!="PASS" or current_errors or not child_ok or expected_nested!=actual_nested:
   details.append({"task_id":dep_id,"status":"not-pass","nested":child_details});continue
  details.append({"task_id":dep_id,"status":"PASS","task_version":dep["task_requirements"]["task_version"],"change_version":dep["task_requirements"]["change_version"],"submission_content_hash":dep["content_hash"],"check_content_hash":check["content_hash"],"nested":child_details})
 return all(x["status"]=="PASS" for x in details),details
def _approval(repo:Path,submission:dict[str,Any],dependencies:list[dict[str,Any]])->tuple[bool,dict[str,Any]]:
 requirement=submission["task_requirements"].get("approval_requirement")
 if requirement is None:return True,{"required":False}
 gate=requirement.get("previous_gate_id")
 if not isinstance(gate,str) or not re.fullmatch(r"G[1-9][0-9]*",gate):return False,{"required":True,"status":"invalid-requirement"}
 locator=f"tmp/quality/acceptance/approvals/{gate}.json"
 try:record=read_json(repo,locator,"user approval")
 except RecordError as exc:return False,{"required":True,"status":exc.code,"locator":locator}
 expected={"schema_version","approval_id","gate_id","decision","subject_task_id","subject_task_version","subject_change_version","subject_check_content_hash","statement","approved_at","content_hash"}
 dep_pass=next((x for x in dependencies if x.get("status")=="PASS" and x.get("task_id")==requirement.get("subject_task_id")),None)
 valid=set(record)==expected and record.get("schema_version")=="lexiflow.user-approval.v1" and record.get("gate_id")==gate and record.get("decision")=="APPROVED" and isinstance(record.get("statement"),str) and bool(record["statement"].strip()) and dep_pass is not None and record.get("subject_task_id")==dep_pass["task_id"] and record.get("subject_task_version")==dep_pass["task_version"] and record.get("subject_change_version")==dep_pass["change_version"] and record.get("subject_check_content_hash")==dep_pass["check_content_hash"]
 return valid,{"required":True,"status":"PASS" if valid else "approval-mismatch","locator":locator,"content_hash":record.get("content_hash")}
def check_conditions(root:str|Path,*,submission_id:str)->dict[str,Any]:
 repo=Path(root).resolve()
 try:
  submission=load_submission(repo,submission_id)
  # Current subject bytes/source must still match, but this does not execute.
  from scripts.acceptance.validate import _verify_frozen
  _verify_frozen(repo,submission)
  validation=_one(list_layer(repo,"validations","submission_id",submission_id),"validation",submission_id)
  if validation is None:raise CheckError("validation-missing",submission_id)
  review=_one(list_layer(repo,"reviews","validation_id",validation["validation_id"]),"review",validation["validation_id"])
  if review is None:raise CheckError("review-missing",validation["validation_id"])
  existing=_one(list_layer(repo,"checks","submission_id",submission_id),"check",submission_id)
  chain_errors=_bound_chain(repo,submission,validation,review);dependencies_ok,dependency_details=_dependency_status(repo,submission);approval_ok,approval=_approval(repo,submission,dependency_details)
  result="PASS" if not chain_errors and dependencies_ok and approval_ok else "FAIL" if chain_errors else "BLOCKED";reason="" if result=="PASS" else (chain_errors[0] if chain_errors else "dependency-not-pass" if not dependencies_ok else "user-approval-missing")
 except RecordError as exc:return {"submission_id":submission_id,"result":"BLOCKED","reason":exc.code,"detail":exc.detail,"delivery_rerun":False}
 except CheckError as exc:return {"submission_id":submission_id,"result":"BLOCKED","reason":exc.code,"detail":exc.detail,"delivery_rerun":False}
 except Exception as exc:return {"submission_id":submission_id,"result":"BLOCKED","reason":getattr(exc,"code","current-input-invalid"),"detail":str(exc),"delivery_rerun":False}
 receipts=[{"task_id":x["task_id"],"submission_content_hash":x["submission_content_hash"],"check_content_hash":x["check_content_hash"]} for x in dependency_details if x["status"]=="PASS"];conditions={"hash_dag_errors":chain_errors,"dependencies_ok":dependencies_ok,"dependency_receipts":receipts,"dependency_details":dependency_details,"user_approval":approval}
 if result!="PASS":return {"submission_id":submission_id,"result":result,"reason":reason,"conditions":conditions,"published":False,"delivery_rerun":False}
 if existing is not None:
  expected={"submission_id":submission_id,"validation_id":validation["validation_id"],"review_id":review["review_id"],"submission_content_hash":submission["content_hash"],"validation_content_hash":validation["content_hash"],"review_content_hash":review["content_hash"],"result":"PASS","reason":"","conditions":conditions,"delivery_rerun":False}
  if any(existing.get(k)!=v for k,v in expected.items()):return {"submission_id":submission_id,"result":"BLOCKED","reason":"existing-check-mismatch","published":False,"delivery_rerun":False}
  return {"check_id":existing["check_id"],"submission_id":submission_id,"result":"PASS","reason":"","content_hash":existing["content_hash"],"record_sha256":sha256_bytes(canonical_bytes(existing)),"conditions":conditions,"published":True,"idempotent":True,"delivery_rerun":False}
 check_id=str(uuid.uuid4());record={"schema_version":"lexiflow.acceptance-check.v3","check_id":check_id,"submission_id":submission_id,"validation_id":validation["validation_id"],"review_id":review["review_id"],"submission_content_hash":submission["content_hash"],"validation_content_hash":validation["content_hash"],"review_content_hash":review["content_hash"],"result":"PASS","reason":"","conditions":conditions,"delivery_rerun":False,"created_at":datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00","Z")}
 try:published=publish_json(repo,acceptance_locator("checks",check_id),record)
 except RecordError as exc:raise CheckError(exc.code,exc.detail) from None
 return {"check_id":check_id,"submission_id":submission_id,"result":"PASS","reason":"","content_hash":content_hash(record),"record_sha256":published["sha256"],"conditions":conditions,"published":True,"idempotent":False,"delivery_rerun":False}
