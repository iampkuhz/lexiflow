#!/usr/bin/env python3
"""Module-owned result adapter for the verification test suite.

The kernel only validates this JSON protocol.  It has no knowledge of unittest,
which keeps tool-specific completeness rules in the owning module.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from pathlib import Path


def verification_tests(root: Path) -> dict[str, object]:
    suite = unittest.defaultTestLoader.discover(str(root / "tests" / "verification"), pattern="test_*.py", top_level_dir=str(root))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    return {
        "status": "PASS" if result.wasSuccessful() and result.testsRun > 0 and not result.skipped else "FAIL",
        "checks_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "reason": "" if result.wasSuccessful() and result.testsRun > 0 and not result.skipped else "verification-module-tests-incomplete",
        "tool_output_sha256": __import__("hashlib").sha256(stream.getvalue().encode("utf-8")).hexdigest(),
    }


def pending_product_coverage() -> dict[str, object]:
    return {"status": "BLOCKED", "reason": "product-quality-checks-not-migrated"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--mode", choices=("verification-tests", "pending-product-coverage"), required=True)
    args = parser.parse_args(argv)
    report = verification_tests(Path(args.root).resolve()) if args.mode == "verification-tests" else pending_product_coverage()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
