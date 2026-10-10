"""Check 单次环境解析、私有隔离与消费时重新解析的直接测试。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.environment.configuration.verification_environment import resolve_check_environment


def _resolve(root, required, env=None):
    return resolve_check_environment(root, {"required_environment": required}, env)


def _jdk(root: Path) -> Path:
    home = root / ".local/toolchains/jdk-25"
    (home / "bin").mkdir(parents=True)
    java = home / "bin/java"
    java.write_text("#!/bin/sh\nexit 0\n")
    java.chmod(0o755)
    (home / "release").write_text(
        'JAVA_VERSION="25.0.1"\nIMPLEMENTOR="Eclipse Adoptium"\n'
        'IMPLEMENTOR_VERSION="Temurin-25.0.1+8"\n'
    )
    return home


class CheckEnvironmentTests(unittest.TestCase):
    def test_python_current_executor_and_virtualenv(self):
        result = _resolve(".", ["python3"], {})
        info = result.diagnostic["details"]["python3"]
        self.assertTrue(info["available"])
        self.assertEqual(info["executable"], sys.executable)
        self.assertIn("version", info)
        self.assertIsInstance(info["in_virtualenv"], bool)

    def test_empty_declaration_does_not_invent_defaults(self):
        result = resolve_check_environment(".", {}, {})
        self.assertEqual(
            result.diagnostic, {"status": "PASS", "missing": [], "details": {}}
        )
        self.assertEqual(result.values, {})

    def test_tool_detection_uses_only_supplied_source_path(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory) / "fixture-tool"
            tool.write_text("#!/bin/sh\nexit 0\n")
            tool.chmod(0o755)
            with patch.dict(os.environ, {"PATH": "/does-not-exist"}):
                present = _resolve(".", ["fixture-tool"], {"PATH": directory})
                missing = _resolve(".", ["fixture-tool"], {"PATH": ""})
            self.assertEqual(present.diagnostic["status"], "PASS")
            self.assertEqual(
                present.diagnostic["details"]["fixture-tool"]["path"], str(tool)
            )
            self.assertEqual(missing.diagnostic["status"], "BLOCKED")
            self.assertEqual(missing.diagnostic["missing"], ["fixture-tool"])

    def test_java_resolved_once_and_private_home_does_not_reach_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = _jdk(root)
            from scripts.environment.toolchains.java_toolchain_selection import resolve_java_home

            with patch(
                "scripts.environment.configuration.verification_environment.resolve_java_home", wraps=resolve_java_home
            ) as resolve:
                result = _resolve(root, ["java-25-temurin"], {"PATH": "/bin"})
            self.assertEqual(resolve.call_count, 1)
            self.assertEqual(result.values["JAVA_HOME"], str(home))
            self.assertEqual(result.values["PATH"], f"{home}/bin:/bin")
            self.assertEqual(result.diagnostic["status"], "PASS")
            self.assertEqual(
                result.diagnostic["details"]["java-25-temurin"]["source"], "temurin-25"
            )
            self.assertNotIn(str(home), json.dumps(result.diagnostic) + repr(result))
            (home / "release").unlink()
            current = _resolve(root, ["java-25-temurin"], {})
            self.assertEqual(current.diagnostic["status"], "BLOCKED")
            self.assertEqual(current.values, {})
            self.assertNotIn(str(home), json.dumps(current.diagnostic))

    def test_java_explicit_invalid_choice_never_falls_back_to_valid_repo_jdk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _jdk(root)
            result = _resolve(
                root,
                ["java-25-temurin"],
                {"LEXIFLOW_JAVA_HOME": "/private-canary/no-jdk"},
            )
            self.assertEqual(result.diagnostic["status"], "BLOCKED")
            self.assertNotIn("private-canary", json.dumps(result.diagnostic))

    def test_private_views_are_nonserializable_immutable_and_independent(self):
        env = {
            "LEXIFLOW_POSTGRES_TEST_JDBC_URL": "jdbc:private-canary",
            "LEXIFLOW_REDIS_TEST_ENDPOINT": "redis-private-canary",
            "LEXIFLOW_CANDIDATE_RUNTIME_REQUEST": "/private-canary/request.json",
            "LEXIFLOW_RELEASE_ARM64_DOCKER_HOST": "unix:///private-canary/socket",
        }
        required = [
            "postgres-test-jdbc-url",
            "redis-test-endpoint",
            "candidate-runtime-request",
            "release-arm64-docker-host",
        ]
        result = _resolve(".", required, env)
        self.assertEqual(result.diagnostic["status"], "PASS")
        self.assertEqual(result.values, env)
        self.assertNotIn("private-canary", json.dumps(result.diagnostic) + repr(result))
        with self.assertRaises(TypeError):
            json.dumps(result)
        with self.assertRaises(TypeError):
            json.dumps(result.values)
        with self.assertRaises(TypeError):
            result.values["LEXIFLOW_REDIS_TEST_ENDPOINT"] = "changed"
        env["LEXIFLOW_REDIS_TEST_ENDPOINT"] = "changed"
        self.assertEqual(
            result.values["LEXIFLOW_REDIS_TEST_ENDPOINT"], "redis-private-canary"
        )
        current = _resolve(".", required, env)
        self.assertEqual(current.values["LEXIFLOW_REDIS_TEST_ENDPOINT"], "changed")
        self.assertIsNot(current.values, result.values)

    def test_missing_or_invalid_private_capability_blocks_entire_injection(self):
        cases = {
            "postgres-test-jdbc-url": ("LEXIFLOW_POSTGRES_TEST_JDBC_URL", " "),
            "redis-test-endpoint": ("LEXIFLOW_REDIS_TEST_ENDPOINT", " "),
            "candidate-runtime-request": (
                "LEXIFLOW_CANDIDATE_RUNTIME_REQUEST",
                "relative.json",
            ),
            "release-arm64-docker-host": (
                "LEXIFLOW_RELEASE_ARM64_DOCKER_HOST",
                "tcp://host",
            ),
            "release-gradle-cache": ("LEXIFLOW_RELEASE_GRADLE_CACHE", "relative"),
            "release-npm-cache": (
                "LEXIFLOW_RELEASE_NPM_CACHE",
                "/private-canary/absent",
            ),
        }
        for name, (variable, value) in cases.items():
            for env in ({}, {variable: value}):
                with self.subTest(name=name, env=env):
                    result = _resolve(
                        ".",
                        ["redis-test-endpoint", name],
                        {"LEXIFLOW_REDIS_TEST_ENDPOINT": "private-canary", **env},
                    )
                    # Missing redis case must not be satisfied by the canary above.
                    if name == "redis-test-endpoint" and not env:
                        result = _resolve(".", [name], {})
                    self.assertEqual(result.diagnostic["status"], "BLOCKED")
                    self.assertEqual(result.values, {})
                    self.assertNotIn("private-canary", json.dumps(result.diagnostic))

    def test_cache_selection_is_explicit_and_rechecked_after_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "private-cache-canary"
            cache.mkdir()
            env = {
                "LEXIFLOW_RELEASE_GRADLE_CACHE": str(cache),
                "LEXIFLOW_RELEASE_NPM_CACHE": str(cache),
            }
            required = ["release-gradle-cache", "release-npm-cache"]
            result = _resolve(".", required, env)
            self.assertEqual(result.diagnostic["status"], "PASS")
            self.assertEqual(result.values, env)
            self.assertNotIn(str(cache), json.dumps(result.diagnostic))
            cache.rmdir()
            self.assertEqual(
                _resolve(".", required, env).diagnostic["status"], "BLOCKED"
            )

    def test_environment_defaults_take_fresh_snapshot_without_private_git_path(self):
        with patch.dict(os.environ, {"LEXIFLOW_REDIS_TEST_ENDPOINT": "first"}):
            first = _resolve(".", ["redis-test-endpoint", "git"])
            os.environ["LEXIFLOW_REDIS_TEST_ENDPOINT"] = "second"
            second = _resolve(".", ["redis-test-endpoint", "git"])
        self.assertEqual(first.values["LEXIFLOW_REDIS_TEST_ENDPOINT"], "first")
        self.assertEqual(second.values["LEXIFLOW_REDIS_TEST_ENDPOINT"], "second")
        self.assertNotIn("PATH", first.values)

    def test_yaml_remains_precheck_with_safe_child_probe_owned_by_kernel(self):
        with patch(
            "scripts.environment.configuration.verification_environment.importlib.util.find_spec", return_value=None
        ):
            result = _resolve(".", ["python-package-yaml"], {})
        self.assertEqual(result.diagnostic["status"], "BLOCKED")
        self.assertEqual(result.diagnostic["missing"], ["python-package-yaml"])

    def test_sha256_uses_same_source_path_and_platform_lock_is_explicit(self):
        with patch(
            "scripts.environment.configuration.verification_environment.shutil.which", return_value=None
        ) as which:
            result = _resolve(".", ["sha256-tool"], {"PATH": "/source-path"})
        self.assertEqual(result.diagnostic["status"], "BLOCKED")
        self.assertEqual(
            [call.kwargs["path"] for call in which.call_args_list],
            ["/source-path", "/source-path"],
        )
        with patch("scripts.environment.configuration.verification_environment.sys.platform", "unsupported"):
            self.assertEqual(
                _resolve(".", ["posix-lock-tool"], {}).diagnostic["status"], "BLOCKED"
            )

    def test_release_host_rejects_non_apple_silicon(self):
        from scripts.checks.release.source.check_synthetic_release_lifecycle import (
            ConsumerError,
            _require_supported_host,
        )

        with patch("scripts.environment.configuration.verification_environment.sys.platform", "linux"):
            with self.assertRaises(ConsumerError) as raised:
                _require_supported_host()
        self.assertEqual(raised.exception.status, "BLOCKED")


if __name__ == "__main__":
    unittest.main()
