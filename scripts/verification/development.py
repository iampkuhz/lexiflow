"""按真实风险执行完整开发验证；不签发正式验收或冒充发行基线。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.verification.coordination import WindowError, verification_window
from scripts.verification.risk import assess
from scripts.verification.scenarios import freeze_inputs, verify_frozen_inputs
from scripts.verification.scope import changed_paths, resolve_base


def _scopes(assessment: dict[str, Any]) -> list[str]:
    level = assessment["level"]
    if level in {"mechanical", "local-function"}:
        return ["development-change"]
    if level == "high-risk-engineering":
        return ["development-change", "development-baseline"]
    raise ValueError("development-risk-invalid")


def prepare_development(root: str | Path) -> dict[str, Any]:
    """由当前完整差异冻结风险所需全部视图，供入口准备精确的隔离测试环境。"""
    repo = Path(root).resolve()
    base = resolve_base(repo)
    if not changed_paths(repo, base):
        raise ValueError("no-changes-to-verify")
    assessment = assess(repo, base=base)
    if assessment["coverage_gaps"]:
        raise ValueError("development-coverage-gap")
    profiles = [
        freeze_inputs(repo, verification_scope=scope, base=assessment["base"])
        for scope in _scopes(assessment)
    ]
    for profile in profiles:
        if profile.get("result") != "PASS":
            raise ValueError(f"development-freeze-{profile.get('reason', 'invalid')}")
    return {"risk_assessment": assessment, "frozen_profiles": profiles}


def _verify_prepared(repo: Path, prepared: dict[str, Any]) -> None:
    original = prepared["risk_assessment"]
    current = assess(
        repo, base=original["base"], _subject_paths=tuple(original["changed_files"])
    )
    profiles = prepared["frozen_profiles"]
    if (
        current != original
        or original["coverage_gaps"]
        or [item.get("verification_scope") for item in profiles] != _scopes(current)
        or any(item.get("required_check_ids") != [] for item in profiles)
        or any(not verify_frozen_inputs(repo, item) for item in profiles)
        or profiles[0].get("changed_files") != original["changed_files"]
        or profiles[0].get("base") != original["base"]
    ):
        raise ValueError("development-input-drift")


def execute_development(
    root: str | Path,
    *,
    prepared: dict[str, Any],
    runner=None,
    _window=None,
) -> dict[str, Any]:
    """在实际持有的验证窗口执行已冻结开发视图，并于发布前重新核对相关输入。"""
    from scripts.verification import persist_report, verify_profiles
    from scripts.verification.summaries import summarize

    repo = Path(root).resolve()
    if _window is None:
        try:
            with verification_window(repo) as window:
                return execute_development(
                    repo, prepared=prepared, runner=runner, _window=window
                )
        except WindowError as exc:
            return _failure(exc.status, exc.code, str(exc))
    reports: list[dict[str, Any]] = []
    try:
        from scripts.verification.coordination import VerificationWindow

        if type(_window) is not VerificationWindow:
            raise WindowError("开发验证需要本次调用实际持有的窗口")
        _window.check(repo)
        _verify_prepared(repo, prepared)
        reports = verify_profiles(
            repo,
            frozen_profiles=prepared["frozen_profiles"],
            runner=runner,
            execution_mode="delivery",
            _window=_window,
        )
        try:
            _verify_prepared(repo, prepared)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            for report in reports:
                if report.get("result") == "PASS":
                    report.update(result="FAIL", reason="input-drift", detail=str(exc))
        _window.check(repo)
        for report in reports:
            report["publication"] = persist_report(repo, report)
        states = [report.get("result") for report in reports]
        result = (
            "FAIL" if "FAIL" in states else "BLOCKED" if "BLOCKED" in states else "PASS"
        )
        if len(reports) != len(prepared["frozen_profiles"]):
            result = "FAIL" if result == "FAIL" else "BLOCKED"
        return {
            "schema_version": "lexiflow.development-verification.v1",
            "result": result,
            "risk_level": prepared["risk_assessment"]["level"],
            "required_scopes": _scopes(prepared["risk_assessment"]),
            "reports": reports,
            "summaries": [summarize(report) for report in reports],
            "formal_receipt_issued": False,
        }
    except WindowError as exc:
        return _failure(exc.status, exc.code, str(exc), reports)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _failure("FAIL", "development-verification-invalid", str(exc), reports)


def _failure(status: str, reason: str, detail: str, reports=None) -> dict[str, Any]:
    return {
        "schema_version": "lexiflow.development-verification.v1",
        "result": status,
        "reason": reason,
        "detail": detail,
        "reports": reports or [],
        "formal_receipt_issued": False,
    }


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """无范围或风险覆盖参数的开发入口；独立命令缺环境即阻断，不自动安装服务。"""
    parser = argparse.ArgumentParser(prog="python3 -m scripts.verification.development")
    parser.parse_args(argv)
    repo = root or Path(__file__).resolve().parents[2]
    try:
        with verification_window(repo) as window:
            prepared = prepare_development(repo)
            result = execute_development(repo, prepared=prepared, _window=window)
    except WindowError as exc:
        result = _failure(exc.status, exc.code, str(exc))
    except (OSError, ValueError) as exc:
        result = _failure("BLOCKED", "development-preflight-blocked", str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}.get(result["result"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
