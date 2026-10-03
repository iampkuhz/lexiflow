"""只读识别运行环境，并为子进程准备受控的执行输入。"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

from scripts.environment.java_runtime import JavaRuntimeError, resolve_java_home


def detect_python() -> dict[str, Any]:
    """探测 Python 运行时是否满足声明的 Check 环境。"""
    return {
        "available": True,
        "executable": sys.executable,
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "in_virtualenv": bool(
            getattr(sys, "real_prefix", None) or sys.base_prefix != sys.prefix
        ),
    }


def detect_tool(name: str) -> dict[str, Any]:
    """只读判断所需本机工具是否可执行，不自动安装。"""
    path = shutil.which(name)
    return {"available": path is not None, "path": path or ""}


def detect_postgres_test_jdbc_url(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """仅确认显式 PostgreSQL 测试目标存在，不泄露其值。"""

    source = os.environ if environ is None else environ
    available = bool(source.get("LEXIFLOW_POSTGRES_TEST_JDBC_URL", "").strip())
    return {
        "available": available,
        "source": "explicit-test-jdbc-url" if available else "",
        "path": "",
    }


def detect_redis_test_endpoint(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """要求显式隔离的 Redis 端点，不泄露其值。"""

    source = os.environ if environ is None else environ
    available = bool(source.get("LEXIFLOW_REDIS_TEST_ENDPOINT", "").strip())
    return {
        "available": available,
        "source": "explicit-test-endpoint" if available else "",
        "path": "",
    }


def detect_java_25_temurin(
    root: Path, environ: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """只读识别精确的 Java 25 Temurin；不安装，也不修改 shell profile。"""

    try:
        home = resolve_java_home(root, os.environ if environ is None else environ)
    except JavaRuntimeError as exc:
        return {"available": False, "source": "", "path": "", "reason": str(exc)}
    return {"available": True, "source": "temurin-25", "path": str(home)}


def detect_java(root: Path) -> dict[str, Any]:
    """探测 Java 25 运行环境并报告缺失原因。"""
    java_home = os.environ.get("LEXIFLOW_JAVA_HOME", "")
    if java_home and (Path(java_home) / "bin" / "java").is_file():
        return {"available": True, "source": "LEXIFLOW_JAVA_HOME", "path": java_home}
    local_jdk = root / ".local" / "toolchains" / "jdk-25"
    if (local_jdk / "bin" / "java").is_file():
        return {"available": True, "source": "local-toolchain", "path": str(local_jdk)}
    return {
        "available": bool(shutil.which("java")),
        "source": "PATH",
        "path": shutil.which("java") or "",
    }


def diagnose(
    root: str | Path = ".",
    required: list[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """按环境能力名称汇总可用性与来源供 Gate 使用。"""
    repo, source = Path(root).resolve(), (os.environ if environ is None else environ)
    tools: dict[str, Any] = {}
    missing: list[str] = []
    for name in ["python3", "git"] if required is None else required:
        if name == "python3":
            info = detect_python()
        elif name == "java":
            info = detect_java(repo)
        elif name == "java-25-temurin":
            info = detect_java_25_temurin(repo, source)
        elif name == "postgres-test-jdbc-url":
            info = detect_postgres_test_jdbc_url(source)
        elif name == "redis-test-endpoint":
            info = detect_redis_test_endpoint(source)
        elif name == "candidate-runtime-request":
            value = source.get("LEXIFLOW_CANDIDATE_RUNTIME_REQUEST", "")
            valid = bool(value and Path(value).is_absolute())
            info = {
                "available": valid,
                "source": "explicit-candidate-request" if valid else "",
                "path": "",
            }
        elif name in {
            "release-amd64-docker-host",
            "release-arm64-docker-host",
            "release-gradle-cache",
            "release-npm-cache",
        }:
            variable = (
                "LEXIFLOW_RELEASE_AMD64_DOCKER_HOST"
                if name == "release-amd64-docker-host"
                else "LEXIFLOW_RELEASE_ARM64_DOCKER_HOST"
            )
            if name in {"release-gradle-cache", "release-npm-cache"}:
                variable = (
                    "LEXIFLOW_RELEASE_GRADLE_CACHE"
                    if name == "release-gradle-cache"
                    else "LEXIFLOW_RELEASE_NPM_CACHE"
                )
                cache = source.get(variable, "").strip()
                valid = bool(
                    cache and Path(cache).is_absolute() and Path(cache).is_dir()
                )
                info = {
                    "available": valid,
                    "source": "explicit-cache-path" if valid else "",
                    "path": "",
                }
            else:
                endpoint = source.get(variable, "").strip()
                valid = endpoint.startswith("unix://")
                info = {
                    "available": valid,
                    "source": "explicit-unix-endpoint" if valid else "",
                    "path": "",
                }
        else:
            info = detect_tool(name)
        tools[name] = info
        if not info["available"]:
            missing.append(name)
    return {
        "status": "PASS" if not missing else "BLOCKED",
        "tools": tools,
        "missing": missing,
    }


def execution_environment(
    root: str | Path,
    check_decl: Mapping[str, Any],
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """仅返回已声明且校验过的单个子进程环境值；值始终不写入报告。"""

    repo, source = Path(root).resolve(), (os.environ if environ is None else environ)
    values: dict[str, str] = {}
    for name in check_decl.get("required_environment", []):
        if name == "java-25-temurin":
            home = resolve_java_home(repo, source)
            values["JAVA_HOME"] = str(home)
            values["PATH"] = os.pathsep.join(
                [str(home / "bin"), source.get("PATH", "")]
            ).rstrip(os.pathsep)
        elif name == "postgres-test-jdbc-url":
            value = source.get("LEXIFLOW_POSTGRES_TEST_JDBC_URL", "").strip()
            if not value:
                raise JavaRuntimeError(
                    "explicit PostgreSQL test JDBC URL is unavailable"
                )
            values["LEXIFLOW_POSTGRES_TEST_JDBC_URL"] = value
        elif name == "candidate-runtime-request":
            value = source.get("LEXIFLOW_CANDIDATE_RUNTIME_REQUEST", "")
            if not value or not Path(value).is_absolute():
                raise JavaRuntimeError(
                    "explicit candidate runtime request is unavailable"
                )
            values["LEXIFLOW_CANDIDATE_RUNTIME_REQUEST"] = value
        elif name in {
            "release-amd64-docker-host",
            "release-arm64-docker-host",
            "release-gradle-cache",
            "release-npm-cache",
        }:
            if name in {"release-gradle-cache", "release-npm-cache"}:
                variable = (
                    "LEXIFLOW_RELEASE_GRADLE_CACHE"
                    if name == "release-gradle-cache"
                    else "LEXIFLOW_RELEASE_NPM_CACHE"
                )
                value = source.get(variable, "").strip()
                if (
                    not value
                    or not Path(value).is_absolute()
                    or not Path(value).is_dir()
                ):
                    raise JavaRuntimeError(f"explicit {name} is unavailable")
                values[variable] = value
            else:
                variable = (
                    "LEXIFLOW_RELEASE_AMD64_DOCKER_HOST"
                    if name == "release-amd64-docker-host"
                    else "LEXIFLOW_RELEASE_ARM64_DOCKER_HOST"
                )
                value = source.get(variable, "").strip()
                if not value:
                    raise JavaRuntimeError(f"explicit {name} is unavailable")
                if not value.startswith("unix://"):
                    raise JavaRuntimeError(f"explicit {name} must be a Unix endpoint")
                values[variable] = value
        elif name == "redis-test-endpoint":
            value = source.get("LEXIFLOW_REDIS_TEST_ENDPOINT", "").strip()
            if not value:
                raise JavaRuntimeError("explicit Redis test endpoint is unavailable")
            values["LEXIFLOW_REDIS_TEST_ENDPOINT"] = value
    return values


def check_for(root: str | Path, check_decl: dict[str, Any]) -> dict[str, Any]:
    """核对单个 Check 的全部必需环境，缺项返回 BLOCKED 而不执行。"""
    diag = diagnose(root, list(check_decl.get("required_environment", [])))
    return {
        "status": diag["status"],
        "missing": diag["missing"],
        "details": diag["tools"],
    }
