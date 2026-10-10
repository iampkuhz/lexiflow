"""用固定 Ruff 与 Pylint 合同只读检查工程 Python 源码；不安装工具、不修复源码、不签发验收。"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


PYTHON_SOURCE_ROOTS = ("scripts", "ops/release", "ops/dataset")


PYLINT_ARGUMENTS = (
    "--load-plugins=scripts.checks.repository.python.chinese_docstring_rule",
    "--recursive=y",
    "--persistent=n",
    "--jobs=1",
    "--disable=all",
    "--enable=missing-chinese-docstring,non-chinese-docstring,syntax-error,"
    "parse-error,fatal,astroid-error,bad-plugin-value,unrecognized-option",
    "--reports=n",
    "--score=n",
    "--output-format=json",
)


def _run_docstrings(root: Path) -> dict[str, object]:
    """通过 Pylint 扫描工程 Python 源码的公共定义，保留全部结构化诊断。"""
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
    if not any(
        path for source in PYTHON_SOURCE_ROOTS for path in (root / source).rglob("*.py")
    ):
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
                os.devnull,
                *PYLINT_ARGUMENTS,
                *PYTHON_SOURCE_ROOTS,
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


def _run_ruff(root: Path) -> dict[str, object]:
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
    if not any(
        path for source in PYTHON_SOURCE_ROOTS for path in (root / source).rglob("*.py")
    ):
        return {**report, "status": "FAIL", "reason": "python-sources-empty"}
    # 依赖锁是唯一版本来源；删掉 Ruff 配置副本不代表接受任意已安装版本。
    try:
        pins = [
            line.removeprefix("ruff==").strip()
            for line in (root / "requirements-dev.txt").read_text().splitlines()
            if line.startswith("ruff==")
        ]
        if len(pins) != 1 or not pins[0]:
            raise ValueError("one exact Ruff dependency pin is required")
        if importlib.metadata.version("ruff") != pins[0]:
            return {**report, "reason": "ruff-version-mismatch"}
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError):
        return {**report, "reason": "ruff-version-unavailable"}
    config = root / "config/engineering/python-quality.toml"
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
                    *PYTHON_SOURCE_ROOTS,
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


def run(root: Path) -> dict[str, object]:
    """执行唯一完整三检查合同，保留工具诊断和失败优先聚合。"""
    style, docs = _run_ruff(root), _run_docstrings(root)
    statuses = {style["status"], docs["status"]}
    status = (
        "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS"
    )
    return {
        "status": status,
        **{
            field: style[field] + docs[field]
            for field in ("checks_run", "failures", "errors", "skipped")
        },
        "reason": "" if status == "PASS" else "python-quality-incomplete",
        "detail": {"ruff": style, "docstrings": docs},
    }


def main(argv: list[str] | None = None) -> int:
    """执行完整的 Ruff 与 Pylint 脚本质量合同，不提供局部检查开关。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    root = Path(__file__).resolve().parents[4]
    report = run(root)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
