"""一次解析 Check 环境，严格分离私有执行值与公开诊断。"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from scripts.environment.toolchains.java_toolchain_selection import (
    JavaRuntimeError,
    resolve_java_home,
)


@dataclass(frozen=True)
class CheckEnvironment:
    """仅 diagnostic 可写入报告；values 只在当前解析事务内供子进程使用。"""

    values: Mapping[str, str] = field(repr=False)
    diagnostic: dict[str, Any]


def _detect_python() -> dict[str, Any]:
    return {
        "available": True,
        "executable": sys.executable,
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "in_virtualenv": bool(
            getattr(sys, "real_prefix", None) or sys.base_prefix != sys.prefix
        ),
    }


def _detect_tool(name: str, source: Mapping[str, str]) -> dict[str, Any]:
    path = shutil.which(name, path=source.get("PATH", ""))
    return {"available": path is not None, "path": path or ""}


def resolve_check_environment(
    root: str | Path,
    check_decl: Mapping[str, Any],
    environ: Mapping[str, str] | None = None,
) -> CheckEnvironment:
    """只读解析当前声明一次；缺项失败关闭，私有值不缓存且不写入诊断。"""
    repo = Path(root).resolve()
    source = dict(os.environ if environ is None else environ)
    values: dict[str, str] = {}
    details: dict[str, Any] = {}
    missing: list[str] = []
    for name in check_decl.get("required_environment", []):
        if name == "python3":
            info = _detect_python()
        elif name == "java-25-temurin":
            try:
                home = resolve_java_home(repo, source)
            except JavaRuntimeError:
                info = {
                    "available": False,
                    "source": "",
                    "path": "",
                    "reason": "required Java 25 Temurin is unavailable",
                }
            else:
                info = {"available": True, "source": "temurin-25", "path": ""}
                values["JAVA_HOME"] = str(home)
                values["PATH"] = os.pathsep.join(
                    [str(home / "bin"), source.get("PATH", "")]
                ).rstrip(os.pathsep)
        elif name in {
            "postgres-test-jdbc-url",
            "redis-test-endpoint",
            "candidate-runtime-request",
            "release-arm64-docker-host",
            "release-gradle-cache",
            "release-npm-cache",
        }:
            variable, origin = {
                "postgres-test-jdbc-url": (
                    "LEXIFLOW_POSTGRES_TEST_JDBC_URL",
                    "explicit-test-jdbc-url",
                ),
                "redis-test-endpoint": (
                    "LEXIFLOW_REDIS_TEST_ENDPOINT",
                    "explicit-test-endpoint",
                ),
                "candidate-runtime-request": (
                    "LEXIFLOW_CANDIDATE_RUNTIME_REQUEST",
                    "explicit-candidate-request",
                ),
                "release-arm64-docker-host": (
                    "LEXIFLOW_RELEASE_ARM64_DOCKER_HOST",
                    "explicit-unix-endpoint",
                ),
                "release-gradle-cache": (
                    "LEXIFLOW_RELEASE_GRADLE_CACHE",
                    "explicit-cache-path",
                ),
                "release-npm-cache": (
                    "LEXIFLOW_RELEASE_NPM_CACHE",
                    "explicit-cache-path",
                ),
            }[name]
            value = source.get(variable, "")
            if name != "candidate-runtime-request":
                value = value.strip()
            valid = bool(value)
            if name == "candidate-runtime-request":
                valid = valid and Path(value).is_absolute()
            elif name == "release-arm64-docker-host":
                valid = valid and value.startswith("unix://")
            elif name in {"release-gradle-cache", "release-npm-cache"}:
                valid = valid and Path(value).is_absolute() and Path(value).is_dir()
            info = {"available": valid, "source": origin if valid else "", "path": ""}
            if valid:
                values[variable] = value
        elif name == "python-package-yaml":
            # 仅 find_spec 预检；kernel 仍在安全子环境中执行实际 import。
            spec = importlib.util.find_spec("yaml")
            info = {"available": bool(spec and spec.origin), "path": ""}
        elif name == "posix-lock-tool":
            path = (
                "/usr/bin/lockf"
                if sys.platform == "darwin"
                else "/usr/bin/flock"
                if sys.platform.startswith("linux")
                else ""
            )
            info = {"available": bool(path and os.access(path, os.X_OK)), "path": path}
        elif name == "sha256-tool":
            path = shutil.which(
                "sha256sum", path=source.get("PATH", "")
            ) or shutil.which("shasum", path=source.get("PATH", ""))
            info = {"available": bool(path), "path": path or ""}
        else:
            info = _detect_tool(name, source)
        details[name] = info
        if not info["available"]:
            missing.append(name)
    return CheckEnvironment(
        values=MappingProxyType({} if missing else values),
        diagnostic={
            "status": "BLOCKED" if missing else "PASS",
            "missing": missing,
            "details": details,
        },
    )
