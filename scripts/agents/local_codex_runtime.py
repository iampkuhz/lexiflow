"""本地 session 来源校验；信任本机用户，不冒充平台加密认证。"""

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

# Keep the documented direct-script entrypoint equivalent to ``python -m``.
# Direct script execution otherwise places the module directory rather than the repository root
# on ``sys.path`` and rejects this required local Gate binding before it can
# inspect runtime evidence.
if __package__ in {None, ""}:
    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

from scripts.agents.codex.runtime_binding import CodexRuntimeBinding, CodexRuntimeError

PROOF_SCHEMA = "lexiflow.codex-local-session-proof.v1"
ACTOR_PREFIX = "codex-session-"
MAX_METADATA_BYTES = 1024 * 1024


def _session_id(value: Any) -> str:
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value or parsed.int == 0:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise CodexRuntimeError("runtime-route-invalid", "session route must be a canonical non-nil UUID") from None
    return value


def _home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


def _owned_path(path: Path, source_root: Path) -> None:
    """Validate the configured Codex source tree, not OS-level alias ancestors."""
    try:
        relative = path.relative_to(source_root)
    except ValueError:
        raise CodexRuntimeError("runtime-source-unsafe", "session source is outside CODEX_HOME") from None
    current_paths = [source_root]
    current = source_root
    for part in relative.parts:
        current = current / part
        current_paths.append(current)
    for current in reversed(current_paths):
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise CodexRuntimeError("runtime-source-unsafe", "symlink in session source")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise CodexRuntimeError("runtime-source-unsafe", "session source must be an owned non-shared regular file")


def _metadata(repo_root: Path, session_id: str) -> tuple[dict[str, Any], str]:
    candidates = []
    home = _home()
    for name in ("sessions", "archived_sessions"):
        directory = home / name
        if directory.exists():
            candidates.extend(directory.glob(f"**/*-{session_id}.jsonl"))
    if len(candidates) != 1:
        raise CodexRuntimeError("runtime-metadata-unavailable", "expected exactly one local session metadata source", status="BLOCKED")
    path = candidates[0]
    try:
        _owned_path(path, home)
        with path.open("rb") as stream:
            line = stream.readline(MAX_METADATA_BYTES + 1)
    except OSError:
        raise CodexRuntimeError("runtime-metadata-unavailable", "cannot read local session metadata", status="BLOCKED") from None
    if len(line) > MAX_METADATA_BYTES:
        raise CodexRuntimeError("runtime-source-invalid", "session metadata exceeds bound")

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
        raise CodexRuntimeError("runtime-source-invalid", "invalid session metadata header") from None
    if meta.get("id") != session_id or meta.get("session_id", session_id) != session_id:
        raise CodexRuntimeError("runtime-identity-drift", "metadata does not match session route")
    cwd = meta.get("cwd")
    if not isinstance(cwd, str) or Path(cwd).resolve() != repo_root:
        raise CodexRuntimeError("runtime-workspace-mismatch", "session belongs to another workspace")
    # 仅接受 Desktop/CLI 已落盘的独立任务会话。协作子代理仍共享宿主会话，
    # 不能借此获得 issuer 身份；但由 Codex 创建且拥有不同 session id 的任务
    # 是可核验的独立会话，和用户直接创建的任务一样可以签发其他 producer 的验收。
    if (meta.get("source") not in ("vscode", "cli", "exec")
            or meta.get("thread_source", "user") not in ("user", "agent_created_thread")):
        raise CodexRuntimeError("runtime-actor-unavailable", "use a distinct user task session; shared/unrecognized actor metadata cannot prove independence", status="BLOCKED")
    if not isinstance(meta.get("originator"), str) or not meta["originator"]:
        raise CodexRuntimeError("runtime-source-invalid", "session originator is missing")
    return meta, hashlib.sha256(line).hexdigest()


@dataclass(frozen=True)
class LocalCodexRuntime:
    context: dict[str, str]
    proof: dict[str, str]

    def bind(self, caller_contract: dict[str, Any], run_id: str) -> CodexRuntimeBinding:
        host = {"agent_id": self.context["actor_id"], "parent_client": "codex",
                **{key: self.context[key] for key in ("session_id", "parent_session_id", "client")}}
        return CodexRuntimeBinding.create(caller_contract, host, run_id)


def discover(repo_root: str | Path) -> LocalCodexRuntime:
    root = Path(repo_root).resolve()
    thread = os.environ.get("CODEX_THREAD_ID")
    session = os.environ.get("CODEX_SESSION_ID")
    if not thread and not session:
        raise CodexRuntimeError("runtime-metadata-unavailable", "run inside a Codex task in this workspace", status="BLOCKED")
    if thread and session and thread != session:
        raise CodexRuntimeError("runtime-route-conflict", "Codex session and thread routes differ")
    session_id = _session_id(thread or session)
    _, digest = _metadata(root, session_id)
    return LocalCodexRuntime(
        context={"actor_id": ACTOR_PREFIX + session_id, "session_id": session_id,
                 "parent_session_id": session_id, "client": "codex"},
        proof={"schema_version": PROOF_SCHEMA, "session_id": session_id,
               "workspace": str(root), "metadata_sha256": digest},
    )


def verify_proof(repo_root: str | Path, proof: Any, context: dict[str, Any]) -> None:
    """消费历史证明时重读原 session 来源，不要求它仍是当前 session。"""
    fields = {"schema_version", "session_id", "workspace", "metadata_sha256"}
    if not isinstance(proof, dict) or set(proof) != fields or proof["schema_version"] != PROOF_SCHEMA:
        raise CodexRuntimeError("runtime-proof-invalid", "local proof fields are invalid")
    root = Path(repo_root).resolve()
    sid = _session_id(proof["session_id"])
    _, digest = _metadata(root, sid)
    if proof["workspace"] != str(root) or proof["metadata_sha256"] != digest:
        raise CodexRuntimeError("runtime-proof-drift", "local metadata source changed")
    expected = {"actor_id": ACTOR_PREFIX + sid, "session_id": sid,
                "parent_session_id": sid, "client": "codex"}
    if context != expected:
        raise CodexRuntimeError("runtime-identity-drift", "actor must derive from the recorded session")


def bind_main_task(repo_root: str | Path, task_id: str) -> dict[str, Any]:
    from scripts.agents.codex_work_package import build_codex_main_task_projection, _write_exclusive

    root = Path(repo_root).resolve()
    runtime = discover(root)
    run_id = str(uuid.uuid4())
    binding = runtime.bind({"parent_client": "codex"}, run_id)
    projection = build_codex_main_task_projection(
        root, task_id, binding.identity,
        goal="Record current source for explicit independent Gate validation",
        required_context="AGENTS.md; harness/README.md; current catalog task",
        failure_policy="BLOCKED on missing current evidence; never infer acceptance",
    )
    descriptor = binding.persist_immutable(root)
    path = root / Path(descriptor["locator"]).parent / "main-task-projection.json"
    _write_exclusive(path, json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    return {"result": "PASS", "scope": "identity-and-projection-only-not-execution-or-acceptance",
            "run_id": run_id, "binding": descriptor, "projection": str(path.relative_to(root))}


def main() -> int:
    import argparse
    from scripts.agents.codex_work_package import CodexWorkPackageError

    parser = argparse.ArgumentParser(description="Bind one current Main task to actual local session metadata")
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
