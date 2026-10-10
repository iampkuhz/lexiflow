"""Repository 模块的结构化 Check adapter；保留失败位置，不改变底层检查结论。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import unittest
from pathlib import Path

from scripts.checks.repository.documentation import check_documentation as docs_check
from scripts.checks.repository.python import check_python_quality as python_quality
from scripts.checks.repository.catalog.task_catalog_rules import PlanningValidator


def _report(
    status: str,
    checks_run: int,
    *,
    failures: int = 0,
    errors: int = 0,
    skipped: int = 0,
    reason: str = "",
    detail: object = None,
) -> dict[str, object]:
    return {
        "status": status,
        "checks_run": checks_run,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "reason": reason,
        "detail": detail,
    }


def evaluate_suite(suite: unittest.TestSuite) -> dict[str, object]:
    """将仓库单元测试转成 Gate JSON，零测试或跳过不能 PASS。"""
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    complete = result.testsRun > 0 and not result.skipped
    passed = result.wasSuccessful() and complete
    report = _report(
        "PASS" if passed else "FAIL",
        result.testsRun,
        failures=len(result.failures),
        errors=len(result.errors),
        skipped=len(result.skipped),
        reason="" if passed else "repository-module-tests-incomplete",
    )
    # 保留可定位的测试身份，不将可能带本机数据的 traceback 写入共享结果。
    report["detail"] = {
        "failed_tests": [test.id() for test, _ in result.failures],
        "error_tests": [test.id() for test, _ in result.errors],
        "skipped_tests": [test.id() for test, _ in result.skipped],
    }
    report["tool_output_sha256"] = hashlib.sha256(
        stream.getvalue().encode()
    ).hexdigest()
    return report


def run(root: Path, mode: str) -> dict[str, object]:
    """按 Harness 指定模式运行仓库模块检查，并保留各工具诊断。"""
    if mode == "python":
        return python_quality.run(root)
    if mode == "tests":
        suite = unittest.defaultTestLoader.discover(
            str(root / "tests" / "repository"),
            pattern="test_*.py",
            top_level_dir=str(root),
        )
        return evaluate_suite(suite)
    if mode == "docs":
        result = docs_check.run(root)
        failures = len(result.get("issues", []))
        return _report(
            result["status"],
            1,
            failures=failures,
            reason="" if not failures else "documentation-governance-failed",
            detail={
                "files": result.get("files", 0),
                "diagrams": result.get("diagrams", 0),
                "issues": result.get("issues", []),
            },
        )
    if mode == "planning":
        validator = PlanningValidator(root)
        validator.load_sources()
        result = validator.run_all()
        failures = len(result.get("errors", []))
        return _report(
            result["status"],
            len(result.get("checks_run", [])),
            failures=failures,
            reason="" if result["status"] == "PASS" else "planning-catalog-invalid",
            detail={
                "task_count": result.get("task_count", 0),
                "issues": result.get("errors", []),
            },
        )
    raise ValueError(f"unknown quality mode: {mode}")


def main(argv: list[str] | None = None) -> int:
    """作为机器适配器输出仓库模块检查结果；不充当人工业务入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("tests", "docs", "planning", "python"),
        required=True,
    )
    args = parser.parse_args(argv)
    try:
        report = run(Path(__file__).resolve().parents[3], args.mode)
    except Exception as exc:  # CLI 异常也必须返回可解析的结构化结果。
        report = _report(
            "FAIL", 0, errors=1, reason="quality-entry-error", detail=str(exc)
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
