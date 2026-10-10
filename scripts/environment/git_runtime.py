"""验证显式准备的只读 Apple Git 子进程入口。"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

_RELATIVE_BIN = Path("tmp/quality/toolchains/git/bin")
_APPLE_GIT = "/usr/bin/git"
_MAX_PREPARED_WRAPPER_BYTES = 4096


def generate_prepared_git_wrapper(target: str | Path) -> bytes:
    """生成唯一受支持的 Git 包装器字节格式。"""
    path = Path(target)
    if not path.is_absolute():
        raise ValueError("prepared Git target must be absolute")
    return (
        f'#!/bin/sh\n# lexiflow-prepared-git\nexec {shlex.quote(str(path))} "$@"\n'
    ).encode()


def parse_prepared_git_wrapper(path: str | Path) -> Path | None:
    """只读识别本模块生成的固定 wrapper；其他脚本一律视为未知。"""
    wrapper = Path(path)
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(wrapper, flags)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or not os.access(wrapper, os.X_OK)
                or info.st_size > _MAX_PREPARED_WRAPPER_BYTES
            ):
                return None
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(_MAX_PREPARED_WRAPPER_BYTES + 1)
        finally:
            os.close(fd)
        real_wrapper = wrapper.resolve(strict=True)
        if len(raw) > _MAX_PREPARED_WRAPPER_BYTES:
            return None
        prefix = b"#!/bin/sh\n# lexiflow-prepared-git\nexec "
        suffix = b' "$@"\n'
        if not raw.startswith(prefix) or not raw.endswith(suffix):
            return None
        quoted = raw[len(prefix) : -len(suffix)].decode("utf-8")
        words = shlex.split(quoted)
        if len(words) != 1:
            return None
        target = Path(words[0])
        if not target.is_absolute() or generate_prepared_git_wrapper(target) != raw:
            return None
        # A direct self-target (and symlink aliases back to the wrapper) is never valid.
        target_real = target.resolve(strict=True)
        if target_real == real_wrapper:
            return None
        return target
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
        return None


def prepared_git_bin(
    root: str | Path, final_path: str, *, platform: str | None = None
) -> Path | None:
    """仅在已准备目录严格指向当前 xcrun Git 时返回其 bin。"""
    if (sys.platform if platform is None else platform) != "darwin":
        return None
    if shutil.which("git", path=final_path) != _APPLE_GIT:
        return None

    repo = Path(root).resolve()
    directory = repo
    try:
        for part in _RELATIVE_BIN.parts:
            directory = directory / part
            info = directory.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                return None
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                return None

        entries = list(directory.iterdir())
        if len(entries) != 1 or entries[0].name != "git":
            return None
        target = parse_prepared_git_wrapper(entries[0])
        if target is None:
            return None
        target = target.resolve(strict=True)
        target_info = target.stat()
        if not stat.S_ISREG(target_info.st_mode) or not os.access(target, os.X_OK):
            return None
        result = subprocess.run(
            ["/usr/bin/xcrun", "--find", "git"],
            env={"PATH": final_path},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        reported = result.stdout.strip()
        if result.returncode != 0 or not reported or not Path(reported).is_absolute():
            return None
        current = Path(reported).resolve(strict=True)
        if current != target:
            return None
        return directory
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        return None


def prepend_prepared_git(
    root: str | Path,
    final_path: str,
    check_decl: Mapping[str, object],
    *,
    platform: str | None = None,
) -> str:
    """满足显式复用合同才给子环境 PATH 前置 prepared Git。"""
    required = check_decl.get("required_environment", [])
    if check_decl.get("transaction_reuse") is not True:
        return final_path
    if not isinstance(required, list) or "git" not in required:
        return final_path
    directory = prepared_git_bin(root, final_path, platform=platform)
    return f"{directory}{os.pathsep}{final_path}" if directory else final_path
