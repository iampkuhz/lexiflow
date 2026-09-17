"""Single public Gate CLI for planning, execution, receipt publication, and status."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, TextIO

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.gates.executor import execute_checks
from scripts.gates.planner import PlannerError, compile_plan
from scripts.gates.receipt_store import (
    START_SCHEMA,
    ImmutableReceiptStore,
    ReceiptStoreError,
    canonical_json_bytes,
    read_bound_bytes,
    read_status,
    safe_locator,
    sha256_bytes,
)


RECEIPT_SCHEMA = "lexiflow.gate-receipt.v1"
MANIFEST_SCHEMA = "lexiflow.gate-artifact-manifest.v1"
CALLER_EVENT_SCHEMA = "lexiflow.gate-caller-event.v1"
RECEIPT_KINDS = ("TASK_VALIDATION", "INDEPENDENT_REVIEW", "CATALOG_DECISION")


class GateCliError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _resolve_locator(flag: str | None, env_name: str, env: dict[str, str]) -> str:
    bound = env.get(env_name)
    if flag is not None and bound is not None and flag != bound:
        raise GateCliError("evidence-context-conflict", f"{env_name} conflicts with CLI flag")
    value = flag if flag is not None else bound
    if value is None or not value:
        raise GateCliError("missing-evidence-context", f"missing {env_name}")
    try:
        return safe_locator(value)
    except ReceiptStoreError as exc:
        raise GateCliError(exc.code, exc.detail) from None


def resolve_context(
    *, evidence_packet: str | None, issuer_packet: str | None, env: dict[str, str] | None = None
) -> tuple[str, str]:
    values = os.environ if env is None else env
    return (
        _resolve_locator(evidence_packet, "LEXIFLOW_GATE_EVIDENCE_PACKET", values),
        _resolve_locator(issuer_packet, "LEXIFLOW_GATE_ISSUER_PACKET", values),
    )


def compile_from_context(
    repo_root: str | os.PathLike[str], *, mode: str, receipt_kind: str,
    evidence_packet: str, issuer_packet: str,
    compiler: Callable[..., dict[str, Any]] = compile_plan,
) -> dict[str, Any]:
    evidence_bytes = read_bound_bytes(repo_root, evidence_packet)
    issuer_bytes = read_bound_bytes(repo_root, issuer_packet)
    return compiler(
        repo_root,
        mode=mode,
        receipt_kind=receipt_kind,
        evidence_packet_locator=evidence_packet,
        evidence_packet_sha256=sha256_bytes(evidence_bytes),
        issuer_packet_locator=issuer_packet,
        issuer_packet_sha256=sha256_bytes(issuer_bytes),
    )


def _revalidate_plan_bindings(repo_root: str | os.PathLike[str], plan: dict[str, Any]) -> None:
    descriptors: list[dict[str, Any]] = []
    descriptors.extend(plan.get("consumed_inputs", []))
    task_source = plan.get("task", {}).get("task_source")
    if isinstance(task_source, dict):
        descriptors.append(task_source)
    if isinstance(plan.get("registry"), dict):
        descriptors.append(plan["registry"])
    subject = plan.get("subject", {})
    if isinstance(subject.get("explicit_evidence_packet"), dict):
        descriptors.append(subject["explicit_evidence_packet"])
    for value in subject.get("raw_artifacts", {}).values():
        descriptors.extend(value if isinstance(value, list) else [value])
    issuer = plan.get("issuer_packet", {})
    descriptors.append(issuer)
    for field in ("authority_registry", "authority_evidence"):
        if isinstance(issuer.get(field), dict):
            descriptors.append(issuer[field])
    descriptors.extend(issuer.get("provenance", []))
    seen: dict[str, str] = {}
    for index, descriptor in enumerate(descriptors):
        if not isinstance(descriptor, dict):
            raise GateCliError("input-drift", f"plan descriptor {index} is missing")
        if descriptor.get("state", "present") != "present":
            raise GateCliError("input-drift", f"plan descriptor {index} is not present")
        locator = descriptor.get("locator")
        digest = descriptor.get("sha256")
        if not isinstance(locator, str) or not isinstance(digest, str):
            raise GateCliError("input-drift", f"plan descriptor {index} is malformed")
        if locator in seen and seen[locator] != digest:
            raise GateCliError("input-drift", f"conflicting plan hashes for {locator}")
        seen[locator] = digest
        try:
            content = read_bound_bytes(repo_root, locator)
        except ReceiptStoreError as exc:
            raise GateCliError("input-drift", exc.detail) from None
        if sha256_bytes(content) != digest:
            raise GateCliError("input-drift", f"bound input changed: {locator}")


def _actor_identity(plan: dict[str, Any]) -> dict[str, Any]:
    packet = plan["issuer_packet"]["packet"]
    fields = (
        "issuer_instance_id", "actor_type", "actor_id", "parent_session_id",
        "session_id", "client", "role",
    )
    return {field: packet[field] for field in fields}


def _descriptor(plan: dict[str, Any], locator: str) -> dict[str, str] | None:
    for item in plan.get("consumed_inputs", []):
        if item.get("locator") == locator and item.get("state") == "present":
            return {"locator": locator, "sha256": item["sha256"]}
    return None


def _aggregate_status(values: list[str]) -> str:
    if not values or any(value not in ("PASS", "BLOCKED", "FAIL") for value in values):
        return "FAIL"
    if "FAIL" in values:
        return "FAIL"
    if "BLOCKED" in values:
        return "BLOCKED"
    return "PASS"


def _evidence_only_execution(plan: dict[str, Any], *, started_at: str, finished_at: str) -> dict[str, Any]:
    """Describe a review/decision run that consumed receipts without rerunning delivery checks."""
    return {
        "schema_version": "lexiflow.gate-check-outcome.v1",
        "plan_content_fingerprint": plan["content_fingerprint"],
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": 0.0,
        "checks": [],
        "run_status": "PASS",
        "run_reason": "",
        "aggregation": {
            "total": 0, "passed": 0, "blocked": 0, "failed": 0,
            "required_total": 0, "required_passed": 0,
        },
    }


def _validate_plan_execution_layer(plan: dict[str, Any], receipt_kind: str) -> None:
    expected = (
        {
            "layer": "delivery-validation",
            "checker_execution": "required",
            "source": "selected-registry-checks",
        }
        if receipt_kind == "TASK_VALIDATION"
        else {
            "layer": "evidence-consumption",
            "checker_execution": "forbidden",
            "source": "prior-immutable-receipts",
        }
    )
    if plan.get("execution") != expected:
        raise GateCliError("invalid-plan", "plan execution layer does not match receipt kind")
    checks = plan.get("checks")
    if not isinstance(checks, list):
        raise GateCliError("invalid-plan", "plan checks must be a list")
    if receipt_kind == "TASK_VALIDATION" and not checks:
        raise GateCliError("invalid-plan", "delivery validation requires selected checks")
    if receipt_kind != "TASK_VALIDATION" and checks:
        raise GateCliError("invalid-plan", "evidence-consumption plans cannot select delivery checks")


def _task_validation_result(plan: dict[str, Any], execution: dict[str, Any]) -> tuple[str, list[str]]:
    attested = plan["subject"]["main_agent_attestation"]["result_fields"]
    statuses = [execution.get("run_status"), attested.get("status"), attested.get("validation", {}).get("status")]
    statuses.extend(attested.get("effect_checks", {}).values())
    reasons: list[str] = []
    result = _aggregate_status(statuses)
    if execution.get("run_reason"):
        reasons.append(str(execution["run_reason"]))
    if result != "PASS" and not reasons:
        reasons.append("subject-evidence-not-pass")
    if not execution.get("checks"):
        result = "FAIL"
        reasons.append("evidence-incomplete")
    return result, sorted(set(reasons))


def _validate_task_validation_execution(plan: dict[str, Any], execution: dict[str, Any]) -> None:
    """Reject executor payloads that cannot support a schema-complete receipt."""
    if not isinstance(execution, dict):
        raise GateCliError("evidence-incomplete", "executor result must be an object")
    if execution.get("schema_version") != "lexiflow.gate-check-outcome.v1":
        raise GateCliError("evidence-incomplete", "executor result schema mismatch")
    if execution.get("plan_content_fingerprint") != plan.get("content_fingerprint"):
        raise GateCliError("evidence-incomplete", "executor result is bound to another plan")
    if (
        not isinstance(execution.get("started_at"), str)
        or not execution["started_at"]
        or not isinstance(execution.get("finished_at"), str)
        or not execution["finished_at"]
        or not isinstance(execution.get("duration_seconds"), (int, float))
        or isinstance(execution.get("duration_seconds"), bool)
        or execution["duration_seconds"] < 0
    ):
        raise GateCliError("evidence-incomplete", "executor lifecycle facts are malformed")

    declared_checks = plan.get("checks")
    executed_checks = execution.get("checks")
    if not isinstance(declared_checks, list) or not declared_checks:
        raise GateCliError("evidence-incomplete", "plan has no checks")
    if not isinstance(executed_checks, list) or len(executed_checks) != len(declared_checks):
        raise GateCliError("evidence-incomplete", "executor did not retain every planned check")

    declared_by_id: dict[str, dict[str, Any]] = {}
    for declared in declared_checks:
        if not isinstance(declared, dict) or not isinstance(declared.get("check_id"), str):
            raise GateCliError("evidence-incomplete", "planned check identity is malformed")
        if declared["check_id"] in declared_by_id:
            raise GateCliError("evidence-incomplete", "planned check identity is duplicated")
        declared_by_id[declared["check_id"]] = declared

    statuses: list[str] = []
    seen: set[str] = set()
    process_fields = {
        "started_at", "finished_at", "duration_seconds", "argv", "argv_fingerprint",
        "cwd", "cwd_fingerprint", "return_code", "signal", "exit_reason",
        "stdout_bytes", "stderr_bytes", "stdout_sha256", "stderr_sha256",
        "stdout_truncated", "stderr_truncated", "capture_error",
        "environment_fingerprint", "child_pid", "timeout_seconds",
    }
    hash_fields = {
        "argv_fingerprint", "cwd_fingerprint", "stdout_sha256",
        "stderr_sha256", "environment_fingerprint",
    }
    outcome_fields = {
        "schema", "status", "reason", "assertions", "suites", "failures",
        "errors", "skipped", "evidence",
    }
    is_sha256 = lambda value: (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
    for check in executed_checks:
        if not isinstance(check, dict):
            raise GateCliError("evidence-incomplete", "executed check is not an object")
        check_id = check.get("check_id")
        declared = declared_by_id.get(check_id)
        if declared is None or check_id in seen:
            raise GateCliError("evidence-incomplete", "executed check identity is missing or duplicated")
        seen.add(check_id)
        if (
            check.get("required") is not declared.get("required")
            or check.get("command_id") != declared.get("command_id")
        ):
            raise GateCliError("evidence-incomplete", f"executed check binding drifted: {check_id}")

        process = check.get("process")
        outcome = check.get("outcome")
        verification = check.get("consumed_input_verification")
        if not isinstance(process, dict) or not process_fields.issubset(process):
            raise GateCliError("evidence-incomplete", f"process facts are incomplete: {check_id}")
        if process.get("argv") != declared.get("fixed_argv"):
            raise GateCliError("evidence-incomplete", f"executed argv drifted: {check_id}")
        if (
            not isinstance(process.get("cwd"), str)
            or not process["cwd"]
            or process.get("argv_fingerprint") != sha256_bytes(canonical_json_bytes(process["argv"]))
            or process.get("cwd_fingerprint") != sha256_bytes(process["cwd"].encode("utf-8"))
            or any(not is_sha256(process.get(field)) for field in hash_fields)
            or not isinstance(process.get("duration_seconds"), (int, float))
            or isinstance(process.get("duration_seconds"), bool)
            or process["duration_seconds"] < 0
            or not isinstance(process.get("started_at"), str)
            or not process["started_at"]
            or not isinstance(process.get("finished_at"), str)
            or not process["finished_at"]
            or any(
                not isinstance(process.get(field), int)
                or isinstance(process.get(field), bool)
                or process[field] < 0
                for field in ("stdout_bytes", "stderr_bytes")
            )
            or not isinstance(process.get("timeout_seconds"), int)
            or isinstance(process.get("timeout_seconds"), bool)
            or process["timeout_seconds"] <= 0
            or any(
                not isinstance(process.get(field), bool)
                for field in ("stdout_truncated", "stderr_truncated", "capture_error")
            )
            or (
                process.get("return_code") is not None
                and (
                    not isinstance(process["return_code"], int)
                    or isinstance(process["return_code"], bool)
                )
            )
            or (
                process.get("signal") is not None
                and (
                    not isinstance(process["signal"], int)
                    or isinstance(process["signal"], bool)
                    or process["signal"] <= 0
                )
            )
            or (
                process.get("child_pid") is not None
                and (
                    not isinstance(process["child_pid"], int)
                    or isinstance(process["child_pid"], bool)
                    or process["child_pid"] <= 0
                )
            )
            or not isinstance(process.get("exit_reason"), str)
            or not process["exit_reason"]
        ):
            raise GateCliError("evidence-incomplete", f"process facts are malformed: {check_id}")
        if (
            not isinstance(outcome, dict)
            or not outcome_fields.issubset(outcome)
            or outcome.get("schema") != "lexiflow.check-outcome.v1"
            or not isinstance(outcome.get("evidence"), dict)
        ):
            raise GateCliError("evidence-incomplete", f"typed outcome is malformed: {check_id}")
        status = outcome.get("status")
        if status not in ("PASS", "BLOCKED", "FAIL") or not isinstance(outcome.get("reason"), str):
            raise GateCliError("evidence-incomplete", f"typed outcome state is malformed: {check_id}")
        if status != "PASS" and not outcome["reason"]:
            raise GateCliError("evidence-incomplete", f"non-PASS outcome lacks a reason: {check_id}")
        if not isinstance(verification, dict):
            raise GateCliError("evidence-incomplete", f"input verification is missing: {check_id}")
        for phase in ("pre_execution", "post_execution"):
            if not isinstance(verification.get(phase), list):
                raise GateCliError("evidence-incomplete", f"input verification phase is missing: {check_id}")
            if any(not isinstance(item, dict) for item in verification[phase]):
                raise GateCliError("evidence-incomplete", f"input verification item is malformed: {check_id}")
        if status == "PASS":
            if (
                outcome.get("reason")
                or process.get("exit_reason") != "EXITED"
                or process.get("return_code") != 0
                or process.get("signal") is not None
                or not isinstance(process.get("child_pid"), int)
                or isinstance(process.get("child_pid"), bool)
                or process["child_pid"] <= 0
                or process.get("stdout_truncated") is not False
                or process.get("stderr_truncated") is not False
                or process.get("capture_error") is not False
                or any(
                    item.get("status") != "verified"
                    for phase in ("pre_execution", "post_execution")
                    for item in verification[phase]
                    if isinstance(item, dict)
                )
            ):
                raise GateCliError("evidence-incomplete", f"PASS check lacks execution proof: {check_id}")
        statuses.append(status)

    if seen != set(declared_by_id):
        raise GateCliError("evidence-incomplete", "executor check set does not match the plan")
    expected_status = _aggregate_status(statuses)
    if execution.get("run_status") != expected_status:
        raise GateCliError("evidence-incomplete", "executor aggregation status mismatch")
    run_reason = execution.get("run_reason")
    if not isinstance(run_reason, str) or (expected_status == "PASS" and run_reason):
        raise GateCliError("evidence-incomplete", "executor aggregation reason mismatch")
    if expected_status != "PASS" and not run_reason:
        raise GateCliError("evidence-incomplete", "executor aggregation reason is missing")
    expected_aggregation = {
        "total": len(statuses),
        "passed": statuses.count("PASS"),
        "blocked": statuses.count("BLOCKED"),
        "failed": statuses.count("FAIL"),
        "required_total": sum(1 for item in executed_checks if item["required"]),
        "required_passed": sum(
            1 for item in executed_checks
            if item["required"] and item["outcome"]["status"] == "PASS"
        ),
    }
    if execution.get("aggregation") != expected_aggregation:
        raise GateCliError("evidence-incomplete", "executor aggregation counts mismatch")


def _canonical_rerun(plan: dict[str, Any]) -> dict[str, list[str]]:
    argv = [
        "python3", "scripts/gates/cli.py", "run", "--mode", plan["mode"],
        "--evidence-packet", plan["subject"]["explicit_evidence_packet"]["locator"],
        "--issuer-packet", plan["issuer_packet"]["locator"],
        "--receipt-kind", plan["receipt_kind"],
    ]
    return {"argv": argv}


def _build_task_validation_receipt(
    *, plan: dict[str, Any], run_id: str, plan_artifact: dict[str, str],
    process_identity: dict[str, Any], execution: dict[str, Any],
    manifest: dict[str, str], check_artifacts: list[dict[str, str]], finished_at: str,
) -> dict[str, Any]:
    result, reasons = _task_validation_result(plan, execution)
    checks = []
    for check, artifact in zip(execution.get("checks", []), check_artifacts):
        declared = next((value for value in plan["checks"] if value["check_id"] == check["check_id"]), None)
        if declared is None:
            result, reasons = "FAIL", sorted(set(reasons + ["evidence-incomplete"]))
            continue
        checks.append({
            "check_id": check["check_id"],
            "required": check["required"],
            "declared_validation_command": declared["declared_validation_command"],
            "command_id": declared["command_id"],
            "fixed_argv": copy.deepcopy(declared["fixed_argv"]),
            "registry_entry_sha256": declared["registry_entry_sha256"],
            "process": copy.deepcopy(check["process"]),
            "typed_outcome": copy.deepcopy(check["outcome"]),
            "evidence": artifact,
        })
    packet = plan["issuer_packet"]
    raw = plan["subject"]["raw_artifacts"]
    snapshot = raw.get("changed_file_snapshot", {})
    policy = _descriptor(plan, "harness/agent-policy.manifest.yaml")
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_kind": "TASK_VALIDATION",
        "run_id": run_id,
        "plan": {**plan_artifact, "content_fingerprint": plan["content_fingerprint"]},
        "issuer": {
            "trusted_issuer_packet": {"locator": packet["locator"], "sha256": packet["sha256"]},
            "actor_identity": _actor_identity(plan),
            "process_identity": process_identity,
        },
        "started_at": execution["started_at"],
        "finished_at": finished_at,
        "task": {field: plan["task"][field] for field in ("task_id", "task_version", "change_version")},
        "current_inputs": {
            "source_snapshot_fingerprint": snapshot.get("sha256"),
            "task_source": copy.deepcopy(plan["task"]["task_source"]),
            "registry": copy.deepcopy(plan["registry"]),
            "policy": policy,
        },
        "validation": {
            "explicit_evidence_packet": copy.deepcopy(plan["subject"]["explicit_evidence_packet"]),
            "raw_artifacts": copy.deepcopy(raw),
            "main_agent_attestation": copy.deepcopy(plan["subject"]["main_agent_attestation"]),
            "subject_identity": copy.deepcopy(plan["subject"]["identity"]),
            "scope_reconciliation": copy.deepcopy(plan["scope"]),
            "checks": checks,
            "expectations": copy.deepcopy(plan["expectations"]),
            "execution_aggregation": copy.deepcopy(execution["aggregation"]),
        },
        "artifact_manifest": manifest,
        "completeness": {
            "required_fields_checked": True,
            "kind_specific_fields_checked": bool(checks) and len(checks) == len(plan["checks"]),
            "status": "PASS" if checks and len(checks) == len(plan["checks"]) else "FAIL",
        },
        "canonical_rerun": _canonical_rerun(plan),
        "result": result,
        "reasons": reasons,
    }
    if receipt["completeness"]["status"] != "PASS":
        receipt["result"] = "FAIL"
        receipt["reasons"] = sorted(set(receipt["reasons"] + ["evidence-incomplete"]))
    return receipt


def _publish_task_validation(
    *, store: ImmutableReceiptStore, plan: dict[str, Any], run_id: str,
    plan_artifact: dict[str, str], process_identity: dict[str, Any],
    execution: dict[str, Any], now: Callable[[], str], started_at: str | None = None, **_: Any,
) -> dict[str, Any]:
    _validate_task_validation_execution(plan, execution)
    check_artifacts: list[dict[str, str]] = []
    manifest_entries: list[dict[str, Any]] = []
    for index, check in enumerate(execution.get("checks", [])):
        artifact = store.publish_json(f"check-outcome-{index:04d}.json", check)
        descriptor = {"locator": artifact.locator, "sha256": artifact.sha256}
        check_artifacts.append(descriptor)
        manifest_entries.append({
            "identity": f"check:{check.get('check_id', index)}", "kind": "typed-check-outcome", **descriptor,
        })
    raw = plan["subject"]["raw_artifacts"]
    for key, value in raw.items():
        values = value if isinstance(value, list) else [value]
        for index, descriptor in enumerate(values):
            manifest_entries.append({
                "identity": f"subject:{key}:{index}", "kind": f"subject-{key}",
                "locator": descriptor["locator"], "sha256": descriptor["sha256"],
            })
    manifest_value = {
        "schema_version": MANIFEST_SCHEMA,
        "run_id": run_id,
        "artifacts": manifest_entries,
    }
    manifest_artifact = store.publish_json("artifact-manifest.json", manifest_value)
    receipt = _build_task_validation_receipt(
        plan=plan, run_id=run_id, plan_artifact=plan_artifact,
        process_identity=process_identity, execution=execution,
        manifest={"locator": manifest_artifact.locator, "sha256": manifest_artifact.sha256},
        check_artifacts=check_artifacts, finished_at=now(),
    )
    if started_at is not None:
        receipt["started_at"] = started_at
    store.publish_receipt(receipt)
    return receipt


def get_receipt_handler(receipt_kind: str) -> Callable[..., dict[str, Any]]:
    if receipt_kind == "TASK_VALIDATION":
        return _publish_task_validation
    if receipt_kind == "INDEPENDENT_REVIEW":
        from scripts.gates.independent_review import publish_independent_review
        return publish_independent_review
    if receipt_kind == "CATALOG_DECISION":
        from scripts.gates.catalog_decision import publish_catalog_decision
        return publish_catalog_decision
    raise GateCliError("unsupported-receipt-kind", receipt_kind)


_installed_handler = get_receipt_handler


def _publish_failed_receipt(
    *, store: ImmutableReceiptStore, plan: dict[str, Any], run_id: str,
    plan_artifact: dict[str, str], process_identity: dict[str, Any],
    started_at: str, now: Callable[[], str], code: str,
) -> dict[str, Any]:
    """Finalize a started run that could not produce kind-complete evidence."""
    packet = plan["issuer_packet"]
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_kind": plan["receipt_kind"],
        "run_id": run_id,
        "plan": {**plan_artifact, "content_fingerprint": plan["content_fingerprint"]},
        "issuer": {
            "trusted_issuer_packet": {"locator": packet["locator"], "sha256": packet["sha256"]},
            "actor_identity": _actor_identity(plan),
            "process_identity": process_identity,
        },
        "started_at": started_at,
        "finished_at": now(),
        "task": {field: plan["task"][field] for field in ("task_id", "task_version", "change_version")},
        "artifact_manifest": None,
        "completeness": {"required_fields_checked": False, "kind_specific_fields_checked": False, "status": "FAIL"},
        "canonical_rerun": _canonical_rerun(plan),
        "result": "FAIL",
        "reasons": [code],
    }
    store.publish_receipt(receipt)
    return receipt


def run_gate(
    repo_root: str | os.PathLike[str], *, mode: str, receipt_kind: str,
    evidence_packet: str, issuer_packet: str,
    compiler: Callable[..., dict[str, Any]] = compile_plan,
    executor: Callable[..., dict[str, Any]] = execute_checks,
    store_factory: Callable[..., ImmutableReceiptStore] = ImmutableReceiptStore,
    now: Callable[[], str] = _utc_now,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    stderr: TextIO = sys.stderr,
) -> dict[str, Any]:
    compiled = compile_from_context(
        repo_root, mode=mode, receipt_kind=receipt_kind,
        evidence_packet=evidence_packet, issuer_packet=issuer_packet, compiler=compiler,
    )
    plan = compiled["plan"]
    canonical_plan = compiled["canonical_bytes"]
    if canonical_plan != canonical_json_bytes(plan):
        raise GateCliError("invalid-plan", "compiler returned non-canonical plan bytes")
    projection = {key: value for key, value in plan.items() if key != "content_fingerprint"}
    if (
        plan.get("receipt_kind") != receipt_kind
        or compiled.get("content_fingerprint") != plan.get("content_fingerprint")
        or sha256_bytes(canonical_json_bytes(projection)) != plan.get("content_fingerprint")
    ):
        raise GateCliError("invalid-plan", "compiler returned an invalid content fingerprint or kind")
    _validate_plan_execution_layer(plan, receipt_kind)
    _revalidate_plan_bindings(repo_root, plan)
    run_id = str(uuid_factory())
    store = store_factory(repo_root, run_id)
    store.create()
    plan_artifact = store.publish_plan(canonical_plan)
    executable_locator = "scripts/gates/cli.py"
    executable = read_bound_bytes(repo_root, executable_locator)
    process_instance_id = str(uuid_factory())
    subject_identity = plan.get("subject", {}).get("identity", {})
    issuer_identity = plan["issuer_packet"].get("packet", {}).get("issuer_instance_id")
    if run_id in {subject_identity.get("run_id"), issuer_identity}:
        raise GateCliError("identity-collision", "Gate run identity reuses subject or issuer identity")
    if process_instance_id in {run_id, subject_identity.get("run_id"), issuer_identity}:
        raise GateCliError("identity-collision", "Gate process identity is not unique")
    process_identity = {
        "process_instance_id": process_instance_id,
        "gate_run_id": run_id,
        "executable_locator": executable_locator,
        "executable_sha256": sha256_bytes(executable),
        "issuer_packet_sha256": plan["issuer_packet"]["sha256"],
    }
    started_at = now()
    start = {
        "schema_version": START_SCHEMA,
        "run_id": run_id,
        "receipt_kind": receipt_kind,
        "content_fingerprint": plan["content_fingerprint"],
        "plan": {"locator": plan_artifact.locator, "sha256": plan_artifact.sha256},
        "trusted_issuer_packet": {
            "locator": plan["issuer_packet"]["locator"], "sha256": plan["issuer_packet"]["sha256"],
        },
        "process_identity": process_identity,
        "started_at": started_at,
    }
    start_artifact = store.publish_start(start)
    caller_event = {
        "schema_version": CALLER_EVENT_SCHEMA,
        "event": "START",
        "run_id": run_id,
        "start_event": {"locator": start_artifact.locator, "sha256": start_artifact.sha256},
    }
    try:
        stderr.write(canonical_json_bytes(caller_event).decode("utf-8") + "\n")
        stderr.flush()
    except Exception as exc:
        return _publish_failed_receipt(
            store=store, plan=plan, run_id=run_id,
            plan_artifact={"locator": plan_artifact.locator, "sha256": plan_artifact.sha256},
            process_identity=process_identity, started_at=started_at, now=now,
            code="start-event-unavailable",
        )
    try:
        execution = (
            executor(plan, repo_root=os.fspath(repo_root))
            if receipt_kind == "TASK_VALIDATION"
            else _evidence_only_execution(plan, started_at=started_at, finished_at=now())
        )
        _revalidate_plan_bindings(repo_root, plan)
        handler = _installed_handler(receipt_kind)
        return handler(
            store=store, plan=plan, run_id=run_id,
            plan_artifact={"locator": plan_artifact.locator, "sha256": plan_artifact.sha256},
            process_identity=process_identity, execution=execution, now=now, started_at=started_at,
        )
    except Exception as exc:
        code = getattr(exc, "code", "gate-execution-failed")
        return _publish_failed_receipt(
            store=store, plan=plan, run_id=run_id,
            plan_artifact={"locator": plan_artifact.locator, "sha256": plan_artifact.sha256},
            process_identity=process_identity, started_at=started_at, now=now, code=code,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/gates/cli.py")
    parser.add_argument("--repo-root", default=None, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        current = sub.add_parser(name)
        current.add_argument("--mode", choices=("incremental", "full"), required=True)
        current.add_argument("--evidence-packet")
        current.add_argument("--issuer-packet")
        current.add_argument("--receipt-kind", choices=RECEIPT_KINDS, default="TASK_VALIDATION")
    status = sub.add_parser("status")
    status.add_argument("--run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[2]
    try:
        if args.command == "status":
            value = read_status(repo_root, args.run_id)
        else:
            evidence, issuer = resolve_context(
                evidence_packet=args.evidence_packet, issuer_packet=args.issuer_packet
            )
            if args.command == "plan":
                value = compile_from_context(
                    repo_root, mode=args.mode, receipt_kind=args.receipt_kind,
                    evidence_packet=evidence, issuer_packet=issuer,
                )["plan"]
            else:
                value = run_gate(
                    repo_root, mode=args.mode, receipt_kind=args.receipt_kind,
                    evidence_packet=evidence, issuer_packet=issuer,
                )
        sys.stdout.write(canonical_json_bytes(value).decode("utf-8") + "\n")
        sys.stdout.flush()
        if isinstance(value, dict) and value.get("result") == "BLOCKED":
            return 2
        return 0 if not isinstance(value, dict) or value.get("result") != "FAIL" else 1
    except (GateCliError, ReceiptStoreError, PlannerError) as exc:
        code = getattr(exc, "code", "gate-failure")
        detail = getattr(exc, "detail", str(exc))
        sys.stdout.write(canonical_json_bytes({"result": "FAIL", "reasons": [code], "detail": detail}).decode("utf-8") + "\n")
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
