"""为冻结审查生成覆盖 tracked 与 untracked 主体的完整 Git patch。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from scripts.delivery_gate.records import RecordError, read_optional_bytes


def _git(root: Path, args: list[str], *, allowed=(0,)) -> bytes:
    try:
        process = subprocess.run(
            ["git", "--literal-pathspecs", *args],
            cwd=root,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecordError("review-patch-unavailable", str(exc)) from None
    if process.returncode not in allowed:
        raise RecordError(
            "review-patch-unavailable", process.stderr.decode(errors="replace")
        )
    return process.stdout


def _quoted_path(path: str) -> str:
    # Git 的 C 风格路径引用使用八进制字节，不把换行或引号解释为 patch 结构。
    value = []
    for byte in os.fsencode(path):
        if byte in (34, 92):
            value.append("\\" + chr(byte))
        elif byte < 32 or byte >= 127:
            value.append(f"\\{byte:03o}")
        else:
            value.append(chr(byte))
    return '"' + "".join(value) + '"'


def review_patch(root: Path, base: str, paths: list[str] | tuple[str, ...]) -> bytes:
    """读取精确主体生成审查附件；未跟踪文本、二进制和空文件均明确记录，不写 Git index。"""
    if not paths or list(paths) != sorted(set(paths)):
        raise RecordError(
            "review-patch-subject-invalid", "sorted nonempty subject required"
        )
    for path in paths:
        # 安全读取校验所有路径；目录 symlink 和特殊文件不能交给 Git 跟随。
        read_optional_bytes(root, path)
    tracked = _git(
        root,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--binary",
            base,
            "--",
            *paths,
        ],
    )
    untracked = set(
        os.fsdecode(
            _git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
        ).split("\0")
    )
    pieces = [tracked]
    for path in paths:
        if path not in untracked:
            continue
        data = read_optional_bytes(root, path)
        if data is None:
            raise RecordError("review-patch-input-drift", path)
        added = _git(
            root,
            [
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-index",
                "--binary",
                "--",
                "/dev/null",
                path,
            ],
            allowed=(0, 1),
        )
        if not added:
            if data:
                raise RecordError("review-patch-incomplete", path)
            mode = "100755" if (root / path).stat().st_mode & 0o111 else "100644"
            added = (
                f"diff --git {_quoted_path('a/' + path)} {_quoted_path('b/' + path)}\n"
                f"new file mode {mode}\nindex 0000000..e69de29\n"
            ).encode()
        if read_optional_bytes(root, path) != data:
            raise RecordError("review-patch-input-drift", path)
        pieces.append(added)
    return b"".join(pieces)
