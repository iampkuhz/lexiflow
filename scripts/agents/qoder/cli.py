"""Qoder 公开命令的参数边界；不执行派发或读取运行记录。"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping


def parse_command(
    argv: list[str], handlers: Mapping[str, Callable]
) -> argparse.Namespace:
    """解析 Qoder 公开动作与精确 run/handoff 输入，不执行派发或读取结果。"""
    parser = argparse.ArgumentParser(description="Qoder historical run entry")
    commands = parser.add_subparsers(dest="command", required=True)

    for name in ("status", "result", "validate-result", "ack"):
        command = commands.add_parser(name)
        command.add_argument("run_id", help="Exact historical run ID")

    fallback = commands.add_parser(
        "record-fallback", help="Verify native Codex spawn result"
    )
    fallback.add_argument("--task", required=True, help="Original bounded handoff JSON")
    fallback.add_argument("--attempt-id", required=True)
    fallback.add_argument("--call-id", required=True)

    args = parser.parse_args(argv)
    args.func = handlers[args.command]
    return args
