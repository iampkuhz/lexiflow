"""Pure-read verifier for immutable Gate evidence hash DAGs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from scripts.gates.receipt_store import (
    ReceiptStoreError,
    canonical_json_bytes,
    read_bound_bytes,
    safe_locator,
    sha256_bytes,
)


VERIFICATION_SCHEMA = "lexiflow.evidence-hash-dag-verification.v1"
RECEIPT_SCHEMA = "lexiflow.gate-receipt.v1"
MANIFEST_SCHEMA = "lexiflow.gate-artifact-manifest.v1"
PLAN_SCHEMA = "lexiflow.gate-plan.v1"
_HEX = re.compile(r"^[0-9a-f]{64}$")


class HashDagError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _invalid(detail: str) -> None:
    raise HashDagError("hash-graph-invalid", detail)


@dataclass(frozen=True)
class _Edge:
    locator: str
    sha256: str
    identity: str | None = None


def _descriptor(value: Any, path: str) -> _Edge:
    if not isinstance(value, dict):
        _invalid(f"{path} is not a descriptor")
    locator = value.get("locator")
    digest = value.get("sha256")
    try:
        locator = safe_locator(locator)
    except ReceiptStoreError as exc:
        _invalid(f"{path}: {exc.detail}")
    if not isinstance(digest, str) or not _HEX.fullmatch(digest):
        _invalid(f"{path}.sha256 is invalid")
    return _Edge(locator, digest)


def _extract_edges(value: Any, path: str = "node") -> list[_Edge]:
    edges: list[_Edge] = []
    active: set[int] = set()
    identity_namespace = (
        f"manifest:{value['run_id']}:"
        if isinstance(value, dict)
        and value.get("schema_version") == MANIFEST_SCHEMA
        and isinstance(value.get("run_id"), str)
        else ""
    )

    def walk(current: Any, current_path: str) -> None:
        if isinstance(current, (dict, list)):
            marker = id(current)
            if marker in active:
                _invalid(f"in-memory object cycle at {current_path}")
            active.add(marker)
        try:
            if isinstance(current, dict):
                if "locator" in current:
                    if "sha256" not in current:
                        if set(current) == {"locator", "state"}:
                            try:
                                safe_locator(current["locator"])
                            except ReceiptStoreError as exc:
                                _invalid(f"{current_path}: {exc.detail}")
                            if current["state"] != "absent":
                                _invalid(f"invalid absent-file observation at {current_path}")
                            # A pre-state absence record deliberately has no
                            # bytes to follow.  Its containing artifact is
                            # hash-bound by the parent edge.
                            return
                        observation_fields = {
                            "locator", "expected_sha256", "actual_sha256", "status",
                        }
                        if set(current) != observation_fields:
                            _invalid(f"partial descriptor at {current_path}")
                        try:
                            safe_locator(current["locator"])
                        except ReceiptStoreError as exc:
                            _invalid(f"{current_path}: {exc.detail}")
                        expected = current["expected_sha256"]
                        actual = current["actual_sha256"]
                        if (
                            current["status"] != "verified"
                            or not isinstance(expected, str)
                            or not _HEX.fullmatch(expected)
                            or actual != expected
                        ):
                            _invalid(f"invalid verified-input observation at {current_path}")
                        # Executor verification metadata is not an artifact edge;
                        # the observed input bytes are already frozen by the plan.
                        return
                    edge = _descriptor(current, current_path)
                    identity = current.get("identity")
                    if identity is not None and (not isinstance(identity, str) or not identity):
                        _invalid(f"invalid explicit identity at {current_path}")
                    if identity is not None:
                        identity = identity_namespace + identity
                    edges.append(_Edge(edge.locator, edge.sha256, identity))
                    # Other descriptor metadata cannot introduce hidden edges.
                    return
                for key in sorted(current):
                    walk(current[key], f"{current_path}.{key}")
            elif isinstance(current, list):
                for index, item in enumerate(current):
                    walk(item, f"{current_path}[{index}]")
        finally:
            if isinstance(current, (dict, list)):
                active.remove(id(current))

    walk(value, path)
    return edges


def _parse_json(content: bytes, locator: str) -> Any | None:
    # Repository JSON Schema contracts are source inputs, not runner evidence.
    # Their property names describe descriptors; their bytes are still verified
    # by the incoming hash edge. Evidence under tmp/ never gets this exemption.
    source_path = PurePosixPath(locator)
    if source_path.parts[0] == "harness" and source_path.name.endswith(".schema.json"):
        return None
    # Command streams and logs are hash-bound opaque bytes even when their
    # payload happens to be JSON. Structured graph nodes use the .json suffix.
    if PurePosixPath(locator).suffix != ".json":
        return None
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, item in items:
            if key in result:
                _invalid(f"duplicate JSON key at {locator}: {key}")
            result[key] = item
        return result

    def nonfinite(value: str) -> None:
        _invalid(f"non-finite JSON number at {locator}: {value}")

    try:
        value = json.loads(content, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if canonical_json_bytes(value) != content:
        structured_schemas = {
            RECEIPT_SCHEMA,
            MANIFEST_SCHEMA,
            PLAN_SCHEMA,
            "lexiflow.trusted-issuer-packet.v1",
            "lexiflow.explicit-evidence-packet.v1",
            "lexiflow.independent-review-evidence.v1",
            "lexiflow.catalog-decision-evidence.v1",
        }

        if isinstance(value, dict) and value.get("schema_version") in structured_schemas:
            _invalid(f"structured JSON evidence node is not canonical: {locator}")
        # Raw runner/auxiliary JSON keeps its exact hash-bound bytes. Whitespace
        # does not suppress its edges; strict parsing still rejects ambiguity.
    return value


def _schema_identity(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    schema = value.get("schema_version")
    if schema == RECEIPT_SCHEMA and isinstance(value.get("run_id"), str):
        return f"receipt:{value['run_id']}"
    if schema == MANIFEST_SCHEMA and isinstance(value.get("run_id"), str):
        return f"manifest:{value['run_id']}"
    if schema == PLAN_SCHEMA and isinstance(value.get("content_fingerprint"), str):
        return f"plan:{value['content_fingerprint']}"
    if schema == "lexiflow.trusted-issuer-packet.v1" and isinstance(value.get("issuer_instance_id"), str):
        return f"issuer:{value['issuer_instance_id']}"
    if schema == "lexiflow.explicit-evidence-packet.v1" and isinstance(value.get("publication_id"), str):
        return f"evidence:{value['publication_id']}"
    return None


def _timestamp(value: Any, path: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _invalid(f"{path} is not a UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _invalid(f"{path} is invalid")


def _required_receipt_edges(receipt: dict[str, Any], locator: str) -> None:
    if receipt.get("schema_version") != RECEIPT_SCHEMA:
        _invalid(f"receipt schema mismatch at {locator}")
    kind = receipt.get("receipt_kind")
    if kind not in ("TASK_VALIDATION", "INDEPENDENT_REVIEW", "CATALOG_DECISION"):
        _invalid(f"receipt kind invalid at {locator}")
    _descriptor(receipt.get("plan"), f"{locator}.plan")
    _descriptor(receipt.get("artifact_manifest"), f"{locator}.artifact_manifest")
    if kind == "INDEPENDENT_REVIEW":
        _descriptor(receipt.get("subject_validation"), f"{locator}.subject_validation")
    if kind == "CATALOG_DECISION":
        subjects = receipt.get("subject_receipts")
        if not isinstance(subjects, dict) or set(subjects) != {"task_validation", "independent_review"}:
            _invalid(f"catalog subject receipts incomplete at {locator}")
        _descriptor(subjects["task_validation"], f"{locator}.subject_receipts.task_validation")
        _descriptor(subjects["independent_review"], f"{locator}.subject_receipts.independent_review")
        dependencies = receipt.get("required_dependency_receipts")
        if not isinstance(dependencies, list):
            _invalid(f"catalog dependency receipt set malformed at {locator}")
        for index, dependency in enumerate(dependencies):
            _descriptor(dependency, f"{locator}.required_dependency_receipts[{index}]")


def verify_hash_dag(
    repo_root: str, root_receipt: dict[str, str], *, max_nodes: int = 10000,
) -> dict[str, Any]:
    """Verify a complete finite graph from one explicit immutable root receipt."""
    root = _descriptor(root_receipt, "root_receipt")
    states: dict[str, str] = {}
    bytes_by_locator: dict[str, bytes] = {}
    hash_by_locator: dict[str, str] = {}
    identity_bytes: dict[str, bytes] = {}
    graph: dict[str, list[str]] = {}
    receipt_times: dict[str, tuple[datetime, datetime]] = {}
    edge_count = 0

    def bind_identity(identity: str | None, content: bytes, locator: str) -> None:
        if identity is None:
            return
        previous = identity_bytes.get(identity)
        if previous is not None and previous != content:
            _invalid(f"identity collision with different bytes: {identity}")
        identity_bytes[identity] = content

    def visit(edge: _Edge, parent: str | None = None) -> None:
        nonlocal edge_count
        if len(bytes_by_locator) >= max_nodes and edge.locator not in bytes_by_locator:
            _invalid("graph exceeds finite node limit")
        if parent is not None:
            edge_count += 1
            if edge.locator == parent:
                _invalid(f"self-edge at {parent}")
        state = states.get(edge.locator)
        if state == "visiting":
            _invalid(f"cycle detected through {edge.locator}")
        if edge.locator in hash_by_locator and hash_by_locator[edge.locator] != edge.sha256:
            _invalid(f"one locator has conflicting hashes: {edge.locator}")
        hash_by_locator[edge.locator] = edge.sha256
        if state == "done":
            bind_identity(edge.identity, bytes_by_locator[edge.locator], edge.locator)
            return
        try:
            content = read_bound_bytes(repo_root, edge.locator)
        except ReceiptStoreError as exc:
            _invalid(f"missing or unsafe node {edge.locator}: {exc.detail}")
        if sha256_bytes(content) != edge.sha256:
            _invalid(f"changed node bytes: {edge.locator}")
        bytes_by_locator[edge.locator] = content
        bind_identity(edge.identity, content, edge.locator)
        states[edge.locator] = "visiting"
        value = _parse_json(content, edge.locator)
        bind_identity(_schema_identity(value), content, edge.locator)
        if isinstance(value, dict) and value.get("schema_version") == RECEIPT_SCHEMA:
            _required_receipt_edges(value, edge.locator)
            started = _timestamp(value.get("started_at"), f"{edge.locator}.started_at")
            finished = _timestamp(value.get("finished_at"), f"{edge.locator}.finished_at")
            if finished < started:
                _invalid(f"receipt finished before it started: {edge.locator}")
            receipt_times[edge.locator] = (started, finished)
        children = _extract_edges(value, edge.locator) if value is not None else []
        seen_child: set[tuple[str, str, str | None]] = set()
        graph[edge.locator] = []
        for child in children:
            key = (child.locator, child.sha256, child.identity)
            if key in seen_child:
                continue
            seen_child.add(key)
            graph[edge.locator].append(child.locator)
            visit(child, edge.locator)
        states[edge.locator] = "done"

    visit(root)

    # Receipt references must move backward in time. This rejects an acyclic
    # forward-reference as well as the cycles caught during DFS.
    for parent, children in graph.items():
        if parent not in receipt_times:
            continue
        parent_started, _ = receipt_times[parent]
        for child in children:
            if child in receipt_times and receipt_times[child][1] > parent_started:
                _invalid(f"receipt back/forward edge is not prior: {parent} -> {child}")

    # Close the read window for every node; the verifier never repairs drift.
    for locator, original in bytes_by_locator.items():
        try:
            current = read_bound_bytes(repo_root, locator)
        except ReceiptStoreError as exc:
            _invalid(f"node disappeared during verification: {locator}: {exc.detail}")
        if current != original:
            _invalid(f"node changed during verification: {locator}")
    return {
        "schema_version": VERIFICATION_SCHEMA,
        "root_receipt": {"locator": root.locator, "sha256": root.sha256},
        "nodes_verified": len(bytes_by_locator),
        "edges_verified": edge_count,
        "identities_verified": len(identity_bytes),
        "complete": True,
        "finite": True,
        "acyclic": True,
        "result": "PASS",
        "reasons": [],
    }


def verify_hash_dag_result(repo_root: str, root_receipt: dict[str, str]) -> dict[str, Any]:
    try:
        return verify_hash_dag(repo_root, root_receipt)
    except HashDagError as exc:
        return {
            "schema_version": VERIFICATION_SCHEMA,
            "root_receipt": root_receipt,
            "complete": False,
            "finite": False,
            "acyclic": False,
            "result": "FAIL",
            "reasons": [exc.code],
            "detail": exc.detail,
        }
