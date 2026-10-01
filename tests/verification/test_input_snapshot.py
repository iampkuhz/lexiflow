"""合成文件树验证 Verify 输入快照的路径、读取与漂移安全边界。"""

from __future__ import annotations

import os
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.verification.input_snapshot import snapshot_inputs
from scripts.verification.kernel import fingerprint_json, snapshot_check_inputs
from scripts.verification.reports import _snapshot_shape, validate_report
from scripts.verification.scenarios import freeze_inputs, verify_repository


def _check(*paths: str) -> dict:
    return {"check_id": "snapshot.test", "input_paths": list(paths)}


class TestInputSnapshot(unittest.TestCase):
    def test_invalid_explicit_collection_rejects_without_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "safe").write_bytes(b"synthetic source")
            for inputs in ("safe", 42, ["safe", 42], [None]):
                with (
                    self.subTest(inputs=inputs),
                    patch(
                        "scripts.verification.input_snapshot.os.read",
                        side_effect=AssertionError("invalid input read"),
                    ),
                ):
                    files, missing = snapshot_inputs(root, {"input_paths": inputs})
                self.assertEqual(files, [])
                self.assertTrue(missing)

    def test_explicit_generated_paths_fail_instead_of_empty_freeze(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "build").mkdir()
            (root / "build" / "artifact").write_bytes(b"generated")
            paths = ["build", "build/artifact", "cache.pyc", "node_modules/pkg/file"]
            with patch(
                "scripts.verification.input_snapshot.os.read",
                side_effect=AssertionError("explicit generated input read"),
            ):
                files, missing = snapshot_inputs(root, _check(*paths))
            self.assertEqual((files, missing), ([], sorted(paths)))
            self._declared(root, "build")
            result = freeze_inputs(root)
            self.assertEqual(
                (result["result"], result["reason"]), ("FAIL", "input-missing")
            )

    def test_excluded_directory_same_name_replacement_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "build").mkdir(parents=True)
            (root / "replacement").mkdir()
            (root / "src" / "source").write_bytes(b"source")
            original = os.read
            changed = False

            def swap(fd, size):
                nonlocal changed
                data = original(fd, size)
                if data and not changed:
                    (root / "src" / "build").rename(root / "old")
                    (root / "replacement").rename(root / "src" / "build")
                    changed = True
                return data

            with patch("scripts.verification.input_snapshot.os.read", side_effect=swap):
                files, missing = snapshot_inputs(root, _check("src"))
            self.assertEqual((files, missing), ([], ["src"]))

    @staticmethod
    def _declared(root: Path, input_path: str) -> None:
        (root / "harness").mkdir()
        (root / "harness" / "module-checks.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": "lexiflow.module-checks.v1",
                    "checks": [
                        {
                            "check_id": "snapshot.test",
                            "module": "snapshot",
                            "command": ["python3", "-c", "pass"],
                            "cwd": ".",
                            "timeout_seconds": 10,
                            "scope": "repository-baseline",
                            "triggers": [{"path": "src/"}],
                            "module_dependencies": [],
                            "required_environment": [],
                            "input_paths": [input_path],
                            "result_contract": {
                                "type": "exit-code",
                                "completeness_guarantee": "fixture",
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _runner(*_args):
        return {
            "status": "PASS",
            "exit_code": 0,
            "exit_reason": "exited",
            "stdout": "",
            "stderr": "",
            "duration_seconds": 0.01,
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:00Z",
            "timed_out": False,
            "executable": sys.executable,
        }

    def test_missing_primitives_or_root_never_freezes_empty_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").write_bytes(b"a")
            with patch(
                "scripts.verification.input_snapshot._require_primitives",
                side_effect=OSError,
            ):
                files, missing = snapshot_inputs(root, _check("a"))
            self.assertEqual((files, missing), ([], ["a"]))
            self.assertEqual(snapshot_inputs(root / "absent", _check("a")), ([], ["a"]))

    def test_ancestor_and_nested_links_reject_without_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "real" / "nested").mkdir(parents=True)
            (root / "real" / "nested" / "file").write_bytes(b"secret")
            (root / "alias").symlink_to("real", target_is_directory=True)
            (root / "real" / "link").symlink_to("nested", target_is_directory=True)
            with patch(
                "scripts.verification.input_snapshot.os.read",
                side_effect=AssertionError("read"),
            ):
                self.assertEqual(
                    snapshot_inputs(
                        root, _check("alias/nested/file", "real/link/file")
                    ),
                    ([], ["alias/nested/file", "real/link/file"]),
                )

    def test_growth_is_bounded_and_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").write_bytes(b"a")
            sizes = []

            def grow(_fd, size):
                sizes.append(size)
                return b"x" * size

            with patch("scripts.verification.input_snapshot.os.read", side_effect=grow):
                files, missing = snapshot_inputs(root, _check("a"))
            self.assertEqual((files, missing), ([], ["a"]))
            self.assertEqual(sizes, [2])

    def test_directory_replacement_after_open_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ancestor" / "nested").mkdir(parents=True)
            (root / "ancestor" / "nested" / "file").write_bytes(b"a")
            original = os.read
            replaced = False

            def swap(fd, size):
                nonlocal replaced
                data = original(fd, size)
                if data and not replaced:
                    os.rename(root / "ancestor", root / "old")
                    (root / "ancestor").mkdir()
                    replaced = True
                return data

            with patch("scripts.verification.input_snapshot.os.read", side_effect=swap):
                files, missing = snapshot_inputs(root, _check("ancestor/nested/file"))
            self.assertEqual((files, missing), ([], ["ancestor/nested/file"]))

    def test_nested_directory_replacement_and_membership_race_rejected(self):
        for change in ("replace", "add"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "src" / "nested").mkdir(parents=True)
                (root / "src" / "nested" / "file").write_bytes(b"a")
                original = os.read
                changed = False

                def mutate(fd, size):
                    nonlocal changed
                    data = original(fd, size)
                    if data and not changed:
                        if change == "replace":
                            os.rename(root / "src" / "nested", root / "src" / "old")
                            (root / "src" / "nested").mkdir()
                        else:
                            (root / "src" / "nested" / "new").write_bytes(b"n")
                        changed = True
                    return data

                with patch(
                    "scripts.verification.input_snapshot.os.read", side_effect=mutate
                ):
                    files, missing = snapshot_inputs(root, _check("src"))
                self.assertEqual((files, missing), ([], ["src"]))

    def test_generated_and_private_entries_never_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "build").mkdir(parents=True)
            (root / "src" / "build" / "output").write_bytes(b"generated")
            (root / "src" / "cache.pyc").write_bytes(b"generated")
            (root / "src" / "ok.py").write_bytes(b"ok")
            (root / ".local").mkdir()
            (root / ".local" / "secret").write_bytes(b"private")
            with patch(
                "scripts.verification.input_snapshot.os.read", wraps=os.read
            ) as reader:
                files, missing = snapshot_inputs(root, _check("src"))
            self.assertEqual(
                ([x["locator"] for x in files], missing), (["src/ok.py"], [])
            )
            self.assertEqual(reader.call_count, 2)
            with patch(
                "scripts.verification.input_snapshot.os.read",
                side_effect=AssertionError("private read"),
            ):
                self.assertEqual(
                    snapshot_inputs(root, _check(".local/secret")),
                    ([], [".local/secret"]),
                )

    def test_missing_freeze_and_execute_does_not_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._declared(root, "absent")
            freeze = freeze_inputs(root)
            self.assertEqual(
                (freeze["result"], freeze["reason"]), ("FAIL", "input-missing")
            )
            with patch.object(
                self, "_runner", side_effect=AssertionError("executed")
            ) as runner:
                report = verify_repository(root, runner=runner)
            runner.assert_not_called()
            self.assertNotEqual(report["result"], "PASS")

    def test_report_currentness_rejects_field_tamper_and_mode_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            item = root / "src" / "input"
            item.write_bytes(b"a")
            self._declared(root, "src/input")
            report = verify_repository(root, runner=self._runner)
            self.assertEqual(report["result"], "PASS")
            validate_report(root, report)
            for field, value in (("size_bytes", 2), ("executable", True)):
                tampered = copy.deepcopy(report)
                snapshots = tampered["checks"][0]["input_snapshot"]
                snapshots["final"]["files"][0][field] = value
                tampered["input_fingerprint"] = fingerprint_json(
                    {
                        "configuration_fingerprint": tampered[
                            "configuration_fingerprint"
                        ],
                        "snapshots": [{"check_id": "snapshot.test", **snapshots}],
                    }
                )
                with self.assertRaises(ValueError):
                    validate_report(root, tampered)
            item.chmod(0o755)
            with self.assertRaises(ValueError):
                validate_report(root, report)

    def test_regular_files_deduplicate_and_sort_with_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "b.py").write_bytes(b"b")
            (root / "src" / "a.py").write_bytes(b"a")
            os.chmod(root / "src" / "a.py", 0o755)
            files, missing = snapshot_inputs(root, _check("src", "src/a.py"))
        self.assertEqual(missing, [])
        self.assertEqual([x["locator"] for x in files], ["src/a.py", "src/b.py"])
        self.assertEqual(
            set(files[0]), {"locator", "sha256", "size_bytes", "executable"}
        )
        self.assertTrue(files[0]["executable"])

    def test_mode_changes_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = root / "tool.sh"
            item.write_text("#!/bin/sh\n")
            before = snapshot_check_inputs(root, _check("tool.sh"))["fingerprint"]
            item.chmod(0o755)
            after = snapshot_check_inputs(root, _check("tool.sh"))["fingerprint"]
        self.assertNotEqual(before, after)

    def test_rejects_unsafe_components_and_private_names_without_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "real").write_text("do not read")
            (root / "link").symlink_to("real")
            (root / ".env").write_text("private")
            (root / "broken").symlink_to("missing-target")
            files, missing = snapshot_inputs(
                root, _check("../real", "link", "broken", ".env", "a//b", "a\\b")
            )
        self.assertEqual(files, [])
        self.assertEqual(missing, ["../real", ".env", "a//b", "a\\b", "broken", "link"])

    def test_fifo_does_not_block_and_private_tree_entry_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.mkfifo(root / "pipe")
            (root / "src").mkdir()
            (root / "src" / ".npmrc").write_text("private")
            files, missing = snapshot_inputs(root, _check("pipe", "src"))
        self.assertEqual(files, [])
        self.assertEqual(missing, ["pipe", "src"])

    def test_prunes_generated_tree_and_detects_membership_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "build").mkdir(parents=True)
            (root / "src" / "main.py").write_text("source")
            (root / "src" / "build" / "output").write_text("generated")
            before = snapshot_check_inputs(root, _check("src"))
            (root / "src" / "later.py").write_text("new")
            after = snapshot_check_inputs(root, _check("src"))
        self.assertEqual([x["locator"] for x in before["files"]], ["src/main.py"])
        self.assertNotEqual(before["fingerprint"], after["fingerprint"])

    def test_read_race_replacement_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = root / "input"
            item.write_text("first")
            original = os.read
            changed = False

            def replace_after_read(fd, size):
                nonlocal changed
                result = original(fd, size)
                if result and not changed:
                    replacement = root / "replacement"
                    replacement.write_text("second")
                    os.replace(replacement, item)
                    changed = True
                return result

            with patch(
                "scripts.verification.input_snapshot.os.read",
                side_effect=replace_after_read,
            ):
                _files, missing = snapshot_inputs(root, _check("input"))
        self.assertEqual(missing, ["input"])

    def test_snapshot_shape_is_strict_and_rejects_bool_size(self):
        good = {
            "fingerprint": "a" * 64,
            "files": [
                {
                    "locator": "a",
                    "sha256": "b" * 64,
                    "size_bytes": 0,
                    "executable": False,
                }
            ],
            "missing": [],
        }
        self.assertTrue(_snapshot_shape(good))
        bad = {**good, "files": [{**good["files"][0], "size_bytes": True}]}
        self.assertFalse(_snapshot_shape(bad))
        self.assertFalse(
            _snapshot_shape({**good, "files": [{"locator": "a", "sha256": "b" * 64}]})
        )


if __name__ == "__main__":
    unittest.main()
