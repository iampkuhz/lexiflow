"""Contract tests for the deterministic Java Gradle launcher."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.toolchain.java_gradle import (
    ToolchainError,
    gradle_invocation,
    main,
    resolve_java_home,
    validate_java_home,
)


class JavaToolchainFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="lexiflow-java-toolchain-")
        self.root = Path(self.temporary.name)

    def close(self) -> None:
        self.temporary.cleanup()

    def jdk(
        self,
        relative: str,
        *,
        version: str = "25.0.4.1",
        implementor: str = "Eclipse Adoptium",
        implementor_version: str = "Temurin-25.0.4.1+1",
    ) -> Path:
        home = self.root / relative
        executable = home / "bin" / ("java.exe" if os.name == "nt" else "java")
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        (home / "release").write_text(
            f'JAVA_VERSION="{version}"\n'
            f'IMPLEMENTOR="{implementor}"\n'
            f'IMPLEMENTOR_VERSION="{implementor_version}"\n',
            encoding="utf-8",
        )
        return home

    def wrapper(self) -> Path:
        wrapper = self.root / "backend" / ("gradlew.bat" if os.name == "nt" else "gradlew")
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        return wrapper


class TestJavaToolchainRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = JavaToolchainFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_repository_local_temurin_25_is_selected(self) -> None:
        expected = self.fixture.jdk(".local/toolchains/jdk-25/Contents/Home")
        actual = resolve_java_home(self.fixture.root, {})
        self.assertEqual(actual, expected.resolve())

    def test_explicit_home_is_authoritative(self) -> None:
        self.fixture.jdk(".local/toolchains/jdk-25/Contents/Home")
        explicit = self.fixture.jdk("explicit")
        actual = resolve_java_home(
            self.fixture.root, {"LEXIFLOW_JAVA_HOME": str(explicit)}
        )
        self.assertEqual(actual, explicit.resolve())

    def test_invalid_explicit_home_does_not_fall_back(self) -> None:
        self.fixture.jdk(".local/toolchains/jdk-25/Contents/Home")
        wrong = self.fixture.jdk(
            "wrong", version="26.0.2", implementor_version="Temurin-26.0.2+1"
        )
        with self.assertRaisesRegex(ToolchainError, "Java 25 required"):
            resolve_java_home(self.fixture.root, {"LEXIFLOW_JAVA_HOME": str(wrong)})

    def test_wrong_distribution_is_rejected(self) -> None:
        wrong = self.fixture.jdk("wrong", implementor="Other Vendor")
        with self.assertRaisesRegex(ToolchainError, "Eclipse Adoptium required"):
            validate_java_home(wrong)

    def test_malformed_release_is_rejected(self) -> None:
        home = self.fixture.jdk("malformed")
        (home / "release").write_text("JAVA_VERSION=25\n", encoding="utf-8")
        with self.assertRaisesRegex(ToolchainError, "unquoted Java release metadata"):
            validate_java_home(home)

    def test_inherited_exact_java_home_is_used_when_local_is_absent(self) -> None:
        inherited = self.fixture.jdk("inherited")
        actual = resolve_java_home(self.fixture.root, {"JAVA_HOME": str(inherited)})
        self.assertEqual(actual, inherited.resolve())

    def test_missing_runtime_fails_closed(self) -> None:
        with self.assertRaisesRegex(ToolchainError, "Temurin 25 is unavailable"):
            resolve_java_home(self.fixture.root, {})

    def test_gradle_invocation_freezes_wrapper_and_build_root(self) -> None:
        wrapper = self.fixture.wrapper()
        backend, command = gradle_invocation(self.fixture.root, ["clean", "check"])
        self.assertEqual(backend, self.fixture.root / "backend")
        self.assertEqual(
            command,
            [str(wrapper), "-p", str(self.fixture.root / "backend"), "--console=colored", "clean", "check", "--rerun-tasks", "--no-build-cache"],
        )

    def test_explicit_console_modes_are_forwarded_without_another_default(self) -> None:
        wrapper = self.fixture.wrapper()
        for options in (
            ["--console=plain"],
            ["--console", "rich"],
            ["--console=auto"],
            ["--console=colored"],
            ["-Dorg.gradle.console=plain"],
            ["-D", "org.gradle.console=plain"],
        ):
            with self.subTest(options=options):
                _, command = gradle_invocation(self.fixture.root, ["help", *options])
                self.assertEqual(
                    command,
                    [str(wrapper), "-p", str(self.fixture.root / "backend"), "help", *options],
                )

    def test_delivery_aggregates_force_current_execution_once(self) -> None:
        self.fixture.wrapper()
        for aggregate in ("check", "qualityFull", ":deliveryFull"):
            _, command = gradle_invocation(self.fixture.root, [aggregate, "--rerun-tasks"])
            self.assertEqual(command.count("--rerun-tasks"), 1)
            self.assertEqual(command.count("--no-build-cache"), 1)

    def test_delivery_rejects_exclusions_filters_and_dry_runs(self) -> None:
        self.fixture.wrapper()
        for option in ("-x", "-xtest", "--exclude-task=test", "--dry-run", "-m", "--tests", "--build-cache"):
            with self.subTest(option=option), self.assertRaises(ToolchainError):
                gradle_invocation(self.fixture.root, ["deliveryFull", option])

    def test_launcher_rejects_alternate_build_and_initialization(self) -> None:
        self.fixture.wrapper()
        for option in ("-p", "-pother", "--project-dir=other", "-I", "-Iother", "--init-script=other", "--include-build=other"):
            with self.subTest(option=option), self.assertRaises(ToolchainError):
                gradle_invocation(self.fixture.root, ["tasks", option])

    def test_diagnostic_target_does_not_force_aggregate_reexecution(self) -> None:
        self.fixture.wrapper()
        _, command = gradle_invocation(self.fixture.root, [":tests:architecture:test"])
        self.assertNotIn("--rerun-tasks", command)

    def test_main_executes_with_validated_java_home(self) -> None:
        java_home = self.fixture.jdk("jdk")
        wrapper = self.fixture.wrapper()
        with patch("scripts.toolchain.java_gradle.os.execve") as execve:
            result = main(
                ["check"],
                repo_root=self.fixture.root,
                environ={"LEXIFLOW_JAVA_HOME": str(java_home), "PATH": "/usr/bin", "NO_COLOR": "1"},
            )
        self.assertEqual(result, 127)
        command, argv, environment = execve.call_args.args
        self.assertEqual(command, str(wrapper))
        self.assertEqual(argv[-3:], ["check", "--rerun-tasks", "--no-build-cache"])
        self.assertIn("--console=colored", argv)
        self.assertEqual(environment["JAVA_HOME"], str(java_home.resolve()))
        self.assertEqual(environment["PATH"].split(os.pathsep)[0], str(java_home.resolve() / "bin"))
        self.assertEqual(environment["NO_COLOR"], "1")


if __name__ == "__main__":
    unittest.main()
