"""Operate the reproducible local LexiFlow API, worker, PostgreSQL and Redis stack."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.toolchain import java_gradle, postgres_migrations
COMPOSE_FILE = ROOT / "infra/local/compose.yaml"
POSTGRES_URL = "postgresql://postgres@127.0.0.1:15432/lexiflow"
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 16379
API_PORT = 18081


class LocalStackError(RuntimeError):
    """Raised when the declared local stack cannot reach a verified state."""


def command() -> list[str]:
    """Return the one declared local container runtime command."""

    executable = shutil.which("podman")
    if executable is None:
        raise LocalStackError("podman is required for the declared local stack")
    return [executable, "compose", "--project-name", "lexiflow-local", "--file", str(COMPOSE_FILE)]


def run_container(*arguments: str) -> str:
    """Run one fail-closed compose command against the checked-in compose file."""

    completed = subprocess.run(
        [*command(), *arguments],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise LocalStackError(completed.stderr.strip() or completed.stdout.strip() or "compose failed")
    return completed.stdout


def wait_for_stack(timeout_seconds: int = 45) -> None:
    """Wait only for the two explicit local dependencies to accept protocol checks."""

    deadline = time.monotonic() + timeout_seconds
    postgres_error = "not attempted"
    redis_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            postgres_migrations.run_psql(POSTGRES_URL, "--command", "SELECT 1")
            postgres_ok = True
        except postgres_migrations.MigrationError as exc:
            postgres_error = str(exc)
            postgres_ok = False
        try:
            with socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=1) as connection:
                connection.sendall(b"PING\r\n")
                redis_ok = connection.recv(64) == b"+PONG\r\n"
                if not redis_ok:
                    redis_error = "Redis did not return PONG"
        except OSError as exc:
            redis_error = str(exc)
            redis_ok = False
        if postgres_ok and redis_ok:
            return
        time.sleep(1)
    raise LocalStackError(
        f"local dependencies were not ready within {timeout_seconds}s; postgres={postgres_error}; redis={redis_error}"
    )


def up() -> dict[str, object]:
    """Start only checked-in PostgreSQL and Redis, then verify both protocols."""

    run_container("up", "--detach")
    wait_for_stack()
    return {"status": "PASS", "postgres_url": POSTGRES_URL, "redis_endpoint": f"{REDIS_HOST}:{REDIS_PORT}"}


def down() -> dict[str, object]:
    """Stop and remove the named local stack, including local test volumes."""

    run_container("down", "--volumes")
    return {"status": "PASS", "action": "down"}


def run_gradle(*tasks: str) -> None:
    """Build exact boot artifacts through the checked Java 25 launcher."""

    completed = subprocess.run(
        [sys.executable, "scripts/toolchain/java_gradle.py", *tasks],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise LocalStackError(completed.stderr.strip() or completed.stdout.strip() or "Gradle build failed")


def wait_for_text(process: subprocess.Popen[str], expected: str, timeout_seconds: int = 30) -> None:
    """Read one process output stream until the expected startup evidence is observed."""

    deadline = time.monotonic() + timeout_seconds
    output: list[str] = []
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LocalStackError("process exited before readiness: " + "".join(output)[-2000:])
        line = process.stdout.readline() if process.stdout is not None else ""
        if line:
            output.append(line)
            if expected in line:
                return
        else:
            time.sleep(0.1)
    raise LocalStackError("process did not report readiness: " + "".join(output)[-2000:])


def terminate(process: subprocess.Popen[str]) -> None:
    """Terminate a locally spawned verification process without leaving it running."""

    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def verify_runtime() -> dict[str, object]:
    """Verify stack protocols, migrations, API health and worker startup using synthetic local data."""

    up()
    migration = postgres_migrations.apply(POSTGRES_URL, ROOT / "infra/postgres/migrations")
    run_gradle(":apps:api:bootJar", ":apps:worker:bootJar")
    java_home = java_gradle.resolve_java_home(ROOT, os.environ)
    java = str(java_home / "bin" / "java")
    api_jar = ROOT / "backend/apps/api/build/libs/api-0.1.0-SNAPSHOT.jar"
    worker_jar = ROOT / "backend/apps/worker/build/libs/worker-0.1.0-SNAPSHOT.jar"
    if not api_jar.is_file() or not worker_jar.is_file():
        raise LocalStackError("expected boot jars are missing after Gradle build")
    api = subprocess.Popen(
        [java, "-jar", str(api_jar), f"--server.port={API_PORT}"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    worker: subprocess.Popen[str] | None = None
    try:
        wait_for_text(api, "Tomcat started on port", 30)
        with urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/actuator/health", timeout=5) as response:
            if response.status != 200 or b'"UP"' not in response.read():
                raise LocalStackError("API health response is not UP")
        worker = subprocess.Popen(
            [java, "-jar", str(worker_jar)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        wait_for_text(worker, "Started WorkerApplication", 30)
    finally:
        if worker is not None:
            terminate(worker)
        terminate(api)
    return {"status": "PASS", "migration": migration, "api_health": "UP", "worker": "STARTED"}


def main(argv: Iterable[str] | None = None) -> int:
    """Run one explicit local stack lifecycle action."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "down", "verify-runtime"))
    args = parser.parse_args(argv)
    try:
        if args.action == "up":
            result = up()
        elif args.action == "down":
            result = down()
        else:
            result = verify_runtime()
    except (LocalStackError, postgres_migrations.MigrationError) as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
