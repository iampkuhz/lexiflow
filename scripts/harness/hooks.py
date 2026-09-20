"""Install and diagnose LexiFlow's optional, non-blocking reminder hooks."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


HOOK_PATH = ".githooks"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )


def install(root: Path) -> dict[str, str]:
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
    configured = _git(root, "config", "--get", "core.hooksPath")
    if configured.returncode == 0 and configured.stdout.strip() == HOOK_PATH:
        return {"result": "PASS", "hooks_path": HOOK_PATH}
    return {
        "result": "BLOCKED", "reason": "advisory-hooks-not-installed",
        "next_action": "python3 -m scripts.harness.hooks install",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage LexiFlow versioned Git hooks")
    parser.add_argument("command", choices=("install", "doctor"))
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    try:
        result = install(Path(args.root).resolve()) if args.command == "install" else doctor(Path(args.root).resolve())
    except (OSError, ValueError) as exc:
        result = {"result": "FAIL", "reason": "hook-install-failed", "detail": str(exc)}
    import json
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[result["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
