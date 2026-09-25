"""`python3 -m scripts.delivery_gate` 的 CLI 入口。各命名场景保持独立执行和身份边界。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _output(result: dict) -> int:
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    status = result.get("result", "FAIL")
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}.get(status, 1)


def _cmd_submit(args: argparse.Namespace) -> int:
    from scripts.delivery_gate.submit import submit, SubmissionError

    try:
        result = submit(
            args.repo_root,
            task_id=args.task_id,
            change_report_id=args.change_report_id,
            confirm_scope_report_id=args.confirm_scope_report_id,
            producer_run_id=args.producer_run_id,
        )
    except SubmissionError as exc:
        result = {"result": "BLOCKED", "reason": exc.code, "detail": exc.detail}
    return _output(result)


def _cmd_validate(args: argparse.Namespace) -> int:
    from scripts.delivery_gate.validate import validate, ValidationError

    try:
        result = validate(
            args.repo_root,
            submission_id=args.submission_id,
        )
    except ValidationError as exc:
        result = {"result": "BLOCKED", "reason": exc.code, "detail": exc.detail}
    return _output(result)


def _cmd_review(args: argparse.Namespace) -> int:
    from scripts.delivery_gate.review import review, ReviewError

    try:
        try:
            findings = (
                json.loads(Path(args.findings_json).read_text())
                if args.findings_json
                else []
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReviewError("findings-invalid", str(exc)) from None
        result = review(
            args.repo_root,
            submission_id=args.submission_id,
            validation_id=args.validation_id,
            findings=findings,
            decision=args.decision,
        )
    except ReviewError as exc:
        result = {"result": "BLOCKED", "reason": exc.code, "detail": exc.detail}
    return _output(result)


def _cmd_check(args: argparse.Namespace) -> int:
    from scripts.delivery_gate.check import check_conditions, CheckError

    try:
        result = check_conditions(
            args.repo_root,
            submission_id=args.submission_id,
        )
    except CheckError as exc:
        result = {"result": "BLOCKED", "reason": exc.code, "detail": exc.detail}
    return _output(result)


def _cmd_status(args: argparse.Namespace) -> int:
    from scripts.delivery_gate.status import query_status

    result = query_status(args.repo_root, submission_id=args.submission_id)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """分派正式送验、独立验证、独立审查及条件核对；各阶段保持身份隔离。"""
    parser = argparse.ArgumentParser(prog="scripts.delivery_gate")
    subparsers = parser.add_subparsers(dest="scenario", required=True)

    submit_parser = subparsers.add_parser("submit", help="Create formal submission")
    submit_parser.add_argument("--task-id", required=True)
    submit_parser.add_argument("--change-report-id", required=True)
    submit_parser.add_argument("--confirm-scope-report-id", required=True)
    submit_parser.add_argument(
        "--producer-run-id",
        default=None,
        help="Optional validated Codex/Qoder run reference",
    )

    validate_parser = subparsers.add_parser(
        "validate", help="Run independent validation"
    )
    validate_parser.add_argument("--submission-id", required=True)

    review_parser = subparsers.add_parser("review", help="Perform independent review")
    review_parser.add_argument("--submission-id", required=True)
    review_parser.add_argument("--validation-id", required=True)
    review_parser.add_argument("--findings-json", default=None)
    review_parser.add_argument(
        "--decision", required=True, choices=("PASS", "BLOCKED", "FAIL")
    )

    check_parser = subparsers.add_parser("check", help="Check Delivery Gate conditions")
    check_parser.add_argument("--submission-id", required=True)

    status_parser = subparsers.add_parser("status", help="Query submission status")
    status_parser.add_argument("--submission-id", required=True)

    args = parser.parse_args(argv)
    args.repo_root = str((root or Path(__file__).resolve().parents[2]).resolve())
    handlers = {
        "submit": _cmd_submit,
        "validate": _cmd_validate,
        "review": _cmd_review,
        "check": _cmd_check,
        "status": _cmd_status,
    }
    return handlers[args.scenario](args)


if __name__ == "__main__":
    raise SystemExit(main())
