"""Planning Catalog 静态检查及显式只读 Task 查询入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.checks.repository.catalog.task_catalog_rules import PlanningValidator


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """不带查询参数时执行原静态规划校验；显式 Task ID 只读查询上下文。"""
    parser = argparse.ArgumentParser(description="LexiFlow planning catalog validator")
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        metavar="ID",
        help="只读读取指定 Task（可重复；按输入顺序去重）",
    )
    parsed = parser.parse_args(argv)
    validator = PlanningValidator(root or Path(__file__).resolve().parents[4])
    validator.load_sources()
    result = (
        validator.query_tasks(parsed.task_id) if parsed.task_id else validator.run_all()
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}.get(result["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
