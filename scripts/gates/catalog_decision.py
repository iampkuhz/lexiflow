"""Current-input catalog decision verifier and immutable receipt publisher."""

from __future__ import annotations

import copy
import json
import re
from pathlib import PurePosixPath
from typing import Any, Callable

import yaml

from scripts.gates.hash_dag import HashDagError, verify_hash_dag
from scripts.gates.independent_review import (
    IndependentReviewError,
    verify_validation_subject_current,
)
from scripts.gates.planner import PlannerError, verify_trusted_issuer_packet
from scripts.gates.receipt_store import (
    ImmutableReceiptStore,
    ReceiptStoreError,
    canonical_json_bytes,
    read_bound_bytes,
    safe_locator,
    sha256_bytes,
    validate_run_id,
)


CATALOG_EVIDENCE_SCHEMA = "lexiflow.catalog-decision-evidence.v1"
RECEIPT_SCHEMA = "lexiflow.gate-receipt.v1"
MANIFEST_SCHEMA = "lexiflow.gate-artifact-manifest.v1"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_CASE = re.compile(r"^## (LF-[A-Z0-9-]+)\b", re.MULTILINE)
_FORBIDDEN_ASSERTION_KEYS = {"user_approval", "approval", "approved", "bootstrap", "backfill"}


class CatalogDecisionError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _fail(code: str, detail: str) -> None:
    raise CatalogDecisionError(code, detail)


def _object(value: Any, fields: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        _fail("evidence-incomplete", f"{path} fields must be exactly {sorted(fields)}")
    return value


def _descriptor(value: Any, path: str) -> dict[str, str]:
    if not isinstance(value, dict) or not {"locator", "sha256"}.issubset(value):
        _fail("evidence-incomplete", f"{path} descriptor missing")
    try:
        locator = safe_locator(value["locator"])
    except ReceiptStoreError as exc:
        _fail("unsafe-locator", f"{path}: {exc.detail}")
    digest = value["sha256"]
    if not isinstance(digest, str) or not _HEX.fullmatch(digest):
        _fail("evidence-incomplete", f"{path}.sha256 invalid")
    return {"locator": locator, "sha256": digest}


def _read(repo_root: str, value: Any, path: str) -> tuple[dict[str, str], bytes]:
    descriptor = _descriptor(value, path)
    try:
        content = read_bound_bytes(repo_root, descriptor["locator"])
    except ReceiptStoreError as exc:
        _fail("evidence-incomplete", f"{path}: {exc.detail}")
    if sha256_bytes(content) != descriptor["sha256"]:
        _fail("hash-drift", f"{path} hash mismatch")
    return descriptor, content


def _json(content: bytes, path: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _fail("evidence-incomplete", f"invalid {path}: {exc}")
    if not isinstance(value, dict) or canonical_json_bytes(value) != content:
        _fail("evidence-incomplete", f"{path} must be a canonical JSON object")
    return value


class _UniqueLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueLoader, node: yaml.nodes.MappingNode, deep: bool = False) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError:
            _fail("invalid-catalog", "unhashable YAML key")
        if duplicate:
            _fail("invalid-catalog", f"duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _catalog_tasks(content: bytes) -> dict[str, dict[str, Any]]:
    try:
        value = yaml.load(content, Loader=_UniqueLoader)
    except yaml.YAMLError as exc:
        _fail("invalid-catalog", str(exc))
    if not isinstance(value, dict) or not isinstance(value.get("workstreams"), list):
        _fail("invalid-catalog", "planning catalog lacks workstreams")
    tasks: dict[str, dict[str, Any]] = {}
    for workstream in value["workstreams"]:
        for epic in workstream.get("epics", []) if isinstance(workstream, dict) else []:
            for capability in epic.get("capabilities", []) if isinstance(epic, dict) else []:
                for task in capability.get("seed_tasks", []) if isinstance(capability, dict) else []:
                    if not isinstance(task, dict) or not isinstance(task.get("id"), str):
                        _fail("invalid-catalog", "catalog task malformed")
                    if task["id"] in tasks:
                        _fail("invalid-catalog", f"duplicate task: {task['id']}")
                    tasks[task["id"]] = task
    return tasks


def verify_acceptance_registry(
    catalog_content: bytes, acceptance_content: bytes, *, expected_registry: dict[str, str],
) -> dict[str, Any]:
    if sha256_bytes(acceptance_content) != expected_registry["sha256"]:
        _fail("stale-acceptance-registry", "acceptance-case registry hash is not current")
    try:
        text = acceptance_content.decode("utf-8")
    except UnicodeDecodeError:
        _fail("invalid-acceptance-registry", "acceptance-case registry is not UTF-8")
    case_list = _CASE.findall(text)
    duplicates = sorted({case for case in case_list if case_list.count(case) > 1})
    tasks = _catalog_tasks(catalog_content)
    mappings: dict[str, list[str]] = {}
    for task_id, task in tasks.items():
        case_ids = task.get("acceptance_case_ids", [])
        if case_ids is None:
            case_ids = []
        if not isinstance(case_ids, list) or any(not isinstance(case, str) for case in case_ids):
            _fail("invalid-mapping", f"{task_id}.acceptance_case_ids invalid")
        if len(case_ids) != len(set(case_ids)):
            _fail("invalid-mapping", f"{task_id} contains duplicate acceptance mappings")
        for case in case_ids:
            mappings.setdefault(case, []).append(task_id)
    registry_cases = set(case_list)
    orphan_cases = sorted(registry_cases - set(mappings))
    unknown_mappings = sorted(set(mappings) - registry_cases)
    duplicate_mappings = sorted(case for case, owners in mappings.items() if len(owners) != 1)
    status = "PASS" if case_list and not duplicates and not orphan_cases and not unknown_mappings and not duplicate_mappings else "FAIL"
    return {
        "locator": expected_registry["locator"],
        "sha256": expected_registry["sha256"],
        "case_count": len(case_list),
        "orphan_cases": orphan_cases,
        "duplicate_cases": duplicates,
        "unknown_mappings": unknown_mappings,
        "duplicate_mappings": duplicate_mappings,
        "mappings": {case: mappings[case][0] for case in sorted(mappings) if len(mappings[case]) == 1},
        "current_mapping_status": status,
    }


def _fixed_receipt_run(descriptor: dict[str, str], path: str) -> str:
    parts = PurePosixPath(descriptor["locator"]).parts
    if len(parts) != 5 or parts[:3] != ("tmp", "quality", "runs") or parts[4] != "receipt.json":
        _fail("unsafe-locator", f"{path} must use a fixed run receipt path")
    try:
        return validate_run_id(parts[3])
    except ReceiptStoreError as exc:
        _fail("unsafe-locator", f"{path}: {exc.detail}")


def _load_receipt(repo_root: str, value: Any, path: str) -> tuple[dict[str, str], dict[str, Any]]:
    descriptor, content = _read(repo_root, value, path)
    run_id = _fixed_receipt_run(descriptor, path)
    receipt = _json(content, path)
    if receipt.get("schema_version") != RECEIPT_SCHEMA or receipt.get("run_id") != run_id:
        _fail("identity-mismatch", f"{path} receipt identity mismatch")
    return descriptor, receipt


def _verify_receipt_issuer_chain(
    repo_root: str, root_descriptor: dict[str, str], *, max_receipts: int = 10000,
) -> list[dict[str, Any]]:
    """Re-verify every receipt issuer in an explicit prior-receipt chain."""
    verified: dict[str, dict[str, Any]] = {}
    visiting: set[str] = set()
    try:
        executable_sha256 = sha256_bytes(read_bound_bytes(repo_root, "scripts/gates/cli.py"))
    except ReceiptStoreError as exc:
        _fail("issuer-authority-invalid", f"gate executable cannot be read: {exc.detail}")

    def visit(value: Any, path: str) -> None:
        descriptor, receipt = _load_receipt(repo_root, value, path)
        locator = descriptor["locator"]
        if locator in visiting:
            _fail("self-reference", f"receipt issuer chain cycles through {locator}")
        if locator in verified:
            if verified[locator]["receipt"]["sha256"] != descriptor["sha256"]:
                _fail("identity-mismatch", f"receipt locator has conflicting hashes: {locator}")
            return
        if len(verified) >= max_receipts:
            _fail("evidence-incomplete", "receipt issuer chain exceeds finite node limit")
        kind = receipt.get("receipt_kind")
        if kind not in ("TASK_VALIDATION", "INDEPENDENT_REVIEW", "CATALOG_DECISION"):
            _fail("identity-mismatch", f"{path} has invalid receipt kind")
        if kind == "TASK_VALIDATION":
            try:
                verify_validation_subject_current(repo_root, receipt)
            except IndependentReviewError as exc:
                _fail("stale-input", f"{path} subject snapshot: {exc.code}")
        issuer = receipt.get("issuer")
        if not isinstance(issuer, dict):
            _fail("issuer-authority-invalid", f"{path} lacks issuer binding")
        packet = _descriptor(issuer.get("trusted_issuer_packet"), f"{path}.issuer.trusted_issuer_packet")
        try:
            frozen = verify_trusted_issuer_packet(
                repo_root, locator=packet["locator"], sha256=packet["sha256"], receipt_kind=kind,
            )
        except PlannerError as exc:
            _fail("issuer-authority-invalid", f"{path}: {exc.code}")
        actor_fields = (
            "issuer_instance_id", "actor_type", "actor_id", "parent_session_id",
            "session_id", "client", "role",
        )
        expected_actor = {field: frozen["packet"][field] for field in actor_fields}
        if issuer.get("actor_identity") != expected_actor:
            _fail("issuer-authority-invalid", f"{path} actor identity differs from trusted packet")
        process = issuer.get("process_identity")
        if (
            not isinstance(process, dict)
            or process.get("gate_run_id") != receipt.get("run_id")
            or process.get("issuer_packet_sha256") != packet["sha256"]
            or process.get("executable_locator") != "scripts/gates/cli.py"
            or process.get("executable_sha256") != executable_sha256
        ):
            _fail("issuer-authority-invalid", f"{path} process/issuer binding is invalid")
        try:
            process_instance_id = validate_run_id(process.get("process_instance_id"))
        except ReceiptStoreError as exc:
            _fail("issuer-authority-invalid", f"{path} process identity: {exc.detail}")
        if process_instance_id in {receipt["run_id"], expected_actor["issuer_instance_id"]}:
            _fail("issuer-authority-invalid", f"{path} process identity is not distinct")
        verified[locator] = {
            "receipt": descriptor,
            "receipt_kind": kind,
            "issuer_packet": packet,
            "authority_registry": copy.deepcopy(frozen["authority_registry"]),
            "authority_evidence": copy.deepcopy(frozen["authority_evidence"]),
            "status": "PASS",
        }
        visiting.add(locator)
        try:
            if kind == "INDEPENDENT_REVIEW":
                visit(receipt.get("subject_validation"), f"{path}.subject_validation")
            elif kind == "CATALOG_DECISION":
                subjects = receipt.get("subject_receipts")
                if not isinstance(subjects, dict) or set(subjects) != {"task_validation", "independent_review"}:
                    _fail("evidence-incomplete", f"{path} subject receipts are incomplete")
                visit(subjects["task_validation"], f"{path}.subject_receipts.task_validation")
                visit(subjects["independent_review"], f"{path}.subject_receipts.independent_review")
                dependencies = receipt.get("required_dependency_receipts")
                if not isinstance(dependencies, list):
                    _fail("evidence-incomplete", f"{path} dependency receipts are malformed")
                for index, dependency in enumerate(dependencies):
                    visit(dependency, f"{path}.required_dependency_receipts[{index}]")
        finally:
            visiting.remove(locator)

    visit(root_descriptor, "receipt")
    return [verified[locator] for locator in sorted(verified)]


def _current_descriptor(repo_root: str, expected: dict[str, Any], path: str) -> dict[str, str]:
    descriptor = _descriptor(expected, path)
    _, content = _read(repo_root, descriptor, path)
    if sha256_bytes(content) != descriptor["sha256"]:
        _fail("stale-input", f"{path} changed")
    return descriptor


def _receipt_freshness(
    receipt: dict[str, Any], *, task: dict[str, Any], source: dict[str, str],
    registry: dict[str, str], policy: dict[str, str], path: str,
) -> None:
    identity = receipt.get("task")
    if not isinstance(identity, dict) or any(identity.get(field) != task.get(field) for field in ("task_id", "task_version", "change_version")):
        _fail("identity-mismatch", f"{path} task identity mismatch")
    current = receipt.get("current_inputs")
    if not isinstance(current, dict):
        _fail("stale-input", f"{path} lacks current inputs")
    receipt_source = current.get("task_source", {})
    if any(receipt_source.get(field) != source[field] for field in ("locator", "sha256")):
        _fail("stale-input", f"{path} source is stale")
    receipt_registry = current.get("registry", {})
    if any(receipt_registry.get(field) != registry[field] for field in ("locator", "sha256")):
        _fail("stale-input", f"{path} registry is stale")
    receipt_policy = current.get("policy", {})
    if any(receipt_policy.get(field) != policy[field] for field in ("locator", "sha256")):
        _fail("stale-input", f"{path} policy is stale")


def _review_independence(validation: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    evidence = review.get("reviewer_independence")
    if not isinstance(evidence, dict) or evidence.get("status") != "PASS":
        _fail("reviewer-not-independent", "review independence status is not PASS")
    if (
        evidence.get("reviewer_write_set_source") != "current-plan-scope-reconciliation"
        or evidence.get("subject_snapshot_reverified") is not True
        or evidence.get("subject_snapshot")
        != validation.get("validation", {}).get("raw_artifacts", {}).get("changed_file_snapshot")
    ):
        _fail("reviewer-not-independent", "review lacks current subject-snapshot proof")
    reviewer = review.get("issuer", {}).get("actor_identity")
    producer = validation.get("validation", {}).get("subject_identity")
    validation_actor = validation.get("issuer", {}).get("actor_identity")
    if not all(isinstance(item, dict) for item in (reviewer, producer, validation_actor)):
        _fail("reviewer-not-independent", "identity chains are incomplete")
    if (
        reviewer.get("issuer_instance_id") == validation_actor.get("issuer_instance_id")
        or reviewer.get("actor_id") in {validation_actor.get("actor_id"), producer.get("agent_id")}
        or review.get("run_id") in {validation.get("run_id"), producer.get("run_id")}
        or evidence.get("reviewer_wrote_subject_files") is not False
        or evidence.get("producer_and_reviewer_differ") is not True
        or evidence.get("review_run_is_distinct") is not True
    ):
        _fail("reviewer-not-independent", "review independence reconciliation failed")
    if evidence.get("reviewer_identity") != reviewer:
        _fail("reviewer-not-independent", "stored reviewer identity differs from issuer identity")
    stored_producer = evidence.get("producer_identity")
    if not isinstance(stored_producer, dict) or any(producer.get(key) != value for key, value in stored_producer.items()):
        _fail("reviewer-not-independent", "stored producer identity differs from validation subject")
    packet_hash = review.get("issuer", {}).get("trusted_issuer_packet", {}).get("sha256")
    if evidence.get("reviewer_issuer_packet_sha256") != packet_hash:
        _fail("reviewer-not-independent", "reviewer packet hash mismatch")
    return copy.deepcopy(evidence)


def _forbidden_assertions(value: Any, path: str = "evidence") -> None:
    if isinstance(value, dict):
        forbidden = _FORBIDDEN_ASSERTION_KEYS.intersection(value)
        if forbidden:
            _fail("forged-approval", f"forbidden inferred assertion at {path}: {sorted(forbidden)}")
        for key, item in value.items():
            _forbidden_assertions(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _forbidden_assertions(item, f"{path}[{index}]")


def verify_catalog_decision(
    repo_root: str, *, current_plan: dict[str, Any], evidence: dict[str, Any], current_run_id: str,
) -> dict[str, Any]:
    _forbidden_assertions(evidence)
    expected_fields = {
        "schema_version", "task_validation", "independent_review",
        "required_dependency_receipts", "acceptance_case_registry",
    }
    _object(evidence, expected_fields, "catalog_decision_evidence")
    if evidence["schema_version"] != CATALOG_EVIDENCE_SCHEMA:
        _fail("evidence-incomplete", "catalog evidence schema mismatch")
    current_run_id = validate_run_id(current_run_id)
    source = _current_descriptor(repo_root, current_plan["task"]["task_source"], "current task source")
    if source["locator"] != "planning/workstreams.yaml":
        _fail("unsafe-locator", "unexpected task source locator")
    registry = _current_descriptor(repo_root, current_plan["registry"], "current check registry")
    policy_input = next(
        (item for item in current_plan.get("consumed_inputs", []) if item.get("locator") == "harness/agent-policy.manifest.yaml"),
        None,
    )
    if policy_input is None:
        _fail("evidence-incomplete", "current plan lacks policy input")
    policy = _current_descriptor(repo_root, policy_input, "current policy")
    acceptance = _current_descriptor(repo_root, evidence["acceptance_case_registry"], "acceptance registry")
    if acceptance["locator"] != "docs/product/product-brief.md":
        _fail("unsafe-locator", "unexpected acceptance registry locator")
    catalog_content = read_bound_bytes(repo_root, source["locator"])
    acceptance_content = read_bound_bytes(repo_root, acceptance["locator"])
    acceptance_reconciliation = verify_acceptance_registry(
        catalog_content, acceptance_content, expected_registry=acceptance,
    )
    if acceptance_reconciliation["current_mapping_status"] != "PASS":
        _fail("invalid-mapping", "acceptance registry contains orphan, duplicate, or unknown mappings")

    validation_descriptor, validation = _load_receipt(repo_root, evidence["task_validation"], "task_validation")
    review_descriptor, review = _load_receipt(repo_root, evidence["independent_review"], "independent_review")
    if current_run_id in {validation["run_id"], review["run_id"]}:
        _fail("self-reference", "catalog run cannot consume itself")
    if validation.get("receipt_kind") != "TASK_VALIDATION" or review.get("receipt_kind") != "INDEPENDENT_REVIEW":
        _fail("identity-mismatch", "subject receipt kinds are invalid")
    issuer_verifications: dict[str, dict[str, Any]] = {}

    def absorb_issuer_chain(descriptor: dict[str, str]) -> None:
        for item in _verify_receipt_issuer_chain(repo_root, descriptor):
            locator = item["receipt"]["locator"]
            previous = issuer_verifications.get(locator)
            if previous is not None and previous != item:
                _fail("identity-mismatch", f"issuer verification drift for {locator}")
            issuer_verifications[locator] = item

    absorb_issuer_chain(validation_descriptor)
    absorb_issuer_chain(review_descriptor)
    task_identity = {field: current_plan["task"][field] for field in ("task_id", "task_version", "change_version")}
    _receipt_freshness(validation, task=task_identity, source=source, registry=registry, policy=policy, path="task_validation")
    _receipt_freshness(review, task=task_identity, source=source, registry=registry, policy=policy, path="independent_review")
    validation_snapshot = validation.get("current_inputs", {}).get("source_snapshot_fingerprint")
    review_snapshot = review.get("current_inputs", {}).get("source_snapshot_fingerprint")
    if (
        not isinstance(validation_snapshot, str)
        or not _HEX.fullmatch(validation_snapshot)
        or review_snapshot != validation_snapshot
    ):
        _fail("stale-input", "validation and review source snapshots differ")
    reviewed_subject = _descriptor(review.get("subject_validation"), "independent_review.subject_validation")
    if reviewed_subject != validation_descriptor:
        _fail("identity-mismatch", "review does not bind the supplied validation receipt")
    independence = _review_independence(validation, review)

    declared_dependencies = current_plan["task"].get("dependencies", [])
    supplied_dependencies = evidence["required_dependency_receipts"]
    if not isinstance(supplied_dependencies, list):
        _fail("evidence-incomplete", "required_dependency_receipts must be a list")
    if len(supplied_dependencies) != len(declared_dependencies):
        _fail("missing-dependency", "dependency receipt count differs from current task")
    supplied_by_id: dict[str, dict[str, Any]] = {}
    for index, supplied in enumerate(supplied_dependencies):
        if not isinstance(supplied, dict) or set(supplied) != {"task_id", "task_version", "change_version", "locator", "sha256"}:
            _fail("evidence-incomplete", f"dependency receipt {index} malformed")
        if supplied["task_id"] in supplied_by_id:
            _fail("identity-mismatch", f"duplicate dependency receipt: {supplied['task_id']}")
        supplied_by_id[supplied["task_id"]] = supplied
    dependency_receipts = []
    hash_results = []
    for declared in declared_dependencies:
        task_id = declared["task_id"]
        supplied = supplied_by_id.get(task_id)
        if supplied is None:
            _fail("missing-dependency", f"missing dependency receipt: {task_id}")
        if supplied["task_version"] != declared["required_task_version"] or supplied["change_version"] != declared["required_change_version"]:
            _fail("stale-dependency", f"dependency pin mismatch: {task_id}")
        descriptor, receipt = _load_receipt(repo_root, supplied, f"dependency[{task_id}]")
        if receipt.get("receipt_kind") != "CATALOG_DECISION":
            _fail("identity-mismatch", f"dependency {task_id} is not a catalog decision")
        dependency_task = {"task_id": task_id, "task_version": supplied["task_version"], "change_version": supplied["change_version"]}
        _receipt_freshness(receipt, task=dependency_task, source=source, registry=registry, policy=policy, path=f"dependency[{task_id}]")
        subject_receipts = receipt.get("subject_receipts")
        hash_verifications = receipt.get("hash_dag_verifications")
        acceptance_status = receipt.get("current_inputs", {}).get("acceptance_case_registry", {}).get("current_mapping_status")
        semantic_pass = (
            receipt.get("completeness", {}).get("status") == "PASS"
            and receipt.get("freshness_reconciliation", {}).get("status") == "PASS"
            and receipt.get("reviewer_independence", {}).get("status") == "PASS"
            and acceptance_status == "PASS"
            and isinstance(subject_receipts, dict)
            and all(subject_receipts.get(name, {}).get("result") == "PASS" for name in ("task_validation", "independent_review"))
            and isinstance(hash_verifications, list)
            and bool(hash_verifications)
            and all(item.get("result") == "PASS" for item in hash_verifications if isinstance(item, dict))
            and all(isinstance(item, dict) for item in hash_verifications)
        )
        if (
            receipt.get("result") != declared.get("required_result")
            or receipt.get("catalog_task_status", receipt.get("result")) != "PASS"
            or not semantic_pass
        ):
            _fail("dependency-not-pass", f"dependency {task_id} is not current-input PASS")
        absorb_issuer_chain(descriptor)
        try:
            verification = verify_hash_dag(repo_root, descriptor)
        except HashDagError as exc:
            _fail("hash-graph-invalid", f"dependency {task_id}: {exc.detail}")
        hash_results.append(verification)
        dependency_receipts.append({**supplied, "result": receipt["result"]})
    if set(supplied_by_id) != {item["task_id"] for item in declared_dependencies}:
        _fail("identity-mismatch", "unexpected dependency receipt supplied")
    packet_descriptor = current_plan.get("subject", {}).get("explicit_evidence_packet")
    try:
        hash_results.append(verify_hash_dag(repo_root, packet_descriptor))
    except HashDagError as exc:
        _fail("hash-graph-invalid", f"explicit_evidence_packet: {exc.detail}")
    for label, descriptor in (("task_validation", validation_descriptor), ("independent_review", review_descriptor)):
        try:
            hash_results.append(verify_hash_dag(repo_root, descriptor))
        except HashDagError as exc:
            _fail("hash-graph-invalid", f"{label}: {exc.detail}")
    if any(result.get("result") != "PASS" for result in hash_results):
        _fail("hash-graph-invalid", "hash verification was not PASS")
    subject_results = [validation.get("result"), review.get("result")]
    if "FAIL" in subject_results:
        catalog_result = "FAIL"
    elif "BLOCKED" in subject_results:
        catalog_result = "BLOCKED"
    elif subject_results == ["PASS", "PASS"]:
        catalog_result = "PASS"
    else:
        _fail("evidence-incomplete", "subject receipt result is invalid")
    return {
        "task": task_identity,
        "current_inputs": {
            "source_snapshot_fingerprint": validation.get("current_inputs", {}).get("source_snapshot_fingerprint"),
            "task_source": source,
            "registry": registry,
            "policy": policy,
            "acceptance_case_registry": acceptance_reconciliation,
        },
        "subject_receipts": {
            "task_validation": {**validation_descriptor, "result": validation["result"]},
            "independent_review": {**review_descriptor, "result": review["result"]},
        },
        "required_dependency_receipts": dependency_receipts,
        "reviewer_independence": independence,
        "issuer_authority_verifications": [issuer_verifications[key] for key in sorted(issuer_verifications)],
        "freshness_reconciliation": {
            "task_version_match": True,
            "change_version_match": True,
            "source_snapshot_match": True,
            "registry_policy_match": True,
            "dependency_receipts_current": True,
            "status": "PASS",
        },
        "hash_dag_verifications": hash_results,
        "catalog_result": catalog_result,
    }


def _load_kind_evidence(repo_root: str, plan: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    matches = []
    for descriptor in plan.get("subject", {}).get("raw_artifacts", {}).get("tests", []):
        try:
            current, content = _read(repo_root, descriptor, "catalog evidence candidate")
            value = _json(content, "catalog evidence candidate")
        except CatalogDecisionError:
            continue
        if value.get("schema_version") == CATALOG_EVIDENCE_SCHEMA:
            matches.append((current, value))
    if len(matches) != 1:
        _fail("evidence-incomplete", "exactly one catalog-decision evidence artifact is required")
    return matches[0]


def publish_catalog_decision(
    *, store: ImmutableReceiptStore, plan: dict[str, Any], run_id: str,
    plan_artifact: dict[str, str], process_identity: dict[str, Any],
    execution: dict[str, Any], now: Callable[[], str], started_at: str | None = None, **_: Any,
) -> dict[str, Any]:
    evidence_descriptor, evidence = _load_kind_evidence(str(store.repo_root), plan)
    verified = verify_catalog_decision(
        str(store.repo_root), current_plan=plan, evidence=evidence, current_run_id=run_id,
    )
    artifacts = [{"identity": "catalog:evidence", "kind": "catalog-decision-evidence", **evidence_descriptor}]
    if execution.get("checks") != [] or execution.get("run_status") != "PASS":
        _fail("invalid-catalog-execution", "catalog decision must consume receipts without delivery checks")
    manifest = store.publish_json(
        "artifact-manifest.json",
        {"schema_version": MANIFEST_SCHEMA, "run_id": run_id, "artifacts": artifacts},
    )
    result = verified["catalog_result"]
    reasons = []
    if result != "PASS" and not reasons:
        reasons.append("catalog-evidence-not-pass")
    packet = plan["issuer_packet"]
    actor = {
        field: packet["packet"][field]
        for field in ("issuer_instance_id", "actor_type", "actor_id", "parent_session_id", "session_id", "client", "role")
    }
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_kind": "CATALOG_DECISION",
        "run_id": run_id,
        "plan": {**plan_artifact, "content_fingerprint": plan["content_fingerprint"]},
        "issuer": {
            "trusted_issuer_packet": {"locator": packet["locator"], "sha256": packet["sha256"]},
            "actor_identity": actor,
            "process_identity": process_identity,
        },
        "started_at": started_at or execution.get("started_at"),
        "finished_at": now(),
        "task": verified["task"],
        "current_inputs": verified["current_inputs"],
        "subject_receipts": verified["subject_receipts"],
        "required_dependency_receipts": verified["required_dependency_receipts"],
        "reviewer_independence": verified["reviewer_independence"],
        "issuer_authority_verifications": verified["issuer_authority_verifications"],
        "freshness_reconciliation": verified["freshness_reconciliation"],
        "hash_dag_verifications": verified["hash_dag_verifications"],
        "catalog_checks": {
            "execution": "evidence-consumption",
            "outcomes": [],
            "aggregation": copy.deepcopy(execution.get("aggregation")),
        },
        "artifact_manifest": {"locator": manifest.locator, "sha256": manifest.sha256},
        "completeness": {"required_fields_checked": True, "kind_specific_fields_checked": True, "status": "PASS"},
        "canonical_rerun": {
            "argv": [
                "python3", "scripts/gates/cli.py", "run", "--mode", plan["mode"],
                "--evidence-packet", plan["subject"]["explicit_evidence_packet"]["locator"],
                "--issuer-packet", packet["locator"], "--receipt-kind", "CATALOG_DECISION",
            ]
        },
        "catalog_task_status": result,
        "result": result,
        "reasons": sorted(set(reasons)),
    }
    store.publish_receipt(receipt)
    return receipt
