"""Integration coverage for the immutable PostgreSQL migration harness."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from scripts.toolchain import postgres_migrations

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "infra/postgres/migrations"


class PostgreSqlMigrationIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.admin_url = os.environ.get("LEXIFLOW_POSTGRES_TEST_URL", "").strip()
        if not cls.admin_url:
            raise RuntimeError(
                "run PostgreSQL integration through scripts/toolchain/postgres_test.py verify"
            )
        if shutil.which("psql") is None:
            raise RuntimeError("psql is required for PostgreSQL integration")

    def setUp(self) -> None:
        self.database = "lexiflow_test_" + uuid.uuid4().hex
        self._psql(self.admin_url, f'CREATE DATABASE "{self.database}"')
        self.database_url = self.admin_url.rsplit("/", 1)[0] + "/" + self.database

    def tearDown(self) -> None:
        self._psql(self.admin_url, f'REVOKE CONNECT ON DATABASE "{self.database}" FROM public')
        self._psql(
            self.admin_url,
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{self.database}' AND pid <> pg_backend_pid()",
        )
        self._psql(self.admin_url, f'DROP DATABASE IF EXISTS "{self.database}"')

    @staticmethod
    def _psql(database_url: str, sql: str) -> str:
        completed = subprocess.run(
            ["psql", database_url, "--no-psqlrc", "--set", "ON_ERROR_STOP=1", "--command", sql],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode:
            raise AssertionError(completed.stderr or completed.stdout)
        return completed.stdout

    def test_fresh_upgrade_and_declared_query_indexes(self) -> None:
        first = postgres_migrations.apply(self.database_url, MIGRATIONS, target_version=1)
        self.assertEqual([1], first["applied"])
        self.assertEqual("content_revision", self._scalar("SELECT to_regclass('content_revision')"))

        upgraded = postgres_migrations.apply(self.database_url, MIGRATIONS)
        self.assertEqual([2, 3, 4, 5], upgraded["applied"])
        self.assertEqual("", self._scalar("SELECT COALESCE(to_regclass('content_revision')::text, '')"))
        self._seed_lexicon_fixture()
        self.assertEqual(
            "lexicon_entry_lookup_idx",
            self._scalar("SELECT to_regclass('lexicon_entry_lookup_idx')"),
        )
        self.assertIn(
            "Index",
            self._explain(
                "SELECT lexicon_entry_id FROM lexicon_entry "
                "WHERE language_tag = 'en' AND normalized_key = 'en:word:reliable' "
                "AND lexicon_version = 1"
            ),
        )

    def test_failure_is_atomic_and_compensation_is_forward_only(self) -> None:
        postgres_migrations.apply(self.database_url, MIGRATIONS)

        with tempfile.TemporaryDirectory() as temp_dir:
            copied = Path(temp_dir) / "migrations"
            shutil.copytree(MIGRATIONS, copied)
            (copied / "V006__failing_change.sql").write_text(
                "CREATE TABLE atomic_probe (id INTEGER PRIMARY KEY);\nSELECT 1 / 0;\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(postgres_migrations.MigrationError, "division by zero"):
                postgres_migrations.apply(self.database_url, copied)
            self.assertEqual("", self._scalar("SELECT COALESCE(to_regclass('atomic_probe')::text, '')"))
            self.assertEqual("5", self._scalar("SELECT max(version)::text FROM schema_migration"))

            (copied / "V006__failing_change.sql").unlink()
            (copied / "V006__expand_probe.sql").write_text(
                "CREATE TABLE compensation_probe (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
            )
            (copied / "V007__compensate_probe.sql").write_text(
                "DROP TABLE compensation_probe;\n", encoding="utf-8"
            )
            compensated = postgres_migrations.apply(self.database_url, copied)
            self.assertEqual([6, 7], compensated["applied"])
            self.assertEqual("", self._scalar("SELECT COALESCE(to_regclass('compensation_probe')::text, '')"))

    def test_rejects_a_changed_applied_migration(self) -> None:
        postgres_migrations.apply(self.database_url, MIGRATIONS, target_version=1)
        with tempfile.TemporaryDirectory() as temp_dir:
            copied = Path(temp_dir) / "migrations"
            shutil.copytree(MIGRATIONS, copied)
            path = copied / "V001__core_data_contract.sql"
            path.write_text(path.read_text(encoding="utf-8") + "\n-- changed\n", encoding="utf-8")
            with self.assertRaisesRegex(postgres_migrations.MigrationError, "checksum or name changed"):
                postgres_migrations.apply(self.database_url, copied, target_version=1)

    def _seed_lexicon_fixture(self) -> None:
        self._psql(
            self.database_url,
            """
            INSERT INTO lexicon_entry (
                lexicon_entry_id, lexicon_version, language_tag, entry_kind, lemma,
                normalized_key, provenance_source_id, provenance_license_id, provenance_digest, acquired_at
            ) VALUES (
                '10000000-0000-0000-0000-000000000001', 1, 'en', 'word', 'reliable',
                'en:word:reliable', 'fixture', 'fixture', repeat('a', 64), CURRENT_TIMESTAMP
            );
            INSERT INTO lexicon_sense (
                sense_id, lexicon_entry_id, lexicon_version, chinese_gloss, definition_text, provenance_reference
            ) VALUES (
                '20000000-0000-0000-0000-000000000001',
                '10000000-0000-0000-0000-000000000001', 1, '可靠的', 'fixture', 'fixture'
            );
            ANALYZE;
            """,
        )

    def _explain(self, sql: str) -> str:
        return self._psql(
            self.database_url, "SET enable_seqscan = off; EXPLAIN " + sql
        )

    def _scalar(self, sql: str) -> str:
        completed = subprocess.run(
            ["psql", self.database_url, "--tuples-only", "--no-align", "--command", sql],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode:
            self.fail(completed.stderr or completed.stdout)
        return completed.stdout.strip()


if __name__ == "__main__":
    unittest.main()
