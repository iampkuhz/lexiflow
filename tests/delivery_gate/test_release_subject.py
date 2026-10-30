"""以合成提交和实际身份生产器验证发行主体绑定及漂移拒绝。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from scripts.acceptance.evidence.acceptance_record_store import RecordError
from scripts.acceptance.task_definition.release_subject import (
    _digest_absolute,
    _digest_relative_absolute_root,
    read_release_subject,
    verify_release_subject,
)
from ops.release.host_resources import release_host_identity
from ops.release.host_resources.release_host_identity import resolve_host_binding
from scripts.acceptance.task_operations.submit_task_for_acceptance import submit
from tests.delivery_gate.fixtures import PRODUCER_SESSION, make_mock_runtime


class TestReleaseSubject(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Test")
        (self.root / ".gitignore").write_text("tmp/\n")
        (self.root / "source.txt").write_text("fixture\n")
        self.dataset_file = Path(self.temp.name) / "dataset.zip"
        self.dataset_file.write_bytes(b"synthetic dataset bytes\n")
        self.notices_root = Path(self.temp.name) / "notices"
        self.notices_root.mkdir()
        self.notice_file = self.notices_root / "LICENSE"
        self.notice_file.write_bytes(b"synthetic notice\n")
        ledger = {
            "schema_version": "lexiflow.distribution-licenses.v1",
            "licenses": [
                {
                    "id": "fixture-license",
                    "component": "LexiFlow",
                    "licenseId": "MIT",
                    "licenseName": "Synthetic MIT",
                    "sourceUrl": "https://example.invalid/license",
                    "noticePath": "LICENSE",
                    "noticeBytes": self.notice_file.stat().st_size,
                    "noticeSha256": self._sha(self.notice_file.read_bytes()),
                }
            ],
        }
        self._write_json(self.root / "ops/release/distribution-licenses.json", ledger)
        project_root = Path(__file__).resolve().parents[2]
        identity_script = self.root / "ops/release/integrity/source-build-identity.mjs"
        identity_script.parent.mkdir(parents=True)
        shutil.copyfile(
            project_root / "ops/release/integrity/source-build-identity.mjs",
            identity_script,
        )
        (self.root / "ops/release/version.txt").write_text("1.2.2\n")
        self._git(
            "add",
            ".gitignore",
            "source.txt",
            "ops/release/integrity/source-build-identity.mjs",
            "ops/release/version.txt",
            "ops/release/distribution-licenses.json",
        )
        self._git("commit", "-qm", "fixture")
        (self.root / "ops/release/version.txt").write_text("1.2.3\n")
        (self.root / "source.txt").write_text("fixture target\n")
        self._git("add", "ops/release/version.txt", "source.txt")
        self._git("commit", "-qm", "target fixture")
        self.commit = self._git("rev-parse", "HEAD")
        self._git("tag", "v1.2.3")
        identity = self._identity("v1.2.3")
        self.candidates = (Path(self.temp.name) / "candidates").resolve()
        self.candidates.mkdir()
        self.target = self._candidate("target", identity, self.commit)
        self.request_file = (Path(self.temp.name) / "request.json").resolve()
        self.request = {
            "releaseTag": "v1.2.3",
            "dataset": {
                "path": str(self.dataset_file.resolve()),
                "sha256": self._sha(self.dataset_file.read_bytes()),
                "releaseId": "release",
                "preparationId": "preparation",
                "ruleId": "rule",
                "sqlVersion": "v1",
            },
            "noticesRoot": str(self.notices_root.resolve()),
            "outputParent": "/fixture/output",
            "endpoint": "unix:///tmp/podman.sock",
        }
        self._write_json(self.request_file, self.request)
        self.runtime_file = (
            self.root
            / "tmp/quality/release-workflow/1.2.3/candidate-runtime-request.json"
        )
        self.runtime = {"target": self.target}
        self._write_json(self.runtime_file, self.runtime)
        self.handoff = {
            "schemaVersion": 2,
            "hostBinding": resolve_host_binding(),
            "root": str(self.root.resolve()),
            "releaseTag": "v1.2.3",
            "buildIdentity": identity,
            "requestSha256": self._sha(self.request_file.read_bytes()),
            "runtimeRequestSha256": self._sha(self.runtime_file.read_bytes()),
            "requestFile": str(self.request_file),
            "target": self.target,
        }
        self.handoff_file = (
            self.root / "tmp/quality/release-workflow/1.2.3/handoff.json"
        )
        self._write_json(self.handoff_file, self.handoff)

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout.strip()

    @staticmethod
    def _sha(data):
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))

    def _candidate(self, name, build_id, source):
        path = self.candidates / name
        path.mkdir()
        marker = {
            "buildIdentity": build_id
            if isinstance(build_id, dict)
            else {"buildId": build_id, "sourceCommit": source}
        }
        self._write_json(path / "candidate.json", marker)
        return {
            "candidateDirectory": str(path.resolve()),
            "candidateSha256": self._sha((path / "candidate.json").read_bytes()),
        }

    def _identity(self, tag="v1.2.3"):
        result = subprocess.run(
            [
                "node",
                "ops/release/integrity/source-build-identity.mjs",
                "--tag",
                tag,
            ],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def _refresh_target_source(self):
        self.commit = self._git("rev-parse", "HEAD")
        self._git("tag", "-f", "v1.2.3")
        identity = self._identity()
        marker_path = self.candidates / "target/candidate.json"
        self._write_json(marker_path, {"buildIdentity": identity})
        self.target["candidateSha256"] = self._sha(marker_path.read_bytes())
        self.runtime = {"target": self.target}
        self._write_json(self.runtime_file, self.runtime)
        self.handoff.update(
            {
                "buildIdentity": identity,
                "requestSha256": self._sha(self.request_file.read_bytes()),
                "runtimeRequestSha256": self._sha(self.runtime_file.read_bytes()),
                "target": self.target,
            }
        )
        self._write_json(self.handoff_file, self.handoff)

    def test_clean_tagged_candidate_handoff_is_accepted(self):
        subject = read_release_subject(self.root)
        self.assertEqual(subject["commit_sha"], self.commit)
        self.assertEqual(
            subject["target"]["candidate_sha256"], self.target["candidateSha256"]
        )

    def test_stable_host_binding_schema_and_exact_shape_are_required(self):
        original = dict(self.handoff)
        for mutation in (
            {**original, "schemaVersion": 1},
            {**original, "host": "legacy-host"},
            {**original, "extra": True},
            {**original, "root": str(self.root.parent)},
            {
                **original,
                "hostBinding": {
                    "scheme": "lexiflow.host-binding.v1",
                    "sha256": "0" * 64,
                },
            },
            {
                **original,
                "hostBinding": {"scheme": "lexiflow.host-binding.v1", "sha256": "bad"},
            },
        ):
            self._write_json(self.handoff_file, mutation)
            with self.assertRaises(RecordError):
                read_release_subject(self.root)
        self._write_json(self.handoff_file, original)

    def test_host_binding_is_domain_separated_by_stable_source_and_uid(self):
        with (
            patch.object(
                release_host_identity,
                "_stable_system_id",
                return_value=("test-source", "opaque-input"),
            ),
            patch.object(release_host_identity.os, "getuid", return_value=501),
            patch.object(socket, "gethostname", return_value="name-one"),
        ):
            first = resolve_host_binding()
        with (
            patch.object(
                release_host_identity,
                "_stable_system_id",
                return_value=("test-source", "opaque-input"),
            ),
            patch.object(release_host_identity.os, "getuid", return_value=501),
            patch.object(socket, "gethostname", return_value="name-two"),
        ):
            self.assertEqual(first, resolve_host_binding())
        with (
            patch.object(
                release_host_identity,
                "_stable_system_id",
                return_value=("test-source", "other-input"),
            ),
            patch.object(release_host_identity.os, "getuid", return_value=501),
        ):
            self.assertNotEqual(first, resolve_host_binding())
        with (
            patch.object(
                release_host_identity,
                "_stable_system_id",
                return_value=("test-source", "opaque-input"),
            ),
            patch.object(release_host_identity.os, "getuid", return_value=502),
        ):
            self.assertNotEqual(first, resolve_host_binding())

    def test_missing_stable_source_fails_closed(self):
        with patch.object(
            release_host_identity,
            "_stable_system_id",
            side_effect=release_host_identity.HostIdentityError(
                "HOST_IDENTITY_SOURCE_UNAVAILABLE"
            ),
        ):
            with self.assertRaises(release_host_identity.HostIdentityError):
                resolve_host_binding()

    def test_system_sources_reject_malformed_zero_and_untrusted_machine_ids(self):
        good_uuid = '"IOPlatformUUID" = "12345678-1234-4234-8234-123456789abc"'
        self.assertEqual(
            release_host_identity._validated_darwin_id(good_uuid),
            "12345678-1234-4234-8234-123456789abc",
        )
        for invalid in (
            "",
            '"IOPlatformUUID" = "00000000-0000-0000-0000-000000000000"',
            '"IOPlatformUUID" = "not-a-uuid"',
        ):
            with self.assertRaises(release_host_identity.HostIdentityError):
                release_host_identity._validated_darwin_id(invalid)
        info = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0, st_size=33)
        self.assertEqual(
            release_host_identity._validated_linux_id(b"a" * 32 + b"\n", info), "a" * 32
        )
        for raw in (b"0" * 32, b"invalid", b"a" * 129):
            with self.assertRaises(release_host_identity.HostIdentityError):
                release_host_identity._validated_linux_id(raw, info)
        with tempfile.TemporaryDirectory() as directory:
            info = os.stat(directory)
            with self.assertRaises(release_host_identity.HostIdentityError):
                release_host_identity._validated_linux_id(b"a" * 32, info)

    def test_historical_version_workflow_residue_is_preserved_and_ignored(self):
        residue = self.root / "tmp/quality/release-workflow/1.2.2/old.json"
        residue.parent.mkdir(parents=True)
        residue.write_text("keep")
        subject = read_release_subject(self.root)
        self.assertEqual(
            subject["target"]["candidate_sha256"], self.target["candidateSha256"]
        )
        residue.write_text("historical change")
        self.assertEqual(read_release_subject(self.root), subject)
        self.assertEqual(residue.read_text(), "historical change")

    def test_request_runtime_or_target_marker_drift_is_rejected(self):
        self.request_file.write_text("{}")
        with self.assertRaises(RecordError):
            read_release_subject(self.root)

    def test_runtime_request_and_target_marker_hash_drift_are_rejected(self):
        self.runtime_file.write_text("{}")
        with self.assertRaises(RecordError) as runtime_error:
            read_release_subject(self.root)
        self.assertEqual(runtime_error.exception.code, "release-runtime-drift")

        self._write_json(self.runtime_file, self.runtime)
        (self.candidates / "target/candidate.json").write_text("{}")
        with self.assertRaises(RecordError) as candidate_error:
            read_release_subject(self.root)
        self.assertEqual(candidate_error.exception.code, "release-candidate-drift")
        self.assertEqual(
            candidate_error.exception.detail, "target marker digest mismatch"
        )

    def test_bound_dataset_or_notice_byte_drift_rejects_old_subject(self):
        subject = read_release_subject(self.root)
        self.dataset_file.write_bytes(b"changed dataset bytes\n")
        with self.assertRaises(RecordError):
            verify_release_subject(self.root, subject)
        self.dataset_file.write_bytes(b"synthetic dataset bytes\n")
        subject = read_release_subject(self.root)
        self.notice_file.write_bytes(b"changed notice bytes\n")
        with self.assertRaises(RecordError):
            verify_release_subject(self.root, subject)

    def test_dataset_missing_symlink_and_wrong_digest_are_rejected(self):
        subject = read_release_subject(self.root)
        moved = self.dataset_file.with_suffix(".saved")
        self.dataset_file.rename(moved)
        with self.assertRaises(RecordError):
            verify_release_subject(self.root, subject)
        self.dataset_file.symlink_to(moved)
        with self.assertRaises(RecordError):
            read_release_subject(self.root)

    def test_notice_symlink_and_wrong_size_are_rejected(self):
        self.notice_file.unlink()
        elsewhere = Path(self.temp.name) / "other-notice"
        elsewhere.write_bytes(b"synthetic notice\n")
        self.notice_file.symlink_to(elsewhere)
        with self.assertRaises(RecordError):
            read_release_subject(self.root)
        self.notice_file.unlink()
        self.notice_file.write_bytes(b"x")
        with self.assertRaises(RecordError):
            read_release_subject(self.root)

    def test_notice_root_ancestor_symlink_is_rejected(self):
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(Path(self.temp.name), target_is_directory=True)
        with self.assertRaises(RecordError):
            _digest_relative_absolute_root(str(alias / "notices"), "LICENSE")

    def test_empty_notice_locator_is_rejected(self):
        with self.assertRaises(RecordError):
            _digest_relative_absolute_root(str(self.notices_root), "")

    def test_unrelated_sibling_creation_during_stream_read_is_ignored(self):
        import scripts.acceptance.task_definition.release_subject as module

        original = module.os.read
        created = False

        def create_sibling(fd, size):
            nonlocal created
            if not created:
                (self.notices_root / "unconsumed-extra.txt").write_text("unrelated")
                created = True
            return original(fd, size)

        with patch.object(module.os, "read", side_effect=create_sibling):
            size, digest = _digest_absolute(str(self.notice_file.resolve()), limit=1024)
        self.assertTrue(created)
        self.assertEqual(size, self.notice_file.stat().st_size)
        self.assertEqual(digest, self._sha(self.notice_file.read_bytes()))

    def test_bad_notice_path_and_more_than_64_declarations_are_rejected(self):
        ledger_path = self.root / "ops/release/distribution-licenses.json"
        ledger = json.loads(ledger_path.read_text())
        ledger["licenses"][0]["noticePath"] = "../outside"
        self._write_json(ledger_path, ledger)
        self._git("add", "ops/release/distribution-licenses.json")
        self._git("commit", "-qm", "invalid fixture ledger path")
        self._refresh_target_source()
        with self.assertRaises(RecordError):
            read_release_subject(self.root)

    def test_duplicate_notice_paths_are_bound_once(self):
        ledger_path = self.root / "ops/release/distribution-licenses.json"
        ledger = json.loads(ledger_path.read_text())
        duplicate = dict(ledger["licenses"][0], id="fixture-license-2")
        ledger["licenses"].append(duplicate)
        self._write_json(ledger_path, ledger)
        self._git("add", "ops/release/distribution-licenses.json")
        self._git("commit", "-qm", "duplicate fixture notice")
        self._refresh_target_source()
        subject = read_release_subject(self.root)
        self.assertEqual(len(subject["source_bindings"]["notices"]), 1)

    def test_external_sources_use_digest_only_streaming_reads(self):
        import scripts.acceptance.task_definition.release_subject as module

        original = module._read_relative
        streamed = []

        def observe(root, locator, **kwargs):
            if kwargs.get("digest_only"):
                streamed.append(locator)
            return original(root, locator, **kwargs)

        with patch.object(module, "_read_relative", side_effect=observe):
            subject = read_release_subject(self.root)
        self.assertEqual(
            subject["source_bindings"]["dataset"]["sha256"],
            self._sha(self.dataset_file.read_bytes()),
        )
        self.assertTrue(
            any(locator.endswith("notices/LICENSE") for locator in streamed)
        )

    def test_more_than_64_ledger_entries_are_rejected(self):
        ledger_path = self.root / "ops/release/distribution-licenses.json"
        ledger = json.loads(ledger_path.read_text())
        ledger["licenses"] = [
            dict(ledger["licenses"][0], id=f"fixture-{index:02d}")
            for index in range(65)
        ]
        self._write_json(ledger_path, ledger)
        self._git("add", "ops/release/distribution-licenses.json")
        self._git("commit", "-qm", "oversized fixture ledger")
        self._refresh_target_source()
        with self.assertRaises(RecordError):
            read_release_subject(self.root)

    def test_dirty_source_and_wrong_tag_are_rejected(self):
        (self.root / "source.txt").write_text("dirty\n")
        with self.assertRaises(RecordError):
            read_release_subject(self.root)

    def test_symlink_handoff_is_rejected(self):
        self.handoff_file.unlink()
        target = self.root / "tmp/quality/release-workflow/other.json"
        target.write_text("{}")
        self.handoff_file.symlink_to(target)
        with self.assertRaises(RecordError):
            read_release_subject(self.root)

    def test_symlink_ancestor_is_rejected(self):
        workflow_dir = self.root / "tmp/quality/release-workflow"
        moved = Path(self.temp.name).resolve() / "moved-workflow"
        workflow_dir.rename(moved)
        workflow_dir.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(RecordError):
            read_release_subject(self.root)

    @patch("scripts.agents.identity.execution_identity.discover")
    def test_real_catalog_formal_task_can_submit_without_diff(self, discover):
        discover.return_value = make_mock_runtime(PRODUCER_SESSION)
        task = {
            "id": "LF-TSK-TEST-0001",
            "task_version": 1,
            "change_version": "1.0.0",
            "depends_on": [],
            "required_check_ids": ["fixture.release"],
            "allowed_files": [],
            "forbidden_files": [],
            "file_claims": [],
        }
        (self.root / "planning").mkdir()
        (self.root / "planning/workstreams.yaml").write_text(
            yaml.safe_dump(
                {
                    "workstreams": [
                        {
                            "id": "test",
                            "epics": [{"capabilities": [{"seed_tasks": [task]}]}],
                        }
                    ]
                }
            )
        )
        (self.root / "config/engineering").mkdir(parents=True)
        declarations = {
            "checks": [
                {
                    "check_id": "fixture.release",
                    "module_dependencies": [],
                    "scenarios": ["repository-baseline"],
                    "command": ["python3", "fixture_check.py"],
                    "timeout_seconds": 10,
                    "required_environment": ["python3"],
                    "input_paths": ["fixture_check.py"],
                }
            ],
            "scenario_policy": {
                "ci_quick": {
                    "always_required_check_ids": [],
                    "formal_only_check_ids": ["fixture.release"],
                }
            },
        }
        (self.root / "config/engineering/module-checks.yaml").write_text(
            yaml.safe_dump(declarations)
        )
        (self.root / "fixture_check.py").write_text("print('PASS')\n")
        self._git(
            "add",
            "planning/workstreams.yaml",
            "config/engineering/module-checks.yaml",
            "fixture_check.py",
        )
        self._git("commit", "-qm", "add synthetic formal Task")
        self.commit = self._git("rev-parse", "HEAD")
        self._git("tag", "-f", "v1.2.3")
        identity = self._identity()
        target_marker = self.candidates / "target/candidate.json"
        self._write_json(target_marker, {"buildIdentity": identity})
        self.target["candidateSha256"] = self._sha(target_marker.read_bytes())
        self._write_json(self.request_file, self.request)
        self.runtime = {"target": self.target}
        self._write_json(self.runtime_file, self.runtime)
        self.handoff.update(
            {
                "root": str(self.root.resolve()),
                "buildIdentity": identity,
                "requestSha256": self._sha(self.request_file.read_bytes()),
                "runtimeRequestSha256": self._sha(self.runtime_file.read_bytes()),
                "target": self.target,
            }
        )
        self._write_json(self.handoff_file, self.handoff)
        result = submit(self.root, task_id="LF-TSK-TEST-0001", release_handoff=True)
        self.assertEqual(result["result"], "PASS")
        from scripts.acceptance.evidence.acceptance_record_store import load_submission

        record = load_submission(self.root, result["submission_id"])
        self.assertEqual(
            record["schema_version"], "lexiflow.delivery-gate-submission.v7"
        )
        self.assertIsNone(record["change_report"])
        self.assertIsNone(record["diff"])
        self.assertIsNone(record["task_subject"])
        self.assertEqual(
            record["acceptance_plan"]["verification_scope"], "repository-baseline"
        )
        from scripts.acceptance.evidence.verify_candidate_acceptance_evidence import (
            consume_candidate_pass,
        )
        from scripts.acceptance.task_definition.task_acceptance_plan import verify_plan
        from scripts.acceptance.task_operations.evaluate_task_acceptance import (
            check_conditions,
        )
        from scripts.acceptance.task_operations.run_independent_task_validation import (
            _verify_frozen,
        )

        with (
            patch(
                "scripts.acceptance.task_operations.run_independent_task_validation.verify_authority",
                return_value=None,
            ),
            patch(
                "scripts.acceptance.task_operations.evaluate_task_acceptance.verify_authority",
                return_value=None,
            ),
        ):
            verify_plan(self.root, record)
            _verify_frozen(self.root, record)
            self.assertEqual(
                check_conditions(self.root, submission_id=result["submission_id"])[
                    "reason"
                ],
                "validation-missing",
            )
            self.assertEqual(
                consume_candidate_pass(
                    self.root,
                    submission_id=result["submission_id"],
                    candidate_directory=Path(self.target["candidateDirectory"]),
                )["reason"],
                "validation-missing",
            )
            reduced = json.loads(json.dumps(record))
            reduced["verification_freeze"]["checks"].pop()
            with self.assertRaises(RecordError):
                verify_plan(self.root, reduced)
            self.request_file.write_text("{}")
            with self.assertRaises(RecordError):
                verify_plan(self.root, record)
            self.assertEqual(
                check_conditions(self.root, submission_id=result["submission_id"])[
                    "reason"
                ],
                "release-plan-drift",
            )
            self.assertNotEqual(
                consume_candidate_pass(
                    self.root,
                    submission_id=result["submission_id"],
                    candidate_directory=Path(self.target["candidateDirectory"]),
                )["reason"],
                "validation-missing",
            )

    def test_forged_identity_and_malformed_candidate_types_fail_closed(self):
        self.handoff["buildIdentity"]["sourceSha256"] = "f" * 64
        self._write_json(self.handoff_file, self.handoff)
        with self.assertRaises(RecordError):
            read_release_subject(self.root)
        self.handoff["buildIdentity"] = self._identity()
        self.handoff["target"]["candidateSha256"] = 12
        self._write_json(self.handoff_file, self.handoff)
        with self.assertRaises(RecordError):
            read_release_subject(self.root)


if __name__ == "__main__":
    unittest.main()
