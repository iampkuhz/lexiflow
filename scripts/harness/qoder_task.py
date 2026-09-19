"""Qoder CLI 子任务入口。

本模块负责显式启动带完整 handoff 与独立身份的 qodercli 子任务并立即返回 run_id，
Worker 后台阻塞等待 CLI 退出后原子写入完成记录，status/result/resume 提供单次读取；
按用户授权默认 bypass_permissions；不负责轮询、自动续跑、读取私人 session 文件或传自动提交参数，
退出 0 仅说明进程结束而不自动等价于质量验收 PASS；
由 Qoder 客户端通过 harness/manifest.yaml 中 public_executables 登记的命令调用。
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

# 支持不设置 PYTHONPATH 直接运行本脚本；把仓库根加入搜索路径
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.harness import qoder_task_lifecycle as _lifecycle  # noqa: E402
from scripts.harness.qoder_cli_failure import (  # noqa: E402
    access_blocked, compact_failure_signal, summarize_cli_failure,
)
from scripts.gates.evidence_packet import dispatch_path_contains, dispatch_paths_intersect  # noqa: E402

REQUIRED_HANDOFF: tuple[str, ...] = (
    "goal",
    "task_id",
    "task_source",
    "task_version",
    "change_version",
    "work_package_id",
    "task_ids",
    "task_versions",
    "change_versions",
    "estimated_minutes",
    "primary_owner",
    "contract_boundary",
    "agent_profile",
    "harness_manifest",
    "allowed_files",
    "forbidden_files",
    "required_context",
    "expected_output",
    "acceptance_criteria",
    "acceptance_evidence",
    "validation_command",
    "failure_policy",
    "parent_client",
)

RUNTIME_IDENTITY_FIELDS: tuple[str, ...] = (
    "agent_id",
    "run_id",
    "session_id",
    "client",
    "harness_manifest_sha256",
    "harness_context",
)

DEFAULT_PERMISSION_MODE = "bypass_permissions"
VALID_PERMISSION_MODES = frozenset({"default", "accept_edits", "dont_ask", DEFAULT_PERMISSION_MODE})
MAX_PROMPT_CHARACTERS = 8000
MIN_QODER_PACKAGE_MINUTES = 10
MAX_QODER_PACKAGE_MINUTES = 360
MIN_QODER_PACKAGE_TASKS = 1

_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_RECEIPT_RE = re.compile(
    r"^Queued message "
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}) "
    r"for thread "
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.$"
)

_DISPATCH_LOCK_FILE = ".dispatch.lock"
_TERMINAL_COMPLETION_STATUSES = frozenset({"finished", "failed", "completed"})


def _is_valid_uuid(value: str) -> bool:
    """校验值是否为合法 UUID 格式。"""
    return isinstance(value, str) and bool(_UUID_RE.fullmatch(value))


def _find_repo_root(start: Path) -> Path:
    """从 start 向上查找含 .git 的仓库根目录。"""
    current = start.absolute()
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    raise FileNotFoundError(f"repo root (with .git) not found from {start}")


def _find_qoder_cli() -> Path:
    """查找 qodercli 可执行文件，支持环境变量覆盖。"""
    override = os.environ.get("QODER_TASK_CLI")
    if override:
        p = Path(override)
        if not p.is_file() or not os.access(p, os.X_OK):
            raise FileNotFoundError(f"qodercli not found at {override}")
        return p
    found = shutil.which("qodercli")
    if not found:
        raise FileNotFoundError("qodercli not found on PATH")
    return Path(found)


def _validate_run_id(run_id: str) -> str:
    """校验 run_id 格式，防路径穿越。"""
    if not _ID_RE.fullmatch(run_id):
        raise ValueError(f"invalid run id: {run_id}")
    return run_id


def _check_qoder_idle() -> None:
    """检测未经过本 Harness 的 CLI；不能替代覆盖执行期的宿主锁。"""
    try:
        processes = subprocess.check_output(
            ["ps", "-U", str(os.getuid()), "-o", "pid=,args="], text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("无法检查 Qoder 进程，未启动任务") from exc
    pids = []
    for line in processes.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and re.fullmatch(r"qodercli(?:-\d[\w.-]*)?", Path(fields[1].split(None, 1)[0]).name):
            pids.append(fields[0])
    if pids:
        raise ValueError(f"BUSY: Qoder CLI 正在运行 (PID {', '.join(pids)})；结束后再 start/resume")


def _is_non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_non_empty_text_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def _validate_task(task: dict[str, Any], *, runtime_bound: bool = False) -> dict[str, Any]:
    """校验 caller handoff；落盘前再校验 runner 绑定的身份。"""
    if not isinstance(task, dict):
        raise ValueError("task must be a JSON object")
    for field in REQUIRED_HANDOFF:
        if field not in task:
            raise ValueError(f"missing required handoff field: {field}")
    text_fields = set(REQUIRED_HANDOFF) - {
        "task_version",
        "task_ids",
        "task_versions",
        "change_versions",
        "estimated_minutes",
        "acceptance_criteria",
        "acceptance_evidence",
    }
    for field in sorted(text_fields):
        if not _is_non_empty_text(task[field]):
            raise ValueError(f"handoff field {field!r} must be non-empty text")
    for field in ("acceptance_criteria", "acceptance_evidence"):
        if not _is_non_empty_text_list(task[field]):
            raise ValueError(f"handoff field {field!r} must be a non-empty text list")
    task_version = task["task_version"]
    if isinstance(task_version, bool) or not isinstance(task_version, int) or task_version <= 0:
        raise ValueError("task_version must be a positive integer")
    change_version = task["change_version"]
    if not isinstance(change_version, str) or not _SEMVER_RE.fullmatch(change_version):
        raise ValueError("change_version must be an exact SemVer string")
    task_ids = task["task_ids"]
    if (
        not isinstance(task_ids, list)
        or len(task_ids) < MIN_QODER_PACKAGE_TASKS
        or len(task_ids) != len(set(task_ids))
        or not all(isinstance(item, str) and _ID_RE.fullmatch(item) for item in task_ids)
    ):
        raise ValueError(
            f"task_ids must contain at least {MIN_QODER_PACKAGE_TASKS} unique stable IDs"
        )
    if task["task_id"] not in task_ids:
        raise ValueError("anchor task_id must be present in task_ids")
    for field, version_type in (("task_versions", int), ("change_versions", str)):
        versions = task[field]
        if not isinstance(versions, dict) or set(versions) != set(task_ids):
            raise ValueError(f"{field} keys must exactly equal task_ids")
        for value in versions.values():
            if version_type is int and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise ValueError(f"{field} values must be positive integers")
            if version_type is str and (not isinstance(value, str) or not _SEMVER_RE.fullmatch(value)):
                raise ValueError(f"{field} values must be exact SemVer strings")
    if task["task_versions"].get(task["task_id"]) != task_version:
        raise ValueError("anchor task_version must match task_versions")
    if task["change_versions"].get(task["task_id"]) != change_version:
        raise ValueError("anchor change_version must match change_versions")
    estimated = task["estimated_minutes"]
    if (
        isinstance(estimated, bool)
        or not isinstance(estimated, int)
        or not MIN_QODER_PACKAGE_MINUTES <= estimated <= MAX_QODER_PACKAGE_MINUTES
    ):
        raise ValueError(
            f"estimated_minutes must be {MIN_QODER_PACKAGE_MINUTES}-{MAX_QODER_PACKAGE_MINUTES}"
        )
    if not _ID_RE.fullmatch(task["work_package_id"]):
        raise ValueError("work_package_id contains invalid characters")
    if not _ID_RE.fullmatch(task["agent_profile"]):
        raise ValueError("agent_profile contains invalid characters")
    if "title" in task and (not isinstance(task["title"], str) or not task["title"].strip()):
        raise ValueError("title must be a non-empty string")
    if "task_id" in task:
        if not isinstance(task["task_id"], str) or not _ID_RE.fullmatch(task["task_id"]):
            raise ValueError("task_id contains invalid characters")
    if not runtime_bound:
        supplied = sorted(field for field in RUNTIME_IDENTITY_FIELDS if field in task)
        if supplied:
            raise ValueError(
                "start task must omit runner-bound identity fields: " + ", ".join(supplied)
            )
    else:
        agent_id = task.get("agent_id", "")
        if not isinstance(agent_id, str) or not _ID_RE.fullmatch(agent_id):
            raise ValueError("agent_id contains invalid characters")
        run_id = task.get("run_id", "")
        if not isinstance(run_id, str) or not _is_valid_uuid(run_id):
            raise ValueError("run_id must be a runner-generated UUID")
        session_id = task.get("session_id", "")
        if not isinstance(session_id, str) or not _is_valid_uuid(session_id):
            raise ValueError("session_id must be a runner-bound UUID")
        if task.get("client") != "qoder":
            raise ValueError("client must be runner-forced to qoder")
    if "permission_mode" in task:
        perm = task["permission_mode"]
        if not isinstance(perm, str) or perm not in VALID_PERMISSION_MODES:
            raise ValueError(
                f"permission_mode must be one of {sorted(VALID_PERMISSION_MODES)}, got {perm!r}"
            )
    if task["parent_client"] != "codex":
        raise ValueError("parent_client must be codex")
    if "parent_session_id" in task and task["parent_session_id"] is not None:
        if not isinstance(task["parent_session_id"], str):
            raise ValueError("parent_session_id must be a string")
    if runtime_bound:
        parent_session_id = task.get("parent_session_id", "")
        if not isinstance(parent_session_id, str) or not _is_valid_uuid(parent_session_id):
            raise ValueError("codex parent_session_id must be bound to a UUID")
    task.setdefault("permission_mode", DEFAULT_PERMISSION_MODE)
    return task


def _repo_regular_file(repo_root: Path, locator: str) -> Path:
    """解析仓库内普通文件，拒绝绝对路径、穿越和符号链接。"""
    if not isinstance(locator, str) or not locator or Path(locator).is_absolute():
        raise ValueError(f"invalid repository file locator: {locator!r}")
    candidate = repo_root / locator
    _check_no_symlink_ancestors(candidate)
    try:
        candidate.resolve().relative_to(repo_root.resolve())
    except ValueError:
        raise ValueError(f"repository file escapes root: {locator}") from None
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError(f"repository file is unavailable: {locator}")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_harness_manifest(
    task: dict[str, Any], repo_root: Path, *, catalog_package: dict[str, Any] | None = None
) -> dict[str, Any]:
    """启动前验证 Qoder 真正可见且可执行的上下文、角色、工具和命令。"""
    path = _repo_regular_file(repo_root, task["harness_manifest"])
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "lexiflow.qoder-harness.v1":
        raise ValueError("invalid Qoder harness manifest schema")
    identity = manifest.get("identity")
    expected_identity = {
        "work_package_id": task["work_package_id"],
        "task_ids": task["task_ids"],
        "task_versions": task["task_versions"],
        "change_versions": task["change_versions"],
        "agent_profile": task["agent_profile"],
    }
    if identity != expected_identity:
        raise ValueError("Qoder harness manifest identity mismatch")
    profile = _repo_regular_file(repo_root, f".qoder/agents/{task['agent_profile']}.md")
    required_context = manifest.get("required_context")
    if not isinstance(required_context, list) or not required_context:
        raise ValueError("Qoder harness required_context must be non-empty")
    seen: set[str] = set()
    for entry in required_context:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ValueError("invalid Qoder harness context entry")
        context_path = _repo_regular_file(repo_root, entry["path"])
        if entry["path"] in seen or _sha256(context_path) != entry["sha256"]:
            raise ValueError(f"Qoder harness context missing, duplicate, or stale: {entry['path']}")
        seen.add(entry["path"])
    mandatory = {
        "AGENTS.md",
        ".qoder/AGENTS.md",
        str(profile.relative_to(repo_root)),
        "harness/agent-policy.manifest.yaml",
    }
    if not mandatory.issubset(seen):
        raise ValueError("Qoder harness context is missing mandatory project policy or catalog files")
    catalog = catalog_package
    context_hashes = {entry["path"]: entry["sha256"] for entry in required_context}
    if catalog is not None and catalog["catalog_sha256"] != context_hashes.get("planning/workstreams.yaml"):
        raise ValueError("Qoder catalog changed after package normalization")
    commands = manifest.get("validation_commands")
    if not isinstance(commands, list) or not commands:
        raise ValueError("Qoder harness validation_commands must be non-empty")
    for command in commands:
        if not isinstance(command, dict) or set(command) != {"argv", "cwd", "timeout_seconds"}:
            raise ValueError("invalid Qoder validation command")
        argv, cwd, timeout = command["argv"], command["cwd"], command["timeout_seconds"]
        if not isinstance(argv, list) or not argv or not all(isinstance(v, str) and v for v in argv):
            raise ValueError("Qoder validation argv must be a non-empty string array")
        if cwd != ".":
            raise ValueError("Qoder validation cwd must be the repository root")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
            raise ValueError("Qoder validation timeout_seconds must be 1-3600")
        executable = argv[0]
        if "/" in executable:
            try:
                candidate = _repo_regular_file(repo_root, (Path(cwd) / executable).as_posix())
            except ValueError:
                raise ValueError(f"Qoder validation executable unavailable: {executable}") from None
            if not os.access(candidate, os.X_OK):
                raise ValueError(f"Qoder validation executable unavailable: {executable}")
        elif shutil.which(executable) is None:
            raise ValueError(f"Qoder validation executable unavailable: {executable}")
    if catalog is None:
        try:
            expected = [tuple(shlex.split(task["validation_command"]))]
        except ValueError as exc:
            raise ValueError("Qoder validation command malformed") from exc
    else:
        expected = list(dict.fromkeys(tuple(argv) for argv in catalog["validation_argv_by_task"].values()))
    observed = [tuple(command["argv"]) for command in commands]
    if observed != expected:
        raise ValueError("Qoder validation commands must exactly cover unique current Task argv in package order")
    tools = manifest.get("required_tools")
    if not isinstance(tools, list) or not tools:
        raise ValueError("Qoder harness required_tools must be non-empty")
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != {
            "name", "probe_argv", "expected_output_regex", "timeout_seconds"
        }:
            raise ValueError("invalid required Qoder tool probe")
        argv = tool["probe_argv"]
        if not isinstance(argv, list) or not argv or not all(isinstance(v, str) and v for v in argv):
            raise ValueError("Qoder tool probe argv must be a non-empty string array")
        timeout = tool["timeout_seconds"]
        if not isinstance(tool["name"], str) or not tool["name"]:
            raise ValueError("Qoder tool probe name must be non-empty")
        if not isinstance(tool["expected_output_regex"], str) or not tool["expected_output_regex"]:
            raise ValueError("Qoder tool probe expected_output_regex must be non-empty")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 30:
            raise ValueError("Qoder tool probe timeout_seconds must be 1-30")
        try:
            probe = subprocess.run(
                argv,
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"required Qoder tool unavailable: {tool['name']}") from exc
        output = probe.stdout + "\n" + probe.stderr
        if probe.returncode != 0 or re.search(tool["expected_output_regex"], output) is None:
            raise ValueError(f"required Qoder tool probe failed: {tool['name']}")
    validated = dict(manifest)
    validated["_manifest_sha256"] = _sha256(path)
    return validated


def _catalog_scope_items(value: Any, field: str) -> list[str]:
    """规范化 catalog 或 handoff 的逗号分隔路径集合。"""
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    else:
        raise ValueError(f"{field} must be a path list or comma-separated path string")
    if not items or any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError(f"{field} must contain non-empty paths")
    normalized = [item.strip() for item in items]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} contains duplicate paths")
    for item in normalized:
        path = Path(item)
        if path.is_absolute() or ".." in path.parts or "\\" in item:
            raise ValueError(f"{field} contains unsafe path: {item}")
    return sorted(normalized)


def _validate_catalog_package(task: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """用当前 catalog 证明 Qoder 工作包的真实规模、owner、版本与写域。"""
    if task["task_source"] != "planning/workstreams.yaml":
        raise ValueError("Qoder work package task_source must be planning/workstreams.yaml")
    catalog_path = _repo_regular_file(repo_root, task["task_source"])
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or not isinstance(catalog.get("workstreams"), list):
        raise ValueError("invalid workstream catalog")
    records: dict[str, tuple[str, dict[str, Any]]] = {}
    for workstream in catalog["workstreams"]:
        if not isinstance(workstream, dict) or not isinstance(workstream.get("id"), str):
            raise ValueError("invalid workstream catalog owner")
        owner = workstream["id"]
        for epic in workstream.get("epics", []):
            for capability in epic.get("capabilities", []):
                for record in capability.get("seed_tasks", []):
                    if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                        raise ValueError("invalid workstream catalog task")
                    task_id = record["id"]
                    if task_id in records:
                        raise ValueError(f"duplicate catalog task: {task_id}")
                    records[task_id] = (record.get("owner", owner), record)

    package_records: list[dict[str, Any]] = []
    total_minutes = 0
    criteria_count = 0
    evidence_count = 0
    validation_argv_by_task: dict[str, list[str]] = {}
    package_allowed: set[str] = set()
    package_forbidden: set[str] = set()
    package_claims: set[tuple[str, str, str]] = set()
    for task_id in task["task_ids"]:
        catalog_entry = records.get(task_id)
        if catalog_entry is None:
            raise ValueError(f"Qoder work package task missing from catalog: {task_id}")
        owner, record = catalog_entry
        if owner != task["primary_owner"]:
            raise ValueError("Qoder work package tasks must share primary_owner")
        if record.get("task_version") != task["task_versions"][task_id]:
            raise ValueError(f"Qoder task_version is stale: {task_id}")
        if record.get("change_version") != task["change_versions"][task_id]:
            raise ValueError(f"Qoder change_version is stale: {task_id}")
        minutes = record.get("estimated_task_minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
            raise ValueError(f"Qoder catalog estimate is missing or invalid: {task_id}")
        total_minutes += minutes
        for field in ("acceptance_criteria", "acceptance_evidence"):
            if not _is_non_empty_text_list(record.get(field)):
                raise ValueError(f"Qoder catalog {field} must be a non-empty text list: {task_id}")
        criteria_count += len(record["acceptance_criteria"])
        evidence_count += len(record["acceptance_evidence"])
        command = record.get("validation_command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"Qoder validation command missing from catalog: {task_id}")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise ValueError(f"Qoder validation command malformed: {task_id}") from exc
        if not argv or any(not argument for argument in argv):
            raise ValueError(f"Qoder validation argv invalid: {task_id}")
        validation_argv_by_task[task_id] = argv
        allowed = _catalog_scope_items(record.get("allowed_files"), f"catalog[{task_id}].allowed_files")
        forbidden = _catalog_scope_items(record.get("forbidden_files"), f"catalog[{task_id}].forbidden_files")
        claims = record.get("file_claims")
        if not isinstance(claims, list) or not claims:
            raise ValueError(f"Qoder catalog file_claims are missing: {task_id}")
        normalized_claims: list[tuple[str, str, str]] = []
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict) or set(claim) != {"path", "mode", "owner"}:
                raise ValueError(f"Qoder catalog file_claim invalid: {task_id}[{index}]")
            _catalog_scope_items([claim["path"]], f"catalog[{task_id}].file_claims[{index}].path")
            if claim["mode"] not in {"read", "write"} or claim["owner"] != task["primary_owner"]:
                raise ValueError(f"Qoder catalog file_claim invalid: {task_id}[{index}]")
            if not any(dispatch_path_contains(path, claim["path"]) for path in allowed):
                raise ValueError(f"Qoder catalog claim outside Task scope: {task_id}")
            normalized_claims.append((claim["path"], claim["mode"], claim["owner"]))
        normalized_claims.sort()
        package_allowed.update(allowed)
        package_forbidden.update(forbidden)
        package_claims.update(normalized_claims)
        package_records.append(record)

    if total_minutes != task["estimated_minutes"]:
        raise ValueError(
            f"Qoder estimated_minutes must equal catalog task sum: {total_minutes}"
        )
    if criteria_count < 4 or evidence_count < 3:
        raise ValueError("large Qoder package requires at least 4 catalog criteria and 3 catalog evidence items")
    if any(dispatch_paths_intersect(allow, deny) for allow in package_allowed for deny in package_forbidden):
        raise ValueError("Qoder package catalog allowed/forbidden scopes intersect")
    if _catalog_scope_items(task["allowed_files"], "allowed_files") != sorted(package_allowed):
        raise ValueError("Qoder allowed_files must equal the derived catalog scope")
    if _catalog_scope_items(task["forbidden_files"], "forbidden_files") != sorted(package_forbidden):
        raise ValueError("Qoder forbidden_files must equal the derived catalog scope")

    anchor = records[task["task_id"]][1]
    expected_anchor = {
        "expected_output": anchor.get("deliverable"),
        "acceptance_criteria": anchor.get("acceptance_criteria"),
        "acceptance_evidence": anchor.get("acceptance_evidence"),
        "validation_command": anchor.get("validation_command"),
    }
    for field, expected in expected_anchor.items():
        if task[field] != expected:
            raise ValueError(f"Qoder anchor {field} differs from current catalog")
    return {
        "catalog_sha256": _sha256(catalog_path),
        "total_minutes": total_minutes,
        "task_count": len(package_records),
        "criteria_count": criteria_count,
        "evidence_count": evidence_count,
        "validation_argv_by_task": validation_argv_by_task,
        "package_file_claims": [{"path": path, "mode": mode, "owner": owner} for path, mode, owner in sorted(package_claims)],
        "task_scopes": {record["id"]: {"allowed_files": record["allowed_files"], "forbidden_files": record["forbidden_files"], "file_claims": record["file_claims"]} for record in package_records},
    }


def _resolve_catalog_package(task: dict[str, Any], repo_root: Path) -> dict[str, Any] | None:
    """尽力使用 Catalog 增强核对；自包含 handoff 不因其缺失或漂移而失去派发资格。"""
    try:
        return _validate_catalog_package(task, repo_root)
    except (FileNotFoundError, ValueError, yaml.YAMLError):
        return None


def _check_no_symlink_ancestors(path: Path) -> None:
    """拒绝路径或其任何祖先为符号链接。检查未解析的绝对路径各级。"""
    abs_path = path.absolute()
    for component in [abs_path, *abs_path.parents]:
        if component.is_symlink():
            # macOS exposes the physical temporary hierarchy through the
            # system-owned `/var` compatibility alias.  It is above every
            # caller-selected repository root, so reject no repository
            # descendant less strictly merely because this host alias exists.
            if component == Path("/var") and component.resolve() == Path("/private/var"):
                continue
            raise ValueError(f"symlink in path: {component}")


def _task_dir(repo_root: Path) -> Path:
    """返回任务目录，支持环境变量覆盖（须在本仓库内且无符号链接）。"""
    override = os.environ.get("QODER_TASK_DIR")
    if override:
        td = Path(override)
        if not td.exists():
            raise FileNotFoundError(f"QODER_TASK_DIR does not exist: {override}")
        _check_no_symlink_ancestors(td)
        try:
            td.resolve().relative_to(repo_root.resolve())
        except ValueError:
            raise ValueError(f"QODER_TASK_DIR must be inside repo: {override}") from None
        return td
    default = repo_root / "tmp" / "qoder-tasks"
    _check_no_symlink_ancestors(default)
    return default


def _ensure_task_dir(task_dir: Path) -> None:
    """以私有权限创建默认任务目录，并拒绝非目录或符号链接。"""
    _check_no_symlink_ancestors(task_dir)
    old_umask = os.umask(0o077)
    try:
        task_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    finally:
        os.umask(old_umask)
    if not task_dir.is_dir() or task_dir.is_symlink():
        raise ValueError(f"task_dir is not a private directory: {task_dir}")


@contextmanager
def _host_lease(*, create: bool = True):
    """同一用户所有 checkout 共用；只 close，不 LOCK_UN 解开子进程继承的锁。"""
    path = _host_lock_path()
    _check_no_symlink_ancestors(path)
    if not create and not path.exists():
        yield None
        return
    if create:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_CREAT if create else 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("unsafe host Qoder lease file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("BUSY: host-wide Qoder lease is held; end this turn and await callback") from None
        yield fd
    finally:
        os.close(fd)


def _host_lock_path() -> Path:
    # Deliberately independent of cwd, CODEX_HOME and task package identity.
    return Path.home() / ".cache" / "lexiflow" / "qoder-cli.lock"


def _assert_host_idle() -> None:
    with _host_lease(create=False):
        pass


def _verify_inherited_lease(fd: int) -> None:
    path = _host_lock_path()
    _check_no_symlink_ancestors(path)
    actual, expected = os.fstat(fd), path.stat()
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError("worker did not inherit the host Qoder lease")
    if not stat.S_ISREG(actual.st_mode) or actual.st_uid != os.getuid() or actual.st_mode & 0o022:
        raise ValueError("unsafe inherited Qoder lease")
    # An unlocked forged descriptor cannot override an existing holder.
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def _dispatch_lock(task_dir: Path):
    """获取 task_dir 级非阻塞文件锁，串行化所有新 run 的派发窗口。"""
    _ensure_task_dir(task_dir)
    lock_path = task_dir / _DISPATCH_LOCK_FILE
    _check_no_symlink_ancestors(lock_path)
    fd = os.open(
        str(lock_path),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"dispatch lock is not a regular file: {lock_path}")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("BUSY: another Qoder dispatch is being reserved") from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _verify_terminal_completion(run_dir: Path) -> tuple[bool, str]:
    """只用 completion 的固定字段确认终态，不读取其他 run 的任务 prompt。"""
    completion_path = run_dir / "completion.json"
    if not completion_path.exists():
        return False, "completion missing"
    try:
        completion = _safe_read_json(completion_path)
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return False, "completion unreadable"
    if not isinstance(completion, dict):
        return False, "completion is not an object"
    status_value = completion.get("status")
    if status_value not in _TERMINAL_COMPLETION_STATUSES:
        return False, f"completion status {status_value!r} is not terminal"
    if completion.get("run_id") != run_dir.name:
        return False, "completion run_id mismatch"
    exit_code = completion.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return False, "completion exit_code is not an integer"
    session_id = completion.get("session_id", "")
    if not isinstance(session_id, str) or not _is_valid_uuid(session_id):
        return False, "completion session_id is invalid"
    for field in ("task_id", "agent_id"):
        value = completion.get(field, "")
        if not isinstance(value, str) or not value or not _ID_RE.fullmatch(value):
            return False, f"completion {field} is invalid"
    task_version = completion.get("task_version")
    if isinstance(task_version, bool) or not isinstance(task_version, int) or task_version <= 0:
        return False, "completion task_version is invalid"
    change_version = completion.get("change_version", "")
    if not isinstance(change_version, str) or not _SEMVER_RE.fullmatch(change_version):
        return False, "completion change_version is invalid"
    if completion.get("client") != "qoder":
        return False, "completion client is not qoder"
    return True, status_value


def _assert_no_unfinished_runs(task_dir: Path) -> None:
    """若本仓库存在任何不可验证终态的 run，则阻断新的 Qoder 派发。"""
    blockers: list[str] = []
    for entry in sorted(task_dir.iterdir(), key=lambda item: item.name):
        if entry.name == _DISPATCH_LOCK_FILE:
            continue
        if entry.is_symlink():
            blockers.append(f"{entry.name}: symlink run directory")
            continue
        if not entry.is_dir():
            continue
        verified, reason = _verify_terminal_completion(entry)
        if not verified:
            blockers.append(f"{entry.name}: {reason}")
        elif ((entry / "continuation.json").exists()
              and _safe_read_json(entry / "continuation.json").get("state") == "AWAITING_CALLBACK"
              and not _lifecycle.ack_path(entry).exists()):
            blockers.append(f"{entry.name}: terminal callback not consumed/acknowledged")
    if blockers:
        details = "; ".join(blockers[:8])
        if len(blockers) > 8:
            details += f"; and {len(blockers) - 8} more"
        raise ValueError(f"BUSY: existing Qoder run is not verifiably terminal: {details}")


def _completed_runs(task_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Read runner completion metadata, not prompts or private sessions."""
    records = []
    for directory in task_dir.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        path = directory / "completion.json"
        if path.is_file() and not path.is_symlink():
            records.append((directory, _safe_read_json(path)))
    return records


def _assert_dispatch_budget(task_dir: Path, task: dict[str, Any], *, correction: bool) -> None:
    """An anchor/package/version change cannot reset a stable Task's budget."""
    counts = dict.fromkeys(task["task_ids"], 0)
    for _, completion in _completed_runs(task_dir):
        historical_ids = completion.get("task_ids")
        if not isinstance(historical_ids, list):
            historical_ids = [completion.get("task_id")]
        for task_id in counts:
            if task_id in historical_ids:
                counts[task_id] += 1
    limit = 2 if correction else 1
    blocked = [task_id for task_id, count in counts.items() if count >= limit]
    if blocked:
        exhausted = any(counts[task_id] >= 2 for task_id in blocked)
        if exhausted:
            instruction = (
                "该 Task 已使用完 Qoder 的两次尝试上限。它并非不能继续："
                "保留原 Task identity，由 Main 或独立 Codex 会话接手；"
                "不得用新同义 Task 重置 Qoder 尝试次数。"
            )
        else:
            instruction = "该 Task 已使用首次 Qoder 尝试；若需一次纠正，请使用显式 resume。"
        raise ValueError(f"ATTEMPT_BUDGET: {', '.join(blocked[:3])}。{instruction}")


def _assert_runtime_recovery(task_dir: Path, *, confirmed: bool) -> str | None:
    """Do not silently allocate another session after an account/access denial."""
    records = _completed_runs(task_dir)
    if not records:
        return
    records.sort(key=lambda item: (item[0] / "completion.json").stat().st_mtime_ns, reverse=True)
    for directory, completion in records:
        if completion.get("status") in {"finished", "completed"}:
            return None
        failure = completion.get("failure")
        if not isinstance(failure, dict):
            failure = summarize_cli_failure(directory / "stdout.log", completion.get("exit_code", 1))
        if not access_blocked(failure):
            # A startup/network/unknown failure is not proof that account access recovered.
            continue
        if not confirmed:
            raise ValueError(
                f"RUNTIME_BLOCKED: run {directory.name}; {compact_failure_signal(failure)}; "
                "check account/model access, then explicitly use --runtime-recovery-confirmed; "
                "this assertion does not verify account health or bypass run/attempt guards"
            )
        return directory.name
    return None


def _cleanup_unspawned_run(run_dir: Path) -> None:
    """删除当前派发创建且尚未 spawn 的私有 run 占位。"""
    if not run_dir.exists():
        return
    _check_no_symlink_ancestors(run_dir)
    for child in run_dir.iterdir():
        if child.is_symlink() or not child.is_file():
            raise ValueError(f"cannot safely clean unspawned run: {child}")
        child.unlink()
    run_dir.rmdir()


def _write_worker_spawn_failure(run_dir: Path, task: dict[str, Any], exc: OSError) -> None:
    """将 worker spawn 失败持久化为可验证终态。"""
    completion = _build_completion(
        task,
        "failed",
        126,
        error=f"worker spawn failed: {type(exc).__name__}",
        run_id=run_dir.name,
    )
    _atomic_write_json(run_dir / "completion.json", completion)
    _atomic_write_json(run_dir / "continuation.json", {
        "schema_version": "lexiflow.qoder-continuation.v1", "run_id": run_dir.name,
        "state": "DISPATCH_FAILED", "next_action": "inspect-dispatch-error",
    })


def _atomic_write_json(path: Path, data: dict[str, Any], mode: int = 0o600) -> None:
    """原子写入 JSON，设置文件权限。"""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.chmod(tmp, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _safe_read_json(path: Path) -> dict[str, Any]:
    """安全读取 JSON，拒绝符号链接。"""
    if path.is_symlink():
        raise ValueError(f"refusing to read symlink: {path}")
    _check_no_symlink_ancestors(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _result_template(task: dict[str, Any]) -> dict[str, Any]:
    """Return a run-bound starting shape, never a claimed execution result."""
    return {
        "schema_version": "lexiflow.qoder-work-package-result.v1",
        "status": "BLOCKED",
        "work_package_id": task["work_package_id"],
        "task_ids": list(task["task_ids"]),
        "task_versions": dict(task["task_versions"]),
        "change_versions": dict(task["change_versions"]),
        "run_id": task["run_id"],
        "outcomes": [
            {"task_id": task_id, "status": "BLOCKED", "acceptance_evidence": []}
            for task_id in task["task_ids"]
        ],
        "changed_files": [],
        "validation": [],
        "acceptance_evidence": [],
        "effect_checks": [],
        "risks": [],
    }


def _build_prompt(task: dict[str, Any]) -> str:
    """构建完整 prompt，包含所有 handoff 字段和身份信息。"""
    lines = [
        f"Goal: {task['goal']}",
        f"Work package id: {task['work_package_id']}",
        f"Task ids: {json.dumps(task['task_ids'], ensure_ascii=False)}",
        f"Task versions: {json.dumps(task['task_versions'], sort_keys=True)}",
        f"Change versions: {json.dumps(task['change_versions'], sort_keys=True)}",
        f"Estimated minutes: {task['estimated_minutes']}",
        f"Primary owner: {task['primary_owner']}",
        f"Contract boundary: {task['contract_boundary']}",
        f"Agent profile: {task['agent_profile']}",
        f"Harness manifest: {task['harness_manifest']}",
        f"Harness manifest SHA-256: {task.get('harness_manifest_sha256', '<runner-bound>')}",
        "Frozen harness context: " + json.dumps(task.get("harness_context", []), ensure_ascii=False),
        "Read every task record named in task_ids from the hash-bound planning/workstreams.yaml; "
        "apply each deliverable, acceptance contract, validation command, and file claim before implementation.",
        f"Task id: {task['task_id']}",
        f"Task source: {task['task_source']}",
        f"Task version: {task['task_version']}",
        f"Change version: {task['change_version']}",
        f"Allowed files/directories: {task['allowed_files']}",
        f"Forbidden files/directories: {task['forbidden_files']}",
        f"Required context files: {task['required_context']}",
        f"Expected output: {task['expected_output']}",
        "Acceptance criteria:\n- " + "\n- ".join(task["acceptance_criteria"]),
        "Acceptance evidence:\n- " + "\n- ".join(task["acceptance_evidence"]),
        f"Validation command: {task['validation_command']}",
        f"Failure policy: {task['failure_policy']}",
        "Execute each unique manifest validation argv once after the complete package source is frozen; "
        "each Task outcome references its hash-bound execution evidence, sharing evidence only for identical argv/source. "
        "Do not repeat delivery commands in review/catalog. ",
        "Required result fields: schema_version, status, work_package_id, task_ids, "
        "task_versions, change_versions, run_id, outcomes, changed_files, validation, "
        "acceptance_evidence, effect_checks, risks",
        f"Copy the run-bound starting shape tmp/qoder-tasks/{task.get('run_id', '<runner-run-id>')}/result.template.json "
        f"to tmp/qoder-tasks/{task.get('run_id', '<runner-run-id>')}/result.json, then replace its BLOCKED placeholders with factual results.",
        f"Required result file: tmp/qoder-tasks/{task.get('run_id', '<runner-run-id>')}/result.json",
        "Result schema: lexiflow.qoder-work-package-result.v1 with exact work_package_id, "
        "task_ids, task_versions, change_versions, run_id and one outcome per task_id. "
        "outcomes MUST be an array in task_ids order, NOT an object keyed by task_id; "
        "each item requires task_id, status (PASS/BLOCKED/FAIL), acceptance_evidence (array). "
        "changed_files, validation, acceptance_evidence, effect_checks and risks MUST be arrays. "
        "Write it before the final response; prose and exit code are not acceptance evidence.",
        "Before final response, validate result structure only (not delivery tests): "
        f"python3 scripts/harness/qoder_task.py validate-result {task.get('run_id', '<runner-run-id>')}",
    ]
    if task.get("title"):
        lines.insert(0, f"Task title: {task['title']}")
    if task.get("agent_id"):
        lines.append(f"Agent id: {task['agent_id']}")
    if task.get("client"):
        lines.append(f"Client: {task['client']}")
    if task.get("session_id"):
        lines.append(f"Session id: {task['session_id']}")
    if task.get("run_id"):
        lines.append(f"Run id: {task['run_id']}")
    if task.get("parent_client"):
        lines.append(f"Parent client: {task['parent_client']}")
    if task.get("parent_session_id"):
        lines.append(f"Parent session id: {task['parent_session_id']}")
    return "\n".join(lines)


def _validate_prompt_budget(task: dict[str, Any]) -> str:
    """阻断会重复粘贴大段设计的 handoff；详细上下文应通过文件定位。"""
    prompt = _build_prompt(task)
    if len(prompt) > MAX_PROMPT_CHARACTERS:
        raise ValueError(
            f"Qoder prompt exceeds {MAX_PROMPT_CHARACTERS} characters; "
            "replace repeated design text with bounded file locators"
        )
    return prompt


def _build_qodercli_args(task: dict[str, Any], cwd: Path) -> list[str]:
    """构建 qodercli 命令行参数数组。新任务传 --session-id，恢复任务传 --resume。"""
    cli = _find_qoder_cli()
    prompt = _validate_prompt_budget(task)
    args = [
        str(cli),
        "-p",
        prompt,
        "--cwd",
        str(cwd),
        "--agent",
        task["agent_profile"],
        # Preserve CLI provider/model settings; project-only silently drops custom models.
        "--setting-sources",
        "user,project,local",
    ]
    perm = task.get("permission_mode", DEFAULT_PERMISSION_MODE)
    args.extend(["--permission-mode", perm])
    # 委派入口只执行当前目标，不暴露递归调度工具；Bash 仍不构成目录沙箱。
    args.extend(["--disallowed-tools", "Agent"])
    args.extend(["--output-format", "json"])
    # No hidden multi-attempt model loop; the parent owns explicit bounded recovery.
    args.extend(["--max-model-request-retries", "0"])
    session_id = task.get("session_id", "")
    if session_id:
        if task.get("_resume_mode"):
            args.extend(["--resume", session_id])
        else:
            args.extend(["--session-id", session_id])
    return args


def _run_codex_queue_cli(
    thread_id: str, message: str, timeout: int = 10, cwd: str = "."
) -> subprocess.CompletedProcess[str]:
    """调用 codex queue CLI，参数数组传递，显式 cwd 与有限超时。"""
    return subprocess.run(
        ["codex", "queue", "--thread", thread_id, "--message", message],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=cwd,
    )


def _build_callback_message(run_id: str, completion: dict[str, Any], run_dir: Path) -> str:
    """构建回调消息，仅含受校验字段与固定指令，不含可注入文本或原始日志。"""
    task_id = completion.get("task_id", "")
    work_package_id = completion.get("work_package_id", "")
    task_ids = completion.get("task_ids", [])
    status = completion.get("status", "unknown")
    exit_code = completion.get("exit_code", "")
    lines = [
        f"[qoder-callback] run_id={run_id} work_package_id={work_package_id} task_id={task_id}",
        f"task_ids: {json.dumps(task_ids)}",
        f"status: {status} exit_code: {exit_code}",
        f"结果目录: {run_dir}",
        "请核对 completion 身份和版本、必要 diff 与已有验证证据；不要无条件重跑交付命令。",
        "已 ack 或 superseded 时直接结束。日志不进入回调。queued 不算任务通过。",
    ]
    signal = compact_failure_signal(completion.get("failure"))
    if signal:
        lines.insert(4, signal)
    if access_blocked(completion.get("failure")):
        lines.insert(-1, "账号/访问阻断不得原样补开；先确认外部状态已修复，再显式恢复。无新产物不得计交付通过。")
    return "\n".join(lines)


def _callback_claim_path(run_dir: Path) -> Path:
    """返回回调认领文件路径。"""
    return run_dir / "callback.claim"


def _is_run_consumed_or_superseded(run_dir: Path) -> bool:
    """检查 run 是否已被消费或替代。"""
    # 检查 ack.json（权威 marker）
    ack_file = _lifecycle.ack_path(run_dir)
    if ack_file.exists():
        return True
    # 检查 lifecycle 状态
    lifecycle = _lifecycle.read_lifecycle(run_dir)
    status = lifecycle.get("status", "")
    return status in ("acknowledged", "superseded")


def _attempt_codex_callback(
    run_dir: Path,
    run_id: str,
    completion: dict[str, Any],
    task: dict[str, Any],
    cwd: Path,
) -> None:
    """尝试向 Codex 父会话发送回调通知。

    写入 callback.json，状态为 queued/failed/unknown。
    不重试，不覆盖已确认 queued。claim 文件保证同一 run 至多一次尝试。
    超时或回执不可验证时状态为 unknown，不伪造 queued。
    已 ack 或 superseded 的 run 不发送。
    """
    if task.get("parent_client") != "codex":
        return
    parent_session_id = task.get("parent_session_id", "")
    if not parent_session_id or not _is_valid_uuid(parent_session_id):
        return

    # 检查是否已被消费或替代 - 在 claim 前检查
    if _is_run_consumed_or_superseded(run_dir):
        return

    callback_path = run_dir / "callback.json"
    if callback_path.exists():
        return

    claim_path = _callback_claim_path(run_dir)
    try:
        fd = os.open(str(claim_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return
        _atomic_write_json(
            callback_path,
            {
                "status": "unknown",
                "error": f"claim failed: {exc}",
                "parent_session_id": parent_session_id,
            },
            mode=0o600,
        )
        return

    # claim 后 queue 前再次检查 - 防止竞态
    if _is_run_consumed_or_superseded(run_dir):
        return

    message = _build_callback_message(run_id, completion, run_dir)
    returned = False
    try:
        result = _run_codex_queue_cli(parent_session_id, message, cwd=str(cwd))
        returned = True
        if result.returncode == 0:
            msg_id = ""
            thread_verified = False
            for line in result.stdout.splitlines():
                m = _RECEIPT_RE.fullmatch(line.strip())
                if m:
                    msg_id = m.group(1)
                    thread_verified = m.group(2).lower() == parent_session_id.lower()
                    break
            if msg_id and thread_verified:
                _atomic_write_json(
                    callback_path,
                    {
                        "status": "queued",
                        "message_id": msg_id,
                        "parent_session_id": parent_session_id,
                    },
                    mode=0o600,
                )
            else:
                _atomic_write_json(
                    callback_path,
                    {
                        "status": "unknown",
                        "error": "exit 0 but no verifiable receipt",
                        "parent_session_id": parent_session_id,
                    },
                    mode=0o600,
                )
        else:
            _atomic_write_json(
                callback_path,
                {
                    "status": "failed",
                    "exit_code": result.returncode,
                    "parent_session_id": parent_session_id,
                },
                mode=0o600,
            )
    except subprocess.TimeoutExpired:
        _atomic_write_json(
            callback_path,
            {
                "status": "unknown",
                "error": "回调超时，状态不确定",
                "parent_session_id": parent_session_id,
            },
            mode=0o600,
        )
    except Exception as exc:
        _atomic_write_json(
            callback_path,
            {
                "status": "failed"
                if isinstance(exc, FileNotFoundError) and not returned
                else "unknown",
                "error": type(exc).__name__,
                "parent_session_id": parent_session_id,
            },
            mode=0o600,
        )


def _build_completion(
    task: dict[str, Any], status: str, exit_code: int, **extra: Any
) -> dict[str, Any]:
    """从任务与结果信息构建完成记录字典。"""
    completion: dict[str, Any] = {
        "status": status,
        "exit_code": exit_code,
        "session_id": task.get("session_id", ""),
        "task_id": task.get("task_id", ""),
        "task_version": task.get("task_version", ""),
        "change_version": task.get("change_version", ""),
        "title": task.get("title", task.get("task_id", "")),
        "agent_id": task.get("agent_id", ""),
        "client": task.get("client", "qoder"),
        "parent_client": task.get("parent_client", ""),
        "parent_session_id": task.get("parent_session_id", ""),
        "work_package_id": task.get("work_package_id", ""),
        "task_ids": task.get("task_ids", []),
        "task_versions": task.get("task_versions", {}),
        "change_versions": task.get("change_versions", {}),
    }
    completion.update(extra)
    return completion


def _validate_structured_result(run_dir: Path, task: dict[str, Any]) -> dict[str, Any]:
    """验证 Qoder 落盘的紧凑结果结构；不把其中 PASS 当作主 Agent 验收。"""
    path = run_dir / "result.json"
    result = _safe_read_json(path)
    if result.get("schema_version") != "lexiflow.qoder-work-package-result.v1":
        raise ValueError("invalid Qoder result schema")
    for field in ("work_package_id", "task_ids", "task_versions", "change_versions", "run_id"):
        expected = task["run_id"] if field == "run_id" else task[field]
        if result.get(field) != expected:
            raise ValueError(f"Qoder result identity mismatch: {field}")
    if result.get("status") not in {"PASS", "BLOCKED", "FAIL"}:
        raise ValueError("Qoder result status must be PASS, BLOCKED, or FAIL")
    outcomes = result.get("outcomes")
    if not isinstance(outcomes, list) or [item.get("task_id") for item in outcomes if isinstance(item, dict)] != task["task_ids"]:
        raise ValueError("Qoder result must contain one ordered outcome per task_id")
    for outcome in outcomes:
        if outcome.get("status") not in {"PASS", "BLOCKED", "FAIL"}:
            raise ValueError("Qoder task outcome has invalid status")
        if not isinstance(outcome.get("acceptance_evidence"), list):
            raise ValueError("Qoder task outcome acceptance_evidence must be a list")
    required_types = {
        "changed_files": list,
        "validation": list,
        "acceptance_evidence": list,
        "effect_checks": list,
        "risks": list,
    }
    for field, expected_type in required_types.items():
        if not isinstance(result.get(field), expected_type):
            raise ValueError(f"Qoder result {field} must be a {expected_type.__name__}")
    return result


def cmd_validate_result(args: argparse.Namespace) -> None:
    """只读检查本 run 的结果结构；不签发收据、结束进程或确认验收。"""
    run_id = _validate_run_id(args.run_id)
    run_dir = _task_dir(_find_repo_root(Path.cwd())) / run_id
    _check_no_symlink_ancestors(run_dir)
    task = _safe_read_json(run_dir / "task.json")
    if task.get("run_id") != run_id:
        raise ValueError("Qoder task identity does not match the run directory")
    result = _validate_structured_result(run_dir, task)
    print(json.dumps({"run_id": run_id, "status": "PASS", "result_status": result["status"],
                      "scope": "result-structure-only-not-task-acceptance"}))


def _worker_entry(task_dir: Path, run_id: str, cwd: Path, lease_fd: int | None = None) -> None:
    if lease_fd is None:
        with _host_lease() as fd:
            return _worker_entry(task_dir, run_id, cwd, fd)
    _verify_inherited_lease(lease_fd)
    _run_worker_with_lease(task_dir, run_id, cwd, lease_fd)


def _run_worker_with_lease(task_dir: Path, run_id: str, cwd: Path, lease_fd: int) -> None:
    """Worker 入口：从 task.json 读取任务，Popen 启动 CLI，记录启动证据后 wait 获取真实退出码。

    started.json 在 qodercli 成功 Popen 后写入真实子进程 PID，作为真正启动证据。
    spawn 失败不写 started.json；metadata 写失败不改变真实退出码。
    """
    run_dir = task_dir / run_id
    record_path = run_dir / "completion.json"
    stdout_log = run_dir / "stdout.log"
    stderr_log = run_dir / "stderr.log"
    task_file = run_dir / "task.json"
    task: dict[str, Any] = {}
    completion: dict[str, Any] = {}
    proc: subprocess.Popen | None = None
    started_at: float | None = None
    lifecycle_errors: list[str] = []
    try:
        task = _validate_task(_safe_read_json(task_file), runtime_bound=True)
        if stdout_log.is_symlink() or stderr_log.is_symlink():
            raise ValueError("log path is a symlink")
        qoder_args = _build_qodercli_args(task, cwd)
        with (
            stdout_log.open("w", encoding="utf-8") as out,
            stderr_log.open("w", encoding="utf-8") as err,
        ):
            # Popen 成功才说明真正启动了子进程
            proc = subprocess.Popen(
                qoder_args,
                pass_fds=(lease_fd,),
                stdout=out,
                stderr=err,
                cwd=str(cwd),
            )
        started_at = time.time()
        # 元数据失败与 CLI 执行分离，始终等待已创建的子进程并保留真实退出码。
        try:
            _lifecycle.record_started(run_dir, proc.pid, task.get("session_id", ""))
            _complete_resume_start(run_dir, task)
        except (OSError, ValueError) as exc:
            lifecycle_errors.append(f"startup metadata: {exc}")
        # 等待真实退出码
        exit_code = proc.wait()
        finished_at = time.time()
        status = "finished" if exit_code == 0 else "failed"
        result_error = ""
        if exit_code == 0:
            try:
                _validate_structured_result(run_dir, task)
            except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
                status = "failed"
                result_error = str(exc)
        completion = _build_completion(
            task,
            status,
            exit_code,
            qoder_exit_code=exit_code,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=round(finished_at - started_at, 3),
            stdout_log=str(stdout_log),
            stderr_log=str(stderr_log),
            run_id=run_id,
        )
        if result_error:
            completion["result_error"] = result_error
    except FileNotFoundError as exc:
        # CLI 缺失 - 不写 started.json
        exit_code = 127
        completion = _build_completion(task, "failed", exit_code, error=str(exc), run_id=run_id)
    except Exception as exc:
        exit_code = proc.wait() if proc is not None else 1
        completion = _build_completion(
            task,
            "finished" if exit_code == 0 else "failed",
            exit_code,
            error=str(exc),
            run_id=run_id,
        )

    if proc is None:
        try:
            _release_resume_reservation(run_dir)
        except (OSError, ValueError) as exc:
            lifecycle_errors.append(f"reservation cleanup: {exc}")
    if lifecycle_errors:
        completion["lifecycle_errors"] = lifecycle_errors
    if completion.get("status") == "failed":
        completion["failure"] = summarize_cli_failure(
            stdout_log, completion["exit_code"],
            pid=proc.pid if proc is not None else None,
            session_id=task.get("session_id", ""), cwd=cwd, started_at=started_at,
        )
    # 无持久化完成记录时不发正常完成通知；watchdog 将诊断缺失结果。
    _atomic_write_json(record_path, completion)

    if task.get("parent_client") == "codex":
        parent_sid = task.get("parent_session_id", "")
        if parent_sid and _is_valid_uuid(parent_sid):
            _attempt_codex_callback(run_dir, run_id, completion, task, cwd)


def _complete_resume_start(run_dir: Path, task: dict[str, Any]) -> None:
    """只有真实 Qoder 已启动才建立替代链，慢启动也由 worker 最终确认。"""
    previous = _lifecycle.read_lifecycle(run_dir).get("previous_run_id")
    if not previous:
        return
    old_dir = run_dir.parent / _validate_run_id(previous)
    old_task = _safe_read_json(old_dir / "task.json")
    for key in ("client", "session_id", "parent_client", "parent_session_id"):
        if old_task.get(key) != task.get(key):
            raise ValueError(f"resume identity mismatch: {key}")
    reservation = _safe_read_json(old_dir / "resume.reservation")
    if reservation.get("new_run_id") != run_dir.name:
        raise ValueError("resume reservation mismatch")
    _lifecycle.record_supersession(old_dir, run_dir.name)


def _release_resume_reservation(run_dir: Path) -> None:
    """仅在确认未创建 Qoder 时释放属于本 run 的恢复占位。"""
    previous = _lifecycle.read_lifecycle(run_dir).get("previous_run_id")
    if not previous:
        return
    old_dir = run_dir.parent / _validate_run_id(previous)
    with _lifecycle._run_lock(old_dir):
        path = old_dir / "resume.reservation"
        if path.exists() and _safe_read_json(path).get("new_run_id") == run_dir.name:
            path.unlink()


def _maybe_start_watchdog(
    task: dict[str, Any], run_dir: Path, repo_root: Path
) -> dict[str, Any] | None:
    """为绑定 Codex 父会话的 run 启动后台 watchdog。返回状态或 None。"""
    if task.get("parent_client") != "codex":
        return None
    parent_sid = task.get("parent_session_id", "")
    if not parent_sid or not _is_valid_uuid(parent_sid):
        return None
    try:
        proc = _lifecycle.start_watchdog(run_dir, repo_root)
        return {"status": "started", "pid": proc.pid}
    except (OSError, ValueError) as exc:
        # 记录错误供 status/result 读取
        try:
            _lifecycle.write_watchdog_status(run_dir, {"status": "unavailable", "error": str(exc)})
        except (OSError, ValueError) as diagnostic_error:
            print(f"warning: watchdog diagnostic unavailable: {diagnostic_error}", file=sys.stderr)
        print(f"warning: watchdog unavailable for {run_dir.name}: {exc}", file=sys.stderr)
        return {"status": "unavailable", "error": str(exc)}


def cmd_start(args: argparse.Namespace) -> None:
    """派发后立即交还控制权，由完成回调续办，不维持主线程等待。"""
    repo_root = _find_repo_root(Path.cwd())
    task_dir = _task_dir(repo_root)
    task_path = Path(args.task)
    _check_no_symlink_ancestors(task_path)
    task = _validate_task(json.loads(task_path.read_text(encoding="utf-8")))

    if "_resume_mode" in task:
        raise ValueError("start does not accept _resume_mode field")

    if not task.get("agent_id"):
        task["agent_id"] = f"agent_{uuid.uuid4().hex[:8]}"
    if not task.get("session_id"):
        task["session_id"] = str(uuid.uuid4())
    task["client"] = "qoder"

    if task.get("parent_client") == "codex":
        if "parent_session_id" not in task:
            parent_sid = os.environ.get("CODEX_THREAD_ID", "")
            if not parent_sid or not _is_valid_uuid(parent_sid):
                raise ValueError(
                    f"parent_session_id must be a valid UUID for codex parent, got {parent_sid!r}"
                )
            task["parent_session_id"] = parent_sid
        else:
            parent_sid = task["parent_session_id"]
            if not isinstance(parent_sid, str) or not parent_sid or not _is_valid_uuid(parent_sid):
                raise ValueError(
                    f"parent_session_id must be a valid UUID for codex parent, got {parent_sid!r}"
                )

    run_id = str(uuid.uuid4())
    task["run_id"] = run_id
    _validate_task(task, runtime_bound=True)
    catalog = _resolve_catalog_package(task, repo_root)
    _validate_prompt_budget(task)
    run_dir = task_dir / run_id
    worker_spawned = False
    with _dispatch_lock(task_dir), _host_lease() as lease_fd:
        # 锁覆盖历史状态扫描、跨 checkout 进程预检、run 占位和 worker spawn。
        _assert_no_unfinished_runs(task_dir)
        _assert_dispatch_budget(task_dir, task, correction=False)
        recovery_from = _assert_runtime_recovery(
            task_dir, confirmed=getattr(args, "runtime_recovery_confirmed", False) is True,
        )
        _find_qoder_cli()
        _check_qoder_idle()
        harness = _validate_harness_manifest(task, repo_root, catalog_package=catalog)
        task["harness_manifest_sha256"] = harness["_manifest_sha256"]
        task["harness_context"] = harness["required_context"]
        _validate_prompt_budget(task)

        _check_no_symlink_ancestors(run_dir)
        old_umask = os.umask(0o077)
        try:
            os.makedirs(run_dir, mode=0o700, exist_ok=False)
        finally:
            os.umask(old_umask)

        try:
            _atomic_write_json(run_dir / "task.json", task)
            # The template proves only shape and identity.  It intentionally has
            # a different name so an untouched template cannot be mistaken for
            # the worker's required result.json.
            _atomic_write_json(run_dir / "result.template.json", _result_template(task))
            _write_continuation(run_id, run_dir)
            if recovery_from:
                _atomic_write_json(run_dir / "runtime-recovery.json", {
                    "from_run_id": recovery_from, "operator_asserted": True,
                    "account_access_verified": False, "recorded_at": time.time(),
                })

            worker_cmd = [
                sys.executable,
                str(Path(__file__).resolve()),
                "_worker",
                str(task_dir),
                run_id,
                str(repo_root),
                str(lease_fd),
            ]
            env = os.environ.copy()
            env.pop("QODER_SESSION_ID", None)
            env["LEXIFLOW_AGENT_CLIENT"] = "qoder"
            env["LEXIFLOW_SESSION_ID"] = task.get("session_id", "")
            env["LEXIFLOW_AGENT_ID"] = task.get("agent_id", "")
            if task.get("parent_client"):
                env["LEXIFLOW_PARENT_CLIENT"] = task["parent_client"]
            if task.get("parent_session_id"):
                env["LEXIFLOW_PARENT_SESSION_ID"] = task["parent_session_id"]

            try:
                subprocess.Popen(
                    worker_cmd,
                    pass_fds=(lease_fd,),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env,
                    cwd=str(repo_root),
                )
                worker_spawned = True
            except OSError as exc:
                _write_worker_spawn_failure(run_dir, task, exc)
                raise ValueError(f"worker spawn failed; run_id={run_id}: {exc}") from None
        except BaseException:
            # 未创建 worker 且没有终态时删除本次唯一占位，避免永久 unknown。
            if not worker_spawned and not (run_dir / "completion.json").exists():
                _cleanup_unspawned_run(run_dir)
            raise

    _maybe_start_watchdog(task, run_dir, repo_root)

    _print_dispatch_handoff(run_id, run_dir)


def _write_continuation(run_id: str, run_dir: Path) -> None:
    _atomic_write_json(run_dir / "continuation.json", {
        "schema_version": "lexiflow.qoder-continuation.v1", "run_id": run_id,
        "state": "AWAITING_CALLBACK", "next_action": "end-current-turn-await-callback",
        "resume_on": "terminal-callback-only", "llm_polling": "forbidden",
        "callback_actions": ["ignore-if-already-acked-or-superseded", "read-exact-run-result",
                             "verify-identity-and-artifacts", "ack-once", "continue-or-report-blocker"],
    })


def _print_dispatch_handoff(run_id: str, run_dir: Path) -> None:
    print(json.dumps({"run_id": run_id, "state": "AWAITING_CALLBACK",
                      "next_action": "end-current-turn-await-callback",
                      "continuation": str(run_dir / "continuation.json"),
                      "started_confirmed": False, "llm_polling": "forbidden"}))


def _reject_parent_polling(run_dir: Path) -> None:
    if (run_dir / "continuation.json").exists() and not (run_dir / "completion.json").exists():
        _print_dispatch_handoff(run_dir.name, run_dir)
        raise SystemExit(3)


def cmd_preflight(args: argparse.Namespace) -> None:
    """只读检查输入及 start 资格快照；不分配 run 或代替锁内复查。"""
    repo_root = _find_repo_root(Path.cwd())
    task_path = Path(args.task)
    _check_no_symlink_ancestors(task_path)
    task = _validate_task(json.loads(task_path.read_text(encoding="utf-8")))
    if "_resume_mode" in task:
        raise ValueError("preflight does not accept _resume_mode field")
    _find_qoder_cli()
    catalog = _resolve_catalog_package(task, repo_root)
    _validate_prompt_budget(task)
    harness = _validate_harness_manifest(task, repo_root, catalog_package=catalog)
    frozen = dict(task)
    frozen["harness_manifest_sha256"] = harness["_manifest_sha256"]
    frozen["harness_context"] = harness["required_context"]
    _validate_prompt_budget(frozen)
    recovery_confirmed = getattr(args, "runtime_recovery_confirmed", False) is True
    blockers = _start_readiness_blockers(
        _task_dir(repo_root), task, runtime_recovery_confirmed=recovery_confirmed,
    )
    print(json.dumps({
        "schema_version": "lexiflow.qoder-preflight-result.v1",
        "status": "BLOCKED" if blockers else "PASS",
        "work_package_id": task["work_package_id"],
        "task_ids": task["task_ids"],
        "estimated_minutes": task["estimated_minutes"],
        "primary_owner": task["primary_owner"],
        "agent_profile": task["agent_profile"],
        "catalog_sha256": None if catalog is None else catalog["catalog_sha256"],
        "harness_manifest_sha256": harness["_manifest_sha256"],
        "context_count": len(harness["required_context"]),
        "required_tools": [item["name"] for item in harness["required_tools"]],
        "validation_command_count": len(harness["validation_commands"]),
        "model_access_checked": False,
        "runtime_recovery_asserted": recovery_confirmed,
        "dispatch_action": "start",
        "blocking_findings": blockers,
        "scope": "core-handoff-context-tools-and-start-readiness-snapshot; "
                 "PASS is not account/model availability, a reservation, or a started run",
    }, ensure_ascii=False))
    if blockers:
        raise SystemExit(1)


def _start_readiness_blockers(
    task_dir: Path, task: dict[str, Any], *, runtime_recovery_confirmed: bool = False,
) -> list[dict[str, str]]:
    """复用派发护栏，不写锁或运行目录；未知历史不继续推断预算。"""
    blockers: list[dict[str, str]] = []

    def check(action) -> bool:
        try:
            action()
        except ValueError as exc:
            code = str(exc).partition(":")[0]
            if code not in {"BUSY", "ATTEMPT_BUDGET", "RUNTIME_BLOCKED"}:
                raise
            blockers.append({"code": code, "message": str(exc)})
            return False
        return True

    if task_dir.exists():
        if not check(lambda: _assert_no_unfinished_runs(task_dir)):
            return blockers
        check(lambda: _assert_dispatch_budget(task_dir, task, correction=False))
        check(lambda: _assert_runtime_recovery(task_dir, confirmed=runtime_recovery_confirmed))
    check(_assert_host_idle)
    check(_check_qoder_idle)
    return blockers


def cmd_status(args: argparse.Namespace) -> None:
    """查询任务状态，包含生命周期信息。"""
    repo_root = _find_repo_root(Path.cwd())
    task_dir = _task_dir(repo_root)
    run_id = _validate_run_id(args.run_id)
    run_dir = task_dir / run_id
    _check_no_symlink_ancestors(run_dir)

    if not run_dir.exists():
        print(f"error: unknown run id: {run_id}", file=sys.stderr)
        sys.exit(1)
    _reject_parent_polling(run_dir)

    lifecycle = _lifecycle.read_lifecycle(run_dir)
    lc_status = lifecycle.get("status", "")
    extra: dict[str, Any] = {}
    watchdog = _lifecycle.read_watchdog_status(run_dir)
    if watchdog:
        extra["watchdog"] = watchdog
    if (run_dir / "ack.json").exists():
        extra["ack"] = _safe_read_json(run_dir / "ack.json")

    record = run_dir / "completion.json"
    if record.exists():
        data = _safe_read_json(record)
        result: dict[str, Any] = {"run_id": run_id, "status": data["status"]}
        if lc_status:
            result["lifecycle"] = lc_status
        print(json.dumps({**result, **extra}))
        return

    if lc_status in ("acknowledged", "superseded", "exhausted"):
        print(json.dumps({"run_id": run_id, "status": lc_status, **extra}))
        return

    if lc_status == "unknown" and lifecycle.get("anomaly"):
        print(json.dumps({"run_id": run_id, "status": "unknown", **extra}))
        return

    pid_file = run_dir / "worker.pid"
    if pid_file.exists():
        pid = _lifecycle._safe_read_pid(pid_file)
        if pid is not None and _lifecycle._is_pid_alive(pid):
            print(json.dumps({"run_id": run_id, "status": "running", "pid": pid, **extra}))
        else:
            print(json.dumps({"run_id": run_id, "status": "unknown", **extra}))
    else:
        print(json.dumps({"run_id": run_id, "status": "unknown", **extra}))


def _read_stdout_tail(path: Path, max_bytes: int = 4000) -> str:
    """读取 stdout 日志尾部最多 max_bytes 字节，不读全文件。"""
    if not path.exists():
        return ""
    if path.is_symlink():
        raise ValueError(f"refusing to read symlink: {path}")
    _check_no_symlink_ancestors(path)
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
        raw = fh.read(max_bytes)
    return raw.decode("utf-8", errors="replace")


def cmd_result(args: argparse.Namespace) -> None:
    """获取任务结果，包含报告/日志路径与有限 stdout 尾部。"""
    repo_root = _find_repo_root(Path.cwd())
    task_dir = _task_dir(repo_root)
    run_id = _validate_run_id(args.run_id)
    run_dir = task_dir / run_id
    _check_no_symlink_ancestors(run_dir)

    if not run_dir.exists():
        print(f"error: unknown run id: {run_id}", file=sys.stderr)
        sys.exit(1)
    _reject_parent_polling(run_dir)

    record = run_dir / "completion.json"

    # 生命周期和异常信息（即使无 completion 也展示）
    lifecycle = _lifecycle.read_lifecycle(run_dir)
    watchdog_status = _lifecycle.read_watchdog_status(run_dir)

    # ack 信息
    ack_file = _lifecycle.ack_path(run_dir)
    ack_data = _safe_read_json(ack_file) if ack_file.exists() else None

    if not record.exists():
        result: dict[str, Any] = {"run_id": run_id, "status": "not_ready"}
        if lifecycle:
            result["lifecycle"] = lifecycle
        if watchdog_status:
            result["watchdog"] = watchdog_status
        if ack_data:
            result["ack"] = ack_data
        print(json.dumps(result, ensure_ascii=False))
        return

    data = _safe_read_json(record)
    data["run_id"] = run_id
    data["task_file"] = str(run_dir / "task.json")
    data["stdout_log"] = str(run_dir / "stdout.log")
    data["stderr_log"] = str(run_dir / "stderr.log")
    result_path = run_dir / "result.json"
    if result_path.exists():
        structured = _safe_read_json(result_path)
        data["result_file"] = str(result_path)
        data["result_status"] = structured.get("status", "invalid")

    callback_path = run_dir / "callback.json"
    if callback_path.exists():
        data["callback"] = _safe_read_json(callback_path)
    elif _callback_claim_path(run_dir).exists():
        data["callback"] = {"status": "unknown", "error": "已认领回调，但无可确认回执"}

    # 生命周期信息（只读）
    if lifecycle:
        data["lifecycle"] = lifecycle

    # watchdog 状态
    if watchdog_status:
        data["watchdog"] = watchdog_status

    # ack 信息
    if ack_data:
        data["ack"] = ack_data

    print(json.dumps(data, ensure_ascii=False))


def cmd_resume(args: argparse.Namespace) -> None:
    """使用记录的 session_id 启动新 run，通过 --resume 恢复。--followup 必填。

    与 start 共用 task_dir 派发锁；先占位再启动 worker，成功后记录 supersession。
    """
    repo_root = _find_repo_root(Path.cwd())
    task_dir = _task_dir(repo_root)
    run_id = _validate_run_id(args.run_id)
    run_dir = task_dir / run_id
    _check_no_symlink_ancestors(run_dir)

    if not run_dir.exists():
        print(f"error: unknown run id: {run_id}", file=sys.stderr)
        sys.exit(1)

    if not args.followup:
        print("error: --followup is required for resume", file=sys.stderr)
        sys.exit(1)

    followup_path = Path(args.followup)
    _check_no_symlink_ancestors(followup_path)
    try:
        followup = json.loads(followup_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"followup is not valid JSON: {exc}") from None
    if not isinstance(followup, dict):
        raise ValueError("followup must be a JSON object")
    immutable_identity = {
        "session_id",
        "client",
        "_resume_mode",
        "agent_id",
        "run_id",
        "parent_client",
        "parent_session_id",
        "harness_manifest_sha256",
        "harness_context",
    }
    forbidden_updates = sorted(immutable_identity.intersection(followup))
    if forbidden_updates:
        raise ValueError(
            "resume followup must omit identity fields: " + ", ".join(forbidden_updates)
        )

    new_run_id = str(uuid.uuid4())
    new_run_dir = task_dir / new_run_id
    _check_no_symlink_ancestors(new_run_dir)
    resume_reservation = run_dir / "resume.reservation"
    _check_no_symlink_ancestors(resume_reservation)
    reservation_created = False
    worker_spawned = False
    new_task: dict[str, Any] = {}

    with _dispatch_lock(task_dir), _host_lease() as lease_fd:
        # 与 start 使用同一原子窗口；旧 run 的可信 completion 会被扫描认定为终态。
        _assert_no_unfinished_runs(task_dir)
        recovery_from = _assert_runtime_recovery(
            task_dir, confirmed=getattr(args, "runtime_recovery_confirmed", False) is True,
        )
        _find_qoder_cli()
        _check_qoder_idle()

        if _lifecycle.has_next_run(run_dir):
            print("error: run already has next_run_id", file=sys.stderr)
            sys.exit(1)

        record = run_dir / "completion.json"
        data = _safe_read_json(record)
        if data.get("status") not in ("finished", "completed", "failed"):
            print(f"error: task status is {data.get('status')}", file=sys.stderr)
            sys.exit(1)

        session_id = data.get("session_id")
        if not session_id:
            print("error: no saved session id to resume", file=sys.stderr)
            sys.exit(1)

        old_task = _safe_read_json(run_dir / "task.json")
        _validate_task(old_task, runtime_bound=True)
        if old_task.get("parent_client") == "codex":
            parent_sid = old_task.get("parent_session_id", "")
            if not parent_sid or not _is_valid_uuid(parent_sid):
                raise ValueError(
                    "saved codex task missing valid parent_session_id; refusing to resume"
                )

        new_task = dict(old_task)
        for key, value in followup.items():
            new_task[key] = value
        new_task["session_id"] = session_id
        new_task["_resume_mode"] = True
        new_task["client"] = "qoder"
        new_task["agent_id"] = f"agent_{uuid.uuid4().hex[:8]}"
        new_task["run_id"] = new_run_id
        _validate_task(new_task, runtime_bound=True)
        _assert_dispatch_budget(task_dir, new_task, correction=True)
        catalog = _resolve_catalog_package(new_task, repo_root)
        _validate_prompt_budget(new_task)
        harness = _validate_harness_manifest(new_task, repo_root, catalog_package=catalog)
        new_task["harness_manifest_sha256"] = harness["_manifest_sha256"]
        new_task["harness_context"] = harness["required_context"]
        _validate_prompt_budget(new_task)

        try:
            try:
                fd = os.open(
                    str(resume_reservation),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                    0o600,
                )
                reservation_created = True
                with os.fdopen(fd, "w", encoding="utf-8") as file_handle:
                    json.dump(
                        {"new_run_id": new_run_id, "reserved_at": time.time(),
                         "runtime_recovery_asserted": recovery_from is not None}, file_handle
                    )
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    print("error: resume already in progress for this run", file=sys.stderr)
                    sys.exit(1)
                raise

            old_umask = os.umask(0o077)
            try:
                os.makedirs(new_run_dir, mode=0o700, exist_ok=False)
            finally:
                os.umask(old_umask)

            _atomic_write_json(new_run_dir / "task.json", new_task)
            _write_continuation(new_run_id, new_run_dir)
            if recovery_from:
                _atomic_write_json(new_run_dir / "runtime-recovery.json", {
                    "from_run_id": recovery_from, "operator_asserted": True,
                    "account_access_verified": False, "recorded_at": time.time(),
                })
            _lifecycle.record_previous_run(new_run_dir, run_id)

            worker_cmd = [
                sys.executable,
                str(Path(__file__).resolve()),
                "_worker",
                str(task_dir),
                new_run_id,
                str(repo_root),
                str(lease_fd),
            ]
            env = os.environ.copy()
            env.pop("QODER_SESSION_ID", None)
            env["LEXIFLOW_AGENT_CLIENT"] = "qoder"
            env["LEXIFLOW_SESSION_ID"] = session_id
            env["LEXIFLOW_AGENT_ID"] = new_task["agent_id"]
            if old_task.get("parent_client"):
                env["LEXIFLOW_PARENT_CLIENT"] = old_task["parent_client"]
            if old_task.get("parent_session_id"):
                env["LEXIFLOW_PARENT_SESSION_ID"] = old_task["parent_session_id"]

            try:
                subprocess.Popen(
                    worker_cmd,
                    pass_fds=(lease_fd,),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env,
                    cwd=str(repo_root),
                )
                worker_spawned = True
            except OSError as exc:
                _write_worker_spawn_failure(new_run_dir, new_task, exc)
                raise ValueError(
                    f"worker spawn failed; new run_id={new_run_id}: {exc}"
                ) from None
        except BaseException as dispatch_error:
            cleanup_errors: list[str] = []
            if reservation_created:
                try:
                    resume_reservation.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    cleanup_errors.append(f"reservation cleanup: {cleanup_error}")
            if not worker_spawned and not (new_run_dir / "completion.json").exists():
                try:
                    _cleanup_unspawned_run(new_run_dir)
                except (OSError, ValueError) as cleanup_error:
                    cleanup_errors.append(f"run cleanup: {cleanup_error}")
            if cleanup_errors:
                cleanup_details = "; ".join(cleanup_errors)
                raise ValueError(
                    f"resume dispatch failed and cleanup was incomplete: {cleanup_details}"
                ) from dispatch_error
            raise

    # 为新 run 启动 watchdog
    _maybe_start_watchdog(new_task, new_run_dir, repo_root)

    _print_dispatch_handoff(new_run_id, new_run_dir)


def cmd_ack(args: argparse.Namespace) -> None:
    """显式消费确认 run 完成。幂等，需要精确父身份核对。"""
    repo_root = _find_repo_root(Path.cwd())
    task_dir = _task_dir(repo_root)

    parent_sid = args.parent_session_id
    codex_thread = os.environ.get("CODEX_THREAD_ID", "")

    try:
        ack_data = _lifecycle.ack_run(
            task_dir,
            args.run_id,
            parent_sid,
            codex_thread_id=codex_thread if codex_thread else None,
        )
        print(json.dumps(ack_data, ensure_ascii=False))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def _dispatch_worker(argv: list[str]) -> int:
    """接收任务目录、运行标识、工作目录并执行后台子任务。"""
    if len(argv) != 5:
        print("usage: _worker <task_dir> <run_id> <cwd> <host-lease-fd>", file=sys.stderr)
        return 1
    task_dir = Path(argv[1])
    run_id = argv[2]
    cwd = Path(argv[3])
    _validate_run_id(run_id)
    _check_no_symlink_ancestors(task_dir)
    run_dir = task_dir / run_id
    _check_no_symlink_ancestors(run_dir)
    try:
        task_dir.resolve().relative_to(cwd.resolve())
        run_dir.resolve().relative_to(cwd.resolve())
    except ValueError:
        print("error: task_dir/run_dir must be inside cwd", file=sys.stderr)
        return 1
    old_umask = os.umask(0o077)
    try:
        pid_file = run_dir / "worker.pid"
        if pid_file.is_symlink():
            raise ValueError("pid path is a symlink")
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
        # 注意：started.json 在 qodercli 成功 Popen 后由 _worker_entry 写入
        lease_fd = int(argv[4])
        _verify_inherited_lease(lease_fd)
        _worker_entry(task_dir, run_id, cwd, lease_fd)
    finally:
        os.umask(old_umask)
    return 0


def main(argv: list[str] | None = None) -> int:
    """主入口，解析子命令并执行。"""
    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] == "_worker":
        try:
            return _dispatch_worker(argv)
        except (ValueError, FileNotFoundError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if argv and argv[0] == "_watchdog":
        try:
            return _lifecycle.watchdog_entry(argv[1], argv[2], argv[3])
        except (ValueError, FileNotFoundError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    parser = argparse.ArgumentParser(description="Qoder CLI subtask entry")
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Start a Qoder subtask")
    p_start.add_argument("--task", required=True, help="Path to task JSON file")
    p_start.add_argument("--runtime-recovery-confirmed", action="store_true",
                         help="Operator asserts account/access repair; not a health check or budget override")
    p_start.set_defaults(func=cmd_start)

    p_preflight = sub.add_parser("preflight", help="Validate a Qoder work package without starting it")
    p_preflight.add_argument("--task", required=True, help="Path to task JSON file")
    p_preflight.add_argument("--runtime-recovery-confirmed", action="store_true",
                            help="Same operator assertion as start; no health check or budget override")
    p_preflight.set_defaults(func=cmd_preflight)

    p_status = sub.add_parser("status", help="Check task status")
    p_status.add_argument("run_id", help="Run ID (UUID) from start")
    p_status.set_defaults(func=cmd_status)

    p_result = sub.add_parser("result", help="Get task result")
    p_result.add_argument("run_id", help="Run ID (UUID) from start")
    p_result.set_defaults(func=cmd_result)

    p_validate = sub.add_parser("validate-result", help="Read-only result structure check, not acceptance")
    p_validate.add_argument("run_id", help="Run ID (UUID) from start or resume")
    p_validate.set_defaults(func=cmd_validate_result)

    p_resume = sub.add_parser("resume", help="Resume with saved session id")
    p_resume.add_argument("run_id", help="Run ID (UUID) from start")
    p_resume.add_argument("--followup", required=True, help="Path to followup JSON")
    p_resume.add_argument("--runtime-recovery-confirmed", action="store_true",
                          help="Operator asserts account/access repair; not a health check or budget override")
    p_resume.set_defaults(func=cmd_resume)

    p_ack = sub.add_parser("ack", help="Acknowledge run completion")
    p_ack.add_argument("run_id", help="Run ID (UUID) to acknowledge")
    p_ack.add_argument(
        "--parent-session-id",
        required=True,
        help="Parent session UUID for identity verification",
    )
    p_ack.set_defaults(func=cmd_ack)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (ValueError, FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
