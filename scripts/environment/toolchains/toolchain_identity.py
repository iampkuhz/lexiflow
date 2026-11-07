"""有限声明驱动的本机工具链身份；仅用于同一 Verification transaction。"""

from __future__ import annotations

import hashlib
import importlib.util
import importlib.metadata
import os
import shlex
import shutil
import stat
import subprocess
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
_QUALITY_DISTRIBUTIONS = {
    "python-package-ruff": ("ruff", "ruff"),
    "python-package-pylint": ("pylint", "pylint"),
    "python-package-astroid": ("astroid", "astroid"),
}
_PYTHON_QUALITY_COMMAND = [
    "python3",
    "-m",
    "scripts.checks.repository.run_repository_check_group",
    "--mode",
    "python",
]


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
    stamp = _executable_stamp(locator, env, seen)
    if (
        name == "git"
        and sys.platform == "darwin"
        and stamp["realpath"] == "/usr/bin/git"
    ):
        # Apple shim 字节不证明实际 Git；只读定位并绑定当前 payload。
        stamp["apple_dispatcher"] = _executable_stamp("/usr/bin/xcrun", env, seen)
        try:
            result = subprocess.run(
                ["/usr/bin/xcrun", "--find", "git"],
                env=dict(env),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise ToolchainUnavailable(
                "Apple Git payload selection unavailable"
            ) from None
        target = result.stdout.strip()
        if result.returncode or not target or not Path(target).is_absolute():
            raise ToolchainUnavailable("Apple Git payload selection invalid")
        stamp["apple_payload"] = _executable_stamp(
            target, env, seen | {stamp["realpath"]}
        )
    return stamp


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


def _exact_pin(distribution: str, repo_root: Path) -> str:
    """从仓库唯一开发依赖清单读取工具的单一精确 pin。"""
    try:
        lock = repo_root.resolve(strict=True) / "requirements-dev.txt"
        matches = [
            line.partition("==")[2].strip()
            for line in lock.read_text(encoding="utf-8").splitlines()
            if line.strip()
            and not line.lstrip().startswith("#")
            and line.split("==", 1)[0].strip().lower().replace("_", "-") == distribution
        ]
    except OSError as exc:
        raise ToolchainUnavailable(
            f"dependency pin source unavailable: {exc}"
        ) from None
    if len(matches) != 1 or not matches[0] or any(c in matches[0] for c in "<>=!~[]; "):
        raise ToolchainUnavailable(f"one exact {distribution} pin is required")
    return matches[0]


def _python_distribution(
    name: str, module: str, env: Mapping[str, str], repo_root: Path
) -> dict[str, Any]:
    """绑定实际导入模块及安装发行物文件，并拒绝非精确 pin。"""
    expected = _exact_pin(name, repo_root)
    try:
        dist = importlib.metadata.distribution(name)
        version = dist.version
        spec = importlib.util.find_spec(module)
    except (importlib.metadata.PackageNotFoundError, ImportError, ValueError) as exc:
        raise ToolchainUnavailable(
            f"python package {name} unavailable: {exc}"
        ) from None
    if version != expected:
        raise ToolchainUnavailable(f"python package {name} pin mismatch")
    if spec is None or not spec.origin or not spec.submodule_search_locations:
        raise ToolchainUnavailable(f"python package {name} import origin unavailable")
    roots = [
        Path(item).resolve(strict=True) for item in spec.submodule_search_locations
    ]
    if len(roots) != 1:
        raise ToolchainUnavailable(f"python package {name} has ambiguous roots")
    package = roots[0]
    origin = Path(spec.origin).resolve(strict=True)
    if not package.is_dir() or not origin.is_relative_to(package):
        raise ToolchainUnavailable(f"python package {name} has unsafe origin")
    files: list[dict[str, Any]] = []
    total = 0
    try:
        for path in sorted(package.rglob("*")):
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_dir():
                if path.is_symlink():
                    raise ToolchainUnavailable(
                        f"python package {name} has symlink directory"
                    )
                continue
            if len(files) >= _MAX_PACKAGE_FILES:
                raise ToolchainUnavailable(f"python package {name} file limit exceeded")
            real = path.resolve(strict=True)
            if not real.is_relative_to(package):
                raise ToolchainUnavailable(f"python package {name} escaped root")
            item = _file_stamp(str(path), executable=False)
            total += item["size_bytes"]
            if total > _MAX_PACKAGE_BYTES:
                raise ToolchainUnavailable(f"python package {name} byte limit exceeded")
            item["relative"] = str(path.relative_to(package))
            files.append(item)
    except (OSError, RuntimeError) as exc:
        raise ToolchainUnavailable(
            f"python package {name} scan failed: {exc}"
        ) from None
    if not files or not any(x["realpath"] == str(origin) for x in files):
        raise ToolchainUnavailable(f"python package {name} origin not in payload")
    dependencies = _distribution_dependency_payload(dist)
    normalized_name = name.lower().replace("_", "-")
    direct_dist = next(
        (item for item in dependencies if item["distribution"] == normalized_name),
        None,
    )
    if direct_dist is None or not any(
        item["realpath"] == str(origin) for item in direct_dist["files"]
    ):
        raise ToolchainUnavailable(
            f"python package {name} import origin is not owned by its distribution"
        )
    result = {
        "distribution": name,
        "version": version,
        "pin": expected,
        "module": module,
        "origin": str(origin),
        "package_root": str(package),
        "files": files,
        "dependencies": dependencies,
    }
    if name == "ruff":
        try:
            from ruff import find_ruff_bin

            binary = find_ruff_bin()
        except (ImportError, OSError, RuntimeError) as exc:
            raise ToolchainUnavailable(
                f"ruff binary selection unavailable: {exc}"
            ) from None
        result["selected_binary"] = _executable_stamp(binary, env)
    return result


def _distribution_dependency_payload(dist: Any) -> list[dict[str, Any]]:
    """绑定实际安装发行物元数据及默认依赖闭包的全部文件。"""
    try:
        from pip._vendor.packaging.markers import default_environment
        from pip._vendor.packaging.requirements import Requirement

        parser_dist = importlib.metadata.distribution("pip")
        marker_spec = importlib.util.find_spec("pip._vendor.packaging.markers")
        requirement_spec = importlib.util.find_spec(
            "pip._vendor.packaging.requirements"
        )
    except (ImportError, importlib.metadata.PackageNotFoundError):
        raise ToolchainUnavailable(
            "Python dependency marker parser unavailable (pip is required)"
        ) from None
    pip_root = Path(parser_dist.locate_file("pip")).resolve(strict=True)
    for spec in (marker_spec, requirement_spec):
        if (
            spec is None
            or not spec.origin
            or not Path(spec.origin).resolve(strict=True).is_relative_to(pip_root)
        ):
            raise ToolchainUnavailable(
                "pip marker parser source is outside its distribution"
            )
    queue = [dist, parser_dist]
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    total = 0
    file_count = 0
    while queue:
        current = queue.pop(0)
        key = current.metadata["Name"].lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        if len(seen) > 64:
            raise ToolchainUnavailable("python dependency closure limit exceeded")
        files: list[dict[str, Any]] = []
        try:
            listed = current.files
            if listed is None:
                raise ToolchainUnavailable(f"distribution file list unavailable: {key}")
            for relative in sorted(listed, key=str):
                path = Path(current.locate_file(relative))
                if "__pycache__" in Path(str(relative)).parts or str(relative).endswith(
                    (".pyc", ".pyo")
                ):
                    continue
                if not path.exists():
                    raise ToolchainUnavailable(
                        f"distribution payload file missing: {key}:{relative}"
                    )
                if path.is_dir():
                    continue
                real = path.resolve(strict=True)
                allowed_roots = _python_install_roots()
                if not any(real.is_relative_to(root) for root in allowed_roots):
                    raise ToolchainUnavailable(
                        f"distribution payload escaped interpreter roots: {key}:{relative}"
                    )
                item = _file_stamp(str(path), executable=False)
                total += item["size_bytes"]
                if total > _MAX_PACKAGE_BYTES or file_count >= _MAX_PACKAGE_FILES:
                    raise ToolchainUnavailable(
                        "python dependency payload limit exceeded"
                    )
                item["relative"] = str(relative)
                files.append(item)
                file_count += 1
            if not files:
                raise ToolchainUnavailable(f"distribution payload unavailable: {key}")
            result.append(
                {"distribution": key, "version": current.version, "files": files}
            )
            for raw in current.requires or []:
                requirement = Requirement(raw)
                if requirement.marker is not None and not requirement.marker.evaluate(
                    default_environment()
                ):
                    continue
                try:
                    queue.append(importlib.metadata.distribution(requirement.name))
                except importlib.metadata.PackageNotFoundError:
                    raise ToolchainUnavailable(
                        f"python dependency unavailable: {requirement.name}"
                    ) from None
        except ToolchainUnavailable:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise ToolchainUnavailable(
                f"python dependency identity unavailable: {key}: {exc}"
            ) from None
    return result


def _python_install_roots() -> set[Path]:
    """返回当前解释器及其基础解释器的可信安装根。"""
    return {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}


def resolve_toolchain(
    check: Mapping[str, Any],
    actual_child_env: Mapping[str, str],
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """绑定工具身份；quality 入口须显式传入目标仓库根目录。"""
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
    python_quality = (
        check.get("executable") == "python3" and command == _PYTHON_QUALITY_COMMAND
    )
    quality_family = (
        check.get("executable") == "python3"
        and command[:3] == _PYTHON_QUALITY_COMMAND[:3]
    )
    known_non_quality = any(
        command == [*_PYTHON_QUALITY_COMMAND[:-1], mode]
        for mode in ("docs", "planning")
    )
    if quality_family and not python_quality and not known_non_quality:
        raise ToolchainUnavailable("unrecognized repository Python quality entry")
    if python_quality:
        if repo_root is None:
            raise ToolchainUnavailable(
                "python quality identity requires target repo_root"
            )
        try:
            repo_root = Path(repo_root).resolve(strict=True)
        except (OSError, RuntimeError, TypeError):
            raise ToolchainUnavailable(
                "python quality target repo_root unavailable"
            ) from None
        if not repo_root.is_dir():
            raise ToolchainUnavailable(
                "python quality target repo_root is not a directory"
            )
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
        elif name in _QUALITY_DISTRIBUTIONS:
            raise ToolchainUnavailable(f"unrecognized declared capability: {name}")
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
    if python_quality:
        assert repo_root is not None
        for token, (distribution, module) in sorted(_QUALITY_DISTRIBUTIONS.items()):
            tools[token] = _python_distribution(
                distribution, module, actual_child_env, repo_root
            )
    # 所有启用复用的 Python 命令均由同一 yaml 解析声明；其实际安装身份必须绑定。
    if python_child and "python-package-yaml" not in tools:
        tools["python-package-yaml"] = _yaml_package_checked()
    return {"executor": executable, "tools": tools}
