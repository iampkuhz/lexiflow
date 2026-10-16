"""只使用合成 Git 仓库验证来源桥接边界。"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.checks.release.source import frozen_source_fixture as bridge
from tests.verification.declaration_fixtures import write_declarations


class ReleaseSourceBridgeTests(unittest.TestCase):
    """覆盖冻结输入、Git 身份、闭包、安全复制与 fixture 拒绝路径。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="release-source-bridge-test-")
        self.base = Path(self.temp.name).resolve()
        self.template = self.base / "empty-template"
        self.template.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _git(self, root: Path, *args: str) -> str:
        env = dict(bridge.FIXED_ENV)
        env.update(
            {
                "PATH": os.defpath,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TEMPLATE_DIR": str(self.template),
                "GIT_AUTHOR_NAME": "Synthetic",
                "GIT_AUTHOR_EMAIL": "synthetic@invalid",
                "GIT_COMMITTER_NAME": "Synthetic",
                "GIT_COMMITTER_EMAIL": "synthetic@invalid",
                "GIT_AUTHOR_DATE": "2001-01-01T00:00:00 +0000",
                "GIT_COMMITTER_DATE": "2001-01-01T00:00:00 +0000",
            }
        )
        return (
            subprocess.check_output(["git", "-C", str(root), *args], env=env)
            .decode()
            .strip()
        )

    def test_real_declared_closure_preserves_transitive_node_and_build_inputs(
        self,
    ) -> None:
        import re
        from scripts.verification.execution.check_runner import (
            resolve_child_check,
            snapshot_check_inputs,
        )

        root = Path(__file__).resolve().parents[2]
        for check_id in (
            "eng.release.lifecycle-runtime",
            "eng.release.candidate-runtime",
        ):
            check = resolve_child_check(root, check_id)
            snapshot = snapshot_check_inputs(root, check)
            self.assertEqual(snapshot["missing"], [])
            files = {f["locator"] for f in snapshot["files"]}
            self.assertEqual({f["locator"] for f in bridge._closure(snapshot)}, files)
            entries = [
                "ops/release/test_tools/build-synthetic-release-candidates.mjs",
                "extension/scripts/packaging/package-extension-release.mjs",
            ]
            visited = set()
            while entries:
                locator = entries.pop()
                if locator in visited:
                    continue
                visited.add(locator)
                self.assertIn(locator, files)
                text = (root / locator).read_text()
                for relative in re.findall(
                    r"(?:from\s+|import\s*\()[\"'](\.[^\"']+)[\"']", text
                ):
                    target = (root / locator).parent.joinpath(relative).resolve()
                    if target.is_file():
                        entries.append(target.relative_to(root).as_posix())
            self.assertIn("backend/gradlew", files)
            self.assertIn("backend/product/api/build.gradle.kts", files)
            self.assertIn("backend/verification/integration/build.gradle.kts", files)
            self.assertTrue(
                any(
                    p.startswith("backend/verification/integration/src/")
                    and p.endswith(".java")
                    for p in files
                )
            )
            self.assertIn("extension/package-lock.json", files)
            self.assertIn("infra/postgres/schema.sql", files)

    def test_real_frozen_fixture_executes_gradle_generation_identity(self) -> None:
        """只从真实 lifecycle 冻结闭包建 fixture，执行 bootJar 固定资料生成器。"""
        from scripts.verification.execution.check_runner import (
            resolve_child_check,
            snapshot_check_inputs,
        )

        root = Path(__file__).resolve().parents[2]
        check = resolve_child_check(root, bridge.CHECK_ID)
        snapshot = snapshot_check_inputs(root, check)
        output = self.base / "real-generation-output"
        output.mkdir()
        result = bridge.consume_verify_snapshot(
            root,
            {
                "run_id": "10000000-0000-4000-8000-000000000001",
                "check_id": bridge.CHECK_ID,
            },
            snapshot,
            output,
        )
        fixture = Path(result["fixture"])
        executed = subprocess.run(
            [
                "node",
                "ops/dataset/dataset-generation-identity.mjs",
                "--print-code-version",
            ],
            cwd=fixture,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(executed.returncode, 0, executed.stderr)
        self.assertRegex(executed.stdout.strip(), r"^[0-9a-f]{64}$")
        task_path = "backend/gradle/build-logic/src/main/kotlin/io/lexiflow/buildlogic/GenerateDataGenerationTask.kt"
        frozen_paths = {item["locator"] for item in snapshot["files"]}
        self.assertIn(task_path, frozen_paths)
        task = (fixture / task_path).read_text()
        self.assertIn(
            'commandLine(node, File(repoRoot.get(), "ops/dataset/dataset-generation-identity.mjs").absolutePath, "--output-dir", outputDirectory.get().asFile.absolutePath)',
            task,
        )
        api = (fixture / "backend/product/api/build.gradle.kts").read_text()
        self.assertRegex(
            api,
            r'tasks.named<BootJar>\("bootJar"\)\s*\{[^}]*dependsOn\(generateDataGeneration\)',
        )
        reads = self._generation_reads(fixture)
        self.assertTrue(reads.issubset(frozen_paths), sorted(reads - frozen_paths))
        self.assertTrue(bridge.BUILD_GENERATION_FILES.issubset(reads))
        expected_rows = [
            [path, hashlib.sha256((fixture / path).read_bytes()).hexdigest()]
            for path in sorted(reads)
        ]
        expected_code = hashlib.sha256(
            json.dumps(
                expected_rows, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
        self.assertEqual(executed.stdout.strip(), expected_code)
        # 每项 manifest 展开读取都逐项失效；仅改临时已复制事实，绝不补拷 root 未冻结文件。
        original_fingerprint = snapshot_check_inputs(fixture, check)["fingerprint"]
        for path in sorted(reads):
            with self.subTest(required_read=path):
                file = fixture / path
                content, mode = file.read_bytes(), file.stat().st_mode & 0o777
                try:
                    file.unlink()
                    absent = subprocess.run(
                        [
                            "node",
                            "ops/dataset/dataset-generation-identity.mjs",
                            "--print-code-version",
                        ],
                        cwd=fixture,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    if absent.returncode == 0:
                        # /** 枚举中删掉成员不会 ENOENT，但必须改变真实读取身份。
                        self.assertNotEqual(absent.stdout.strip(), expected_code, path)
                    self.assertNotEqual(
                        snapshot_check_inputs(fixture, check)["fingerprint"],
                        original_fingerprint,
                        path,
                    )
                    file.write_bytes(content + b"\n")
                    file.chmod(mode)
                    changed = subprocess.run(
                        [
                            "node",
                            "ops/dataset/dataset-generation-identity.mjs",
                            "--print-code-version",
                        ],
                        cwd=fixture,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(changed.returncode, 0, changed.stderr)
                    self.assertNotEqual(changed.stdout.strip(), expected_code, path)
                    self.assertNotEqual(
                        snapshot_check_inputs(fixture, check)["fingerprint"],
                        original_fingerprint,
                        path,
                    )
                finally:
                    file.write_bytes(content)
                    file.chmod(mode)
        self.assertEqual(
            snapshot_check_inputs(fixture, check)["fingerprint"], original_fingerprint
        )
        self.assertFalse(result["verificationRunBound"])
        self.assertFalse(result["formalReleaseEligible"])

    def _generation_reads(self, root: Path) -> set[str]:
        """测试只展开固定生成器现行 manifest 的精确文件与 /** 目录读取。"""
        manifest = json.loads(
            (root / "ops/dataset/generation-inputs.json").read_bytes()
        )
        self.assertEqual(manifest["schemaVersion"], 1)
        reads = {
            "ops/dataset/dataset-generation-identity.mjs",
            "ops/dataset/generation-inputs.json",
        }
        for pattern in manifest["include"]:
            if pattern.endswith("/**"):
                directory = root / pattern[:-3]
                self.assertTrue(directory.is_dir(), pattern)
                reads.update(
                    p.relative_to(root).as_posix()
                    for p in directory.rglob("*")
                    if p.is_file()
                )
            else:
                self.assertTrue((root / pattern).is_file(), pattern)
                reads.add(pattern)
        return reads

    def test_generation_build_anchors_required_only_by_actual_build_bridge(
        self,
    ) -> None:
        """bootJar bridge 拒绝缺生成器的合法快照，预建 candidate 最低闭包不伪扩。"""
        from scripts.verification.execution.check_runner import (
            resolve_child_check,
            snapshot_check_inputs,
        )

        repository = Path(__file__).resolve().parents[2]
        candidate = snapshot_check_inputs(
            repository, resolve_child_check(repository, "eng.release.candidate-runtime")
        )
        self.assertEqual(len(bridge._closure(candidate)), len(candidate["files"]))
        root = self._repo("missing-build-generation")
        from scripts.verification.planning.check_definitions import load_declarations

        check = load_declarations(root)["checks"][0]
        check["input_paths"] = [
            p for p in check["input_paths"] if p not in bridge.BUILD_GENERATION_FILES
        ]
        write_declarations(root / "config/engineering/module-checks.yaml", [check])
        context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)[1:]
        out = self.base / "missing-build-generation-out"
        out.mkdir()
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.consume_verify_snapshot(root, context, snapshot, out)
        self.assertEqual(caught.exception.code, "release-generation-closure-incomplete")
        self.assertEqual(list(out.iterdir()), [])

    def _repo(self, name: str = "repo", check_id: str = bridge.CHECK_ID) -> Path:
        root = self.base / name
        root.mkdir()
        all_files = set(bridge.FIXED_FILES | bridge.BUILD_GENERATION_FILES)
        dirs = {
            "backend/gradle/build-logic": "src/main/kotlin/Convention.kt",
            "backend/gradle/config": "quality.gradle.kts",
            "backend/product/api": "src/main/java/Api.java",
            "backend/product/adapters": "src/main/java/Adapter.java",
            "backend/product/enrichment": "src/main/java/Enrichment.java",
            "backend/product/lexicon": "src/main/java/Lexicon.java",
            "backend/verification/architecture": "src/test/java/ArchitectureTest.java",
            "backend/verification/integration": "src/test/java/IntegrationTest.java",
            "backend/verification/quality-gates": "src/test/java/QualityTest.java",
            "extension/assets": "icon.svg",
            "extension/scripts": "build/build-extension.mjs",
            "extension/src": "entry.ts",
        }
        for directory, relative in dirs.items():
            all_files.add(f"{directory}/{relative}")
        # 源树含固定闭包所要求的目录，不含真实项目源代码。
        for path in sorted(all_files):
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(("synthetic:" + path + "\n").encode())
            if path.endswith("gradlew") or path.endswith(".sh"):
                target.chmod(0o755)
        (root / "harness").mkdir()
        paths = sorted(
            [
                *bridge.FIXED_FILES,
                *bridge.BUILD_GENERATION_FILES,
                *bridge.FIXED_DIRS,
                "config/engineering/module-checks.yaml",
            ]
        )
        write_declarations(
            root / "config/engineering/module-checks.yaml",
            [
                {
                    "check_id": check_id,
                    "command": [
                        "python3",
                        "-m",
                        "scripts.checks.release.source.check_synthetic_release_lifecycle",
                    ],
                    "timeout_seconds": 1,
                    "scope": "change-targeted"
                    if check_id == bridge.CHECK_ID
                    else "repository-baseline",
                    "triggers": [{"path": ".gitignore"}]
                    if check_id == bridge.CHECK_ID
                    else [],
                    "module_dependencies": [],
                    "required_environment": [],
                    "input_paths": paths,
                }
            ],
        )
        self._git(root, "init", "--quiet")
        self._git(root, "config", "core.hooksPath", os.devnull)
        # 合成基线不得启动后台维护，避免对象库只读断言和清理被异步打包干扰。
        self._git(root, "config", "gc.auto", "0")
        self._git(root, "config", "maintenance.auto", "false")
        self._git(root, "add", "--all")
        self._git(root, "commit", "--quiet", "-m", "synthetic baseline")
        return root

    def _consumer_inputs(self, root: Path, check_id: str, reasons=None):
        from scripts.verification.planning.check_definitions import (
            load_declarations_snapshot,
        )
        from scripts.verification.execution.check_plan_runner import (
            _freeze_selection,
            _runtime_checks,
        )

        declarations, declaration = load_declarations_snapshot(root)
        declared = next(
            check for check in declarations["checks"] if check["check_id"] == check_id
        )
        selected = dict(declared)
        if reasons is not None:
            selected["selection_reasons"] = reasons
        effective = _runtime_checks([selected])[0]
        frozen, unchanged = _freeze_selection(root, [effective], declaration)
        self.assertTrue(unchanged)
        context = {
            "run_id": "10000000-0000-4000-8000-000000000001",
            "check_id": check_id,
        }
        return effective, context, frozen[check_id]

    def test_verify_consumer_detects_copy_time_source_drift(self) -> None:
        root = self._repo("consumer-copy-drift")
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        output = self.base / "consumer-copy-drift-out"
        output.mkdir()
        original_read = bridge._read_source
        changed = False

        def drift_during_copy(source_root, path, expected):
            nonlocal changed
            data = original_read(source_root, path, expected)
            if not changed:
                changed = True
                (root / path).write_bytes(b"changed while copying")
            return data

        with mock.patch.object(bridge, "_read_source", side_effect=drift_during_copy):
            with self.assertRaises(bridge.BridgeError):
                bridge.consume_verify_snapshot(root, context, snapshot, output)
        self.assertEqual(list(output.iterdir()), [])

    def test_baseline_verify_snapshot_and_fixture_are_mechanism_only(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        output = self.base / "out"
        output.mkdir()
        result = bridge.consume_verify_snapshot(root, context, snapshot, output)
        self.assertFalse(result["verificationRunBound"])
        self.assertFalse(result["formalReleaseEligible"])
        self.assertEqual(result["provenance"]["kind"], bridge.KIND)
        self.assertEqual(result["provenance"]["purpose"], bridge.PURPOSE)
        self.assertTrue((Path(result["directory"]) / "provenance.json").is_file())
        self.assertEqual(result["provenance"]["verifyContext"], context)
        for item in result["provenance"]["files"]:
            source = root / item["sourcePath"]
            copied = Path(result["fixture"]) / item["fixturePath"]
            self.assertEqual(source.read_bytes(), copied.read_bytes())
            self.assertEqual(
                copied.stat().st_mode & 0o777,
                0o755 if source.stat().st_mode & 0o111 else 0o644,
            )
        self.assertEqual(
            result["provenance"]["files"],
            sorted(result["provenance"]["files"], key=lambda item: item["sourcePath"]),
        )

    def test_source_index_and_object_store_remain_byte_identical(self) -> None:
        root = self._repo()
        before = self._source_git_state(root)
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        out = self.base / "readonly-out"
        out.mkdir()
        bridge.consume_verify_snapshot(root, context, snapshot, out)
        self.assertEqual(before, self._source_git_state(root))

    def _source_git_state(
        self, root: Path
    ) -> tuple[bytes, dict[str, tuple[bytes, int]]]:
        index = (root / ".git/index").read_bytes()
        objects = root / ".git/objects"
        content = {
            path.relative_to(objects).as_posix(): (
                path.read_bytes(),
                path.stat().st_mode & 0o777,
            )
            for path in sorted(objects.rglob("*"))
            if path.is_file()
        }
        return index, content

    def test_freeze_then_source_edit_or_removal_is_rejected(self) -> None:
        for remove in (False, True):
            root = self._repo("remove" if remove else "edit")
            effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
            path = root / "ops/release/version.txt"
            if remove:
                path.unlink()
            else:
                path.write_text(path.read_text() + "drift\n")
            out = self.base / ("out-remove" if remove else "out-edit")
            out.mkdir()
            with self.assertRaises(bridge.BridgeError):
                bridge.consume_verify_snapshot(root, context, snapshot, out)

    def test_added_file_after_freeze_is_rejected(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        (root / "extension/src/added.ts").write_text("synthetic\n")
        out = self.base / "out"
        out.mkdir()
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, context, snapshot, out)

    def test_required_verify_input_missing_is_rejected(self) -> None:
        root = self._repo()
        path = root / "ops/release/third-party-notices.md"
        path.unlink()
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.consume_verify_snapshot(
                root, *self._consumer_inputs(root, bridge.CHECK_ID)[1:], self.base
            )
        self.assertEqual(
            (caught.exception.status, caught.exception.code),
            ("FAIL", "verify-snapshot-mismatch"),
        )

    def test_wrong_check_id_and_forged_snapshot_are_rejected(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        out = self.base / "out"
        out.mkdir()
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.consume_verify_snapshot(
                root, {**context, "check_id": "forged"}, snapshot, out
            )
        self.assertEqual(caught.exception.code, "verify-context-invalid")
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.consume_verify_snapshot(root, context, object(), out)
        self.assertEqual(caught.exception.code, "verify-snapshot-mismatch")
        self.assertEqual(list(out.iterdir()), [])

    def test_missing_duplicate_unsorted_or_unknown_snapshot_locator_rejected(
        self,
    ) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        out = self.base / "out"
        out.mkdir()
        snapshot["files"] = snapshot["files"][:-1]
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, context, snapshot, out)
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        snapshot["files"].append(dict(snapshot["files"][0]))
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, context, snapshot, out)
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        snapshot["files"].reverse()
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, context, snapshot, out)
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        snapshot["files"].append(
            {
                "locator": "unknown/synthetic",
                "sha256": "0" * 64,
                "size_bytes": 1,
                "executable": False,
            }
        )
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, context, snapshot, out)

    def test_source_build_directories_are_not_generated_artifacts(self) -> None:
        """只允许两个精确源码目录，不把同名内部产物放入冻结闭包。"""
        for path in (
            "ops/release/build/release-image-builder.mjs",
            "extension/scripts/build/build-extension.mjs",
        ):
            with self.subTest(path=path):
                self.assertFalse(bridge._generated(path))
        for path in (
            "ops/release/build/build/generated.txt",
            "ops/release/build/dist/generated.txt",
            "extension/scripts/build/node_modules/package.js",
            "extension/scripts/build/__pycache__/module.pyc",
            "backend/product/api/build/app.jar",
            "other/build/source.mjs",
            "backend/gradle/build-logic/.kotlin/sessions/compiler.salive",
            "ops/release/build/.kotlin/errors/error.log",
        ):
            with self.subTest(path=path):
                self.assertTrue(bridge._generated(path))

    def test_fixed_release_closure_rejects_missing_and_generated_extras(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        files = snapshot["files"]
        snapshot["files"] = [
            x for x in files if x["locator"] != "ops/docker/compose.yaml"
        ]
        with self.assertRaises(bridge.BridgeError):
            bridge._closure(snapshot)
        with self.assertRaises(bridge.BridgeError):
            bridge._closure({"files": [{"locator": "extension/dist/app.js"}]})
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge._closure(
                {
                    "files": [
                        x
                        for x in files
                        if x["locator"] != "ops/release/third-party-notices.md"
                    ]
                }
            )
        self.assertEqual(
            (caught.exception.status, caught.exception.code),
            ("BLOCKED", "missing-frozen-input"),
        )

    def test_git_attributes_transformations_are_rejected_before_copy(self) -> None:
        root = self._repo("attributes")
        # 本机 Git attributes 不在复制闭包内，但必须禁止其转换已冻结源码。
        attributes = root / ".git/info/attributes"
        attributes.parent.mkdir()
        attributes.write_text("ops/release/version.txt text eol=crlf\n")
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        out = self.base / "attributes-out"
        out.mkdir()
        before = self._source_git_state(root)
        with mock.patch.object(bridge, "_read_source") as copier:
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge.consume_verify_snapshot(root, context, snapshot, out)
        self.assertEqual(
            (caught.exception.status, caught.exception.code),
            ("BLOCKED", "git-attributes-unrepresentable"),
        )
        copier.assert_not_called()
        self.assertEqual(list(out.iterdir()), [])
        self.assertEqual(before, self._source_git_state(root))

    def test_private_paths_rejected_before_target_file_read(self) -> None:
        root = self._repo()
        (root / "extension/src/.env").write_text("private synthetic\n")
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(
                root, *self._consumer_inputs(root, bridge.CHECK_ID)[1:], self.base
            )

    def test_symlink_fifo_special_and_case_collision_are_rejected(self) -> None:
        root = self._repo()
        (root / "extension/src/link.ts").symlink_to(root / "extension/src/entry.ts")
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(
                root, *self._consumer_inputs(root, bridge.CHECK_ID)[1:], self.base
            )
        root = self._repo("fifo")
        os.mkfifo(root / "extension/src/fifo.ts")
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(
                root, *self._consumer_inputs(root, bridge.CHECK_ID)[1:], self.base
            )
        effective, context, snapshot = self._consumer_inputs(
            self._repo("case"), bridge.CHECK_ID
        )
        first = snapshot["files"][0]
        snapshot["files"].append({**first, "locator": first["locator"].swapcase()})
        with self.assertRaises(bridge.BridgeError):
            bridge._closure(snapshot)

    def test_copy_hash_size_and_executable_mode_mismatch_are_rejected(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        item = next(
            x for x in snapshot["files"] if x["locator"] == "ops/release/version.txt"
        )
        original = bridge._read_source
        out = self.base / "out"
        out.mkdir()
        with mock.patch.object(bridge, "_read_source", return_value=b"tampered"):
            with self.assertRaises(bridge.BridgeError):
                bridge.consume_verify_snapshot(root, context, snapshot, out)
        item["size_bytes"] += 1
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, context, snapshot, out)
        self.assertTrue(callable(original))
        self.assertEqual(list(out.iterdir()), [])

    def test_short_read_and_copy_executable_bit_mismatch_are_rejected(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        out = self.base / "short-read-out"
        out.mkdir()
        watched_inode = (root / ".gitignore").stat().st_ino
        real_read = bridge.os.read

        def short_source_read(fd: int, size: int) -> bytes:
            if os.fstat(fd).st_ino == watched_inode:
                return b""
            return real_read(fd, size)

        with (
            mock.patch.object(bridge, "snapshot_check_inputs", return_value=snapshot),
            mock.patch.object(bridge.os, "read", side_effect=short_source_read),
        ):
            with self.assertRaises(bridge.BridgeError):
                bridge.consume_verify_snapshot(root, context, snapshot, out)
        self.assertEqual(list(out.iterdir()), [])

        root = self._repo("mode")
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        out = self.base / "mode-out"
        out.mkdir()
        real_fchmod = bridge.os.fchmod
        with mock.patch.object(
            bridge.os,
            "fchmod",
            side_effect=lambda fd, mode: real_fchmod(fd, mode ^ 0o111),
        ):
            with self.assertRaises(bridge.BridgeError):
                bridge.consume_verify_snapshot(root, context, snapshot, out)
        self.assertEqual(list(out.iterdir()), [])

    def test_output_inside_existing_or_symlink_parent_is_rejected(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, context, snapshot, root / "extension")
        existing = self.base / "exists"
        existing.write_text("not a directory")
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, context, snapshot, existing)
        link = self.base / "link"
        link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, context, snapshot, link)

    def test_atomic_publish_race_preserves_empty_nonempty_and_symlink_targets(
        self,
    ) -> None:
        for kind in ("empty-directory", "nonempty-directory", "symlink"):
            root = self._repo(kind)
            effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
            out = self.base / f"race-out-{kind}"
            out.mkdir()
            target_record: dict[str, object] = {}
            original_publish = bridge._publish_noreplace

            def race_after_precheck(stage: Path, final: Path) -> None:
                if kind == "empty-directory" or kind == "nonempty-directory":
                    final.mkdir()
                    if kind == "nonempty-directory":
                        (final / "sentinel").write_text("pre-existing")
                else:
                    final.symlink_to(self.base, target_is_directory=True)
                target_record["path"] = final
                target_record["identity"] = (final.lstat().st_dev, final.lstat().st_ino)
                if kind == "symlink":
                    target_record["link"] = os.readlink(final)
                original_publish(stage, final)

            with mock.patch.object(
                bridge, "_publish_noreplace", side_effect=race_after_precheck
            ):
                with self.assertRaises(bridge.BridgeError) as caught:
                    bridge.consume_verify_snapshot(root, context, snapshot, out)
            self.assertEqual(
                (caught.exception.status, caught.exception.code),
                ("FAIL", "output-target-exists"),
            )
            final = target_record["path"]
            after = final.lstat()
            self.assertEqual((after.st_dev, after.st_ino), target_record["identity"])
            if kind == "nonempty-directory":
                self.assertEqual((final / "sentinel").read_text(), "pre-existing")
            if kind == "symlink":
                self.assertTrue(final.is_symlink())
                self.assertEqual(os.readlink(final), target_record["link"])
            self.assertFalse((final / "provenance.json").exists())
            self.assertFalse(
                any(p.name.startswith(".release-source-") for p in out.iterdir())
            )

    def test_atomic_publish_unsupported_and_errno_mapping(self) -> None:
        source, destination = self.base / "stage", self.base / "target"
        for result, expected in (
            (None, ("BLOCKED", "atomic-no-replace-unavailable")),
            ((-1, errno.EEXIST), ("FAIL", "output-target-exists")),
            (
                (-1, getattr(errno, "ENOTSUP", errno.EINVAL)),
                ("BLOCKED", "atomic-no-replace-unavailable"),
            ),
            ((-1, errno.EACCES), ("FAIL", "output-publish-failed")),
        ):
            with mock.patch.object(
                bridge, "_rename_noreplace_syscall", return_value=result
            ):
                with self.assertRaises(bridge.BridgeError) as caught:
                    bridge._publish_noreplace(source, destination)
            self.assertEqual((caught.exception.status, caught.exception.code), expected)

    def test_linux_atomic_publish_uses_renameat2_noreplace(self) -> None:
        source, destination = self.base / "stage", self.base / "target"
        for result, expected_errno in ((0, 0), (-1, errno.EEXIST)):
            with self.subTest(result=result):
                libc = mock.Mock()
                libc.renameat2.return_value = result
                with (
                    mock.patch.object(bridge.sys, "platform", "linux"),
                    mock.patch.object(bridge.ctypes, "CDLL", return_value=libc),
                    mock.patch.object(
                        bridge.ctypes, "get_errno", return_value=expected_errno
                    ),
                ):
                    outcome = bridge._rename_noreplace_syscall(source, destination)
                self.assertEqual(outcome, (result, expected_errno))
                libc.renameat2.assert_called_once_with(
                    -100, os.fsencode(source), -100, os.fsencode(destination), 1
                )
                libc.renamex_np.assert_not_called()

    def test_linux_without_renameat2_is_blocked_without_fallback(self) -> None:
        with (
            mock.patch.object(bridge.sys, "platform", "linux"),
            mock.patch.object(bridge.ctypes, "CDLL", return_value=object()),
        ):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge._publish_noreplace(self.base / "stage", self.base / "target")
        self.assertEqual(
            (caught.exception.status, caught.exception.code),
            ("BLOCKED", "atomic-no-replace-unavailable"),
        )

    def test_unsupported_platform_is_blocked_without_rename_fallback(self) -> None:
        stage, destination = self.base / "stage", self.base / "target"
        with mock.patch.object(bridge.sys, "platform", "unsupported-test-platform"):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge._publish_noreplace(stage, destination)
        self.assertEqual(
            (caught.exception.status, caught.exception.code),
            ("BLOCKED", "atomic-no-replace-unavailable"),
        )

    def test_extra_fixture_member_dirty_fixture_and_wrong_tree_are_rejected(
        self,
    ) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        out = self.base / "extra-out"
        out.mkdir()
        original_git = bridge._git

        def extra_tree(repo: Path, *args: str, env=None, input=None) -> bytes:
            result = original_git(repo, *args, env=env, input=input)
            if args and args[0] == "ls-tree":
                return result + b"unexpected-member\n"
            return result

        with mock.patch.object(bridge, "_git", side_effect=extra_tree):
            with self.assertRaises(bridge.BridgeError):
                bridge.consume_verify_snapshot(root, context, snapshot, out)

        root = self._repo("dirty-fixture")
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        out = self.base / "dirty-fixture-out"
        out.mkdir()

        def dirty_fixture(repo: Path, *args: str, env=None, input=None) -> bytes:
            if Path(repo).name == "fixture" and args and args[0] == "status":
                return b" M injected\0"
            return original_git(repo, *args, env=env, input=input)

        with mock.patch.object(bridge, "_git", side_effect=dirty_fixture):
            with self.assertRaises(bridge.BridgeError):
                bridge.consume_verify_snapshot(root, context, snapshot, out)

        root = self._repo("wrong-tree")
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        out = self.base / "wrong-tree-out"
        out.mkdir()
        original_git_text = bridge._git_text

        def wrong_tree(repo: Path, *args: str, env=None) -> str:
            if args == ("rev-parse", "HEAD^{tree}"):
                return "0" * 40
            return original_git_text(repo, *args, env=env)

        with mock.patch.object(bridge, "_git_text", side_effect=wrong_tree):
            with self.assertRaises(bridge.BridgeError):
                bridge.consume_verify_snapshot(root, context, snapshot, out)

    def test_repeated_same_input_has_canonical_identical_provenance(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        a, b = self.base / "out-a", self.base / "out-b"
        a.mkdir()
        b.mkdir()
        first = bridge.consume_verify_snapshot(root, context, snapshot, a)
        second = bridge.consume_verify_snapshot(
            root, *self._consumer_inputs(root, bridge.CHECK_ID)[1:], b
        )
        self.assertEqual(first["provenanceBytes"], second["provenanceBytes"])
        self.assertEqual(
            hashlib.sha256(first["provenanceBytes"]).hexdigest(),
            first["provenanceSha256"],
        )

    def test_failed_publication_leaves_no_success_receipt(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        out = self.base / "out"
        out.mkdir()
        with mock.patch.object(
            bridge,
            "_read_source",
            side_effect=bridge.BridgeError("FAIL", "copy-injected"),
        ):
            with self.assertRaises(bridge.BridgeError):
                bridge.consume_verify_snapshot(root, context, snapshot, out)
        self.assertFalse(
            any(p.name.startswith("release-source-") for p in out.iterdir())
        )
        self.assertFalse(
            any(p.name.startswith(".release-source-") for p in out.iterdir())
        )


if __name__ == "__main__":
    unittest.main()
