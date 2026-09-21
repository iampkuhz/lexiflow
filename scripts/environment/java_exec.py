"""Execute a caller-owned command with the selected Temurin 25 runtime."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

from scripts.environment.java_runtime import JavaRuntimeError, resolve_java_home


def command_environment(root: Path, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a child environment that puts the exact JDK first on PATH."""

    source = dict(os.environ if environ is None else environ)
    java_home = resolve_java_home(root, source)
    source["JAVA_HOME"] = str(java_home)
    source["PATH"] = os.pathsep.join([str(java_home / "bin"), source.get("PATH", "")]).rstrip(os.pathsep)
    return source


def main(arguments: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    """Replace this process with the caller-owned command under Temurin 25."""

    argv = list(sys.argv[1:] if arguments is None else arguments)
    if not argv:
        raise JavaRuntimeError("java_exec requires a command")
    repository = (root or Path(__file__).resolve().parents[2]).resolve()
    os.execvpe(argv[0], argv, command_environment(repository))
    return 127


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except JavaRuntimeError as exc:
        print(f"Java environment FAIL: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
