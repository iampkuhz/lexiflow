"""Run the complete agent-module regression suite and emit one JSON result."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import unittest
from pathlib import Path


def evaluate_suite(suite: unittest.TestSuite) -> dict[str, object]:
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    complete = result.testsRun > 0 and not result.skipped
    passed = result.wasSuccessful() and complete
    return {
        "status": "PASS" if passed else "FAIL",
        "checks_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "reason": "" if passed else "agent-module-tests-incomplete",
        "tool_output_sha256": hashlib.sha256(stream.getvalue().encode()).hexdigest(),
    }


def run(root: Path) -> dict[str, object]:
    suite = unittest.defaultTestLoader.discover(
        str(root / "tests" / "agents"), pattern="test_*.py", top_level_dir=str(root)
    )
    return evaluate_suite(suite)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    print(json.dumps(run(args.root.resolve()), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
