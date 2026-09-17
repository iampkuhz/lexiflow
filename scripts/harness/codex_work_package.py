"""Canonical immutable publisher for a runner-bound Codex work package.

The module does not invoke commands or inspect their output.  A caller owns the
stable contract, a runner supplies trusted runtime identity, and the main agent
supplies structured six-field outcomes.  Publication is deliberately a small
write-only boundary under ``tmp/quality/codex-work-packages/<run_id>``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

from scripts.gates.evidence_packet import (
    CODEX_MAIN_TASK_PROJECTION_SCHEMA_VERSION,
    CODEX_TASK_PROJECTION_SCHEMA_VERSION,
    EvidencePacketError,
    REQUIRED_RESULT_FIELDS,
    check_locator_safety_strict,
    dispatch_path_contains,
    _normalize_scope_string,
    sha256_file_strict,
    validate_all_result_fields,
    validate_codex_work_package_task_projection,
    validate_codex_main_task_projection,
    materialize as materialize_evidence_packet,
)

SCHEMA_VERSION = "lexiflow.codex-work-package-result.v1"
OUTCOME_SCHEMA_VERSION = "lexiflow.codex-work-package-task-outcome.v1"
COMPLETION_SCHEMA_VERSION = "lexiflow.codex-work-package-task-completion.v1"
SIGNAL_SCHEMA_VERSION = "lexiflow.codex-work-package-signal.v1"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_AGENT_ID = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}|/[A-Za-z0-9_][A-Za-z0-9._/-]{0,255})$")
_IDENTITY_FIELDS = ("parent_session_id", "agent_id", "run_id", "session_id", "client", "parent_client")


class CodexWorkPackageError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _descriptor(locator: str, content: bytes) -> dict[str, Any]:
    return {"locator": locator, "sha256": sha256_bytes(content), "bytes": len(content)}


def _safe_path(root: Path, locator: str, *, leaf_missing: bool = False) -> Path:
    try:
        check_locator_safety_strict(locator)
    except EvidencePacketError as exc:
        raise CodexWorkPackageError("unsafe-locator", str(exc)) from None
    current = root
    parts = locator.split("/")
    for index, part in enumerate(parts):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if leaf_missing and index == len(parts) - 1:
                return current
            raise CodexWorkPackageError("artifact-missing", f"missing path: {locator}") from None
        if stat.S_ISLNK(mode):
            raise CodexWorkPackageError("unsafe-locator", f"symlink path component: {locator}")
        if index != len(parts) - 1 and not stat.S_ISDIR(mode):
            raise CodexWorkPackageError("unsafe-locator", f"non-directory ancestor: {locator}")
    return current


def _mkdir(root: Path, locator: str) -> Path:
    current = root
    for part in locator.split("/"):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                mode = current.lstat().st_mode
            else:
                continue
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise CodexWorkPackageError("unsafe-locator", f"unsafe directory: {locator}")
    return current


def _write_exclusive(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise CodexWorkPackageError("run-collision", f"immutable path already exists: {path.name}") from None
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CodexWorkPackageError(code, f"cannot parse {path}: {exc}") from None
    if not isinstance(value, dict):
        raise CodexWorkPackageError(code, "JSON root must be an object")
    return value


def _catalog_tasks(repo_root: Path) -> dict[str, dict[str, Any]]:
    path = repo_root / "planning/workstreams.yaml"
    try:
        root = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise CodexWorkPackageError("catalog-unavailable", str(exc)) from None
    result: dict[str, dict[str, Any]] = {}
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            task_id = value.get("id")
            if isinstance(task_id, str) and task_id.startswith("LF-TSK-") and "task_version" in value:
                if task_id in result:
                    raise CodexWorkPackageError("catalog-invalid", f"duplicate task: {task_id}")
                result[task_id] = value
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(root)
    return result


def _identity(runtime: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(runtime, Mapping) or set(runtime) != set(_IDENTITY_FIELDS):
        raise CodexWorkPackageError("runtime-context-invalid", "trusted runtime context must contain only identity fields")
    result: dict[str, str] = {}
    for field in _IDENTITY_FIELDS:
        value = runtime[field]
        if not isinstance(value, str) or not value:
            raise CodexWorkPackageError("runtime-context-invalid", f"{field} is missing")
        result[field] = value
    if not _UUID.fullmatch(result["run_id"]) or not _UUID.fullmatch(result["session_id"]) or not _UUID.fullmatch(result["parent_session_id"]):
        raise CodexWorkPackageError("runtime-context-invalid", "runner session and run identities must be UUIDs")
    if not _AGENT_ID.fullmatch(result["agent_id"]) or result["client"] != "codex" or result["parent_client"] != "codex":
        raise CodexWorkPackageError("runtime-context-invalid", "runner identity is not a Codex identity")
    return result


def _caller(caller: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(caller, Mapping):
        raise CodexWorkPackageError("caller-contract-invalid", "caller contract must be an object")
    # parent_client is a stable caller-side routing constraint; all other fields
    # are runner-owned execution identity.
    forbidden = (set(_IDENTITY_FIELDS) - {"parent_client"}) & set(caller)
    if forbidden:
        raise CodexWorkPackageError("caller-contract-invalid", "caller owns no runner identity")
    # The existing projection validator performs the complete caller shape check.
    return dict(caller)


def _validate_current(caller: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> None:
    for task_id in caller.get("task_ids", []):
        current = tasks.get(task_id)
        if current is None:
            raise CodexWorkPackageError("catalog-drift", f"task missing: {task_id}")
        checks = {
            "task_version": caller["task_versions"].get(task_id),
            "change_version": caller["change_versions"].get(task_id),
            "owner": caller["primary_owner"],
            "deliverable": caller["expected_outputs_by_task"].get(task_id),
            "acceptance_criteria": caller["acceptance_by_task"].get(task_id, {}).get("acceptance_criteria"),
            "acceptance_evidence": caller["acceptance_by_task"].get(task_id, {}).get("acceptance_evidence"),
            "validation_command": caller["validation_commands"].get(task_id),
        }
        expected = {
            "task_version": current.get("task_version"), "change_version": current.get("change_version"),
            "owner": current.get("owner"), "deliverable": current.get("deliverable"),
            "acceptance_criteria": current.get("acceptance_criteria"), "acceptance_evidence": current.get("acceptance_evidence"),
            "validation_command": current.get("validation_command"),
        }
        if checks != expected:
            raise CodexWorkPackageError("catalog-drift", f"current task contract drifted: {task_id}")
        allowed = current.get("allowed_files")
        forbidden = current.get("forbidden_files")
        if not isinstance(allowed, list) or not isinstance(forbidden, list):
            raise CodexWorkPackageError("catalog-invalid", f"scope unavailable: {task_id}")
        # A package scope can be wider only when every task claimed file is inside it.
        package_allowed = _normalize_scope_string(caller["allowed_files"])
        package_forbidden = _normalize_scope_string(caller["forbidden_files"])
        if any(not any(dispatch_path_contains(container, item) for container in package_allowed) for item in allowed) or any(not any(dispatch_path_contains(container, item) for container in package_forbidden) for item in forbidden):
            raise CodexWorkPackageError("catalog-drift", f"scope drifted: {task_id}")


def _projection(caller: dict[str, Any], identity: dict[str, str], task_id: str) -> dict[str, Any]:
    item = caller["acceptance_by_task"][task_id]
    projection = {
        "schema_version": CODEX_TASK_PROJECTION_SCHEMA_VERSION, "work_package_id": caller["work_package_id"],
        "task_ids": list(caller["task_ids"]), "target_task_id": task_id, "task_id": task_id,
        "task_source": "planning/workstreams.yaml", "task_version": caller["task_versions"][task_id],
        "change_version": caller["change_versions"][task_id], "allowed_files": caller["allowed_files"],
        "forbidden_files": caller["forbidden_files"], "expected_output": caller["expected_outputs_by_task"][task_id],
        "acceptance_criteria": item["acceptance_criteria"], "acceptance_evidence": item["acceptance_evidence"],
        "validation_command": caller["validation_commands"][task_id], "caller_contract": caller, **identity,
    }
    try:
        validate_codex_work_package_task_projection(projection)
    except EvidencePacketError as exc:
        raise CodexWorkPackageError("projection-invalid", str(exc)) from None
    return projection


def _outcome(task_id: str, supplied: Any, repo_root: Path, command: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if not isinstance(supplied, Mapping) or set(supplied) != {"result_fields", "artifacts", "validation_summary"}:
        raise CodexWorkPackageError("outcome-invalid", f"{task_id} must have result_fields, artifacts and validation_summary")
    fields = supplied["result_fields"]
    if not isinstance(fields, Mapping) or set(fields) != set(REQUIRED_RESULT_FIELDS):
        raise CodexWorkPackageError("outcome-invalid", f"{task_id} six result fields are incomplete")
    artifacts = supplied["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise CodexWorkPackageError("outcome-invalid", f"{task_id} artifacts are empty")
    descriptors: list[dict[str, str]] = []
    for index, item in enumerate(artifacts):
        if not isinstance(item, Mapping) or set(item) != {"locator", "sha256"}:
            raise CodexWorkPackageError("outcome-invalid", f"{task_id} artifact {index} is invalid")
        locator, digest = item["locator"], item["sha256"]
        _safe_path(repo_root, locator)
        actual = sha256_file_strict(repo_root, locator)
        if digest != actual:
            raise CodexWorkPackageError("artifact-hash-mismatch", f"{task_id} artifact hash mismatch: {locator}")
        descriptors.append({"locator": locator, "sha256": digest})
    locators = {item["locator"] for item in descriptors}
    try:
        validate_all_result_fields(dict(fields), locators)
    except EvidencePacketError as exc:
        raise CodexWorkPackageError("outcome-invalid", str(exc)) from None
    summary = supplied["validation_summary"]
    if not isinstance(summary, Mapping) or summary.get("command") != command:
        raise CodexWorkPackageError("outcome-invalid", f"{task_id} validation_summary is invalid")
    exit_code = summary.get("exit_code")
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise CodexWorkPackageError("outcome-invalid", f"{task_id} exit_code must be an integer or null")
    if fields["status"] == "PASS" and (exit_code != 0 or fields["validation"]["status"] != "PASS" or any(value != "PASS" for value in fields["effect_checks"].values())):
        raise CodexWorkPackageError("outcome-invalid", f"{task_id} PASS requires completed validation")
    return {
        "schema_version": OUTCOME_SCHEMA_VERSION, "task_id": task_id,
        "status": fields["status"], "result_fields": dict(fields),
        "artifacts": descriptors, "validation_summary": dict(supplied["validation_summary"]),
    }, descriptors


def _aggregate(statuses: list[str]) -> str:
    return "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS"


def _read_descriptor(repo_root: Path, descriptor: Any, expected_locator: str) -> dict[str, Any]:
    if not isinstance(descriptor, dict) or set(descriptor) != {"locator", "sha256", "bytes"} or descriptor["locator"] != expected_locator:
        raise CodexWorkPackageError("completion-invalid", "artifact must use its fixed canonical locator")
    path = _safe_path(repo_root, expected_locator)
    content = path.read_bytes()
    if descriptor != _descriptor(expected_locator, content):
        raise CodexWorkPackageError("artifact-hash-mismatch", expected_locator)
    return _read_json(path, "completion-invalid")


def _descriptor_from_input(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"locator", "sha256"}:
        raise CodexWorkPackageError("evidence-input-invalid", f"{label} must be a locator/hash descriptor")
    locator, digest = value["locator"], value["sha256"]
    if not isinstance(locator, str) or not isinstance(digest, str):
        raise CodexWorkPackageError("evidence-input-invalid", f"{label} descriptor values are invalid")
    return {"locator": locator, "sha256": digest}


def _runtime_binding_matches(root: Path, run_root_locator: str, identity: Mapping[str, str]) -> None:
    """Validate a pre-persisted host binding when a trusted runner supplied one."""
    locator = f"{run_root_locator}/runtime-binding.json"
    path = root / locator
    if not path.exists():
        return
    binding = _read_json(_safe_path(root, locator), "runtime-binding-invalid")
    expected = {"schema_version": "lexiflow.codex-runtime-binding.v1", "identity": dict(identity)}
    if binding != expected:
        raise CodexWorkPackageError("runtime-binding-invalid", "persisted runtime binding differs from publisher identity")


class CodexWorkPackagePublisher:
    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).resolve()

    def publish(self, caller_contract: Mapping[str, Any], trusted_runtime_context: Mapping[str, Any], outcomes: Mapping[str, Any]) -> dict[str, Any]:
        caller = _caller(caller_contract)
        identity = _identity(trusted_runtime_context)
        task_ids = caller.get("task_ids")
        if not isinstance(task_ids, list) or len(task_ids) < 2 or set(outcomes) != set(task_ids):
            raise CodexWorkPackageError("outcome-invalid", "outcomes must exactly cover caller task_ids")
        # Build once before any writes; this validates the complete contract through the existing projection validator.
        projections = {task_id: _projection(caller, identity, task_id) for task_id in task_ids}
        _validate_current(caller, _catalog_tasks(self.repo_root))
        prepared_outcomes = {
            task_id: _outcome(task_id, outcomes[task_id], self.repo_root, caller["validation_commands"][task_id])
            for task_id in task_ids
        }
        run_root_locator = f"tmp/quality/codex-work-packages/{identity['run_id']}"
        run_root = self.repo_root / run_root_locator
        _mkdir(self.repo_root, "tmp/quality/codex-work-packages")
        if run_root.exists() or run_root.is_symlink():
            # Runner-owned command captures may be prepared first, but a publisher
            # never resumes a partially published task layout.
            permitted_runner_entries = {"validation", "cache", "tmp", "runtime-binding.json"}
            if (
                run_root.is_symlink() or not run_root.is_dir()
                or not set(item.name for item in run_root.iterdir()).issubset(permitted_runner_entries)
            ):
                raise CodexWorkPackageError("run-collision", "run directory already contains publication state")
            _safe_path(self.repo_root, f"{run_root_locator}/validation")
        else:
            try:
                run_root.mkdir(mode=0o700)
            except FileExistsError:
                raise CodexWorkPackageError("run-collision", "run directory already exists") from None
        _runtime_binding_matches(self.repo_root, run_root_locator, identity)
        package_tasks: dict[str, Any] = {}
        statuses: list[str] = []
        try:
            for task_id in task_ids:
                task_dir_locator = f"{run_root_locator}/tasks/{task_id}"
                _mkdir(self.repo_root, task_dir_locator)
                task_dir = self.repo_root / task_dir_locator
                projection_bytes = canonical_json_bytes(projections[task_id])
                projection_locator = f"{task_dir_locator}/task-projection.json"
                _write_exclusive(task_dir / "task-projection.json", projection_bytes)
                outcome, external = prepared_outcomes[task_id]
                outcome_bytes = canonical_json_bytes(outcome)
                outcome_locator = f"{task_dir_locator}/outcome.json"
                _write_exclusive(task_dir / "outcome.json", outcome_bytes)
                completion = {"schema_version": COMPLETION_SCHEMA_VERSION, "work_package_id": caller["work_package_id"], "task_id": task_id, "task_version": caller["task_versions"][task_id], "change_version": caller["change_versions"][task_id], "identity": identity, "status": outcome["status"], "artifacts": {"task_projection": _descriptor(projection_locator, projection_bytes), "outcome": _descriptor(outcome_locator, outcome_bytes), "external": external}, "validation_summary": outcome["validation_summary"]}
                completion_bytes = canonical_json_bytes(completion)
                completion_locator = f"{task_dir_locator}/completion.json"
                _write_exclusive(task_dir / "completion.json", completion_bytes)
                signal = {"schema_version": SIGNAL_SCHEMA_VERSION, "status": outcome["status"], "work_package_id": caller["work_package_id"], "task_ids": task_ids, "run_id": identity["run_id"], "artifact_locators": {"completion": _descriptor(completion_locator, completion_bytes), "outcome": _descriptor(outcome_locator, outcome_bytes)}, "validation_commands": [caller["validation_commands"][task_id]], "blocking_findings": []}
                signal_bytes = canonical_json_bytes(signal)
                signal_locator = f"{task_dir_locator}/signal.json"
                _write_exclusive(task_dir / "signal.json", signal_bytes)
                package_tasks[task_id] = {"status": outcome["status"], "artifacts": {"task_projection": _descriptor(projection_locator, projection_bytes), "outcome": _descriptor(outcome_locator, outcome_bytes), "completion": _descriptor(completion_locator, completion_bytes), "signal": _descriptor(signal_locator, signal_bytes)}}
                statuses.append(outcome["status"])
            completion = {"schema_version": SCHEMA_VERSION, "work_package_id": caller["work_package_id"], "task_ids": task_ids, "task_versions": caller["task_versions"], "change_versions": caller["change_versions"], "identity": identity, "status": _aggregate(statuses), "tasks": package_tasks, "validation_summary": {task_id: outcomes[task_id]["validation_summary"] for task_id in task_ids}}
            completion_bytes = canonical_json_bytes(completion)
            _write_exclusive(run_root / "package-completion.json", completion_bytes)
            return completion
        except Exception:
            # A partial immutable run is evidence of interruption; it is never reused or overwritten.
            raise

    def verify(self, run_id: str) -> dict[str, Any]:
        if not isinstance(run_id, str) or not _UUID.fullmatch(run_id):
            raise CodexWorkPackageError("runtime-context-invalid", "run_id must be a UUID")
        locator = f"tmp/quality/codex-work-packages/{run_id}/package-completion.json"
        path = _safe_path(self.repo_root, locator)
        completion = _read_json(path, "completion-invalid")
        if set(completion) != {"schema_version", "work_package_id", "task_ids", "task_versions", "change_versions", "identity", "status", "tasks", "validation_summary"}:
            raise CodexWorkPackageError("completion-invalid", "completion fields are not exact")
        identity = _identity(completion["identity"])
        if completion["schema_version"] != SCHEMA_VERSION or identity["run_id"] != run_id:
            raise CodexWorkPackageError("completion-invalid", "completion identity is invalid")
        tasks = completion.get("tasks")
        task_ids = completion.get("task_ids")
        if not isinstance(tasks, dict) or not isinstance(task_ids, list) or len(task_ids) < 2 or any(not isinstance(task, str) or not _ID.fullmatch(task) for task in task_ids) or len(set(task_ids)) != len(task_ids) or set(tasks) != set(task_ids):
            raise CodexWorkPackageError("completion-invalid", "task map must cover the ordered unique Task list exactly")
        catalog = _catalog_tasks(self.repo_root)
        caller = None
        summaries: dict[str, Any] = {}
        statuses: list[str] = []
        for task_id in task_ids:
            entry = tasks[task_id]
            if not isinstance(entry, dict) or set(entry) != {"status", "artifacts"} or entry["status"] not in {"PASS", "BLOCKED", "FAIL"}:
                raise CodexWorkPackageError("completion-invalid", f"invalid task status: {task_id}")
            artifacts = entry["artifacts"]
            if not isinstance(artifacts, dict) or set(artifacts) != {"task_projection", "outcome", "completion", "signal"}:
                raise CodexWorkPackageError("completion-invalid", f"missing canonical artifacts: {task_id}")
            task_locator = f"tmp/quality/codex-work-packages/{run_id}/tasks/{task_id}"
            projection = _read_descriptor(self.repo_root, artifacts["task_projection"], f"{task_locator}/task-projection.json")
            projected_caller = _caller(projection.get("caller_contract"))
            expected_projection = _projection(projected_caller, identity, task_id)
            if projection != expected_projection or (caller is not None and caller != projected_caller):
                raise CodexWorkPackageError("completion-invalid", "per-Task projection binding drifted")
            caller = projected_caller
            _validate_current(caller, catalog)
            if any(completion[field] != caller[field] for field in ("work_package_id", "task_ids", "task_versions", "change_versions")):
                raise CodexWorkPackageError("completion-invalid", "package contract differs from per-Task projection")
            outcome = _read_descriptor(self.repo_root, artifacts["outcome"], f"{task_locator}/outcome.json")
            supplied = {key: outcome.get(key) for key in ("result_fields", "artifacts", "validation_summary")}
            checked_outcome, external = _outcome(task_id, supplied, self.repo_root, caller["validation_commands"][task_id])
            if outcome != checked_outcome or entry["status"] != outcome["status"]:
                raise CodexWorkPackageError("completion-invalid", "outcome fields or status drifted")
            task_completion = _read_descriptor(self.repo_root, artifacts["completion"], f"{task_locator}/completion.json")
            expected_completion = {
                "schema_version": COMPLETION_SCHEMA_VERSION, "work_package_id": caller["work_package_id"],
                "task_id": task_id, "task_version": caller["task_versions"][task_id],
                "change_version": caller["change_versions"][task_id], "identity": identity,
                "status": outcome["status"], "validation_summary": outcome["validation_summary"],
                "artifacts": {"task_projection": artifacts["task_projection"], "outcome": artifacts["outcome"], "external": external},
            }
            if task_completion != expected_completion:
                raise CodexWorkPackageError("completion-invalid", "task completion binding drifted")
            signal = _read_descriptor(self.repo_root, artifacts["signal"], f"{task_locator}/signal.json")
            expected_signal = {
                "schema_version": SIGNAL_SCHEMA_VERSION, "status": outcome["status"],
                "work_package_id": caller["work_package_id"], "task_ids": task_ids, "run_id": run_id,
                "artifact_locators": {"completion": artifacts["completion"], "outcome": artifacts["outcome"]},
                "validation_commands": [caller["validation_commands"][task_id]], "blocking_findings": [],
            }
            if signal != expected_signal:
                raise CodexWorkPackageError("completion-invalid", "task signal binding drifted")
            summaries[task_id] = outcome["validation_summary"]
            statuses.append(entry["status"])
        if completion["validation_summary"] != summaries or completion["status"] != _aggregate(statuses):
            raise CodexWorkPackageError("completion-invalid", "aggregate status is invalid")
        return {"status": "PASS", "work_package_id": completion["work_package_id"], "task_ids": task_ids, "run_id": run_id, "artifact_locators": {"package_completion": {"locator": locator, "sha256": sha256_file_strict(self.repo_root, locator)}}, "validation_commands": [], "blocking_findings": []}

    def materialize_gate_evidence(
        self,
        run_id: str,
        task_id: str,
        *,
        main_agent_attestation: Mapping[str, Any],
        artifacts: Mapping[str, Any],
        scope: Mapping[str, Any],
        publication_id: str,
    ) -> dict[str, Any]:
        """Adapt one verified canonical task to ``evidence_packet.materialize``.

        The caller supplies only explicit Main attestation and reviewed artifact
        descriptors.  This method never runs commands, reads logs for meaning,
        scans temporary directories, or constructs a legacy receipt.
        """
        completion = self.verify(run_id)
        if task_id not in completion["task_ids"]:
            raise CodexWorkPackageError("task-not-in-package", f"unknown task: {task_id}")
        base = f"tmp/quality/codex-work-packages/{run_id}/tasks/{task_id}"
        package = _read_json(_safe_path(self.repo_root, f"tmp/quality/codex-work-packages/{run_id}/package-completion.json"), "completion-invalid")
        entry = package["tasks"][task_id]
        projection = _read_descriptor(self.repo_root, entry["artifacts"]["task_projection"], f"{base}/task-projection.json")
        outcome = _read_descriptor(self.repo_root, entry["artifacts"]["outcome"], f"{base}/outcome.json")
        task_completion = _read_descriptor(self.repo_root, entry["artifacts"]["completion"], f"{base}/completion.json")
        if not isinstance(main_agent_attestation, Mapping) or main_agent_attestation.get("result_fields") != outcome.get("result_fields"):
            raise CodexWorkPackageError("evidence-input-invalid", "Main attestation must exactly bind the canonical six result fields")
        if not isinstance(artifacts, Mapping) or set(artifacts) != {"stdout", "stderr", "changed_file_snapshot", "diff", "tests"}:
            raise CodexWorkPackageError("evidence-input-invalid", "reviewed artifact descriptors are incomplete")
        reviewed = {key: _descriptor_from_input(artifacts[key], key) for key in ("stdout", "stderr", "changed_file_snapshot", "diff")}
        if not isinstance(artifacts["tests"], list) or not artifacts["tests"]:
            raise CodexWorkPackageError("evidence-input-invalid", "tests must be a non-empty descriptor list")
        reviewed["tests"] = [_descriptor_from_input(value, f"tests[{index}]") for index, value in enumerate(artifacts["tests"])]
        external = outcome["artifacts"]
        permitted = {(item["locator"], item["sha256"]) for item in external}
        for value in [*reviewed.values()]:
            descriptors = value if isinstance(value, list) else [value]
            if any((item["locator"], item["sha256"]) not in permitted for item in descriptors):
                raise CodexWorkPackageError("evidence-input-invalid", "reviewed descriptor is not a canonical outcome artifact")
        if not isinstance(scope, Mapping) or set(scope) != {"changed_files", "raw_caller_strings", "normalized", "three_way_reconciliation"}:
            raise CodexWorkPackageError("evidence-input-invalid", "scope must be explicit and complete")
        source_locator = "planning/workstreams.yaml"
        evidence_input = {
            "task": {"task_id": task_id, "task_version": projection["task_version"], "change_version": projection["change_version"], "task_source": {"locator": source_locator, "sha256": sha256_file_strict(self.repo_root, source_locator)}},
            "subject": {"raw_artifacts": {"task": entry["artifacts"]["task_projection"], "completion": entry["artifacts"]["completion"], **reviewed}, "main_agent_attestation": dict(main_agent_attestation), "identity": projection_identity(projection)},
            "scope": dict(scope),
        }
        return materialize_evidence_packet(self.repo_root, evidence_input, publication_id)


def projection_identity(projection: Mapping[str, Any]) -> dict[str, Any]:
    return {field: projection[field] for field in _IDENTITY_FIELDS}


def build_codex_main_task_projection(
    repo_root: str | Path,
    task_id: str,
    trusted_runtime_context: Mapping[str, Any],
    *,
    goal: str,
    required_context: str,
    failure_policy: str,
) -> dict[str, Any]:
    """Build the one-Task Main route from the current catalog and host binding.

    The caller never supplies task version, scope, acceptance, or validation
    values.  Runtime provenance must have already been bound by the trusted
    host adapter; this function is intentionally separate from sub-agent
    package publication.
    """
    root = Path(repo_root).resolve()
    identity = _identity(trusted_runtime_context)
    record = _catalog_tasks(root).get(task_id)
    if record is None:
        raise CodexWorkPackageError("catalog-drift", f"task missing: {task_id}")
    required = ("task_version", "change_version", "allowed_files", "forbidden_files", "deliverable", "acceptance_criteria", "acceptance_evidence", "validation_command")
    if any(field not in record for field in required):
        raise CodexWorkPackageError("catalog-invalid", f"Main task contract is incomplete: {task_id}")
    projection = {
        "schema_version": CODEX_MAIN_TASK_PROJECTION_SCHEMA_VERSION,
        "task_id": task_id, "task_source": "planning/workstreams.yaml",
        "task_version": record["task_version"], "change_version": record["change_version"],
        "allowed_files": ", ".join(record["allowed_files"]), "forbidden_files": ", ".join(record["forbidden_files"]),
        "expected_output": record["deliverable"], "acceptance_criteria": record["acceptance_criteria"],
        "acceptance_evidence": record["acceptance_evidence"], "validation_command": record["validation_command"],
        "goal": goal, "required_context": required_context, "failure_policy": failure_policy,
        **identity,
    }
    try:
        validate_codex_main_task_projection(projection)
    except EvidencePacketError as exc:
        raise CodexWorkPackageError("projection-invalid", str(exc)) from None
    return projection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify canonical Codex work-package artifacts")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    try:
        result = CodexWorkPackagePublisher(args.root).verify(args.run_id)
    except (CodexWorkPackageError, EvidencePacketError, OSError) as exc:
        result = {"status": "FAIL", "work_package_id": None, "task_ids": [], "run_id": args.run_id, "artifact_locators": {}, "validation_commands": [], "blocking_findings": [getattr(exc, "code", "artifact-unavailable")]}
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
