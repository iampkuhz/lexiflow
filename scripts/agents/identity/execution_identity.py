"""通过显式注册的原生适配器提供客户端中立的执行身份入口。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ExecutionIdentityError(ValueError):
    """表示原生执行身份或目标绑定证明不可用、无效。"""

    def __init__(self, code: str, detail: str, *, status: str = "FAIL") -> None:
        self.code = code
        self.status = status
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ExecutionIdentity:
    """保存可信原生适配器返回的身份上下文与来源证明。"""

    context: dict[str, str]
    proof: dict[str, Any]


_NATIVE_ADAPTERS = {"codex": "scripts.agents.identity.codex_actor_identity"}
_PROOF_ADAPTERS = {
    "lexiflow.codex-local-session-proof.v2": "scripts.agents.identity.codex_actor_identity"
}


def _load_adapter(module_name: str):
    if module_name not in _NATIVE_ADAPTERS.values():
        raise ExecutionIdentityError(
            "runtime-adapter-unavailable",
            "native identity adapter is not registered",
            status="BLOCKED",
        )
    from importlib import import_module

    return import_module(module_name)


def discover(execution_target: str | Path) -> ExecutionIdentity:
    """从当前受支持的原生路由发现真实身份。

    调用者不能指定身份提供方；新增客户端须实现可信原生适配器并测试后注册。
    """
    if not os.environ.get("CODEX_THREAD_ID"):
        raise ExecutionIdentityError(
            "runtime-adapter-unavailable",
            "no supported native execution route is available",
            status="BLOCKED",
        )
    adapter = _load_adapter(_NATIVE_ADAPTERS["codex"])
    try:
        result = adapter.discover(execution_target)
    except adapter.CodexRuntimeError as exc:
        raise ExecutionIdentityError(exc.code, str(exc), status=exc.status) from None
    return ExecutionIdentity(context=dict(result.context), proof=dict(result.proof))


def verify_proof(
    execution_target: str | Path, proof: Any, context: dict[str, Any]
) -> None:
    """通过静态注册的原生适配器重新读取并核对证明。"""
    if not isinstance(proof, dict):
        raise ExecutionIdentityError("runtime-proof-invalid", "proof is not an object")
    schema = proof.get("schema_version")
    if not isinstance(schema, str):
        raise ExecutionIdentityError(
            "runtime-proof-invalid", "proof schema_version must be a string"
        )
    module_name = _PROOF_ADAPTERS.get(schema)
    if module_name is None:
        raise ExecutionIdentityError(
            "runtime-adapter-unavailable",
            "proof has no registered native adapter",
            status="BLOCKED",
        )
    adapter = _load_adapter(module_name)
    try:
        adapter.verify_proof(execution_target, proof, context)
    except adapter.CodexRuntimeError as exc:
        raise ExecutionIdentityError(exc.code, str(exc), status=exc.status) from None
