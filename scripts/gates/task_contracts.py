"""Closed, current-input contract checks for the G1 receipt-activation tranche."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from scripts.gates.receipt_store import ReceiptStoreError, read_bound_bytes, safe_locator

SCHEMA_VERSION = "lexiflow.task-contract-check.v1"
PROFILE_SCHEMA = "lexiflow.g1-task-contract-profiles.v1"
PROFILE_LOCATOR = "harness/g1-task-contract-profiles.yaml"
_TASK_ID = re.compile(r"^LF-TSK-[A-Z]+-\d{4}$")
_ASSERTION_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class TaskContractError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _exact_object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise TaskContractError("invalid-profile", f"{path} keys differ from {sorted(keys)}")
    return value


def load_profiles(repo_root: str | Path) -> dict[str, dict[str, Any]]:
    try:
        content = read_bound_bytes(repo_root, PROFILE_LOCATOR)
        value = yaml.safe_load(content)
    except (ReceiptStoreError, OSError, UnicodeError, yaml.YAMLError) as exc:
        raise TaskContractError("invalid-profile", f"cannot load profile: {exc}") from None
    value = _exact_object(value, {"schema_version", "owner", "profile_version", "profiles"}, "profile")
    if value["schema_version"] != PROFILE_SCHEMA or value["owner"] != "LF-WS-QLT":
        raise TaskContractError("invalid-profile", "profile schema or owner mismatch")
    if isinstance(value["profile_version"], bool) or not isinstance(value["profile_version"], int) or value["profile_version"] <= 0:
        raise TaskContractError("invalid-profile", "profile_version must be positive")
    profiles = value["profiles"]
    if not isinstance(profiles, dict) or not profiles:
        raise TaskContractError("invalid-profile", "profiles must be a non-empty mapping")
    normalized: dict[str, dict[str, Any]] = {}
    for task_id, raw in profiles.items():
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise TaskContractError("invalid-profile", f"invalid task id: {task_id!r}")
        required = {"owner", "evidence_file", "required_inputs", "runner", "assertions"}
        optional = {"validation_command", "approval_gate"}
        if not isinstance(raw, dict) or not required.issubset(raw) or set(raw) - required - optional:
            raise TaskContractError("invalid-profile", f"{task_id} fields are invalid")
        owner = raw["owner"]
        domain = task_id.split("-")[2]
        if owner != f"LF-WS-{domain}":
            raise TaskContractError("invalid-profile", f"{task_id} owner mismatch")
        expected_evidence = f"tmp/quality/task-evidence/{domain}/{task_id}/result.json"
        try:
            evidence_file = safe_locator(raw["evidence_file"])
        except ReceiptStoreError as exc:
            raise TaskContractError("invalid-profile", f"{task_id} evidence_file: {exc}") from None
        if evidence_file != expected_evidence:
            raise TaskContractError("invalid-profile", f"{task_id} evidence_file mismatch")
        runner = raw["runner"]
        if runner not in {"task-contract", "external"}:
            raise TaskContractError("invalid-profile", f"{task_id} runner invalid")
        if runner == "external":
            command = raw.get("validation_command")
            if not isinstance(command, str) or not command:
                raise TaskContractError("invalid-profile", f"{task_id} external command missing")
        elif "validation_command" in raw:
            raise TaskContractError("invalid-profile", f"{task_id} task-contract runner cannot override command")
        locators = raw["required_inputs"]
        if (
            not isinstance(locators, list) or not locators
            or any(not isinstance(item, str) or not item for item in locators)
            or len(locators) != len(set(locators))
        ):
            raise TaskContractError("invalid-profile", f"{task_id} required_inputs invalid")
        try:
            for locator in locators:
                safe_locator(locator)
        except ReceiptStoreError as exc:
            raise TaskContractError("invalid-profile", f"{task_id} required_inputs: {exc}") from None
        assertions = raw["assertions"]
        if not isinstance(assertions, list) or not assertions:
            raise TaskContractError("invalid-profile", f"{task_id} assertions missing")
        seen: set[str] = set()
        for index, assertion in enumerate(assertions):
            assertion = _exact_object(assertion, {"id", "contains_all"}, f"{task_id}.assertions[{index}]")
            assertion_id = assertion["id"]
            terms = assertion["contains_all"]
            if not isinstance(assertion_id, str) or not _ASSERTION_ID.fullmatch(assertion_id) or assertion_id in seen:
                raise TaskContractError("invalid-profile", f"{task_id} assertion id invalid")
            if (
                not isinstance(terms, list) or not terms
                or any(not isinstance(term, str) or not term.strip() for term in terms)
                or len({term.casefold() for term in terms}) != len(terms)
            ):
                raise TaskContractError("invalid-profile", f"{task_id} assertion terms invalid")
            seen.add(assertion_id)
        gate = raw.get("approval_gate")
        if gate is not None:
            gate = _exact_object(gate, {"locator", "approved_marker", "assertion_id"}, f"{task_id}.approval_gate")
            if task_id != "LF-TSK-ARCH-0008" or gate["locator"] not in locators:
                raise TaskContractError("invalid-profile", "approval gate must bind the G1 exit task input")
            if not all(isinstance(gate[field], str) and gate[field] for field in gate):
                raise TaskContractError("invalid-profile", "approval gate fields invalid")
            if gate["assertion_id"] in seen or not _ASSERTION_ID.fullmatch(gate["assertion_id"]):
                raise TaskContractError("invalid-profile", "approval gate assertion id invalid")
        elif task_id == "LF-TSK-ARCH-0008":
            raise TaskContractError("invalid-profile", "G1 exit task requires an approval gate")
        normalized[task_id] = raw
    return normalized


def evaluate_task(repo_root: str | Path, task_id: str) -> dict[str, Any]:
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise TaskContractError("invalid-task-id", "task id is malformed")
    profiles = load_profiles(repo_root)
    profile = profiles.get(task_id)
    if profile is None:
        raise TaskContractError("unknown-task", f"task is not in the closed profile: {task_id}")
    if profile["runner"] != "task-contract":
        raise TaskContractError("external-runner-required", f"{task_id} uses its registered external validator")

    texts: list[str] = []
    inputs: list[dict[str, Any]] = []
    for locator in profile["required_inputs"]:
        try:
            content = read_bound_bytes(repo_root, locator)
            text = content.decode("utf-8", errors="strict")
        except (ReceiptStoreError, UnicodeError) as exc:
            raise TaskContractError("invalid-current-input", f"{locator}: {exc}") from None
        if not text.strip():
            raise TaskContractError("invalid-current-input", f"{locator} is empty")
        texts.append(text.casefold())
        inputs.append({
            "locator": locator,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        })
    corpus = "\n".join(texts)
    assertions: list[dict[str, Any]] = []
    for assertion in profile["assertions"]:
        missing = [term for term in assertion["contains_all"] if term.casefold() not in corpus]
        assertions.append({
            "id": assertion["id"],
            "status": "PASS" if not missing else "BLOCKED",
            "required_terms": list(assertion["contains_all"]),
            "missing_terms": missing,
        })
    gate = profile.get("approval_gate")
    if gate is not None:
        gate_content = texts[profile["required_inputs"].index(gate["locator"])]
        approved = gate["approved_marker"].casefold() in gate_content
        assertions.append({
            "id": gate["assertion_id"],
            "status": "PASS" if approved else "BLOCKED",
            "required_terms": [gate["approved_marker"]],
            "missing_terms": [] if approved else [gate["approved_marker"]],
        })
    status = "PASS" if all(item["status"] == "PASS" for item in assertions) else "BLOCKED"
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "status": status,
        "assertions": assertions,
        "inputs": inputs,
        "profile": {
            "locator": PROFILE_LOCATOR,
            "sha256": hashlib.sha256(read_bound_bytes(repo_root, PROFILE_LOCATOR)).hexdigest(),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate one closed G1 task contract")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)
    try:
        result = evaluate_task(args.repo_root, args.task_id)
    except TaskContractError as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "task_id": args.task_id,
            "status": "FAIL",
            "reason": exc.code,
            "assertions": [],
            "inputs": [],
        }
    print(canonical_json_bytes(result).decode("utf-8"))
    return {"PASS": 0, "BLOCKED": 1, "FAIL": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
