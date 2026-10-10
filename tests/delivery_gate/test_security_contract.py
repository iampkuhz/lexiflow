from __future__ import annotations
from scripts.acceptance.task_definition.task_acceptance_scope import subject_paths
import json, os, tempfile, unittest, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import yaml
from scripts.acceptance.evidence.acceptance_actor_identity import verify_authority
from scripts.acceptance.task_operations.evaluate_task_acceptance import check_conditions
from scripts.acceptance.evidence.acceptance_record_store import (
    RecordError,
    delivery_gate_locator,
    content_hash,
    list_layer,
    list_submissions,
    load_layer,
    load_submission,
    publish_json,
    parse_json,
    read_regular_bytes,
)
from scripts.acceptance.evidence.implementation_run_evidence import resolve_producer
from scripts.acceptance.task_operations.run_independent_task_validation import ValidationError, _verify_producer
from tests.delivery_gate.fixtures import (
    DeliveryGateFixture,
    PRODUCER_SESSION,
    mock_authority,
    make_mock_runtime,
)


class TestRecordSafety(unittest.TestCase):
    def test_concurrent_publication_has_one_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rid = str(uuid.uuid4())
            locator = delivery_gate_locator("submissions", rid)

            def write(i):
                try:
                    publish_json(root, locator, {"writer": i})
                    return "PASS"
                except RecordError as exc:
                    return exc.code

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(write, (1, 2)))
            self.assertEqual(results.count("PASS"), 1)
            self.assertEqual(results.count("immutable-record-exists"), 1)

    def test_duplicate_keys_symlink_and_fifo_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "x.json"
            path.write_text('{"a":1,"a":2}')
            with self.assertRaisesRegex(RecordError, "duplicate JSON key"):
                parse_json(read_regular_bytes(root, "x.json"), "fixture")
            path.unlink()
            outside = root / "outside"
            outside.write_text("x")
            path.symlink_to(outside)
            with self.assertRaisesRegex(RecordError, "unsafe-path"):
                parse_json(read_regular_bytes(root, "x.json"), "fixture")
            path.unlink()
            if hasattr(os, "mkfifo"):
                os.mkfifo(path)
                with self.assertRaisesRegex(RecordError, "unsafe-path"):
                    parse_json(read_regular_bytes(root, "x.json"), "fixture")

    def test_dangling_record_directories_are_not_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "tmp/quality/delivery-gate/current"
            base.mkdir(parents=True)
            (base / "submissions").symlink_to(root / "missing-submissions")
            with self.assertRaisesRegex(RecordError, "unsafe-path"):
                list_submissions(root)
            (base / "validations").symlink_to(root / "missing-validations")
            with self.assertRaisesRegex(RecordError, "unsafe-path"):
                list_layer(root, "validations", "submission_id", str(uuid.uuid4()))

    def test_publication_does_not_follow_existing_parent_symlink(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as outside,
        ):
            root = Path(tmp)
            (root / "tmp").symlink_to(Path(outside))
            rid = str(uuid.uuid4())
            with self.assertRaisesRegex(RecordError, "unsafe-path"):
                publish_json(
                    root, delivery_gate_locator("submissions", rid), {"writer": 1}
                )
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_link_side_effect_error_rolls_back_owned_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tmp/quality/delivery-gate/current/submissions").mkdir(parents=True)
            rid = str(uuid.uuid4())
            locator = delivery_gate_locator("submissions", rid)
            real_link = os.link

            def link_then_error(*args, **kwargs):
                real_link(*args, **kwargs)
                raise OSError("injected after link")

            with patch(
                "scripts.acceptance.evidence.acceptance_record_store.os.link", side_effect=link_then_error
            ):
                with self.assertRaisesRegex(RecordError, "publication-failed"):
                    publish_json(root, locator, {"writer": 1})
            self.assertFalse((root / locator).exists())
            publish_json(root, locator, {"writer": 1})


class TestAuthoritySemantics(unittest.TestCase):
    def test_historical_receipt_has_no_synthetic_ttl_but_source_must_reverify(self):
        runtime = make_mock_runtime(PRODUCER_SESSION)
        record = {
            "producer_identity": runtime.context,
            "runtime_proof": runtime.proof,
            "authority": mock_authority(PRODUCER_SESSION),
        }
        with patch(
            "scripts.agents.identity.codex_actor_identity.verify_proof", return_value=None
        ):
            self.assertIsNone(
                verify_authority(Path("/tmp"), record, "producer_identity")
            )
        from scripts.agents.identity.codex_actor_identity import CodexRuntimeError

        with patch(
            "scripts.agents.identity.codex_actor_identity.verify_proof",
            side_effect=CodexRuntimeError("runtime-proof-drift", "stale source"),
        ):
            self.assertEqual(
                verify_authority(Path("/tmp"), record, "producer_identity"),
                "producer_identity-source-runtime-proof-drift",
            )


class TestProducerFacts(unittest.TestCase):
    def test_qoder_run_reference_is_rejected_without_reading_or_rewriting(self):
        from scripts.acceptance.evidence.implementation_run_evidence import ProducerError, producer_identity

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = str(uuid.uuid4())
            base = root / f"tmp/qoder-tasks/{run_id}"
            base.mkdir(parents=True)
            for name in ("task.json", "completion.json", "result.json"):
                (base / name).write_bytes(b'{"status":"PASS","client":"qoder"}\n')
            before = {p.name: p.read_bytes() for p in base.iterdir()}
            req = {
                "task_id": "LF-TSK-TEST-0001",
                "task_version": 1,
                "change_version": "1.0.0",
            }
            with self.assertRaisesRegex(ProducerError, "producer-run-unavailable"):
                resolve_producer(root, req, run_id)
            submission = {
                "producer": {
                    "kind": "qoder-work-package",
                    "run_id": run_id,
                    "identity": make_mock_runtime(PRODUCER_SESSION).context,
                },
                "submitter_identity": make_mock_runtime(PRODUCER_SESSION).context,
            }
            with self.assertRaisesRegex(ProducerError, "producer-source-invalid"):
                producer_identity(submission)
            with self.assertRaisesRegex(ValidationError, "producer-source-invalid"):
                _verify_producer(root, submission)
            self.assertEqual(before, {p.name: p.read_bytes() for p in base.iterdir()})


from scripts.acceptance.evidence.task_review_diff import review_patch


class TestDependencyDagAndApproval(unittest.TestCase):
    def setUp(self):
        self.f = DeliveryGateFixture()
        self._catalog()
        self.f._git("add", "planning/workstreams.yaml")
        self.f._git("commit", "-m", "dependency catalog fixture")
        self.f.create_verification_report()

    def tearDown(self):
        self.f.cleanup()

    def _catalog(self):
        def task(tid, deps=None, approval=None):
            value = {
                "id": tid,
                "task_version": 1,
                "change_version": "1.0.0",
                "depends_on": [
                    {
                        "task_id": dep,
                        "required_task_version": 1,
                        "required_change_version": "1.0.0",
                        "required_result": "PASS",
                    }
                    for dep in (deps or [])
                ],
                "required_check_ids": ["fixture.change"],
                "allowed_files": ["src/test.py"],
                "forbidden_files": [],
                "file_claims": [{"path": "src/**"}],
            }
            if approval:
                value["phase_entry_prerequisite"] = {
                    "previous_gate_id": "G1",
                    "previous_gate_exit_task_id": approval,
                    "required_user_approval": "APPROVED",
                }
            return value

        tasks = [
            task("LF-TSK-TEST-0001"),
            task("LF-TSK-TEST-0002", ["LF-TSK-TEST-0001"]),
            task("LF-TSK-TEST-0003", ["LF-TSK-TEST-0002"], "LF-TSK-TEST-0002"),
        ]
        self.f.write(
            "planning/workstreams.yaml",
            yaml.safe_dump(
                {
                    "workstreams": [
                        {
                            "id": "LF-WS-TEST",
                            "epics": [{"capabilities": [{"seed_tasks": tasks}]}],
                        }
                    ]
                },
                sort_keys=False,
            ),
        )

    def _submission(self, tid):
        record = self.f.create_submission(task_id=tid)
        self.f.create_validation()
        self.f.create_review()
        return record["submission_id"]

    def test_multilevel_hash_update_and_explicit_approval(self):
        a = self._submission("LF-TSK-TEST-0001")
        ca = check_conditions(self.f.root, submission_id=a)
        self.assertEqual(ca["result"], "PASS")
        b = self._submission("LF-TSK-TEST-0002")
        cb = check_conditions(self.f.root, submission_id=b)
        self.assertEqual(cb["result"], "PASS")
        c1 = self._submission("LF-TSK-TEST-0003")
        missing = check_conditions(self.f.root, submission_id=c1)
        self.assertEqual(
            (missing["result"], missing["reason"], missing["published"]),
            ("BLOCKED", "user-approval-missing", False),
        )
        self.assertEqual(list_layer(self.f.root, "checks", "submission_id", c1), [])
        approval = {
            "schema_version": "lexiflow.user-approval.v1",
            "approval_id": str(uuid.uuid4()),
            "gate_id": "G1",
            "decision": "APPROVED",
            "subject_task_id": "LF-TSK-TEST-0002",
            "subject_task_version": 1,
            "subject_change_version": "1.0.0",
            "subject_check_content_hash": cb["content_hash"],
            "statement": "User explicitly approved G1",
            "approved_at": "2026-09-21T00:00:00Z",
        }
        publish_json(
            self.f.root, "tmp/quality/delivery-gate/current/approvals/G1.json", approval
        )
        approved = check_conditions(self.f.root, submission_id=c1)
        self.assertEqual(approved["result"], "PASS")
        again = check_conditions(self.f.root, submission_id=c1)
        self.assertEqual(
            (again["check_id"], again["idempotent"]), (approved["check_id"], True)
        )
        # A validly re-hashed mutation changes the transitive edge and invalidates B.
        path = self.f.root / delivery_gate_locator("checks", ca["check_id"])
        value = json.loads(path.read_text())
        value["reason"] = "changed"
        value["content_hash"] = content_hash(value)
        path.chmod(0o600)
        path.write_text(json.dumps(value, separators=(",", ":"), sort_keys=True))
        path.chmod(0o400)
        self.assertEqual(
            check_conditions(self.f.root, submission_id=c1)["reason"],
            "dependency-not-pass",
        )

    def test_missing_dependency_can_be_added_then_same_submission_rechecked(self):
        dependent = self._submission("LF-TSK-TEST-0002")
        first = check_conditions(self.f.root, submission_id=dependent)
        self.assertEqual(
            (first["result"], first["reason"], first["published"]),
            ("BLOCKED", "dependency-not-pass", False),
        )
        self.assertEqual(
            list_layer(self.f.root, "checks", "submission_id", dependent), []
        )
        dependency = self._submission("LF-TSK-TEST-0001")
        self.assertEqual(
            check_conditions(self.f.root, submission_id=dependency)["result"], "PASS"
        )
        self.assertEqual(
            check_conditions(self.f.root, submission_id=dependent)["result"], "PASS"
        )


if __name__ == "__main__":
    unittest.main()
