"""Repository verification CLI entry point.

Runs all declared repository-baseline checks and aggregates results.
This is a thin wrapper over ``scripts.verification.verify_repository``.

Usage::

    python3 scripts/check_repository.py [--repo-root .]
        [--check-id ID ...]

Does NOT import scripts.gates or scripts.harness.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verification import verify_repository, persist_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/check_repository.py",
        description="Run all declared repository-baseline checks.",
    )
    parser.add_argument(
        "--repo-root", default=".",
        help="Repository root directory (default: .)",
    )
    parser.add_argument(
        "--check-id", action="append", default=None,
        dest="check_ids",
        help="Restrict to specific check IDs (repeatable)",
    )
    args = parser.parse_args(argv)

    report = verify_repository(
        args.repo_root,
        check_ids=args.check_ids,
    )
    try:
        report["publication"] = persist_report(args.repo_root, report)
    except ValueError as exc:
        report = {**report, "result": "FAIL", "reason": "report-publication-failed", "detail": str(exc)}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))

    result = report.get("result", "FAIL")
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}.get(result, 1)


if __name__ == "__main__":
    raise SystemExit(main())
