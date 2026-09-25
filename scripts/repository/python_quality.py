"""用固定 Ruff 合同只读检查 scripts；不安装工具、不修复源码、不签发验收。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


def run_docstrings(root: Path) -> dict[str, object]:
    """通过 Pylint 扫描 scripts 的公共定义，保留全部结构化诊断。"""
    report = {
        "status": "BLOCKED",
        "checks_run": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "reason": "pylint-unavailable",
        "detail": [],
    }
    if importlib.util.find_spec("pylint") is None:
        return report
    if not any((root / "scripts").rglob("*.py")):
        return {**report, "status": "FAIL", "reason": "python-docstring-sources-empty"}
    # Pylint 加载插件前会调整 sys.path；只为该子进程声明本仓包根。
    env = {**os.environ, "PYTHONPATH": str(root.resolve())}
    try:
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "pylint",
                "--rcfile",
                str(root / "harness/python-docstrings.toml"),
                "scripts",
            ],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            **report,
            "reason": "pylint-execution-unavailable",
            "detail": [str(exc)],
        }
    try:
        findings = json.loads(process.stdout)
        if not isinstance(findings, list) or any(
            not isinstance(item, dict)
            or not {"path", "line", "message-id", "message"} <= item.keys()
            for item in findings
        ):
            raise ValueError("invalid diagnostic schema")
    except (ValueError, TypeError):
        return {
            **report,
            "status": "FAIL",
            "errors": 1,
            "reason": "pylint-result-invalid",
            "detail": [(process.stdout + process.stderr)[-16000:]],
        }
    failed = bool(process.returncode or findings or process.stderr.strip())
    return {
        **report,
        "status": "FAIL" if failed else "PASS",
        "checks_run": 1,
        "failures": len(findings) if findings else int(failed),
        "reason": "python-docstrings-failed" if failed else "",
        "detail": findings,
        "exit_code": process.returncode,
        "tool_stderr": process.stderr[-16000:],
    }


def run(root: Path) -> dict[str, object]:
    """执行 lint 与 format 检查；缺工具或超时不能解释为检查通过。"""
    report: dict[str, object] = {
        "status": "BLOCKED",
        "checks_run": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "reason": "ruff-unavailable",
        "detail": [],
    }
    if importlib.util.find_spec("ruff") is None:
        return report
    if not any((root / "scripts").rglob("*.py")):
        return {**report, "status": "FAIL", "reason": "python-sources-empty"}
    config = root / "harness/python-quality.toml"
    commands = (
        ["check", "--output-format", "concise"],
        ["format", "--check"],
    )
    details = []
    for command in commands:
        try:
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    *command,
                    "--config",
                    str(config),
                    "--no-cache",
                    "scripts",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {
                **report,
                "status": "BLOCKED",
                "reason": "ruff-execution-unavailable",
                "detail": details,
            }
        report["checks_run"] += 1
        details.append(
            {
                "tool": f"ruff {command[0]}",
                "exit_code": process.returncode,
                "output": (process.stdout + process.stderr)[-16000:],
            }
        )
        if process.returncode:
            report["failures"] += 1
    return {
        **report,
        "status": "FAIL" if report["failures"] else "PASS",
        "reason": "python-quality-failed" if report["failures"] else "",
        "detail": details,
    }


def main(argv: list[str] | None = None) -> int:
    """执行完整的 Ruff 与 Pylint 脚本质量合同，不提供局部检查开关。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    from scripts.repository.quality import run as run_quality

    root = Path(__file__).resolve().parents[2]
    report = run_quality(root, "python")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
