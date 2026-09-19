"""从真实本地 session 准备一次新鲜 issuer，不接受调用者身份字段。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.gates.evidence_packet import verify_packet_strict
from scripts.gates.issuer_packet import (
    ATTESTATION_SCHEMA, IssuerPacketError, IssuerPacketMaterializer,
    canonical_json_bytes, sha256_bytes,
)
from scripts.gates.receipt_store import read_bound_bytes
from scripts.harness.local_codex_runtime import discover


def local_authority(repo_root: str | Path) -> IssuerPacketMaterializer:
    materializer = IssuerPacketMaterializer(repo_root)
    authority = materializer.authorities["codex"]
    if not authority["available"] or authority["verifier_id"] != "codex.local-session.v1":
        raise IssuerPacketError("authority-unavailable", "local Codex authority must be enabled with codex.local-session.v1")
    return materializer


def prepare(repo_root: str | Path, evidence_locator: str, receipt_kind: str):
    root = Path(repo_root).resolve()
    evidence = read_bound_bytes(root, evidence_locator)
    packet = verify_packet_strict(str(root), evidence_locator, sha256_bytes(evidence))
    runtime = discover(root)
    subject = packet["subject"]["identity"]
    # 先拒绝同会话自签，避免产生任何无用的 authority/issuer 文件。
    if (subject["agent_id"] == runtime.context["actor_id"]
            or subject["client"] == "codex" and subject["session_id"] == runtime.context["session_id"]):
        raise IssuerPacketError("copied-subject-identity", "use an independent Codex task session; the producer cannot issue its own validation/review")
    materializer = local_authority(root)
    materializer._receipt_kinds([receipt_kind])
    if receipt_kind not in materializer.authorities["codex"]["authorized_receipt_kinds"]:
        raise IssuerPacketError("issuer-unauthorized-receipt", "local authority cannot issue this receipt kind")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    attestation_id = str(uuid.uuid4())
    data = {
        "schema_version": ATTESTATION_SCHEMA, "actor_type": "codex",
        "attestation_id": attestation_id, "nonce": str(uuid.uuid4()),
        "audience": materializer.attestation_audience,
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(seconds=materializer.max_age_seconds)).isoformat().replace("+00:00", "Z"),
        **runtime.context, "runtime_proof": runtime.proof,
    }
    locator = f"tmp/quality/authority/codex/{attestation_id}.json"
    content = canonical_json_bytes(data)
    materializer._ensure_directory("tmp/quality/authority/codex")
    materializer._create_file_exclusive(root / locator, content)
    return materializer.materialize(
        {"issuer_instance_id": str(uuid.uuid4()), "receipt_kinds": [receipt_kind]},
        authority_evidence_locator=locator, authority_evidence_sha256=sha256_bytes(content),
        subject_identity=subject,
    )
