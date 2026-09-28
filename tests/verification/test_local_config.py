"""本机私有运行配置的直接安全测试。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.environment import java_exec

from scripts.environment.java_exec import command_environment
from scripts.environment.java_runtime import JavaRuntimeError
from scripts.environment.local_config import command_local_config


class LocalConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / ".local/lexiflow/runtime.json"
        self.path.parent.mkdir(parents=True)

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def test_missing_file_preserves_environment_mode(self):
        self.assertEqual(
            command_local_config(self.root, {"JDBC_URL": "env"}),
            {"JDBC_URL": "env"},
        )

    def test_reads_only_allowed_values_and_environment_wins_including_empty(self):
        self.write(json.dumps({"JDBC_URL": "file", "STARDICT_CSV": "/data/dict.csv"}))
        self.assertEqual(
            command_local_config(self.root, {"JDBC_URL": "", "OTHER": "kept"}),
            {
                "JDBC_URL": "",
                "STARDICT_CSV": "/data/dict.csv",
            },
        )

    def test_rejects_duplicate_keys_unknown_fields_and_non_objects(self):
        for raw in (
            '{"JDBC_URL":"first","JDBC_URL":"second"}',
            '{"OTHER":"value"}',
            '[]',
            'null',
            '{broken',
        ):
            with self.subTest(raw=raw):
                self.write(raw)
                with self.assertRaises(JavaRuntimeError):
                    command_local_config(self.root, {})

    def test_rejects_invalid_values(self):
        for raw in (
            '{"JDBC_URL":""}',
            '{"JDBC_URL":"   "}',
            '{"JDBC_URL":42}',
            '{"STARDICT_CSV":"relative/dict.csv"}',
        ):
            with self.subTest(raw=raw):
                self.write(raw)
                with self.assertRaises(JavaRuntimeError):
                    command_local_config(self.root, {})

    def test_exec_keeps_original_stdio_and_argument_boundaries(self):
        argv = ["command with spaces", "one argument"]
        with patch.object(java_exec, "command_environment", return_value={"PATH": "fixture"}), patch.object(java_exec.os, "execvpe") as execute:
            java_exec.main(argv, root=self.root)
        execute.assert_called_once_with(argv[0], argv, {"PATH": "fixture"})

    def test_error_does_not_echo_configuration_contents(self):
        secret = "jdbc:postgresql://private-host/secret"
        self.write('{"JDBC_URL":' + json.dumps(secret) + ',"OTHER":"secret-value"}')
        with self.assertRaises(JavaRuntimeError) as caught:
            command_local_config(self.root, {})
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn("secret-value", str(caught.exception))

    def test_command_environment_merges_config_after_java_selection(self):
        jdk = self.root / "jdk"
        (jdk / "bin").mkdir(parents=True)
        (jdk / "bin/java").write_text("", encoding="utf-8")
        (jdk / "release").write_text(
            'JAVA_VERSION="25.0.1"\nIMPLEMENTOR="Eclipse Adoptium"\n'
            'IMPLEMENTOR_VERSION="Temurin-25.0.1"\n',
            encoding="utf-8",
        )
        (jdk / "bin/java").chmod(0o755)
        self.write('{"JDBC_URL":"jdbc:test"}')
        environment = command_environment(self.root, {"LEXIFLOW_JAVA_HOME": str(jdk)})
        self.assertEqual(environment["JDBC_URL"], "jdbc:test")


if __name__ == "__main__":
    unittest.main()
