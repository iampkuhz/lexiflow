from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.toolchain import postgres_test as tool


def completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class PostgresRunnerTest(unittest.TestCase):
    def test_scope_selection(self) -> None:
        commands = tool.scope_commands("all", Path("/repo"), "postgresql://u:p@localhost/db")
        self.assertEqual(2, len(commands))
        self.assertEqual(
            [
                sys.executable,
                "scripts/toolchain/java_gradle.py",
                ":platform:adapters:postgresIntegrationTest",
                "--no-daemon",
            ],
            commands[-1],
        )
        self.assertEqual(1, len(tool.scope_commands("indexes", Path("/repo"), "url")))
        with self.assertRaises(tool.PostgresTestError):
            tool.scope_commands("bad", Path("/repo"), "url")

    def test_redacts_credentials(self) -> None:
        self.assertEqual("postgresql://alice:***@localhost:5432/db", tool._redact_url("postgresql://alice:secret@localhost:5432/db"))

    @patch.object(tool.shutil, "which", side_effect=lambda name: "/usr/bin/" + name)
    @patch.object(tool, "_run")
    def test_override_creates_unique_database_and_cleans_up(self, run_mock, _which) -> None:
        run_mock.return_value = completed()
        with patch.object(tool.uuid, "uuid4", return_value=type("U", (), {"hex": "abc123"})()):
            provisioned = tool.provision({tool.OVERRIDE_ENV: "postgresql://admin:secret@localhost/postgres"})
        self.assertEqual("lexiflow_test_abc123", provisioned.database)
        self.assertIn("secret", provisioned.database_url)  # internal child-process input, never terminal output
        self.assertTrue(any("CREATE DATABASE" in call.args[0][-1] for call in run_mock.call_args_list))

    @patch.object(tool.shutil, "which", return_value=None)
    def test_missing_dependency_is_blocked(self, _which) -> None:
        result = tool.run("indexes")
        self.assertEqual("BLOCKED", result["status"])

    @patch.object(tool.shutil, "which", side_effect=lambda name: "/usr/bin/" + name)
    @patch.object(tool, "_run")
    def test_default_provision_uses_dynamic_port_and_cleanup(self, run_mock, _which) -> None:
        run_mock.side_effect = [
            completed("container-id\n"),
            completed("127.0.0.1:55432\n"),
            completed("1\n"),
            completed(),
        ]
        provisioned = tool.provision({})
        self.assertEqual("postgres", provisioned.database)
        self.assertIn(":55432/", provisioned.database_url)
        self.assertIsNotNone(provisioned.container_id)
        self.assertIn("--name", run_mock.call_args_list[0].args[0])

    @patch.object(tool, "provision")
    @patch.object(tool, "_drop_database")
    def test_test_failure_still_cleans_override_database(self, drop, provision) -> None:
        provision.return_value = tool.Provision("admin", "db", "isolated", override=True)
        result = tool.run("indexes", runner=lambda *_args, **_kwargs: completed(stderr="assertion", returncode=1))
        self.assertEqual("FAIL", result["status"])
        drop.assert_called_once_with("admin", "isolated")

    @patch.object(tool, "provision", side_effect=tool.ExternalDependencyBlocked("podman missing"))
    def test_blocked_result_is_canonical_json_shape(self, _provision) -> None:
        result = tool.run("all")
        self.assertEqual('{"reason":"podman missing","scope":"all","status":"BLOCKED"}', json.dumps(result, sort_keys=True, separators=(",", ":")))

    @patch.object(tool.postgres_migrations, "apply")
    @patch.object(tool, "provision")
    def test_persistence_applies_migrations_and_forwards_jdbc_url(self, provision, apply) -> None:
        provision.return_value = tool.Provision("admin", "postgresql://postgres:secret@localhost/test", "test")
        environments = []

        result = tool.run(
            "persistence",
            root=Path("/repo"),
            runner=lambda *_args, **kwargs: environments.append(kwargs["env"]) or completed(),
        )

        self.assertEqual("PASS", result["status"])
        apply.assert_called_once_with(
            "postgresql://postgres:secret@localhost/test",
            Path("/repo/infra/postgres/migrations"),
        )
        self.assertEqual(
            "jdbc:postgresql://localhost/test?user=postgres&password=secret",
            environments[0]["LEXIFLOW_POSTGRES_TEST_JDBC_URL"],
        )


if __name__ == "__main__":
    unittest.main()
