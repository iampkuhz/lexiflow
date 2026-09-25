"""Change Verify CLI 入口。按 Git 差异选择检查，输出报告；不签发正式 Delivery Gate receipt。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verification import verify_changes, persist_report


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """按当前 Git 差异执行 Change Verify 并发布报告；不签发正式验收收据。"""
    parser = argparse.ArgumentParser(
        prog="scripts/check_changes.py",
        description="Run checks selected by git diff.",
    )
    parser.parse_args(argv)
    repo_root = root or Path(__file__).resolve().parents[1]

    report = verify_changes(repo_root)
    try:
        report["publication"] = persist_report(repo_root, report)
    except ValueError as exc:
        report = {
            **report,
            "result": "FAIL",
            "reason": "report-publication-failed",
            "detail": str(exc),
        }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))

    result = report.get("result", "FAIL")
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}.get(result, 1)


if __name__ == "__main__":
    raise SystemExit(main())
