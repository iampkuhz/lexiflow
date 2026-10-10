"""验证 Java 原生构建与 Python 启动器的真实责任边界。"""

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.local import run_with_java_toolchain as java_exec


REPO = Path(__file__).resolve().parents[2]


class TestQualityGateLayering(unittest.TestCase):
    def test_java_launcher_preserves_native_argv_and_selected_environment(self):
        """启动器只选择 Java 环境，再原样交给 Gradle，不复制 Java 检查。"""
        argv = ["backend/gradlew", "-p", "backend", "deliveryFull"]
        java_home = Path("/isolated/jdk-25")
        with (
            patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True),
            patch.object(java_exec, "command_local_config", return_value={}),
            patch.object(java_exec, "resolve_java_home", return_value=java_home),
            patch.object(java_exec.os, "execvpe") as execute,
        ):
            java_exec.main(argv, root=REPO)
        execute.assert_called_once_with(
            argv[0],
            argv,
            {"PATH": "/isolated/jdk-25/bin:/usr/bin", "JAVA_HOME": str(java_home)},
        )

    def test_gradle_aggregate_owns_java_delivery_rules(self):
        """检验真实构建入口，不读取一份仅复述 Gradle 的配置。"""
        build = (REPO / "backend/build.gradle.kts").read_text()
        check = build.split('tasks.named("check") {', 1)[1].split(
            'tasks.register("deliveryFull") {', 1
        )[0]
        for task in (
            "verifyProductLanguage",
            "verifyProjectDependencies",
            "architectureTest",
            "javaSourceGates",
            "verifyNoSkippedJavaTests",
        ):
            with self.subTest(task=task):
                self.assertIn(task, check)
        delivery = build.split('tasks.register("deliveryFull") {', 1)[1].split(
            '\ntasks.register("spotlessApply")', 1
        )[0]
        for task in (
            '"check"',
            '":adapters:postgresIntegrationTest"',
            '":integration-tests:runtimeSmokeTest"',
            '":api:bootJar"',
        ):
            with self.subTest(task=task):
                self.assertIn(task, delivery)

    def test_retired_declaration_layers_remain_absent(self):
        """不恢复没有执行消费者的配置壳。"""
        for name in ("java-product.manifest.yaml", "gate-check-registry.yaml"):
            with self.subTest(name=name):
                self.assertFalse((REPO / "harness" / name).exists())


if __name__ == "__main__":
    unittest.main()
