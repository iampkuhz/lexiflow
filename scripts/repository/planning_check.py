"""Planning Catalog 的零参数静态检查入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.repository.planning_validator import PlanningValidator


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """加载当前仓库 Catalog 并输出静态规划校验结果。"""
    parser = argparse.ArgumentParser(description="LexiFlow planning catalog validator")
    parser.parse_args(argv)
    validator = PlanningValidator(root or Path(__file__).resolve().parents[2])
    validator.load_sources()
    result = validator.run_all()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}.get(result["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
