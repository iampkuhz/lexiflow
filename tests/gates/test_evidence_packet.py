"""Exhaustive adversarial tests for strict explicit evidence packet materializer.

LF-TSK-QLT-0007 final rework: Addresses all defects from Rework-1 independent review.
Acceptance case: LF-GATE-EVIDENCE-001
"""

import copy
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest import mock

import scripts.gates.evidence_packet as evidence_packet

from scripts.gates.evidence_packet import (
    CODEX_TASK_PROJECTION_SCHEMA_VERSION,
    CODEX_MAIN_TASK_PROJECTION_SCHEMA_VERSION,
    EvidencePacketError,
    SNAPSHOT_SCHEMA_VERSION,
    check_locator_safety_strict,
    dispatch_path_contains,
    dispatch_path_specificity,
    dispatch_paths_intersect,
    extract_diff_file_set,
    match_dispatch_path_v1,
    materialize,
    validate_dispatch_path_v1,
    verify_packet_strict,
    validate_codex_main_task_projection,
)


def _write_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(content, (dict, list)):
        with open(path, "w") as f:
            json.dump(content, f, indent=2, sort_keys=True)
            f.write("\n")
    elif isinstance(content, bytes):
        with open(path, "wb") as f:
            f.write(content)
    else:
        with open(path, "w") as f:
            f.write(str(content))


def _hash_bytes(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _hash_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class FixtureRepo:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="evidence-packet-test-")
        self._build()

    def _build(self):
        self.task_source_path = "planning/workstreams.yaml"
        self.task_source_content = b"# LF-TSK-QLT-0007 fixture\n"
        _write_file(os.path.join(self.root, self.task_source_path), self.task_source_content)
        self.task_source_sha = _hash_bytes(self.task_source_content)

        self.changed_files = [
            "scripts/gates/evidence_packet.py",
            "tests/gates/test_evidence_packet.py",
        ]

        # Create actual changed files so snapshot content hash verification can find them
        _write_file(os.path.join(self.root, "scripts/gates/evidence_packet.py"), b"# evidence_packet.py content\n")
        _write_file(os.path.join(self.root, "tests/gates/test_evidence_packet.py"), b"# test_evidence_packet.py content\n")

        self.snapshot = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "files": {
                "scripts/gates/evidence_packet.py": {
                    "state": "present",
                    "sha256": _hash_bytes(b"# evidence_packet.py content\n"),
                },
                "tests/gates/test_evidence_packet.py": {
                    "state": "present",
                    "sha256": _hash_bytes(b"# test_evidence_packet.py content\n"),
                },
            }
        }
        self.snapshot_path = "tmp/quality/evidence/fixture/snapshot.json"
        _write_file(os.path.join(self.root, self.snapshot_path), self.snapshot)

        self.diff_content = (
            b"diff --git a/scripts/gates/evidence_packet.py b/scripts/gates/evidence_packet.py\n"
            b"--- a/scripts/gates/evidence_packet.py\n"
            b"+++ b/scripts/gates/evidence_packet.py\n"
            b"@@ -1 +1 @@\n"
            b"-old\n"
            b"+new\n"
            b"diff --git a/tests/gates/test_evidence_packet.py b/tests/gates/test_evidence_packet.py\n"
            b"--- a/tests/gates/test_evidence_packet.py\n"
            b"+++ b/tests/gates/test_evidence_packet.py\n"
            b"@@ -1 +1 @@\n"
            b"-old\n"
            b"+new\n"
        )
        self.diff_path = "tmp/quality/evidence/fixture/reviewed.diff"
        _write_file(os.path.join(self.root, self.diff_path), self.diff_content)

        self.task_artifact = {
            "goal": "Fixture task",
            "task_id": "LF-TSK-QLT-0007",
            "task_source": "planning/workstreams.yaml",
            "task_version": 1,
            "change_version": "1.0.0",
            "allowed_files": "scripts/gates/evidence_packet.py, tests/gates/test_evidence_packet.py",
            "forbidden_files": "planning/**, openspec/**, docs/**",
            "required_context": "fixture",
            "expected_output": "fixture",
            "acceptance_criteria": ["fixture"],
            "acceptance_evidence": ["fixture"],
            "validation_command": "python3 -m unittest discover -s tests/gates -p 'test_evidence_packet.py'",
            "failure_policy": "Fail closed.",
            "parent_client": "codex",
            "agent_id": "agent_fixture",
            "session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "client": "qoder",
            "parent_session_id": "00000000-0000-4000-8000-000000000000",
            "run_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        }
        self.task_artifact_path = "tmp/harness/tasks/fixture/task.json"
        _write_file(os.path.join(self.root, self.task_artifact_path), self.task_artifact)

        self.completion_artifact = {
            "status": "finished",
            "exit_code": 0,
            "session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "task_id": "LF-TSK-QLT-0007",
            "task_version": 1,
            "change_version": "1.0.0",
            "agent_id": "agent_fixture",
            "client": "qoder",
            "parent_client": "codex",
            "parent_session_id": "00000000-0000-4000-8000-000000000000",
            "run_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        }
        self.completion_artifact_path = "tmp/harness/tasks/fixture/completion.json"
        _write_file(os.path.join(self.root, self.completion_artifact_path), self.completion_artifact)

        self.stdout_content = b"some stdout output\n"
        self.stdout_path = "tmp/harness/tasks/fixture/stdout.log"
        _write_file(os.path.join(self.root, self.stdout_path), self.stdout_content)

        self.stderr_content = b""
        self.stderr_path = "tmp/harness/tasks/fixture/stderr.log"
        _write_file(os.path.join(self.root, self.stderr_path), self.stderr_content)

        self.test_evidence = {
            "suite": "tests/gates/test_evidence_packet.py",
            "tests_run": 100,
            "failures": 0,
            "errors": 0,
            "status": "PASS",
        }
        self.test_evidence_path = "tmp/quality/evidence/fixture/tests.json"
        _write_file(os.path.join(self.root, self.test_evidence_path), self.test_evidence)

    def artifact_sha(self, name):
        path = {
            "task": self.task_artifact_path,
            "completion": self.completion_artifact_path,
            "stdout": self.stdout_path,
            "stderr": self.stderr_path,
            "changed_file_snapshot": self.snapshot_path,
            "diff": self.diff_path,
        }[name]
        return _hash_file(os.path.join(self.root, path))

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def _build_valid_input(fixture):
    return {
        "task": {
            "task_id": "LF-TSK-QLT-0007",
            "task_version": 1,
            "change_version": "1.0.0",
            "task_source": {
                "locator": fixture.task_source_path,
                "sha256": fixture.task_source_sha,
            },
        },
        "subject": {
            "raw_artifacts": {
                "task": {"locator": fixture.task_artifact_path, "sha256": fixture.artifact_sha("task")},
                "completion": {"locator": fixture.completion_artifact_path, "sha256": fixture.artifact_sha("completion")},
                "stdout": {"locator": fixture.stdout_path, "sha256": fixture.artifact_sha("stdout")},
                "stderr": {"locator": fixture.stderr_path, "sha256": fixture.artifact_sha("stderr")},
                "changed_file_snapshot": {"locator": fixture.snapshot_path, "sha256": fixture.artifact_sha("changed_file_snapshot")},
                "diff": {"locator": fixture.diff_path, "sha256": fixture.artifact_sha("diff")},
                "tests": [
                    {"locator": fixture.test_evidence_path, "sha256": _hash_file(os.path.join(fixture.root, fixture.test_evidence_path))},
                ],
            },
            "main_agent_attestation": {
                "actor_id": "agent_fixture",
                "reviewed_at": "2026-09-16T10:00:00Z",
                "result_fields": {
                    "status": "PASS",
                    "changed_files": fixture.changed_files,
                    "validation": {
                        "status": "PASS",
                        "evidence_locator": "tmp/quality/evidence/fixture/tests.json",
                    },
                    "acceptance_evidence": [
                        "tmp/quality/evidence/fixture/tests.json",
                    ],
                    "effect_checks": {
                        "determinism": "PASS",
                        "no_free_text_inference": "PASS",
                    },
                    "risks": ["bootstrap-non-READY"],
                },
                "field_source_bindings": {
                    "status": "explicit-main-agent-review",
                    "changed_files": "reviewed-snapshot-diff",
                    "validation": "test-evidence",
                    "acceptance_evidence": "test-evidence",
                    "effect_checks": "explicit-main-agent-review",
                    "risks": "explicit-main-agent-review",
                },
            },
            "identity": {
                "parent_session_id": "00000000-0000-4000-8000-000000000000",
                "agent_id": "agent_fixture",
                "run_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "client": "qoder",
                "parent_client": "codex",
            },
        },
        "scope": {
            "changed_files": fixture.changed_files,
            "raw_caller_strings": {
                "allowed_files": "scripts/gates/evidence_packet.py, tests/gates/test_evidence_packet.py",
                "forbidden_files": "planning/**, openspec/**, docs/**",
            },
            "normalized": {
                "allowed_files": ["scripts/gates/evidence_packet.py", "tests/gates/test_evidence_packet.py"],
                "forbidden_files": ["docs/**", "openspec/**", "planning/**"],
                "canonical_file_claims": ["scripts/gates/evidence_packet.py", "tests/gates/test_evidence_packet.py"],
            },
            "three_way_reconciliation": {
                "changed_outside_allowed": [],
                "changed_matching_forbidden": [],
                "changed_without_claim": [],
                "claims_outside_allowed": [],
                "claims_intersecting_forbidden": [],
                "status": "PASS",
            },
        },
    }


def _rewrite_json_artifact(fixture, inp, artifact_key, value):
    locator = inp["subject"]["raw_artifacts"][artifact_key]["locator"]
    _write_file(os.path.join(fixture.root, locator), value)
    inp["subject"]["raw_artifacts"][artifact_key]["sha256"] = _hash_file(
        os.path.join(fixture.root, locator)
    )


def _use_codex_work_package_projection(fixture, inp):
    target = fixture.task_artifact
    task_ids = [target["task_id"], "LF-TSK-QLT-0011"]
    caller = {
        "goal": "Review the Gate package with independent per-Task outcomes.",
        "work_package_id": "LF-WP-QLT-CODEX-REVIEW-PROJECTION-001",
        "task_ids": task_ids,
        "task_versions": {task_id: 1 for task_id in task_ids},
        "change_versions": {task_id: "1.0.0" for task_id in task_ids},
        "estimated_minutes": 180,
        "primary_owner": "LF-WS-QLT",
        "contract_boundary": "runner identity -> evidence packet -> planner -> review",
        "allowed_files": target["allowed_files"],
        "forbidden_files": target["forbidden_files"],
        "required_context": "current catalog and immutable Gate inputs",
        "expected_outputs_by_task": {
            target["task_id"]: target["expected_output"],
            "LF-TSK-QLT-0011": "Independent review outcome",
        },
        "acceptance_by_task": {
            target["task_id"]: {
                "acceptance_criteria": copy.deepcopy(target["acceptance_criteria"]),
                "acceptance_evidence": copy.deepcopy(target["acceptance_evidence"]),
            },
            "LF-TSK-QLT-0011": {
                "acceptance_criteria": ["Review is independent"],
                "acceptance_evidence": ["tests/gates/test_independent_review.py"],
            },
        },
        "validation_commands": {
            target["task_id"]: target["validation_command"],
            "LF-TSK-QLT-0011": "python3 -m unittest tests.gates.test_independent_review",
        },
        "failure_policy": "Fail closed on missing, stale, or ambiguous evidence.",
        "parent_client": "codex",
    }
    projection = {
        "schema_version": CODEX_TASK_PROJECTION_SCHEMA_VERSION,
        "work_package_id": caller["work_package_id"],
        "task_ids": copy.deepcopy(task_ids),
        "target_task_id": target["task_id"],
        "task_id": target["task_id"],
        "task_source": target["task_source"],
        "task_version": target["task_version"],
        "change_version": target["change_version"],
        "allowed_files": target["allowed_files"],
        "forbidden_files": target["forbidden_files"],
        "expected_output": target["expected_output"],
        "acceptance_criteria": copy.deepcopy(target["acceptance_criteria"]),
        "acceptance_evidence": copy.deepcopy(target["acceptance_evidence"]),
        "validation_command": target["validation_command"],
        "caller_contract": caller,
        "parent_session_id": target["parent_session_id"],
        "agent_id": "codex_review_agent",
        "run_id": target["run_id"],
        "session_id": target["session_id"],
        "client": "codex",
        "parent_client": "codex",
    }
    fixture.task_artifact = projection
    _rewrite_json_artifact(fixture, inp, "task", projection)
    completion = copy.deepcopy(fixture.completion_artifact)
    completion.update({
        "agent_id": projection["agent_id"],
        "client": "codex",
        "parent_client": "codex",
    })
    fixture.completion_artifact = completion
    _rewrite_json_artifact(fixture, inp, "completion", completion)
    inp["subject"]["identity"] = {
        field: projection[field]
        for field in (
            "parent_session_id", "agent_id", "run_id", "session_id",
            "client", "parent_client",
        )
    }
    return projection


def _set_changed_files(fixture, inp, paths, *, diff_content=None):
    snapshot_files = {}
    for index, path in enumerate(paths):
        content = f"changed-{index}\n".encode("utf-8")
        _write_file(os.path.join(fixture.root, path), content)
        snapshot_files[path] = {
            "state": "present",
            "sha256": _hash_bytes(content),
        }
    fixture.snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "files": snapshot_files,
    }
    _rewrite_json_artifact(fixture, inp, "changed_file_snapshot", fixture.snapshot)
    if diff_content is None:
        chunks = []
        for path in paths:
            chunks.extend(
                (
                    f"diff --git a/{path} b/{path}\n",
                    f"--- a/{path}\n",
                    f"+++ b/{path}\n",
                    "@@ -1 +1 @@\n",
                    "-old\n",
                    "+new\n",
                )
            )
        diff_content = "".join(chunks).encode("utf-8")
    diff_locator = inp["subject"]["raw_artifacts"]["diff"]["locator"]
    _write_file(os.path.join(fixture.root, diff_locator), diff_content)
    inp["subject"]["raw_artifacts"]["diff"]["sha256"] = _hash_file(
        os.path.join(fixture.root, diff_locator)
    )
    inp["subject"]["main_agent_attestation"]["result_fields"]["changed_files"] = list(paths)
    inp["scope"]["changed_files"] = list(paths)


def _relocate_quality_artifacts(fixture, inp):
    """Move every raw input out of tmp so the publication tree is fresh."""
    basenames = {
        "task": "task.json",
        "completion": "completion.json",
        "stdout": "stdout.log",
        "stderr": "stderr.log",
        "changed_file_snapshot": "snapshot.json",
        "diff": "reviewed.diff",
    }
    for key, basename in basenames.items():
        descriptor = inp["subject"]["raw_artifacts"][key]
        old_path = os.path.join(fixture.root, descriptor["locator"])
        new_locator = f"fixture-input/{basename}"
        new_path = os.path.join(fixture.root, new_locator)
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        os.replace(old_path, new_path)
        descriptor["locator"] = new_locator
        descriptor["sha256"] = _hash_file(new_path)
    test_descriptor = inp["subject"]["raw_artifacts"]["tests"][0]
    old_test_path = os.path.join(fixture.root, test_descriptor["locator"])
    new_test_locator = "fixture-input/tests.json"
    new_test_path = os.path.join(fixture.root, new_test_locator)
    os.replace(old_test_path, new_test_path)
    test_descriptor["locator"] = new_test_locator
    test_descriptor["sha256"] = _hash_file(new_test_path)
    result_fields = inp["subject"]["main_agent_attestation"]["result_fields"]
    result_fields["validation"]["evidence_locator"] = new_test_locator
    result_fields["acceptance_evidence"] = [new_test_locator]
    shutil.rmtree(os.path.join(fixture.root, "tmp"))


def _published_packet_paths(fixture):
    output_dir = os.path.join(fixture.root, "tmp", "quality", "evidence")
    if not os.path.isdir(output_dir):
        return []
    return sorted(
        os.path.join(output_dir, name)
        for name in os.listdir(output_dir)
        if name.endswith(".json") and name != "fixture"
    )


class TestSchemaVersion(unittest.TestCase):
    def test_schema_version_is_v1(self):
        from scripts.gates.evidence_packet import SCHEMA_VERSION
        self.assertEqual(SCHEMA_VERSION, "lexiflow.explicit-evidence-packet.v1")

    def test_main_only_projection_is_mutually_exclusive_with_work_package_shape(self):
        projection = {
            "schema_version": CODEX_MAIN_TASK_PROJECTION_SCHEMA_VERSION,
            "task_id": "LF-TSK-QLT-0007", "task_source": "planning/workstreams.yaml",
            "task_version": 3, "change_version": "1.2.0",
            "allowed_files": "scripts/gates/evidence_packet.py",
            "forbidden_files": "openspec/**", "expected_output": "evidence",
            "acceptance_criteria": ["criterion"], "acceptance_evidence": ["test"],
            "validation_command": "python3 -m unittest fixture", "goal": "Main task",
            "required_context": "current catalog", "failure_policy": "fail closed",
            "parent_session_id": "00000000-0000-4000-8000-000000000001",
            "agent_id": "/root", "run_id": "00000000-0000-4000-8000-000000000002",
            "session_id": "00000000-0000-4000-8000-000000000003", "client": "codex",
            "parent_client": "codex",
        }
        validate_codex_main_task_projection(projection)
        forged = dict(projection); forged["task_ids"] = [projection["task_id"]]
        with self.assertRaises(EvidencePacketError):
            validate_codex_main_task_projection(forged)
        for actor in ("/root/child", "root_main", "codex_fixture"):
            with self.subTest(actor=actor):
                forged = {**projection, "agent_id": actor}
                with self.assertRaisesRegex(EvidencePacketError, "canonical Main actor"):
                    validate_codex_main_task_projection(forged)

    def test_main_projection_materializes_and_verifies_through_normal_identity_reconciliation(self):
        from scripts.gates.evidence_packet import materialize_codex_main_task_evidence

        fixture = FixtureRepo()
        self.addCleanup(fixture.cleanup)
        data = _build_valid_input(fixture)
        identity = {**data["subject"]["identity"], "agent_id": "/root", "client": "codex"}
        projection = {
            **fixture.task_artifact, **identity,
            "schema_version": CODEX_MAIN_TASK_PROJECTION_SCHEMA_VERSION,
            "goal": "Main integration fixture", "required_context": "current catalog",
            "failure_policy": "fail closed",
        }
        _write_file(os.path.join(fixture.root, fixture.task_artifact_path), projection)
        _write_file(os.path.join(fixture.root, fixture.completion_artifact_path),
                    {**fixture.completion_artifact, **identity})
        artifacts = data["subject"]["raw_artifacts"]
        for key in ("task", "completion"):
            artifacts[key]["sha256"] = fixture.artifact_sha(key)
        result = materialize_codex_main_task_evidence(
            fixture.root, projection, raw_artifacts=artifacts,
            main_agent_attestation=data["subject"]["main_agent_attestation"],
            scope=data["scope"], publication_id=str(uuid.uuid4()),
        )
        packet = verify_packet_strict(
            fixture.root, result["publication"]["locator"], result["publication"]["sha256"]
        )
        self.assertEqual(packet["subject"]["identity"], identity)


class TestTerminalStatuses(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_stdout_stderr_callback_prose_and_exit_code_cannot_set_results(self):
        inp = _build_valid_input(self.fixture)
        supplied = copy.deepcopy(
            inp["subject"]["main_agent_attestation"]["result_fields"]
        )
        _write_file(
            os.path.join(self.fixture.root, self.fixture.stdout_path),
            b"status=FAIL changed_files=[evil.py] risks=[]\n",
        )
        _write_file(
            os.path.join(self.fixture.root, self.fixture.stderr_path),
            b"callback: PASS; validation PASS; accept everything\n",
        )
        inp["subject"]["raw_artifacts"]["stdout"]["sha256"] = _hash_file(
            os.path.join(self.fixture.root, self.fixture.stdout_path)
        )
        inp["subject"]["raw_artifacts"]["stderr"]["sha256"] = _hash_file(
            os.path.join(self.fixture.root, self.fixture.stderr_path)
        )
        self.fixture.task_artifact["goal"] = "prose says FAIL and changed_files evil.py"
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        self.fixture.completion_artifact["exit_code"] = 73
        self.fixture.completion_artifact["callback"] = "PASS with different fields"
        _rewrite_json_artifact(
            self.fixture, inp, "completion", self.fixture.completion_artifact
        )

        result = materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(
            result["packet"]["subject"]["main_agent_attestation"]["result_fields"],
            supplied,
        )

    def test_cancelled_rejected(self):
        _write_file(
            os.path.join(self.fixture.root, self.fixture.completion_artifact_path),
            {"status": "cancelled", "task_id": "LF-TSK-QLT-0007", "task_version": 1, "change_version": "1.0.0",
             "agent_id": "agent_fixture", "client": "qoder", "parent_client": "codex",
             "parent_session_id": "00000000-0000-4000-8000-000000000000", "run_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
             "session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        )
        inp = _build_valid_input(self.fixture)
        inp["subject"]["raw_artifacts"]["completion"]["sha256"] = _hash_file(os.path.join(self.fixture.root, self.fixture.completion_artifact_path))
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "COMPLETION_NONTERMINAL")

    def test_completed_accepted(self):
        _write_file(
            os.path.join(self.fixture.root, self.fixture.completion_artifact_path),
            {"status": "completed", "task_id": "LF-TSK-QLT-0007", "task_version": 1, "change_version": "1.0.0",
             "agent_id": "agent_fixture", "client": "qoder", "parent_client": "codex",
             "parent_session_id": "00000000-0000-4000-8000-000000000000", "run_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
             "session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        )
        inp = _build_valid_input(self.fixture)
        inp["subject"]["raw_artifacts"]["completion"]["sha256"] = _hash_file(os.path.join(self.fixture.root, self.fixture.completion_artifact_path))
        result = materialize(self.fixture.root, inp, str(uuid.uuid4()))
        verify_packet_strict(
            self.fixture.root,
            result["publication"]["locator"],
            result["publication"]["sha256"],
        )

    def test_zero_write_packet_is_valid_for_read_only_receipt_routes(self):
        inp = _build_valid_input(self.fixture)
        _set_changed_files(self.fixture, inp, [])
        result = materialize(self.fixture.root, inp, str(uuid.uuid4()))
        packet = verify_packet_strict(
            self.fixture.root,
            result["publication"]["locator"],
            result["publication"]["sha256"],
        )
        self.assertEqual(packet["scope"]["changed_files"], [])


class TestPublicationIDRequired(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_auto_generation_rejected(self):
        inp = _build_valid_input(self.fixture)
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp)
        self.assertEqual(ctx.exception.code, "PUBLISH_ID_REQUIRED")

    def test_publication_id_remains_canonical_lowercase_uuid4(self):
        inp = _build_valid_input(self.fixture)
        invalid = (
            "018f47a6-3a2b-7c4d-8e5f-123456789abc",
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".upper(),
            "00000000-0000-0000-0000-000000000000",
            True,
        )
        for publication_id in invalid:
            with self.subTest(publication_id=publication_id):
                with self.assertRaises(EvidencePacketError) as ctx:
                    materialize(self.fixture.root, inp, publication_id)
                self.assertEqual(ctx.exception.code, "PUBLISH_ID_INVALID")


class TestUnknownFields(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_task_unknown_field(self):
        inp = _build_valid_input(self.fixture)
        inp["task"]["extra_field"] = "value"
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "UNKNOWN_FIELD")

    def test_subject_unknown_field(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["extra_field"] = "value"
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "UNKNOWN_FIELD")

    def test_result_fields_unknown(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["main_agent_attestation"]["result_fields"]["extra"] = "value"
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "UNKNOWN_FIELD")


class TestIssuerAuthorityRejection(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_issuer_field_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["issuer"] = {"fake": "data"}
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "ISSUER_FIELD_PRESENT")

    def test_authority_field_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["authority"] = {"fake": "data"}
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "ISSUER_FIELD_PRESENT")


class TestActorIDValidation(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_empty_actor_id_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["main_agent_attestation"]["actor_id"] = ""
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "ACTOR_ID_INVALID")

    def test_unsafe_actor_id_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["main_agent_attestation"]["actor_id"] = "../evil"
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "ACTOR_ID_INVALID")


class TestReviewedAtValidation(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_invalid_timestamp_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["main_agent_attestation"]["reviewed_at"] = "not-a-timestamp"
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "REVIEWED_AT_INVALID")

    def test_calendar_invalid_timestamp_rejected(self):
        for reviewed_at in ("2026-99-99T99:99:99Z", "0000-00-00T00:00:00Z"):
            with self.subTest(reviewed_at=reviewed_at):
                inp = _build_valid_input(self.fixture)
                inp["subject"]["main_agent_attestation"]["reviewed_at"] = reviewed_at
                with self.assertRaises(EvidencePacketError) as ctx:
                    materialize(self.fixture.root, inp, str(uuid.uuid4()))
                self.assertEqual(ctx.exception.code, "REVIEWED_AT_INVALID")


class TestChangedFilesSortedUnique(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_unsorted_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["main_agent_attestation"]["result_fields"]["changed_files"] = [
            "tests/gates/test_evidence_packet.py",
            "scripts/gates/evidence_packet.py",
        ]
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "CHANGED_FILES_NOT_SORTED")

    def test_duplicates_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["main_agent_attestation"]["result_fields"]["changed_files"] = [
            "scripts/gates/evidence_packet.py",
            "scripts/gates/evidence_packet.py",
        ]
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "CHANGED_FILES_NOT_UNIQUE")


class TestValidationEvidenceLocator(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_evidence_locator_not_in_tests_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["main_agent_attestation"]["result_fields"]["validation"]["evidence_locator"] = "nonexistent.json"
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "VALIDATION_EVIDENCE_NOT_IN_TESTS")


class TestAcceptanceEvidenceVerified(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_unverified_evidence_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["main_agent_attestation"]["result_fields"]["acceptance_evidence"] = ["nonexistent.json"]
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "ACCEPTANCE_EVIDENCE_NOT_VERIFIED")


class TestEffectChecksSafeKeys(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_unsafe_key_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["main_agent_attestation"]["result_fields"]["effect_checks"]["../evil"] = "PASS"
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "EFFECT_CHECKS_KEY_UNSAFE")


class TestRisksNonblank(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_blank_risk_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["main_agent_attestation"]["result_fields"]["risks"] = [""]
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "RISK_BLANK")


class TestFieldSourceBindingsExact(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_changed_files_binding_must_be_snapshot_diff(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["main_agent_attestation"]["field_source_bindings"]["changed_files"] = "reviewed-diff"
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "BINDING_INVALID")

    def test_extra_binding_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["subject"]["main_agent_attestation"]["field_source_bindings"]["extra"] = "value"
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "BINDING_UNKNOWN_FIELD")


class TestSHA256LowercaseHex(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_uppercase_sha256_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["task"]["task_source"]["sha256"] = self.fixture.task_source_sha.upper()
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "SHA256_NOT_LOWERCASE_HEX")

    def test_literal_star_artifact_locator_rejected(self):
        inp = _build_valid_input(self.fixture)
        locator = "planning/workstreams*.yaml"
        _write_file(os.path.join(self.fixture.root, locator), self.fixture.task_source_content)
        inp["task"]["task_source"] = {
            "locator": locator,
            "sha256": _hash_file(os.path.join(self.fixture.root, locator)),
        }
        self.fixture.task_artifact["task_source"] = locator
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        with self.assertRaises(EvidencePacketError):
            materialize(self.fixture.root, inp, str(uuid.uuid4()))


class TestSymlinkAncestors(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_repo_root_symlink_ancestor_rejected(self):
        symlink_root = os.path.join(self.fixture.root, "symlink_root")
        os.symlink(self.fixture.root, symlink_root)
        inp = _build_valid_input(self.fixture)
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(symlink_root, inp, str(uuid.uuid4()))
        self.assertIn(ctx.exception.code, ("LOCATOR_SYMLINK_ANCESTOR", "REPO_SYMLINK"))

    def test_repo_root_with_symlink_ancestor_rejected(self):
        real_parent = tempfile.mkdtemp(prefix="evidence-packet-real-parent-")
        alias_parent = tempfile.mkdtemp(prefix="evidence-packet-alias-parent-")
        moved_root = os.path.join(real_parent, "repo")
        os.rename(self.fixture.root, moved_root)
        self.fixture.root = moved_root
        os.symlink(real_parent, os.path.join(alias_parent, "parent-link"))
        aliased_root = os.path.join(alias_parent, "parent-link", "repo")
        try:
            with self.assertRaises(EvidencePacketError) as ctx:
                materialize(
                    aliased_root, _build_valid_input(self.fixture), str(uuid.uuid4())
                )
            self.assertEqual(ctx.exception.code, "LOCATOR_SYMLINK_ANCESTOR")
        finally:
            shutil.rmtree(alias_parent, ignore_errors=True)
            self.fixture.cleanup()
            try:
                os.rmdir(real_parent)
            except OSError:
                pass

    def test_artifact_ancestor_swap_cannot_read_outside_repository(self):
        inp = _build_valid_input(self.fixture)
        planning = os.path.join(self.fixture.root, "planning")
        held = os.path.join(self.fixture.root, "planning-held")
        outside = tempfile.mkdtemp(prefix="evidence-packet-outside-")
        _write_file(
            os.path.join(outside, "workstreams.yaml"),
            self.fixture.task_source_content,
        )
        real_open = os.open
        swapped = False

        def swap_before_component_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            if not swapped and path == "planning" and dir_fd is not None:
                swapped = True
                os.rename(planning, held)
                os.symlink(outside, planning)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        try:
            with mock.patch.object(
                evidence_packet.os, "open", side_effect=swap_before_component_open
            ):
                with self.assertRaises(EvidencePacketError):
                    materialize(self.fixture.root, inp, str(uuid.uuid4()))
            self.assertTrue(swapped)
        finally:
            if os.path.islink(planning):
                os.unlink(planning)
            if os.path.isdir(held):
                os.rename(held, planning)
            shutil.rmtree(outside, ignore_errors=True)

    def test_fifo_locator_is_rejected_without_blocking(self):
        fifo_path = os.path.join(self.fixture.root, "artifact.pipe")
        os.mkfifo(fifo_path)
        probe = (
            "import sys\n"
            "from scripts.gates.evidence_packet import EvidencePacketError, _read_file_bytes_strict\n"
            "try:\n"
            "    _read_file_bytes_strict(sys.argv[1], 'artifact.pipe')\n"
            "except EvidencePacketError:\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(2)\n"
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-c", probe, self.fixture.root],
                cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                timeout=1,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail("strict artifact read blocked on a FIFO")
        self.assertEqual(completed.returncode, 0)


class TestSnapshotContentHash(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_snapshot_content_mismatch_rejected(self):
        _write_file(os.path.join(self.fixture.root, "scripts/gates/evidence_packet.py"), b"# modified content\n")
        inp = _build_valid_input(self.fixture)
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "SNAPSHOT_CONTENT_MISMATCH")

    def test_snapshot_requires_exact_versioned_schema(self):
        for mutate in (
            lambda snapshot: snapshot.pop("schema_version"),
            lambda snapshot: snapshot.__setitem__("schema_version", "latest"),
            lambda snapshot: snapshot.__setitem__("extra", True),
        ):
            with self.subTest(mutate=mutate):
                inp = _build_valid_input(self.fixture)
                snapshot = copy.deepcopy(self.fixture.snapshot)
                mutate(snapshot)
                _rewrite_json_artifact(
                    self.fixture, inp, "changed_file_snapshot", snapshot
                )
                with self.assertRaises(EvidencePacketError):
                    materialize(self.fixture.root, inp, str(uuid.uuid4()))

    def test_deleted_path_snapshot_can_materialize(self):
        inp = _build_valid_input(self.fixture)
        path = "x/deleted.py"
        diff = (
            b"diff --git a/x/deleted.py b/x/deleted.py\n"
            b"deleted file mode 100644\n"
            b"--- a/x/deleted.py\n"
            b"+++ /dev/null\n"
        )
        _set_changed_files(self.fixture, inp, [path], diff_content=diff)
        os.unlink(os.path.join(self.fixture.root, path))
        self.fixture.snapshot["files"][path] = {"state": "absent"}
        _rewrite_json_artifact(
            self.fixture, inp, "changed_file_snapshot", self.fixture.snapshot
        )
        inp["scope"]["raw_caller_strings"] = {
            "allowed_files": "x/**", "forbidden_files": "z/**",
        }
        inp["scope"]["normalized"] = {
            "allowed_files": ["x/**"], "forbidden_files": ["z/**"],
            "canonical_file_claims": [path],
        }
        self.fixture.task_artifact["allowed_files"] = "x/**"
        self.fixture.task_artifact["forbidden_files"] = "z/**"
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        result = materialize(self.fixture.root, inp, str(uuid.uuid4()))
        verify_packet_strict(
            self.fixture.root,
            result["publication"]["locator"],
            result["publication"]["sha256"],
        )

    def test_deleted_path_with_missing_ancestor_can_materialize(self):
        inp = _build_valid_input(self.fixture)
        path = "gone/deep/deleted.py"
        diff = (
            b"diff --git a/gone/deep/deleted.py b/gone/deep/deleted.py\n"
            b"deleted file mode 100644\n"
            b"--- a/gone/deep/deleted.py\n+++ /dev/null\n"
        )
        _set_changed_files(self.fixture, inp, [path], diff_content=diff)
        shutil.rmtree(os.path.join(self.fixture.root, "gone"))
        self.fixture.snapshot["files"][path] = {"state": "absent"}
        _rewrite_json_artifact(
            self.fixture, inp, "changed_file_snapshot", self.fixture.snapshot
        )
        inp["scope"]["raw_caller_strings"] = {
            "allowed_files": "gone/**", "forbidden_files": "z/**",
        }
        inp["scope"]["normalized"] = {
            "allowed_files": ["gone/**"], "forbidden_files": ["z/**"],
            "canonical_file_claims": [path],
        }
        self.fixture.task_artifact["allowed_files"] = "gone/**"
        self.fixture.task_artifact["forbidden_files"] = "z/**"
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        materialize(self.fixture.root, inp, str(uuid.uuid4()))

    def test_renamed_paths_snapshot_can_materialize(self):
        inp = _build_valid_input(self.fixture)
        old_path = "x/old.py"
        new_path = "x/new.py"
        diff = (
            b"diff --git a/x/old.py b/x/new.py\n"
            b"similarity index 100%\n"
            b"rename from x/old.py\n"
            b"rename to x/new.py\n"
        )
        _set_changed_files(
            self.fixture, inp, sorted([old_path, new_path]), diff_content=diff
        )
        os.unlink(os.path.join(self.fixture.root, old_path))
        self.fixture.snapshot["files"][old_path] = {"state": "absent"}
        _rewrite_json_artifact(
            self.fixture, inp, "changed_file_snapshot", self.fixture.snapshot
        )
        inp["scope"]["raw_caller_strings"] = {
            "allowed_files": "x/**", "forbidden_files": "z/**",
        }
        inp["scope"]["normalized"] = {
            "allowed_files": ["x/**"], "forbidden_files": ["z/**"],
            "canonical_file_claims": ["x/**"],
        }
        self.fixture.task_artifact["allowed_files"] = "x/**"
        self.fixture.task_artifact["forbidden_files"] = "z/**"
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        result = materialize(self.fixture.root, inp, str(uuid.uuid4()))
        verify_packet_strict(
            self.fixture.root,
            result["publication"]["locator"],
            result["publication"]["sha256"],
        )

    def test_rename_source_with_missing_ancestor_can_materialize(self):
        inp = _build_valid_input(self.fixture)
        old_path = "gone/deep/old.py"
        new_path = "present/new.py"
        diff = (
            b"diff --git a/gone/deep/old.py b/present/new.py\n"
            b"similarity index 100%\n"
            b"rename from gone/deep/old.py\n"
            b"rename to present/new.py\n"
        )
        _set_changed_files(
            self.fixture, inp, sorted([old_path, new_path]), diff_content=diff
        )
        shutil.rmtree(os.path.join(self.fixture.root, "gone"))
        self.fixture.snapshot["files"][old_path] = {"state": "absent"}
        _rewrite_json_artifact(
            self.fixture, inp, "changed_file_snapshot", self.fixture.snapshot
        )
        inp["scope"]["raw_caller_strings"] = {
            "allowed_files": "gone/**, present/**", "forbidden_files": "z/**",
        }
        inp["scope"]["normalized"] = {
            "allowed_files": ["gone/**", "present/**"],
            "forbidden_files": ["z/**"],
            "canonical_file_claims": ["gone/**", "present/**"],
        }
        self.fixture.task_artifact["allowed_files"] = "gone/**, present/**"
        self.fixture.task_artifact["forbidden_files"] = "z/**"
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        materialize(self.fixture.root, inp, str(uuid.uuid4()))

    def test_absent_path_with_symlink_ancestor_is_rejected(self):
        inp = _build_valid_input(self.fixture)
        path = "gone/deleted.py"
        diff = (
            b"diff --git a/gone/deleted.py b/gone/deleted.py\n"
            b"deleted file mode 100644\n--- a/gone/deleted.py\n+++ /dev/null\n"
        )
        _set_changed_files(self.fixture, inp, [path], diff_content=diff)
        os.unlink(os.path.join(self.fixture.root, path))
        os.rmdir(os.path.join(self.fixture.root, "gone"))
        os.symlink("missing-target", os.path.join(self.fixture.root, "gone"))
        self.fixture.snapshot["files"][path] = {"state": "absent"}
        _rewrite_json_artifact(
            self.fixture, inp, "changed_file_snapshot", self.fixture.snapshot
        )
        with self.assertRaises(EvidencePacketError):
            materialize(self.fixture.root, inp, str(uuid.uuid4()))

    def test_absent_snapshot_entry_cannot_carry_an_unverified_hash(self):
        inp = _build_valid_input(self.fixture)
        path = "x/deleted.py"
        diff = (
            b"diff --git a/x/deleted.py b/x/deleted.py\n"
            b"deleted file mode 100644\n"
            b"--- a/x/deleted.py\n"
            b"+++ /dev/null\n"
        )
        _set_changed_files(self.fixture, inp, [path], diff_content=diff)
        os.unlink(os.path.join(self.fixture.root, path))
        self.fixture.snapshot["files"][path] = {
            "state": "absent", "sha256": "0" * 64,
        }
        _rewrite_json_artifact(
            self.fixture, inp, "changed_file_snapshot", self.fixture.snapshot
        )
        with self.assertRaises(EvidencePacketError):
            materialize(self.fixture.root, inp, str(uuid.uuid4()))


class TestNormalizedExact(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_normalized_mismatch_rejected(self):
        inp = _build_valid_input(self.fixture)
        inp["scope"]["normalized"]["allowed_files"] = ["wrong/**"]
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "NORMALIZED_MISMATCH")


class TestPathParserStrict(unittest.TestCase):
    def test_empty_child_rejected(self):
        with self.assertRaises(EvidencePacketError):
            validate_dispatch_path_v1("scripts/*")
            match_dispatch_path_v1("scripts/", "scripts/*")

    def test_triple_star_rejected(self):
        with self.assertRaises(EvidencePacketError):
            validate_dispatch_path_v1("scripts/***")

    def test_brace_rejected(self):
        with self.assertRaises(EvidencePacketError):
            validate_dispatch_path_v1("scripts/{a,b}")


class TestContentFingerprint(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_fingerprint_excludes_publication_id(self):
        inp = _build_valid_input(self.fixture)
        pub_id_1 = str(uuid.uuid4())
        pub_id_2 = str(uuid.uuid4())
        result_1 = materialize(self.fixture.root, copy.deepcopy(inp), pub_id_1)
        result_2 = materialize(self.fixture.root, copy.deepcopy(inp), pub_id_2)
        self.assertEqual(result_1["packet"]["content_fingerprint"], result_2["packet"]["content_fingerprint"])


class TestVerifierStrict(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_verifier_rejects_missing_task(self):
        inp = _build_valid_input(self.fixture)
        result = materialize(self.fixture.root, inp, str(uuid.uuid4()))
        packet_path = os.path.join(self.fixture.root, result["publication"]["locator"])
        with open(packet_path, "r") as f:
            packet = json.load(f)
        del packet["task"]
        with open(packet_path, "w") as f:
            json.dump(packet, f)
        new_sha = _hash_file(packet_path)
        with self.assertRaises(EvidencePacketError):
            verify_packet_strict(self.fixture.root, result["publication"]["locator"], new_sha)

    def test_verifier_rejects_packet_symlink(self):
        inp = _build_valid_input(self.fixture)
        result = materialize(self.fixture.root, inp, str(uuid.uuid4()))
        packet_path = os.path.join(self.fixture.root, result["publication"]["locator"])
        symlink_path = packet_path + ".link"
        os.symlink(packet_path, symlink_path)
        with self.assertRaises(EvidencePacketError):
            verify_packet_strict(self.fixture.root, os.path.relpath(symlink_path, self.fixture.root), result["publication"]["sha256"])


class TestStrictJsonLoading(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_raw_task_duplicate_task_id_is_rejected(self):
        inp = _build_valid_input(self.fixture)
        descriptor = inp["subject"]["raw_artifacts"]["task"]
        path = os.path.join(self.fixture.root, descriptor["locator"])
        valid = evidence_packet.canonical_json(self.fixture.task_artifact)
        duplicate = b'{"task_id":"EVIL_FIRST",' + valid[1:]
        _write_file(path, duplicate)
        descriptor["sha256"] = _hash_bytes(duplicate)
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "JSON_DUPLICATE_KEY")

    def test_nested_snapshot_duplicate_key_is_rejected(self):
        inp = _build_valid_input(self.fixture)
        descriptor = inp["subject"]["raw_artifacts"]["changed_file_snapshot"]
        path = os.path.join(self.fixture.root, descriptor["locator"])
        valid = evidence_packet.canonical_json(self.fixture.snapshot)
        duplicate = valid.replace(
            b'"state":"present"',
            b'"state":"absent","state":"present"',
            1,
        )
        self.assertNotEqual(duplicate, valid)
        _write_file(path, duplicate)
        descriptor["sha256"] = _hash_bytes(duplicate)
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "JSON_DUPLICATE_KEY")

    def test_verifier_rejects_duplicate_key_at_any_depth(self):
        result = materialize(
            self.fixture.root, _build_valid_input(self.fixture), str(uuid.uuid4())
        )
        packet_path = os.path.join(self.fixture.root, result["publication"]["locator"])
        with open(packet_path, "rb") as stream:
            valid = stream.read()
        duplicate = valid.replace(
            b'"task":{"change_version":',
            b'"task":{"task_id":"EVIL_FIRST","change_version":',
            1,
        )
        self.assertNotEqual(duplicate, valid)
        _write_file(packet_path, duplicate)
        with self.assertRaises(EvidencePacketError) as ctx:
            verify_packet_strict(
                self.fixture.root,
                result["publication"]["locator"],
                _hash_bytes(duplicate),
            )
        self.assertEqual(ctx.exception.code, "JSON_DUPLICATE_KEY")

    def test_cli_materialize_rejects_duplicate_input_key_with_typed_error(self):
        inp = _build_valid_input(self.fixture)
        valid = evidence_packet.canonical_json(inp)
        duplicate = b'{"task":null,' + valid[1:]
        input_path = os.path.join(self.fixture.root, "input.json")
        _write_file(input_path, duplicate)
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "scripts", "gates", "evidence_packet.py",
        )
        completed = subprocess.run(
            [
                sys.executable, script, "materialize",
                "--repo-root", self.fixture.root,
                "--input", input_path,
                "--publication-id", str(uuid.uuid4()),
            ],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        error = json.loads(completed.stderr)
        self.assertEqual(error["code"], "JSON_DUPLICATE_KEY")


class TestNineFieldIdentityStrict(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_parsed_raw_task_bytes_must_match_their_declared_hash(self):
        inp = _build_valid_input(self.fixture)
        task_locator = inp["subject"]["raw_artifacts"]["task"]["locator"]
        invalid_bytes = b"{}\n"
        with open(os.path.join(self.fixture.root, task_locator), "rb") as stream:
            valid_bytes = stream.read()
        inp["subject"]["raw_artifacts"]["task"]["sha256"] = _hash_bytes(
            invalid_bytes
        )
        real_read = evidence_packet._read_file_bytes_strict
        task_reads = 0

        def alternate_hash_and_parse_views(repo_root, locator, root_identity=None):
            nonlocal task_reads
            if locator != task_locator:
                return real_read(repo_root, locator, root_identity)
            task_reads += 1
            return invalid_bytes if task_reads % 3 != 2 else valid_bytes

        with mock.patch.object(
            evidence_packet,
            "_read_file_bytes_strict",
            side_effect=alternate_hash_and_parse_views,
        ):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, str(uuid.uuid4()))

    def _write_task_and_completion(self, inp):
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        _rewrite_json_artifact(self.fixture, inp, "completion", self.fixture.completion_artifact)

    def _reset_fixture(self):
        self.fixture.cleanup()
        self.fixture = FixtureRepo()

    def test_supplied_nine_fields_reject_null_and_wrong_types_even_when_raw_matches(self):
        task_cases = {
            "task_id": (None, "bad/task"),
            "task_version": (None, True, 0, "1"),
            "change_version": (None, 1, "01.0.0", "1.0"),
        }
        identity_cases = {
            "parent_session_id": (
                None,
                "not-a-uuid",
                "00000000-0000-0000-0000-000000000000",
                "11111111-1111-0111-8111-111111111111",
            ),
            "run_id": (None, "not-a-uuid", "BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB"),
            "session_id": (None, "not-a-uuid", True),
            "agent_id": (None, "bad/id"),
            "client": (None, "bad/client"),
            "parent_client": (None, "bad/client"),
        }
        for field, invalid_values in task_cases.items():
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    inp = _build_valid_input(self.fixture)
                    inp["task"][field] = value
                    self.fixture.task_artifact[field] = value
                    self.fixture.completion_artifact[field] = value
                    self._write_task_and_completion(inp)
                    with self.assertRaises(EvidencePacketError):
                        materialize(self.fixture.root, inp, str(uuid.uuid4()))
                    self._reset_fixture()
        for field, invalid_values in identity_cases.items():
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    inp = _build_valid_input(self.fixture)
                    inp["subject"]["identity"][field] = value
                    self.fixture.task_artifact[field] = value
                    self.fixture.completion_artifact[field] = value
                    self._write_task_and_completion(inp)
                    with self.assertRaises(EvidencePacketError):
                        materialize(self.fixture.root, inp, str(uuid.uuid4()))
                    self._reset_fixture()

    def test_runner_identity_accepts_canonical_uuid7(self):
        inp = _build_valid_input(self.fixture)
        values = {
            "parent_session_id": "018f47a6-3a2b-7c4d-8e5f-123456789abc",
            "run_id": "018f47a6-3a2b-7c4d-9e5f-223456789abc",
            "session_id": "018f47a6-3a2b-7c4d-ae5f-323456789abc",
        }
        for field, value in values.items():
            self.assertEqual(uuid.UUID(value).version, 7)
            inp["subject"]["identity"][field] = value
            self.fixture.task_artifact[field] = value
            self.fixture.completion_artifact[field] = value
        self._write_task_and_completion(inp)
        result = materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(result["packet"]["subject"]["identity"], {
            **inp["subject"]["identity"],
        })

    def test_raw_task_requires_all_nine_fields(self):
        for field in (
            "task_id", "task_version", "change_version", "parent_session_id",
            "agent_id", "run_id", "session_id", "client", "parent_client",
        ):
            with self.subTest(field=field):
                inp = _build_valid_input(self.fixture)
                del self.fixture.task_artifact[field]
                _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
                with self.assertRaises(EvidencePacketError):
                    materialize(self.fixture.root, inp, str(uuid.uuid4()))
                self._reset_fixture()

    def test_raw_completion_requires_all_nine_fields(self):
        for field in (
            "task_id", "task_version", "change_version", "parent_session_id",
            "agent_id", "run_id", "session_id", "client", "parent_client",
        ):
            with self.subTest(field=field):
                inp = _build_valid_input(self.fixture)
                del self.fixture.completion_artifact[field]
                _rewrite_json_artifact(self.fixture, inp, "completion", self.fixture.completion_artifact)
                with self.assertRaises(EvidencePacketError):
                    materialize(self.fixture.root, inp, str(uuid.uuid4()))
                self._reset_fixture()

    def test_raw_task_identity_drift_rejected(self):
        inp = _build_valid_input(self.fixture)
        self.fixture.task_artifact["agent_id"] = "other_agent"
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        with self.assertRaises(EvidencePacketError):
            materialize(self.fixture.root, inp, str(uuid.uuid4()))

    def test_raw_task_source_and_scope_must_match_supplied_values(self):
        for field, value in (
            ("task_source", "planning/other.yaml"),
            ("allowed_files", "scripts/**"),
            ("forbidden_files", "other/**"),
        ):
            with self.subTest(field=field):
                inp = _build_valid_input(self.fixture)
                self.fixture.task_artifact[field] = value
                _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
                with self.assertRaises(EvidencePacketError):
                    materialize(self.fixture.root, inp, str(uuid.uuid4()))
                self._reset_fixture()

    def test_completion_exit_code_rejects_bool(self):
        inp = _build_valid_input(self.fixture)
        self.fixture.completion_artifact["exit_code"] = True
        _rewrite_json_artifact(self.fixture, inp, "completion", self.fixture.completion_artifact)
        with self.assertRaises(EvidencePacketError):
            materialize(self.fixture.root, inp, str(uuid.uuid4()))

    def test_numeric_prerelease_leading_zero_is_not_semver(self):
        inp = _build_valid_input(self.fixture)
        invalid = "1.0.0-01"
        inp["task"]["change_version"] = invalid
        self.fixture.task_artifact["change_version"] = invalid
        self.fixture.completion_artifact["change_version"] = invalid
        self._write_task_and_completion(inp)
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "CHANGE_VERSION_INVALID")


class TestExactNestedSchemas(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_all_nested_objects_reject_extra_fields(self):
        mutations = (
            lambda x: x["task"]["task_source"].__setitem__("extra", 1),
            lambda x: x["subject"]["raw_artifacts"].__setitem__("extra", {}),
            lambda x: x["subject"]["identity"].__setitem__("extra", "x"),
            lambda x: x["scope"]["raw_caller_strings"].__setitem__("extra", "x"),
            lambda x: x["scope"]["normalized"].__setitem__("extra", []),
            lambda x: x["scope"]["three_way_reconciliation"].__setitem__("extra", []),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                inp = _build_valid_input(self.fixture)
                mutate(inp)
                with self.assertRaises(EvidencePacketError):
                    materialize(self.fixture.root, inp, str(uuid.uuid4()))


class TestDispatchPathLanguageApplied(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_parser_rejects_every_unsupported_form(self):
        invalid = (
            "", "/a", "./a", "../a", "a/./b", "a/../b", "a//b", "a/b/",
            "a\\b", "a/latest/b", "a/LATEST/b", "a/\x00b", "a/b*", "a/*/b",
            "a/**/b", "a/***", "a/{b,c}", "a/?", "a/[b]",
        )
        for pattern in invalid:
            with self.subTest(pattern=repr(pattern)):
                with self.assertRaises(EvidencePacketError):
                    validate_dispatch_path_v1(pattern)

    def test_symbolic_containment_intersection_and_specificity_truth_table(self):
        cases = (
            ("dir/x", "dir/*", True, False, True),
            ("dir/x", "dir/**", True, False, True),
            ("dir/x", "dir/x/**", False, False, False),
            ("dir/*", "dir/x/**", False, False, False),
            ("dir/*", "dir/**", True, False, True),
            ("dir/x/*", "dir/**", True, False, True),
            ("dir/x/**", "dir/**", True, False, True),
            ("foo/**", "foobar/**", False, False, False),
        )
        for left, right, intersects, left_contains, right_contains in cases:
            with self.subTest(left=left, right=right):
                self.assertEqual(dispatch_paths_intersect(left, right), intersects)
                self.assertEqual(dispatch_path_contains(left, right), left_contains)
                self.assertEqual(dispatch_path_contains(right, left), right_contains)
        self.assertGreater(dispatch_path_specificity("dir/x"), dispatch_path_specificity("dir/*"))
        self.assertGreater(dispatch_path_specificity("dir/*"), dispatch_path_specificity("dir/**"))

    def test_invalid_forbidden_pattern_is_not_ignored(self):
        inp = _build_valid_input(self.fixture)
        inp["scope"]["raw_caller_strings"]["forbidden_files"] = "planning/**, bad/path*"
        inp["scope"]["normalized"]["forbidden_files"] = ["bad/path*", "planning/**"]
        self.fixture.task_artifact["forbidden_files"] = "planning/**, bad/path*"
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        with self.assertRaises(EvidencePacketError):
            materialize(self.fixture.root, inp, str(uuid.uuid4()))

    def test_changed_allowed_and_claim_paths_use_same_strict_parser(self):
        inp = _build_valid_input(self.fixture)
        invalid_path = "x/file*"
        _set_changed_files(self.fixture, inp, [invalid_path])
        inp["scope"]["raw_caller_strings"]["allowed_files"] = invalid_path
        inp["scope"]["normalized"]["allowed_files"] = [invalid_path]
        inp["scope"]["normalized"]["canonical_file_claims"] = [invalid_path]
        self.fixture.task_artifact["allowed_files"] = invalid_path
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        with self.assertRaises(EvidencePacketError):
            materialize(self.fixture.root, inp, str(uuid.uuid4()))

    def test_exact_claim_under_one_child_allowed_scope(self):
        inp = _build_valid_input(self.fixture)
        _set_changed_files(self.fixture, inp, ["x/file"])
        inp["scope"]["raw_caller_strings"] = {
            "allowed_files": "x/*", "forbidden_files": "z/**",
        }
        inp["scope"]["normalized"] = {
            "allowed_files": ["x/*"],
            "forbidden_files": ["z/**"],
            "canonical_file_claims": ["x/file"],
        }
        self.fixture.task_artifact["allowed_files"] = "x/*"
        self.fixture.task_artifact["forbidden_files"] = "z/**"
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        result = materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(result["packet"]["scope"]["three_way_reconciliation"]["status"], "PASS")

    def test_descendant_claim_intersects_one_child_forbidden_scope(self):
        inp = _build_valid_input(self.fixture)
        _set_changed_files(self.fixture, inp, ["x/y/z"])
        inp["scope"]["raw_caller_strings"] = {
            "allowed_files": "x/**", "forbidden_files": "x/*",
        }
        inp["scope"]["normalized"] = {
            "allowed_files": ["x/**"],
            "forbidden_files": ["x/*"],
            "canonical_file_claims": ["x/**"],
        }
        self.fixture.task_artifact["allowed_files"] = "x/**"
        self.fixture.task_artifact["forbidden_files"] = "x/*"
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        with self.assertRaises(EvidencePacketError) as ctx:
            materialize(self.fixture.root, inp, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "RECONCILIATION_MISMATCH")


class TestStrictDiffFileSet(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def test_diff_git_only_extra_path_enters_file_set(self):
        inp = _build_valid_input(self.fixture)
        injected = self.fixture.diff_content + b"diff --git a/evil.py b/evil.py\nnew file mode 100644\n"
        _set_changed_files(self.fixture, inp, self.fixture.changed_files, diff_content=injected)
        with self.assertRaises(EvidencePacketError):
            materialize(self.fixture.root, inp, str(uuid.uuid4()))

    def test_header_like_lines_after_completed_hunk_are_rejected(self):
        diff = (
            b"diff --git a/x/one.py b/x/one.py\n"
            b"--- a/x/one.py\n"
            b"+++ b/x/one.py\n"
            b"@@ -1 +1 @@\n"
            b"-old\n+new\n"
            b"--- a/x/hidden.py\n"
            b"+++ b/x/hidden.py\n"
        )
        with self.assertRaises(EvidencePacketError):
            extract_diff_file_set(diff)

    def test_header_like_source_content_inside_hunk_is_not_a_path(self):
        diff = (
            b"diff --git a/x/one.py b/x/one.py\n"
            b"--- a/x/one.py\n"
            b"+++ b/x/one.py\n"
            b"@@ -1 +1 @@\n"
            b"--- a/not-a-header.py\n"
            b"+++ b/not-a-header.py\n"
        )
        self.assertEqual(extract_diff_file_set(diff), {"x/one.py"})

    def test_quoted_diff_paths_with_spaces_are_parsed(self):
        diff = (
            b'diff --git "a/x/with space.py" "b/x/with space.py"\n'
            b'--- "a/x/with space.py"\n'
            b'+++ "b/x/with space.py"\n'
            b"@@ -1 +1 @@\n-old\n+new\n"
        )
        self.assertEqual(extract_diff_file_set(diff), {"x/with space.py"})

    def test_each_supported_diff_section_kind_is_accepted(self):
        cases = {
            "text": (
                b"diff --git a/x.py b/x.py\n"
                b"index 1111111..2222222 100644\n"
                b"--- a/x.py\n+++ b/x.py\n"
                b"@@ -1 +1 @@\n-old\n+new\n"
            ),
            "new": (
                b"diff --git a/new.py b/new.py\n"
                b"new file mode 100644\n"
                b"--- /dev/null\n+++ b/new.py\n"
                b"@@ -0,0 +1 @@\n+new\n"
            ),
            "delete": (
                b"diff --git a/deleted.py b/deleted.py\n"
                b"deleted file mode 100644\n"
                b"--- a/deleted.py\n+++ /dev/null\n"
                b"@@ -1 +0,0 @@\n-old\n"
            ),
            "binary": (
                b"diff --git a/image.bin b/image.bin\n"
                b"index 1111111..2222222 100644\n"
                b"Binary files a/image.bin and b/image.bin differ\n"
            ),
            "rename": (
                b"diff --git a/old.py b/new.py\n"
                b"similarity index 100%\n"
                b"rename from old.py\nrename to new.py\n"
            ),
            "copy": (
                b"diff --git a/source.py b/copy.py\n"
                b"similarity index 100%\n"
                b"copy from source.py\ncopy to copy.py\n"
            ),
            "mode-only": (
                b"diff --git a/tool.sh b/tool.sh\n"
                b"old mode 100644\nnew mode 100755\n"
            ),
            "rewrite": (
                b"diff --git a/rewrite.py b/rewrite.py\n"
                b"dissimilarity index 100%\n"
                b"--- a/rewrite.py\n+++ b/rewrite.py\n"
                b"@@ -1 +1 @@\n-old\n+new\n"
            ),
            "modified-rename": (
                b"diff --git a/old-mod.py b/new-mod.py\n"
                b"similarity index 80%\n"
                b"rename from old-mod.py\nrename to new-mod.py\n"
                b"--- a/old-mod.py\n+++ b/new-mod.py\n"
                b"@@ -1 +1 @@\n-old\n+new\n"
            ),
            "modified-copy": (
                b"diff --git a/source-mod.py b/copy-mod.py\n"
                b"similarity index 75%\n"
                b"copy from source-mod.py\ncopy to copy-mod.py\n"
                b"--- a/source-mod.py\n+++ b/copy-mod.py\n"
                b"@@ -1 +1 @@\n-old\n+new\n"
            ),
            "binary-mode-change": (
                b"diff --git a/mode.bin b/mode.bin\n"
                b"old mode 100644\nnew mode 100755\n"
                b"index 1111111..2222222\n"
                b"Binary files a/mode.bin and b/mode.bin differ\n"
            ),
            "binary-delete": (
                b"diff --git a/old.bin b/old.bin\n"
                b"deleted file mode 100644\n"
                b"Binary files a/old.bin and /dev/null differ\n"
            ),
            "git-binary-patch": (
                b"diff --git a/data.bin b/data.bin\n"
                b"index 1111111..2222222 100644\n"
                b"GIT binary patch\nliteral 1\nIc$@<O000310RR91\n"
            ),
            "binary-and-in-path": (
                b'diff --git "a/a and b" "b/a and b"\n'
                b"index 1111..2222 100644\n"
                b'Binary files "a/a and b" and "b/a and b" differ\n'
            ),
            "git-binary-patch-double": (
                b"diff --git a/double.bin b/double.bin\n"
                b"index 1111..2222 100644\nGIT binary patch\n"
                b"literal 1\nIc$@<O000310RR91\n\n"
                b"delta 1\nIc$@<O000310RR91\n"
            ),
        }
        expected = {
            "text": {"x.py"},
            "new": {"new.py"},
            "delete": {"deleted.py"},
            "binary": {"image.bin"},
            "rename": {"old.py", "new.py"},
            "copy": {"source.py", "copy.py"},
            "mode-only": {"tool.sh"},
            "rewrite": {"rewrite.py"},
            "modified-rename": {"old-mod.py", "new-mod.py"},
            "modified-copy": {"source-mod.py", "copy-mod.py"},
            "binary-mode-change": {"mode.bin"},
            "binary-delete": {"old.bin"},
            "git-binary-patch": {"data.bin"},
            "binary-and-in-path": {"a and b"},
            "git-binary-patch-double": {"double.bin"},
        }
        for kind, diff in cases.items():
            with self.subTest(kind=kind):
                self.assertEqual(extract_diff_file_set(diff), expected[kind])

    def test_malformed_diff_section_metadata_is_rejected(self):
        malformed = (
            b"diff --cc x.py\nindex 1111111,2222222..3333333\n",
            b"diff --combined x.py\nindex 1111111,2222222..3333333\n",
            b"unexpected preamble\ndiff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n",
            (
                b"diff --git a/x.py b/x.py\n"
                b"--- a/y.py\n+++ b/y.py\n"
            ),
            (
                b"diff --git a/x.py b/x.py\n"
                b"+++ b/x.py\n--- a/x.py\n"
            ),
            (
                b"diff --git a/x.py b/x.py\n"
                b"deleted file mode 100644\n"
                b"--- a/x.py\n+++ b/x.py\n"
            ),
            (
                b"diff --git a/old.py b/new.py\n"
                b"similarity index 100%\nrename from old.py\n"
            ),
            (
                b"diff --git a/foo b/foo\n"
                b"rename from foo\nrename to foo\n"
            ),
            (
                b"diff --git a/foo b/bar\n"
                b"deleted file mode 100644\n"
                b"rename from foo\nrename to bar\n"
            ),
            b"diff --git a/x.py b/x.py\nunknown metadata\n",
            b"diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n",
            b"diff --git a/x.py b/x.py\n@@ -1 +1 @@\n-old\n+new\n",
            b"diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+new\n",
            b"diff --git a/deleted.py b/deleted.py\n--- a/deleted.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n",
            (
                b"diff --git a/x.py b/x.py\n"
                b"new file mode 100644\ndeleted file mode 100644\n"
                b"--- /dev/null\n+++ /dev/null\n"
            ),
            b"diff --git a/x.py b/x.py\ndeleted file mode 100644\n",
            (
                b"diff --git a/old.py b/new.py\n"
                b"similarity index 100%\nrename from old.py\nrename to new.py\n"
                b"--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-old\n+new\n"
            ),
            (
                b"diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
                b"@@ -1 +1 @@\n-old\n\\ garbage\n+new\n"
            ),
            (
                b"diff --git a/new.py b/new.py\n"
                b"new file mode 100644\nsimilarity index 100%\n"
                b"--- /dev/null\n+++ b/new.py\n"
            ),
            (
                b"diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
                b"index 1111111..2222222 100644\n"
                b"@@ -1 +1 @@\n-old\n+new\n"
            ),
            (
                b"diff --git a/x.bin b/x.bin\n"
                b"Binary files a/x.bin and b/x.bin differ\n"
                b"index 1111111..2222222 100644\n"
            ),
            (
                b"diff --git a/tool.sh b/tool.sh\n"
                b"old mode 100644\nnew mode 100644\n"
            ),
            (
                b"diff --git a/x.bin b/x.bin\nGIT binary patch\n"
                b"literal 1\nliteral 1\nA\n"
            ),
            (
                b"diff --git a/x.bin b/x.bin\nGIT binary patch\n"
                b"literal 1\n\ndelta 1\nA\n"
            ),
            (
                b"diff --git a/x.bin b/x.bin\nGIT binary patch\n"
                b"literal 1\n\nA\n"
            ),
            (
                b"diff --git a/x.bin b/x.bin\nGIT binary patch\n"
                b"literal 1\nIc$@<O000310RR91\n\nIc$@<O000310RR91\n"
            ),
            (
                b"diff --git a/x.bin b/x.bin\nGIT binary patch\n"
                b"literal 1\nnot-a-valid-base85-line\n"
            ),
        )
        for diff in malformed:
            with self.subTest(diff=diff):
                with self.assertRaises(EvidencePacketError):
                    extract_diff_file_set(diff)

    def test_dev_null_is_only_a_marker_for_added_side(self):
        inp = _build_valid_input(self.fixture)
        diff = (
            b"diff --git a/x/new.py b/x/new.py\n"
            b"new file mode 100644\n"
            b"--- /dev/null\n+++ b/x/new.py\n"
            b"@@ -0,0 +1 @@\n+new\n"
        )
        _set_changed_files(self.fixture, inp, ["x/new.py"], diff_content=diff)
        inp["scope"]["raw_caller_strings"] = {
            "allowed_files": "x/**", "forbidden_files": "z/**",
        }
        inp["scope"]["normalized"] = {
            "allowed_files": ["x/**"], "forbidden_files": ["z/**"],
            "canonical_file_claims": ["x/new.py"],
        }
        self.fixture.task_artifact["allowed_files"] = "x/**"
        self.fixture.task_artifact["forbidden_files"] = "z/**"
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        materialize(self.fixture.root, inp, str(uuid.uuid4()))

    def test_dev_null_in_diff_git_header_is_rejected(self):
        inp = _build_valid_input(self.fixture)
        diff = b"diff --git /dev/null b/x/new.py\n--- /dev/null\n+++ b/x/new.py\n"
        _set_changed_files(self.fixture, inp, ["x/new.py"], diff_content=diff)
        inp["scope"]["raw_caller_strings"] = {
            "allowed_files": "x/**", "forbidden_files": "z/**",
        }
        inp["scope"]["normalized"] = {
            "allowed_files": ["x/**"], "forbidden_files": ["z/**"],
            "canonical_file_claims": ["x/new.py"],
        }
        self.fixture.task_artifact["allowed_files"] = "x/**"
        self.fixture.task_artifact["forbidden_files"] = "z/**"
        _rewrite_json_artifact(self.fixture, inp, "task", self.fixture.task_artifact)
        with self.assertRaises(EvidencePacketError):
            materialize(self.fixture.root, inp, str(uuid.uuid4()))

    def test_binary_and_rename_only_headers_contribute_both_paths(self):
        binary = b"diff --git a/assets/new.bin b/assets/new.bin\nnew file mode 100644\nBinary files /dev/null and b/assets/new.bin differ\n"
        rename = b"diff --git a/old/name.py b/new/name.py\nsimilarity index 100%\nrename from old/name.py\nrename to new/name.py\n"
        self.assertEqual(extract_diff_file_set(binary), {"assets/new.bin"})
        self.assertEqual(
            extract_diff_file_set(rename), {"old/name.py", "new/name.py"}
        )

    def test_reusing_a_path_across_sections_is_rejected(self):
        diff = (
            b"diff --git a/x.py b/x.py\n"
            b"deleted file mode 100644\n--- a/x.py\n+++ /dev/null\n"
            b"diff --git a/x.py b/x.py\n"
            b"new file mode 100644\n--- /dev/null\n+++ b/x.py\n"
        )
        with self.assertRaises(EvidencePacketError):
            extract_diff_file_set(diff)

    def test_copy_source_may_be_reused_for_multiple_targets(self):
        diff = (
            b"diff --git a/source.py b/copy-one.py\n"
            b"similarity index 100%\ncopy from source.py\ncopy to copy-one.py\n"
            b"diff --git a/source.py b/copy-two.py\n"
            b"similarity index 100%\ncopy from source.py\ncopy to copy-two.py\n"
        )
        self.assertEqual(
            extract_diff_file_set(diff),
            {"source.py", "copy-one.py", "copy-two.py"},
        )


class TestPublicationStateMachine(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()

    def tearDown(self):
        self.fixture.cleanup()

    def _assert_no_output_or_temp(self, publication_id):
        output_dir = os.path.join(self.fixture.root, "tmp", "quality", "evidence")
        self.assertFalse(os.path.lexists(os.path.join(output_dir, f"{publication_id}.json")))
        if not os.path.isdir(output_dir):
            return
        self.assertFalse(any(name.startswith(f".tmp.{publication_id}.") for name in os.listdir(output_dir)))

    def test_final_fifo_swap_is_rejected_without_blocking(self):
        output_dir = os.path.join(self.fixture.root, "tmp", "quality", "evidence")
        fifo_name = "final.pipe"
        fifo_path = os.path.join(output_dir, fifo_name)
        os.mkfifo(fifo_path)
        probe = (
            "import hashlib, os, stat, sys\n"
            "from unittest import mock\n"
            "import scripts.gates.evidence_packet as ep\n"
            "fd = os.open(sys.argv[1], os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))\n"
            "st = os.stat(sys.argv[2], dir_fd=fd, follow_symlinks=False)\n"
            "try:\n"
            "  with mock.patch.object(ep, '_is_owned_regular_at', return_value=True):\n"
            "    ep._verify_published_file_at(fd, sys.argv[2], (st.st_dev, st.st_ino, st.st_mode), '0' * 64)\n"
            "except ep.EvidencePacketError:\n"
            "  raise SystemExit(0)\n"
            "finally:\n"
            "  os.close(fd)\n"
            "raise SystemExit(2)\n"
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-c", probe, output_dir, fifo_name],
                cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                timeout=1,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail("final packet verification blocked on a FIFO replacement")
        self.assertEqual(completed.returncode, 0)

    def test_first_temp_fstat_failure_cleans_exclusive_temp(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        real_open = os.open
        real_fstat = os.fstat
        temp_fd = None
        failed = False

        def track_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal temp_fd
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            if isinstance(path, str) and path.startswith(f".tmp.{publication_id}."):
                temp_fd = fd
            return fd

        def fail_first_temp_fstat(fd):
            nonlocal failed
            if fd == temp_fd and not failed:
                failed = True
                raise OSError("first temp fstat fault")
            return real_fstat(fd)

        with mock.patch.object(evidence_packet.os, "open", side_effect=track_open), \
             mock.patch.object(evidence_packet.os, "fstat", side_effect=fail_first_temp_fstat):
            with self.assertRaises(EvidencePacketError) as ctx:
                materialize(self.fixture.root, inp, publication_id)
        self.assertEqual(ctx.exception.code, "PUBLISH_OPEN_FAILED")
        self.assertTrue(failed)
        self._assert_no_output_or_temp(publication_id)

    def test_each_fresh_publication_directory_and_parent_are_fsynced(self):
        inp = _build_valid_input(self.fixture)
        _relocate_quality_artifacts(self.fixture, inp)
        publication_id = str(uuid.uuid4())
        real_mkdir = os.mkdir
        real_fsync = os.fsync
        real_fstat = os.fstat
        events = []

        def identity(fd):
            st = real_fstat(fd)
            return st.st_dev, st.st_ino

        def record_mkdir(path, mode=0o777, *, dir_fd=None):
            parent_identity = identity(dir_fd)
            result = real_mkdir(path, mode, dir_fd=dir_fd)
            child_st = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
            events.append(("mkdir", path, parent_identity, (child_st.st_dev, child_st.st_ino)))
            return result

        def record_fsync(fd):
            events.append(("fsync", identity(fd)))
            return real_fsync(fd)

        with mock.patch.object(evidence_packet.os, "mkdir", side_effect=record_mkdir), \
             mock.patch.object(evidence_packet.os, "fsync", side_effect=record_fsync):
            materialize(self.fixture.root, inp, publication_id)

        mkdir_indexes = [i for i, event in enumerate(events) if event[0] == "mkdir"]
        self.assertEqual([events[i][1] for i in mkdir_indexes], ["tmp", "quality", "evidence"])
        for position, event_index in enumerate(mkdir_indexes):
            _, _, parent_identity, child_identity = events[event_index]
            next_mkdir = (
                mkdir_indexes[position + 1]
                if position + 1 < len(mkdir_indexes)
                else len(events)
            )
            fsynced = {
                event[1]
                for event in events[event_index + 1:next_mkdir]
                if event[0] == "fsync"
            }
            self.assertIn(parent_identity, fsynced)
            self.assertIn(child_identity, fsynced)

    def test_each_fresh_directory_fsync_failure_is_typed(self):
        for failure_ordinal in range(1, 7):
            with self.subTest(failure_ordinal=failure_ordinal):
                if failure_ordinal > 1:
                    self.fixture.cleanup()
                    self.fixture = FixtureRepo()
                inp = _build_valid_input(self.fixture)
                _relocate_quality_artifacts(self.fixture, inp)
                publication_id = str(uuid.uuid4())
                real_fsync = os.fsync
                calls = 0

                def fail_selected_setup_fsync(fd):
                    nonlocal calls
                    calls += 1
                    if calls == failure_ordinal:
                        raise OSError(f"setup fsync fault {failure_ordinal}")
                    return real_fsync(fd)

                with mock.patch.object(
                    evidence_packet.os,
                    "fsync",
                    side_effect=fail_selected_setup_fsync,
                ):
                    with self.assertRaises(EvidencePacketError) as ctx:
                        materialize(self.fixture.root, inp, publication_id)
                self.assertEqual(calls, failure_ordinal)
                self.assertTrue(ctx.exception.code.startswith("PUBLISH_"))
                self._assert_no_output_or_temp(publication_id)

    def test_file_fsync_side_effect_mutation_fails_prelink(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        real_fsync = os.fsync
        mutated = False

        def fsync_and_mutate(fd):
            nonlocal mutated
            real_fsync(fd)
            if not mutated and stat.S_ISREG(os.fstat(fd).st_mode):
                inp["subject"]["main_agent_attestation"]["result_fields"]["status"] = "FAIL"
                mutated = True

        with mock.patch.object(evidence_packet.os, "fsync", side_effect=fsync_and_mutate):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, publication_id)
        self._assert_no_output_or_temp(publication_id)

    def test_link_side_effect_mutation_fails_postlink_and_rolls_back_owned_final(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        real_link = os.link

        def link_and_mutate(source, target, **kwargs):
            real_link(source, target, **kwargs)
            inp["subject"]["main_agent_attestation"]["result_fields"]["status"] = "FAIL"

        with mock.patch.object(evidence_packet.os, "link", side_effect=link_and_mutate):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, publication_id)
        self._assert_no_output_or_temp(publication_id)

    def test_postlink_rollback_retries_transient_isolation_failure(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        output_name = f"{publication_id}.json"
        real_link = os.link
        real_rename = os.rename
        failed_once = False

        def link_and_mutate(source, target, **kwargs):
            real_link(source, target, **kwargs)
            inp["subject"]["main_agent_attestation"]["result_fields"]["status"] = "FAIL"

        def fail_first_final_isolation(source, target, **kwargs):
            nonlocal failed_once
            if not failed_once and source == output_name:
                failed_once = True
                raise OSError("transient rollback rename failure")
            return real_rename(source, target, **kwargs)

        with mock.patch.object(evidence_packet.os, "link", side_effect=link_and_mutate), \
             mock.patch.object(evidence_packet.os, "rename", side_effect=fail_first_final_isolation):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, publication_id)
        self.assertTrue(failed_once)
        self._assert_no_output_or_temp(publication_id)

    def test_rollback_removes_relinked_owned_final_after_isolation(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        output_name = f"{publication_id}.json"
        real_link = os.link
        real_rename = os.rename
        injected = False

        def link_and_mutate(source, target, **kwargs):
            real_link(source, target, **kwargs)
            inp["subject"]["main_agent_attestation"]["result_fields"]["status"] = "FAIL"

        def relink_after_isolation(source, target, **kwargs):
            nonlocal injected
            result = real_rename(source, target, **kwargs)
            if not injected and source == output_name:
                injected = True
                real_link(
                    target,
                    source,
                    src_dir_fd=kwargs["dst_dir_fd"],
                    dst_dir_fd=kwargs["src_dir_fd"],
                    follow_symlinks=False,
                )
            return result

        with mock.patch.object(evidence_packet.os, "link", side_effect=link_and_mutate), \
             mock.patch.object(evidence_packet.os, "rename", side_effect=relink_after_isolation):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, publication_id)
        self.assertTrue(injected)
        self._assert_no_output_or_temp(publication_id)

    def test_postlink_rollback_fsyncs_directory_after_removal(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        real_link = os.link
        real_fsync = os.fsync
        fsync_kinds = []

        def link_and_mutate(source, target, **kwargs):
            real_link(source, target, **kwargs)
            inp["subject"]["main_agent_attestation"]["result_fields"]["status"] = "FAIL"

        def record_fsync(fd):
            fsync_kinds.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            return real_fsync(fd)

        with mock.patch.object(evidence_packet.os, "link", side_effect=link_and_mutate), \
             mock.patch.object(evidence_packet.os, "fsync", side_effect=record_fsync):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, publication_id)
        self.assertEqual(fsync_kinds[-1], "dir")
        self._assert_no_output_or_temp(publication_id)

    def test_link_that_succeeds_then_wrapper_raises_is_rolled_back(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        real_link = os.link

        def link_then_raise(source, target, **kwargs):
            real_link(source, target, **kwargs)
            raise OSError("wrapper failed after link")

        with mock.patch.object(evidence_packet.os, "link", side_effect=link_then_raise):
            with self.assertRaises(EvidencePacketError) as ctx:
                materialize(self.fixture.root, inp, publication_id)
        self.assertEqual(ctx.exception.code, "PUBLISH_LINK_FAILED")
        self._assert_no_output_or_temp(publication_id)

    def test_link_exception_never_unlinks_foreign_replacement(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        real_link = os.link
        real_unlink = os.unlink
        foreign = b"foreign-winner\n"

        def replace_then_raise(source, target, **kwargs):
            real_link(source, target, **kwargs)
            dir_fd = kwargs["dst_dir_fd"]
            real_unlink(target, dir_fd=dir_fd)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=dir_fd)
            os.write(fd, foreign)
            os.close(fd)
            raise OSError("wrapper failed after replacement")

        with mock.patch.object(evidence_packet.os, "link", side_effect=replace_then_raise):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, publication_id)
        final_path = os.path.join(self.fixture.root, "tmp", "quality", "evidence", f"{publication_id}.json")
        with open(final_path, "rb") as stream:
            self.assertEqual(stream.read(), foreign)

    def test_postlink_failure_never_unlinks_foreign_replacement(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        real_link = os.link
        real_unlink = os.unlink
        foreign = b"foreign-after-link\n"

        def replace_and_mutate(source, target, **kwargs):
            real_link(source, target, **kwargs)
            dir_fd = kwargs["dst_dir_fd"]
            real_unlink(target, dir_fd=dir_fd)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=dir_fd)
            os.write(fd, foreign)
            os.close(fd)
            inp["subject"]["main_agent_attestation"]["result_fields"]["status"] = "FAIL"

        with mock.patch.object(evidence_packet.os, "link", side_effect=replace_and_mutate):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, publication_id)
        final_path = os.path.join(self.fixture.root, "tmp", "quality", "evidence", f"{publication_id}.json")
        with open(final_path, "rb") as stream:
            self.assertEqual(stream.read(), foreign)

    def test_tmp_unlink_failure_rolls_back_owned_final_and_cleans_retry(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        real_unlink = os.unlink
        failed_once = False

        def fail_first_tmp_unlink(path, *args, **kwargs):
            nonlocal failed_once
            if not failed_once:
                failed_once = True
                raise OSError("temporary cleanup fault")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(evidence_packet.os, "unlink", side_effect=fail_first_tmp_unlink):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, publication_id)
        self._assert_no_output_or_temp(publication_id)

    def test_directory_fsync_failure_is_stable_and_rolls_back(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        real_fsync = os.fsync

        def fail_directory_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("directory fsync fault")
            return real_fsync(fd)

        with mock.patch.object(evidence_packet.os, "fsync", side_effect=fail_directory_fsync):
            with self.assertRaises(EvidencePacketError) as ctx:
                materialize(self.fixture.root, inp, publication_id)
        self.assertTrue(ctx.exception.code.startswith("PUBLISH_"))
        self._assert_no_output_or_temp(publication_id)

    def test_write_and_file_fsync_faults_are_stable_and_clean(self):
        for operation in ("write", "fsync"):
            with self.subTest(operation=operation):
                inp = _build_valid_input(self.fixture)
                publication_id = str(uuid.uuid4())
                if operation == "write":
                    patcher = mock.patch.object(
                        evidence_packet.os, "write", side_effect=OSError("write fault")
                    )
                else:
                    patcher = mock.patch.object(
                        evidence_packet.os, "fsync", side_effect=OSError("fsync fault")
                    )
                with patcher:
                    with self.assertRaises(EvidencePacketError):
                        materialize(self.fixture.root, inp, publication_id)
                self._assert_no_output_or_temp(publication_id)

    def test_short_writes_are_completed_before_publication(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        real_write = os.write

        def short_write(fd, data):
            return real_write(fd, data[:7])

        with mock.patch.object(evidence_packet.os, "write", side_effect=short_write):
            result = materialize(self.fixture.root, inp, publication_id)
        final_path = os.path.join(self.fixture.root, result["publication"]["locator"])
        self.assertEqual(_hash_file(final_path), result["publication"]["sha256"])

    def test_partial_write_then_exception_leaves_no_packet_or_temp(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        real_write = os.write
        calls = 0

        def partial_then_raise(fd, data):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_write(fd, data[:11])
            raise OSError("write failed after partial progress")

        with mock.patch.object(
            evidence_packet.os, "write", side_effect=partial_then_raise
        ):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, publication_id)
        self._assert_no_output_or_temp(publication_id)

    def test_preexisting_temp_symlink_is_never_followed_or_overwritten(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        fixed_uuid = uuid.UUID("11111111-1111-4111-8111-111111111111")
        output_dir = os.path.join(self.fixture.root, "tmp", "quality", "evidence")
        candidate = f".tmp.{publication_id}.{fixed_uuid.hex}"
        candidate_path = os.path.join(output_dir, candidate)
        os.symlink("foreign-target", candidate_path)

        with mock.patch.object(evidence_packet.uuid, "uuid4", return_value=fixed_uuid):
            with self.assertRaises(EvidencePacketError) as ctx:
                materialize(self.fixture.root, inp, publication_id)
        self.assertEqual(ctx.exception.code, "PUBLISH_RACE")
        self.assertTrue(os.path.islink(candidate_path))
        self.assertEqual(os.readlink(candidate_path), "foreign-target")

    def test_link_fault_before_side_effect_is_stable_and_clean(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        with mock.patch.object(
            evidence_packet.os, "link", side_effect=OSError("link fault")
        ):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, publication_id)
        self._assert_no_output_or_temp(publication_id)

    def test_link_content_mutation_is_detected_and_owned_final_removed(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        real_link = os.link

        def link_and_tamper(source, target, **kwargs):
            real_link(source, target, **kwargs)
            fd = os.open(target, os.O_WRONLY | os.O_APPEND, dir_fd=kwargs["dst_dir_fd"])
            os.write(fd, b"tampered")
            os.close(fd)

        with mock.patch.object(evidence_packet.os, "link", side_effect=link_and_tamper):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, publication_id)
        self._assert_no_output_or_temp(publication_id)

    def test_temp_creation_is_bound_to_opened_directory_inode(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        output_dir = os.path.join(self.fixture.root, "tmp", "quality", "evidence")
        held_dir = os.path.join(self.fixture.root, "tmp", "quality", "evidence-held")
        outside_dir = tempfile.mkdtemp(prefix="evidence-packet-outside-")
        real_open = os.open
        swapped = False

        def swap_path_during_temp_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            if (
                not swapped
                and isinstance(path, str)
                and path.startswith(f".tmp.{publication_id}.")
            ):
                swapped = True
                os.rename(output_dir, held_dir)
                os.symlink(outside_dir, output_dir)
                try:
                    return real_open(path, flags, mode, dir_fd=dir_fd)
                finally:
                    os.unlink(output_dir)
                    os.rename(held_dir, output_dir)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        try:
            with mock.patch.object(evidence_packet.os, "open", side_effect=swap_path_during_temp_open):
                result = materialize(self.fixture.root, inp, publication_id)
            self.assertTrue(swapped)
            self.assertTrue(os.path.isfile(os.path.join(self.fixture.root, result["publication"]["locator"])))
            self.assertEqual(os.listdir(outside_dir), [])
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    def test_final_directory_binding_is_checked_after_last_file_verification(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        output_dir = os.path.join(self.fixture.root, "tmp", "quality", "evidence")
        held_dir = os.path.join(self.fixture.root, "tmp", "quality", "evidence-held")
        real_verify = evidence_packet._verify_published_file_at
        calls = 0

        def verify_then_swap(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = real_verify(*args, **kwargs)
            if calls == 2:
                os.rename(output_dir, held_dir)
                os.mkdir(output_dir)
            return result

        try:
            with mock.patch.object(
                evidence_packet,
                "_verify_published_file_at",
                side_effect=verify_then_swap,
            ):
                with self.assertRaises(EvidencePacketError) as ctx:
                    materialize(self.fixture.root, inp, publication_id)
            self.assertEqual(ctx.exception.code, "PUBLISH_DIR_DRIFT")
        finally:
            if os.path.isdir(output_dir) and os.path.isdir(held_dir):
                os.rmdir(output_dir)
                os.rename(held_dir, output_dir)
        self._assert_no_output_or_temp(publication_id)

    def test_final_locator_never_contains_a_symlink_ancestor(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        tmp_dir = os.path.join(self.fixture.root, "tmp")
        held_tmp = os.path.join(self.fixture.root, "tmp-held")
        real_verify = evidence_packet._verify_published_file_at
        calls = 0

        def verify_then_alias_ancestor(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = real_verify(*args, **kwargs)
            if calls == 2:
                os.rename(tmp_dir, held_tmp)
                os.symlink("tmp-held", tmp_dir)
            return result

        try:
            with mock.patch.object(
                evidence_packet,
                "_verify_published_file_at",
                side_effect=verify_then_alias_ancestor,
            ):
                with self.assertRaises(EvidencePacketError) as ctx:
                    materialize(self.fixture.root, inp, publication_id)
            self.assertEqual(ctx.exception.code, "PUBLISH_DIR_DRIFT")
        finally:
            if os.path.islink(tmp_dir):
                os.unlink(tmp_dir)
            if os.path.isdir(held_tmp):
                os.rename(held_tmp, tmp_dir)
        self._assert_no_output_or_temp(publication_id)

    def test_repo_root_inode_is_bound_across_validation_and_publication(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        original_root = self.fixture.root
        held_root = original_root + "-held"
        candidate_root = original_root + "-candidate"
        shutil.copytree(original_root, candidate_root)
        real_publish = evidence_packet.publish_packet_atomic
        swapped = False

        def swap_real_directory_then_publish(*args, **kwargs):
            nonlocal swapped
            swapped = True
            os.rename(original_root, held_root)
            os.rename(candidate_root, original_root)
            return real_publish(*args, **kwargs)

        try:
            with mock.patch.object(
                evidence_packet,
                "publish_packet_atomic",
                side_effect=swap_real_directory_then_publish,
            ):
                with self.assertRaises(EvidencePacketError):
                    materialize(original_root, inp, publication_id)
            self.assertTrue(swapped)
        finally:
            if os.path.isdir(original_root) and os.path.isdir(held_root):
                shutil.rmtree(original_root, ignore_errors=True)
                os.rename(held_root, original_root)
            else:
                shutil.rmtree(candidate_root, ignore_errors=True)
        self._assert_no_output_or_temp(publication_id)

    def test_rollback_rename_race_restores_foreign_final(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        output_name = f"{publication_id}.json"
        foreign = b"foreign-during-rollback\n"
        real_link = os.link
        real_rename = os.rename
        real_unlink = os.unlink
        swapped = False

        def mutate_after_link(source, target, **kwargs):
            real_link(source, target, **kwargs)
            inp["subject"]["main_agent_attestation"]["result_fields"]["status"] = "FAIL"

        def replace_at_rollback(source, target, **kwargs):
            nonlocal swapped
            if not swapped and source == output_name:
                swapped = True
                dir_fd = kwargs["src_dir_fd"]
                real_unlink(source, dir_fd=dir_fd)
                fd = os.open(
                    source,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o644,
                    dir_fd=dir_fd,
                )
                os.write(fd, foreign)
                os.close(fd)
            return real_rename(source, target, **kwargs)

        with mock.patch.object(evidence_packet.os, "link", side_effect=mutate_after_link), \
             mock.patch.object(evidence_packet.os, "rename", side_effect=replace_at_rollback):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, publication_id)
        self.assertTrue(swapped)
        final_path = os.path.join(self.fixture.root, "tmp", "quality", "evidence", output_name)
        with open(final_path, "rb") as stream:
            self.assertEqual(stream.read(), foreign)

    def test_rollback_restore_never_overwrites_newer_foreign_final(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        output_name = f"{publication_id}.json"
        old_foreign = b"foreign-old\n"
        new_foreign = b"foreign-new\n"
        real_link = os.link
        real_rename = os.rename
        real_unlink = os.unlink
        isolated_foreign = False

        def mutate_after_link(source, target, **kwargs):
            nonlocal isolated_foreign
            if source.startswith(".rollback."):
                dir_fd = kwargs["dst_dir_fd"]
                fd = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o644,
                    dir_fd=dir_fd,
                )
                os.write(fd, new_foreign)
                os.close(fd)
                return real_link(source, target, **kwargs)
            real_link(source, target, **kwargs)
            inp["subject"]["main_agent_attestation"]["result_fields"]["status"] = "FAIL"

        def replace_before_isolation(source, target, **kwargs):
            nonlocal isolated_foreign
            if not isolated_foreign and source == output_name:
                isolated_foreign = True
                dir_fd = kwargs["src_dir_fd"]
                real_unlink(source, dir_fd=dir_fd)
                fd = os.open(
                    source,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o644,
                    dir_fd=dir_fd,
                )
                os.write(fd, old_foreign)
                os.close(fd)
            return real_rename(source, target, **kwargs)

        with mock.patch.object(evidence_packet.os, "link", side_effect=mutate_after_link), \
             mock.patch.object(evidence_packet.os, "rename", side_effect=replace_before_isolation):
            with self.assertRaises(EvidencePacketError):
                materialize(self.fixture.root, inp, publication_id)
        self.assertTrue(isolated_foreign)
        final_path = os.path.join(
            self.fixture.root, "tmp", "quality", "evidence", output_name
        )
        with open(final_path, "rb") as stream:
            self.assertEqual(stream.read(), new_foreign)

    def test_foreign_restore_link_side_effect_then_error_leaves_no_private_name(self):
        output_dir = os.path.join(self.fixture.root, "tmp", "quality", "evidence")
        dir_fd = os.open(output_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        isolated_name = ".rollback.foreign"
        output_name = "foreign.json"
        fd = os.open(
            isolated_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
            dir_fd=dir_fd,
        )
        os.write(fd, b"foreign\n")
        os.close(fd)
        real_link = os.link

        def link_then_raise(source, target, **kwargs):
            real_link(source, target, **kwargs)
            raise OSError("restore wrapper failed after link")

        try:
            with mock.patch.object(evidence_packet.os, "link", side_effect=link_then_raise):
                evidence_packet._restore_foreign_at(
                    dir_fd, isolated_name, output_name
                )
            self.assertIsNone(
                evidence_packet._entry_identity(dir_fd, isolated_name)
            )
            self.assertIsNotNone(evidence_packet._entry_identity(dir_fd, output_name))
        finally:
            try:
                os.unlink(isolated_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
            try:
                os.unlink(output_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
            os.close(dir_fd)

    def test_same_publication_id_has_one_winner_without_overwrite(self):
        inp = _build_valid_input(self.fixture)
        publication_id = str(uuid.uuid4())
        results = []
        failures = []

        def worker():
            try:
                results.append(materialize(self.fixture.root, copy.deepcopy(inp), publication_id))
            except EvidencePacketError as exc:
                failures.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 1)
        self.assertEqual(len(failures), 7)
        self.assertTrue(
            all(exc.code in {"PUBLISH_EXISTS", "PUBLISH_RACE"} for exc in failures)
        )
        final_path = os.path.join(self.fixture.root, results[0]["publication"]["locator"])
        self.assertEqual(_hash_file(final_path), results[0]["publication"]["sha256"])


class TestVerifierReusesMaterializerSemantics(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()
        self.result = materialize(
            self.fixture.root, _build_valid_input(self.fixture), str(uuid.uuid4())
        )
        self.locator = self.result["publication"]["locator"]
        self.packet_path = os.path.join(self.fixture.root, self.locator)

    def tearDown(self):
        self.fixture.cleanup()

    def _tamper(self, mutate):
        with open(self.packet_path, "rb") as stream:
            packet = json.load(stream)
        mutate(packet)
        data = evidence_packet.canonical_json(packet)
        _write_file(self.packet_path, data)
        return _hash_bytes(data)

    def test_missing_nested_fields_are_stable_evidence_errors(self):
        deletions = (
            ("schema_version",), ("publication_id",), ("content_fingerprint",),
            ("task",), ("task", "task_id"), ("task", "task_version"),
            ("task", "change_version"), ("task", "task_source"),
            ("task", "task_source", "locator"),
            ("task", "task_source", "sha256"),
            ("subject",), ("subject", "raw_artifacts"),
            ("subject", "main_agent_attestation"), ("subject", "identity"),
            ("subject", "raw_artifacts", "task"),
            ("subject", "raw_artifacts", "completion"),
            ("subject", "raw_artifacts", "stdout"),
            ("subject", "raw_artifacts", "stderr"),
            ("subject", "raw_artifacts", "changed_file_snapshot"),
            ("subject", "raw_artifacts", "diff"),
            ("subject", "raw_artifacts", "tests"),
            ("subject", "raw_artifacts", "task", "locator"),
            ("subject", "raw_artifacts", "task", "sha256"),
            ("subject", "raw_artifacts", "tests", 0, "locator"),
            ("subject", "raw_artifacts", "tests", 0, "sha256"),
            ("subject", "main_agent_attestation", "actor_id"),
            ("subject", "main_agent_attestation", "reviewed_at"),
            ("subject", "main_agent_attestation", "result_fields"),
            ("subject", "main_agent_attestation", "field_source_bindings"),
            ("subject", "main_agent_attestation", "result_fields", "status"),
            ("subject", "main_agent_attestation", "result_fields", "changed_files"),
            ("subject", "main_agent_attestation", "result_fields", "validation"),
            ("subject", "main_agent_attestation", "result_fields", "acceptance_evidence"),
            ("subject", "main_agent_attestation", "result_fields", "effect_checks"),
            ("subject", "main_agent_attestation", "result_fields", "risks"),
            ("subject", "main_agent_attestation", "result_fields", "validation", "status"),
            ("subject", "main_agent_attestation", "result_fields", "validation", "evidence_locator"),
            ("subject", "main_agent_attestation", "field_source_bindings", "status"),
            ("subject", "main_agent_attestation", "field_source_bindings", "risks"),
            ("subject", "identity", "parent_session_id"),
            ("subject", "identity", "agent_id"),
            ("subject", "identity", "run_id"),
            ("subject", "identity", "session_id"),
            ("subject", "identity", "client"),
            ("subject", "identity", "parent_client"),
            ("scope",), ("scope", "changed_files"),
            ("scope", "raw_caller_strings"), ("scope", "normalized"),
            ("scope", "three_way_reconciliation"),
            ("scope", "raw_caller_strings", "allowed_files"),
            ("scope", "raw_caller_strings", "forbidden_files"),
            ("scope", "normalized", "allowed_files"),
            ("scope", "normalized", "forbidden_files"),
            ("scope", "normalized", "canonical_file_claims"),
            ("scope", "three_way_reconciliation", "changed_outside_allowed"),
            ("scope", "three_way_reconciliation", "changed_matching_forbidden"),
            ("scope", "three_way_reconciliation", "changed_without_claim"),
            ("scope", "three_way_reconciliation", "claims_outside_allowed"),
            ("scope", "three_way_reconciliation", "claims_intersecting_forbidden"),
            ("scope", "three_way_reconciliation", "status"),
        )

        def pop_path(packet, path):
            owner = packet
            for component in path[:-1]:
                owner = owner[component]
            if isinstance(owner, list):
                owner.pop(path[-1])
            else:
                owner.pop(path[-1])

        with open(self.packet_path, "rb") as stream:
            original = stream.read()
        for path in deletions:
            with self.subTest(path=path):
                _write_file(self.packet_path, original)
                new_sha = self._tamper(lambda packet, p=path: pop_path(packet, p))
                with self.assertRaises(EvidencePacketError):
                    verify_packet_strict(self.fixture.root, self.locator, new_sha)

    def test_extra_nested_field_is_rejected(self):
        new_sha = self._tamper(lambda packet: packet["scope"]["normalized"].__setitem__("extra", []))
        with self.assertRaises(EvidencePacketError):
            verify_packet_strict(self.fixture.root, self.locator, new_sha)

    def test_identity_and_scope_drift_are_rejected(self):
        new_sha = self._tamper(
            lambda packet: packet["subject"]["identity"].__setitem__("agent_id", "other_agent")
        )
        with self.assertRaises(EvidencePacketError):
            verify_packet_strict(self.fixture.root, self.locator, new_sha)

    def test_reconciliation_drift_is_rejected(self):
        new_sha = self._tamper(
            lambda packet: packet["scope"]["three_way_reconciliation"].__setitem__(
                "changed_outside_allowed", ["evil.py"]
            )
        )
        with self.assertRaises(EvidencePacketError):
            verify_packet_strict(self.fixture.root, self.locator, new_sha)

    def test_current_task_source_drift_is_rejected(self):
        _write_file(
            os.path.join(self.fixture.root, self.fixture.task_source_path),
            b"changed task source\n",
        )
        with self.assertRaises(EvidencePacketError):
            verify_packet_strict(
                self.fixture.root, self.locator, self.result["publication"]["sha256"]
            )

    def test_current_snapshot_member_drift_is_rejected(self):
        _write_file(
            os.path.join(self.fixture.root, self.fixture.changed_files[0]),
            b"changed after publication\n",
        )
        with self.assertRaises(EvidencePacketError):
            verify_packet_strict(
                self.fixture.root, self.locator, self.result["publication"]["sha256"]
            )

    def test_content_fingerprint_drift_is_rejected(self):
        new_sha = self._tamper(lambda packet: packet.__setitem__("content_fingerprint", "0" * 64))
        with self.assertRaises(EvidencePacketError):
            verify_packet_strict(self.fixture.root, self.locator, new_sha)

    def test_publication_id_must_equal_locator_basename(self):
        new_sha = self._tamper(lambda packet: packet.__setitem__("publication_id", str(uuid.uuid4())))
        with self.assertRaises(EvidencePacketError):
            verify_packet_strict(self.fixture.root, self.locator, new_sha)

    def test_packet_locator_must_use_exact_publication_namespace(self):
        outside_locator = "tmp/elsewhere/packet.json"
        outside_path = os.path.join(self.fixture.root, outside_locator)
        with open(self.packet_path, "rb") as stream:
            _write_file(outside_path, stream.read())
        with self.assertRaises(EvidencePacketError):
            verify_packet_strict(
                self.fixture.root, outside_locator, self.result["publication"]["sha256"]
            )

    def test_external_hash_must_be_lowercase_sha256(self):
        for invalid in (None, "", "A" * 64, True):
            with self.subTest(invalid=invalid):
                with self.assertRaises(EvidencePacketError):
                    verify_packet_strict(self.fixture.root, self.locator, invalid)

    def test_empty_tests_are_rejected_without_key_error(self):
        new_sha = self._tamper(
            lambda packet: packet["subject"]["raw_artifacts"].__setitem__("tests", [])
        )
        with self.assertRaises(EvidencePacketError):
            verify_packet_strict(self.fixture.root, self.locator, new_sha)

    def test_missing_source_binding_is_rejected_without_key_error(self):
        new_sha = self._tamper(
            lambda packet: packet["subject"]["main_agent_attestation"]
            ["field_source_bindings"].pop("risks")
        )
        with self.assertRaises(EvidencePacketError):
            verify_packet_strict(self.fixture.root, self.locator, new_sha)


class TestCodexWorkPackageProjection(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureRepo()
        self.addCleanup(self.fixture.cleanup)
        self.inp = _build_valid_input(self.fixture)
        self.projection = _use_codex_work_package_projection(
            self.fixture, self.inp
        )

    def _materialize(self):
        return materialize(self.fixture.root, self.inp, str(uuid.uuid4()))

    def _rewrite_task(self):
        self.fixture.task_artifact = self.projection
        _rewrite_json_artifact(
            self.fixture, self.inp, "task", self.projection
        )

    def test_pretty_noncanonical_codex_runner_bytes_materialize(self):
        raw_path = os.path.join(
            self.fixture.root, self.fixture.task_artifact_path
        )
        with open(raw_path, "rb") as stream:
            raw_bytes = stream.read()
        self.assertNotEqual(raw_bytes, evidence_packet.canonical_json(self.projection))
        result = self._materialize()
        packet = verify_packet_strict(
            self.fixture.root,
            result["publication"]["locator"],
            result["publication"]["sha256"],
        )
        self.assertEqual(packet["subject"]["identity"]["client"], "codex")

    def test_unknown_client_is_rejected(self):
        self.inp["subject"]["identity"]["client"] = "unknown"
        self.projection["client"] = "unknown"
        self._rewrite_task()
        self.fixture.completion_artifact["client"] = "unknown"
        _rewrite_json_artifact(
            self.fixture, self.inp, "completion",
            self.fixture.completion_artifact,
        )
        with self.assertRaises(EvidencePacketError):
            self._materialize()

    def test_duplicate_key_in_codex_raw_task_is_rejected(self):
        descriptor = self.inp["subject"]["raw_artifacts"]["task"]
        path = os.path.join(self.fixture.root, descriptor["locator"])
        with open(path, "rb") as stream:
            valid = stream.read()
        duplicate = b'{"work_package_id":"LF-WP-EVIL-001",' + valid[1:]
        _write_file(path, duplicate)
        descriptor["sha256"] = _hash_bytes(duplicate)
        with self.assertRaises(EvidencePacketError):
            self._materialize()

    def test_qoder_permission_or_resume_field_smuggling_is_rejected(self):
        for field, value in (("permission_mode", "bypass_permissions"), ("_resume_mode", True)):
            with self.subTest(field=field):
                fixture = FixtureRepo()
                self.addCleanup(fixture.cleanup)
                inp = _build_valid_input(fixture)
                projection = _use_codex_work_package_projection(fixture, inp)
                projection[field] = value
                fixture.task_artifact = projection
                _rewrite_json_artifact(fixture, inp, "task", projection)
                with self.assertRaises(EvidencePacketError):
                    materialize(fixture.root, inp, str(uuid.uuid4()))

    def test_single_task_fake_work_package_is_rejected(self):
        task_id = self.projection["task_id"]
        caller = self.projection["caller_contract"]
        self.projection["task_ids"] = [task_id]
        caller["task_ids"] = [task_id]
        for field in (
            "task_versions", "change_versions", "expected_outputs_by_task",
            "acceptance_by_task", "validation_commands",
        ):
            caller[field] = {task_id: caller[field][task_id]}
        self._rewrite_task()
        with self.assertRaises(EvidencePacketError):
            self._materialize()

    def test_missing_target_task_is_rejected(self):
        self.projection["target_task_id"] = "LF-TSK-QLT-9999"
        self._rewrite_task()
        with self.assertRaises(EvidencePacketError):
            self._materialize()

    def test_codex_runner_identity_drift_is_rejected(self):
        self.fixture.completion_artifact["agent_id"] = "different_agent"
        _rewrite_json_artifact(
            self.fixture, self.inp, "completion",
            self.fixture.completion_artifact,
        )
        with self.assertRaises(EvidencePacketError) as raised:
            self._materialize()
        self.assertEqual(raised.exception.code, "IDENTITY_DRIFT")


if __name__ == "__main__":
    unittest.main()
