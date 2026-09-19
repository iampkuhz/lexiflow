"""Materialize immutable Gate issuer packets from fixed authority evidence.

The request surface intentionally contains no actor, role, verifier, authority, or
authorization claim.  Those values are derived by one of the fixed verifier
adapters registered in ``harness/gate-issuer-authorities.yaml``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Mapping

import yaml


REGISTRY_LOCATOR = "harness/gate-issuer-authorities.yaml"
PACKET_SCHEMA = "lexiflow.trusted-issuer-packet.v1"
REGISTRY_SCHEMA = "lexiflow.gate-issuer-authorities.v1"
ATTESTATION_SCHEMA = "lexiflow.gate-issuer-attestation.v1"
RECEIPT_KINDS = ("TASK_VALIDATION", "INDEPENDENT_REVIEW", "CATALOG_DECISION")

_REQUEST_FIELDS = frozenset({"issuer_instance_id", "receipt_kinds"})
_SUBJECT_IDENTITY_FIELDS = frozenset(
    {
        "parent_session_id",
        "agent_id",
        "run_id",
        "session_id",
        "client",
        "parent_client",
    }
)
_CALLER_AUTHORITY_FIELDS = frozenset(
    {
        "actor_type",
        "actor_id",
        "role",
        "verifier_id",
        "authority_id",
        "authorized_receipt_kinds",
        "session_id",
        "parent_session_id",
        "client",
    }
)
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_TERMINAL_QODER_STATUSES = frozenset({"finished", "failed", "completed"})
_SAFE_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")
_SAFE_CODEX_ACTOR = re.compile(r"(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}|/[A-Za-z0-9_][A-Za-z0-9._/-]{0,255})")
_SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
_ACTOR_VERIFIERS = MappingProxyType({
    "qoder": ("qoder.runner-provenance.v1", 1),
    "codex": ("codex.current-session.v1", 1),
    "human": ("human.operator-record.v1", 1),
    "ci": ("ci.workload-identity.v1", 1),
})


def _allowed_verifier(actor_type: str, binding: tuple[str, int]) -> bool:
    return binding == _ACTOR_VERIFIERS[actor_type] or (
        actor_type == "codex" and binding == ("codex.local-session.v1", 1)
    )

REASON_CODES = frozenset(
    {
        "authority-unavailable",
        "caller-authority-override",
        "copied-subject-identity",
        "duplicate-packet-identity",
        "evidence-drift",
        "evidence-unavailable",
        "forged-attestation",
        "identity-drift",
        "invalid-attestation",
        "invalid-authority-registry",
        "invalid-canonical-json",
        "invalid-evidence",
        "invalid-hash",
        "invalid-identity",
        "invalid-request",
        "invalid-subject-identity",
        "publication-failed",
        "replayed-attestation",
        "revoked-actor",
        "stale-attestation",
        "unauthorized-receipt-kind",
        "unknown-actor-type",
        "unknown-verifier",
        "unregistered-actor",
        "unsafe-locator",
        "unsupported-receipt-kind",
    }
)


class IssuerPacketError(ValueError):
    """A fail-closed issuer materialization error with a stable reason code."""

    def __init__(self, code: str, detail: str) -> None:
        if code not in REASON_CODES:
            raise RuntimeError(f"unregistered issuer reason code: {code}")
        self.code = code
        self.result = "FAIL"
        self.category = "issuer-untrusted"
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class MaterializedIssuerPacket:
    locator: str
    sha256: str
    packet: Mapping[str, Any]


@dataclass(frozen=True)
class _VerifiedIssuer:
    actor_type: str
    actor_id: str
    parent_session_id: str | None
    session_id: str
    client: str
    role: str
    authorized_receipt_kinds: tuple[str, ...]
    verifier_id: str
    verifier_abi: int
    authority_id: str
    actor_run_id: str | None
    replay_claims: tuple[tuple[str, str], ...]
    provenance: tuple[Mapping[str, str], ...]


@dataclass(frozen=True)
class _FrozenConfiguration:
    registry: Mapping[str, Any]
    registry_sha256: str
    semantic_fingerprint: str
    authorities: Mapping[str, Mapping[str, Any]]
    max_age_seconds: int
    attestation_audience: str
    store_locator: str
    verifier_bindings: Mapping[str, tuple[str, int]]
    evidence_roots: Mapping[str, tuple[str, ...]]


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise IssuerPacketError(
                "invalid-authority-registry", "registry object keys must be strings"
            )
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise IssuerPacketError(
        "invalid-authority-registry",
        f"unsupported registry value type: {type(value).__name__}",
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Return the canonical JSON projection used by this contract.

    Gate issuer objects contain only JSON strings, booleans, nulls, arrays, and
    objects, so sorted compact JSON is the RFC 8785 representation for this
    restricted value domain.
    """

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise IssuerPacketError("invalid-canonical-json", str(exc)) from None
    return encoded.encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise IssuerPacketError("invalid-attestation", f"{field} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise IssuerPacketError("invalid-attestation", f"{field} is invalid") from None
    if parsed.tzinfo is None:
        raise IssuerPacketError("invalid-attestation", f"{field} has no timezone")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_rfc_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise IssuerPacketError("invalid-identity", f"{field} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise IssuerPacketError("invalid-identity", f"{field} must be a UUID") from None
    if (
        parsed.int == 0
        or str(parsed) != value
        or parsed.variant != uuid.RFC_4122
        or parsed.version not in range(1, 9)
    ):
        raise IssuerPacketError(
            "invalid-identity", f"{field} must be a canonical non-nil RFC UUID"
        )
    return value


def _canonical_uuid4(value: Any, field: str) -> str:
    value = _canonical_rfc_uuid(value, field)
    if uuid.UUID(value).version != 4:
        raise IssuerPacketError("invalid-identity", f"{field} must be a canonical UUIDv4")
    return value


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IssuerPacketError("invalid-identity", f"{field} must be a non-empty string")
    return value


def _safe_locator(locator: Any) -> str:
    if not isinstance(locator, str) or not locator or "\\" in locator or "\x00" in locator:
        raise IssuerPacketError("unsafe-locator", "locator must be a non-empty POSIX path")
    path = PurePosixPath(locator)
    if path.is_absolute() or any(part in {"", ".", "..", "latest"} for part in path.parts):
        raise IssuerPacketError("unsafe-locator", f"unsafe repo-relative locator: {locator!r}")
    normalized = path.as_posix()
    if normalized != locator:
        raise IssuerPacketError("unsafe-locator", f"locator is not normalized: {locator!r}")
    return normalized


def _is_under(locator: str, root: str) -> bool:
    path_parts = PurePosixPath(locator).parts
    root_parts = PurePosixPath(_safe_locator(root)).parts
    return len(path_parts) > len(root_parts) and path_parts[: len(root_parts)] == root_parts


def _assert_no_symlink_path(repo_root: Path, locator: str, *, allow_missing_leaf: bool = False) -> Path:
    current = repo_root
    parts = PurePosixPath(_safe_locator(locator)).parts
    for index, part in enumerate(parts):
        current = current / part
        is_leaf = index == len(parts) - 1
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if allow_missing_leaf and is_leaf:
                return current
            raise IssuerPacketError("evidence-unavailable", f"missing locator: {locator}") from None
        if stat.S_ISLNK(mode):
            raise IssuerPacketError("unsafe-locator", f"symlink is forbidden: {locator}")
        if not is_leaf and not stat.S_ISDIR(mode):
            raise IssuerPacketError("unsafe-locator", f"non-directory ancestor: {locator}")
    return current


def _read_artifact(repo_root: Path, locator: str, expected_sha256: str | None = None) -> tuple[bytes, str]:
    path = _assert_no_symlink_path(repo_root, locator)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise IssuerPacketError("evidence-unavailable", f"cannot open {locator}: {exc}") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise IssuerPacketError("unsafe-locator", f"artifact is not a regular file: {locator}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            content = stream.read()
    finally:
        os.close(fd)
    digest = sha256_bytes(content)
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not _HEX_64.fullmatch(expected_sha256):
            raise IssuerPacketError("invalid-hash", "expected SHA-256 must be 64 lowercase hex characters")
        if not hmac.compare_digest(digest, expected_sha256):
            raise IssuerPacketError("evidence-drift", f"SHA-256 mismatch for {locator}")
    return content, digest


def _json_object(content: bytes, locator: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IssuerPacketError("invalid-evidence", f"invalid JSON at {locator}: {exc}") from None
    if not isinstance(value, dict):
        raise IssuerPacketError("invalid-evidence", f"JSON at {locator} must be an object")
    return value


class IssuerPacketMaterializer:
    """Verify authority-owned evidence and exclusively publish one issuer packet."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        trusted_codex_context: Mapping[str, Any] | None = None,
        key_resolver: Callable[[str], bytes | None] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo_root = Path(repo_root).resolve()
        self._trusted_codex_context = MappingProxyType(dict(trusted_codex_context or {}))
        self._key_resolver = key_resolver or (lambda _key_id: None)
        self._now = now or (lambda: datetime.now(timezone.utc))
        registry_content, registry_sha = _read_artifact(self.repo_root, REGISTRY_LOCATOR)
        self._configuration = self._parse_configuration(registry_content, registry_sha)

    @property
    def repo_root(self) -> Path:
        return self._repo_root

    @property
    def registry(self) -> Mapping[str, Any]:
        return self._configuration.registry

    @property
    def registry_sha256(self) -> str:
        return self._configuration.registry_sha256

    @property
    def authorities(self) -> Mapping[str, Mapping[str, Any]]:
        return self._configuration.authorities

    @property
    def max_age_seconds(self) -> int:
        return self._configuration.max_age_seconds

    @property
    def attestation_audience(self) -> str:
        return self._configuration.attestation_audience

    @property
    def store_locator(self) -> str:
        return self._configuration.store_locator

    @property
    def trusted_codex_context(self) -> Mapping[str, Any]:
        return self._trusted_codex_context

    @property
    def key_resolver(self) -> Callable[[str], bytes | None]:
        return self._key_resolver

    @property
    def now(self) -> Callable[[], datetime]:
        return self._now

    @classmethod
    def _parse_configuration(
        cls, registry_content: bytes, registry_sha: str
    ) -> _FrozenConfiguration:
        try:
            registry = yaml.safe_load(registry_content)
        except yaml.YAMLError as exc:
            raise IssuerPacketError("invalid-authority-registry", str(exc)) from None
        expected_registry_fields = {
            "schema_version",
            "owner",
            "packet_store_locator",
            "attestation_max_age_seconds",
            "attestation_audience",
            "authorities",
        }
        if (
            not isinstance(registry, dict)
            or set(registry) != expected_registry_fields
            or registry.get("schema_version") != REGISTRY_SCHEMA
        ):
            raise IssuerPacketError("invalid-authority-registry", "unsupported registry schema")
        if registry.get("owner") != "LF-WS-QLT":
            raise IssuerPacketError("invalid-authority-registry", "registry owner must be LF-WS-QLT")
        authorities = registry.get("authorities")
        if not isinstance(authorities, dict) or set(authorities) != {"qoder", "codex", "human", "ci"}:
            raise IssuerPacketError("invalid-authority-registry", "registry must define exactly four actor types")
        max_age_seconds = registry.get("attestation_max_age_seconds")
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or max_age_seconds <= 0:
            raise IssuerPacketError("invalid-authority-registry", "invalid attestation_max_age_seconds")
        attestation_audience = registry.get("attestation_audience")
        if not isinstance(attestation_audience, str) or not attestation_audience.strip():
            raise IssuerPacketError("invalid-authority-registry", "invalid attestation_audience")
        try:
            store_locator = _safe_locator(registry.get("packet_store_locator"))
        except IssuerPacketError as exc:
            raise IssuerPacketError("invalid-authority-registry", str(exc)) from None
        cls._validate_authority_registry(authorities)
        try:
            semantic_fingerprint = sha256_bytes(canonical_json_bytes(registry))
        except IssuerPacketError as exc:
            raise IssuerPacketError("invalid-authority-registry", str(exc)) from None
        frozen_registry = _deep_freeze(registry)
        frozen_authorities = frozen_registry["authorities"]
        verifier_bindings = MappingProxyType(
            {
                actor_type: (
                    str(authority["verifier_id"]),
                    int(authority["verifier_abi"]),
                )
                for actor_type, authority in frozen_authorities.items()
            }
        )
        evidence_roots = MappingProxyType(
            {
                actor_type: tuple(str(root) for root in authority["evidence_roots"])
                for actor_type, authority in frozen_authorities.items()
            }
        )
        return _FrozenConfiguration(
            registry=frozen_registry,
            registry_sha256=registry_sha,
            semantic_fingerprint=semantic_fingerprint,
            authorities=frozen_authorities,
            max_age_seconds=max_age_seconds,
            attestation_audience=attestation_audience,
            store_locator=store_locator,
            verifier_bindings=verifier_bindings,
            evidence_roots=evidence_roots,
        )

    def materialize(
        self,
        request: Mapping[str, Any],
        *,
        authority_evidence_locator: str,
        authority_evidence_sha256: str,
        subject_identity: Mapping[str, Any] | None = None,
    ) -> MaterializedIssuerPacket:
        """Materialize a packet; the request cannot carry authority claims."""

        if not isinstance(request, Mapping):
            raise IssuerPacketError("invalid-request", "request must be an object")
        extra = set(request) - _REQUEST_FIELDS
        if extra & _CALLER_AUTHORITY_FIELDS:
            raise IssuerPacketError("caller-authority-override", f"forbidden request fields: {sorted(extra)}")
        if extra or set(request) != _REQUEST_FIELDS:
            raise IssuerPacketError("invalid-request", f"request fields must be exactly {sorted(_REQUEST_FIELDS)}")
        instance_id = _canonical_uuid4(request["issuer_instance_id"], "issuer_instance_id")
        receipt_kinds = self._receipt_kinds(request["receipt_kinds"])
        subject = self._validate_subject_identity(subject_identity)
        locator = _safe_locator(authority_evidence_locator)
        verified, evidence_sha = self._resolve_verified_issuer(
            locator, authority_evidence_sha256
        )
        self._assert_receipt_authorization(verified, receipt_kinds)
        self._reject_copied_subject(verified, instance_id, subject)
        initially_verified = verified

        def revalidate_trust() -> None:
            current, current_sha = self._resolve_verified_issuer(
                locator, authority_evidence_sha256
            )
            if current_sha != evidence_sha or current != initially_verified:
                raise IssuerPacketError(
                    "identity-drift",
                    "publication verifier output drifted from the initial verification",
                )
            self._assert_receipt_authorization(current, receipt_kinds)
            self._reject_copied_subject(current, instance_id, subject)

        materialized_at = self.now().astimezone(timezone.utc)
        packet: dict[str, Any] = {
            "schema_version": PACKET_SCHEMA,
            "issuer_instance_id": instance_id,
            "actor_type": verified.actor_type,
            "actor_id": verified.actor_id,
            "parent_session_id": verified.parent_session_id,
            "session_id": verified.session_id,
            "client": verified.client,
            "role": verified.role,
            "authorized_receipt_kinds": list(receipt_kinds),
            "authority": {
                "registry_locator": REGISTRY_LOCATOR,
                "registry_sha256": self.registry_sha256,
                "authority_id": verified.authority_id,
                "verifier_id": verified.verifier_id,
                "verifier_abi": verified.verifier_abi,
                "evidence_locator": locator,
                "evidence_sha256": evidence_sha,
                "provenance": list(verified.provenance),
            },
            "materialized_at": _format_utc(materialized_at),
        }
        packet_bytes = canonical_json_bytes(packet)
        packet_sha = sha256_bytes(packet_bytes)
        packet_locator = f"{self.store_locator}/{instance_id}.json"
        self._publish_exclusive(
            packet_locator,
            packet_bytes,
            replay_claims=verified.replay_claims,
            issuer_instance_id=instance_id,
            revalidate_trust=revalidate_trust,
        )
        return MaterializedIssuerPacket(packet_locator, packet_sha, packet)

    def _resolve_verified_issuer(
        self, locator: str, expected_evidence_sha256: str
    ) -> tuple[_VerifiedIssuer, str]:
        configuration = self._current_configuration()
        evidence, evidence_sha = _read_artifact(
            self.repo_root, locator, expected_evidence_sha256
        )
        actor_type = self._route_actor_type(locator, evidence, configuration)
        authority = self._authority(actor_type, configuration)
        verifier_id = authority.get("verifier_id")
        verifier_abi = authority.get("verifier_abi")
        expected_binding = configuration.verifier_bindings[actor_type]
        if (
            (verifier_id, verifier_abi) != expected_binding
            or not _allowed_verifier(actor_type, expected_binding)
        ):
            raise IssuerPacketError(
                "unknown-verifier",
                f"{actor_type} must use {_ACTOR_VERIFIERS[actor_type][0]}@1",
            )
        verifier = {
            ("qoder.runner-provenance.v1", 1): self._verify_qoder,
            ("codex.current-session.v1", 1): self._verify_codex,
            ("codex.local-session.v1", 1): self._verify_codex,
            ("human.operator-record.v1", 1): self._verify_human,
            ("ci.workload-identity.v1", 1): self._verify_ci,
        }.get((verifier_id, verifier_abi))
        if verifier is None:
            raise IssuerPacketError(
                "unknown-verifier", f"unrecognized verifier ABI: {verifier_id!r}@{verifier_abi!r}"
            )
        verified = verifier(locator, evidence, evidence_sha, authority, configuration)
        if verified.actor_type != actor_type or verified.verifier_id != verifier_id:
            raise IssuerPacketError("identity-drift", "verifier output does not match routed authority")
        self._validate_verified_issuer(
            verified,
            actor_type,
            locator,
            evidence_sha,
            evidence,
            authority,
            configuration,
        )
        return verified, evidence_sha

    @staticmethod
    def _assert_receipt_authorization(
        verified: _VerifiedIssuer, receipt_kinds: tuple[str, ...]
    ) -> None:
        unauthorized = set(receipt_kinds) - set(verified.authorized_receipt_kinds)
        if unauthorized:
            raise IssuerPacketError("unauthorized-receipt-kind", f"not authorized: {sorted(unauthorized)}")

    def _validate_verified_issuer(
        self,
        verified: _VerifiedIssuer,
        actor_type: str,
        evidence_locator: str,
        evidence_sha256: str,
        evidence: bytes,
        authority: Mapping[str, Any],
        configuration: _FrozenConfiguration,
    ) -> None:
        expected = self._derive_expected_issuer(
            actor_type,
            evidence_locator,
            evidence,
            evidence_sha256,
            authority,
            configuration,
        )
        if verified != expected:
            raise IssuerPacketError(
                "identity-drift",
                "fixed verifier output is not bound to the parsed authority evidence",
            )
        expected_verifier = configuration.verifier_bindings[actor_type]
        if (
            verified.actor_type != actor_type
            or verified.verifier_id != expected_verifier[0]
            or verified.verifier_abi != expected_verifier[1]
            or verified.authority_id != authority.get("authority_id")
            or verified.client != actor_type
            or not isinstance(verified.actor_id, str)
            or not ( (_SAFE_CODEX_ACTOR if actor_type == "codex" else _SAFE_ID).fullmatch(verified.actor_id) )
            or not isinstance(verified.role, str)
            or not verified.role
        ):
            raise IssuerPacketError("identity-drift", "verifier returned invalid issuer identity")
        _canonical_rfc_uuid(verified.session_id, "verified.session_id")
        if actor_type in {"qoder", "codex"}:
            _canonical_rfc_uuid(verified.parent_session_id, "verified.parent_session_id")
        elif verified.parent_session_id is not None:
            raise IssuerPacketError("identity-drift", "verifier returned unexpected parent session")
        if actor_type == "qoder":
            actor_run_id = _canonical_rfc_uuid(verified.actor_run_id, "verified.actor_run_id")
            expected_claims = (("qoder-run-id", actor_run_id),)
            expected_source = authority
            expected_provenance_kinds = ("runner-task", "runner-completion")
        else:
            if verified.actor_run_id is not None:
                raise IssuerPacketError("identity-drift", "verifier returned unexpected actor run")
            if tuple(kind for kind, _value in verified.replay_claims) != (
                "attestation-id",
                "nonce",
            ):
                raise IssuerPacketError("identity-drift", "verifier returned incomplete replay claims")
            for kind, value in verified.replay_claims:
                _canonical_uuid4(value, f"verified.{kind}")
            expected_claims = verified.replay_claims
            if actor_type == "codex":
                expected_source = authority
                local = "runtime_proof" in _json_object(evidence, evidence_locator)
                expected_provenance_kinds = ("local-session-attestation" if local else "host-session-attestation",)
            elif actor_type == "human":
                expected_source = self._registry_record(
                    authority, "actors", verified.actor_id
                )
                expected_provenance_kinds = ("authenticated-operator-attestation",)
            else:
                expected_source = self._registry_record(
                    authority, "workloads", verified.actor_id
                )
                expected_provenance_kinds = ("authenticated-workload-attestation",)
        if verified.replay_claims != expected_claims:
            raise IssuerPacketError("identity-drift", "verifier replay claims are invalid")
        if verified.role != expected_source.get("role"):
            raise IssuerPacketError("identity-drift", "verifier role drifted from registry")
        if verified.authorized_receipt_kinds != self._registered_kinds(expected_source):
            raise IssuerPacketError("identity-drift", "verifier authorization drifted from registry")
        if (
            not verified.authorized_receipt_kinds
            or len(set(verified.authorized_receipt_kinds))
            != len(verified.authorized_receipt_kinds)
            or any(kind not in RECEIPT_KINDS for kind in verified.authorized_receipt_kinds)
        ):
            raise IssuerPacketError("identity-drift", "verifier authorization is invalid")
        if not verified.provenance:
            raise IssuerPacketError("identity-drift", "verifier provenance is empty")
        if tuple(artifact.get("kind") for artifact in verified.provenance) != expected_provenance_kinds:
            raise IssuerPacketError("identity-drift", "verifier provenance kinds are invalid")
        for artifact in verified.provenance:
            if set(artifact) != {"kind", "locator", "sha256"}:
                raise IssuerPacketError("identity-drift", "verifier provenance fields are invalid")
            _non_empty_string(artifact.get("kind"), "provenance.kind")
            _safe_locator(artifact.get("locator"))
            digest = artifact.get("sha256")
            if not isinstance(digest, str) or not _HEX_64.fullmatch(digest):
                raise IssuerPacketError("identity-drift", "verifier provenance hash is invalid")
            _read_artifact(self.repo_root, artifact["locator"], digest)

    @classmethod
    def _validate_authority_registry(
        cls, authorities: Mapping[str, Any]
    ) -> None:
        verifier_bindings: set[tuple[str, int]] = set()
        authority_ids: set[str] = set()
        evidence_roots: list[str] = []
        for actor_type, expected_verifier in _ACTOR_VERIFIERS.items():
            authority = authorities.get(actor_type)
            if not isinstance(authority, dict):
                raise IssuerPacketError(
                    "invalid-authority-registry", f"authority {actor_type} must be an object"
                )
            expected_fields = {
                "available",
                "authority_id",
                "verifier_id",
                "verifier_abi",
                "evidence_roots",
                "role",
                "authorized_receipt_kinds",
            }
            if actor_type == "human":
                expected_fields = {
                    "available",
                    "authority_id",
                    "verifier_id",
                    "verifier_abi",
                    "evidence_roots",
                    "actors",
                }
            elif actor_type == "ci":
                expected_fields = {
                    "available",
                    "authority_id",
                    "verifier_id",
                    "verifier_abi",
                    "evidence_roots",
                    "workloads",
                }
            if set(authority) != expected_fields or type(authority.get("available")) is not bool:
                raise IssuerPacketError(
                    "invalid-authority-registry",
                    f"authority {actor_type} fields are invalid",
                )
            verifier_id = authority.get("verifier_id")
            verifier_abi = authority.get("verifier_abi")
            if not isinstance(verifier_id, str) or isinstance(verifier_abi, bool) or not isinstance(verifier_abi, int):
                raise IssuerPacketError(
                    "invalid-authority-registry", f"invalid verifier ABI for {actor_type}"
                )
            binding = (verifier_id, verifier_abi)
            if binding in verifier_bindings:
                raise IssuerPacketError("invalid-authority-registry", "duplicate verifier binding")
            verifier_bindings.add(binding)
            if not _allowed_verifier(actor_type, binding):
                raise IssuerPacketError(
                    "unknown-verifier", f"unexpected verifier binding for {actor_type}"
                )
            authority_id = authority.get("authority_id")
            if (
                not isinstance(authority_id, str)
                or not _SAFE_ID.fullmatch(authority_id)
                or authority_id in authority_ids
            ):
                raise IssuerPacketError("invalid-authority-registry", "authority IDs must be unique")
            authority_ids.add(authority_id)
            roots = authority.get("evidence_roots")
            if not isinstance(roots, list) or not roots:
                raise IssuerPacketError(
                    "invalid-authority-registry", f"missing evidence roots for {actor_type}"
                )
            for root in roots:
                normalized = _safe_locator(root)
                if any(
                    PurePosixPath(normalized).parts[: len(PurePosixPath(other).parts)]
                    == PurePosixPath(other).parts
                    or PurePosixPath(other).parts[: len(PurePosixPath(normalized).parts)]
                    == PurePosixPath(normalized).parts
                    for other in evidence_roots
                ):
                    raise IssuerPacketError(
                        "invalid-authority-registry", "authority evidence roots must not overlap"
                    )
                evidence_roots.append(normalized)
            if actor_type in {"qoder", "codex"}:
                role = authority.get("role")
                if not isinstance(role, str) or not _SAFE_ID.fullmatch(role):
                    raise IssuerPacketError(
                        "invalid-authority-registry", f"authority {actor_type} role is invalid"
                    )
                cls._registered_kinds(authority)

        human_authority = authorities["human"]
        actors = human_authority.get("actors")
        if not isinstance(actors, dict) or not actors:
            raise IssuerPacketError(
                "invalid-authority-registry", "human actors must be a non-empty object"
            )
        for actor_id, record in actors.items():
            if (
                not isinstance(actor_id, str)
                or not _SAFE_ID.fullmatch(actor_id)
                or not isinstance(record, dict)
                or set(record)
                != {"revoked", "role", "key_id", "authorized_receipt_kinds"}
            ):
                raise IssuerPacketError(
                    "invalid-authority-registry", "human actor record is malformed"
                )
            if type(record.get("revoked")) is not bool:
                raise IssuerPacketError(
                    "invalid-authority-registry", f"human actor {actor_id} has invalid revoked"
                )
            for field in ("key_id", "role"):
                value = record.get(field)
                if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
                    raise IssuerPacketError(
                        "invalid-authority-registry",
                        f"human actor {actor_id} has invalid {field}",
                    )
            cls._registered_kinds(record)

        ci_authority = authorities["ci"]
        workloads = ci_authority.get("workloads")
        if not isinstance(workloads, dict) or not workloads:
            raise IssuerPacketError(
                "invalid-authority-registry", "CI workloads must be a non-empty object"
            )
        for workload_id, record in workloads.items():
            if (
                not isinstance(workload_id, str)
                or not _SAFE_ID.fullmatch(workload_id)
                or not isinstance(record, dict)
                or set(record)
                != {
                    "revoked",
                    "role",
                    "key_id",
                    "repository",
                    "allowed_ref_prefixes",
                    "environment",
                    "authorized_receipt_kinds",
                }
            ):
                raise IssuerPacketError(
                    "invalid-authority-registry", "CI workload record is malformed"
                )
            for field in ("repository", "environment", "key_id", "role"):
                value = record.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise IssuerPacketError(
                        "invalid-authority-registry",
                        f"CI workload {workload_id} has invalid {field}",
                    )
            repository_parts = str(record["repository"]).split("/")
            if len(repository_parts) != 2 or any(
                not _SAFE_ID.fullmatch(part) for part in repository_parts
            ):
                raise IssuerPacketError(
                    "invalid-authority-registry",
                    f"CI workload {workload_id} has invalid repository",
                )
            if not _SAFE_ID.fullmatch(str(record["environment"])):
                raise IssuerPacketError(
                    "invalid-authority-registry",
                    f"CI workload {workload_id} has invalid environment",
                )
            prefixes = record.get("allowed_ref_prefixes")
            if (
                not isinstance(prefixes, list)
                or not prefixes
                or any(not cls._valid_ref_prefix(prefix) for prefix in prefixes)
            ):
                raise IssuerPacketError(
                    "invalid-authority-registry",
                    f"CI workload {workload_id} has invalid allowed_ref_prefixes",
                )
            if type(record.get("revoked")) is not bool:
                raise IssuerPacketError(
                    "invalid-authority-registry", f"CI workload {workload_id} has invalid revoked"
                )
            cls._registered_kinds(record)

    @classmethod
    def _valid_ref_prefix(cls, value: Any) -> bool:
        return (
            isinstance(value, str)
            and value.endswith("/")
            and not value.endswith("//")
            and cls._valid_git_ref(value[:-1], allow_namespace_only=True)
        )

    @staticmethod
    def _valid_git_ref(value: Any, *, allow_namespace_only: bool = False) -> bool:
        if not isinstance(value, str) or value == "@" or not value.startswith("refs/"):
            return False
        if (
            value.endswith("/")
            or value.endswith(".")
            or "//" in value
            or ".." in value
            or "@{" in value
            or any(character in value for character in "\\~^:?*[")
            or any(
                character.isspace()
                or ord(character) == 127
                or unicodedata.category(character).startswith("C")
                for character in value
            )
        ):
            return False
        parts = value.split("/")
        minimum_parts = 2 if allow_namespace_only else 3
        if len(parts) < minimum_parts or parts[0] != "refs":
            return False
        return all(
            part not in {"", ".", "..", "@"}
            and not part.startswith(".")
            and not part.endswith(".lock")
            for part in parts
        )

    def _current_configuration(self) -> _FrozenConfiguration:
        content, current_sha = _read_artifact(self.repo_root, REGISTRY_LOCATOR)
        current = self._parse_configuration(content, current_sha)
        frozen = self._configuration
        if (
            not hmac.compare_digest(current.registry_sha256, frozen.registry_sha256)
            or not hmac.compare_digest(
                current.semantic_fingerprint, frozen.semantic_fingerprint
            )
        ):
            raise IssuerPacketError(
                "evidence-drift",
                "authority registry bytes or validated semantics changed during materialization",
            )
        return current

    def _receipt_kinds(self, value: Any) -> tuple[str, ...]:
        if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
            raise IssuerPacketError("invalid-request", "receipt_kinds must be a non-empty string list")
        if len(value) != len(set(value)):
            raise IssuerPacketError("invalid-request", "receipt_kinds must not contain duplicates")
        unknown = set(value) - set(RECEIPT_KINDS)
        if unknown:
            raise IssuerPacketError("unsupported-receipt-kind", f"unsupported kinds: {sorted(unknown)}")
        return tuple(kind for kind in RECEIPT_KINDS if kind in value)

    def _validate_subject_identity(
        self, value: Mapping[str, Any] | None
    ) -> Mapping[str, str]:
        if not isinstance(value, Mapping) or set(value) != _SUBJECT_IDENTITY_FIELDS:
            raise IssuerPacketError(
                "invalid-subject-identity",
                f"subject identity fields must be exactly {sorted(_SUBJECT_IDENTITY_FIELDS)}",
            )
        try:
            normalized = {
                "parent_session_id": _canonical_rfc_uuid(
                    value.get("parent_session_id"), "subject.parent_session_id"
                ),
                "run_id": _canonical_rfc_uuid(value.get("run_id"), "subject.run_id"),
                "session_id": _canonical_rfc_uuid(value.get("session_id"), "subject.session_id"),
                "agent_id": _non_empty_string(value.get("agent_id"), "subject.agent_id"),
                "client": _non_empty_string(value.get("client"), "subject.client"),
                "parent_client": _non_empty_string(
                    value.get("parent_client"), "subject.parent_client"
                ),
            }
        except IssuerPacketError as exc:
            raise IssuerPacketError("invalid-subject-identity", str(exc)) from None
        actor_pattern = _SAFE_CODEX_ACTOR if normalized["client"] == "codex" else _SAFE_ID
        if not actor_pattern.fullmatch(normalized["agent_id"]):
            raise IssuerPacketError("invalid-subject-identity", "subject agent_id is invalid")
        for field in ("client", "parent_client"):
            if not _SAFE_ID.fullmatch(normalized[field]):
                raise IssuerPacketError(
                    "invalid-subject-identity", f"subject {field} is invalid"
                )
        return normalized

    def _authority(
        self, actor_type: str, configuration: _FrozenConfiguration
    ) -> Mapping[str, Any]:
        authority = configuration.authorities.get(actor_type)
        if not isinstance(authority, Mapping):
            raise IssuerPacketError("unknown-actor-type", f"unsupported actor type: {actor_type!r}")
        if authority.get("available") is not True:
            raise IssuerPacketError("authority-unavailable", f"authority unavailable for {actor_type}")
        _non_empty_string(authority.get("authority_id"), "authority_id")
        _non_empty_string(authority.get("verifier_id"), "verifier_id")
        if authority.get("verifier_abi") != 1:
            raise IssuerPacketError("unknown-verifier", "authority verifier ABI is unsupported")
        roots = authority.get("evidence_roots")
        if not isinstance(roots, tuple) or not roots or any(not isinstance(root, str) for root in roots):
            raise IssuerPacketError("invalid-authority-registry", f"invalid evidence roots for {actor_type}")
        return authority

    def _route_actor_type(
        self,
        locator: str,
        content: bytes,
        configuration: _FrozenConfiguration,
    ) -> str:
        candidates: list[str] = []
        for actor_type, roots in configuration.evidence_roots.items():
            if any(_is_under(locator, root) for root in roots):
                candidates.append(actor_type)
        if len(candidates) != 1:
            raise IssuerPacketError("unsafe-locator", "evidence locator must resolve under exactly one authority root")
        routed = candidates[0]
        if routed == "qoder":
            if PurePosixPath(locator).name != "completion.json":
                raise IssuerPacketError("invalid-evidence", "Qoder evidence must be a runner completion.json")
            return routed
        data = _json_object(content, locator)
        claimed = data.get("actor_type")
        if claimed not in configuration.authorities:
            raise IssuerPacketError("unknown-actor-type", f"unsupported actor type: {claimed!r}")
        if claimed != routed:
            raise IssuerPacketError("identity-drift", "attestation actor type does not match authority-owned root")
        return routed

    def _fresh_attestation(
        self, data: Mapping[str, Any], configuration: _FrozenConfiguration
    ) -> tuple[str, str]:
        if data.get("schema_version") != ATTESTATION_SCHEMA:
            raise IssuerPacketError("invalid-attestation", "unsupported attestation schema")
        attestation_id = _canonical_uuid4(data.get("attestation_id"), "attestation_id")
        if data.get("audience") != configuration.attestation_audience:
            raise IssuerPacketError("invalid-attestation", "attestation audience is not LexiFlow Gate")
        nonce = _canonical_uuid4(data.get("nonce"), "nonce")
        issued = _parse_utc(data.get("issued_at"), "issued_at")
        expires = _parse_utc(data.get("expires_at"), "expires_at")
        now = self.now().astimezone(timezone.utc)
        if issued > now or expires < now or expires <= issued:
            raise IssuerPacketError("stale-attestation", "attestation is not current")
        if (now - issued).total_seconds() > configuration.max_age_seconds:
            raise IssuerPacketError("stale-attestation", "attestation exceeds maximum age")
        return attestation_id, nonce

    @staticmethod
    def _reject_attestation_authority_claims(data: Mapping[str, Any]) -> None:
        forbidden = {
            "role",
            "verifier_id",
            "authority_id",
            "authorized_receipt_kinds",
        } & set(data)
        if forbidden:
            raise IssuerPacketError(
                "caller-authority-override",
                f"attestation cannot assert authority fields: {sorted(forbidden)}",
            )

    def _derive_expected_issuer(
        self,
        actor_type: str,
        locator: str,
        content: bytes,
        evidence_sha: str,
        authority: Mapping[str, Any],
        configuration: _FrozenConfiguration,
    ) -> _VerifiedIssuer:
        derivation = {
            "qoder": self._derive_qoder_expected,
            "codex": self._derive_codex_expected,
            "human": self._derive_human_expected,
            "ci": self._derive_ci_expected,
        }.get(actor_type)
        if derivation is None:
            raise IssuerPacketError(
                "unknown-actor-type", f"unsupported actor type: {actor_type!r}"
            )
        return derivation(locator, content, evidence_sha, authority, configuration)

    def _verify_qoder(
        self,
        locator: str,
        content: bytes,
        evidence_sha: str,
        authority: Mapping[str, Any],
        configuration: _FrozenConfiguration,
    ) -> _VerifiedIssuer:
        return self._derive_qoder_expected(
            locator, content, evidence_sha, authority, configuration
        )

    def _verify_codex(
        self,
        locator: str,
        content: bytes,
        evidence_sha: str,
        authority: Mapping[str, Any],
        configuration: _FrozenConfiguration,
    ) -> _VerifiedIssuer:
        return self._derive_codex_expected(
            locator, content, evidence_sha, authority, configuration
        )

    def _verify_human(
        self,
        locator: str,
        content: bytes,
        evidence_sha: str,
        authority: Mapping[str, Any],
        configuration: _FrozenConfiguration,
    ) -> _VerifiedIssuer:
        return self._derive_human_expected(
            locator, content, evidence_sha, authority, configuration
        )

    def _verify_ci(
        self,
        locator: str,
        content: bytes,
        evidence_sha: str,
        authority: Mapping[str, Any],
        configuration: _FrozenConfiguration,
    ) -> _VerifiedIssuer:
        return self._derive_ci_expected(
            locator, content, evidence_sha, authority, configuration
        )

    def _derive_qoder_expected(
        self,
        locator: str,
        content: bytes,
        evidence_sha: str,
        authority: Mapping[str, Any],
        configuration: _FrozenConfiguration,
    ) -> _VerifiedIssuer:
        completion = _json_object(content, locator)
        completion_path = PurePosixPath(locator)
        run_id = _canonical_rfc_uuid(completion.get("run_id"), "run_id")
        if completion_path.parent.name != run_id:
            raise IssuerPacketError("identity-drift", "completion path and run_id differ")
        task_locator = (completion_path.parent / "task.json").as_posix()
        task_content, task_sha = _read_artifact(self.repo_root, task_locator)
        task = _json_object(task_content, task_locator)
        if completion.get("status") not in _TERMINAL_QODER_STATUSES:
            raise IssuerPacketError("invalid-evidence", "Qoder completion is not terminal")
        if isinstance(completion.get("exit_code"), bool) or not isinstance(completion.get("exit_code"), int):
            raise IssuerPacketError("invalid-evidence", "Qoder completion exit_code is invalid")
        identity_fields = (
            "task_id",
            "task_version",
            "change_version",
            "agent_id",
            "run_id",
            "session_id",
            "client",
            "parent_client",
            "parent_session_id",
        )
        if any(task.get(field) != completion.get(field) for field in identity_fields):
            raise IssuerPacketError("identity-drift", "Qoder task and completion identity differ")
        if task.get("client") != "qoder" or task.get("parent_client") != "codex":
            raise IssuerPacketError("identity-drift", "Qoder runner client binding is invalid")
        for field in ("task_id", "agent_id"):
            value = task.get(field)
            if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
                raise IssuerPacketError("invalid-evidence", f"Qoder {field} is invalid")
        task_version = task.get("task_version")
        if isinstance(task_version, bool) or not isinstance(task_version, int) or task_version <= 0:
            raise IssuerPacketError("invalid-evidence", "Qoder task_version is invalid")
        change_version = task.get("change_version")
        if not isinstance(change_version, str) or not _SEMVER.fullmatch(change_version):
            raise IssuerPacketError("invalid-evidence", "Qoder change_version is invalid")
        session_id = _canonical_rfc_uuid(task.get("session_id"), "session_id")
        parent_session_id = _canonical_rfc_uuid(task.get("parent_session_id"), "parent_session_id")
        actor_id = _non_empty_string(task.get("agent_id"), "agent_id")
        age = self.now().timestamp() - (self.repo_root / locator).stat().st_mtime
        if age < -1 or age > configuration.max_age_seconds:
            raise IssuerPacketError("stale-attestation", "Qoder completion is outside the freshness window")
        allowed = self._registered_kinds(authority)
        return _VerifiedIssuer(
            "qoder",
            actor_id,
            parent_session_id,
            session_id,
            "qoder",
            _non_empty_string(authority.get("role"), "role"),
            allowed,
            str(authority["verifier_id"]),
            int(authority["verifier_abi"]),
            str(authority["authority_id"]),
            run_id,
            (("qoder-run-id", run_id),),
            (
                {"kind": "runner-task", "locator": task_locator, "sha256": task_sha},
                {"kind": "runner-completion", "locator": locator, "sha256": evidence_sha},
            ),
        )

    def _derive_codex_expected(
        self,
        locator: str,
        content: bytes,
        evidence_sha: str,
        authority: Mapping[str, Any],
        configuration: _FrozenConfiguration,
    ) -> _VerifiedIssuer:
        data = _json_object(content, locator)
        self._reject_attestation_authority_claims(data)
        attestation_id, nonce = self._fresh_attestation(data, configuration)
        actor_id = _non_empty_string(data.get("actor_id"), "actor_id")
        session_id = _canonical_rfc_uuid(data.get("session_id"), "session_id")
        parent_session_id = _canonical_rfc_uuid(data.get("parent_session_id"), "parent_session_id")
        observed = {
            "actor_id": actor_id,
            "session_id": session_id,
            "parent_session_id": parent_session_id,
            "client": data.get("client"),
        }
        context = self.trusted_codex_context
        local = authority["verifier_id"] == "codex.local-session.v1"
        if local != ("runtime_proof" in data):
            raise IssuerPacketError("invalid-attestation", "runtime proof must match the configured Codex verifier")
        if local:
            from scripts.harness.local_codex_runtime import discover, verify_proof
            from scripts.harness.codex_runtime import CodexRuntimeError
            try:
                verify_proof(self.repo_root, data["runtime_proof"], observed)
                context = discover(self.repo_root).context
            except CodexRuntimeError as exc:
                raise IssuerPacketError("authority-unavailable", str(exc)) from None
        if not context:
            raise IssuerPacketError("authority-unavailable", "current Codex host context is unavailable")
        if data.get("client") != "codex" or observed != context:
            raise IssuerPacketError("identity-drift", "Codex attestation is not the current host session")
        return _VerifiedIssuer(
            "codex",
            actor_id,
            parent_session_id,
            session_id,
            "codex",
            _non_empty_string(authority.get("role"), "role"),
            self._registered_kinds(authority),
            str(authority["verifier_id"]),
            int(authority["verifier_abi"]),
            str(authority["authority_id"]),
            None,
            (("attestation-id", attestation_id), ("nonce", nonce)),
            ({"kind": "local-session-attestation" if local else "host-session-attestation",
              "locator": locator, "sha256": evidence_sha},),
        )

    def _derive_human_expected(
        self,
        locator: str,
        content: bytes,
        evidence_sha: str,
        authority: Mapping[str, Any],
        configuration: _FrozenConfiguration,
    ) -> _VerifiedIssuer:
        data = _json_object(content, locator)
        self._reject_attestation_authority_claims(data)
        attestation_id, nonce = self._fresh_attestation(data, configuration)
        actor_id = _non_empty_string(data.get("operator_id"), "operator_id")
        record = self._registry_record(authority, "actors", actor_id)
        self._verify_signature(data, record)
        session_id = _canonical_rfc_uuid(data.get("session_id"), "session_id")
        if data.get("client") != "human":
            raise IssuerPacketError("identity-drift", "human client must be authority asserted as human")
        return _VerifiedIssuer(
            "human",
            actor_id,
            None,
            session_id,
            "human",
            _non_empty_string(record.get("role"), "role"),
            self._registered_kinds(record),
            str(authority["verifier_id"]),
            int(authority["verifier_abi"]),
            str(authority["authority_id"]),
            None,
            (("attestation-id", attestation_id), ("nonce", nonce)),
            ({"kind": "authenticated-operator-attestation", "locator": locator, "sha256": evidence_sha},),
        )

    def _derive_ci_expected(
        self,
        locator: str,
        content: bytes,
        evidence_sha: str,
        authority: Mapping[str, Any],
        configuration: _FrozenConfiguration,
    ) -> _VerifiedIssuer:
        data = _json_object(content, locator)
        self._reject_attestation_authority_claims(data)
        attestation_id, nonce = self._fresh_attestation(data, configuration)
        actor_id = _non_empty_string(data.get("workload_id"), "workload_id")
        record = self._registry_record(authority, "workloads", actor_id)
        self._verify_signature(data, record)
        self._verify_ci_constraints(data, record)
        session_id = _canonical_rfc_uuid(data.get("session_id"), "session_id")
        if data.get("client") != "ci":
            raise IssuerPacketError("identity-drift", "CI client must be authority asserted as ci")
        return _VerifiedIssuer(
            "ci",
            actor_id,
            None,
            session_id,
            "ci",
            _non_empty_string(record.get("role"), "role"),
            self._registered_kinds(record),
            str(authority["verifier_id"]),
            int(authority["verifier_abi"]),
            str(authority["authority_id"]),
            None,
            (("attestation-id", attestation_id), ("nonce", nonce)),
            ({"kind": "authenticated-workload-attestation", "locator": locator, "sha256": evidence_sha},),
        )

    def _registry_record(
        self, authority: Mapping[str, Any], collection: str, actor_id: str
    ) -> Mapping[str, Any]:
        records = authority.get(collection)
        if (
            not isinstance(records, Mapping)
            or actor_id not in records
            or not isinstance(records[actor_id], Mapping)
        ):
            raise IssuerPacketError("unregistered-actor", f"actor is not registered: {actor_id}")
        record = records[actor_id]
        if record.get("revoked") is not False:
            raise IssuerPacketError("revoked-actor", f"actor is revoked: {actor_id}")
        return record

    @staticmethod
    def _registered_kinds(source: Mapping[str, Any]) -> tuple[str, ...]:
        value = source.get("authorized_receipt_kinds")
        if (
            not isinstance(value, (list, tuple))
            or not value
            or any(item not in RECEIPT_KINDS for item in value)
        ):
            raise IssuerPacketError("invalid-authority-registry", "invalid receipt-kind authorization")
        if len(value) != len(set(value)):
            raise IssuerPacketError("invalid-authority-registry", "duplicate receipt-kind authorization")
        return tuple(kind for kind in RECEIPT_KINDS if kind in value)

    def _verify_signature(self, data: Mapping[str, Any], record: Mapping[str, Any]) -> None:
        signature = data.get("signature")
        if not isinstance(signature, dict) or set(signature) != {"algorithm", "key_id", "value"}:
            raise IssuerPacketError("invalid-attestation", "detached signature fields are incomplete")
        expected_key_id = record.get("key_id")
        if signature.get("algorithm") != "hmac-sha256" or signature.get("key_id") != expected_key_id:
            raise IssuerPacketError("identity-drift", "attestation key does not match the registry record")
        try:
            secret = self.key_resolver(str(expected_key_id))
        except Exception as exc:
            raise IssuerPacketError(
                "authority-unavailable", f"verification key resolver failed: {type(exc).__name__}"
            ) from None
        if not isinstance(secret, bytes) or not secret:
            raise IssuerPacketError("authority-unavailable", f"verification key unavailable: {expected_key_id}")
        signed = dict(data)
        signed.pop("signature", None)
        expected = hmac.new(secret, canonical_json_bytes(signed), hashlib.sha256).hexdigest()
        actual = signature.get("value")
        if not isinstance(actual, str) or not _HEX_64.fullmatch(actual) or not hmac.compare_digest(expected, actual):
            raise IssuerPacketError("forged-attestation", "attestation signature is invalid")

    def _verify_ci_constraints(self, data: Mapping[str, Any], record: Mapping[str, Any]) -> None:
        repository = record.get("repository")
        environment = record.get("environment")
        prefixes = record.get("allowed_ref_prefixes")
        ref = data.get("ref")
        if data.get("repository") != repository or data.get("environment") != environment:
            raise IssuerPacketError("identity-drift", "CI repository or environment does not match registry")
        if (
            not self._valid_git_ref(ref)
            or not isinstance(prefixes, tuple)
            or not prefixes
            or any(not isinstance(prefix, str) or not prefix for prefix in prefixes)
            or not any(ref.startswith(prefix) and len(ref) > len(prefix) for prefix in prefixes)
        ):
            raise IssuerPacketError("identity-drift", "CI ref is not authorized by the registry")

    def _reject_copied_subject(
        self,
        issuer: _VerifiedIssuer,
        issuer_instance_id: str,
        subject: Mapping[str, Any],
    ) -> None:
        if not isinstance(subject, Mapping):
            raise IssuerPacketError("invalid-request", "subject_identity must be an object")
        subject_actor_ids = {subject.get("actor_id"), subject.get("agent_id")}
        local_same_session = (
            any(item["kind"] == "local-session-attestation" for item in issuer.provenance)
            and subject.get("client") == "codex"
            and issuer.session_id == subject.get("session_id")
        )
        if (
            local_same_session or issuer.actor_id in subject_actor_ids
            or issuer_instance_id == subject.get("run_id")
            or issuer.actor_run_id == subject.get("run_id")
            or any(issuer_instance_id == value for _kind, value in issuer.replay_claims)
        ):
            raise IssuerPacketError("copied-subject-identity", "issuer identity is copied from the subject")

    def _publish_exclusive(
        self,
        packet_locator: str,
        packet_bytes: bytes,
        *,
        replay_claims: tuple[tuple[str, str], ...],
        issuer_instance_id: str,
        revalidate_trust: Callable[[], None],
    ) -> None:
        store = self.repo_root / self.store_locator
        claims = store / ".attestation-claims"
        self._ensure_directory(self.store_locator)
        self._ensure_directory(f"{self.store_locator}/.attestation-claims")
        packet_path = _assert_no_symlink_path(self.repo_root, packet_locator, allow_missing_leaf=True)
        if packet_path.exists():
            raise IssuerPacketError("duplicate-packet-identity", f"packet already exists: {packet_locator}")
        normalized_claims = sorted(set(replay_claims))
        if len(normalized_claims) != len(replay_claims) or not normalized_claims:
            raise IssuerPacketError("invalid-evidence", "replay claims must be unique and non-empty")
        acquired: list[Path] = []
        try:
            for claim_kind, claim_value in normalized_claims:
                if not claim_kind or not claim_value:
                    raise IssuerPacketError("invalid-evidence", "replay claim is incomplete")
                claim_identity = f"{claim_kind}:{claim_value}"
                claim_path = claims / (sha256_bytes(claim_identity.encode("utf-8")) + ".json")
                claim_bytes = canonical_json_bytes(
                    {
                        "claim_kind": claim_kind,
                        "claim_value": claim_value,
                        "issuer_instance_id": issuer_instance_id,
                    }
                )
                try:
                    self._create_file_exclusive(claim_path, claim_bytes)
                except FileExistsError:
                    raise IssuerPacketError(
                        "replayed-attestation", f"authority {claim_kind} was already consumed"
                    ) from None
                except OSError as exc:
                    raise IssuerPacketError(
                        "publication-failed", f"cannot persist replay claim: {exc}"
                    ) from None
                acquired.append(claim_path)

            self._install_packet_atomic(packet_path, packet_bytes, revalidate_trust)
        except Exception:
            self._remove_claims(acquired)
            raise

    def _install_packet_atomic(
        self,
        final_path: Path,
        content: bytes,
        revalidate: Callable[[], None],
    ) -> None:
        temp_path = final_path.parent / f"{final_path.stem}.{uuid.uuid4()}.tmp"
        final_linked = False
        try:
            self._write_temp_file(temp_path, content)
            revalidate()
            os.link(temp_path, final_path, follow_symlinks=False)
            final_linked = True
            revalidate()
            self._fsync_directory(final_path.parent)
            temp_path.unlink()
            self._fsync_directory(final_path.parent)
        except FileExistsError:
            temp_path.unlink(missing_ok=True)
            raise IssuerPacketError(
                "duplicate-packet-identity", f"packet already exists: {final_path.name}"
            ) from None
        except IssuerPacketError:
            if final_linked:
                final_path.unlink(missing_ok=True)
            temp_path.unlink(missing_ok=True)
            self._fsync_directory_best_effort(final_path.parent)
            raise
        except OSError as exc:
            if final_linked:
                final_path.unlink(missing_ok=True)
            temp_path.unlink(missing_ok=True)
            self._fsync_directory_best_effort(final_path.parent)
            raise IssuerPacketError("publication-failed", f"atomic packet install failed: {exc}") from None

    @staticmethod
    def _write_temp_file(path: Path, content: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(fd)

    def _remove_claims(self, paths: list[Path]) -> None:
        if not paths:
            return
        for path in paths:
            path.unlink(missing_ok=True)
        self._fsync_directory_best_effort(paths[0].parent)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        directory_fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @classmethod
    def _fsync_directory_best_effort(cls, path: Path) -> None:
        try:
            cls._fsync_directory(path)
        except OSError:
            pass

    def _ensure_directory(self, locator: str) -> None:
        current = self.repo_root
        for part in PurePosixPath(_safe_locator(locator)).parts:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    mode = current.lstat().st_mode
                    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                        raise IssuerPacketError("unsafe-locator", f"unsafe directory: {locator}") from None
                continue
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise IssuerPacketError("unsafe-locator", f"unsafe directory: {locator}")

    @staticmethod
    def _create_file_exclusive(path: Path, content: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(fd)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
