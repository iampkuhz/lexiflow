"""精确 Registry Check 闭包诊断；派生环境，不授予 Formal 资格。"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from scripts.verification.execution.checkout_verification_window import (
    verification_window,
)
from scripts.verification.planning.check_definitions import load_declarations_snapshot
from scripts.verification.execution.check_runner import aggregate_results
from scripts.verification.execution.check_plan_runner import (
    _execute,
    _freeze_selection,
    _runtime_checks,
    _verify_frozen_closure,
)
from scripts.verification.planning.check_scope_selection import (
    resolve_module_dependencies,
)


def diagnose(root: Path, *, check_ids: tuple[str, ...]) -> dict[str, object]:
    """执行精确规范 ID 的完整依赖闭包；结果仅限诊断身份。"""
    from scripts.environment.services.isolated_postgres_redis import (
        isolated_test_services,
    )

    if not check_ids or len(check_ids) != len(set(check_ids)):
        raise ValueError("diagnosis requires unique exact Check IDs")
    root = root.resolve()
    document, declaration = load_declarations_snapshot(root)
    by_id = {check["check_id"]: check for check in document["checks"]}
    if set(check_ids) - set(by_id):
        raise ValueError("unknown-check-id")
    checks = _runtime_checks(
        resolve_module_dependencies([by_id[c] for c in check_ids], document["checks"])
    )
    with verification_window(root) as window:
        frozen, unchanged = _freeze_selection(root, checks, declaration)
        required = {name for check in checks for name in check["required_environment"]}
        with isolated_test_services(root, required, lambda _message: None):
            results = _execute(
                root,
                checks,
                frozen,
                unchanged,
                str(uuid.uuid4()),
                None,
                stop_on_blocked=False,
            )
            _verify_frozen_closure(root, checks, frozen, results)
        window.check(root)
    status, reason = aggregate_results(results)
    return {
        "kind": "diagnostic",
        "selected_result": status,
        "reason": reason,
        "full_repository_executed": False,
        "formal_eligible": False,
        "selected_report": {
            "checks": results,
            "selected_check_ids": [c["check_id"] for c in checks],
        },
    }


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """只接受精确 Check ID，不接受 change/base/expected-path 自定义范围。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-id", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        result = diagnose(
            root or Path(__file__).resolve().parents[3], check_ids=tuple(args.check_id)
        )
    except (ValueError, OSError) as exc:
        result = {
            "kind": "diagnostic",
            "selected_result": "FAIL",
            "reason": str(exc),
            "formal_eligible": False,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[result["selected_result"]]


if __name__ == "__main__":
    raise SystemExit(main())
