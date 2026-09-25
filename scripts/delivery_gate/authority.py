"""Delivery Gate 签发者的运行身份绑定。记录创建和读取时均核验身份来源，不能用调用者自报替代。"""

from __future__ import annotations
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from scripts.delivery_gate.records import canonical_bytes, sha256_bytes


class AuthorityError(ValueError):
    """正式证据签发者的可信来源身份不可用或不匹配时失败。"""

    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def discover_authority(
    repo: Path, error_type: type[ValueError] = AuthorityError
) -> dict[str, Any]:
    """从真实 Codex Session 取得签发身份及来源证明，并绑定时间与内容哈希；不接受调用者自报。"""
    from scripts.agents.local_codex_runtime import CodexRuntimeError, discover

    try:
        runtime = discover(repo)
        identity = dict(runtime.context)
        proof = dict(runtime.proof)
    except CodexRuntimeError as exc:
        raise error_type(
            getattr(exc, "code", "runtime-unavailable"), str(exc)
        ) from None
    required = {"actor_id", "session_id", "parent_session_id", "client"}
    if set(identity) != required or any(
        not isinstance(identity[x], str) or not identity[x] for x in required
    ):
        raise error_type("runtime-invalid", "runtime identity is incomplete")
    if not isinstance(proof, dict):
        raise error_type("runtime-invalid", "runtime proof is incomplete")
    verified_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "identity": identity,
        "proof": proof,
        "authority": {
            "trust_boundary": "trusted-local-user-not-platform-cryptographic-attestation",
            "verifier": "scripts.agents.local_codex_runtime.discover",
            "verified_at": verified_at,
            "identity_sha256": sha256_bytes(canonical_bytes(identity)),
            "proof_sha256": sha256_bytes(canonical_bytes(proof)),
        },
    }


def verify_authority(
    repo: Path, record: dict[str, Any], identity_key: str
) -> str | None:
    """重读历史签发身份的本机来源证明并核对哈希；不以任意 TTL 代替当前性验证。"""
    identity = record.get(identity_key)
    proof = record.get("runtime_proof")
    authority = record.get("authority")
    if (
        not isinstance(identity, dict)
        or not isinstance(proof, dict)
        or not isinstance(authority, dict)
    ):
        return f"{identity_key}-authority-invalid"
    expected = {
        "trust_boundary",
        "verifier",
        "verified_at",
        "identity_sha256",
        "proof_sha256",
    }
    if (
        set(authority) != expected
        or authority.get("trust_boundary")
        != "trusted-local-user-not-platform-cryptographic-attestation"
        or authority.get("verifier") != "scripts.agents.local_codex_runtime.discover"
    ):
        return f"{identity_key}-authority-invalid"
    try:
        stamp = datetime.fromisoformat(authority["verified_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        return f"{identity_key}-authority-invalid"
    if authority.get("identity_sha256") != sha256_bytes(
        canonical_bytes(identity)
    ) or authority.get("proof_sha256") != sha256_bytes(canonical_bytes(proof)):
        return f"{identity_key}-authority-invalid"
    # 新鲜性在物化时判定：discover() 已在 verified_at 校验实时来源。
    # 此处重读绑定来源，但不和人为推算的 receipt 到期时间比较。
    from scripts.agents.local_codex_runtime import CodexRuntimeError, verify_proof

    try:
        verify_proof(repo, proof, identity)
    except CodexRuntimeError as exc:
        return f"{identity_key}-source-{getattr(exc, 'code', 'invalid')}"
    return None
