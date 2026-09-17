"""Independent-review receipt verifier and publisher.

The CLI supplies a frozen Gate plan.  Kind-specific review inputs live in one
explicit, hash-bound test artifact so the generic evidence packet contract does
not need a review-only extension.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import PurePosixPath
from typing import Any, Callable

from scripts.gates.evidence_packet import EvidencePacketError, verify_snapshot_content_hash
from scripts.gates.receipt_store import (
    ImmutableReceiptStore,
    ReceiptStoreError,
    canonical_json_bytes,
    read_bound_bytes,
    safe_locator,
    sha256_bytes,
    validate_run_id,
)


REVIEW_EVIDENCE_SCHEMA = "lexiflow.independent-review-evidence.v1"
RECEIPT_SCHEMA = "lexiflow.gate-receipt.v1"
MANIFEST_SCHEMA = "lexiflow.gate-artifact-manifest.v1"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")


class IndependentReviewError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _fail(code: str, detail: str) -> None:
    raise IndependentReviewError(code, detail)


def _object(value: Any, fields: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        _fail("evidence-incomplete", f"{path} fields must be exactly {sorted(fields)}")
    return value


def _descriptor(value: Any, path: str) -> dict[str, str]:
    value = _object(value, {"locator", "sha256"}, path)
    try:
        locator = safe_locator(value["locator"])
    except ReceiptStoreError as exc:
        _fail("unsafe-locator", f"{path}: {exc.detail}")
    digest = value["sha256"]
    if not isinstance(digest, str) or not _HEX.fullmatch(digest):
        _fail("evidence-incomplete", f"{path}.sha256 invalid")
    return {"locator": locator, "sha256": digest}


def _read_descriptor(repo_root: str, value: Any, path: str) -> tuple[dict[str, str], bytes]:
    descriptor = _descriptor(value, path)
    try:
        content = read_bound_bytes(repo_root, descriptor["locator"])
    except ReceiptStoreError as exc:
        _fail("evidence-incomplete", f"{path}: {exc.detail}")
    if sha256_bytes(content) != descriptor["sha256"]:
        _fail("hash-drift", f"{path} hash mismatch")
    return descriptor, content


def _json_object(content: bytes, path: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _fail("evidence-incomplete", f"invalid {path}: {exc}")
    if not isinstance(value, dict) or canonical_json_bytes(value) != content:
        _fail("evidence-incomplete", f"{path} must be a canonical JSON object")
    return value


def _receipt_run(locator: str) -> str:
    parts = PurePosixPath(locator).parts
    if len(parts) != 5 or parts[:3] != ("tmp", "quality", "runs") or parts[4] != "receipt.json":
        _fail("unsafe-locator", "validation receipt must use its fixed immutable run path")
    try:
        return validate_run_id(parts[3])
    except ReceiptStoreError as exc:
        _fail("unsafe-locator", exc.detail)


def _verify_manifest(repo_root: str, receipt: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    descriptor, content = _read_descriptor(repo_root, receipt.get("artifact_manifest"), "validation.artifact_manifest")
    manifest = _json_object(content, "validation artifact manifest")
    if manifest.get("schema_version") != MANIFEST_SCHEMA or manifest.get("run_id") != receipt.get("run_id"):
        _fail("hash-drift", "validation artifact manifest identity mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        _fail("evidence-incomplete", "validation artifact manifest lacks artifacts")
    identities: dict[str, bytes] = {}
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict) or not {"identity", "kind", "locator", "sha256"}.issubset(artifact):
            _fail("evidence-incomplete", f"manifest artifact {index} malformed")
        identity = artifact["identity"]
        if not isinstance(identity, str) or not identity:
            _fail("evidence-incomplete", f"manifest artifact {index} identity invalid")
        current, leaf = _read_descriptor(repo_root, {"locator": artifact["locator"], "sha256": artifact["sha256"]}, f"manifest.artifacts[{index}]")
        if identity in identities and identities[identity] != leaf:
            _fail("hash-drift", f"duplicate artifact identity has different bytes: {identity}")
        identities[identity] = leaf
    return descriptor, manifest


def _verify_plan(repo_root: str, receipt: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    value = receipt.get("plan")
    if not isinstance(value, dict) or set(value) != {"locator", "sha256", "content_fingerprint"}:
        _fail("evidence-incomplete", "validation plan descriptor malformed")
    descriptor, content = _read_descriptor(repo_root, {"locator": value["locator"], "sha256": value["sha256"]}, "validation.plan")
    plan = _json_object(content, "validation plan")
    if plan.get("content_fingerprint") != value["content_fingerprint"]:
        _fail("hash-drift", "validation plan content fingerprint mismatch")
    projected = {key: item for key, item in plan.items() if key != "content_fingerprint"}
    if sha256_bytes(canonical_json_bytes(projected)) != value["content_fingerprint"]:
        _fail("hash-drift", "validation plan fingerprint is invalid")
    return descriptor, plan


def verify_validation_subject_current(repo_root: str, validation: dict[str, Any]) -> dict[str, str]:
    """Re-read the validation-bound subject snapshot against current repository bytes."""
    raw = validation.get("validation", {}).get("raw_artifacts")
    if not isinstance(raw, dict):
        _fail("stale-subject", "validation lacks bound raw artifacts")
    snapshot = _descriptor(raw.get("changed_file_snapshot"), "validation.raw_artifacts.changed_file_snapshot")
    try:
        verify_snapshot_content_hash(repo_root, {"subject": {"raw_artifacts": raw}})
    except EvidencePacketError as exc:
        _fail("stale-subject", f"validation subject snapshot is not current: {exc.code}")
    return snapshot


def _current_review_scope(plan: dict[str, Any], validation: dict[str, Any], scope: dict[str, Any]) -> dict[str, Any]:
    scope = _object(
        scope,
        {"source_snapshot_fingerprint", "registry_sha256", "policy_sha256", "source", "diff", "changed_files"},
        "review_scope",
    )
    source = _descriptor(scope["source"], "review_scope.source")
    diff = _descriptor(scope["diff"], "review_scope.diff")
    validation_source = validation.get("current_inputs", {}).get("task_source", {})
    if any(source.get(field) != validation_source.get(field) for field in ("locator", "sha256")):
        _fail("stale-subject", "review source descriptor differs from validation source")
    current_task_source = plan.get("task", {}).get("task_source", {})
    if source.get("locator") != current_task_source.get("locator") or source.get("sha256") != current_task_source.get("sha256"):
        _fail("stale-subject", "validation source is not current")
    if scope["source_snapshot_fingerprint"] != validation.get("current_inputs", {}).get("source_snapshot_fingerprint"):
        _fail("stale-subject", "source snapshot fingerprint drift")
    if scope["registry_sha256"] != validation.get("current_inputs", {}).get("registry", {}).get("sha256"):
        _fail("stale-subject", "registry hash drift")
    if scope["registry_sha256"] != plan.get("registry", {}).get("sha256"):
        _fail("stale-subject", "registry is not current")
    policy = next((item for item in plan.get("consumed_inputs", []) if item.get("locator") == "harness/agent-policy.manifest.yaml"), None)
    if not policy or scope["policy_sha256"] != policy.get("sha256") or scope["policy_sha256"] != validation.get("current_inputs", {}).get("policy", {}).get("sha256"):
        _fail("stale-subject", "policy hash drift")
    changed = scope["changed_files"]
    expected_changed = validation.get("validation", {}).get("scope_reconciliation", {}).get("changed_files")
    if not isinstance(changed, list) or changed != sorted(set(changed)) or changed != expected_changed:
        _fail("stale-subject", "reviewed changed-file scope differs from validation")
    validation_diff = validation.get("validation", {}).get("raw_artifacts", {}).get("diff", {})
    if diff != validation_diff:
        _fail("stale-subject", "reviewed diff descriptor differs from validation")
    return {**copy.deepcopy(scope), "source": source, "diff": diff}


def verify_independent_review(
    repo_root: str, *, validation_receipt: dict[str, str], reviewer_identity: dict[str, Any],
    review_run_id: str, reviewer_changed_files: list[str], review_scope: dict[str, Any],
    findings: list[dict[str, Any]], rerun_evidence: list[dict[str, str]], decision: str,
    current_plan: dict[str, Any],
) -> dict[str, Any]:
    """Pure-read verification of a completed validation and review assertions."""
    descriptor, receipt_bytes = _read_descriptor(repo_root, validation_receipt, "validation_receipt")
    validation_run = _receipt_run(descriptor["locator"])
    validation = _json_object(receipt_bytes, "validation receipt")
    if (
        validation.get("schema_version") != RECEIPT_SCHEMA
        or validation.get("receipt_kind") != "TASK_VALIDATION"
        or validation.get("run_id") != validation_run
    ):
        _fail("evidence-incomplete", "subject is not a TASK_VALIDATION receipt")
    if validation.get("result") != "PASS" or validation.get("completeness", {}).get("status") != "PASS":
        _fail("subject-validation-not-pass", "review requires a complete PASS validation receipt")
    _verify_plan(repo_root, validation)
    subject_snapshot = verify_validation_subject_current(repo_root, validation)
    manifest_descriptor, _ = _verify_manifest(repo_root, validation)

    reviewer_required = {"issuer_instance_id", "actor_type", "actor_id", "parent_session_id", "session_id", "client", "role"}
    if not isinstance(reviewer_identity, dict) or set(reviewer_identity) != reviewer_required:
        _fail("reviewer-not-independent", "reviewer identity is incomplete")
    producer = validation.get("validation", {}).get("subject_identity")
    validation_actor = validation.get("issuer", {}).get("actor_identity")
    if not isinstance(producer, dict) or not isinstance(validation_actor, dict):
        _fail("reviewer-not-independent", "producer identity chain is incomplete")
    if (
        reviewer_identity["issuer_instance_id"] == validation_actor.get("issuer_instance_id")
        or reviewer_identity["actor_id"] in {validation_actor.get("actor_id"), producer.get("agent_id")}
    ):
        _fail("reviewer-not-independent", "producer self-review is forbidden")
    if review_run_id in {validation_run, producer.get("run_id")}:
        _fail("reviewer-not-independent", "review run must differ from validation and subject runs")
    subject_files = set(validation.get("validation", {}).get("scope_reconciliation", {}).get("changed_files", []))
    if not isinstance(reviewer_changed_files, list) or any(not isinstance(item, str) for item in reviewer_changed_files):
        _fail("reviewer-not-independent", "reviewer_changed_files must be explicit")
    trusted_write_set = current_plan.get("scope", {}).get("changed_files")
    if (
        not isinstance(trusted_write_set, list)
        or any(not isinstance(item, str) for item in trusted_write_set)
        or trusted_write_set != sorted(set(trusted_write_set))
    ):
        _fail("reviewer-not-independent", "current review plan lacks a canonical trusted write set")
    if reviewer_changed_files != trusted_write_set:
        _fail("reviewer-not-independent", "self-reported reviewer writes differ from the frozen review plan")
    overlap = sorted(subject_files.intersection(trusted_write_set))
    if overlap:
        _fail("reviewer-not-independent", f"reviewer wrote subject files: {overlap}")

    scope = _current_review_scope(current_plan, validation, review_scope)
    _read_descriptor(repo_root, scope["source"], "review_scope.source")
    _read_descriptor(repo_root, scope["diff"], "review_scope.diff")
    if decision not in ("PASS", "BLOCKED", "FAIL"):
        _fail("evidence-incomplete", "review decision is invalid")
    if not isinstance(findings, list):
        _fail("evidence-incomplete", "findings must be a list")
    verified_findings = []
    for index, finding in enumerate(findings):
        finding = _object(finding, {"finding_id", "severity", "code", "evidence"}, f"findings[{index}]")
        if not isinstance(finding["finding_id"], str) or not _SAFE_ID.fullmatch(finding["finding_id"]):
            _fail("evidence-incomplete", f"findings[{index}].finding_id invalid")
        if finding["severity"] not in ("PASS", "BLOCKED", "FAIL"):
            _fail("evidence-incomplete", f"findings[{index}].severity invalid")
        if not isinstance(finding["code"], str) or not _SAFE_ID.fullmatch(finding["code"]):
            _fail("evidence-incomplete", f"findings[{index}].code invalid")
        if not isinstance(finding["evidence"], list) or not finding["evidence"]:
            _fail("evidence-incomplete", f"findings[{index}] lacks evidence")
        evidence = [_read_descriptor(repo_root, item, f"findings[{index}].evidence")[0] for item in finding["evidence"]]
        verified_findings.append({**copy.deepcopy(finding), "evidence": evidence})
    if not isinstance(rerun_evidence, list) or not rerun_evidence:
        _fail("evidence-incomplete", "rerun evidence is required")
    reruns = [_read_descriptor(repo_root, item, f"rerun_evidence[{index}]")[0] for index, item in enumerate(rerun_evidence)]
    finding_statuses = [item["severity"] for item in verified_findings]
    if decision == "PASS" and any(status != "PASS" for status in finding_statuses):
        _fail("decision-mismatch", "PASS review cannot contain blocking or failed findings")
    if decision == "BLOCKED" and "BLOCKED" not in finding_statuses:
        _fail("decision-mismatch", "BLOCKED review requires a BLOCKED finding")
    if decision == "FAIL" and "FAIL" not in finding_statuses:
        _fail("decision-mismatch", "FAIL review requires a FAIL finding")
    # Re-read after all linked artifacts to close the verification window.
    if read_bound_bytes(repo_root, descriptor["locator"]) != receipt_bytes:
        _fail("hash-drift", "validation receipt changed during review")
    return {
        "validation_receipt": descriptor,
        "validation_receipt_bytes": receipt_bytes,
        "validation": validation,
        "validation_manifest": manifest_descriptor,
        "review_scope": scope,
        "reviewer_changed_files": list(trusted_write_set),
        "findings": verified_findings,
        "rerun_evidence": reruns,
        "decision": decision,
        "independence": {
            "reviewer_issuer_packet_sha256": current_plan["issuer_packet"]["sha256"],
            "reviewer_identity": copy.deepcopy(reviewer_identity),
            "producer_identity": copy.deepcopy(producer),
            "validation_issuer_identity": copy.deepcopy(validation_actor),
            "producer_and_reviewer_differ": True,
            "review_run_is_distinct": True,
            "reviewer_write_set_source": "current-plan-scope-reconciliation",
            "subject_snapshot": subject_snapshot,
            "subject_snapshot_reverified": True,
            "reviewer_wrote_subject_files": False,
            "status": "PASS",
        },
    }


def _load_kind_evidence(repo_root: str, plan: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    matches = []
    for descriptor in plan.get("subject", {}).get("raw_artifacts", {}).get("tests", []):
        try:
            current, content = _read_descriptor(repo_root, descriptor, "review evidence candidate")
            value = _json_object(content, "review evidence candidate")
        except IndependentReviewError:
            continue
        if value.get("schema_version") == REVIEW_EVIDENCE_SCHEMA:
            matches.append((current, value))
    if len(matches) != 1:
        _fail("evidence-incomplete", "exactly one independent-review evidence artifact is required")
    descriptor, value = matches[0]
    expected = {
        "schema_version", "validation_receipt", "review_scope", "reviewer_changed_files",
        "findings", "rerun_evidence", "decision",
    }
    _object(value, expected, "independent_review_evidence")
    return descriptor, value


def publish_independent_review(
    *, store: ImmutableReceiptStore, plan: dict[str, Any], run_id: str,
    plan_artifact: dict[str, str], process_identity: dict[str, Any],
    execution: dict[str, Any], now: Callable[[], str], started_at: str | None = None, **_: Any,
) -> dict[str, Any]:
    repo_root = str(store.repo_root)
    evidence_descriptor, evidence = _load_kind_evidence(repo_root, plan)
    reviewer = {
        field: plan["issuer_packet"]["packet"][field]
        for field in ("issuer_instance_id", "actor_type", "actor_id", "parent_session_id", "session_id", "client", "role")
    }
    verified = verify_independent_review(
        repo_root,
        validation_receipt=evidence["validation_receipt"],
        reviewer_identity=reviewer,
        review_run_id=run_id,
        reviewer_changed_files=evidence["reviewer_changed_files"],
        review_scope=evidence["review_scope"],
        findings=evidence["findings"],
        rerun_evidence=evidence["rerun_evidence"],
        decision=evidence["decision"],
        current_plan=plan,
    )
    artifacts = [
        {"identity": "review:evidence", "kind": "independent-review-evidence", **evidence_descriptor},
        {"identity": f"receipt:{verified['validation']['run_id']}", "kind": "prior-validation-receipt", **verified["validation_receipt"]},
    ]
    if execution.get("checks") != [] or execution.get("run_status") != "PASS":
        _fail("invalid-review-execution", "independent review must consume evidence without delivery checks")
    for index, item in enumerate(verified["rerun_evidence"]):
        artifacts.append({"identity": f"review-rerun:{index}", "kind": "rerun-evidence", **item})
    manifest_value = {"schema_version": MANIFEST_SCHEMA, "run_id": run_id, "artifacts": artifacts}
    published_manifest = store.publish_json("artifact-manifest.json", manifest_value)
    if read_bound_bytes(repo_root, verified["validation_receipt"]["locator"]) != verified["validation_receipt_bytes"]:
        _fail("hash-drift", "validation receipt changed before review publication")
    result = verified["decision"]
    reasons = []
    if result != "PASS":
        reasons.extend(item["code"] for item in verified["findings"] if item["severity"] == result)
    packet = plan["issuer_packet"]
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_kind": "INDEPENDENT_REVIEW",
        "run_id": run_id,
        "plan": {**plan_artifact, "content_fingerprint": plan["content_fingerprint"]},
        "issuer": {
            "trusted_issuer_packet": {"locator": packet["locator"], "sha256": packet["sha256"]},
            "actor_identity": reviewer,
            "process_identity": process_identity,
        },
        "started_at": started_at or execution.get("started_at"),
        "finished_at": now(),
        "task": {field: plan["task"][field] for field in ("task_id", "task_version", "change_version")},
        "current_inputs": {
            "source_snapshot_fingerprint": verified["review_scope"]["source_snapshot_fingerprint"],
            "task_source": verified["review_scope"]["source"],
            "registry": {"locator": plan["registry"]["locator"], "sha256": verified["review_scope"]["registry_sha256"]},
            "policy": {"locator": "harness/agent-policy.manifest.yaml", "sha256": verified["review_scope"]["policy_sha256"]},
        },
        "subject_validation": {**verified["validation_receipt"], "result": verified["validation"]["result"]},
        "reviewer_independence": verified["independence"],
        "review_scope": verified["review_scope"],
        "findings": verified["findings"],
        "rerun_evidence": verified["rerun_evidence"],
        "review_checks": {
            "execution": "evidence-consumption",
            "outcomes": [],
            "aggregation": copy.deepcopy(execution.get("aggregation")),
        },
        "decision": verified["decision"],
        "artifact_manifest": {"locator": published_manifest.locator, "sha256": published_manifest.sha256},
        "completeness": {"required_fields_checked": True, "kind_specific_fields_checked": True, "status": "PASS"},
        "canonical_rerun": {
            "argv": [
                "python3", "scripts/gates/cli.py", "run", "--mode", plan["mode"],
                "--evidence-packet", plan["subject"]["explicit_evidence_packet"]["locator"],
                "--issuer-packet", packet["locator"], "--receipt-kind", "INDEPENDENT_REVIEW",
            ]
        },
        "result": result,
        "reasons": sorted(set(reasons)),
    }
    store.publish_receipt(receipt)
    return receipt
