"""使用目录描述符安全枚举并摘要 Verify 声明的源码输入。"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any

_GENERATED_DIRS = frozenset({"__pycache__", ".gradle", "build", "node_modules"})
_PRIVATE_NAMES = frozenset({".git", ".local", ".npmrc", "secrets"})
_PRIVATE_ENV = re.compile(r"\.env(?:\..*)?\Z")


class UnsafeInput(OSError):
    """输入无法在安全边界内枚举或读取。"""


def _valid_relative(locator: Any) -> bool:
    if (
        not isinstance(locator, str)
        or not locator
        or locator.startswith("/")
        or "\\" in locator
    ):
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in locator):
        return False
    parts = locator.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _private(part: str) -> bool:
    return part in _PRIVATE_NAMES or _PRIVATE_ENV.fullmatch(part) is not None


def _require_primitives() -> None:
    if (
        not getattr(os, "supports_dir_fd", None)
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.listdir not in os.supports_fd
    ):
        raise UnsafeInput("descriptor-relative open unavailable")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise UnsafeInput("nofollow directory traversal unavailable")


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _open_root(root: Path) -> int:
    _require_primitives()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(root, flags)
    except OSError:
        raise UnsafeInput("repository root unavailable") from None
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode) or _identity(
            os.stat(root, follow_symlinks=False)
        ) != _identity(os.fstat(fd)):
            raise UnsafeInput("repository root changed")
    except OSError:
        os.close(fd)
        raise UnsafeInput("repository root unavailable") from None
    return fd


def _open_directory(parent: int, name: str) -> int:
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode):
            raise UnsafeInput("non-directory component")
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except OSError:
        raise UnsafeInput("unsafe directory component") from None
    try:
        _assert_directory(parent, name, fd, before)
    except OSError:
        os.close(fd)
        raise UnsafeInput("directory changed while opening") from None
    return fd


def _assert_directory(parent: int, name: str, fd: int, before: os.stat_result) -> None:
    """确认目录 fd 仍属于父目录中同名的原始入口。"""
    current = os.fstat(fd)
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (
        not stat.S_ISDIR(current.st_mode)
        or _identity(before) != _identity(current)
        or _identity(current) != _identity(named)
    ):
        raise UnsafeInput("directory entry changed")


def _read_file(parent: int, name: str, locator: str) -> dict[str, Any]:
    try:
        before_path = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before_path.st_mode):
            raise UnsafeInput("non-regular input")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except OSError:
        raise UnsafeInput("unsafe file input") from None
    try:
        before_fd = os.fstat(fd)
        if not stat.S_ISREG(before_fd.st_mode) or _identity(before_path) != _identity(
            before_fd
        ):
            raise UnsafeInput("input changed while opening")
        digest = hashlib.sha256()
        remaining = before_fd.st_size
        while True:
            chunk = os.read(fd, min(1024 * 1024, remaining + 1))
            if not chunk:
                break
            if len(chunk) > remaining:
                raise UnsafeInput("input grew while reading")
            digest.update(chunk)
            remaining -= len(chunk)
        if remaining:
            raise UnsafeInput("input shortened while reading")
        after_fd = os.fstat(fd)
        after_path = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if _identity(before_fd) != _identity(after_fd) or _identity(
            after_fd
        ) != _identity(after_path):
            raise UnsafeInput("input changed while reading")
        return {
            "locator": locator,
            "sha256": digest.hexdigest(),
            "size_bytes": after_fd.st_size,
            "executable": bool(after_fd.st_mode & 0o111),
        }
    finally:
        os.close(fd)


def _walk_directory(fd: int, locator: str, found: list[dict[str, Any]]) -> None:
    try:
        before = os.fstat(fd)
        names = sorted(os.listdir(fd))
        for name in names:
            child_locator = f"{locator}/{name}"
            if _private(name):
                raise UnsafeInput("private input entry")
            if name.endswith(".pyc"):
                continue
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError:
                raise UnsafeInput("directory entry unavailable") from None
            if stat.S_ISDIR(info.st_mode):
                if name in _GENERATED_DIRS:
                    continue
                child = _open_directory(fd, name)
                try:
                    _walk_directory(child, child_locator, found)
                    _assert_directory(fd, name, child, info)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                found.append(_read_file(fd, name, child_locator))
            else:
                raise UnsafeInput("non-regular directory entry")
        after = os.fstat(fd)
        if _identity(before) != _identity(after):
            raise UnsafeInput("directory changed while reading")
        if sorted(os.listdir(fd)) != names:
            raise UnsafeInput("directory membership changed while reading")
    except OSError as exc:
        if isinstance(exc, UnsafeInput):
            raise
        raise UnsafeInput("directory enumeration failed") from None


def snapshot_inputs(
    root: Path, check: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """枚举安全输入；失败仅暴露仓库相对 locator，不暴露底层异常。"""
    files: dict[str, dict[str, Any]] = {}
    missing: set[str] = set()
    locators = check.get("input_paths", [])
    if not isinstance(locators, list) or any(not isinstance(x, str) for x in locators):
        # 声明加载器也验证类型；直接调用不能通过丢弃坏项形成空成功闭包。
        return [], ["."]
    candidates = sorted(set(locators))
    try:
        root_fd = _open_root(root)
    except OSError:
        return [], candidates or ["."]
    try:
        root_identity = os.fstat(root_fd)
        for locator in candidates:
            if not _valid_relative(locator):
                missing.add(locator)
                continue
            parts = locator.split("/")
            if any(_private(part) for part in parts):
                missing.add(locator)
                continue
            if any(part in _GENERATED_DIRS or part.endswith(".pyc") for part in parts):
                # 目录递归可以剪枝生成物，显式声明则必须拒绝，不能静默跳过。
                missing.add(locator)
                continue
            directories: list[tuple[int, str, int, os.stat_result]] = []
            current = root_fd
            try:
                for part in parts[:-1]:
                    before = os.stat(part, dir_fd=current, follow_symlinks=False)
                    current_next = _open_directory(current, part)
                    directories.append((current, part, current_next, before))
                    current = current_next
                leaf = parts[-1]
                info = os.stat(leaf, dir_fd=current, follow_symlinks=False)
                found: list[dict[str, Any]] = []
                if stat.S_ISDIR(info.st_mode):
                    directory_fd = _open_directory(current, leaf)
                    try:
                        _walk_directory(directory_fd, locator, found)
                        _assert_directory(current, leaf, directory_fd, info)
                    finally:
                        os.close(directory_fd)
                elif stat.S_ISREG(info.st_mode):
                    found.append(_read_file(current, leaf, locator))
                else:
                    raise UnsafeInput("non-regular explicit input")
                for parent, name, fd, before in reversed(directories):
                    _assert_directory(parent, name, fd, before)
                if _identity(root_identity) != _identity(
                    os.fstat(root_fd)
                ) or _identity(root_identity) != _identity(
                    os.stat(root, follow_symlinks=False)
                ):
                    raise UnsafeInput("repository root changed")
                for item in found:
                    files[item["locator"]] = item
            except OSError:
                missing.add(locator)
            finally:
                for _parent, _name, fd, _before in reversed(directories):
                    os.close(fd)
    finally:
        os.close(root_fd)
    return [files[key] for key in sorted(files)], sorted(missing)
