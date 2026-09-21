"""Read-only runtime detection and controlled execution inputs."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

from scripts.environment.java_runtime import JavaRuntimeError, resolve_java_home


def detect_python() -> dict[str, Any]:
    return {"available": True, "executable": sys.executable, "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", "in_virtualenv": bool(getattr(sys, "real_prefix", None) or sys.base_prefix != sys.prefix)}


def detect_tool(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    return {"available": path is not None, "path": path or ""}


def detect_postgres_test_jdbc_url(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Check only for an explicit target; never disclose its value."""

    source = os.environ if environ is None else environ
    available = bool(source.get("LEXIFLOW_POSTGRES_TEST_JDBC_URL", "").strip())
    return {"available": available, "source": "explicit-test-jdbc-url" if available else "", "path": ""}


def detect_redis_test_endpoint(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Require an explicit isolated Redis endpoint without disclosing its value."""

    source = os.environ if environ is None else environ
    available = bool(source.get("LEXIFLOW_REDIS_TEST_ENDPOINT", "").strip())
    return {"available": available, "source": "explicit-test-endpoint" if available else "", "path": ""}


def detect_java_25_temurin(root: Path, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Detect the exact Java runtime without installation or shell-profile mutation."""

    try:
        home = resolve_java_home(root, os.environ if environ is None else environ)
    except JavaRuntimeError as exc:
        return {"available": False, "source": "", "path": "", "reason": str(exc)}
    return {"available": True, "source": "temurin-25", "path": str(home)}


def detect_java(root: Path) -> dict[str, Any]:
    java_home = os.environ.get("LEXIFLOW_JAVA_HOME", "")
    if java_home and (Path(java_home) / "bin" / "java").is_file():
        return {"available": True, "source": "LEXIFLOW_JAVA_HOME", "path": java_home}
    local_jdk = root / ".local" / "toolchains" / "jdk-25"
    if (local_jdk / "bin" / "java").is_file():
        return {"available": True, "source": "local-toolchain", "path": str(local_jdk)}
    return {"available": bool(shutil.which("java")), "source": "PATH", "path": shutil.which("java") or ""}


def diagnose(root: str | Path = ".", required: list[str] | None = None, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    repo, source = Path(root).resolve(), (os.environ if environ is None else environ)
    tools: dict[str, Any] = {}
    missing: list[str] = []
    for name in (["python3", "git"] if required is None else required):
        if name == "python3": info = detect_python()
        elif name == "java": info = detect_java(repo)
        elif name == "java-25-temurin": info = detect_java_25_temurin(repo, source)
        elif name == "postgres-test-jdbc-url": info = detect_postgres_test_jdbc_url(source)
        elif name == "redis-test-endpoint": info = detect_redis_test_endpoint(source)
        else: info = detect_tool(name)
        tools[name] = info
        if not info["available"]: missing.append(name)
    return {"status": "PASS" if not missing else "BLOCKED", "tools": tools, "missing": missing}


def execution_environment(root: str | Path, check_decl: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return only explicitly declared, validated values for one child process.

    Values never appear in diagnostics or verification reports. Unknown declared
    tools add nothing, so the verifier does not encode product-specific keys.
    """

    repo, source = Path(root).resolve(), (os.environ if environ is None else environ)
    values: dict[str, str] = {}
    for name in check_decl.get("required_environment", []):
        if name == "java-25-temurin":
            home = resolve_java_home(repo, source)
            values["JAVA_HOME"] = str(home)
            values["PATH"] = os.pathsep.join([str(home / "bin"), source.get("PATH", "")]).rstrip(os.pathsep)
        elif name == "postgres-test-jdbc-url":
            value = source.get("LEXIFLOW_POSTGRES_TEST_JDBC_URL", "").strip()
            if not value:
                raise JavaRuntimeError("explicit PostgreSQL test JDBC URL is unavailable")
            values["LEXIFLOW_POSTGRES_TEST_JDBC_URL"] = value
        elif name == "redis-test-endpoint":
            value = source.get("LEXIFLOW_REDIS_TEST_ENDPOINT", "").strip()
            if not value:
                raise JavaRuntimeError("explicit Redis test endpoint is unavailable")
            values["LEXIFLOW_REDIS_TEST_ENDPOINT"] = value
    return values


def check_for(root: str | Path, check_decl: dict[str, Any]) -> dict[str, Any]:
    diag = diagnose(root, list(check_decl.get("required_environment", [])))
    return {"status": diag["status"], "missing": diag["missing"], "details": diag["tools"]}
