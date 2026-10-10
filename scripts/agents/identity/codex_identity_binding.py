"""可信且不可变的 Codex 运行身份绑定。此模块只消费受信任的宿主事实，不从调用者字段发现或推断身份。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_AGENT = re.compile(
    r"^(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}|/[A-Za-z0-9_][A-Za-z0-9._/-]{0,255})$"
)
_IDENTITY = (
    "parent_session_id",
    "agent_id",
    "run_id",
    "session_id",
    "client",
    "parent_client",
)
_HOST = ("parent_session_id", "agent_id", "session_id", "client", "parent_client")


class CodexRuntimeError(ValueError):
    """可信 Codex 运行身份不可得或与 caller 声明冲突时失败。"""

    def __init__(self, code: str, detail: str, *, status: str = "FAIL") -> None:
        self.code = code
        self.status = status
        super().__init__(f"{code}: {detail}")


def canonical_json_bytes(value: Any) -> bytes:
    """固定运行身份记录的 JSON 编码以便内容哈希比较。"""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _uuid(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise CodexRuntimeError(
            "runtime-context-invalid", f"{field} must be a canonical UUID"
        )
    return value


def _host_context(value: Mapping[str, Any] | None) -> dict[str, str]:
    if value is None:
        raise CodexRuntimeError(
            "runtime-metadata-unavailable",
            "trusted host metadata is unavailable",
            status="BLOCKED",
        )
    if not isinstance(value, Mapping) or set(value) != set(_HOST):
        raise CodexRuntimeError(
            "runtime-context-invalid", "trusted host context has an invalid shape"
        )
    result = {field: value[field] for field in _HOST}
    _uuid(result["parent_session_id"], "parent_session_id")
    _uuid(result["session_id"], "session_id")
    if not isinstance(result["agent_id"], str) or not _AGENT.fullmatch(
        result["agent_id"]
    ):
        raise CodexRuntimeError("runtime-context-invalid", "agent_id is invalid")
    if result["client"] != "codex" or result["parent_client"] != "codex":
        raise CodexRuntimeError("runtime-context-invalid", "host must be Codex routed")
    return result


@dataclass(frozen=True)
class _CodexRuntimeBinding:
    """内部序列化记录；持久化仍必须重核原生 proof，类型不授予身份。"""

    identity: Mapping[str, str]
    proof: Mapping[str, Any]

    def persist_immutable(self, repo_root: str | Path) -> dict[str, Any]:
        """一次性写入身份绑定；已有记录不得覆盖或悄然替换。"""
        root = Path(repo_root).resolve()
        identity = dict(self.identity)
        _host_context({key: identity.get(key) for key in _HOST})
        _uuid(identity.get("run_id"), "run_id")
        if set(identity) != set(_IDENTITY):
            raise CodexRuntimeError(
                "runtime-context-invalid", "binding identity is incomplete"
            )
        run_id = identity["run_id"]
        # A binding is evidence only when the persisted identity follows the
        # native thread ancestry. A caller-provided host-shaped dict is not proof.
        from scripts.agents.identity.codex_actor_identity import verify_proof

        verify_proof(
            root,
            dict(self.proof),
            {
                "actor_id": identity["agent_id"],
                "session_id": identity["session_id"],
                "parent_session_id": identity["parent_session_id"],
                "client": identity["client"],
            },
        )
        directory = root / "tmp/quality/codex-work-packages" / run_id
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink() or not directory.is_dir():
            raise CodexRuntimeError(
                "runtime-binding-invalid", "binding directory is unsafe"
            )
        path = directory / "runtime-binding.json"
        payload = {
            "schema_version": "lexiflow.codex-runtime-binding.v2",
            "identity": identity,
            "proof": dict(self.proof),
        }
        content = canonical_json_bytes(payload)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            raise CodexRuntimeError(
                "runtime-binding-collision", "immutable binding already exists"
            ) from None
        else:
            try:
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(fd)
            finally:
                os.close(fd)
        return {
            "locator": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
