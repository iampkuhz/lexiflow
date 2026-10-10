"""按真实风险执行完整开发验证；不签发正式验收或冒充发行基线。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.verification.execution.checkout_verification_window import (
    WindowError,
    verification_window,
)
from scripts.verification.planning.change_risk_assessment import assess
from scripts.verification.execution.check_plan_runner import (
    freeze_inputs,
    verify_frozen_inputs,
)
from scripts.verification.planning.check_scope_selection import (
    changed_paths,
    resolve_base,
)


def prepare_development(root: str | Path) -> dict[str, Any]:
    """由当前完整差异冻结风险所需全部视图，供入口准备精确的隔离测试环境。"""
    repo = Path(root).resolve()
    base = resolve_base(repo)
    if not changed_paths(repo, base):
        raise ValueError("no-changes-to-verify")
    assessment = assess(repo, base=base)
    if assessment["coverage_gaps"]:
        raise ValueError("development-coverage-gap")
    profile = freeze_inputs(
        repo, verification_scope="development-change", base=assessment["base"]
    )
    if profile.get("result") != "PASS":
        raise ValueError(f"development-freeze-{profile.get('reason', 'invalid')}")
    return {"risk_assessment": assessment, "frozen_profile": profile}


def _verify_prepared(repo: Path, prepared: dict[str, Any]) -> None:
    original = prepared["risk_assessment"]
    current = assess(
        repo, base=original["base"], _subject_paths=tuple(original["changed_files"])
    )
    profile = prepared["frozen_profile"]
    if (
        current != original
        or original["coverage_gaps"]
        or profile.get("verification_scope") != "development-change"
        or profile.get("required_check_ids") != []
        or not verify_frozen_inputs(repo, profile)
        or profile.get("changed_files") != original["changed_files"]
        or profile.get("base") != original["base"]
    ):
        raise ValueError("development-input-drift")


def execute_development(
    root: str | Path,
    *,
    prepared: dict[str, Any],
    progress_observer=None,
    _window=None,
) -> dict[str, Any]:
    """在实际持有的验证窗口执行已冻结开发视图，并于发布前重新核对相关输入。"""
    from scripts.verification import persist_report, verify_profile
    from scripts.verification.reports.verification_report_summary import summarize

    repo = Path(root).resolve()
    if _window is None:
        try:
            with verification_window(repo) as window:
                return execute_development(
                    repo,
                    prepared=prepared,
                    progress_observer=progress_observer,
                    _window=window,
                )
        except WindowError as exc:
            return _failure(exc.status, exc.code, str(exc))
    report: dict[str, Any] | None = None
    try:
        from scripts.verification.execution.checkout_verification_window import (
            VerificationWindow,
        )

        if type(_window) is not VerificationWindow:
            raise WindowError("开发验证需要本次调用实际持有的窗口")
        _window.check(repo)
        _verify_prepared(repo, prepared)
        report = verify_profile(
            repo,
            frozen_inputs=prepared["frozen_profile"],
            progress_observer=progress_observer,
            _window=_window,
        )
        try:
            _verify_prepared(repo, prepared)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report.update(result="FAIL", reason="input-drift", detail=str(exc))
        _window.check(repo)
        report["publication"] = persist_report(repo, report)
        return {
            "schema_version": "lexiflow.development-verification.v2",
            "result": report["result"],
            "risk_level": prepared["risk_assessment"]["level"],
            "report": report,
            "summary": summarize(report),
            "formal_receipt_issued": False,
        }
    except WindowError as exc:
        return _failure(exc.status, exc.code, str(exc), report)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _failure("FAIL", "development-verification-invalid", str(exc), report)


def _failure(status: str, reason: str, detail: str, report=None) -> dict[str, Any]:
    return {
        "schema_version": "lexiflow.development-verification.v2",
        "result": status,
        "reason": reason,
        "detail": detail,
        "report": report,
        "formal_receipt_issued": False,
    }


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """无范围或风险覆盖参数的开发入口；独立命令缺环境即阻断，不自动安装服务。"""
    parser = argparse.ArgumentParser(
        prog="python3 -m scripts.verification.commands.run_development_checks"
    )
    parser.parse_args(argv)
    repo = root or Path(__file__).resolve().parents[3]
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
