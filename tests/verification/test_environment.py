"""Environment 公共 API 的探测与缺项诊断测试；Verification 直接消费此边界。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.environment import (
    check_for as check_required_environment,
    detect_java,
    detect_python,
    detect_tool,
    diagnose as diagnose_environment,
)


class TestDetectPython(unittest.TestCase):
    def test_always_available(self):
        info = detect_python()
        self.assertTrue(info["available"])
        self.assertIn("version", info)
        self.assertEqual(info["executable"], sys.executable)

    def test_reports_virtualenv(self):
        info = detect_python()
        self.assertIsInstance(info["in_virtualenv"], bool)


class TestDetectTool(unittest.TestCase):
    def test_finds_python3(self):
        info = detect_tool("python3")
        # python3 should be findable on any system running these tests
        self.assertTrue(info["available"])
        self.assertTrue(info["path"])

    def test_missing_tool(self):
        info = detect_tool("nonexistent-tool-xyz-12345")
        self.assertFalse(info["available"])
        self.assertEqual(info["path"], "")


class TestDetectJava(unittest.TestCase):
    def test_missing_java(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {}, clear=False):
                env = dict(os.environ)
                env.pop("LEXIFLOW_JAVA_HOME", None)
                with patch.dict(os.environ, env, clear=True):
                    info = detect_java(Path(tmpdir))
                    # May or may not find java on PATH
                    self.assertIsInstance(info["available"], bool)

    def test_java_home_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jdk = Path(tmpdir) / "jdk"
            jdk_bin = jdk / "bin"
            jdk_bin.mkdir(parents=True)
            (jdk_bin / "java").write_text("#!/bin/sh\necho java")
            os.chmod(str(jdk_bin / "java"), 0o755)
            with patch.dict(os.environ, {"LEXIFLOW_JAVA_HOME": str(jdk)}):
                info = detect_java(Path(tmpdir))
                self.assertTrue(info["available"])
                self.assertEqual(info["source"], "LEXIFLOW_JAVA_HOME")

    def test_local_toolchain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            jdk = root / ".local" / "toolchains" / "jdk-25" / "bin"
            jdk.mkdir(parents=True)
            (jdk / "java").write_text("#!/bin/sh\necho java")
            os.chmod(str(jdk / "java"), 0o755)
            with patch.dict(os.environ, {}, clear=False):
                env = dict(os.environ)
                env.pop("LEXIFLOW_JAVA_HOME", None)
                with patch.dict(os.environ, env, clear=True):
                    info = detect_java(root)
                    self.assertTrue(info["available"])
                    self.assertEqual(info["source"], "local-toolchain")


class TestDiagnoseEnvironment(unittest.TestCase):
    def test_python_and_git_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = diagnose_environment(Path(tmpdir), ["python3", "git"])
            self.assertIn("python3", result["tools"])
            self.assertTrue(result["tools"]["python3"]["available"])

    def test_missing_tool_blocks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = diagnose_environment(
                Path(tmpdir), ["nonexistent-tool-xyz"],
            )
            self.assertEqual(result["status"], "BLOCKED")
            self.assertIn("nonexistent-tool-xyz", result["missing"])

    def test_default_required(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = diagnose_environment(Path(tmpdir))
            self.assertIn("python3", result["tools"])
            self.assertIn("git", result["tools"])


class TestCheckRequiredEnvironment(unittest.TestCase):
    def test_no_requirements_pass(self):
        check = {"required_environment": []}
        result = check_required_environment(Path("."), check)
        self.assertEqual(result["status"], "PASS")

    def test_missing_requirement_blocked(self):
        check = {"required_environment": ["nonexistent-tool-xyz"]}
        result = check_required_environment(Path("."), check)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("nonexistent-tool-xyz", result["missing"])

    def test_python_available(self):
        check = {"required_environment": ["python3"]}
        result = check_required_environment(Path("."), check)
        self.assertEqual(result["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
