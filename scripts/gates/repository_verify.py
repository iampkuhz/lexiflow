"""Repository-wide deterministic baseline and readiness verification.

This module deliberately has no task/evidence/session input.  Its result is
health feedback for a checkout; formal validation invokes the same code from
an independent validator and binds that result into its receipt.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.gates.executor import execute_checks
from scripts.gates.local_verify import LocalVerifyError, compile_local_plan
from scripts.gates.planner import canonical_json_bytes, sha256_bytes
from scripts.gates.registry_profiles import RegistryProfileError, check_registry
from scripts.toolchain.java_gradle import ToolchainError, resolve_java_home

SCHEMA = "lexiflow.repository-verification.v1"
READINESS_SCOPE = "repository-readiness"
REMEDIATIONS = {
    "jdk-25": {
        "doctor": "python3 scripts/gates/repository_verify.py doctor",
        "bootstrap": "python3 scripts/gates/repository_verify.py bootstrap --remediation-id jdk-25",
        "action": "Install Temurin 25 under .local/toolchains/jdk-25 or set LEXIFLOW_JAVA_HOME.",
    },
    "podman": {
        "doctor": "python3 scripts/gates/repository_verify.py doctor",
        "bootstrap": "python3 scripts/gates/repository_verify.py bootstrap --remediation-id podman",
        "action": "Install Podman and make the podman executable available on PATH.",
    },
    "psql": {
        "doctor": "python3 scripts/gates/repository_verify.py doctor",
        "bootstrap": "python3 scripts/gates/repository_verify.py bootstrap --remediation-id psql",
        "action": "Install the PostgreSQL client and make psql available on PATH.",
    },
}


def _readiness(root: Path, environ: dict[str, str] | None = None) -> list[dict[str, str]]:
    env = dict(os.environ if environ is None else environ)
    missing: list[dict[str, str]] = []
    try:
        resolve_java_home(root, env)
    except ToolchainError as exc:
        missing.append({"remediation_id": "jdk-25", "detail": str(exc), **REMEDIATIONS["jdk-25"]})
    if shutil.which("podman") is None:
        missing.append({"remediation_id": "podman", "detail": "podman executable is unavailable", **REMEDIATIONS["podman"]})
    if shutil.which("psql") is None:
        missing.append({"remediation_id": "psql", "detail": "psql executable is unavailable", **REMEDIATIONS["psql"]})
    return missing


def doctor(root: str | Path = ".", *, environ: dict[str, str] | None = None) -> dict[str, Any]:
    missing = _readiness(Path(root).resolve(), environ)
    if missing:
        return {"schema_version": SCHEMA, "result": "BLOCKED", "blocking_scope": READINESS_SCOPE,
                "missing": missing}
    return {"schema_version": SCHEMA, "result": "PASS", "readiness": "ready"}


def bootstrap(root: str | Path = ".", *, remediation_id: str | None = None) -> dict[str, Any]:
    """Print only supported, explicit preparation actions; never installs implicitly."""
    current = doctor(root)
    actions = current.get("missing", [])
    if remediation_id:
        actions = [item for item in actions if item["remediation_id"] == remediation_id]
        if not actions and remediation_id not in REMEDIATIONS:
            return {"schema_version": SCHEMA, "result": "FAIL", "reason": "unknown-remediation-id"}
    return {"schema_version": SCHEMA, "result": current["result"], "blocking_scope": current.get("blocking_scope"),
            "actions": actions, "automatic_installation": False}


def compile_baseline_plan(root: Path) -> dict[str, Any]:
    return compile_local_plan(root, [], "full", verification_scope="repository-baseline")


def verify(root: str | Path = ".", *, executor: Callable[..., dict[str, Any]] = execute_checks,
           readiness: Callable[[str | Path], dict[str, Any]] = doctor) -> dict[str, Any]:
    repo = Path(root).resolve()
    try:
        # Unlike Change Verify, repository verification owns registry/profile
        # integrity validation for the entire checkout.
        integrity = check_registry(repo)
    except (RegistryProfileError, OSError, UnicodeError, ValueError) as exc:
        return {"schema_version": SCHEMA, "result": "FAIL", "reason": "registry-input-integrity", "detail": str(exc)}
    ready = readiness(repo)
    if ready.get("result") != "PASS":
        return {"schema_version": SCHEMA, "result": "BLOCKED", "blocking_scope": READINESS_SCOPE,
                "readiness": ready, "registry_integrity": integrity}
    try:
        plan = compile_baseline_plan(repo)
    except LocalVerifyError as exc:
        return {"schema_version": SCHEMA, "result": exc.result, "reason": exc.code, "detail": exc.detail}
    execution = executor(plan, repo_root=str(repo))
    return {"schema_version": SCHEMA, "result": execution.get("run_status", "FAIL"),
            "registry_integrity": integrity, "plan": plan, "execution": execution,
            "repository_fingerprint": sha256_bytes(canonical_json_bytes(plan))}


def run(root: str | Path = ".") -> dict[str, Any]:
    result = verify(root)
    repo = Path(root).resolve()
    run_id = str(uuid.uuid4())
    directory = repo / "tmp/quality/repository-verification" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {**result, "run_id": run_id, "artifact": str(directory.relative_to(repo) / "summary.json")}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/gates/repository_verify.py")
    parser.add_argument("command", choices=("run", "doctor", "bootstrap"))
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--remediation-id")
    args = parser.parse_args(argv)
    if args.command == "doctor": result = doctor(args.repo_root)
    elif args.command == "bootstrap": result = bootstrap(args.repo_root, remediation_id=args.remediation_id)
    else: result = run(args.repo_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}.get(result["result"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
