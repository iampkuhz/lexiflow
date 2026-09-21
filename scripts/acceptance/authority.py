"""Runtime authority binding for acceptance issuers.

Authority is checked when a record is issued.  Historical records do not become
invalid after an arbitrary fifteen-minute TTL; consumers instead revalidate the
recorded local runtime proof and its immutable binding.  This is explicitly a
trusted-local-user boundary, not platform cryptographic attestation.
"""
from __future__ import annotations
from datetime import UTC,datetime
from pathlib import Path
from typing import Any
from scripts.acceptance.records import canonical_bytes,sha256_bytes
class AuthorityError(ValueError):
 def __init__(self,code:str,detail:str):self.code,self.detail=code,detail;super().__init__(f"{code}: {detail}")
def discover_authority(repo:Path,error_type:type[ValueError]=AuthorityError)->dict[str,Any]:
 from scripts.agents.local_codex_runtime import CodexRuntimeError,discover
 try:
  runtime=discover(repo);identity=dict(runtime.context);proof=dict(runtime.proof)
 except CodexRuntimeError as exc:raise error_type(getattr(exc,"code","runtime-unavailable"),str(exc)) from None
 required={"actor_id","session_id","parent_session_id","client"}
 if set(identity)!=required or any(not isinstance(identity[x],str) or not identity[x] for x in required):raise error_type("runtime-invalid","runtime identity is incomplete")
 if not isinstance(proof,dict):raise error_type("runtime-invalid","runtime proof is incomplete")
 verified_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00","Z")
 return {"identity":identity,"proof":proof,"authority":{"trust_boundary":"trusted-local-user-not-platform-cryptographic-attestation","verifier":"scripts.agents.local_codex_runtime.discover","verified_at":verified_at,"identity_sha256":sha256_bytes(canonical_bytes(identity)),"proof_sha256":sha256_bytes(canonical_bytes(proof))}}
def verify_authority(repo:Path,record:dict[str,Any],identity_key:str)->str|None:
 identity=record.get(identity_key);proof=record.get("runtime_proof");authority=record.get("authority")
 if not isinstance(identity,dict) or not isinstance(proof,dict) or not isinstance(authority,dict):return f"{identity_key}-authority-invalid"
 expected={"trust_boundary","verifier","verified_at","identity_sha256","proof_sha256"}
 if set(authority)!=expected or authority.get("trust_boundary")!="trusted-local-user-not-platform-cryptographic-attestation" or authority.get("verifier")!="scripts.agents.local_codex_runtime.discover":return f"{identity_key}-authority-invalid"
 try:
  stamp=datetime.fromisoformat(authority["verified_at"].replace("Z","+00:00"))
  if stamp.tzinfo is None:raise ValueError()
 except (KeyError,TypeError,ValueError):return f"{identity_key}-authority-invalid"
 if authority.get("identity_sha256")!=sha256_bytes(canonical_bytes(identity)) or authority.get("proof_sha256")!=sha256_bytes(canonical_bytes(proof)):return f"{identity_key}-authority-invalid"
 # Freshness belongs to materialization: discover() already validated the live
 # source at verified_at.  Re-read the bound source now, but do not compare now
 # to a synthetic receipt expiry.
 from scripts.agents.local_codex_runtime import CodexRuntimeError,verify_proof
 try:verify_proof(repo,proof,identity)
 except CodexRuntimeError as exc:return f"{identity_key}-source-{getattr(exc,'code','invalid')}"
 return None
