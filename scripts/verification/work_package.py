"""执行有界工作包诊断，复用 Verification 冻结与子进程内核；不签发正式验收。"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from scripts.verification.execution import Interrupted, bounded
from scripts.verification.kernel import fingerprint_json, run_check_process
from scripts.verification.reports import persist_diagnostic
from scripts.verification.scenarios import verify_repository
from scripts.verification.summaries import selected_result, summarize

_SCHEMA = "lexiflow.verification-work-package.v1"
_FIELDS = {
    "schema_version",
    "work_package_id",
    "check_ids",
    "budget_seconds",
    "execution_mode",
}


def validate_request(request: Any) -> dict[str, Any]:
    """校验唯一配置形状；ID 最多 32 个、预算 1..7200 秒，不接受任意命令或身份。"""
    if not isinstance(request, dict) or set(request) != _FIELDS:
        raise ValueError("work package requires exact bounded configuration fields")
    if request["schema_version"] != _SCHEMA:
        raise ValueError("unknown work package schema")
    package = request["work_package_id"]
    if not isinstance(package, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", package
    ):
        raise ValueError("invalid work package identity")
    ids = request["check_ids"]
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= 32
        or any(not isinstance(x, str) or not x for x in ids)
    ):
        raise ValueError("check_ids requires 1..32 nonempty registered IDs")
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate check ID")
    budget = request["budget_seconds"]
    if type(budget) is not int or not 1 <= budget <= 7200:
        raise ValueError("budget_seconds must be an integer in 1..7200")
    if request["execution_mode"] not in {"delivery", "diagnostic"}:
        raise ValueError("execution_mode must be delivery or diagnostic")
    return {**request, "check_ids": list(ids)}


def execute(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    """运行所选 Registry 检查及真实依赖闭包，保留非正式结果和固定日志 locator。

    root 只供隔离测试注入；公开 CLI 固定当前仓库。delivery 表示失败早停，
    不是正式验收权限。预算覆盖整个运行，单项最多使用剩余总预算。
    """
    request = validate_request(request)
    started = time.monotonic()
    result: dict[str, Any] = {
        "kind": "work-package-diagnostic",
        "diagnostic_id": str(uuid.uuid4()),
        "work_package_id": request["work_package_id"],
        "request_fingerprint": fingerprint_json(request),
        "full_repository_executed": False,
        "formal_acceptance": False,
    }
    try:
        with bounded(request["budget_seconds"]) as deadline:

            def runner(argv, cwd, env, timeout, executable=None, **kwargs):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Interrupted("budget-exhausted")
                return run_check_process(
                    argv, cwd, env, min(timeout, remaining), executable, **kwargs
                )

            report = verify_repository(
                root,
                check_ids=request["check_ids"],
                runner=runner,
                execution_mode=request["execution_mode"],
            )
            result.update(
                selected_result=selected_result(report),
                qualification_result=report.get("result", "FAIL"),
                qualification_reason=report.get("reason", ""),
                summary=summarize(report),
                selected_report={
                    k: v
                    for k, v in report.items()
                    if k not in {"result", "scope", "schema_version", "publication"}
                },
            )
    except Interrupted as exc:
        result.update(
            selected_result="BLOCKED",
            reason=exc.reason,
            interrupted_check_id=getattr(exc, "check_id", None),
            output_artifacts=getattr(exc, "output_artifacts", {}),
        )
    result["wall_seconds"] = round(time.monotonic() - started, 6)
    result["diagnostic_artifact"] = persist_diagnostic(root, result)
    return result


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate request key: {key}")
        value[key] = item
    return value


def main(argv: list[str] | None = None) -> int:
    """接受最多 64 KiB 的 JSON 配置，输出诊断 JSON；检查命令只来自本仓 Registry。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    try:
        with args.request.open("rb") as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError("request exceeds 64 KiB")
        request = json.loads(raw, object_pairs_hook=_unique)
        result = execute(Path(__file__).resolve().parents[2], request)
    except (OSError, ValueError) as exc:
        result = {
            "kind": "work-package-diagnostic",
            "selected_result": "BLOCKED",
            "reason": str(exc),
            "formal_acceptance": False,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[result["selected_result"]]


if __name__ == "__main__":
    raise SystemExit(main())
