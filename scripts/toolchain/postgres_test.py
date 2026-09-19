"""Run the reproducible PostgreSQL checks used by the LexiFlow data contract."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.toolchain import postgres_migrations

IMAGE = "postgres:17-alpine"
POSTGRES_PASSWORD = "lexiflow-test-" + secrets.token_hex(12)
OVERRIDE_ENV = "LEXIFLOW_POSTGRES_TEST_URL"
SCOPES = ("indexes", "versioning", "persistence", "all")
PERSISTENCE_TEST = "io.lexiflow.lexicon.platform.persistence.PostgresLexiconRepositoryIntegrationTest"


class PostgresTestError(RuntimeError):
    """A test or lifecycle failure (as opposed to a missing external dependency)."""


class ExternalDependencyBlocked(PostgresTestError):
    """The requested check cannot run because a local dependency is unavailable."""


@dataclass(frozen=True)
class Provision:
    admin_url: str
    database_url: str
    database: str
    container_id: str | None = None
    override: bool = False


def _redact_url(value: str) -> str:
    """Do not expose passwords in terminal JSON or diagnostics."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.port:
            host += f":{parsed.port}"
        user = parsed.username
        authority = (f"{user}:***@" if user else "") + host
        return urlunsplit((parsed.scheme, authority, parsed.path, parsed.query, parsed.fragment))
    except ValueError:
        return "<redacted-postgresql-url>"


def _safe_detail(value: str) -> str:
    """Keep child-process diagnostics useful without echoing a credential-bearing URL."""
    for marker in ("postgresql://", "postgres://"):
        if marker in value:
            prefix, candidate = value.split(marker, 1)
            tail = candidate.split(None, 1)
            value = prefix + _redact_url(marker + tail[0]) + (" " + tail[1] if len(tail) == 2 else "")
    return value


def _database_url(admin_url: str, database: str) -> str:
    parsed = urlsplit(admin_url)
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname:
        raise PostgresTestError("PostgreSQL test URL is invalid")
    return urlunsplit((parsed.scheme, parsed.netloc, "/" + database, parsed.query, parsed.fragment))


def _psql_command(url: str, *args: str) -> list[str]:
    return ["psql", url, "--no-psqlrc", "--set", "ON_ERROR_STOP=1", *args]


def _run(
    command: Sequence[str],
    *,
    input_text: str | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        check=False,
    )


def _require_tools(*, podman: bool = True) -> None:
    required = ("psql", "podman") if podman else ("psql",)
    missing = [tool for tool in required if shutil.which(tool) is None]
    if missing:
        raise ExternalDependencyBlocked("missing external dependency: " + ", ".join(missing))


def _create_database(admin_url: str, database: str) -> None:
    result = _run(_psql_command(admin_url, "--command", f'CREATE DATABASE "{database}"'))
    if result.returncode:
        raise ExternalDependencyBlocked(
            _safe_detail(result.stderr.strip() or "cannot create isolated PostgreSQL test database")
        )


def _drop_database(admin_url: str, database: str) -> None:
    # Terminate sessions first so cleanup is deterministic even after a test opened a pool.
    for sql in (
        f'REVOKE CONNECT ON DATABASE "{database}" FROM public',
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{database}' AND pid <> pg_backend_pid()",
        f'DROP DATABASE IF EXISTS "{database}"',
    ):
        result = _run(_psql_command(admin_url, "--command", sql))
        if result.returncode:
            raise PostgresTestError(_safe_detail(result.stderr.strip() or "database cleanup failed"))


def _wait_ready(url: str, *, retries: int = 30, sleep: float = 1.0) -> None:
    for _ in range(retries):
        result = _run(_psql_command(url, "--tuples-only", "--command", "SELECT 1"))
        if result.returncode == 0:
            return
        time.sleep(sleep)
    raise ExternalDependencyBlocked("PostgreSQL did not become ready; start Podman and retry")


def provision(environ: Mapping[str, str] | None = None) -> Provision:
    """Provision an isolated database, using an admin URL when supplied."""
    env = os.environ if environ is None else environ
    override = env.get(OVERRIDE_ENV, "").strip()
    _require_tools(podman=not bool(override))
    database = "lexiflow_test_" + uuid.uuid4().hex
    if override:
        try:
            _create_database(override, database)
            admin_url = override
            database_url = _database_url(override, database)
            _wait_ready(database_url)
            return Provision(admin_url, database_url, database, override=True)
        except PostgresTestError:
            try:
                _drop_database(override, database)
            except PostgresTestError:
                pass
            raise

    started = _run(
        [
            "podman",
            "run",
            "--detach",
            "--rm",
            "--name",
            "lexiflow-postgres-test-" + database.removeprefix("lexiflow_test_"),
            "-e",
            f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
            "-e",
            "POSTGRES_DB=postgres",
            "-p",
            "127.0.0.1::5432",
            IMAGE,
        ]
    )
    if started.returncode:
        raise ExternalDependencyBlocked(started.stderr.strip() or "podman could not start PostgreSQL")
    container_id = started.stdout.strip()
    try:
        port = _run(["podman", "port", container_id, "5432/tcp"])
        if port.returncode:
            raise ExternalDependencyBlocked(
                port.stderr.strip() or "cannot resolve PostgreSQL container port"
            )
        host_port = port.stdout.strip().rsplit(":", 1)[-1].split("/", 1)[0]
        admin_url = f"postgresql://postgres:{POSTGRES_PASSWORD}@127.0.0.1:{host_port}/postgres"
        _wait_ready(admin_url)
        return Provision(admin_url, admin_url, "postgres", container_id=container_id)
    except PostgresTestError:
        _run(["podman", "rm", "--force", container_id])
        raise


def scope_commands(scope: str, root: Path, database_url: str) -> list[list[str]]:
    """Build the closed command set; URL is passed only to child processes."""
    if scope not in SCOPES:
        raise PostgresTestError(f"unknown scope: {scope}")
    migration_suite = "tests.toolchain.test_postgres_migrations.PostgreSqlMigrationIntegrationTest"
    indexes = [sys.executable, "-m", "unittest", f"{migration_suite}.test_fresh_upgrade_and_declared_query_indexes"]
    versioning = [
        sys.executable,
        "-m",
        "unittest",
        f"{migration_suite}.test_fresh_upgrade_and_declared_query_indexes",
        f"{migration_suite}.test_failure_is_atomic_and_compensation_is_forward_only",
        f"{migration_suite}.test_rejects_a_changed_applied_migration",
    ]
    migration_test = [sys.executable, "-m", "unittest", migration_suite]
    persistence = [
        sys.executable,
        "scripts/toolchain/java_gradle.py",
        ":platform:adapters:postgresIntegrationTest",
        "--no-daemon",
    ]
    if scope == "indexes":
        return [indexes]
    if scope == "versioning":
        return [versioning]
    if scope == "persistence":
        return [persistence]
    return [migration_test, persistence]


def _jdbc_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if parsed.scheme not in ("postgresql", "postgres") or not parsed.hostname:
        raise PostgresTestError("PostgreSQL test URL is invalid")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    if parsed.port:
        host += f":{parsed.port}"
    parameters = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.username and not any(key == "user" for key, _ in parameters):
        parameters.append(("user", parsed.username))
    if parsed.password and not any(key == "password" for key, _ in parameters):
        parameters.append(("password", parsed.password))
    query = urlencode(parameters)
    return f"jdbc:postgresql://{host}{parsed.path}" + (f"?{query}" if query else "")


def run(scope: str = "all", *, root: Path | None = None, environ: Mapping[str, str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = _run) -> dict[str, object]:
    """Run one scope and always clean the temporary database/container."""
    root = (root or Path(__file__).resolve().parents[2]).resolve()
    provisioned: Provision | None = None
    failure: PostgresTestError | None = None
    try:
        provisioned = provision(environ)
        if scope in ("persistence", "all"):
            postgres_migrations.apply(
                provisioned.database_url, root / "infra" / "postgres" / "migrations"
            )
        for command in scope_commands(scope, root, provisioned.database_url):
            child_environment = dict(os.environ if environ is None else environ)
            child_environment[OVERRIDE_ENV] = provisioned.admin_url
            child_environment["LEXIFLOW_POSTGRES_TEST_JDBC_URL"] = _jdbc_url(
                provisioned.database_url
            )
            result = runner(command, cwd=str(root), env=child_environment)
            if result.returncode:
                failure = PostgresTestError(_safe_detail(result.stderr.strip() or result.stdout.strip() or "PostgreSQL test failed"))
                break
    except ExternalDependencyBlocked as exc:
        return {"status": "BLOCKED", "scope": scope, "reason": str(exc)}
    except (PostgresTestError, postgres_migrations.MigrationError) as exc:
        failure = PostgresTestError(_safe_detail(str(exc)))
    finally:
        if provisioned is not None:
            try:
                if provisioned.override:
                    _drop_database(provisioned.admin_url, provisioned.database)
                elif provisioned.container_id:
                    cleaned = _run(["podman", "rm", "--force", provisioned.container_id])
                    if cleaned.returncode:
                        raise PostgresTestError(cleaned.stderr.strip() or "container cleanup failed")
            except PostgresTestError as exc:
                failure = failure or exc
    if failure:
        return {"status": "FAIL", "scope": scope, "reason": str(failure)}
    return {"status": "PASS", "scope": scope}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify",))
    parser.add_argument("--scope", choices=SCOPES, default="all")
    args = parser.parse_args(argv)
    result = run(args.scope)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return {"PASS": 0, "BLOCKED": 1, "FAIL": 1}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
