"""按精确 run ID 核对 Codex 工作包原始产物的薄命令入口。"""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.agents.codex.work_package import (
    CodexWorkPackageError,
    CodexWorkPackagePublisher,
    canonical_json_bytes,
)
from scripts.agents.contracts import AgentContractError


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """解析精确 run ID、调用发布物核对，不推断最新运行或签发 Delivery Gate。"""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = CodexWorkPackagePublisher(
            root or Path(__file__).resolve().parents[3]
        ).verify(args.run_id)
    except (CodexWorkPackageError, AgentContractError, OSError) as exc:
        result = {
            "status": "FAIL",
            "work_package_id": None,
            "task_ids": [],
            "run_id": args.run_id,
            "artifact_locators": {},
            "validation_commands": [],
            "blocking_findings": [getattr(exc, "code", "artifact-unavailable")],
        }
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
