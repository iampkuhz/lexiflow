"""Apply hash-locked PostgreSQL migrations with explicit local connection input."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MIGRATION_NAME = re.compile(r"^V(?P<version>[0-9]+)__(?P<name>[a-z0-9_]+)\.sql$")
LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    checksum CHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


class MigrationError(RuntimeError):
    """Raised for a closed migration contract violation."""


@dataclass(frozen=True)
class Migration:
    """One immutable SQL migration file."""

    version: int
    name: str
    path: Path
    checksum: str


def migrations(directory: Path) -> list[Migration]:
    """Load ordered contiguous migrations and reject every ambiguous filename."""

    if not directory.is_dir():
        raise MigrationError(f"migration directory is unavailable: {directory}")
    result: list[Migration] = []
    for path in sorted(directory.iterdir()):
        if path.is_dir() or path.name.startswith("."):
            continue
        match = MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"invalid migration filename: {path.name}")
        result.append(
            Migration(
                int(match.group("version")),
                match.group("name"),
                path,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    if not result:
        raise MigrationError("migration directory is empty")
    expected = 1
    for item in result:
        if item.version != expected:
            raise MigrationError(f"migration versions must be contiguous; expected V{expected:03d}")
        expected += 1
    return result


def psql_executable() -> str:
    """Return a caller-visible psql executable without guessing credentials."""

    executable = shutil.which("psql")
    if executable is None:
        raise MigrationError("psql is required on PATH")
    return executable


def run_psql(database_url: str, *arguments: str, input_text: str | None = None) -> str:
    """Run one fail-closed psql command against the explicit database URL."""

    completed = subprocess.run(
        [psql_executable(), database_url, "--no-psqlrc", "--set", "ON_ERROR_STOP=1", *arguments],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "psql failed"
        raise MigrationError(detail)
    return completed.stdout


def read_ledger(database_url: str) -> dict[int, tuple[str, str]]:
    """Read applied migration identity and hash from PostgreSQL."""

    output = run_psql(
        database_url,
        "--tuples-only",
        "--no-align",
        "--field-separator=|",
        "--command",
        "SELECT version, name, checksum FROM schema_migration ORDER BY version",
    )
    result: dict[int, tuple[str, str]] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        version, name, checksum = line.split("|")
        result[int(version)] = (name, checksum)
    return result


def apply(database_url: str, directory: Path, target_version: int | None = None) -> dict[str, object]:
    """Create the ledger, verify immutable history, and apply pending migrations."""

    if not database_url.startswith(("postgresql://", "postgres://")):
        raise MigrationError("database URL must use PostgreSQL")
    available = migrations(directory)
    highest = available[-1].version
    if target_version is not None and (target_version < 1 or target_version > highest):
        raise MigrationError("target version is outside available migrations")
    run_psql(database_url, "--single-transaction", "--command", LEDGER_SQL)
    applied = read_ledger(database_url)
    available_by_version = {item.version: item for item in available}
    for version, (name, checksum) in applied.items():
        item = available_by_version.get(version)
        if item is None:
            raise MigrationError(f"applied migration V{version:03d} is unavailable")
        if (item.name, item.checksum) != (name, checksum):
            raise MigrationError(f"applied migration V{version:03d} checksum or name changed")
    selected = [item for item in available if target_version is None or item.version <= target_version]
    executed: list[int] = []
    for item in selected:
        if item.version in applied:
            continue
        ledger_insert = (
            "INSERT INTO schema_migration (version, name, checksum) VALUES "
            f"({item.version}, '{item.name}', '{item.checksum}');\n"
        )
        run_psql(
            database_url,
            "--single-transaction",
            input_text=item.path.read_text(encoding="utf-8") + "\n" + ledger_insert,
        )
        executed.append(item.version)
    return {"status": "PASS", "applied": executed, "target_version": target_version or highest}


def main(argv: Iterable[str] | None = None) -> int:
    """Run the explicit migration application command."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apply", nargs="?")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--migrations-dir", default="infra/postgres/migrations")
    parser.add_argument("--target-version", type=int)
    args = parser.parse_args(argv)
    try:
        result = apply(args.database_url, Path(args.migrations_dir), args.target_version)
    except MigrationError as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
