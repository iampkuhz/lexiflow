"""Codex Work Package 的规范不可变发布器。完整 caller 只冻结一次，Task 引用父记录；唯一 completion 最后发布，部分发布不得复用。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from scripts.agents.work_packages.work_package_contract_checks import (
    CODEX_TASK_PROJECTION_SCHEMA_VERSION,
    REQUIRED_RESULT_FIELDS,
    AgentContractError,
    check_locator_safety_strict,
    dispatch_paths_intersect,
    normalize_scope_string,
    match_dispatch_path_v1,
    validate_dispatch_path_v1,
    sha256_file_strict,
    validate_all_result_fields,
    validate_codex_work_package_task_projection,
)

SCHEMA_VERSION = "lexiflow.codex-work-package-result.v2"
OUTCOME_SCHEMA_VERSION = "lexiflow.codex-work-package-task-outcome.v1"
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_AGENT_ID = re.compile(
    r"^(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}|/[A-Za-z0-9_][A-Za-z0-9._/-]{0,255})$"
)
_IDENTITY_FIELDS = (
    "parent_session_id",
    "agent_id",
    "run_id",
    "session_id",
    "client",
    "parent_client",
)


class CodexWorkPackageError(ValueError):
    """工作包发布或核对失败；代码用于区分合同与存储边界。"""

    def __init__(self, code: str, detail: str, *, status: str = "FAIL") -> None:
        self.code = code
        self.status = status
        super().__init__(f"{code}: {detail}")


def canonical_json_bytes(value: Any) -> bytes:
    """以固定 JSON 编码产生可哈希的记录字节。"""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    """计算原始记录字节的 SHA-256，供不可变证据绑定。"""
    return hashlib.sha256(value).hexdigest()


def _descriptor(locator: str, content: bytes) -> dict[str, Any]:
    return {"locator": locator, "sha256": sha256_bytes(content), "bytes": len(content)}


def _safe_path(root: Path, locator: str) -> Path:
    try:
        check_locator_safety_strict(locator)
    except AgentContractError as exc:
        raise CodexWorkPackageError("unsafe-locator", str(exc)) from None
    current = root
    parts = locator.split("/")
    for index, part in enumerate(parts):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            raise CodexWorkPackageError(
                "artifact-missing", f"missing path: {locator}"
            ) from None
        if stat.S_ISLNK(mode):
            raise CodexWorkPackageError(
                "unsafe-locator", f"symlink path component: {locator}"
            )
        if index != len(parts) - 1 and not stat.S_ISDIR(mode):
            raise CodexWorkPackageError(
                "unsafe-locator", f"non-directory ancestor: {locator}"
            )
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
            raise CodexWorkPackageError(
                "unsafe-locator", f"unsafe directory: {locator}"
            )
    return current


def _write_exclusive(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise CodexWorkPackageError(
            "run-collision", f"immutable path already exists: {path.name}"
        ) from None
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


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise CodexWorkPackageError("duplicate-json-key", key)
        value[key] = item
    return value


def _read_json(path: Path, code: str, content: bytes | None = None) -> dict[str, Any]:
    try:
        value = json.loads(
            content if content is not None else path.read_bytes(),
            object_pairs_hook=_unique_keys,
        )
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

    def visit(value: Any, owner: str | None = None) -> None:
        if isinstance(value, dict):
            task_id = value.get("id")
            if isinstance(task_id, str) and task_id.startswith("LF-WS-"):
                owner = task_id
            if (
                isinstance(task_id, str)
                and task_id.startswith("LF-TSK-")
                and "task_version" in value
            ):
                if task_id in result:
                    raise CodexWorkPackageError(
                        "catalog-invalid", f"duplicate task: {task_id}"
                    )
                result[task_id] = {"owner": owner, **value}
            for child in value.values():
                visit(child, owner)
        elif isinstance(value, list):
            for child in value:
                visit(child, owner)

    visit(root)
    return result


def _identity(runtime: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(runtime, Mapping) or set(runtime) != set(_IDENTITY_FIELDS):
        raise CodexWorkPackageError(
            "runtime-context-invalid",
            "trusted runtime context must contain only identity fields",
        )
    result: dict[str, str] = {}
    for field in _IDENTITY_FIELDS:
        value = runtime[field]
        if not isinstance(value, str) or not value:
            raise CodexWorkPackageError(
                "runtime-context-invalid", f"{field} is missing"
            )
        result[field] = value
    if (
        not _UUID.fullmatch(result["run_id"])
        or not _UUID.fullmatch(result["session_id"])
        or not _UUID.fullmatch(result["parent_session_id"])
    ):
        raise CodexWorkPackageError(
            "runtime-context-invalid", "runner session and run identities must be UUIDs"
        )
    if (
        not _AGENT_ID.fullmatch(result["agent_id"])
        or result["client"] != "codex"
        or result["parent_client"] != "codex"
    ):
        raise CodexWorkPackageError(
            "runtime-context-invalid", "runner identity is not a Codex identity"
        )
    return result


_CALLER_FIELDS = {
    "goal",
    "work_package_id",
    "task_ids",
    "task_versions",
    "change_versions",
    "contract_boundary",
    "additional_forbidden_files",
    "required_context",
    "failure_policy",
}
_FROZEN_FIELDS = _CALLER_FIELDS | {
    "task_source",
    "estimated_minutes",
    "primary_owner",
    "allowed_files",
    "forbidden_files",
    "scopes_by_task",
    "expected_outputs_by_task",
    "acceptance_by_task",
    "validation_commands",
    "parent_client",
}


def _scope_list(value: Any) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) for item in value)
        or len(set(value)) != len(value)
    ):
        raise CodexWorkPackageError(
            "caller-contract-invalid", "scope must be a unique list"
        )
    for item in value:
        validate_dispatch_path_v1(item)
    return sorted(value)


def _caller(caller: Any, *, frozen: bool = False) -> dict[str, Any]:
    """校验精确输入或完整冻结事实；绝不从当前 Catalog 补齐缺失证据。"""
    if not isinstance(caller, Mapping) or set(caller) != (
        _FROZEN_FIELDS if frozen else _CALLER_FIELDS
    ):
        raise CodexWorkPackageError(
            "caller-contract-invalid",
            "caller contract fields are not exact; caller owns no runner identity",
        )
    ids = caller["task_ids"]
    if (
        not isinstance(ids, list)
        or not ids
        or any(not isinstance(task, str) or not _ID.fullmatch(task) for task in ids)
        or len(set(ids)) != len(ids)
    ):
        raise CodexWorkPackageError("caller-contract-invalid", "task_ids are invalid")
    for field in (
        "goal",
        "work_package_id",
        "contract_boundary",
        "required_context",
        "failure_policy",
    ):
        if not isinstance(caller[field], str) or not caller[field].strip():
            raise CodexWorkPackageError("caller-contract-invalid", f"{field} is empty")
    if not _ID.fullmatch(caller["work_package_id"]):
        raise CodexWorkPackageError(
            "caller-contract-invalid", "work_package_id is invalid"
        )
    maps = ["task_versions", "change_versions"]
    if frozen:
        maps += [
            "expected_outputs_by_task",
            "acceptance_by_task",
            "validation_commands",
            "scopes_by_task",
        ]
    if any(
        not isinstance(caller[field], Mapping) or set(caller[field]) != set(ids)
        for field in maps
    ):
        raise CodexWorkPackageError(
            "caller-contract-invalid", "Task maps are not exact"
        )
    for task in ids:
        if (
            type(caller["task_versions"][task]) is not int
            or caller["task_versions"][task] < 1
            or not isinstance(caller["change_versions"][task], str)
            or not re.fullmatch(
                r"[0-9]+\.[0-9]+\.[0-9]+", caller["change_versions"][task]
            )
        ):
            raise CodexWorkPackageError(
                "caller-contract-invalid", "version pins are invalid"
            )
    _scope_list(caller["additional_forbidden_files"])
    if frozen:
        if (
            caller["task_source"] != "planning/workstreams.yaml"
            or caller["parent_client"] != "codex"
            or not isinstance(caller["primary_owner"], str)
            or not caller["primary_owner"]
            or type(caller["estimated_minutes"]) is not int
            or not 10 <= caller["estimated_minutes"] <= 360
        ):
            raise CodexWorkPackageError(
                "caller-contract-invalid", "frozen source, owner or budget is invalid"
            )
        normalize_scope_string(caller["allowed_files"])
        normalize_scope_string(caller["forbidden_files"])
        for task in ids:
            acceptance = caller["acceptance_by_task"][task]
            scope = caller["scopes_by_task"][task]
            if (
                not isinstance(acceptance, Mapping)
                or set(acceptance) != {"acceptance_criteria", "acceptance_evidence"}
                or any(
                    not isinstance(value, list)
                    or not value
                    or any(not isinstance(x, str) or not x.strip() for x in value)
                    for value in acceptance.values()
                )
            ):
                raise CodexWorkPackageError(
                    "caller-contract-invalid", "frozen acceptance is incomplete"
                )
            if not isinstance(scope, Mapping) or set(scope) != {
                "allowed_files",
                "forbidden_files",
            }:
                raise CodexWorkPackageError(
                    "caller-contract-invalid", "frozen scope is incomplete"
                )
            for value in scope.values():
                _scope_list(value)
            for field in ("expected_outputs_by_task", "validation_commands"):
                value = caller[field][task]
                if not isinstance(value, str) or not value.strip():
                    raise CodexWorkPackageError(
                        "caller-contract-invalid", f"{field} is incomplete"
                    )
    return dict(caller)


def _freeze_caller(
    caller: dict[str, Any], tasks: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """核对派发版本 pins，从精确 Catalog 派生基础范围和预算；附加禁区只能收紧。"""
    records = {}
    required = {
        "owner",
        "deliverable",
        "acceptance_criteria",
        "acceptance_evidence",
        "validation_command",
        "task_version",
        "change_version",
        "estimated_minutes",
        "allowed_files",
        "forbidden_files",
    }
    for task in caller["task_ids"]:
        record = tasks.get(task)
        if record is None:
            raise CodexWorkPackageError("catalog-drift", f"task missing: {task}")
        if not required.issubset(record):
            raise CodexWorkPackageError("catalog-invalid", f"task incomplete: {task}")
        if (
            record["task_version"] != caller["task_versions"][task]
            or record["change_version"] != caller["change_versions"][task]
        ):
            raise CodexWorkPackageError("catalog-drift", f"version drifted: {task}")
        if (
            type(record["estimated_minutes"]) is not int
            or record["estimated_minutes"] < 1
        ):
            raise CodexWorkPackageError("catalog-invalid", "Task estimate is invalid")
        records[task] = record
    owners = {record["owner"] for record in records.values()}
    if len(owners) != 1 or not all(
        isinstance(owner, str) and owner for owner in owners
    ):
        raise CodexWorkPackageError(
            "catalog-drift", "work package must share primary_owner"
        )
    scopes = {
        task: {
            field: _scope_list(record[field])
            for field in ("allowed_files", "forbidden_files")
        }
        for task, record in records.items()
    }
    for task, scope in scopes.items():
        if any(
            dispatch_paths_intersect(allowed, forbidden)
            for other, other_scope in scopes.items()
            if other != task
            for allowed in scope["allowed_files"]
            for forbidden in other_scope["forbidden_files"]
        ):
            raise CodexWorkPackageError(
                "catalog-drift", "package Task write scopes conflict"
            )
    allowed = sorted(
        {path for scope in scopes.values() for path in scope["allowed_files"]}
    )
    forbidden = sorted(
        {path for scope in scopes.values() for path in scope["forbidden_files"]}
        | set(caller["additional_forbidden_files"])
    )
    frozen = {
        **caller,
        "task_source": "planning/workstreams.yaml",
        "primary_owner": next(iter(owners)),
        "estimated_minutes": sum(
            record["estimated_minutes"] for record in records.values()
        ),
        "allowed_files": ", ".join(allowed),
        "forbidden_files": ", ".join(forbidden),
        "scopes_by_task": scopes,
        "parent_client": "codex",
        "expected_outputs_by_task": {
            task: record["deliverable"] for task, record in records.items()
        },
        "acceptance_by_task": {
            task: {
                field: record[field]
                for field in ("acceptance_criteria", "acceptance_evidence")
            }
            for task, record in records.items()
        },
        "validation_commands": {
            task: record["validation_command"] for task, record in records.items()
        },
    }
    return _caller(frozen, frozen=True)


def _validate_current(caller: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> None:
    """单独对比 CURRENT Catalog，冻结证据缺字段时先失败，不借当前值补齐。"""
    frozen = _caller(caller, frozen=True)
    expected = _freeze_caller({key: frozen[key] for key in _CALLER_FIELDS}, tasks)
    if frozen != expected:
        raise CodexWorkPackageError("catalog-drift", "current package contract drifted")


def _task_view(
    caller: dict[str, Any], identity: dict[str, str], task: str
) -> dict[str, Any]:
    """仅从已核验父事实派生完整 Task 内存视图，不另行落盘。"""
    return {
        "work_package_id": caller["work_package_id"],
        "task_ids": caller["task_ids"],
        "target_task_id": task,
        "task_id": task,
        "task_source": caller["task_source"],
        "task_version": caller["task_versions"][task],
        "change_version": caller["change_versions"][task],
        "allowed_files": caller["allowed_files"],
        "forbidden_files": caller["forbidden_files"],
        "expected_output": caller["expected_outputs_by_task"][task],
        **caller["acceptance_by_task"][task],
        "validation_command": caller["validation_commands"][task],
        "caller_contract": caller,
        **identity,
    }


def _check_changed_files(
    caller: dict[str, Any], task: str, outcome: dict[str, Any]
) -> None:
    paths = outcome["result_fields"]["changed_files"]
    scope = caller["scopes_by_task"][task]
    if (
        not isinstance(paths, list)
        or any(not isinstance(path, str) for path in paths)
        or len(set(paths)) != len(paths)
    ):
        raise CodexWorkPackageError(
            "outcome-invalid", "changed_files must be exact unique paths"
        )
    forbidden = normalize_scope_string(caller["forbidden_files"])
    for path in paths:
        if not any(
            match_dispatch_path_v1(path, pattern) for pattern in scope["allowed_files"]
        ) or any(match_dispatch_path_v1(path, pattern) for pattern in forbidden):
            raise CodexWorkPackageError(
                "outcome-scope-invalid", f"unauthorized changed file: {path}"
            )


def _outcome(
    task_id: str, supplied: Any, repo_root: Path, command: str
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """将单个 Task 的原生执行事实规整为发布 outcome；校验验证命令和证据定位符，不从日志或自报状态推断 PASS。"""
    if not isinstance(supplied, Mapping) or set(supplied) != {
        "result_fields",
        "artifacts",
        "validation_summary",
    }:
        raise CodexWorkPackageError(
            "outcome-invalid",
            f"{task_id} must have result_fields, artifacts and validation_summary",
        )
    fields = supplied["result_fields"]
    if not isinstance(fields, Mapping) or set(fields) != set(REQUIRED_RESULT_FIELDS):
        raise CodexWorkPackageError(
            "outcome-invalid", f"{task_id} six result fields are incomplete"
        )
    artifacts = supplied["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise CodexWorkPackageError("outcome-invalid", f"{task_id} artifacts are empty")
    descriptors: list[dict[str, str]] = []
    for index, item in enumerate(artifacts):
        if not isinstance(item, Mapping) or set(item) != {"locator", "sha256"}:
            raise CodexWorkPackageError(
                "outcome-invalid", f"{task_id} artifact {index} is invalid"
            )
        locator, digest = item["locator"], item["sha256"]
        _safe_path(repo_root, locator)
        actual = sha256_file_strict(repo_root, locator)
        if digest != actual:
            raise CodexWorkPackageError(
                "artifact-hash-mismatch", f"{task_id} artifact hash mismatch: {locator}"
            )
        descriptors.append({"locator": locator, "sha256": digest})
    locators = {item["locator"] for item in descriptors}
    if len(locators) != len(descriptors):
        raise CodexWorkPackageError(
            "outcome-invalid", "artifact locators must be unique"
        )
    try:
        validate_all_result_fields(dict(fields), locators)
    except AgentContractError as exc:
        raise CodexWorkPackageError("outcome-invalid", str(exc)) from None
    summary = supplied["validation_summary"]
    if not isinstance(summary, Mapping) or summary.get("command") != command:
        raise CodexWorkPackageError(
            "outcome-invalid", f"{task_id} validation_summary is invalid"
        )
    exit_code = summary.get("exit_code")
    if exit_code is not None and (
        isinstance(exit_code, bool) or not isinstance(exit_code, int)
    ):
        raise CodexWorkPackageError(
            "outcome-invalid", f"{task_id} exit_code must be an integer or null"
        )
    if fields["status"] == "PASS" and (
        exit_code != 0
        or fields["validation"]["status"] != "PASS"
        or any(value != "PASS" for value in fields["effect_checks"].values())
    ):
        raise CodexWorkPackageError(
            "outcome-invalid", f"{task_id} PASS requires completed validation"
        )
    return {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "task_id": task_id,
        "status": fields["status"],
        "result_fields": dict(fields),
        "artifacts": descriptors,
        "validation_summary": dict(supplied["validation_summary"]),
    }, descriptors


def _aggregate(statuses: list[str]) -> str:
    return (
        "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS"
    )


def _read_descriptor(
    repo_root: Path, descriptor: Any, expected_locator: str
) -> dict[str, Any]:
    if (
        not isinstance(descriptor, dict)
        or set(descriptor) != {"locator", "sha256", "bytes"}
        or descriptor["locator"] != expected_locator
    ):
        raise CodexWorkPackageError(
            "completion-invalid", "artifact must use its fixed canonical locator"
        )
    path = _safe_path(repo_root, expected_locator)
    content = path.read_bytes()
    if descriptor != _descriptor(expected_locator, content):
        raise CodexWorkPackageError("artifact-hash-mismatch", expected_locator)
    return _read_json(path, "completion-invalid", content)


def _runtime_binding(root: Path, base: str) -> tuple[dict[str, str], dict[str, Any]]:
    """认证持久化 binding 的真实原生来源；JSON 结构不授予身份。"""
    locator = f"{base}/runtime-binding.json"
    path = root / locator
    if not path.exists() and not path.is_symlink():
        raise CodexWorkPackageError(
            "runtime-metadata-unavailable",
            "native binding is missing",
            status="BLOCKED",
        )
    content = _safe_path(root, locator).read_bytes()
    binding = _read_json(path, "runtime-binding-invalid", content)
    if (
        set(binding) != {"schema_version", "identity", "proof"}
        or binding["schema_version"] != "lexiflow.codex-runtime-binding.v2"
    ):
        raise CodexWorkPackageError(
            "runtime-binding-invalid", "binding fields are invalid"
        )
    identity = _identity(binding["identity"])
    if identity["run_id"] != base.rsplit("/", 1)[1] or not identity[
        "agent_id"
    ].startswith("codex-thread-"):
        raise CodexWorkPackageError(
            "runtime-identity-drift", "binding is not this delegated run"
        )
    from scripts.agents.identity.codex_actor_identity import (
        CodexRuntimeError,
        verify_proof,
    )

    try:
        verify_proof(
            root,
            binding["proof"],
            {
                "actor_id": identity["agent_id"],
                "session_id": identity["session_id"],
                "parent_session_id": identity["parent_session_id"],
                "client": identity["client"],
            },
        )
    except CodexRuntimeError as exc:
        raise CodexWorkPackageError(exc.code, str(exc), status=exc.status) from None
    return identity, _descriptor(locator, content)


def _current_delegated_identity(root: Path, identity: Mapping[str, str]) -> None:
    """发布者必须就是当前原生子代理，而非声称拥有其身份的调用者。"""
    from scripts.agents.identity.codex_actor_identity import CodexRuntimeError, discover

    try:
        actual = discover(root).context
    except CodexRuntimeError as exc:
        raise CodexWorkPackageError(exc.code, str(exc), status=exc.status) from None
    expected = {
        "actor_id": identity["agent_id"],
        "session_id": identity["session_id"],
        "parent_session_id": identity["parent_session_id"],
        "client": identity["client"],
    }
    if not actual["actor_id"].startswith("codex-thread-") or actual != expected:
        raise CodexWorkPackageError(
            "runtime-identity-drift", "publisher is not the bound native subagent"
        )


class CodexWorkPackagePublisher:
    """发布并只读核验原始执行事实；不签发正式 Delivery Gate。"""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).resolve()

    def _parents(
        self, run_id: str
    ) -> tuple[str, dict[str, Any], dict[str, str], dict[str, Any], dict[str, Any]]:
        if not isinstance(run_id, str) or not _UUID.fullmatch(run_id):
            raise CodexWorkPackageError(
                "runtime-context-invalid", "run_id must be a UUID"
            )
        base = f"tmp/quality/codex-work-packages/{run_id}"
        identity, runtime_descriptor = _runtime_binding(self.repo_root, base)
        path = _safe_path(self.repo_root, f"{base}/frozen-caller.json")
        content = path.read_bytes()
        caller = _caller(
            _read_json(path, "caller-contract-invalid", content), frozen=True
        )
        _validate_current(caller, _catalog_tasks(self.repo_root))
        return (
            base,
            caller,
            identity,
            _descriptor(f"{base}/frozen-caller.json", content),
            runtime_descriptor,
        )

    def publish(self, run_id: str, outcomes: Mapping[str, Any]) -> dict[str, Any]:
        """只收 runner 的 run locator 和逐 Task outcome；唯一 completion 最后发布。"""
        base, caller, identity, frozen_descriptor, runtime_descriptor = self._parents(
            run_id
        )
        _current_delegated_identity(self.repo_root, identity)
        if not isinstance(outcomes, Mapping) or set(outcomes) != set(
            caller["task_ids"]
        ):
            raise CodexWorkPackageError(
                "outcome-invalid", "outcomes must exactly cover task_ids"
            )
        directory = self.repo_root / base
        permitted = {
            "validation",
            "cache",
            "tmp",
            "runtime-binding.json",
            "frozen-caller.json",
        }
        if not {item.name for item in directory.iterdir()}.issubset(permitted):
            raise CodexWorkPackageError(
                "run-collision", "run contains publication state"
            )
        prepared = {}
        for task in caller["task_ids"]:
            outcome, _ = _outcome(
                task,
                outcomes[task],
                self.repo_root,
                caller["validation_commands"][task],
            )
            _check_changed_files(caller, task, outcome)
            prepared[task] = outcome
        entries = {}
        for task in caller["task_ids"]:
            task_base = f"{base}/tasks/{task}"
            _mkdir(self.repo_root, task_base)
            projection = {
                "schema_version": CODEX_TASK_PROJECTION_SCHEMA_VERSION,
                "target_task_id": task,
                "frozen_caller_descriptor": frozen_descriptor,
                "runtime_binding_descriptor": runtime_descriptor,
            }
            validate_codex_work_package_task_projection(projection)
            entries[task] = {}
            for key, filename, value in (
                ("task_projection", "task-projection.json", projection),
                ("outcome", "outcome.json", prepared[task]),
            ):
                locator = f"{task_base}/{filename}"
                content = canonical_json_bytes(value)
                _write_exclusive(self.repo_root / locator, content)
                entries[task][key] = _descriptor(locator, content)
        # Recheck native sources and all referenced evidence before the final publication.
        _, final_caller, final_identity, final_frozen, final_runtime = self._parents(
            run_id
        )
        if (
            final_caller != caller
            or final_identity != identity
            or final_frozen != frozen_descriptor
            or final_runtime != runtime_descriptor
        ):
            raise CodexWorkPackageError(
                "artifact-hash-mismatch", "parents changed during publication"
            )
        _current_delegated_identity(self.repo_root, identity)
        for outcome in prepared.values():
            for artifact in outcome["artifacts"]:
                if (
                    sha256_file_strict(self.repo_root, artifact["locator"])
                    != artifact["sha256"]
                ):
                    raise CodexWorkPackageError(
                        "artifact-hash-mismatch",
                        "external artifact changed during publication",
                    )
        completion = {
            "schema_version": SCHEMA_VERSION,
            "frozen_caller_descriptor": frozen_descriptor,
            "runtime_binding_descriptor": runtime_descriptor,
            "tasks": entries,
        }
        _write_exclusive(
            directory / "package-completion.json", canonical_json_bytes(completion)
        )
        return self.verify(run_id)

    def verify(self, run_id: str) -> dict[str, Any]:
        """重读父来源、raw hashes 和 CURRENT Catalog，派生 producer 内存视图。"""
        base, caller, identity, frozen_descriptor, runtime_descriptor = self._parents(
            run_id
        )
        locator = f"{base}/package-completion.json"
        completion_path = _safe_path(self.repo_root, locator)
        completion_bytes = completion_path.read_bytes()
        completion = _read_json(completion_path, "completion-invalid", completion_bytes)
        if (
            set(completion)
            != {
                "schema_version",
                "frozen_caller_descriptor",
                "runtime_binding_descriptor",
                "tasks",
            }
            or completion["schema_version"] != SCHEMA_VERSION
        ):
            raise CodexWorkPackageError(
                "completion-invalid", "completion fields are not exact"
            )
        for field, descriptor, filename in (
            ("frozen_caller_descriptor", frozen_descriptor, "frozen-caller.json"),
            ("runtime_binding_descriptor", runtime_descriptor, "runtime-binding.json"),
        ):
            _read_descriptor(self.repo_root, completion[field], f"{base}/{filename}")
            if completion[field] != descriptor:
                raise CodexWorkPackageError(
                    "artifact-hash-mismatch", "parent binding changed"
                )
        entries = completion["tasks"]
        if not isinstance(entries, dict) or set(entries) != set(caller["task_ids"]):
            raise CodexWorkPackageError("completion-invalid", "Task map is not exact")
        views = {}
        for task in caller["task_ids"]:
            entry = entries[task]
            if not isinstance(entry, dict) or set(entry) != {
                "task_projection",
                "outcome",
            }:
                raise CodexWorkPackageError(
                    "completion-invalid", "Task artifact fields are not exact"
                )
            task_base = f"{base}/tasks/{task}"
            projection = _read_descriptor(
                self.repo_root,
                entry["task_projection"],
                f"{task_base}/task-projection.json",
            )
            try:
                validate_codex_work_package_task_projection(projection)
            except AgentContractError as exc:
                raise CodexWorkPackageError("projection-invalid", str(exc)) from None
            if (
                projection["target_task_id"] != task
                or projection["frozen_caller_descriptor"] != frozen_descriptor
                or projection["runtime_binding_descriptor"] != runtime_descriptor
            ):
                raise CodexWorkPackageError(
                    "completion-invalid", "Task parent or target binding drifted"
                )
            outcome = _read_descriptor(
                self.repo_root, entry["outcome"], f"{task_base}/outcome.json"
            )
            checked, _ = _outcome(
                task,
                {
                    key: outcome.get(key)
                    for key in ("result_fields", "artifacts", "validation_summary")
                },
                self.repo_root,
                caller["validation_commands"][task],
            )
            if outcome != checked:
                raise CodexWorkPackageError(
                    "completion-invalid", "outcome fields drifted"
                )
            _check_changed_files(caller, task, outcome)
            views[task] = {
                "projection": _task_view(caller, identity, task),
                "outcome": outcome,
                "artifacts": entry,
            }
        # 历史消费返回前重核 raw 字节和原生来源；不缓存先前的认证结论。
        _, final_caller, final_identity, final_frozen, final_runtime = self._parents(
            run_id
        )
        if (
            final_caller != caller
            or final_identity != identity
            or final_frozen != frozen_descriptor
            or final_runtime != runtime_descriptor
        ):
            raise CodexWorkPackageError(
                "artifact-hash-mismatch", "parents changed during verification"
            )
        if completion_path.read_bytes() != completion_bytes:
            raise CodexWorkPackageError(
                "artifact-hash-mismatch", "completion changed during verification"
            )
        for view in views.values():
            for descriptor in [
                *view["artifacts"].values(),
                *view["outcome"]["artifacts"],
            ]:
                if (
                    sha256_file_strict(self.repo_root, descriptor["locator"])
                    != descriptor["sha256"]
                ):
                    raise CodexWorkPackageError(
                        "artifact-hash-mismatch", "artifact changed during verification"
                    )
        return {
            "status": "PASS",
            "work_package_id": caller["work_package_id"],
            "task_ids": caller["task_ids"],
            "run_id": run_id,
            "artifact_locators": {
                "package_completion": _descriptor(locator, completion_bytes)
            },
            "validation_commands": [],
            "blocking_findings": [],
            "producer_view": {
                "caller_contract": caller,
                "identity": identity,
                "status": _aggregate(
                    [views[task]["outcome"]["status"] for task in caller["task_ids"]]
                ),
                "tasks": views,
            },
        }
