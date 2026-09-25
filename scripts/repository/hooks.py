"""显式安装或诊断可选的非阻断 Hook 提醒；doctor 不修改 Git 配置。"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

HOOK_PATH = ".githooks"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def install(root: Path) -> dict[str, str]:
    """经显式调用安装非阻断 Git Hook 路径，不运行交付检查。"""
    hooks = root / HOOK_PATH
    required = (hooks / "pre-commit", hooks / "post-commit")
    if not hooks.is_dir() or any(not item.is_file() for item in required):
        raise ValueError("versioned hooks are unavailable")
    for item in required:
        item.chmod(item.stat().st_mode | 0o111)
    result = _git(root, "config", "core.hooksPath", HOOK_PATH)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "cannot configure core.hooksPath")
    return {"result": "PASS", "hooks_path": HOOK_PATH}


def doctor(root: Path) -> dict[str, str]:
    """只读核对 Git Hook 配置是否指向本仓受版本管理的提醒脚本。"""
    configured = _git(root, "config", "--get", "core.hooksPath")
    if configured.returncode == 0 and configured.stdout.strip() == HOOK_PATH:
        return {"result": "PASS", "hooks_path": HOOK_PATH}
    return {
        "result": "BLOCKED",
        "reason": "advisory-hooks-not-installed",
        "next_action": "python3 -m scripts.repository.hooks install",
    }


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """选择 Hook 诊断或显式安装动作并输出结果。"""
    parser = argparse.ArgumentParser(description="Manage LexiFlow versioned Git hooks")
    parser.add_argument("command", choices=("install", "doctor"))
    args = parser.parse_args(argv)
    try:
        repository = (root or Path(__file__).resolve().parents[2]).resolve()
        result = (
            install(repository) if args.command == "install" else doctor(repository)
        )
    except (OSError, ValueError) as exc:
        result = {"result": "FAIL", "reason": "hook-install-failed", "detail": str(exc)}
    import json

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[result["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
