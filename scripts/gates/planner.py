"""Pure, fail-closed Gate plan compiler with immutable input binding."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import shlex
import stat
import unicodedata
import uuid
from pathlib import PurePosixPath
from typing import Any

import yaml

from scripts.gates.evidence_packet import (
    AGENT_ID_PATTERN,
    CODEX_MAIN_TASK_PROJECTION_SCHEMA_VERSION,
    CODEX_TASK_PROJECTION_SCHEMA_VERSION,
    EvidencePacketError,
    validate_codex_main_task_projection,
    validate_codex_work_package_task_projection,
    verify_packet_strict,
)
from scripts.gates.dispatch_preflight import parse_path_expression, path_contains

PLAN_SCHEMA = "lexiflow.gate-plan.v1"
QODER_TASK_PROJECTION_SCHEMA_VERSION = "lexiflow.qoder-work-package-projection.v1"
ISSUER_PACKET_SCHEMA = "lexiflow.trusted-issuer-packet.v1"
ISSUER_REGISTRY_SCHEMA = "lexiflow.gate-issuer-authorities.v1"
ISSUER_ATTESTATION_SCHEMA = "lexiflow.gate-issuer-attestation.v1"
REGISTRY_SCHEMA = "lexiflow.gate-check-registry.v1"
OUTCOME_SCHEMA = "lexiflow.check-outcome.v1"
RECEIPT_KINDS = ("TASK_VALIDATION", "INDEPENDENT_REVIEW", "CATALOG_DECISION")
VALID_MODES = ("incremental", "full")

_HEX = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_TASK_ID = re.compile(r"^LF-TSK-[A-Z]+-\d{4}$")
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_RFC3339_UTC = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?Z$"
)
_TASK_CRITERION = re.compile(r"^(LF-TSK-[A-Z]+-\d{4})\.acceptance_criteria\[(0|[1-9]\d*)\]$")
_CASE = re.compile(r"^## (LF-[A-Z0-9-]+)\b", re.MULTILINE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAX_SAFE_INT = 9007199254740991
_LEGACY_REGISTRY_COMMANDS = {
    ("LF-TSK-QLT-0002", 1, "1.0.0"): "python3 -m scripts.gates.planning --root .",
}

_QODER_REQUIRED_HANDOFF_FIELDS = {
    "goal", "task_id", "task_source", "task_version", "change_version",
    "work_package_id", "task_ids", "task_versions", "change_versions",
    "estimated_minutes", "primary_owner", "contract_boundary", "agent_profile",
    "harness_manifest", "allowed_files", "forbidden_files", "required_context",
    "expected_output", "acceptance_criteria", "acceptance_evidence",
    "validation_command", "failure_policy", "parent_client",
}
_QODER_RUNTIME_FIELDS = {
    "agent_id", "run_id", "session_id", "client",
    "harness_manifest_sha256", "harness_context",
}

REASON_CODES = frozenset({
    "catalog-drift", "command-mismatch", "dependency-version-mismatch",
    "duplicate-check-id", "duplicate-command-id", "duplicate-declared-command",
    "duplicate-subject-task", "duplicate-task-id", "entry-hash-mismatch", "evidence-drift",
    "evidence-packet-incomplete", "evidence-packet-schema-mismatch", "input-drift",
    "invalid-argv", "invalid-canonical-json", "invalid-consumed-input", "invalid-cwd",
    "invalid-dependency", "invalid-duplicate-key", "invalid-entry", "invalid-hash",
    "invalid-issuer-packet", "invalid-mapping", "invalid-mode", "invalid-owner",
    "invalid-receipt-kind", "invalid-registry", "invalid-scope", "invalid-task",
    "invalid-timeout", "invalid-trigger", "issuer-packet-schema-mismatch",
    "issuer-unauthorized-receipt", "missing-task", "multiple-command-match",
    "non-current-task-version", "reconciliation-fail", "registry-drift",
    "stale-dependency", "symlink-unsafe", "task-reconciliation-fail",
    "task-source-drift", "unsafe-locator",
})


class PlannerError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        if code not in REASON_CODES:
            raise RuntimeError(f"unregistered planner reason code: {code}")
        self.code = code
        self.result = "FAIL"
        super().__init__(f"{code}: {detail}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_value(value: Any, path: str = "value", active: set[int] | None = None) -> None:
    if active is None:
        active = set()
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INT:
            raise PlannerError("invalid-canonical-json", f"{path} integer exceeds JCS safe range")
        return
    if isinstance(value, float):
        raise PlannerError("invalid-canonical-json", f"{path} floats are forbidden")
    if isinstance(value, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise PlannerError("invalid-canonical-json", f"{path} contains a surrogate")
        return
    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise PlannerError("invalid-canonical-json", f"{path} contains a recursive container")
        active.add(identity)
        try:
            for i, item in enumerate(value):
                _canonical_value(item, f"{path}[{i}]", active)
        finally:
            active.remove(identity)
        return
    if isinstance(value, dict):
        identity = id(value)
        if identity in active:
            raise PlannerError("invalid-canonical-json", f"{path} contains a recursive container")
        active.add(identity)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise PlannerError("invalid-canonical-json", f"{path} has a non-string key")
                _canonical_value(key, f"{path}.<key>", active)
                _canonical_value(item, f"{path}.{key}", active)
        finally:
            active.remove(identity)
        return
    raise PlannerError("invalid-canonical-json", f"{path} has unsupported type {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    _canonical_value(value)
    def ordered(item: Any) -> Any:
        if isinstance(item, list):
            return [ordered(child) for child in item]
        if isinstance(item, dict):
            return {
                key: ordered(item[key])
                for key in sorted(item, key=lambda text: text.encode("utf-16-be"))
            }
        return item
    try:
        return json.dumps(ordered(value), ensure_ascii=False, sort_keys=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise PlannerError("invalid-canonical-json", str(exc)) from None


def _upstream_canonical_json_bytes(value: Any) -> bytes:
    """Preserve the accepted QLT7/QLT14 canonical byte contract."""
    _canonical_value(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise PlannerError("invalid-canonical-json", str(exc)) from None


def _safe_locator(locator: Any) -> str:
    if not isinstance(locator, str) or not locator or _CONTROL.search(locator):
        raise PlannerError("unsafe-locator", "locator must be a non-empty control-free string")
    if "\\" in locator or any(char in locator for char in "*?[]{}") or os.path.isabs(locator) or unicodedata.normalize("NFC", locator) != locator:
        raise PlannerError("unsafe-locator", f"unsafe locator: {locator!r}")
    path = PurePosixPath(locator)
    if path.is_absolute() or path.as_posix() != locator:
        raise PlannerError("unsafe-locator", f"unnormalized locator: {locator!r}")
    if any(part in {"", ".", ".."} or part.lower() == "latest" for part in path.parts):
        raise PlannerError("unsafe-locator", f"unsafe locator segment: {locator!r}")
    return locator


def _hash(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise PlannerError("invalid-hash", f"{path} must be lowercase SHA-256")
    return value


def _open_root(root: str, expected_inode: tuple[int, int] | None = None) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    current: int | None = None
    try:
        current = os.open(os.path.sep, flags)
        for part in (part for part in os.path.abspath(root).split(os.path.sep) if part):
            before = os.stat(part, dir_fd=current, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                raise PlannerError("symlink-unsafe", f"symlink repository ancestor: {root}")
            if not stat.S_ISDIR(before.st_mode):
                raise PlannerError("unsafe-locator", f"non-directory repository ancestor: {root}")
            child = os.open(part, flags, dir_fd=current)
            after = os.fstat(child)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                os.close(child)
                raise PlannerError("input-drift", "repository ancestor changed while opening")
            os.close(current)
            current = child
        root_stat = os.fstat(current)
        inode = (root_stat.st_dev, root_stat.st_ino)
        if expected_inode is not None and inode != expected_inode:
            raise PlannerError("input-drift", "repository root changed")
        result = current
        current = None
        return result
    except PlannerError:
        raise
    except OSError as exc:
        raise PlannerError("unsafe-locator", f"cannot safely open repo_root: {exc}") from None
    finally:
        if current is not None:
            try: os.close(current)
            except OSError: pass


def _safe_read(root: str, root_inode: tuple[int, int], locator: str) -> bytes:
    locator = _safe_locator(locator)
    dflags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fflags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    dirs: list[int] = []
    file_fd: int | None = None
    try:
        current = _open_root(root, root_inode)
        dirs.append(current)
        parts = locator.split("/")
        for part in parts[:-1]:
            before = os.stat(part, dir_fd=current, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                raise PlannerError("symlink-unsafe", f"symlink ancestor: {locator}")
            if not stat.S_ISDIR(before.st_mode):
                raise PlannerError("unsafe-locator", f"non-directory ancestor: {locator}")
            child = os.open(part, dflags, dir_fd=current)
            after = os.fstat(child)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                os.close(child)
                raise PlannerError("input-drift", f"ancestor changed while opening: {locator}")
            dirs.append(child)
            current = child
        before = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode):
            raise PlannerError("symlink-unsafe", f"symlink leaf: {locator}")
        if not stat.S_ISREG(before.st_mode):
            raise PlannerError("unsafe-locator", f"non-regular input: {locator}")
        file_fd = os.open(parts[-1], fflags, dir_fd=current)
        after = os.fstat(file_fd)
        if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise PlannerError("input-drift", f"input changed while opening: {locator}")
        chunks = []
        while True:
            chunk = os.read(file_fd, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except PlannerError:
        raise
    except OSError as exc:
        raise PlannerError("unsafe-locator", f"cannot safely read {locator}: {exc}") from None
    finally:
        if file_fd is not None:
            try: os.close(file_fd)
            except OSError: pass
        for fd in reversed(dirs):
            try: os.close(fd)
            except OSError: pass


class _Inputs:
    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        fd = _open_root(self.root)
        try:
            st = os.fstat(fd)
            self.root_inode = (st.st_dev, st.st_ino)
        finally: os.close(fd)
        self.data: dict[str, bytes] = {}
        self.hashes: dict[str, str] = {}

    def get(self, locator: str, expected: str | None = None, *, mismatch: str = "input-drift") -> tuple[bytes, str]:
        locator = _safe_locator(locator)
        if expected is not None: _hash(expected, f"{locator}.sha256")
        if locator in self.hashes:
            if expected is not None and not hmac.compare_digest(self.hashes[locator], expected):
                raise PlannerError(mismatch, f"hash mismatch: {locator}")
            return self.data[locator], self.hashes[locator]
        content = _safe_read(self.root, self.root_inode, locator)
        digest = sha256_bytes(content)
        if expected is not None and not hmac.compare_digest(digest, expected):
            raise PlannerError(mismatch, f"hash mismatch: {locator}")
        self.data[locator] = content
        self.hashes[locator] = digest
        return content, digest

    def descriptor(self, locator: str) -> dict:
        if locator not in self.hashes:
            raise PlannerError("invalid-consumed-input", f"not frozen: {locator}")
        return {"locator": locator, "state": "present", "sha256": self.hashes[locator]}

    def descriptors(self) -> list[dict]:
        return [self.descriptor(locator) for locator in self.hashes]

    def revalidate(self) -> None:
        for locator, digest in self.hashes.items():
            if not hmac.compare_digest(sha256_bytes(_safe_read(self.root, self.root_inode, locator)), digest):
                raise PlannerError("input-drift", f"changed during compile: {locator}")


def _json(content: bytes, label: str, *, canonical: bool = False) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PlannerError("invalid-duplicate-key", f"duplicate key in {label}: {key!r}")
            result[key] = value
        return result
    def bad_number(text):
        raise PlannerError("invalid-canonical-json", f"non-integer number in {label}: {text}")
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=pairs,
                           parse_float=bad_number, parse_constant=bad_number)
    except PlannerError: raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlannerError("invalid-canonical-json", f"invalid JSON in {label}: {exc}") from None
    _canonical_value(value, label)
    if canonical and _upstream_canonical_json_bytes(value) != content:
        raise PlannerError("invalid-canonical-json", f"{label} is not canonical JSON")
    return value


def _yaml(content: bytes, label: str) -> Any:
    class Loader(yaml.SafeLoader): pass
    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=True)
            try: hash(key)
            except (TypeError, ValueError):
                raise PlannerError("invalid-duplicate-key", f"unhashable key in {label}") from None
            if key in result:
                raise PlannerError("invalid-duplicate-key", f"duplicate key in {label}: {key!r}")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result
    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try: value = yaml.load(content.decode("utf-8"), Loader=Loader)
    except PlannerError: raise
    except (UnicodeDecodeError, yaml.YAMLError, TypeError, ValueError) as exc:
        raise PlannerError("invalid-registry", f"invalid YAML in {label}: {exc}") from None
    _canonical_value(value, label)
    return value


def _keys(value: Any, expected: set[str], path: str, code: str = "invalid-entry") -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        actual = set(value) if isinstance(value, dict) else set()
        raise PlannerError(code, f"{path} keys differ: missing={sorted(expected-actual)} extra={sorted(actual-expected)}")
    return value


def _string(value: Any, path: str, *, safe: bool = False) -> str:
    if not isinstance(value, str) or not value.strip() or _CONTROL.search(value):
        raise PlannerError("invalid-entry", f"{path} must be a non-empty control-free string")
    if safe and not _SAFE_ID.fullmatch(value):
        raise PlannerError("invalid-entry", f"{path} is not a safe id")
    return value


def _strings(value: Any, path: str, *, empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not empty and not value):
        raise PlannerError("invalid-entry", f"{path} must be a list")
    if any(not isinstance(item, str) or not item.strip() or _CONTROL.search(item) for item in value):
        raise PlannerError("invalid-entry", f"{path} has invalid strings")
    if len(value) != len(set(value)):
        raise PlannerError("invalid-entry", f"{path} has duplicates")
    return value


def _positive(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PlannerError("invalid-entry", f"{path} must be a positive integer")
    return value


def _uuid(value: Any, path: str, *, v4: bool = False) -> str:
    if not isinstance(value, str):
        raise PlannerError("invalid-issuer-packet", f"{path} must be UUID")
    try: parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise PlannerError("invalid-issuer-packet", f"{path} must be UUID") from None
    if parsed.int == 0 or str(parsed) != value or parsed.variant != uuid.RFC_4122 or parsed.version not in range(1, 9) or (v4 and parsed.version != 4):
        raise PlannerError("invalid-issuer-packet", f"{path} must be canonical RFC UUID")
    return value


def _timestamp(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _RFC3339_UTC.fullmatch(value):
        raise PlannerError("invalid-issuer-packet", f"{path} must be RFC3339 UTC")
    try: parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise PlannerError("invalid-issuer-packet", f"{path} must be RFC3339 UTC") from None
    if parsed.tzinfo != dt.timezone.utc:
        raise PlannerError("invalid-issuer-packet", f"{path} must be UTC")
    return value


def _under(locator: str, roots: list[str]) -> bool:
    return any(locator.startswith(root + "/") for root in roots)


def _issuer_registry(value: Any) -> dict:
    value = _keys(value, {"schema_version", "owner", "packet_store_locator",
        "attestation_max_age_seconds", "attestation_audience", "authorities"},
        "issuer_registry", "invalid-issuer-packet")
    if value["schema_version"] != ISSUER_REGISTRY_SCHEMA or value["owner"] != "LF-WS-QLT" or value["packet_store_locator"] != "tmp/quality/issuers":
        raise PlannerError("invalid-issuer-packet", "issuer registry identity mismatch")
    _positive(value["attestation_max_age_seconds"], "issuer_registry.max_age")
    _string(value["attestation_audience"], "issuer_registry.audience")
    if not isinstance(value["authorities"], dict) or set(value["authorities"]) != {"qoder", "codex", "human", "ci"}:
        raise PlannerError("invalid-issuer-packet", "issuer authority set mismatch")
    simple = {"available", "authority_id", "verifier_id", "verifier_abi", "evidence_roots", "role", "authorized_receipt_kinds"}
    human = {"available", "authority_id", "verifier_id", "verifier_abi", "evidence_roots", "actors"}
    ci = {"available", "authority_id", "verifier_id", "verifier_abi", "evidence_roots", "workloads"}
    _keys(value["authorities"]["qoder"], simple, "issuer_registry.qoder", "invalid-issuer-packet")
    _keys(value["authorities"]["codex"], simple, "issuer_registry.codex", "invalid-issuer-packet")
    human_record = _keys(value["authorities"]["human"], human, "issuer_registry.human", "invalid-issuer-packet")
    ci_record = _keys(value["authorities"]["ci"], ci, "issuer_registry.ci", "invalid-issuer-packet")
    for actor_type, authority in value["authorities"].items():
        if not isinstance(authority.get("available"), bool):
            raise PlannerError("invalid-issuer-packet", f"{actor_type} availability must be boolean")
        if not isinstance(authority.get("authority_id"), str) or not _SAFE_ID.fullmatch(authority["authority_id"]):
            raise PlannerError("invalid-issuer-packet", f"{actor_type} authority_id is invalid")
        if not isinstance(authority.get("verifier_id"), str) or not _SAFE_ID.fullmatch(authority["verifier_id"]):
            raise PlannerError("invalid-issuer-packet", f"{actor_type} verifier_id is invalid")
        if isinstance(authority.get("verifier_abi"), bool) or not isinstance(authority.get("verifier_abi"), int) or authority["verifier_abi"] <= 0:
            raise PlannerError("invalid-issuer-packet", f"{actor_type} verifier_abi is invalid")
        roots = authority.get("evidence_roots")
        if not isinstance(roots, list) or not roots or len(roots) != len(set(roots)):
            raise PlannerError("invalid-issuer-packet", f"{actor_type} evidence_roots are invalid")
        for root in roots:
            _safe_locator(root)
    if not isinstance(human_record["actors"], dict) or not human_record["actors"]:
        raise PlannerError("invalid-issuer-packet", "human actor registry is empty")
    if not isinstance(ci_record["workloads"], dict) or not ci_record["workloads"]:
        raise PlannerError("invalid-issuer-packet", "CI workload registry is empty")
    for actor_id, actor in human_record["actors"].items():
        _string(actor_id, "issuer_registry.human.actor_id", safe=True)
        _keys(actor, {"revoked", "role", "key_id", "authorized_receipt_kinds"},
              f"issuer_registry.human.actors.{actor_id}", "invalid-issuer-packet")
        if not isinstance(actor["revoked"], bool) or not isinstance(actor["role"], str) or not _SAFE_ID.fullmatch(actor["role"]) or not isinstance(actor["key_id"], str) or not _SAFE_ID.fullmatch(actor["key_id"]):
            raise PlannerError("invalid-issuer-packet", f"human actor {actor_id} fields are invalid")
        if not isinstance(actor["authorized_receipt_kinds"], list) or not actor["authorized_receipt_kinds"] or len(actor["authorized_receipt_kinds"]) != len(set(actor["authorized_receipt_kinds"])) or any(kind not in RECEIPT_KINDS for kind in actor["authorized_receipt_kinds"]):
            raise PlannerError("invalid-issuer-packet", f"human actor {actor_id} receipt kinds are invalid")
    for workload_id, workload in ci_record["workloads"].items():
        _string(workload_id, "issuer_registry.ci.workload_id", safe=True)
        _keys(workload, {"revoked", "role", "key_id", "repository", "allowed_ref_prefixes",
                         "environment", "authorized_receipt_kinds"},
              f"issuer_registry.ci.workloads.{workload_id}", "invalid-issuer-packet")
        if not isinstance(workload["revoked"], bool) or not isinstance(workload["role"], str) or not _SAFE_ID.fullmatch(workload["role"]) or not isinstance(workload["key_id"], str) or not _SAFE_ID.fullmatch(workload["key_id"]):
            raise PlannerError("invalid-issuer-packet", f"CI workload {workload_id} fields are invalid")
        for field in ("repository", "environment"):
            if not isinstance(workload[field], str) or not workload[field].strip() or _CONTROL.search(workload[field]):
                raise PlannerError("invalid-issuer-packet", f"CI workload {workload_id} {field} is invalid")
        prefixes = workload["allowed_ref_prefixes"]
        if not isinstance(prefixes, list) or not prefixes or len(prefixes) != len(set(prefixes)) or any(not isinstance(prefix, str) or not prefix.startswith("refs/") or _CONTROL.search(prefix) for prefix in prefixes):
            raise PlannerError("invalid-issuer-packet", f"CI workload {workload_id} ref prefixes are invalid")
        kinds = workload["authorized_receipt_kinds"]
        if not isinstance(kinds, list) or not kinds or len(kinds) != len(set(kinds)) or any(kind not in RECEIPT_KINDS for kind in kinds):
            raise PlannerError("invalid-issuer-packet", f"CI workload {workload_id} receipt kinds are invalid")
    for actor_type in ("qoder", "codex"):
        authority = value["authorities"][actor_type]
        if not isinstance(authority["role"], str) or not _SAFE_ID.fullmatch(authority["role"]):
            raise PlannerError("invalid-issuer-packet", f"{actor_type} role is invalid")
        kinds = authority["authorized_receipt_kinds"]
        if not isinstance(kinds, list) or not kinds or len(kinds) != len(set(kinds)) or any(kind not in RECEIPT_KINDS for kind in kinds):
            raise PlannerError("invalid-issuer-packet", f"{actor_type} receipt kinds are invalid")
    return value


def _authority(packet: dict, registry: dict) -> tuple[dict, dict, list[str]]:
    actor_type = packet["actor_type"]
    if actor_type not in {"qoder", "codex", "human", "ci"}:
        raise PlannerError("invalid-issuer-packet", "unknown issuer actor type")
    authority = registry["authorities"][actor_type]
    if not isinstance(authority, dict) or authority.get("available") is not True:
        raise PlannerError("invalid-issuer-packet", "issuer authority unavailable")
    for field in ("authority_id", "verifier_id"): _string(authority.get(field), f"authority.{field}", safe=True)
    _positive(authority.get("verifier_abi"), "authority.verifier_abi")
    roots = _strings(authority.get("evidence_roots"), "authority.evidence_roots")
    for root in roots: _safe_locator(root)
    if actor_type in {"qoder", "codex"}:
        actor = authority
    elif actor_type == "human":
        records = authority.get("actors")
        actor = records.get(packet["actor_id"]) if isinstance(records, dict) else None
    else:
        records = authority.get("workloads")
        actor = records.get(packet["actor_id"]) if isinstance(records, dict) else None
    if not isinstance(actor, dict) or actor.get("revoked") is True:
        raise PlannerError("invalid-issuer-packet", "issuer actor is not registered/current")
    _string(actor.get("role"), "authority.role", safe=True)
    allowed = _strings(actor.get("authorized_receipt_kinds"), "authority.authorized_receipt_kinds")
    if any(kind not in RECEIPT_KINDS for kind in allowed):
        raise PlannerError("invalid-issuer-packet", "authority registry contains an unknown receipt kind")
    return authority, actor, allowed


def _attestation(packet: dict, evidence: dict, registry: dict, actor: dict) -> str:
    actor_type = packet["actor_type"]
    common = {"schema_version", "actor_type", "attestation_id", "audience", "nonce",
              "issued_at", "expires_at", "session_id", "client"}
    if actor_type == "codex":
        expected_keys = common | {"actor_id", "parent_session_id"}
    elif actor_type == "human":
        expected_keys = common | {"operator_id", "signature"}
    else:
        expected_keys = common | {"workload_id", "repository", "ref", "environment", "signature"}
    _keys(evidence, expected_keys, "authority_evidence", "invalid-issuer-packet")
    if evidence.get("schema_version") != ISSUER_ATTESTATION_SCHEMA or evidence.get("actor_type") != actor_type:
        raise PlannerError("invalid-issuer-packet", "authority evidence schema/actor mismatch")
    for field in ("attestation_id", "nonce"): _uuid(evidence.get(field), f"evidence.{field}", v4=True)
    _uuid(evidence.get("session_id"), "evidence.session_id")
    for field in ("issued_at", "expires_at"): _timestamp(evidence.get(field), f"evidence.{field}")
    if evidence.get("audience") != registry["attestation_audience"] or evidence.get("session_id") != packet["session_id"] or evidence.get("client") != packet["client"]:
        raise PlannerError("invalid-issuer-packet", "authority evidence context mismatch")
    if actor_type == "codex":
        if evidence.get("actor_id") != packet["actor_id"] or evidence.get("parent_session_id") != packet["parent_session_id"]:
            raise PlannerError("invalid-issuer-packet", "Codex evidence identity mismatch")
        return "host-session-attestation"
    field = "operator_id" if actor_type == "human" else "workload_id"
    if evidence.get(field) != packet["actor_id"]:
        raise PlannerError("invalid-issuer-packet", "signed evidence actor mismatch")
    signature = evidence.get("signature")
    if not isinstance(signature, dict) or set(signature) != {"algorithm", "key_id", "value"} or signature.get("algorithm") != "hmac-sha256" or signature.get("key_id") != actor.get("key_id"):
        raise PlannerError("invalid-issuer-packet", "signed evidence key binding mismatch")
    _hash(signature.get("value"), "evidence.signature.value")
    if actor_type == "ci":
        if evidence.get("repository") != actor.get("repository") or evidence.get("environment") != actor.get("environment"):
            raise PlannerError("invalid-issuer-packet", "CI context mismatch")
        prefixes = actor.get("allowed_ref_prefixes")
        if not isinstance(prefixes, list) or not isinstance(evidence.get("ref"), str) or not any(evidence["ref"].startswith(p) for p in prefixes):
            raise PlannerError("invalid-issuer-packet", "CI ref mismatch")
    return (
        "authenticated-operator-attestation"
        if actor_type == "human"
        else "authenticated-workload-attestation"
    )


def _freeze_issuer(inputs: _Inputs, locator: str, expected: str, receipt_kind: str) -> tuple[dict, dict]:
    prefix = "tmp/quality/issuers/"
    locator = _safe_locator(locator)
    if not locator.startswith(prefix) or "/" in locator[len(prefix):] or not locator.endswith(".json"):
        raise PlannerError("invalid-issuer-packet", "issuer locator namespace mismatch")
    content, digest = inputs.get(locator, expected, mismatch="invalid-issuer-packet")
    packet = _json(content, "issuer_packet", canonical=True)
    packet = _keys(packet, {"schema_version", "issuer_instance_id", "actor_type", "actor_id",
        "parent_session_id", "session_id", "client", "role", "authorized_receipt_kinds",
        "authority", "materialized_at"}, "issuer_packet", "invalid-issuer-packet")
    if packet["schema_version"] != ISSUER_PACKET_SCHEMA:
        raise PlannerError("issuer-packet-schema-mismatch", "issuer schema mismatch")
    instance = _uuid(packet["issuer_instance_id"], "issuer.instance", v4=True)
    if locator != f"{prefix}{instance}.json":
        raise PlannerError("invalid-issuer-packet", "issuer instance/locator mismatch")
    for field in ("actor_type", "client", "role"): _string(packet[field], f"issuer.{field}", safe=True)
    actor_id = _string(packet["actor_id"], "issuer.actor_id")
    actor_pattern = AGENT_ID_PATTERN if packet["actor_type"] == "codex" else _SAFE_ID
    if not actor_pattern.fullmatch(actor_id):
        raise PlannerError("invalid-issuer-packet", "issuer.actor_id is invalid for its actor type")
    if packet["parent_session_id"] is not None: _uuid(packet["parent_session_id"], "issuer.parent_session_id")
    if packet["actor_type"] in {"qoder", "codex"} and packet["parent_session_id"] is None:
        raise PlannerError("invalid-issuer-packet", "runner/host issuer requires parent_session_id")
    if packet["actor_type"] in {"human", "ci"} and packet["parent_session_id"] is not None:
        raise PlannerError("invalid-issuer-packet", "human/CI issuer cannot carry parent_session_id")
    _uuid(packet["session_id"], "issuer.session_id")
    _timestamp(packet["materialized_at"], "issuer.materialized_at")
    kinds = _strings(packet["authorized_receipt_kinds"], "issuer.authorized_receipt_kinds")
    if any(kind not in RECEIPT_KINDS for kind in kinds) or receipt_kind not in kinds:
        raise PlannerError("issuer-unauthorized-receipt", "issuer is not authorized for receipt kind")
    auth = _keys(packet["authority"], {"registry_locator", "registry_sha256", "authority_id",
        "verifier_id", "verifier_abi", "evidence_locator", "evidence_sha256", "provenance"},
        "issuer.authority", "invalid-issuer-packet")
    if auth["registry_locator"] != "harness/gate-issuer-authorities.yaml":
        raise PlannerError("invalid-issuer-packet", "authority registry locator mismatch")
    registry_bytes, registry_hash = inputs.get(auth["registry_locator"], auth["registry_sha256"], mismatch="invalid-issuer-packet")
    registry = _issuer_registry(_yaml(registry_bytes, "issuer_registry"))
    authority, actor, allowed = _authority(packet, registry)
    if isinstance(auth["verifier_abi"], bool) or not isinstance(auth["verifier_abi"], int) or auth["verifier_abi"] <= 0:
        raise PlannerError("invalid-issuer-packet", "issuer verifier_abi must be a positive integer")
    for field in ("authority_id", "verifier_id", "verifier_abi"):
        if auth[field] != authority.get(field): raise PlannerError("invalid-issuer-packet", f"authority {field} mismatch")
    if packet["role"] != actor.get("role"):
        raise PlannerError("invalid-issuer-packet", "issuer role mismatch")
    if not set(kinds).issubset(set(allowed)):
        raise PlannerError("issuer-unauthorized-receipt", "issuer expands registered authorization")
    evidence_locator = _safe_locator(auth["evidence_locator"])
    roots = authority["evidence_roots"]
    if not _under(evidence_locator, roots):
        raise PlannerError("invalid-issuer-packet", "authority evidence outside allowed root")
    evidence_bytes, evidence_hash = inputs.get(evidence_locator, auth["evidence_sha256"], mismatch="invalid-issuer-packet")
    provenance = auth["provenance"]
    if not isinstance(provenance, list) or not provenance:
        raise PlannerError("invalid-issuer-packet", "issuer provenance missing")
    frozen, seen = [], set()
    for i, ref in enumerate(provenance):
        ref = _keys(ref, {"kind", "locator", "sha256"}, f"provenance[{i}]", "invalid-issuer-packet")
        kind = _string(ref["kind"], f"provenance[{i}].kind", safe=True)
        prov_locator = _safe_locator(ref["locator"])
        if prov_locator in seen or not _under(prov_locator, roots):
            raise PlannerError("invalid-issuer-packet", "duplicate/out-of-root provenance")
        seen.add(prov_locator)
        _, prov_hash = inputs.get(prov_locator, ref["sha256"], mismatch="invalid-issuer-packet")
        frozen.append({"kind": kind, "locator": prov_locator, "sha256": prov_hash})
    if packet["actor_type"] == "qoder":
        # Qoder persists runner task/completion records with stable pretty JSON.
        # Their exact bytes are hash-bound provenance, while strict parsing still
        # rejects duplicate keys and non-integer/non-finite numbers.
        completion = _json(evidence_bytes, "qoder_completion")
        task_locator = (PurePosixPath(evidence_locator).parent / "task.json").as_posix()
        task_bytes, task_hash = inputs.get(task_locator, mismatch="invalid-issuer-packet")
        task = _json(task_bytes, "qoder_task")
        expected_provenance = [{"kind": "runner-task", "locator": task_locator, "sha256": task_hash},
            {"kind": "runner-completion", "locator": evidence_locator, "sha256": evidence_hash}]
        for source in (task, completion):
            for source_field, packet_field in (("agent_id", "actor_id"), ("session_id", "session_id"),
                    ("parent_session_id", "parent_session_id"), ("client", "client")):
                if source.get(source_field) != packet.get(packet_field):
                    raise PlannerError("invalid-issuer-packet", f"qoder provenance {source_field} mismatch")
        if task.get("run_id") != completion.get("run_id"):
            raise PlannerError("invalid-issuer-packet", "qoder run identity mismatch")
    else:
        evidence = _json(evidence_bytes, "authority_evidence", canonical=True)
        expected_kind = _attestation(packet, evidence, registry, actor)
        expected_provenance = [{"kind": expected_kind, "locator": evidence_locator, "sha256": evidence_hash}]
    if frozen != expected_provenance:
        raise PlannerError("invalid-issuer-packet", "issuer provenance set/order mismatch")
    return packet, {"locator": locator, "sha256": digest, "packet": copy.deepcopy(packet),
        "authority_registry": {"locator": auth["registry_locator"], "sha256": registry_hash, "owner": registry["owner"]},
        "authority_evidence": {"locator": evidence_locator, "sha256": evidence_hash}, "provenance": frozen}


def verify_trusted_issuer_packet(
    repo_root: str | os.PathLike[str], *, locator: str, sha256: str, receipt_kind: str,
) -> dict[str, Any]:
    """Pure-read verification of one materialized issuer against current authority inputs."""
    if receipt_kind not in RECEIPT_KINDS:
        raise PlannerError("invalid-receipt-kind", f"unknown receipt kind: {receipt_kind!r}")
    if not isinstance(repo_root, (str, os.PathLike)):
        raise PlannerError("unsafe-locator", "repo_root must be path-like")
    inputs = _Inputs(os.fspath(repo_root))
    _, frozen = _freeze_issuer(inputs, locator, sha256, receipt_kind)
    inputs.revalidate()
    return frozen


def _catalog(value: Any) -> tuple[dict[str, dict], dict[str, str], list[str]]:
    if not isinstance(value, dict) or not isinstance(value.get("workstreams"), list):
        raise PlannerError("invalid-task", "catalog topology invalid")
    tasks, owners, registry_subjects = {}, {}, []
    seen_workstreams = set()
    for workstream in value["workstreams"]:
        if not isinstance(workstream, dict) or not isinstance(workstream.get("epics", []), list): raise PlannerError("invalid-task", "workstream invalid")
        workstream_id = workstream.get("id")
        if not isinstance(workstream_id, str) or not _SAFE_ID.fullmatch(workstream_id) or not workstream_id.startswith("LF-WS-"):
            raise PlannerError("invalid-task", "workstream id invalid")
        if workstream_id in seen_workstreams:
            raise PlannerError("invalid-task", f"duplicate workstream id: {workstream_id}")
        seen_workstreams.add(workstream_id)
        for epic in workstream.get("epics", []):
            if not isinstance(epic, dict) or not isinstance(epic.get("capabilities", []), list): raise PlannerError("invalid-task", "epic invalid")
            for capability in epic.get("capabilities", []):
                if not isinstance(capability, dict) or not isinstance(capability.get("seed_tasks", []), list): raise PlannerError("invalid-task", "capability invalid")
                for task in capability.get("seed_tasks", []):
                    task_id = task.get("id") if isinstance(task, dict) else None
                    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id): raise PlannerError("invalid-task", "task id invalid")
                    if task_id in tasks: raise PlannerError("duplicate-task-id", f"duplicate task: {task_id}")
                    task_version = task.get("task_version")
                    change_version = task.get("change_version")
                    if isinstance(task_version, bool) or not isinstance(task_version, int) or task_version <= 0:
                        raise PlannerError("invalid-task", f"task {task_id} version invalid")
                    if not isinstance(change_version, str) or not _SEMVER.fullmatch(change_version):
                        raise PlannerError("invalid-task", f"task {task_id} change version invalid")
                    if "owner" in task:
                        explicit_owner = task["owner"]
                        if not isinstance(explicit_owner, str) or not _SAFE_ID.fullmatch(explicit_owner) or explicit_owner != workstream_id:
                            raise PlannerError("invalid-task", f"task {task_id} explicit owner invalid")
                        resolved_owner = explicit_owner
                    else:
                        resolved_owner = workstream_id
                    has_explicit_command = "validation_command" in task
                    if has_explicit_command:
                        command = task["validation_command"]
                        if not isinstance(command, str) or not command.strip() or _CONTROL.search(command):
                            raise PlannerError("invalid-task", f"task {task_id} validation command invalid")
                    legacy_command = _LEGACY_REGISTRY_COMMANDS.get((task_id, task_version, change_version))
                    if task_id == "LF-TSK-QLT-0002" and not has_explicit_command and legacy_command is None:
                        raise PlannerError("invalid-task", "legacy planning predeclaration version no longer applies")
                    if has_explicit_command and "owner" not in task:
                        raise PlannerError("invalid-task", f"task {task_id} with validation command requires explicit owner")
                    tasks[task_id] = task
                    owners[task_id] = resolved_owner
                    if has_explicit_command or legacy_command is not None:
                        registry_subjects.append(task_id)
    return tasks, owners, registry_subjects


def _dispatch(value: Any) -> tuple[str, tuple[str, ...]]:
    if (not isinstance(value, str) or not value or _CONTROL.search(value) or "\\" in value
            or value.startswith("/") or unicodedata.normalize("NFC", value) != value):
        raise PlannerError("invalid-trigger", f"invalid dispatch path: {value!r}")
    parts = value.split("/")
    if any(not p or p in {".", ".."} or p.lower() == "latest" for p in parts): raise PlannerError("invalid-trigger", f"invalid dispatch path: {value!r}")
    wild = [i for i, p in enumerate(parts) if any(ch in p for ch in "*?[]{}")]
    if not wild: return "exact", tuple(parts)
    if len(parts) < 2 or wild != [len(parts)-1] or parts[-1] not in {"*", "**"}: raise PlannerError("invalid-trigger", f"invalid wildcard: {value!r}")
    return ("child" if parts[-1] == "*" else "subtree"), tuple(parts[:-1])


def _match(pattern: tuple[str, tuple[str, ...]], changed: str) -> bool:
    kind, parts = _dispatch(changed)
    if kind != "exact": raise PlannerError("invalid-scope", f"changed path is not exact: {changed}")
    pkind, prefix = pattern
    if pkind == "exact": return parts == prefix
    if pkind == "child": return len(parts) == len(prefix)+1 and parts[:len(prefix)] == prefix
    return len(parts) > len(prefix) and parts[:len(prefix)] == prefix


def _csv(value: Any, path: str) -> list[str]:
    if not isinstance(value, str) or not value.strip(): raise PlannerError("invalid-task", f"{path} invalid")
    items = [item.strip() for item in value.split(",")]
    if any(not item for item in items) or len(items) != len(set(items)): raise PlannerError("invalid-task", f"{path} malformed")
    for item in items: _dispatch(item)
    return sorted(items)


def _minimal_path_union(values: list[str], path: str) -> list[str]:
    patterns = [parse_path_expression(value, f"{path}[{index}]") for index, value in enumerate(values)]
    return sorted(
        pattern.raw
        for index, pattern in enumerate(patterns)
        if not any(
            other.raw != pattern.raw and path_contains(other, pattern)
            for other_index, other in enumerate(patterns)
            if other_index != index
        )
    )


def _raw_task(inputs: _Inputs, packet: dict, record: dict, tasks: dict[str, dict], owners: dict[str, str]) -> tuple[dict, list[dict]]:
    desc = packet["subject"]["raw_artifacts"]["task"]
    raw = _json(inputs.get(desc["locator"], desc["sha256"], mismatch="evidence-drift")[0], "raw_task")
    identity = packet["subject"]["identity"]
    if identity["client"] == "qoder":
        required = _QODER_REQUIRED_HANDOFF_FIELDS | _QODER_RUNTIME_FIELDS | {
            "parent_session_id", "permission_mode",
        }
        optional = {"title", "_resume_mode"}
        if not isinstance(raw, dict) or not required.issubset(raw) or set(raw) - required - optional:
            raise PlannerError("invalid-task", "raw_task fields differ from the persisted Qoder contract")
        if "title" in raw:
            if not isinstance(raw["title"], str) or not raw["title"].strip():
                raise PlannerError("invalid-task", "raw_task.title must be non-empty when present")
        if "_resume_mode" in raw and raw["_resume_mode"] is not True:
            raise PlannerError("invalid-task", "raw_task._resume_mode must be true when present")
        if raw["permission_mode"] not in {"default", "accept_edits", "dont_ask", "bypass_permissions"}:
            raise PlannerError("invalid-task", "raw_task.permission_mode invalid")
        work_package_id = raw["work_package_id"]
        if not isinstance(work_package_id, str) or not _SAFE_ID.fullmatch(work_package_id):
            raise PlannerError("invalid-task", "raw_task.work_package_id invalid")
        package_task_ids = raw["task_ids"]
        if (
            not isinstance(package_task_ids, list)
            or len(package_task_ids) < 2
            or len(package_task_ids) != len(set(package_task_ids))
            or any(not isinstance(item, str) or not _TASK_ID.fullmatch(item) for item in package_task_ids)
            or raw["task_id"] not in package_task_ids
        ):
            raise PlannerError("invalid-task", "raw_task.task_ids invalid")
        estimated_minutes = raw["estimated_minutes"]
        if (
            isinstance(estimated_minutes, bool)
            or not isinstance(estimated_minutes, int)
            or not 180 <= estimated_minutes <= 360
        ):
            raise PlannerError("invalid-task", "raw_task.estimated_minutes must be 180..360")
        if raw["primary_owner"] != owners[record["id"]]:
            raise PlannerError("task-reconciliation-fail", "Qoder work-package owner differs from current catalog")
        for field in ("contract_boundary", "agent_profile", "harness_manifest"):
            _string(raw[field], f"raw_task.{field}")
        if not _SAFE_ID.fullmatch(raw["agent_profile"]):
            raise PlannerError("invalid-task", "raw_task.agent_profile invalid")
        manifest_kind, _ = _dispatch(raw["harness_manifest"])
        if manifest_kind != "exact":
            raise PlannerError("invalid-task", "raw_task.harness_manifest must be an exact repository path")
        if not isinstance(raw["harness_manifest_sha256"], str) or not _HEX.fullmatch(raw["harness_manifest_sha256"]):
            raise PlannerError("invalid-task", "raw_task.harness_manifest_sha256 invalid")
        harness_context = raw["harness_context"]
        if not isinstance(harness_context, list) or len(harness_context) < 3:
            raise PlannerError("invalid-task", "raw_task.harness_context invalid")
        context_paths = []
        for index, entry in enumerate(harness_context):
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
                raise PlannerError("invalid-task", f"raw_task.harness_context[{index}] invalid")
            context_kind, _ = _dispatch(entry["path"])
            if context_kind != "exact" or not isinstance(entry["sha256"], str) or not _HEX.fullmatch(entry["sha256"]):
                raise PlannerError("invalid-task", f"raw_task.harness_context[{index}] invalid")
            context_paths.append(entry["path"])
        if len(context_paths) != len(set(context_paths)):
            raise PlannerError("invalid-task", "raw_task.harness_context paths must be unique")
        mandatory_context = {
            "AGENTS.md", ".qoder/AGENTS.md", f".qoder/agents/{raw['agent_profile']}.md",
            "planning/workstreams.yaml",
            "harness/agent-policy.manifest.yaml",
            "harness/agent-runtime.manifest.yaml",
        }
        if not mandatory_context.issubset(context_paths):
            raise PlannerError("invalid-task", "raw_task.harness_context is missing mandatory Qoder policy files")
        for field, validator in (("task_versions", "task"), ("change_versions", "change")):
            values = raw[field]
            if not isinstance(values, dict) or set(values) != set(package_task_ids):
                raise PlannerError("invalid-task", f"raw_task.{field} keys differ from task_ids")
            for package_task_id in package_task_ids:
                value = values[package_task_id]
                if validator == "task":
                    valid = not isinstance(value, bool) and isinstance(value, int) and value > 0
                else:
                    valid = isinstance(value, str) and bool(_SEMVER.fullmatch(value))
                if not valid:
                    raise PlannerError("invalid-task", f"raw_task.{field}[{package_task_id!r}] invalid")
        catalog_estimated_minutes = 0
        for package_task_id in package_task_ids:
            package_record = tasks.get(package_task_id)
            if package_record is None:
                raise PlannerError("missing-task", f"Qoder work-package task missing: {package_task_id}")
            if owners[package_task_id] != raw["primary_owner"]:
                raise PlannerError("task-reconciliation-fail", "Qoder work-package tasks do not share current catalog owner")
            if (
                package_record.get("task_version") != raw["task_versions"][package_task_id]
                or package_record.get("change_version") != raw["change_versions"][package_task_id]
            ):
                raise PlannerError("non-current-task-version", f"Qoder work-package task not current: {package_task_id}")
            task_minutes = package_record.get("estimated_task_minutes")
            if isinstance(task_minutes, bool) or not isinstance(task_minutes, int) or task_minutes <= 0:
                raise PlannerError("invalid-task", f"Qoder work-package task estimate invalid: {package_task_id}")
            catalog_estimated_minutes += task_minutes
        if catalog_estimated_minutes != estimated_minutes:
            raise PlannerError("task-reconciliation-fail", "Qoder estimated_minutes differs from current catalog sum")
    elif identity["client"] == "codex" and raw.get("schema_version") == CODEX_MAIN_TASK_PROJECTION_SCHEMA_VERSION:
        try:
            validate_codex_main_task_projection(raw)
        except EvidencePacketError as exc:
            raise PlannerError("invalid-task", f"invalid Codex Main task projection: {exc.code}: {exc.message}") from exc
        raw = copy.deepcopy(raw)
    elif identity["client"] == "codex":
        try:
            validate_codex_work_package_task_projection(raw)
        except EvidencePacketError as exc:
            raise PlannerError(
                "invalid-task",
                f"invalid Codex work-package task projection: {exc.code}: {exc.message}",
            ) from exc
        caller = raw["caller_contract"]
        if caller["primary_owner"] != owners[record["id"]]:
            raise PlannerError("task-reconciliation-fail", "Codex work-package owner differs from current catalog")
        package_allowed, package_forbidden = set(), set()
        for package_task_id in raw["task_ids"]:
            package_record = tasks.get(package_task_id)
            if package_record is None:
                raise PlannerError("missing-task", f"Codex work-package task missing: {package_task_id}")
            if owners[package_task_id] != caller["primary_owner"]:
                raise PlannerError("task-reconciliation-fail", "Codex work-package tasks do not share current catalog owner")
            if (
                package_record.get("task_version") != caller["task_versions"][package_task_id]
                or package_record.get("change_version") != caller["change_versions"][package_task_id]
            ):
                raise PlannerError("non-current-task-version", f"Codex work-package task not current: {package_task_id}")
            if caller["expected_outputs_by_task"][package_task_id] != package_record.get("deliverable"):
                raise PlannerError("task-reconciliation-fail", f"Codex expected output differs from current catalog: {package_task_id}")
            acceptance = caller["acceptance_by_task"][package_task_id]
            if (
                acceptance["acceptance_criteria"] != package_record.get("acceptance_criteria")
                or acceptance["acceptance_evidence"] != package_record.get("acceptance_evidence")
                or caller["validation_commands"][package_task_id] != package_record.get("validation_command")
            ):
                raise PlannerError("task-reconciliation-fail", f"Codex acceptance contract differs from current catalog: {package_task_id}")
            package_allowed.update(_strings(package_record.get("allowed_files"), f"catalog[{package_task_id}].allowed_files"))
            package_forbidden.update(_strings(package_record.get("forbidden_files"), f"catalog[{package_task_id}].forbidden_files"))
        caller_allowed = _csv(caller["allowed_files"], "raw.caller_contract.allowed_files")
        caller_forbidden = _csv(caller["forbidden_files"], "raw.caller_contract.forbidden_files")
        if _minimal_path_union(caller_allowed, "raw.caller_contract.allowed_files") != _minimal_path_union(
            sorted(package_allowed), "catalog.package.allowed_files"
        ):
            raise PlannerError("task-reconciliation-fail", "Codex package allowed_files differ from current catalog union")
        if _minimal_path_union(caller_forbidden, "raw.caller_contract.forbidden_files") != _minimal_path_union(
            sorted(package_forbidden), "catalog.package.forbidden_files"
        ):
            raise PlannerError("task-reconciliation-fail", "Codex package forbidden_files differ from current catalog union")
        raw = copy.deepcopy(raw)
        raw.update({
            "goal": caller["goal"],
            "required_context": caller["required_context"],
            "failure_policy": caller["failure_policy"],
        })
    else:
        raise PlannerError("invalid-task", "raw_task client is not supported")
    expected = {"task_id": packet["task"]["task_id"], "task_version": packet["task"]["task_version"],
        "change_version": packet["task"]["change_version"], "task_source": packet["task"]["task_source"]["locator"],
        **identity, "validation_command": record.get("validation_command"), "acceptance_criteria": record.get("acceptance_criteria"),
        "acceptance_evidence": record.get("acceptance_evidence")}
    for field, value in expected.items():
        if raw.get(field) != value: raise PlannerError("task-reconciliation-fail", f"raw task {field} mismatch")
    for field in ("goal", "required_context", "expected_output", "failure_policy"): _string(raw[field], f"raw_task.{field}")
    _string(raw["validation_command"], "raw_task.validation_command")
    _strings(raw["acceptance_criteria"], "raw_task.acceptance_criteria")
    _strings(raw["acceptance_evidence"], "raw_task.acceptance_evidence")
    allowed = sorted(_strings(record.get("allowed_files"), "catalog.allowed_files"))
    forbidden = sorted(_strings(record.get("forbidden_files"), "catalog.forbidden_files"))
    if _csv(raw["allowed_files"], "raw.allowed_files") != allowed or _csv(raw["forbidden_files"], "raw.forbidden_files") != forbidden:
        raise PlannerError("task-reconciliation-fail", "raw scope differs from catalog")
    scope = packet["scope"]
    if scope["raw_caller_strings"] != {"allowed_files": raw["allowed_files"], "forbidden_files": raw["forbidden_files"]}:
        raise PlannerError("task-reconciliation-fail", "packet raw scope differs from task")
    if scope["normalized"]["allowed_files"] != allowed or scope["normalized"]["forbidden_files"] != forbidden:
        raise PlannerError("task-reconciliation-fail", "packet normalized scope differs from catalog")
    claims = record.get("file_claims")
    if not isinstance(claims, list) or not claims:
        raise PlannerError("invalid-task", "catalog file_claims must be non-empty")
    expected_claims = []
    for index, claim in enumerate(claims):
        claim = _keys(claim, {"path", "mode", "owner"}, f"catalog.file_claims[{index}]", "invalid-task")
        _dispatch(claim["path"])
        _string(claim["mode"], f"catalog.file_claims[{index}].mode", safe=True)
        if claim["owner"] != owners[record["id"]]:
            raise PlannerError("invalid-task", "catalog file claim owner mismatch")
        expected_claims.append(claim["path"])
    if scope["normalized"]["canonical_file_claims"] != sorted(expected_claims):
        raise PlannerError("task-reconciliation-fail", "packet canonical claims differ from catalog file_claims")
    deps = record.get("depends_on", [])
    if not isinstance(deps, list): raise PlannerError("invalid-dependency", "depends_on must be list")
    seen, frozen = set(), []
    for i, dep in enumerate(deps):
        dep_type = dep.get("type") if isinstance(dep, dict) else None
        if not isinstance(dep_type, str):
            raise PlannerError("invalid-dependency", f"dependency[{i}] type invalid")
        keys = {"task_id", "type", "required_task_version", "required_change_version", "required_result"}
        if dep_type == "contract": keys |= {"contract_name", "required_contract_version"}
        elif dep_type not in {"hard", "soft"}: raise PlannerError("invalid-dependency", f"dependency[{i}] type invalid")
        dep = _keys(dep, keys, f"dependency[{i}]", "invalid-dependency")
        dep_id = dep["task_id"]
        if not isinstance(dep_id, str) or not _TASK_ID.fullmatch(dep_id) or dep_id not in tasks or dep_id in seen or dep_id == raw["task_id"]:
            raise PlannerError("invalid-dependency", f"dependency[{i}] id invalid")
        seen.add(dep_id); producer = tasks[dep_id]
        if (isinstance(dep["required_task_version"], bool)
                or not isinstance(dep["required_task_version"], int)
                or dep["required_task_version"] <= 0
                or not isinstance(dep["required_change_version"], str)
                or not _SEMVER.fullmatch(dep["required_change_version"])
                or dep["required_result"] != "PASS"):
            raise PlannerError("invalid-dependency", f"dependency {dep_id} fields invalid")
        producer_owner = owners[dep_id]
        if isinstance(producer.get("task_version"), bool) or not isinstance(producer.get("task_version"), int) or producer["task_version"] <= 0:
            raise PlannerError("invalid-dependency", f"dependency {dep_id} producer version invalid")
        if not isinstance(producer.get("change_version"), str) or not _SEMVER.fullmatch(producer["change_version"]):
            raise PlannerError("invalid-dependency", f"dependency {dep_id} producer change version invalid")
        if producer.get("task_version") != dep["required_task_version"] or producer.get("change_version") != dep["required_change_version"]:
            raise PlannerError("dependency-version-mismatch", f"dependency {dep_id} stale")
        resolved = {"task_id": dep_id, "task_version": producer.get("task_version"), "change_version": producer.get("change_version"), "owner": producer_owner}
        if dep_type == "contract":
            if (not isinstance(dep["contract_name"], str)
                    or not _SAFE_ID.fullmatch(dep["contract_name"])
                    or not isinstance(dep["required_contract_version"], str)
                    or not _SEMVER.fullmatch(dep["required_contract_version"])):
                raise PlannerError("invalid-dependency", f"contract dependency {dep_id} fields invalid")
            contracts = producer.get("produced_contracts", [])
            if not isinstance(contracts, list):
                raise PlannerError("invalid-dependency", f"dependency {dep_id} producer contracts invalid")
            for j, contract in enumerate(contracts):
                contract = _keys(contract, {"name", "version"}, f"producer {dep_id} contract[{j}]", "invalid-dependency")
                if (not isinstance(contract["name"], str) or not _SAFE_ID.fullmatch(contract["name"])
                        or not isinstance(contract["version"], str) or not _SEMVER.fullmatch(contract["version"])):
                    raise PlannerError("invalid-dependency", f"dependency {dep_id} producer contract invalid")
            matches = [c for c in contracts if c["name"] == dep["contract_name"] and c["version"] == dep["required_contract_version"]]
            if len(matches) != 1: raise PlannerError("stale-dependency", f"contract dependency {dep_id} stale")
            resolved["contract"] = copy.deepcopy(matches[0])
        frozen.append({**copy.deepcopy(dep), "resolved_producer": resolved})
    return raw, frozen


def _criterion(criterion: str, task_id: str, tasks: dict[str, dict], cases: set[str]) -> bool:
    task = tasks[task_id]
    if criterion in cases:
        case_ids = task.get("acceptance_case_ids", [])
        return (isinstance(case_ids, list)
                and all(isinstance(item, str) for item in case_ids)
                and len(case_ids) == len(set(case_ids))
                and criterion in case_ids)
    match = _TASK_CRITERION.fullmatch(criterion)
    if not match or match.group(1) != task_id: return False
    criteria = task.get("acceptance_criteria")
    return (isinstance(criteria, list)
            and all(isinstance(item, str) and item.strip() for item in criteria)
            and len(criteria) == len(set(criteria))
            and int(match.group(2)) < len(criteria))


def _registry(value: Any, tasks: dict[str, dict], owners: dict[str, str], expected_subjects: list[str], cases: set[str]) -> list[dict]:
    value = _keys(value, {"schema_version", "owner", "registry_version", "execution", "entries"}, "registry", "invalid-registry")
    if value["schema_version"] != REGISTRY_SCHEMA: raise PlannerError("invalid-registry", "registry schema mismatch")
    if value["owner"] != "LF-WS-QLT": raise PlannerError("invalid-owner", "registry owner mismatch")
    _positive(value["registry_version"], "registry.version")
    execution = _keys(
        value["execution"],
        {"owner", "executor", "source_scan", "task_validation", "independent_review", "catalog_decision"},
        "registry.execution", "invalid-registry",
    )
    if execution != {
        "owner": "python-control-plane",
        "executor": "python3",
        "source_scan": "forbidden",
        "task_validation": "executes-selected-checks",
        "independent_review": "consumes-immutable-receipts",
        "catalog_decision": "consumes-immutable-receipts",
    }:
        raise PlannerError("invalid-registry", "registry execution-layer contract mismatch")
    entries = value["entries"]
    if not isinstance(entries, list) or not entries: raise PlannerError("invalid-registry", "registry entries missing")
    checks, commands, declared, subjects, output = set(), set(), set(), set(), []
    expected_keys = {"check_id", "check_version", "owner", "subject_task_id", "subject_task_version",
        "subject_change_version", "modes", "triggers", "required",
        "declared_validation_command", "command_id", "fixed_argv", "cwd", "timeout_seconds",
        "consumed_inputs", "outcome_contract", "acceptance_criterion_ids", "effect_check_ids", "entry_hash"}
    for i, original in enumerate(entries):
        entry = _keys(original, expected_keys, f"entries[{i}]")
        check = _string(entry["check_id"], f"entries[{i}].check_id", safe=True)
        command = _string(entry["command_id"], f"entries[{i}].command_id", safe=True)
        text = _string(entry["declared_validation_command"], f"entries[{i}].declared")
        subject = entry["subject_task_id"]
        if not isinstance(subject, str) or not _TASK_ID.fullmatch(subject) or subject not in tasks:
            raise PlannerError("invalid-registry", f"entry {check} subject task invalid")
        if subject in subjects: raise PlannerError("duplicate-subject-task", f"duplicate registry subject: {subject}")
        if check in checks: raise PlannerError("duplicate-check-id", f"duplicate check: {check}")
        if command in commands: raise PlannerError("duplicate-command-id", f"duplicate command: {command}")
        if text in declared: raise PlannerError("duplicate-declared-command", "duplicate declared command")
        checks.add(check); commands.add(command); declared.add(text); subjects.add(subject)
        _positive(entry["check_version"], f"entries[{i}].version")
        task = tasks[subject]
        if entry["owner"] != value["owner"] or entry["owner"] != "LF-WS-QLT":
            raise PlannerError("invalid-owner", f"entry {check} owner mismatch")
        if (isinstance(entry["subject_task_version"], bool)
                or not isinstance(entry["subject_task_version"], int)
                or entry["subject_task_version"] != task["task_version"]
                or not isinstance(entry["subject_change_version"], str)
                or entry["subject_change_version"] != task["change_version"]):
            raise PlannerError("invalid-registry", f"entry {check} subject version mismatch")
        catalog_command = task.get("validation_command")
        if catalog_command is None:
            catalog_command = _LEGACY_REGISTRY_COMMANDS.get(
                (subject, task["task_version"], task["change_version"])
            )
        if catalog_command is None or text != catalog_command:
            raise PlannerError("command-mismatch", f"registry command differs from current catalog task {subject}")
        try:
            modes = _strings(entry["modes"], f"entry {check} modes")
        except PlannerError as exc:
            raise PlannerError("invalid-mode", str(exc)) from exc
        if any(mode not in VALID_MODES for mode in modes): raise PlannerError("invalid-mode", f"entry {check} mode invalid")
        triggers = entry["triggers"]
        if not isinstance(triggers, list) or not triggers: raise PlannerError("invalid-trigger", f"entry {check} triggers missing")
        trigger_set = set()
        for j, trigger in enumerate(triggers):
            try:
                trigger = _keys(trigger, {"path", "terminal"}, f"{check}.triggers[{j}]")
            except PlannerError as exc:
                raise PlannerError("invalid-trigger", str(exc)) from exc
            if trigger["terminal"] is not True: raise PlannerError("invalid-trigger", "trigger terminal must be true")
            _dispatch(trigger["path"])
            if trigger["path"] in trigger_set: raise PlannerError("invalid-trigger", "duplicate trigger")
            trigger_set.add(trigger["path"])
        if not isinstance(entry["required"], bool): raise PlannerError("invalid-entry", f"entry {check} required invalid")
        try:
            argv = _strings(entry["fixed_argv"], f"entry {check} argv")
        except PlannerError as exc:
            raise PlannerError("invalid-argv", str(exc)) from exc
        try: parsed_argv = shlex.split(text, posix=True)
        except ValueError as exc: raise PlannerError("invalid-argv", f"entry {check}: {exc}") from None
        shell_tokens = {"&&", "||", ";", "|", ">", ">>", "<", "<<"}
        if argv != parsed_argv or any("\x00" in arg for arg in argv) or any(arg in shell_tokens for arg in argv) or "`" in text or "$" in text:
            raise PlannerError("invalid-argv", f"entry {check} argv mismatch or shell syntax")
        cwd = entry["cwd"]
        if not isinstance(cwd, str) or (cwd != "." and (not cwd or cwd.endswith("/"))): raise PlannerError("invalid-cwd", f"entry {check} cwd invalid")
        if cwd != ".":
            try: _safe_locator(cwd)
            except PlannerError as exc: raise PlannerError("invalid-cwd", str(exc)) from exc
        try:
            _positive(entry["timeout_seconds"], f"entry {check} timeout")
        except PlannerError as exc:
            raise PlannerError("invalid-timeout", str(exc)) from exc
        try:
            consumed = _strings(entry["consumed_inputs"], f"entry {check} consumed")
        except PlannerError as exc:
            raise PlannerError("invalid-consumed-input", str(exc)) from exc
        for locator in consumed:
            try: _safe_locator(locator)
            except PlannerError as exc: raise PlannerError("invalid-consumed-input", str(exc)) from exc
        outcome = _keys(entry["outcome_contract"], {"schema", "required_fields"}, f"entry {check} outcome")
        if outcome["schema"] != OUTCOME_SCHEMA or set(_strings(outcome["required_fields"], f"entry {check} fields")) != {"exit_code", "stdout_locator", "stderr_locator", "typed_result"}:
            raise PlannerError("invalid-entry", f"entry {check} outcome mismatch")
        try:
            criteria = _strings(entry["acceptance_criterion_ids"], f"entry {check} mappings")
        except PlannerError as exc:
            raise PlannerError("invalid-mapping", str(exc)) from exc
        if any(not _criterion(item, subject, tasks, cases) for item in criteria):
            raise PlannerError("invalid-mapping", f"entry {check} mapping invalid")
        try:
            effects = _strings(entry["effect_check_ids"], f"entry {check} effects")
        except PlannerError as exc:
            raise PlannerError("invalid-mapping", str(exc)) from exc
        if any(not _SAFE_ID.fullmatch(effect) for effect in effects):
            raise PlannerError("invalid-mapping", f"entry {check} effect id invalid")
        _hash(entry["entry_hash"], f"entry {check} hash")
        computed = sha256_bytes(canonical_json_bytes({k:v for k,v in entry.items() if k != "entry_hash"}))
        if not hmac.compare_digest(computed, entry["entry_hash"]): raise PlannerError("entry-hash-mismatch", f"entry {check} hash mismatch")
        registry_path = "harness/gate-check-registry.yaml"
        scopes = task.get("allowed_files", [])
        claims = task.get("file_claims", [])
        owns_registry = (
            isinstance(scopes, list)
            and any(isinstance(scope, str) and _match(_dispatch(scope), registry_path) for scope in scopes)
        ) or (
            isinstance(claims, list)
            and any(
                isinstance(claim, dict)
                and isinstance(claim.get("path"), str)
                and _match(_dispatch(claim["path"]), registry_path)
                for claim in claims
            )
        )
        covers_registry = (
            registry_path in consumed
            and any(_match(_dispatch(trigger["path"]), registry_path) for trigger in triggers)
        )
        if owns_registry and not covers_registry:
            raise PlannerError("invalid-registry", f"registry-owning task {subject} lacks self-coverage")
        output.append(copy.deepcopy(entry))
    actual_subjects = [entry["subject_task_id"] for entry in output]
    if actual_subjects != expected_subjects:
        raise PlannerError("invalid-registry", "registry ordered subject inventory mismatch")
    return output


def _selection(entries: list[dict], matched: dict, mode: str, changed: list[str]) -> list[tuple[dict, list[dict]]]:
    if mode not in matched["modes"]: raise PlannerError("invalid-mode", "matched command does not support mode")
    reasons = {entry["check_id"]: [] for entry in entries}; selected = set()
    if mode == "full":
        for entry in entries:
            if "full" in entry["modes"]:
                selected.add(entry["check_id"]); reasons[entry["check_id"]].append({"kind": "full-mode"})
    else:
        selected.add(matched["check_id"]); reasons[matched["check_id"]].append({"kind": "declared-command", "command_id": matched["command_id"]})
        rank = {"exact":3, "child":2, "subtree":1}
        for entry in entries:
            if "incremental" not in entry["modes"]: continue
            parsed = [(t, _dispatch(t["path"])) for t in entry["triggers"]]
            for path in changed:
                matches = [(t,p) for t,p in parsed if _match(p,path)]
                if matches:
                    trigger, pattern = max(matches, key=lambda pair:(rank[pair[1][0]],len(pair[1][1])))
                    selected.add(entry["check_id"]); reasons[entry["check_id"]].append({"kind":"changed-file","changed_file":path,"trigger":trigger["path"],"specificity":pattern[0]})
    return [(entry,reasons[entry["check_id"]]) for entry in entries if entry["check_id"] in selected]


def _evidence_error(exc: EvidencePacketError) -> PlannerError:
    if exc.code in {"VERIFY_HASH_MISMATCH", "HASH_MISMATCH", "TASK_SOURCE_CHANGED", "SNAPSHOT_CONTENT_MISMATCH"}: code = "evidence-drift"
    elif exc.code in {"VERIFY_SCHEMA", "SCHEMA_INVALID"}: code = "evidence-packet-schema-mismatch"
    else: code = "evidence-packet-incomplete"
    return PlannerError(code, f"upstream evidence verification failed: {exc.code}: {exc.message}")


def compile_plan(repo_root: str | os.PathLike[str], *, mode: str, receipt_kind: str,
        evidence_packet_locator: str, evidence_packet_sha256: str,
        issuer_packet_locator: str, issuer_packet_sha256: str) -> dict:
    """Compile a canonical plan with zero writes, execution, scans, IDs, or clock reads."""
    if mode not in VALID_MODES: raise PlannerError("invalid-mode", f"unknown mode: {mode!r}")
    if receipt_kind not in RECEIPT_KINDS: raise PlannerError("invalid-receipt-kind", f"unknown receipt kind: {receipt_kind!r}")
    if not isinstance(repo_root, (str, os.PathLike)): raise PlannerError("unsafe-locator", "repo_root must be path-like")
    inputs = _Inputs(os.fspath(repo_root))
    evidence_bytes, evidence_hash = inputs.get(evidence_packet_locator, evidence_packet_sha256, mismatch="evidence-drift")
    parsed = _json(evidence_bytes, "evidence_packet", canonical=True)
    try: evidence = verify_packet_strict(inputs.root, evidence_packet_locator, evidence_packet_sha256)
    except EvidencePacketError as exc: raise _evidence_error(exc) from exc
    if evidence != parsed: raise PlannerError("evidence-drift", "verified packet differs from frozen bytes")
    if receipt_kind == "TASK_VALIDATION" and not evidence["scope"]["changed_files"]:
        raise PlannerError(
            "evidence-packet-incomplete",
            "TASK_VALIDATION requires a non-empty subject changed-file snapshot",
        )
    for value in evidence["subject"]["raw_artifacts"].values():
        for descriptor in value if isinstance(value,list) else [value]:
            inputs.get(descriptor["locator"], descriptor["sha256"], mismatch="evidence-drift")
    _, frozen_issuer = _freeze_issuer(inputs, issuer_packet_locator, issuer_packet_sha256, receipt_kind)
    source = evidence["task"]["task_source"]
    if source["locator"] != "planning/workstreams.yaml":
        raise PlannerError("unsafe-locator", "task source must use the canonical planning/workstreams.yaml locator")
    catalog_bytes, catalog_hash = inputs.get(source["locator"], source["sha256"], mismatch="task-source-drift")
    tasks, owners, expected_subjects = _catalog(_yaml(catalog_bytes, "planning/workstreams.yaml"))
    task_id = evidence["task"]["task_id"]
    if task_id not in tasks: raise PlannerError("missing-task", f"task missing: {task_id}")
    record = tasks[task_id]
    if isinstance(record.get("task_version"), bool) or not isinstance(record.get("task_version"), int) or record["task_version"] <= 0:
        raise PlannerError("invalid-task", "current catalog task_version is invalid")
    if not isinstance(record.get("change_version"), str) or not _SEMVER.fullmatch(record["change_version"]):
        raise PlannerError("invalid-task", "current catalog change_version is invalid")
    if record.get("task_version") != evidence["task"]["task_version"] or record.get("change_version") != evidence["task"]["change_version"]:
        raise PlannerError("non-current-task-version", f"task not current: {task_id}")
    raw, dependencies = _raw_task(inputs, evidence, record, tasks, owners)
    for locator in ("planning/task-template.yaml", "harness/agent-policy.manifest.yaml",
            "harness/agent-runtime.manifest.yaml", "harness/manifest.yaml", "docs/product/product-brief.md"):
        inputs.get(locator)
    acceptance_bytes = inputs.data["docs/product/product-brief.md"]
    try: case_list = _CASE.findall(acceptance_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc: raise PlannerError("invalid-mapping", f"acceptance registry not UTF-8: {exc}") from None
    if not case_list or len(case_list) != len(set(case_list)):
        raise PlannerError("invalid-mapping", "acceptance registry is empty or has duplicate cases")
    cases = set(case_list)
    registry_locator = "harness/gate-check-registry.yaml"
    registry_bytes, registry_hash = inputs.get(registry_locator)
    registry_value = _yaml(registry_bytes, "gate-check-registry.yaml")
    entries = _registry(registry_value, tasks, owners, expected_subjects, cases)
    matches = [entry for entry in entries if entry["declared_validation_command"] == raw["validation_command"]]
    if not matches: raise PlannerError("command-mismatch", "validation command is not registered")
    if len(matches) != 1: raise PlannerError("multiple-command-match", "validation command is ambiguous")
    # TASK_VALIDATION is the sole delivery-validation layer.  Review and
    # catalog-decision routes verify immutable evidence produced by that layer;
    # they must never schedule the subject's checker a second time.
    delivery_execution = receipt_kind == "TASK_VALIDATION"
    selected = (
        _selection(entries, matches[0], mode, evidence["scope"]["changed_files"])
        if delivery_execution
        else []
    )
    fields = evidence["subject"]["main_agent_attestation"]["result_fields"]
    explicit_effects = fields["effect_checks"]
    if any(status not in {"PASS", "BLOCKED", "FAIL"} for status in explicit_effects.values()):
        raise PlannerError("invalid-mapping", "explicit effect status is invalid")
    for entry, _ in selected:
        if any(effect_id not in explicit_effects for effect_id in entry["effect_check_ids"]):
            raise PlannerError("invalid-mapping", f"selected check {entry['check_id']} effect mapping is not explicitly attested")
    for entry,_ in selected:
        for locator in entry["consumed_inputs"]: inputs.get(locator)
    checks = []
    for entry,reasons in selected:
        check_task_id = entry["subject_task_id"]
        check_task = tasks[check_task_id]
        checks.append({"check_id":entry["check_id"], "check_version":entry["check_version"], "owner":entry["owner"],
            "subject_task":{"task_id":check_task_id,"task_version":check_task["task_version"],
                "change_version":check_task["change_version"],"owner":owners[check_task_id]},
            "modes":copy.deepcopy(entry["modes"]), "triggers":copy.deepcopy(entry["triggers"]), "required":entry["required"],
            "selection_reasons":reasons, "declared_validation_command":entry["declared_validation_command"],
            "command_id":entry["command_id"], "fixed_argv":copy.deepcopy(entry["fixed_argv"]), "cwd":entry["cwd"],
            "timeout_seconds":entry["timeout_seconds"], "consumed_inputs":[inputs.descriptor(x) for x in entry["consumed_inputs"]],
            "outcome_contract":copy.deepcopy(entry["outcome_contract"]), "acceptance_criterion_ids":copy.deepcopy(entry["acceptance_criterion_ids"]),
            "effect_check_ids":copy.deepcopy(entry["effect_check_ids"]),
            "effect_checks":[{"effect_id":effect_id,"explicit_status":explicit_effects[effect_id]}
                for effect_id in entry["effect_check_ids"]],
            "registry_entry_sha256":entry["entry_hash"]})
    acceptance_expectations = [
        {"criterion_id":f"{task_id}.acceptance_criteria[{index}]", "criterion":criterion,
         "catalog_evidence":copy.deepcopy(record["acceptance_evidence"]),
         "attested_evidence":copy.deepcopy(fields["acceptance_evidence"])}
        for index, criterion in enumerate(record["acceptance_criteria"])
    ]
    effect_expectations = [
        {"check_id":entry["check_id"], "effect_id":effect_id, "required":entry["required"],
         "attested_status":explicit_effects[effect_id]}
        for entry, _ in selected for effect_id in entry["effect_check_ids"]
    ]
    risk_expectations = [
        {"risk_id":risk, "required_fields":["impact", "mitigation", "fallback", "remaining_limitation"]}
        for risk in fields["risks"]
    ]
    plan = {"schema_version":PLAN_SCHEMA, "mode":mode, "receipt_kind":receipt_kind,
        "execution": {
            "layer": "delivery-validation" if delivery_execution else "evidence-consumption",
            "checker_execution": "required" if delivery_execution else "forbidden",
            "source": "selected-registry-checks" if delivery_execution else "prior-immutable-receipts",
        },
        "task":{"task_id":task_id, "task_version":evidence["task"]["task_version"], "change_version":evidence["task"]["change_version"],
            "task_source":{"locator":source["locator"],"sha256":catalog_hash,"raw_value":raw["task_source"],"catalog_value":record.get("task_source")},
            "goal":raw["goal"],"required_context":raw["required_context"],"expected_output":raw["expected_output"],"failure_policy":raw["failure_policy"],
            "owner":owners[task_id],"discovered_from":record.get("discovered_from"),"validation_command":raw["validation_command"],
            "allowed_files":copy.deepcopy(record.get("allowed_files")),"forbidden_files":copy.deepcopy(record.get("forbidden_files")),
            "file_claims":copy.deepcopy(record.get("file_claims")),"acceptance_criteria":copy.deepcopy(record.get("acceptance_criteria")),
            "acceptance_evidence":copy.deepcopy(record.get("acceptance_evidence")),"dependencies":dependencies},
        "subject":{"explicit_evidence_packet":{"locator":evidence_packet_locator,"sha256":evidence_hash,"content_fingerprint":evidence["content_fingerprint"]},
            "raw_artifacts":copy.deepcopy(evidence["subject"]["raw_artifacts"]),
            "main_agent_attestation":copy.deepcopy(evidence["subject"]["main_agent_attestation"]),"identity":copy.deepcopy(evidence["subject"]["identity"])},
        "issuer_packet":frozen_issuer,"scope":copy.deepcopy(evidence["scope"]),"consumed_inputs":inputs.descriptors(),"checks":checks,
        "expectations":{"acceptance":acceptance_expectations,"effect_checks":effect_expectations,
            "attested_effect_checks":copy.deepcopy(explicit_effects),"risks":risk_expectations},
        "registry":{"locator":registry_locator,"sha256":registry_hash,"schema_version":registry_value["schema_version"],
            "registry_version":registry_value["registry_version"],"owner":registry_value["owner"],
            "execution":copy.deepcopy(registry_value["execution"])},"content_fingerprint":""}
    if (evidence["subject"]["identity"]["client"] == "codex"
            and raw.get("schema_version") == CODEX_TASK_PROJECTION_SCHEMA_VERSION):
        plan["task"]["work_package_projection"] = {
            "schema_version": CODEX_TASK_PROJECTION_SCHEMA_VERSION,
            "work_package_id": raw["work_package_id"],
            "task_ids": copy.deepcopy(raw["task_ids"]),
            "target_task_id": raw["target_task_id"],
            "caller_contract": copy.deepcopy(raw["caller_contract"]),
        }
    elif evidence["subject"]["identity"]["client"] == "qoder":
        plan["task"]["work_package_projection"] = {
            "schema_version": QODER_TASK_PROJECTION_SCHEMA_VERSION,
            "work_package_id": raw["work_package_id"],
            "task_ids": copy.deepcopy(raw["task_ids"]),
            "task_versions": copy.deepcopy(raw["task_versions"]),
            "change_versions": copy.deepcopy(raw["change_versions"]),
            "estimated_minutes": raw["estimated_minutes"],
            "primary_owner": raw["primary_owner"],
            "contract_boundary": raw["contract_boundary"],
            "agent_profile": raw["agent_profile"],
            "harness_manifest": raw["harness_manifest"],
            "harness_manifest_sha256": raw["harness_manifest_sha256"],
            "harness_context": copy.deepcopy(raw["harness_context"]),
        }
    payload = {k:v for k,v in plan.items() if k != "content_fingerprint"}
    fingerprint = sha256_bytes(canonical_json_bytes(payload)); plan["content_fingerprint"] = fingerprint
    inputs.revalidate()
    if sha256_bytes(canonical_json_bytes({k:v for k,v in plan.items() if k != "content_fingerprint"})) != fingerprint:
        raise PlannerError("input-drift", "plan changed during final revalidation")
    return {"plan":plan,"canonical_bytes":canonical_json_bytes(plan),"content_fingerprint":fingerprint}
