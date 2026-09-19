"""Deterministically materialize and verify profile-backed Gate registry entries."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shlex
from pathlib import Path
from typing import Any

import yaml

from scripts.gates.task_contracts import PROFILE_LOCATOR, load_profiles

REGISTRY_SCHEMA = "lexiflow.gate-check-registry.v1"
REGISTRY_VERSION = 3
REGISTRY_EXECUTION = {
    "owner": "python-control-plane",
    "executor": "python3",
    "source_scan": "forbidden",
    "task_validation": "executes-selected-checks",
    "independent_review": "consumes-immutable-receipts",
    "catalog_decision": "consumes-immutable-receipts",
}
OUTCOME_SCHEMA = "lexiflow.check-outcome.v1"
CHECKER_LOCATOR = "scripts/gates/task_contracts.py"
PREDECLARED_SUBJECTS = frozenset({
    "LF-TSK-QLT-0002", "LF-TSK-QLT-0005", "LF-TSK-QLT-0007",
    "LF-TSK-QLT-0008", "LF-TSK-QLT-0009", "LF-TSK-QLT-0010",
    "LF-TSK-QLT-0011", "LF-TSK-QLT-0012", "LF-TSK-QLT-0013",
    "LF-TSK-QLT-0014",
})


class RegistryProfileError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _tasks(catalog: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for workstream in catalog.get("workstreams", []):
        owner = workstream.get("id")
        for epic in workstream.get("epics", []):
            for capability in epic.get("capabilities", []):
                for task in capability.get("seed_tasks", []):
                    result.append((owner, task))
    return result


def _entry_hash(entry: dict[str, Any]) -> str:
    projected = {key: value for key, value in entry.items() if key != "entry_hash"}
    return hashlib.sha256(canonical_json_bytes(projected)).hexdigest()


def build_profile_entry(task: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    task_id = task["id"]
    domain, number = task_id.split("-")[2:]
    if profile["runner"] == "task-contract":
        command = f"python3 -m scripts.gates.task_contracts --task-id {task_id}"
        argv = ["python3", "-m", "scripts.gates.task_contracts", "--task-id", task_id]
        check_id = f"task-contract.{domain.lower()}.{number}"
        command_id = f"task-contract.{domain.lower()}.{number}.v1"
        consumed = [PROFILE_LOCATOR, CHECKER_LOCATOR, *profile["required_inputs"]]
    elif task_id == "LF-TSK-QLT-0006":
        command = profile["validation_command"]
        argv = ["python3", "-m", "unittest", "discover", "-s", "tests/harness", "-p", "test_qoder_runner.py"]
        check_id = "qlt.runner.validate"
        command_id = "qlt.runner.validate.v1"
        consumed = list(profile["required_inputs"])
    elif profile["runner"] == "external":
        command = profile["validation_command"]
        argv = profile.get("fixed_argv")
        if not isinstance(argv, list) or shlex.join(argv) != command:
            raise RegistryProfileError(f"external profile command/argv mismatch: {task_id}")
        check_id = f"external.{domain.lower()}.{number}"
        command_id = f"external.{domain.lower()}.{number}.v1"
        consumed = list(profile["required_inputs"])
    else:
        raise RegistryProfileError(f"unsupported profile runner: {task_id}")
    if task.get("validation_command") != command:
        raise RegistryProfileError(f"catalog command mismatch: {task_id}")
    criteria = task.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        raise RegistryProfileError(f"task has no acceptance criteria: {task_id}")
    triggers = []
    for locator in consumed:
        trigger = {"path": locator, "terminal": True}
        if trigger not in triggers:
            triggers.append(trigger)
    entry = {
        "check_id": check_id,
        "check_version": 1,
        "owner": "LF-WS-QLT",
        "modes": ["incremental", "full"],
        "triggers": triggers,
        "required": True,
        "declared_validation_command": command,
        "command_id": command_id,
        "fixed_argv": argv,
        "cwd": ".",
        "timeout_seconds": profile.get("timeout_seconds", 240 if task_id == "LF-TSK-QLT-0006" else 180),
        "consumed_inputs": consumed,
        "outcome_contract": {
            "schema": OUTCOME_SCHEMA,
            "required_fields": ["exit_code", "stdout_locator", "stderr_locator", "typed_result"],
        },
        "acceptance_criterion_ids": [
            f"{task_id}.acceptance_criteria[{index}]" for index in range(len(criteria))
        ],
        "effect_check_ids": ["behavior", "regression"],
        "subject_task_id": task_id,
        "subject_task_version": task["task_version"],
        "subject_change_version": task["change_version"],
    }
    entry["entry_hash"] = _entry_hash(entry)
    return entry


def render_registry(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root)
    catalog = yaml.safe_load((root / "planning/workstreams.yaml").read_text(encoding="utf-8"))
    current = yaml.safe_load((root / "harness/gate-check-registry.yaml").read_text(encoding="utf-8"))
    profiles = load_profiles(root)
    if not isinstance(current, dict) or current.get("schema_version") != REGISTRY_SCHEMA:
        raise RegistryProfileError("current registry schema mismatch")
    if current.get("registry_version") != REGISTRY_VERSION:
        raise RegistryProfileError(
            f"current registry version must be {REGISTRY_VERSION}"
        )
    if current.get("execution") != REGISTRY_EXECUTION:
        raise RegistryProfileError("current registry execution contract mismatch")
    existing = {entry.get("subject_task_id"): entry for entry in current.get("entries", [])}
    if None in existing or len(existing) != len(current.get("entries", [])):
        raise RegistryProfileError("current registry subjects are invalid or duplicated")
    catalog_tasks = _tasks(catalog)
    task_map = {task["id"]: (owner, task) for owner, task in catalog_tasks}
    if set(profiles) - set(task_map):
        raise RegistryProfileError("profile contains an unknown task")
    entries: list[dict[str, Any]] = []
    activated_profiles: set[str] = set()
    for owner, task in catalog_tasks:
        task_id = task["id"]
        command = task.get("validation_command")
        if command is None and task_id == "LF-TSK-QLT-0002":
            command = "python3 -m scripts.gates.planning --root ."
        if command is None:
            continue
        profile = profiles.get(task_id)
        if task_id in PREDECLARED_SUBJECTS:
            if task_id not in existing:
                raise RegistryProfileError(f"predeclared entry missing: {task_id}")
            entry = copy.deepcopy(existing[task_id])
            if (
                entry.get("declared_validation_command") != command
                or entry.get("subject_task_version") != task.get("task_version")
                or entry.get("subject_change_version") != task.get("change_version")
            ):
                raise RegistryProfileError(f"predeclared entry drift: {task_id}")
            criteria = task.get("acceptance_criteria")
            if not isinstance(criteria, list) or not criteria:
                raise RegistryProfileError(f"predeclared task has no acceptance criteria: {task_id}")
            # Earlier registry entries pointed internal harness checks at
            # removed product-case IDs.  The catalog Task's own explicit
            # criteria are the stable acceptance contract for these checks.
            entry["acceptance_criterion_ids"] = [
                f"{task_id}.acceptance_criteria[{index}]" for index in range(len(criteria))
            ]
            entry["entry_hash"] = _entry_hash(entry)
            if profile is not None:
                if profile["runner"] != "external" or profile.get("validation_command") != command:
                    raise RegistryProfileError(f"predeclared profile mismatch: {task_id}")
                activated_profiles.add(task_id)
        else:
            if profile is None:
                raise RegistryProfileError(f"dispatchable task lacks profile or predeclaration: {task_id}")
            if profile["owner"] != owner:
                raise RegistryProfileError(f"profile owner mismatch: {task_id}")
            entry = build_profile_entry(task, profile)
            activated_profiles.add(task_id)
        entries.append(entry)
    if activated_profiles != set(profiles):
        raise RegistryProfileError(
            f"profile activation mismatch: missing={sorted(set(profiles) - activated_profiles)}"
        )
    return {
        "schema_version": REGISTRY_SCHEMA,
        "owner": "LF-WS-QLT",
        "registry_version": REGISTRY_VERSION,
        "execution": copy.deepcopy(REGISTRY_EXECUTION),
        "entries": entries,
    }


def check_registry(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root)
    rendered = render_registry(root)
    current = yaml.safe_load((root / "harness/gate-check-registry.yaml").read_text(encoding="utf-8"))
    if current != rendered:
        raise RegistryProfileError("registry differs from deterministic profile rendering")
    return {
        "schema_version": "lexiflow.registry-profile-check.v1",
        "status": "PASS",
        "entry_count": len(rendered["entries"]),
        "profile_count": len(load_profiles(root)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render or check the Gate registry profile projection")
    parser.add_argument("--root", default=".")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--render", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = check_registry(args.root) if args.check else render_registry(args.root)
    except (RegistryProfileError, OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}, sort_keys=True))
        return 1
    if args.render:
        print(yaml.safe_dump(result, sort_keys=False), end="")
    else:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
