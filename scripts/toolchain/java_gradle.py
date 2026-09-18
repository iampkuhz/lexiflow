"""Run the backend Gradle wrapper with the repository's exact Java runtime.

This launcher keeps Java selection out of agent prompts and shell profiles. It
accepts only the Java release and distribution declared by the product harness,
then replaces the current process with the checked Gradle wrapper invocation.
Colored output is the default even when a caller captures stdout; explicit
console options and Gradle's NO_COLOR handling remain authoritative.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping, Sequence


JAVA_FEATURE = "25"
JAVA_IMPLEMENTOR = "Eclipse Adoptium"
JAVA_IMPLEMENTOR_VERSION_PREFIX = "Temurin-25."
EXPLICIT_JAVA_HOME = "LEXIFLOW_JAVA_HOME"


class ToolchainError(RuntimeError):
    """Raised when the exact Java toolchain cannot be selected safely."""


def _parse_release(release_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = release_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ToolchainError(f"cannot read Java release metadata: {release_file}") from exc

    for line in lines:
        if not line or line.startswith("#"):
            continue
        key, separator, raw_value = line.partition("=")
        if not separator or not key or key in values:
            raise ToolchainError(f"malformed Java release metadata: {release_file}")
        value = raw_value.strip()
        if len(value) < 2 or value[0] != '"' or value[-1] != '"':
            raise ToolchainError(f"unquoted Java release metadata: {release_file}")
        values[key] = value[1:-1]
    return values


def validate_java_home(java_home: Path) -> Path:
    """Return a resolved Temurin 25 home or fail with one typed diagnostic."""

    resolved = java_home.expanduser().resolve()
    java_executable = resolved / "bin" / ("java.exe" if os.name == "nt" else "java")
    if not java_executable.is_file() or not os.access(java_executable, os.X_OK):
        raise ToolchainError(f"Java executable is missing or not executable: {java_executable}")

    release = _parse_release(resolved / "release")
    version = release.get("JAVA_VERSION", "")
    feature = version.split(".", 1)[0]
    if feature != JAVA_FEATURE:
        raise ToolchainError(f"Java {JAVA_FEATURE} required, found {version or 'unknown'} at {resolved}")
    if release.get("IMPLEMENTOR") != JAVA_IMPLEMENTOR:
        raise ToolchainError(
            f"{JAVA_IMPLEMENTOR} required, found {release.get('IMPLEMENTOR', 'unknown')} at {resolved}"
        )
    implementor_version = release.get("IMPLEMENTOR_VERSION", "")
    if not implementor_version.startswith(JAVA_IMPLEMENTOR_VERSION_PREFIX):
        raise ToolchainError(
            f"Temurin {JAVA_FEATURE} required, found {implementor_version or 'unknown'} at {resolved}"
        )
    return resolved


def resolve_java_home(repo_root: Path, environ: Mapping[str, str]) -> Path:
    """Select the explicit, repository-local, or inherited exact runtime."""

    explicit = environ.get(EXPLICIT_JAVA_HOME, "").strip()
    if explicit:
        return validate_java_home(Path(explicit))

    local_candidates = (
        repo_root / ".local" / "toolchains" / "jdk-25" / "Contents" / "Home",
        repo_root / ".local" / "toolchains" / "jdk-25",
    )
    for candidate in local_candidates:
        if candidate.exists():
            return validate_java_home(candidate)

    inherited = environ.get("JAVA_HOME", "").strip()
    if inherited:
        return validate_java_home(Path(inherited))

    raise ToolchainError(
        "Temurin 25 is unavailable; install the repository-local toolchain or set "
        f"{EXPLICIT_JAVA_HOME} to an exact JDK home"
    )


def gradle_invocation(repo_root: Path, arguments: Sequence[str]) -> tuple[Path, list[str]]:
    """Build the exact wrapper invocation without consulting PATH."""

    forbidden_options = {"-p", "--project-dir", "-I", "--init-script", "--include-build"}
    for argument in arguments:
        option = argument.split("=", 1)[0]
        if option in forbidden_options or (
            argument.startswith(("-p", "-I")) and not argument.startswith("--")
        ):
            raise ToolchainError("Gradle build root and initialization are fixed by the launcher")
    delivery = any(
        argument.rsplit(":", 1)[-1] in {"check", "qualityFull", "deliveryFull"}
        for argument in arguments
    )
    execution_arguments = list(arguments)
    console_configured = any(
        argument == "--console"
        or argument.startswith(("--console=", "-Dorg.gradle.console="))
        or argument == "-Dorg.gradle.console"
        or (
            argument == "-D"
            and index + 1 < len(arguments)
            and arguments[index + 1].split("=", 1)[0] == "org.gradle.console"
        )
        for index, argument in enumerate(arguments)
    )
    if not console_configured:
        execution_arguments.insert(0, "--console=colored")
    if delivery:
        if any(argument.split("=", 1)[0] in {
            "-x", "--exclude-task", "-m", "--dry-run", "--tests", "--build-cache"
        } or argument.startswith("-x") for argument in arguments):
            raise ToolchainError("Java delivery cannot exclude, filter, or skip required checks")
        for required_option in ("--rerun-tasks", "--no-build-cache"):
            if required_option not in execution_arguments:
                execution_arguments.append(required_option)
    backend = repo_root / "backend"
    wrapper = backend / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if not wrapper.is_file() or (os.name != "nt" and not os.access(wrapper, os.X_OK)):
        raise ToolchainError(f"Gradle wrapper is missing or not executable: {wrapper}")
    return backend, [str(wrapper), "-p", str(backend), *execution_arguments]


def main(
    arguments: Sequence[str] | None = None,
    *,
    repo_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Validate the runtime and replace this process with the Gradle wrapper."""

    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    source_environment = dict(os.environ if environ is None else environ)
    java_home = resolve_java_home(root, source_environment)
    _, command = gradle_invocation(root, list(sys.argv[1:] if arguments is None else arguments))
    child_environment = source_environment.copy()
    child_environment["JAVA_HOME"] = str(java_home)
    child_environment["PATH"] = os.pathsep.join(
        [str(java_home / "bin"), child_environment.get("PATH", "")]
    ).rstrip(os.pathsep)
    os.execve(command[0], command, child_environment)
    return 127


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ToolchainError as exc:
        print(f"Java toolchain FAIL: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
