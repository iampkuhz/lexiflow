"""读取原生任务与子代理来源；共享宿主 Session 不妨碍不同子代理分工。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.agents.identity.codex_identity_binding import (
    _CodexRuntimeBinding,
    CodexRuntimeError,
)

PROOF_SCHEMA = "lexiflow.codex-local-session-proof.v2"
ACTOR_PREFIX = "codex-session-"
MAX_METADATA_BYTES = 1024 * 1024


def _session_id(value: Any) -> str:
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value or parsed.int == 0:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise CodexRuntimeError(
            "runtime-route-invalid", "session route must be a canonical non-nil UUID"
        ) from None
    return value


def _home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


def _owned_path(path: Path, source_root: Path) -> None:
    """校验配置的 Codex 来源目录；仅允许受控的系统级路径别名。"""
    try:
        relative = path.relative_to(source_root)
    except ValueError:
        raise CodexRuntimeError(
            "runtime-source-unsafe", "session source is outside CODEX_HOME"
        ) from None
    current_paths = [source_root]
    current = source_root
    for part in relative.parts:
        current = current / part
        current_paths.append(current)
    for current in reversed(current_paths):
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise CodexRuntimeError(
                "runtime-source-unsafe", "symlink in session source"
            )
    info = path.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
    ):
        raise CodexRuntimeError(
            "runtime-source-unsafe",
            "session source must be an owned non-shared regular file",
        )


def _metadata(session_id: str) -> tuple[dict[str, Any], str]:
    candidates = []
    home = _home()
    for name in ("sessions", "archived_sessions"):
        directory = home / name
        if directory.exists():
            candidates.extend(directory.glob(f"**/*-{session_id}.jsonl"))
    if len(candidates) != 1:
        raise CodexRuntimeError(
            "runtime-metadata-unavailable",
            "expected exactly one local session metadata source",
            status="BLOCKED",
        )
    path = candidates[0]
    try:
        _owned_path(path, home)
        with path.open("rb") as stream:
            line = stream.readline(MAX_METADATA_BYTES + 1)
    except OSError:
        raise CodexRuntimeError(
            "runtime-metadata-unavailable",
            "cannot read local session metadata",
            status="BLOCKED",
        ) from None
    if len(line) > MAX_METADATA_BYTES:
        raise CodexRuntimeError(
            "runtime-source-invalid", "session metadata exceeds bound"
        )

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate metadata key")
            result[key] = value
        return result

    try:
        event = json.loads(line, object_pairs_hook=unique)
        meta = event["payload"]
        if event["type"] != "session_meta" or not isinstance(meta, dict):
            raise ValueError()
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise CodexRuntimeError(
            "runtime-source-invalid", "invalid session metadata header"
        ) from None
    if meta.get("id") != session_id:
        raise CodexRuntimeError(
            "runtime-identity-drift", "metadata does not match session route"
        )
    # cwd is a native provenance fact, not the execution target. A session may
    # legitimately operate on another checkout; retain it in the metadata hash.
    cwd = meta.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise CodexRuntimeError("runtime-source-invalid", "session cwd is missing")
    if not isinstance(meta.get("originator"), str) or not meta["originator"]:
        raise CodexRuntimeError(
            "runtime-source-invalid", "session originator is missing"
        )
    return meta, hashlib.sha256(line).hexdigest()


def _native_runtime(
    root: Path, thread_id: str, ancestors: tuple[str, ...] = ()
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """按原生父子元数据识别执行者，不把不同 Session 作为验收前置条件。"""
    thread_id = _session_id(thread_id)
    if thread_id in ancestors or len(ancestors) >= 8:
        raise CodexRuntimeError(
            "runtime-source-invalid", "cyclic or excessive ancestry"
        )
    meta, digest = _metadata(thread_id)
    session_id = _session_id(meta.get("session_id", thread_id))
    source = meta.get("source")
    kind = meta.get("thread_source", "user")
    sources = [{"thread_id": thread_id, "metadata_sha256": digest}]
    if kind == "subagent":
        spawn = (
            source.get("subagent", {}).get("thread_spawn")
            if isinstance(source, dict) and isinstance(source.get("subagent"), dict)
            else None
        )
        if not isinstance(spawn, dict):
            raise CodexRuntimeError(
                "runtime-source-invalid", "missing native spawn source"
            )
        parent_id = _session_id(meta.get("parent_thread_id"))
        path = meta.get("agent_path")
        if (
            spawn.get("parent_thread_id") != parent_id
            or not isinstance(path, str)
            or not path.startswith("/root/")
            or spawn.get("agent_path") != path
        ):
            raise CodexRuntimeError(
                "runtime-identity-drift", "native parent route differs"
            )
        parent, parent_sources = _native_runtime(
            root, parent_id, (*ancestors, thread_id)
        )
        if session_id not in {thread_id, parent["session_id"]}:
            raise CodexRuntimeError("runtime-route-conflict", "unrelated host session")
        sources.extend(parent_sources)
        actor = "codex-thread-" + thread_id
    elif source in ("vscode", "cli", "exec") and kind in (
        "user",
        "agent_created_thread",
    ):
        if session_id != thread_id:
            raise CodexRuntimeError(
                "runtime-route-conflict", "root session route differs"
            )
        parent_id = thread_id
        actor = ACTOR_PREFIX + thread_id
    else:
        raise CodexRuntimeError(
            "runtime-actor-unavailable",
            "unsupported native task source",
            status="BLOCKED",
        )
    return {
        "actor_id": actor,
        "session_id": session_id,
        "parent_session_id": parent_id,
        "client": "codex",
    }, sources


@dataclass(frozen=True)
class LocalCodexRuntime:
    """保存从真实本机 Codex Session 推导的身份上下文及来源证明。"""

    context: dict[str, str]
    proof: dict[str, Any]


def _canonical_repository_root(execution_target: str | Path) -> Path:
    root = Path(execution_target).resolve()
    try:
        actual = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise CodexRuntimeError(
            "runtime-target-invalid",
            "execution target is not a readable git repository",
        ) from None
    canonical = Path(actual).resolve()
    if canonical != root:
        raise CodexRuntimeError(
            "runtime-target-mismatch", "execution target must be the canonical git root"
        )
    return canonical


def discover(execution_target: str | Path) -> LocalCodexRuntime:
    """从当前原生 thread 推导 actor；环境变量只定位来源，不要求独立 Session。"""
    root = _canonical_repository_root(execution_target)
    thread = os.environ.get("CODEX_THREAD_ID")
    session = os.environ.get("CODEX_SESSION_ID")
    if not thread:
        raise CodexRuntimeError(
            "runtime-metadata-unavailable",
            "run inside a Codex task in this workspace",
            status="BLOCKED",
        )
    thread_id = _session_id(thread)
    context, sources = _native_runtime(root, thread_id)
    if session and _session_id(session) != context["session_id"]:
        raise CodexRuntimeError(
            "runtime-route-conflict", "Codex session and thread routes differ"
        )
    return LocalCodexRuntime(
        context=context,
        proof={
            "schema_version": PROOF_SCHEMA,
            "thread_id": thread_id,
            "workspace": str(root),
            "sources": sources,
        },
    )


def verify_proof(
    execution_target: str | Path, proof: Any, context: dict[str, Any]
) -> None:
    """消费历史证明时重读原 session 来源，不要求它仍是当前 session。"""
    fields = {"schema_version", "thread_id", "workspace", "sources"}
    if (
        not isinstance(proof, dict)
        or set(proof) != fields
        or proof["schema_version"] != PROOF_SCHEMA
    ):
        raise CodexRuntimeError(
            "runtime-proof-invalid", "local proof fields are invalid"
        )
    root = _canonical_repository_root(execution_target)
    expected, sources = _native_runtime(root, _session_id(proof["thread_id"]))
    if proof["workspace"] != str(root) or proof["sources"] != sources:
        raise CodexRuntimeError("runtime-proof-drift", "local metadata source changed")
    if context != expected:
        raise CodexRuntimeError(
            "runtime-identity-drift", "actor must derive from the native thread"
        )


def bind_work_package(
    repo_root: str | Path, caller_contract: dict[str, Any]
) -> dict[str, Any]:
    """认证当前原生子代理，生成唯一 run 并冻结完整包合同；不接受身份注入。"""
    from scripts.agents.work_packages.codex_work_package_publisher import (
        _caller,
        _catalog_tasks,
        _descriptor,
        _freeze_caller,
        _mkdir,
        _write_exclusive,
    )
    from scripts.agents.identity.codex_identity_binding import canonical_json_bytes

    root = Path(repo_root).resolve()
    caller = _freeze_caller(_caller(caller_contract), _catalog_tasks(root))
    runtime = discover(root)
    if not runtime.context["actor_id"].startswith("codex-thread-"):
        raise CodexRuntimeError(
            "runtime-identity-drift", "runner is not a native subagent"
        )
    run_id = str(uuid.uuid4())
    identity = {
        "agent_id": runtime.context["actor_id"],
        "parent_client": "codex",
        "run_id": run_id,
        **{
            key: runtime.context[key]
            for key in ("session_id", "parent_session_id", "client")
        },
    }
    _mkdir(root, "tmp/quality/codex-work-packages")
    binding = _CodexRuntimeBinding(identity, runtime.proof).persist_immutable(root)
    locator = f"tmp/quality/codex-work-packages/{run_id}/frozen-caller.json"
    content = canonical_json_bytes(caller)
    _write_exclusive(root / locator, content)
    return {
        "run_id": run_id,
        "frozen_caller_descriptor": _descriptor(locator, content),
        "runtime_binding_descriptor": binding,
    }
