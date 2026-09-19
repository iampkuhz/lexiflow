"""Invoke the Java offline lexicon importer without placing import logic in Python."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[2]


def jdbc_url(value: str) -> str:
    """Normalize a password-free PostgreSQL URL into the JDBC URL contract."""
    if value.startswith("jdbc:postgresql://"):
        return value
    parsed = urlsplit(value)
    if parsed.scheme != "postgresql" or not parsed.hostname or parsed.fragment:
        raise ValueError("database URL must be a PostgreSQL URL without a fragment")
    if parsed.password is not None:
        raise ValueError("database URL must not embed a password; use a local password-free URL")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.username and not any(key == "user" for key, _ in query):
        query.append(("user", parsed.username))
    suffix = "" if not query else "?" + urlencode(query)
    return f"jdbc:postgresql://{netloc}{parsed.path}{suffix}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the LexiFlow Java lexicon importer.")
    parser.add_argument("action", choices=("validate", "prewarm-report", "publish"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--acquired-at", default="1970-01-01T00:00:00Z")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--database-url")
    parser.add_argument("--batch-source-id")
    parser.add_argument("--batch-license-id")
    args = parser.parse_args(argv)
    if args.action == "publish" and not all(
        (args.database_url, args.batch_source_id, args.batch_license_id)
    ):
        parser.error("publish requires --database-url, --batch-source-id and --batch-license-id")
    if args.action != "publish" and any(
        (args.database_url, args.batch_source_id, args.batch_license_id)
    ):
        parser.error("database publication options are only valid for publish")

    database_url = ""
    if args.action == "publish":
        try:
            database_url = jdbc_url(args.database_url)
        except ValueError as exc:
            parser.error(str(exc))

    importer_args = [args.action, "--input", str(args.input.resolve()), "--acquired-at", args.acquired_at]
    if args.action == "prewarm-report":
        importer_args.extend(("--limit", str(args.limit)))
    if args.action == "publish":
        importer_args.extend(
            (
                "--database-url",
                database_url,
                "--batch-source-id",
                args.batch_source_id,
                "--batch-license-id",
                args.batch_license_id,
            )
        )

    command = [
        sys.executable,
        "scripts/toolchain/java_gradle.py",
        ":platform:adapters:lexiconImport",
        "-PlexiconImportArgs=" + "\x1f".join(importer_args),
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
