"""Qoder 公开命令的参数边界；不执行派发或读取运行记录。"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping


def parse_command(
    argv: list[str], handlers: Mapping[str, Callable]
) -> argparse.Namespace:
    """解析 Qoder 公开动作与精确 run/handoff 输入，不执行派发或读取结果。"""
    parser = argparse.ArgumentParser(description="Qoder CLI subtask entry")
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="Start a Qoder subtask")
    start.add_argument("--task", required=True, help="Path to task JSON file")
    start.add_argument(
        "--runtime-recovery-confirmed",
        action="store_true",
        help="Operator asserts account/access repair; not a health check or budget override",
    )

    preflight = commands.add_parser("preflight", help="Validate without starting")
    preflight.add_argument("--task", required=True, help="Path to task JSON file")
    preflight.add_argument(
        "--runtime-recovery-confirmed",
        action="store_true",
        help="Same operator assertion as start; no health check or budget override",
    )

    for name in ("status", "result", "validate-result", "ack"):
        command = commands.add_parser(name)
        command.add_argument("run_id", help="Run ID (UUID) from start or resume")

    resume = commands.add_parser("resume", help="Resume with saved session id")
    resume.add_argument("run_id", help="Run ID (UUID) from start")
    resume.add_argument("--followup", required=True, help="Path to followup JSON")
    resume.add_argument(
        "--runtime-recovery-confirmed",
        action="store_true",
        help="Operator asserts account/access repair; not a health check or budget override",
    )

    fallback = commands.add_parser(
        "record-fallback", help="Verify native Codex spawn result"
    )
    fallback.add_argument("--task", required=True, help="Original bounded handoff JSON")
    fallback.add_argument("--attempt-id", required=True)
    fallback.add_argument("--call-id", required=True)

    args = parser.parse_args(argv)
    args.func = handlers[args.command]
    return args
