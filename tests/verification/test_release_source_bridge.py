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

from scripts.verification import release_source_bridge as bridge
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

    def test_bulk_index_identity_matches_per_entry_oracle_across_states(self) -> None:
        root = self.base / "identity-states"
        root.mkdir()
        (root / "路径 with space.txt").write_text("ordinary\n", encoding="utf-8")
        executable = root / "可执行.sh"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
        (root / "link").symlink_to("路径 with space.txt")
        self._git(root, "init", "--quiet")
        self._git(root, "config", "core.hooksPath", os.devnull)
        self._git(root, "add", "--all")
        self._git(root, "commit", "--quiet", "-m", "identity states")
        original = bridge._git

        def per_entry(repo: Path, *args: str, env=None, input=None) -> bytes:
            # Only replace transport with the prior algorithm, not identity reads.
            if args == ("update-index", "-z", "--index-info"):
                for row in input.split(b"\0"):
                    if row:
                        meta, path = row.split(b"\t", 1)
                        mode, oid, stage = meta.decode("ascii").split()
                        self.assertEqual(stage, "0")
                        original(
                            repo,
                            "update-index",
                            "--add",
                            "--cacheinfo",
                            mode,
                            oid,
                            path.decode("utf-8"),
                            env=env,
                        )
                return b""
            return original(repo, *args, env=env, input=input)

        previous = None
        for state in ("clean", "staged", "unstaged", "untracked", "executable", "head"):
            with self.subTest(state=state):
                if state == "staged":
                    (root / "路径 with space.txt").write_text("staged\n")
                    self._git(root, "add", "--", "路径 with space.txt")
                elif state == "unstaged":
                    executable.write_text("#!/bin/sh\n# unstaged\n")
                elif state == "untracked":
                    (root / "new.txt").write_text("new\n")
                elif state == "executable":
                    (root / "new.txt").chmod(0o755)
                elif state == "head":
                    self._git(root, "add", "--all")
                    self._git(root, "commit", "--quiet", "-m", "next identity")
                before = self._source_git_state(root)
                with mock.patch.object(bridge, "_git", side_effect=per_entry):
                    expected = bridge._git_identity(root)
                with mock.patch.object(bridge, "_git", wraps=original) as git:
                    actual = bridge._git_identity(root)
                writes = [
                    call
                    for call in git.call_args_list
                    if call.args[1:2] == ("update-index",)
                ]
                self.assertEqual(actual, expected)
                self.assertEqual(len(writes), 1)
                self.assertEqual(
                    writes[0].args[1:], ("update-index", "-z", "--index-info")
                )
                self.assertIn("路径 with space.txt".encode(), writes[0].kwargs["input"])
                self.assertIn(b"120000 ", writes[0].kwargs["input"])
                self.assertIn(b"100755 ", writes[0].kwargs["input"])
                self.assertEqual(before, self._source_git_state(root))
                if previous is not None:
                    self.assertNotEqual(actual, previous)
                previous = actual

    def test_bulk_index_rejects_every_unsafe_row_before_write(self) -> None:
        root = self._repo("unsafe-index")
        original = bridge._git
        rows = original(root, "ls-files", "--stage", "-z")
        oid = rows.split(b"\t", 1)[0].split()[1]
        invalid = [
            b"invalid",
            b"100644 " + oid + b" 0\t../escape",
            b"100644 " + oid + b" 0\t.env",
            b"100644 " + oid + b" 0\tcontrol\nname",
            b"100644 " + oid + b" 0\t\xff",
            b"100644 " + oid + b" 1\tconflict",
            b"160000 " + oid + b" 0\tsubmodule",
            b"100644 " + b"0" * 40 + b" 0\tintent-to-add",
        ]
        before = self._source_git_state(root)
        for row in invalid:
            with self.subTest(row=row):

                def injected(repo: Path, *args: str, env=None, input=None) -> bytes:
                    if args == ("ls-files", "--stage", "-z"):
                        return rows + row + b"\0"
                    return original(repo, *args, env=env, input=input)

                with mock.patch.object(bridge, "_git", side_effect=injected) as git:
                    with self.assertRaises(bridge.BridgeError) as caught:
                        bridge._git_identity(root)
                self.assertEqual(caught.exception.status, "BLOCKED")
                self.assertFalse(
                    any(
                        call.args[1:2] == ("update-index",)
                        for call in git.call_args_list
                    )
                )
                self.assertEqual(before, self._source_git_state(root))

    def test_empty_index_identity_uses_no_bulk_write(self) -> None:
        root = self._repo("empty-index")
        self._git(root, "read-tree", "--empty")
        before = self._source_git_state(root)
        with mock.patch.object(bridge, "_git", wraps=bridge._git) as git:
            identity = bridge._git_identity(root)
        self.assertEqual(
            identity["indexTree"],
            self._git(root, "hash-object", "-t", "tree", os.devnull),
        )
        self.assertFalse(
            any(call.args[1:2] == ("update-index",) for call in git.call_args_list)
        )
        self.assertEqual(before, self._source_git_state(root))

    def test_bulk_write_process_failure_cannot_return_identity(self) -> None:
        root = self._repo("failed-bulk")
        before = self._source_git_state(root)
        run = bridge.subprocess.run

        def failed(argv, **kwargs):
            if argv[3:6] == ["update-index", "-z", "--index-info"]:
                raise subprocess.CalledProcessError(128, argv, stderr=b"synthetic")
            return run(argv, **kwargs)

        with mock.patch.object(bridge.subprocess, "run", side_effect=failed):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge._git_identity(root)
        self.assertEqual(
            (caught.exception.status, caught.exception.code),
            ("BLOCKED", "git-metadata-unavailable"),
        )
        self.assertEqual(before, self._source_git_state(root))

    def _repo(self, name: str = "repo", check_id: str = bridge.CHECK_ID) -> Path:
        root = self.base / name
        root.mkdir()
        all_files = set(bridge.FIXED_FILES)
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
            "extension/scripts": "build.mjs",
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
            [*bridge.FIXED_FILES, *bridge.FIXED_DIRS, "harness/module-checks.yaml"]
        )
        write_declarations(
            root / "harness/module-checks.yaml",
            [
                {
                    "check_id": check_id,
                    "module": "synthetic",
                    "command": ["bash", "synthetic.sh"],
                    "timeout_seconds": 1,
                    "scope": "change-targeted"
                    if check_id == bridge.CHANGE_CHECK_ID
                    else "repository-baseline",
                    "triggers": [{"path": ".gitignore"}]
                    if check_id == bridge.CHANGE_CHECK_ID
                    else [],
                    "module_dependencies": [],
                    "required_environment": [],
                    "input_paths": paths,
                }
            ],
        )
        self._git(root, "init", "--quiet")
        self._git(root, "config", "core.hooksPath", os.devnull)
        self._git(root, "add", "--all")
        self._git(root, "commit", "--quiet", "-m", "synthetic baseline")
        return root

    def _consumer_inputs(self, root: Path, check_id: str, reasons=None):
        from scripts.verification.declarations import load_declarations_snapshot
        from scripts.verification.scenarios import _freeze_selection, _runtime_checks

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
            "run_id": "synthetic-verify-run",
            "check_id": check_id,
            "check_config_fingerprint": bridge.fingerprint_json(effective),
        }
        return effective, context, frozen[check_id]

    def test_verify_consumer_accepts_both_fixed_check_ids(self) -> None:
        for index, check_id in enumerate((bridge.CHECK_ID, bridge.CHANGE_CHECK_ID)):
            with self.subTest(check_id=check_id):
                root = self._repo(f"consumer-{index}", check_id)
                reasons = (
                    [
                        {
                            "kind": "changed-file",
                            "changed_file": ".gitignore",
                            "trigger_path": ".gitignore",
                        }
                    ]
                    if check_id == bridge.CHANGE_CHECK_ID
                    else None
                )
                effective, context, snapshot = self._consumer_inputs(
                    root, check_id, reasons
                )
                output = self.base / f"consumer-out-{index}"
                output.mkdir()
                with mock.patch.object(
                    bridge, "snapshot_check_inputs", wraps=bridge.snapshot_check_inputs
                ) as freshness:
                    result = bridge.consume_verify_snapshot(
                        root, effective, context, snapshot, output
                    )
                self.assertGreaterEqual(freshness.call_count, 4)
                self.assertFalse(result["verificationRunBound"])
                self.assertFalse(result["formalReleaseEligible"])
                self.assertEqual(
                    result["provenance"]["verifyInputFingerprint"],
                    snapshot["fingerprint"],
                )
                self.assertEqual(result["provenance"]["verifyContext"], context)

    def test_verify_consumer_rejects_context_extra_fields_and_wrong_selection_reason(
        self,
    ) -> None:
        root = self._repo("consumer-invalid", bridge.CHANGE_CHECK_ID)
        reason = [
            {
                "kind": "changed-file",
                "changed_file": ".gitignore",
                "trigger_path": ".gitignore",
            }
        ]
        effective, context, snapshot = self._consumer_inputs(
            root, bridge.CHANGE_CHECK_ID, reason
        )
        output = self.base / "consumer-invalid-out"
        output.mkdir()
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(
                root, effective, {**context, "extra": True}, snapshot, output
            )
        bad_reason = [
            {
                "kind": "changed-file",
                "changed_file": "secret.txt",
                "trigger_path": ".gitignore",
            }
        ]
        bad_effective, bad_context, _ = self._consumer_inputs(
            root, bridge.CHANGE_CHECK_ID, bad_reason
        )
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(
                root, bad_effective, bad_context, snapshot, output
            )
        self.assertEqual(list(output.iterdir()), [])

    def test_verify_consumer_rejects_snapshot_mismatch_and_added_input_path(
        self,
    ) -> None:
        root = self._repo("consumer-snapshot")
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        output = self.base / "consumer-snapshot-out"
        output.mkdir()
        changed = {**snapshot, "fingerprint": "0" * 64}
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, effective, context, changed, output)
        expanded = {
            **effective,
            "input_paths": [*effective["input_paths"], "unlisted.txt"],
        }
        context = {
            **context,
            "check_config_fingerprint": bridge.fingerprint_json(expanded),
        }
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, expanded, context, snapshot, output)
        self.assertEqual(list(output.iterdir()), [])

    def test_verify_consumer_rejects_boolean_integer_type_collisions(self) -> None:
        root = self._repo("consumer-type-collisions")
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        output = self.base / "consumer-type-out"
        output.mkdir()
        malformed = {**effective, "timeout_seconds": True}
        self.assertEqual(
            malformed, effective
        )  # Ordinary Python equality is insufficient.
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.consume_verify_snapshot(root, malformed, context, snapshot, output)
        self.assertEqual(caught.exception.code, "verify-context-invalid")
        malformed_snapshot = json.loads(json.dumps(snapshot))
        item = malformed_snapshot["files"][0]
        item["executable"] = int(item["executable"])
        self.assertEqual(malformed_snapshot, snapshot)
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.consume_verify_snapshot(
                root, effective, context, malformed_snapshot, output
            )
        self.assertEqual(caught.exception.code, "verify-snapshot-mismatch")
        self.assertEqual(list(output.iterdir()), [])

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
                bridge.consume_verify_snapshot(
                    root, effective, context, snapshot, output
                )
        self.assertEqual(list(output.iterdir()), [])

    def test_baseline_verify_snapshot_and_fixture_are_mechanism_only(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        output = self.base / "out"
        output.mkdir()
        result = bridge.consume_verify_snapshot(
            root, effective, context, snapshot, output
        )
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
        bridge.consume_verify_snapshot(root, effective, context, snapshot, out)
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

    def test_staged_change_changes_index_identity(self) -> None:
        root = self._repo()
        original = bridge._git_identity(root)
        target = root / ".gitignore"
        target.write_text(target.read_text() + "staged\n")
        self._git(root, "add", ".gitignore")
        changed = bridge._git_identity(root)
        self.assertEqual(original["head"], changed["head"])
        self.assertNotEqual(original["indexTree"], changed["indexTree"])

    def test_unstaged_change_changes_dirty_identity(self) -> None:
        root = self._repo()
        original = bridge._git_identity(root)
        target = root / ".gitignore"
        target.write_text(target.read_text() + "unstaged\n")
        changed = bridge._git_identity(root)
        self.assertEqual(original["head"], changed["head"])
        self.assertNotEqual(original["dirtyFingerprint"], changed["dirtyFingerprint"])

    def test_untracked_content_and_executable_bit_change_identity(self) -> None:
        root = self._repo()
        path = root / "untracked-synthetic.txt"
        path.write_text("one\n")
        first = bridge._git_identity(root)
        path.write_text("two\n")
        second = bridge._git_identity(root)
        self.assertNotEqual(first["dirtyFingerprint"], second["dirtyFingerprint"])
        path.chmod(0o755)
        third = bridge._git_identity(root)
        self.assertNotEqual(second["dirtyFingerprint"], third["dirtyFingerprint"])

    def test_head_change_changes_identity(self) -> None:
        root = self._repo()
        first = bridge._git_identity(root)
        (root / "synthetic-extra.txt").write_text("head two\n")
        self._git(root, "add", "synthetic-extra.txt")
        self._git(root, "commit", "--quiet", "-m", "second synthetic commit")
        second = bridge._git_identity(root)
        self.assertNotEqual(first["head"], second["head"])

    def test_index_worktree_cross_drift_is_rejected(self) -> None:
        root = self._repo()
        p = root / ".gitignore"
        p.write_text(p.read_text() + "index\n")
        self._git(root, "add", ".gitignore")
        p.write_text(p.read_text() + "worktree\n")
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(
                root, *self._consumer_inputs(root, bridge.CHECK_ID), self.base
            )

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
                bridge.consume_verify_snapshot(root, effective, context, snapshot, out)

    def test_added_file_after_freeze_is_rejected(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        (root / "extension/src/added.ts").write_text("synthetic\n")
        out = self.base / "out"
        out.mkdir()
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, effective, context, snapshot, out)

    def test_required_verify_input_missing_is_rejected(self) -> None:
        root = self._repo()
        path = root / "ops/release/third-party-notices.md"
        path.unlink()
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.consume_verify_snapshot(
                root, *self._consumer_inputs(root, bridge.CHECK_ID), self.base
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
                root, {**effective, "check_id": "forged"}, context, snapshot, out
            )
        self.assertEqual(caught.exception.code, "verify-context-invalid")
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.consume_verify_snapshot(root, effective, context, object(), out)
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
            bridge.consume_verify_snapshot(root, effective, context, snapshot, out)
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        snapshot["files"].append(dict(snapshot["files"][0]))
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, effective, context, snapshot, out)
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        snapshot["files"].reverse()
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, effective, context, snapshot, out)
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
            bridge.consume_verify_snapshot(root, effective, context, snapshot, out)

    def test_self_reported_verified_and_fake_check_identity_are_rejected(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        out = self.base / "out"
        out.mkdir()
        snapshot["verified"] = True
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, effective, context, snapshot, out)
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        effective["check_id"] = "forged"
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, effective, context, snapshot, out)

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
                bridge.consume_verify_snapshot(root, effective, context, snapshot, out)
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
                root, *self._consumer_inputs(root, bridge.CHECK_ID), self.base
            )

    def test_private_git_input_outside_check_snapshot_is_rejected(self) -> None:
        root = self._repo("private-git-input")
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        (root / ".env").write_text("synthetic private Git input\n")
        out = self.base / "private-git-out"
        out.mkdir()
        before = self._source_git_state(root)
        with mock.patch.object(bridge, "_read_dirty_path") as dirty_reader:
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge.consume_verify_snapshot(root, effective, context, snapshot, out)
        self.assertEqual(
            (caught.exception.status, caught.exception.code),
            ("BLOCKED", "git-path-unrepresentable"),
        )
        dirty_reader.assert_not_called()
        self.assertEqual(list(out.iterdir()), [])
        self.assertEqual(before, self._source_git_state(root))

    def test_symlink_fifo_special_and_case_collision_are_rejected(self) -> None:
        root = self._repo()
        (root / "extension/src/link.ts").symlink_to(root / "extension/src/entry.ts")
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(
                root, *self._consumer_inputs(root, bridge.CHECK_ID), self.base
            )
        root = self._repo("fifo")
        os.mkfifo(root / "extension/src/fifo.ts")
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(
                root, *self._consumer_inputs(root, bridge.CHECK_ID), self.base
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
                bridge.consume_verify_snapshot(root, effective, context, snapshot, out)
        item["size_bytes"] += 1
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, effective, context, snapshot, out)
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
                bridge.consume_verify_snapshot(root, effective, context, snapshot, out)
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
                bridge.consume_verify_snapshot(root, effective, context, snapshot, out)
        self.assertEqual(list(out.iterdir()), [])

    def test_output_inside_existing_or_symlink_parent_is_rejected(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(
                root, effective, context, snapshot, root / "extension"
            )
        existing = self.base / "exists"
        existing.write_text("not a directory")
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, effective, context, snapshot, existing)
        link = self.base / "link"
        link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(bridge.BridgeError):
            bridge.consume_verify_snapshot(root, effective, context, snapshot, link)

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
                    bridge.consume_verify_snapshot(
                        root, effective, context, snapshot, out
                    )
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
                bridge.consume_verify_snapshot(root, effective, context, snapshot, out)

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
                bridge.consume_verify_snapshot(root, effective, context, snapshot, out)

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
                bridge.consume_verify_snapshot(root, effective, context, snapshot, out)

    def test_repeated_same_input_has_canonical_identical_provenance(self) -> None:
        root = self._repo()
        effective, context, snapshot = self._consumer_inputs(root, bridge.CHECK_ID)
        a, b = self.base / "out-a", self.base / "out-b"
        a.mkdir()
        b.mkdir()
        first = bridge.consume_verify_snapshot(root, effective, context, snapshot, a)
        second = bridge.consume_verify_snapshot(
            root, *self._consumer_inputs(root, bridge.CHECK_ID), b
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
                bridge.consume_verify_snapshot(root, effective, context, snapshot, out)
        self.assertFalse(
            any(p.name.startswith("release-source-") for p in out.iterdir())
        )
        self.assertFalse(
            any(p.name.startswith(".release-source-") for p in out.iterdir())
        )


if __name__ == "__main__":
    unittest.main()
