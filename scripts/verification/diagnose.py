"""定向 Verify 诊断；不发布可用于正式 Delivery Gate 的完整报告。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.verification import verify_changes, verify_repository


def diagnose(
    root: Path,
    mode: str,
    *,
    base: str | None = None,
    expected_paths: tuple[str, ...] = (),
    check_ids: tuple[str, ...] = (),
) -> dict[str, object]:
    """执行定向 Verify 诊断并剥离完整报告身份；所选 PASS 不是仓库 PASS。"""
    if mode == "change":
        report = verify_changes(root, base=base, expected_paths=expected_paths)
    elif mode == "repository":
        if not check_ids:
            raise ValueError("repository diagnosis requires at least one check ID")
        report = verify_repository(root, check_ids=check_ids)
    else:
        raise ValueError(f"unknown diagnosis mode: {mode}")
    # 内部 Verify API 会返回完整报告的字段形状；诊断输出不得暴露可被误认成
    # 完整 Repository Verify 的 result/scope/schema 或正式 publication。
    selected_report = {
        key: value
        for key, value in report.items()
        if key not in {"result", "scope", "schema_version", "publication"}
    }
    return {
        "kind": "diagnostic",
        "full_repository_executed": False,
        "selected_scope": report.get("scope"),
        "selected_result": report.get("result", "FAIL"),
        "selected_report": selected_report,
    }


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """解析定向诊断参数并输出不可用于正式 Delivery Gate 的结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    change = modes.add_parser("change")
    change.add_argument("--base")
    change.add_argument("--expected-path", action="append", default=[])
    repository = modes.add_parser("repository")
    repository.add_argument("--check-id", action="append", required=True)
    args = parser.parse_args(argv)
    result = diagnose(
        root or Path(__file__).resolve().parents[2],
        args.mode,
        base=getattr(args, "base", None),
        expected_paths=tuple(getattr(args, "expected_path", ())),
        check_ids=tuple(getattr(args, "check_id", ())),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}.get(result["selected_result"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
