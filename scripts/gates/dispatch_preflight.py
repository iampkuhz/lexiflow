"""Pure, fail-closed dispatch preflight for ``LF-TSK-QLT-0005``.

The checker deliberately consumes three caller-frozen JSON values.  It never
discovers processes, task directories, completion records, leases, or Gate
receipts.  A PASS therefore applies only to the supplied dispatch window.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable


RESULT_SCHEMA = "lexiflow.dispatch-preflight-result.v1"
CANDIDATE_SCHEMA = "lexiflow.dispatch-candidate-view.v1"
CATALOG_SCHEMA = "lexiflow.dispatch-catalog-snapshot.v1"
ACTIVE_SCHEMA = "lexiflow.dispatch-active-snapshot.v1"
TASK_SCHEMA = "lexiflow.task.v1"
MATCHER_VERSION = "dispatch-path-v1"

_HEX = re.compile(r"^[0-9a-f]{64}$")
_TASK_ID = re.compile(r"^LF-TSK-[A-Z]+-\d{4}$")
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._:-]{0,255}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class InputError(ValueError):
    def __init__(self, code: str, detail: str, status: str = "FAIL") -> None:
        if status not in {"FAIL", "BLOCKED"}:
            raise ValueError(f"invalid decision status: {status}")
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class PathPattern:
    raw: str
    prefix: tuple[str, ...]
    kind: str

    @property
    def minimum_length(self) -> int:
        return len(self.prefix) if self.kind == "exact" else len(self.prefix) + 1

    @property
    def maximum_length(self) -> int | None:
        return None if self.kind == "descendant" else self.minimum_length


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise InputError("input-incomplete", f"input is not canonical JSON data: {exc}") from None


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _mapping(value: Any, path: str, code: str = "input-incomplete") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InputError(code, f"{path} must be an object")
    return value


def _list(value: Any, path: str, code: str = "input-incomplete") -> list[Any]:
    if not isinstance(value, list):
        raise InputError(code, f"{path} must be a list")
    return value


def _string(value: Any, path: str, code: str = "input-incomplete") -> str:
    if not isinstance(value, str) or not value or _CONTROL.search(value):
        raise InputError(code, f"{path} must be a non-empty control-free string")
    return value


def _sha(value: Any, path: str, code: str = "input-incomplete") -> str:
    value = _string(value, path, code)
    if not _HEX.fullmatch(value):
        raise InputError(code, f"{path} must be a lowercase SHA-256")
    return value


def _task_version(value: Any, path: str, code: str = "task-version-mismatch") -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InputError(code, f"{path} must be a positive integer")
    return value


def _semver(value: Any, path: str, code: str = "task-version-mismatch") -> str:
    value = _string(value, path, code)
    if not _SEMVER.fullmatch(value):
        raise InputError(code, f"{path} must be SemVer")
    core_and_prerelease = value.split("+", 1)[0]
    if "-" in core_and_prerelease:
        prerelease = core_and_prerelease.split("-", 1)[1]
        if any(
            identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
            for identifier in prerelease.split(".")
        ):
            raise InputError(code, f"{path} must be SemVer")
    return value


def _parse_timestamp(value: Any, path: str) -> str:
    value = _string(value, path, "active-snapshot-invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise InputError("active-snapshot-invalid", f"{path} must be RFC3339") from None
    if parsed.tzinfo is None:
        raise InputError("active-snapshot-invalid", f"{path} must include a timezone")
    return value


def parse_path_expression(value: Any, path: str = "path") -> PathPattern:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InputError("path-expression-invalid", f"{path} must be a non-empty trimmed string")
    if (
        _CONTROL.search(value)
        or unicodedata.normalize("NFC", value) != value
        or "\\" in value
        or "," in value
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
        or value.startswith("//")
        or value.endswith("/")
        or "//" in value
    ):
        raise InputError("path-expression-invalid", f"unsafe or unnormalized {path}: {value!r}")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise InputError("path-expression-invalid", f"unsafe segment in {path}: {value!r}")
    wildcard_positions = [i for i, segment in enumerate(segments) if "*" in segment]
    if wildcard_positions:
        if wildcard_positions != [len(segments) - 1] or segments[-1] not in {"*", "**"}:
            raise InputError("path-expression-unsupported", f"unsupported wildcard in {path}: {value!r}")
        if len(segments) == 1:
            raise InputError("path-expression-invalid", f"root-wide wildcard is forbidden in {path}")
        kind = "one-child" if segments[-1] == "*" else "descendant"
        prefix = tuple(segments[:-1])
    else:
        if any(any(char in segment for char in "?[]{}!") for segment in segments):
            raise InputError("path-expression-unsupported", f"unsupported pattern in {path}: {value!r}")
        kind = "exact"
        prefix = tuple(segments)
    return PathPattern(value, prefix, kind)


def _prefixes_compatible(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return all(a == b for a, b in zip(left, right))


def paths_intersect(left: PathPattern, right: PathPattern) -> bool:
    if not _prefixes_compatible(left.prefix, right.prefix):
        return False
    minimum = max(left.minimum_length, right.minimum_length, len(left.prefix), len(right.prefix))
    maxima = [bound for bound in (left.maximum_length, right.maximum_length) if bound is not None]
    return not maxima or minimum <= min(maxima)


def path_contains(container: PathPattern, member: PathPattern) -> bool:
    if container.kind == "exact":
        return member.kind == "exact" and container.prefix == member.prefix
    if container.kind == "one-child":
        if member.minimum_length != container.minimum_length or member.maximum_length != container.maximum_length:
            return False
        return len(member.prefix) >= len(container.prefix) and member.prefix[: len(container.prefix)] == container.prefix
    if len(member.prefix) < len(container.prefix):
        return False
    if member.prefix[: len(container.prefix)] != container.prefix:
        return False
    return member.minimum_length >= container.minimum_length


def _specificity(pattern: PathPattern) -> tuple[int, int]:
    return (len(pattern.prefix), {"descendant": 0, "one-child": 1, "exact": 2}[pattern.kind])


def _casefold_pattern(pattern: PathPattern) -> PathPattern:
    return PathPattern(pattern.raw.casefold(), tuple(segment.casefold() for segment in pattern.prefix), pattern.kind)


def _parse_path_list(value: Any, path: str) -> list[PathPattern]:
    raw = _list(value, path)
    patterns = [parse_path_expression(item, f"{path}[{index}]") for index, item in enumerate(raw)]
    _reject_redundant(patterns, path)
    return sorted(patterns, key=lambda item: item.raw)


def _parse_handoff_paths(value: Any, path: str) -> list[PathPattern]:
    value = _string(value, path, "path-expression-invalid")
    pieces = value.split(",")
    if any(not piece.strip() for piece in pieces):
        raise InputError("path-expression-invalid", f"{path} contains an empty comma-delimited item")
    patterns = [parse_path_expression(piece.strip(), f"{path}[{index}]") for index, piece in enumerate(pieces)]
    _reject_redundant(patterns, path)
    return sorted(patterns, key=lambda item: item.raw)


def _parse_forbidden_paths(value: Any, path: str) -> tuple[list[PathPattern], list[InputError]]:
    value = _string(value, path, "input-incomplete")
    pieces = value.split(",")
    patterns: list[PathPattern] = []
    errors: list[InputError] = []
    for index, piece in enumerate(pieces):
        if not piece.strip():
            errors.append(InputError("path-expression-invalid", f"{path}[{index}] is empty"))
            continue
        try:
            patterns.append(parse_path_expression(piece.strip(), f"{path}[{index}]"))
        except InputError as exc:
            # Preserve valid exclusions so a malformed sibling cannot hide a
            # proven write/forbidden conflict.  The caller still receives the
            # input error and the result remains fail-closed.
            errors.append(exc)
    errors.extend(_redundancy_errors(patterns, path))
    return sorted(patterns, key=lambda item: item.raw), errors


def _reject_redundant(patterns: list[PathPattern], path: str) -> None:
    errors = _redundancy_errors(patterns, path)
    if errors:
        raise errors[0]


def _redundancy_errors(patterns: list[PathPattern], path: str) -> list[InputError]:
    errors: list[InputError] = []
    for index, left in enumerate(patterns):
        for right in patterns[index + 1 :]:
            if left.raw == right.raw or path_contains(left, right) or path_contains(right, left):
                errors.append(InputError("path-expression-invalid", f"{path} contains duplicate or redundant expressions: {left.raw!r}, {right.raw!r}"))
            if left.raw.casefold() == right.raw.casefold():
                errors.append(InputError("path-case-ambiguous", f"{path} differs only by case: {left.raw!r}, {right.raw!r}"))
    return errors


def _raw_set(patterns: Iterable[PathPattern]) -> set[str]:
    return {pattern.raw for pattern in patterns}


def _diag(status: str, code: str, detail: str, **context: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"status": status, "code": code, "detail": detail}
    if context:
        value["context"] = context
    return value


def _result(
    status: str,
    diagnostics: list[dict[str, Any]],
    fingerprints: dict[str, str | None],
    candidate: dict[str, Any] | None = None,
    active_declaration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ordered = sorted(
        diagnostics,
        key=lambda item: (
            {"FAIL": 0, "BLOCKED": 1, "PASS": 2}[item["status"]], item["code"],
            json.dumps(item.get("context", {}), sort_keys=True, ensure_ascii=False),
            item["detail"],
        ),
    )
    body: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA,
        "matcher_version": MATCHER_VERSION,
        "status": status,
        "input_fingerprints": fingerprints,
        "diagnostics": ordered,
    }
    if candidate is not None:
        body["candidate"] = candidate
    if active_declaration is not None:
        body["active_snapshot"] = active_declaration
    body["result_fingerprint"] = _fingerprint(body)
    return body


def _safe_fingerprints(candidate: Any, catalog: Any, active: Any) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for name, value in (("candidate_view_sha256", candidate), ("catalog_snapshot_sha256", catalog), ("active_instances_sha256", active)):
        try:
            values[name] = _fingerprint(value)
        except InputError:
            values[name] = None
    return values


def _task_identity(task: dict[str, Any], path: str, code: str = "task-version-mismatch") -> tuple[str, int, str]:
    identity = _mapping(task.get("identity"), f"{path}.identity", code)
    task_id = _string(identity.get("task_id"), f"{path}.identity.task_id", code)
    if not _TASK_ID.fullmatch(task_id):
        raise InputError(code, f"{path}.identity.task_id is invalid")
    _string(identity.get("task_source"), f"{path}.identity.task_source", "input-incomplete")
    return (
        task_id,
        _task_version(identity.get("task_version"), f"{path}.identity.task_version", code),
        _semver(identity.get("change_version"), f"{path}.identity.change_version", code),
    )


def _entry_identity(entry: dict[str, Any], path: str, code: str) -> tuple[str, int, str]:
    task_id = _string(entry.get("task_id"), f"{path}.task_id", code)
    if not _TASK_ID.fullmatch(task_id):
        raise InputError(code, f"{path}.task_id is invalid")
    return (
        task_id,
        _task_version(entry.get("task_version"), f"{path}.task_version", code),
        _semver(entry.get("change_version"), f"{path}.change_version", code),
    )


def _catalog_index(catalog: dict[str, Any]) -> dict[tuple[str, int, str], dict[str, Any]]:
    entries = _list(catalog.get("tasks"), "catalog_snapshot.tasks")
    result: dict[tuple[str, int, str], dict[str, Any]] = {}
    for index, raw in enumerate(entries):
        entry = _mapping(raw, f"catalog_snapshot.tasks[{index}]")
        identity = _entry_identity(entry, f"catalog_snapshot.tasks[{index}]", "input-incomplete")
        if identity in result:
            raise InputError("input-incomplete", f"duplicate catalog task identity: {identity}")
        _sha(entry.get("descriptor_sha256"), f"catalog_snapshot.tasks[{index}].descriptor_sha256")
        _string(entry.get("task_source"), f"catalog_snapshot.tasks[{index}].task_source")
        result[identity] = entry
    return result


def _claims(
    entry: dict[str, Any],
    path: str,
    code: str = "claim-mismatch",
    decision_status: str = "BLOCKED",
) -> tuple[list[PathPattern], str, str, list[dict[str, str]]]:
    # Missing or malformed descriptor fields are input failures.  Only a
    # well-formed disagreement between declarations is dispatch-blocking.
    owner = _string(entry.get("primary_owner"), f"{path}.primary_owner", "input-incomplete")
    contract_owner = _string(entry.get("contract_owner"), f"{path}.contract_owner", "input-incomplete")
    allowed = _parse_path_list(entry.get("allowed_files"), f"{path}.allowed_files")
    raw_claims = entry.get("file_claims")
    if not isinstance(raw_claims, list) or not raw_claims:
        raise InputError("input-incomplete", f"{path}.file_claims must be a non-empty list")
    claims: list[PathPattern] = []
    for index, raw in enumerate(raw_claims):
        claim = _mapping(raw, f"{path}.file_claims[{index}]", code)
        if claim.get("mode") != "write":
            raise InputError("input-incomplete", f"{path}.file_claims[{index}].mode must be write")
        if claim.get("owner") != owner:
            raise InputError("owner-mismatch", f"{path}.file_claims[{index}] owner does not match primary_owner", decision_status)
        claims.append(parse_path_expression(claim.get("path"), f"{path}.file_claims[{index}].path"))
    _reject_redundant(claims, f"{path}.file_claims")
    if _raw_set(allowed) != _raw_set(claims):
        raise InputError(code, f"{path} allowed_files and write claims differ", decision_status)
    contracts: list[dict[str, str]] = []
    for index, raw in enumerate(_list(entry.get("produced_contracts"), f"{path}.produced_contracts")):
        contract = _mapping(raw, f"{path}.produced_contracts[{index}]")
        name = _string(contract.get("name"), f"{path}.produced_contracts[{index}].name")
        version = _semver(contract.get("version"), f"{path}.produced_contracts[{index}].version", "input-incomplete")
        contracts.append({"name": name, "version": version})
    if len({item["name"] for item in contracts}) != len(contracts):
        raise InputError("input-incomplete", f"{path}.produced_contracts repeats a name")
    return sorted(claims, key=lambda item: item.raw), owner, contract_owner, sorted(contracts, key=lambda item: (item["name"], item["version"]))


def _candidate_entry(task: dict[str, Any]) -> dict[str, Any]:
    identity = _mapping(task.get("identity"), "candidate_view.task.identity")
    ownership = _mapping(task.get("ownership"), "candidate_view.task.ownership")
    scope = _mapping(task.get("scope"), "candidate_view.task.scope")
    logical_scopes = _list(scope.get("logical_scopes"), "candidate_view.task.scope.logical_scopes")
    produced_contracts = _list(task.get("produced_contracts"), "candidate_view.task.produced_contracts")
    return {
        "task_id": identity.get("task_id"),
        "task_version": identity.get("task_version"),
        "change_version": identity.get("change_version"),
        "task_source": identity.get("task_source"),
        "descriptor_sha256": _fingerprint(task),
        "primary_owner": ownership.get("primary_owner"),
        "contract_owner": ownership.get("contract_owner"),
        "logical_scopes": logical_scopes,
        "allowed_files": scope.get("allowed_files"),
        "file_claims": scope.get("file_claims"),
        "produced_contracts": produced_contracts,
    }


def _parse_owner_patterns(catalog: dict[str, Any]) -> list[tuple[PathPattern, str, str]]:
    ownership = _mapping(catalog.get("path_ownership"), "catalog_snapshot.path_ownership")
    scopes = _list(ownership.get("scopes"), "catalog_snapshot.path_ownership.scopes")
    result: list[tuple[PathPattern, str, str]] = []
    for index, raw in enumerate(scopes):
        scope = _mapping(raw, f"catalog_snapshot.path_ownership.scopes[{index}]")
        scope_id = _string(scope.get("scope_id"), f"catalog_snapshot.path_ownership.scopes[{index}].scope_id")
        owner = _string(scope.get("owner"), f"catalog_snapshot.path_ownership.scopes[{index}].owner")
        for path_index, pattern in enumerate(_parse_path_list(scope.get("proposed_paths"), f"catalog_snapshot.path_ownership.scopes[{index}].proposed_paths")):
            result.append((pattern, owner, scope_id))
    for index, (left, left_owner, _) in enumerate(result):
        for right, right_owner, _ in result[index + 1 :]:
            if (
                paths_intersect(_casefold_pattern(left), _casefold_pattern(right))
                and not paths_intersect(left, right)
            ):
                raise InputError("path-case-ambiguous", f"owner patterns overlap only after casefold: {left.raw!r}, {right.raw!r}")
            if left.raw == right.raw and left_owner != right_owner:
                raise InputError("catalog-owner-ambiguous", f"owner patterns disagree for identical path: {left.raw!r}")
    return result


def _validate_path_metadata(catalog: dict[str, Any], all_patterns: list[PathPattern]) -> list[str]:
    metadata = _mapping(catalog.get("path_metadata"), "catalog_snapshot.path_metadata")
    if metadata.get("scan_complete") is not True:
        raise InputError("input-incomplete", "catalog_snapshot.path_metadata.scan_complete must be true")
    catalog_root = _string(catalog.get("repo_root_identity"), "catalog_snapshot.repo_root_identity")
    metadata_root = _string(metadata.get("repo_root_identity"), "catalog_snapshot.path_metadata.repo_root_identity")
    if metadata_root != catalog_root:
        raise InputError("input-incomplete", "catalog and path metadata repo root identities differ")
    _sha(metadata.get("source_sha256"), "catalog_snapshot.path_metadata.source_sha256")
    symlinks = _parse_path_list(metadata.get("symlink_paths"), "catalog_snapshot.path_metadata.symlink_paths")
    if any(pattern.kind != "exact" for pattern in symlinks):
        raise InputError("input-incomplete", "symlink_paths must contain exact paths")
    collisions = _list(metadata.get("casefold_collisions"), "catalog_snapshot.path_metadata.casefold_collisions")
    if collisions:
        raise InputError("path-case-ambiguous", "catalog path metadata contains casefold collisions")
    for pattern in all_patterns:
        for symlink in symlinks:
            if (
                paths_intersect(_casefold_pattern(pattern), _casefold_pattern(symlink))
                and not paths_intersect(pattern, symlink)
            ):
                raise InputError("path-case-ambiguous", f"{pattern.raw!r} aliases symlink {symlink.raw!r} after casefold")
            if paths_intersect(pattern, symlink) or (
                len(pattern.prefix) >= len(symlink.prefix)
                and pattern.prefix[: len(symlink.prefix)] == symlink.prefix
            ):
                raise InputError("path-symlink-unsafe", f"{pattern.raw!r} can address symlink {symlink.raw!r} or its descendant")
    return [pattern.raw for pattern in symlinks]


def _resolve_owners(
    claims: list[PathPattern],
    owner_patterns: list[tuple[PathPattern, str, str]],
    expected_owner: str,
    logical_scopes: set[str],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for claim in claims:
        for pattern, _, _ in owner_patterns:
            if (
                paths_intersect(_casefold_pattern(claim), _casefold_pattern(pattern))
                and not paths_intersect(claim, pattern)
            ):
                diagnostics.append(_diag("FAIL", "path-case-ambiguous", "claim overlaps an owner path only after casefold", claim=claim.raw, owner_pattern=pattern.raw))
        containing = [item for item in owner_patterns if path_contains(item[0], claim)]
        if not containing:
            diagnostics.append(_diag("BLOCKED", "path-unowned", "write claim has no containing owner", claim=claim.raw))
            continue
        best_score = max(_specificity(item[0]) for item in containing)
        best = [item for item in containing if _specificity(item[0]) == best_score]
        owners = {item[1] for item in best}
        if len(owners) != 1:
            diagnostics.append(_diag("FAIL", "catalog-owner-ambiguous", "equally specific owner rules disagree", claim=claim.raw, owners=sorted(owners)))
            continue
        resolved_owner = next(iter(owners))
        resolved_scopes = {item[2] for item in best}
        if resolved_owner != expected_owner or not resolved_scopes.intersection(logical_scopes):
            diagnostics.append(_diag("BLOCKED", "owner-mismatch", "claim owner does not match task owner/logical scope", claim=claim.raw, expected_owner=expected_owner, resolved_owner=resolved_owner, resolved_scopes=sorted(resolved_scopes)))
        for pattern, owner, scope_id in owner_patterns:
            if owner != resolved_owner and paths_intersect(claim, pattern) and _specificity(pattern) > best_score:
                diagnostics.append(_diag("BLOCKED", "owner-crossing", "wide claim crosses a more specific foreign owner", claim=claim.raw, foreign_pattern=pattern.raw, foreign_owner=owner, foreign_scope=scope_id))
    return diagnostics


def _contract_registry(catalog: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[PathPattern]]:
    registry: dict[str, dict[str, Any]] = {}
    contract_paths: list[PathPattern] = [parse_path_expression("contracts/**", "implicit contract path")]
    for index, raw in enumerate(_list(catalog.get("public_contract_producers"), "catalog_snapshot.public_contract_producers")):
        entry = _mapping(raw, f"catalog_snapshot.public_contract_producers[{index}]")
        name = _string(entry.get("name"), f"catalog_snapshot.public_contract_producers[{index}].name")
        if name in registry:
            raise InputError("input-incomplete", f"duplicate public contract producer: {name}")
        _semver(entry.get("version"), f"catalog_snapshot.public_contract_producers[{index}].version", "input-incomplete")
        producer = _entry_identity(entry, f"catalog_snapshot.public_contract_producers[{index}]", "input-incomplete")
        paths = _parse_path_list(entry.get("paths", []), f"catalog_snapshot.public_contract_producers[{index}].paths")
        contract_paths.extend(paths)
        registry[name] = {"entry": entry, "producer": producer, "paths": paths}
    return registry, contract_paths


def _validate_contract_declaration(
    identity: tuple[str, int, str],
    claims: list[PathPattern],
    contracts: list[dict[str, str]],
    registry: dict[str, dict[str, Any]],
    contract_paths: list[PathPattern],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    declared = {contract["name"]: contract["version"] for contract in contracts}
    touches_contract_path = any(paths_intersect(claim, path) for claim in claims for path in contract_paths)
    if touches_contract_path and not declared:
        diagnostics.append(_diag("FAIL", "contract-writer-unknown", "task writes a public-contract path without declaring produced_contracts", task_id=identity[0]))
    for name, registration in registry.items():
        if any(paths_intersect(claim, path) for claim in claims for path in registration["paths"]):
            if name not in declared:
                diagnostics.append(_diag("FAIL", "contract-writer-unknown", "task writes a registered public-contract path without its contract declaration", task_id=identity[0], contract=name))
    for name, version in declared.items():
        registered = registry.get(name)
        if registered is None or registered["entry"].get("version") != version:
            diagnostics.append(_diag("FAIL", "contract-writer-unknown", "produced contract does not match the canonical producer registry", task_id=identity[0], contract=name, version=version))
    return diagnostics


def check_dispatch(candidate_view: Any, catalog_snapshot: Any, active_instances: Any) -> dict[str, Any]:
    """Return a deterministic PASS/BLOCKED/FAIL decision for frozen inputs."""
    fingerprints = _safe_fingerprints(candidate_view, catalog_snapshot, active_instances)
    try:
        candidate = _mapping(candidate_view, "candidate_view")
        catalog = _mapping(catalog_snapshot, "catalog_snapshot")
        active = _mapping(active_instances, "active_instances")
        if candidate.get("schema_version") != CANDIDATE_SCHEMA:
            raise InputError("input-incomplete", f"candidate_view.schema_version must be {CANDIDATE_SCHEMA}")
        if catalog.get("schema_version") != CATALOG_SCHEMA:
            raise InputError("input-incomplete", f"catalog_snapshot.schema_version must be {CATALOG_SCHEMA}")
        if active.get("schema_version") != ACTIVE_SCHEMA:
            raise InputError("active-snapshot-declaration-missing", f"active_instances.schema_version must be {ACTIVE_SCHEMA}")
        if catalog.get("matcher_version") != MATCHER_VERSION:
            raise InputError("input-incomplete", f"catalog_snapshot.matcher_version must be {MATCHER_VERSION}")

        source = _mapping(catalog.get("catalog_source"), "catalog_snapshot.catalog_source")
        _string(source.get("locator"), "catalog_snapshot.catalog_source.locator")
        _sha(source.get("sha256"), "catalog_snapshot.catalog_source.sha256")
        catalog_index = _catalog_index(catalog)
        owner_patterns = _parse_owner_patterns(catalog)
        registry, contract_paths = _contract_registry(catalog)

        task = _mapping(candidate.get("task"), "candidate_view.task")
        if task.get("schema_version") != TASK_SCHEMA:
            raise InputError("input-incomplete", f"candidate_view.task.schema_version must be {TASK_SCHEMA}")
        candidate_identity = _task_identity(task, "candidate_view.task")
        canonical = catalog_index.get(candidate_identity)
        if canonical is None:
            raise InputError("task-version-mismatch", f"candidate task is absent from catalog snapshot: {candidate_identity}")
        projected = _candidate_entry(task)
        if canonical.get("descriptor_sha256") != projected["descriptor_sha256"]:
            raise InputError("task-version-mismatch", "candidate descriptor hash differs from catalog snapshot")
        candidate_claims, candidate_owner, candidate_contract_owner, candidate_contracts = _claims(projected, "candidate_view.task")
        canonical_claims, canonical_owner, canonical_contract_owner, canonical_contracts = _claims(canonical, "catalog_snapshot candidate task")
        if (
            _raw_set(candidate_claims) != _raw_set(canonical_claims)
            or candidate_owner != canonical_owner
            or candidate_contract_owner != canonical_contract_owner
            or candidate_contracts != canonical_contracts
            or projected["task_source"] != canonical.get("task_source")
        ):
            raise InputError("task-version-mismatch", "candidate descriptor fields differ from canonical catalog task")
        registered_owners = {owner for _, owner, _ in owner_patterns}
        if candidate_contract_owner not in registered_owners:
            raise InputError(
                "owner-mismatch",
                "candidate contract_owner is absent from the catalog owner registry",
                "BLOCKED",
            )
        logical_scopes = set(_list(projected["logical_scopes"], "candidate_view.task.scope.logical_scopes"))
        if not logical_scopes or any(not isinstance(item, str) or not item for item in logical_scopes):
            raise InputError(
                "owner-mismatch",
                "candidate logical_scopes must contain non-empty scope IDs",
                "BLOCKED",
            )

        handoff = _mapping(candidate.get("handoff"), "candidate_view.handoff")
        handoff_allowed = _parse_handoff_paths(handoff.get("allowed_files"), "candidate_view.handoff.allowed_files")
        if _raw_set(handoff_allowed) != _raw_set(candidate_claims):
            raise InputError(
                "claim-mismatch",
                "candidate handoff allowed_files differs from descriptor claims",
                "BLOCKED",
            )
        forbidden_raw = _string(handoff.get("forbidden_files"), "candidate_view.handoff.forbidden_files", "input-incomplete")

        all_catalog_patterns = [item[0] for item in owner_patterns] + contract_paths
        for entry_index, entry in enumerate(catalog_index.values()):
            entry_claims, _, _, _ = _claims(
                entry,
                f"catalog_snapshot.tasks[{entry_index}]",
                "input-incomplete",
                "FAIL",
            )
            all_catalog_patterns.extend(entry_claims)
        _validate_path_metadata(catalog, all_catalog_patterns + candidate_claims)

        if active.get("complete_for_dispatch_window") is not True or active.get("nonterminal_only") is not True:
            raise InputError("active-snapshot-declaration-missing", "active snapshot must declare complete_for_dispatch_window and nonterminal_only true")
        snapshot_source = _string(active.get("snapshot_source"), "active_instances.snapshot_source", "active-snapshot-declaration-missing")
        snapshot_sha256 = _sha(active.get("snapshot_sha256"), "active_instances.snapshot_sha256", "active-snapshot-declaration-missing")
        selected_at = _parse_timestamp(active.get("selected_at"), "active_instances.selected_at")
        raw_active = _list(active.get("instances"), "active_instances.instances", "active-snapshot-declaration-missing")

        diagnostics: list[dict[str, Any]] = []
        diagnostics.extend(_resolve_owners(candidate_claims, owner_patterns, candidate_owner, logical_scopes))
        diagnostics.extend(_validate_contract_declaration(candidate_identity, candidate_claims, candidate_contracts, registry, contract_paths))

        forbidden, forbidden_errors = _parse_forbidden_paths(
            forbidden_raw, "candidate_view.handoff.forbidden_files"
        )
        diagnostics.extend(
            _diag("FAIL", error.code, error.detail) for error in forbidden_errors
        )
        for allowed_path in candidate_claims:
            for forbidden_path in forbidden:
                if (
                    paths_intersect(_casefold_pattern(allowed_path), _casefold_pattern(forbidden_path))
                    and not paths_intersect(allowed_path, forbidden_path)
                ):
                    diagnostics.append(_diag("FAIL", "path-case-ambiguous", "candidate allowed and forbidden paths overlap only after casefold", allowed=allowed_path.raw, forbidden=forbidden_path.raw))
                if paths_intersect(allowed_path, forbidden_path):
                    diagnostics.append(_diag("BLOCKED", "forbidden-overlap", "candidate allowed and forbidden paths intersect", allowed=allowed_path.raw, forbidden=forbidden_path.raw))

        seen_instances: set[str] = set()
        for index, raw in enumerate(raw_active):
            instance = _mapping(raw, f"active_instances.instances[{index}]", "active-snapshot-invalid")
            allowed_instance_fields = {
                "task_id", "task_version", "change_version",
                "task_descriptor_sha256", "allowed_files", "instance_id",
                "client", "parent_session_id",
            }
            unknown_fields = set(instance) - allowed_instance_fields
            if unknown_fields:
                raise InputError(
                    "active-snapshot-invalid",
                    f"active instance has unsupported fields: {sorted(unknown_fields)}",
                )
            instance_id = _string(instance.get("instance_id"), f"active_instances.instances[{index}].instance_id", "active-snapshot-invalid")
            if not _SAFE_ID.fullmatch(instance_id) or instance_id in seen_instances:
                raise InputError("active-snapshot-invalid", f"invalid or duplicate active instance_id: {instance_id!r}")
            seen_instances.add(instance_id)
            client = _string(instance.get("client"), f"active_instances.instances[{index}].client", "active-snapshot-invalid")
            if client not in {"codex", "qoder"}:
                raise InputError("active-snapshot-invalid", f"active instance client is unsupported: {client}")
            _string(instance.get("parent_session_id"), f"active_instances.instances[{index}].parent_session_id", "active-snapshot-invalid")
            identity = _entry_identity(instance, f"active_instances.instances[{index}]", "active-snapshot-invalid")
            entry = catalog_index.get(identity)
            if entry is None:
                raise InputError("active-snapshot-invalid", f"active task is absent from catalog: {identity}")
            descriptor_hash = _sha(instance.get("task_descriptor_sha256"), f"active_instances.instances[{index}].task_descriptor_sha256", "active-snapshot-invalid")
            if descriptor_hash != entry.get("descriptor_sha256"):
                raise InputError("active-snapshot-invalid", f"active descriptor hash differs from catalog: {instance_id}")
            active_claims, active_owner, _, active_contracts = _claims(
                entry,
                f"catalog active task {identity}",
                "active-snapshot-invalid",
                "FAIL",
            )
            handoff_claims = _parse_handoff_paths(instance.get("allowed_files"), f"active_instances.instances[{index}].allowed_files")
            if _raw_set(handoff_claims) != _raw_set(active_claims):
                raise InputError("active-snapshot-invalid", f"active handoff allowed_files differs from canonical claims: {instance_id}")
            active_scopes = set(_list(entry.get("logical_scopes"), f"catalog active task {identity}.logical_scopes", "active-snapshot-invalid"))
            active_owner_diagnostics = _resolve_owners(active_claims, owner_patterns, active_owner, active_scopes)
            if active_owner_diagnostics:
                raise InputError(
                    "active-snapshot-invalid",
                    f"canonical active task has invalid owner or claim coverage: {instance_id}",
                )
            diagnostics.extend(_validate_contract_declaration(identity, active_claims, active_contracts, registry, contract_paths))
            for candidate_claim in candidate_claims:
                for active_claim in active_claims:
                    if (
                        paths_intersect(_casefold_pattern(candidate_claim), _casefold_pattern(active_claim))
                        and not paths_intersect(candidate_claim, active_claim)
                    ):
                        diagnostics.append(_diag("FAIL", "path-case-ambiguous", "candidate and active claims overlap only after casefold", candidate_claim=candidate_claim.raw, active_claim=active_claim.raw, active_instance_id=instance_id))
                    if paths_intersect(candidate_claim, active_claim):
                        diagnostics.append(_diag("BLOCKED", "write-overlap", "candidate and active write claims intersect", candidate_claim=candidate_claim.raw, active_claim=active_claim.raw, active_instance_id=instance_id, active_task_id=identity[0]))
            candidate_contract_names = {item["name"] for item in candidate_contracts}
            for contract in active_contracts:
                if contract["name"] in candidate_contract_names and identity != candidate_identity:
                    diagnostics.append(_diag("BLOCKED", "contract-writer-conflict", "candidate and active tasks write the same public contract", contract=contract["name"], active_instance_id=instance_id, active_task_id=identity[0]))

        status = "FAIL" if any(item["status"] == "FAIL" for item in diagnostics) else "BLOCKED" if diagnostics else "PASS"
        return _result(
            status,
            diagnostics,
            fingerprints,
            candidate={
                "task_id": candidate_identity[0],
                "task_version": candidate_identity[1],
                "change_version": candidate_identity[2],
                "descriptor_sha256": projected["descriptor_sha256"],
                "owner": candidate_owner,
                "claims": sorted(_raw_set(candidate_claims)),
                "produced_contracts": candidate_contracts,
            },
            active_declaration={
                "snapshot_source": snapshot_source,
                "snapshot_sha256": snapshot_sha256,
                "selected_at": selected_at,
                "complete_for_dispatch_window": True,
                "nonterminal_only": True,
                "instance_count": len(raw_active),
            },
        )
    except InputError as exc:
        return _result(exc.status, [_diag(exc.status, exc.code, exc.detail)], fingerprints)


def _load_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise InputError("input-incomplete", f"cannot read JSON input {path!r}: {exc}") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="check one frozen dispatch window")
    check.add_argument("--candidate", required=True)
    check.add_argument("--catalog", required=True)
    check.add_argument("--active", required=True)
    args = parser.parse_args(argv)
    try:
        result = check_dispatch(_load_json(args.candidate), _load_json(args.catalog), _load_json(args.active))
    except InputError as exc:
        result = _result(
            "FAIL",
            [_diag("FAIL", exc.code, exc.detail)],
            {"candidate_view_sha256": None, "catalog_snapshot_sha256": None, "active_instances_sha256": None},
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return {"PASS": 0, "BLOCKED": 1, "FAIL": 2}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
