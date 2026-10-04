"""有限声明驱动的本机工具链身份；仅用于同一 Verification transaction。"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shlex
import shutil
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_TOOLS = frozenset(
    {
        "python3",
        "node",
        "git",
        "sh",
        "/bin/sh",
        "bash",
        "mkfifo",
        "mktemp",
        "npm",
        "docker",
    }
)
_MAX_PACKAGE_FILES = 4096
_MAX_PACKAGE_BYTES = 64 * 1024 * 1024


class ToolchainUnavailable(ValueError):
    """工具缺失、身份未知或包边界超限时禁止复用。"""


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def _file_stamp(locator: str, *, executable: bool) -> dict[str, Any]:
    try:
        path = Path(locator)
        real = path.resolve(strict=True)
        info = real.stat()
        if not stat.S_ISREG(info.st_mode) or (
            executable and not os.access(real, os.X_OK)
        ):
            raise ToolchainUnavailable(f"tool is not a regular executable: {locator}")
        return {
            "path": str(path),
            "realpath": str(real),
            "sha256": _digest(real),
            "size_bytes": info.st_size,
            "mode": stat.S_IMODE(info.st_mode),
            "uid": info.st_uid,
            "gid": info.st_gid,
            "executable": executable,
        }
    except (OSError, RuntimeError) as exc:
        raise ToolchainUnavailable(
            f"tool identity unavailable: {locator}: {exc}"
        ) from None


def _executable_stamp(
    locator: str, env: Mapping[str, str], seen: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """在可执行文件为脚本时连同内核 shebang 解释器一并绑定。"""
    stamp = _file_stamp(locator, executable=True)
    real = stamp["realpath"]
    if real in seen or len(seen) >= 8:
        raise ToolchainUnavailable("executable shebang cycle or depth exceeded")
    try:
        with Path(real).open("rb") as stream:
            head = stream.readline(256)
    except OSError as exc:
        raise ToolchainUnavailable(f"executable shebang unavailable: {exc}") from None
    if not head.startswith(b"#!"):
        return stamp
    if len(head) == 256 and not head.endswith(b"\n"):
        raise ToolchainUnavailable("executable shebang too long")
    try:
        words = shlex.split(head[2:].decode("utf-8").strip())
    except (UnicodeDecodeError, ValueError):
        raise ToolchainUnavailable("executable shebang invalid") from None
    if not words or not Path(words[0]).is_absolute():
        raise ToolchainUnavailable("executable shebang interpreter unknown")
    next_seen = seen | {real}
    shebang: dict[str, Any] = {
        "argv": words,
        "interpreter": _executable_stamp(words[0], env, next_seen),
    }
    if Path(words[0]).name == "env":
        args = words[1:]
        if args and args[0] == "-S":
            args = args[1:]
        if not args or args[0].startswith("-") or "=" in args[0]:
            raise ToolchainUnavailable("env shebang selected tool unknown")
        shebang["selected"] = _named_tool(args[0], env, next_seen)
    stamp["shebang"] = shebang
    return stamp


def _named_tool(
    name: str, env: Mapping[str, str], seen: frozenset[str] = frozenset()
) -> dict[str, Any]:
    locator = shutil.which(name, path=env.get("PATH", ""))
    if not locator:
        raise ToolchainUnavailable(f"required tool unavailable: {name}")
    return _executable_stamp(locator, env, seen)


def _yaml_package() -> dict[str, Any]:
    """哈希实际 Python origin 下的有限 yaml 包，忽略普通生成的字节码缓存。"""
    spec = importlib.util.find_spec("yaml")
    if spec is None or not spec.origin or not spec.submodule_search_locations:
        raise ToolchainUnavailable("python-package-yaml origin unavailable")
    origin = Path(spec.origin).resolve(strict=True)
    roots = list(spec.submodule_search_locations)
    if len(roots) != 1:
        raise ToolchainUnavailable("python-package-yaml has ambiguous roots")
    package = Path(roots[0]).resolve(strict=True)
    if not package.is_dir() or not origin.is_relative_to(package):
        raise ToolchainUnavailable("python-package-yaml has unsafe origin")
    files: list[dict[str, Any]] = []
    total = 0
    for path in sorted(package.rglob("*")):
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink() and path.is_dir():
            raise ToolchainUnavailable("python-package-yaml contains symlink directory")
        if path.is_dir():
            continue
        if len(files) >= _MAX_PACKAGE_FILES:
            raise ToolchainUnavailable("python-package-yaml file limit exceeded")
        real = path.resolve(strict=True)
        if not real.is_relative_to(package):
            raise ToolchainUnavailable("python-package-yaml escaped package root")
        item = _file_stamp(str(path), executable=False)
        total += item["size_bytes"]
        if total > _MAX_PACKAGE_BYTES:
            raise ToolchainUnavailable("python-package-yaml byte limit exceeded")
        item["relative"] = str(path.relative_to(package))
        files.append(item)
    if not files or not any(Path(x["realpath"]) == origin for x in files):
        raise ToolchainUnavailable("python-package-yaml origin not in package")
    return {"origin": str(origin), "package_root": str(package), "files": files}


def _yaml_package_checked() -> dict[str, Any]:
    try:
        return _yaml_package()
    except ToolchainUnavailable:
        raise
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        raise ToolchainUnavailable(
            f"python-package-yaml identity unavailable: {exc}"
        ) from None


def resolve_toolchain(
    check: Mapping[str, Any], actual_child_env: Mapping[str, str]
) -> dict[str, Any]:
    """绑定真实执行器、已声明工具及受限的实际选择分支。"""
    path_value = actual_child_env.get("PATH", "")
    if not path_value or any(
        not entry or not Path(entry).is_absolute()
        for entry in path_value.split(os.pathsep)
    ):
        # 相对 PATH 在子进程 cwd 下解析；无法在此接口中证明其真实目标。
        raise ToolchainUnavailable("child PATH contains unresolved relative entries")
    command = check.get("command")
    if not isinstance(command, list) or not command or not isinstance(command[0], str):
        raise ToolchainUnavailable("check command unavailable")
    required = check.get("required_environment", [])
    if not isinstance(required, list) or any(not isinstance(x, str) for x in required):
        raise ToolchainUnavailable("required environment unavailable")
    # Python Check 的执行路径由 kernel 固定为 sys.executable；PATH python3 可不同。
    python_child = check.get("executable") == "python3" and command[0] == "python3"
    if python_child:
        executable = _executable_stamp(sys.executable, actual_child_env)
    elif command[0] in _TOOLS:
        executable = _named_tool(command[0], actual_child_env)
    else:
        raise ToolchainUnavailable(f"undeclared executor identity: {command[0]}")
    tools: dict[str, Any] = {}
    for name in sorted(set(required)):
        if name == "python3" and python_child:
            tools[name] = executable
        elif name in _TOOLS:
            tools[name] = _named_tool(name, actual_child_env)
        elif name == "python-package-yaml":
            tools[name] = _yaml_package_checked()
        elif name == "posix-lock-tool":
            selected = (
                "/usr/bin/lockf"
                if sys.platform == "darwin"
                else "/usr/bin/flock"
                if sys.platform.startswith("linux")
                else ""
            )
            if not selected:
                raise ToolchainUnavailable("posix lock tool platform unsupported")
            tools[name] = _executable_stamp(selected, actual_child_env)
        elif name == "sha256-tool":
            selected = (
                "sha256sum"
                if shutil.which("sha256sum", path=actual_child_env.get("PATH", ""))
                else "shasum"
            )
            tools[name] = {
                "selected": selected,
                "identity": _named_tool(selected, actual_child_env),
            }
        else:
            raise ToolchainUnavailable(f"unbounded reuse capability: {name}")
    # 所有启用复用的 Python 命令均由同一 yaml 解析声明；其实际安装身份必须绑定。
    if python_child and "python-package-yaml" not in tools:
        tools["python-package-yaml"] = _yaml_package_checked()
    return {"executor": executable, "tools": tools}
