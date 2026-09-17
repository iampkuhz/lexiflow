"""Trusted, immutable Codex runtime bindings.

This module deliberately does not discover identity from a caller payload.  The
``trusted_host_context`` argument is the host/orchestrator boundary; JSON that
merely has the same shape is not an authentication mechanism.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_AGENT = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}|/[A-Za-z0-9_][A-Za-z0-9._/-]{0,255})$")
_IDENTITY = ("parent_session_id", "agent_id", "run_id", "session_id", "client", "parent_client")
_HOST = ("parent_session_id", "agent_id", "session_id", "client", "parent_client")


class CodexRuntimeError(ValueError):
    def __init__(self, code: str, detail: str, *, status: str = "FAIL") -> None:
        self.code = code
        self.status = status
        super().__init__(f"{code}: {detail}")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _uuid(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise CodexRuntimeError("runtime-context-invalid", f"{field} must be a canonical UUID")
    return value


def _host_context(value: Mapping[str, Any] | None) -> dict[str, str]:
    if value is None:
        raise CodexRuntimeError("runtime-metadata-unavailable", "trusted host metadata is unavailable", status="BLOCKED")
    if not isinstance(value, Mapping) or set(value) != set(_HOST):
        raise CodexRuntimeError("runtime-context-invalid", "trusted host context has an invalid shape")
    result = {field: value[field] for field in _HOST}
    _uuid(result["parent_session_id"], "parent_session_id")
    _uuid(result["session_id"], "session_id")
    if not isinstance(result["agent_id"], str) or not _AGENT.fullmatch(result["agent_id"]):
        raise CodexRuntimeError("runtime-context-invalid", "agent_id is invalid")
    if result["client"] != "codex" or result["parent_client"] != "codex":
        raise CodexRuntimeError("runtime-context-invalid", "host must be Codex routed")
    return result


def bind_trusted_host_context(
    caller_contract: Mapping[str, Any], trusted_host_context: Mapping[str, Any] | None, run_id: str,
) -> dict[str, str]:
    """Bind a runner-owned run to host metadata without accepting caller identity."""
    if not isinstance(caller_contract, Mapping):
        raise CodexRuntimeError("caller-contract-invalid", "caller contract must be an object")
    forbidden = set(_IDENTITY) - {"parent_client"}
    if forbidden & set(caller_contract):
        raise CodexRuntimeError("caller-identity-injection", "caller contract cannot supply runner identity")
    host = _host_context(trusted_host_context)
    _uuid(run_id, "run_id")
    return {**host, "run_id": run_id}


@dataclass(frozen=True)
class CodexRuntimeBinding:
    identity: Mapping[str, str]

    @classmethod
    def create(cls, caller_contract: Mapping[str, Any], trusted_host_context: Mapping[str, Any] | None, run_id: str) -> "CodexRuntimeBinding":
        return cls(bind_trusted_host_context(caller_contract, trusted_host_context, run_id))

    def persist_immutable(self, repo_root: str | Path) -> dict[str, Any]:
        root = Path(repo_root).resolve()
        identity = dict(self.identity)
        if set(identity) != set(_IDENTITY):
            raise CodexRuntimeError("runtime-context-invalid", "binding identity is incomplete")
        run_id = identity["run_id"]
        directory = root / "tmp/quality/codex-work-packages" / run_id
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink() or not directory.is_dir():
            raise CodexRuntimeError("runtime-binding-invalid", "binding directory is unsafe")
        path = directory / "runtime-binding.json"
        payload = {"schema_version": "lexiflow.codex-runtime-binding.v1", "identity": identity}
        content = canonical_json_bytes(payload)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            try:
                if path.is_symlink() or path.read_bytes() != content:
                    raise CodexRuntimeError("runtime-binding-collision", "immutable binding differs")
            except OSError as exc:
                raise CodexRuntimeError("runtime-binding-invalid", str(exc)) from None
        else:
            try:
                os.write(fd, content)
                os.fsync(fd)
            finally:
                os.close(fd)
        return {"locator": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
