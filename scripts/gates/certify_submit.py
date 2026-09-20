"""Direct producer-submission entrypoint for formal subject evidence."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.gates.local_verify import LocalVerifyError, certify_submit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/gates/certify_submit.py")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--confirm-scope-review", required=True)
    args = parser.parse_args(argv)
    try:
        result = certify_submit(args.repo_root, task_id=args.task_id, run_id=args.run_id,
                                confirm_scope_review=args.confirm_scope_review)
    except LocalVerifyError as exc:
        result = {"result": exc.result, "reasons": [exc.code], "detail": exc.detail}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[result["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
