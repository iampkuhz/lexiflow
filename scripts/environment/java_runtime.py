"""为产品模块执行只读选择精确的 Temurin 25，不安装或改写环境。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

JAVA_FEATURE = "25"
JAVA_IMPLEMENTOR = "Eclipse Adoptium"
JAVA_IMPLEMENTOR_VERSION_PREFIX = "Temurin-25."
EXPLICIT_JAVA_HOME = "LEXIFLOW_JAVA_HOME"


class JavaRuntimeError(RuntimeError):
    """无法安全选定必需 Java 运行时的有界诊断。"""


def _parse_release(release_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = release_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise JavaRuntimeError(
            f"cannot read Java release metadata: {release_file}"
        ) from exc
    for line in lines:
        if not line or line.startswith("#"):
            continue
        key, separator, raw_value = line.partition("=")
        if not separator or not key or key in values:
            raise JavaRuntimeError(f"malformed Java release metadata: {release_file}")
        value = raw_value.strip()
        if len(value) < 2 or value[0] != '"' or value[-1] != '"':
            raise JavaRuntimeError(f"unquoted Java release metadata: {release_file}")
        values[key] = value[1:-1]
    return values


def validate_java_home(java_home: Path) -> Path:
    """校验并返回精确的 Temurin 25 home；不满足时抛出有界诊断。"""

    resolved = java_home.expanduser().resolve()
    executable = resolved / "bin" / ("java.exe" if os.name == "nt" else "java")
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise JavaRuntimeError(
            f"Java executable is missing or not executable: {executable}"
        )
    release = _parse_release(resolved / "release")
    version = release.get("JAVA_VERSION", "")
    if version.split(".", 1)[0] != JAVA_FEATURE:
        raise JavaRuntimeError(
            f"Java {JAVA_FEATURE} required, found {version or 'unknown'} at {resolved}"
        )
    if release.get("IMPLEMENTOR") != JAVA_IMPLEMENTOR:
        raise JavaRuntimeError(
            f"{JAVA_IMPLEMENTOR} required, found {release.get('IMPLEMENTOR', 'unknown')} at {resolved}"
        )
    implementation = release.get("IMPLEMENTOR_VERSION", "")
    if not implementation.startswith(JAVA_IMPLEMENTOR_VERSION_PREFIX):
        raise JavaRuntimeError(
            f"Temurin {JAVA_FEATURE} required, found {implementation or 'unknown'} at {resolved}"
        )
    return resolved


def resolve_java_home(repo_root: Path, environ: Mapping[str, str]) -> Path:
    """按显式配置、仓库本地、继承环境的顺序选择 Temurin 25；不进行安装。"""

    explicit = environ.get(EXPLICIT_JAVA_HOME, "").strip()
    if explicit:
        return validate_java_home(Path(explicit))
    for candidate in (
        repo_root / ".local" / "toolchains" / "jdk-25" / "Contents" / "Home",
        repo_root / ".local" / "toolchains" / "jdk-25",
    ):
        if candidate.exists():
            return validate_java_home(candidate)
    inherited = environ.get("JAVA_HOME", "").strip()
    if inherited:
        return validate_java_home(Path(inherited))
    raise JavaRuntimeError(
        "Temurin 25 is unavailable; install the repository-local toolchain or set "
        f"{EXPLICIT_JAVA_HOME} to an exact JDK home"
    )
