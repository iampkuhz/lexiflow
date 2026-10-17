"""`python3 -m scripts.acceptance` 的 CLI 入口。各命名场景保持独立执行和身份边界。"""

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
    from scripts.acceptance.task_operations.submit_task_for_acceptance import (
        SubmissionError,
        submit,
    )

    try:
        result = submit(
            args.repo_root,
            task_id=args.task_id,
            change_report_id=args.change_report_id,
            release_handoff=args.release_handoff,
            producer_run_id=args.producer_run_id,
        )
    except SubmissionError as exc:
        result = {"result": exc.status, "reason": exc.code, "detail": exc.detail}
    return _output(result)


def _cmd_validate(args: argparse.Namespace) -> int:
    from scripts.acceptance.task_operations.run_independent_task_validation import (
        ValidationError,
        validate_batch,
    )

    try:
        ids = args.submission_id
        validations = validate_batch(args.repo_root, submission_ids=ids)
        if len(ids) > 1:
            statuses = {item["result"] for item in validations}
            status = (
                "FAIL"
                if "FAIL" in statuses
                else ("BLOCKED" if "BLOCKED" in statuses else "PASS")
            )
            result = {"result": status, "validations": validations}
        else:
            result = validations[0]
    except ValidationError as exc:
        result = {"result": "BLOCKED", "reason": exc.code, "detail": exc.detail}
    return _output(result)


def _cmd_review(args: argparse.Namespace) -> int:
    from scripts.acceptance.task_operations.record_independent_task_review import (
        ReviewError,
        review,
    )

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
            validation_id=args.validation_id,
            findings=findings,
        )
    except ReviewError as exc:
        result = {"result": "BLOCKED", "reason": exc.code, "detail": exc.detail}
    return _output(result)


def _cmd_check(args: argparse.Namespace) -> int:
    from scripts.acceptance.task_operations.evaluate_task_acceptance import (
        CheckError,
        check_conditions,
    )

    try:
        result = check_conditions(
            args.repo_root,
            submission_id=args.submission_id,
        )
    except CheckError as exc:
        result = {"result": "BLOCKED", "reason": exc.code, "detail": exc.detail}
    return _output(result)


def _cmd_status(args: argparse.Namespace) -> int:
    from scripts.acceptance.task_operations.show_task_acceptance_status import (
        query_status,
    )

    result = query_status(args.repo_root, submission_id=args.submission_id)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _cmd_consume_existing(args: argparse.Namespace) -> int:
    from scripts.acceptance.evidence.verify_existing_acceptance_chain import (
        consume_existing_pass,
    )

    result = consume_existing_pass(args.repo_root, submission_id=args.submission_id)
    return _output(result)


def _cmd_consume_candidate(args: argparse.Namespace) -> int:
    from scripts.acceptance.evidence.verify_candidate_acceptance_evidence import (
        consume_candidate_pass,
    )

    return _output(
        consume_candidate_pass(
            args.repo_root,
            submission_id=args.submission_id,
            candidate_directory=args.candidate_directory,
        )
    )


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """分派正式送验、独立验证、独立审查及条件核对；各阶段保持身份隔离。"""
    parser = argparse.ArgumentParser(prog="scripts.acceptance")
    subparsers = parser.add_subparsers(dest="scenario", required=True)

    submit_parser = subparsers.add_parser("submit", help="Create formal submission")
    submit_parser.add_argument("--task-id", required=True)
    modes = submit_parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--change-report-id")
    modes.add_argument(
        "--release-handoff",
        action="store_true",
        help="Use the fixed local release workflow handoff",
    )
    submit_parser.add_argument(
        "--producer-run-id",
        default=None,
        help="Optional validated Codex run reference",
    )

    validate_parser = subparsers.add_parser(
        "validate", help="Run independent validation"
    )
    validate_parser.add_argument(
        "--submission-id",
        required=True,
        action="append",
        help="Repeat up to 16 times for bounded batch validation",
    )

    review_parser = subparsers.add_parser("review", help="Perform independent review")
    review_parser.add_argument("--validation-id", required=True)
    review_parser.add_argument("--findings-json", default=None)

    check_parser = subparsers.add_parser("check", help="Check Delivery Gate conditions")
    check_parser.add_argument("--submission-id", required=True)

    status_parser = subparsers.add_parser("status", help="Query submission status")
    status_parser.add_argument("--submission-id", required=True)

    consume_parser = subparsers.add_parser(
        "consume-existing", help="Consume and reverify an existing formal PASS chain"
    )
    consume_parser.add_argument("--submission-id", required=True)

    candidate_parser = subparsers.add_parser(
        "consume-candidate",
        help="Consume an existing PASS chain bound to a verified runtime candidate",
    )
    candidate_parser.add_argument("--submission-id", required=True)
    candidate_parser.add_argument("--candidate-directory", required=True)

    args = parser.parse_args(argv)
    args.repo_root = str((root or Path(__file__).resolve().parents[2]).resolve())
    handlers = {
        "submit": _cmd_submit,
        "validate": _cmd_validate,
        "review": _cmd_review,
        "check": _cmd_check,
        "status": _cmd_status,
        "consume-existing": _cmd_consume_existing,
        "consume-candidate": _cmd_consume_candidate,
    }
    return handlers[args.scenario](args)


if __name__ == "__main__":
    raise SystemExit(main())
