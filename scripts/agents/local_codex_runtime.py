"""读取原生任务与子代理来源；共享宿主 Session 不妨碍不同子代理分工。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 文档中的直接脚本入口须与 ``python -m`` 等价。直接运行时，Python 默认将
# 模块目录而非仓库根放进 ``sys.path``，会在读取运行证据前拒绝必需的本地 Gate 绑定。
if __package__ in {None, ""}:
    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

from scripts.agents.codex.runtime_binding import CodexRuntimeBinding, CodexRuntimeError

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


def _metadata(repo_root: Path, session_id: str) -> tuple[dict[str, Any], str]:
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
    cwd = meta.get("cwd")
    if not isinstance(cwd, str) or Path(cwd).resolve() != repo_root:
        raise CodexRuntimeError(
            "runtime-workspace-mismatch", "session belongs to another workspace"
        )
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
    meta, digest = _metadata(root, thread_id)
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

    def bind(self, caller_contract: dict[str, Any], run_id: str) -> CodexRuntimeBinding:
        """把 caller 合同与宿主身份绑定到一个精确 run。"""
        host = {
            "agent_id": self.context["actor_id"],
            "parent_client": "codex",
            **{
                key: self.context[key]
                for key in ("session_id", "parent_session_id", "client")
            },
        }
        return CodexRuntimeBinding.create(caller_contract, host, run_id)


def discover(repo_root: str | Path) -> LocalCodexRuntime:
    """从当前原生 thread 推导 actor；环境变量只定位来源，不要求独立 Session。"""
    root = Path(repo_root).resolve()
    thread = os.environ.get("CODEX_THREAD_ID")
    session = os.environ.get("CODEX_SESSION_ID")
    if not thread and not session:
        raise CodexRuntimeError(
            "runtime-metadata-unavailable",
            "run inside a Codex task in this workspace",
            status="BLOCKED",
        )
    thread_id = _session_id(thread or session)
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


def verify_proof(repo_root: str | Path, proof: Any, context: dict[str, Any]) -> None:
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
    root = Path(repo_root).resolve()
    expected, sources = _native_runtime(root, _session_id(proof["thread_id"]))
    if proof["workspace"] != str(root) or proof["sources"] != sources:
        raise CodexRuntimeError("runtime-proof-drift", "local metadata source changed")
    if context != expected:
        raise CodexRuntimeError(
            "runtime-identity-drift", "actor must derive from the native thread"
        )


def bind_main_task(repo_root: str | Path, task_id: str) -> dict[str, Any]:
    """为当前主任务发布运行身份与投影，不代表任务已执行或验收通过。"""
    from scripts.agents.codex.work_package import (
        build_codex_main_task_projection,
        _write_exclusive,
    )

    root = Path(repo_root).resolve()
    runtime = discover(root)
    run_id = str(uuid.uuid4())
    binding = runtime.bind({"parent_client": "codex"}, run_id)
    projection = build_codex_main_task_projection(
        root,
        task_id,
        binding.identity,
        goal="Record current source for explicit independent Gate validation",
        required_context="AGENTS.md; harness/README.md; current catalog task",
        failure_policy="BLOCKED on missing current evidence; never infer acceptance",
    )
    descriptor = binding.persist_immutable(root)
    path = root / Path(descriptor["locator"]).parent / "main-task-projection.json"
    _write_exclusive(
        path,
        json.dumps(
            projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode(),
    )
    return {
        "result": "PASS",
        "scope": "identity-and-projection-only-not-execution-or-acceptance",
        "run_id": run_id,
        "binding": descriptor,
        "projection": str(path.relative_to(root)),
    }


def main() -> int:
    """读取精确 Task ID 并发布当前主任务的本机身份绑定。"""
    import argparse
    from scripts.agents.codex.work_package import CodexWorkPackageError

    parser = argparse.ArgumentParser(
        description="Bind one current Main task to actual local session metadata"
    )
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()
    try:
        result = bind_main_task(Path.cwd(), args.task_id)
    except (CodexRuntimeError, CodexWorkPackageError, OSError) as exc:
        result = {"result": getattr(exc, "status", "FAIL"), "detail": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[result["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
