"""验证代码变更送验及固定发行模式的互斥边界。"""

from __future__ import annotations

import json
import unittest
import uuid
from unittest.mock import patch

from scripts.acceptance.task_operations.submit_task_for_acceptance import (
    SubmissionError,
    submit,
)
from tests.delivery_gate.fixtures import (
    PRODUCER_SESSION,
    DeliveryGateFixture,
    make_mock_runtime,
)


class TestSubmit(unittest.TestCase):
    def setUp(self):
        self.f = DeliveryGateFixture()
        self.f.create_verification_report()

    def tearDown(self):
        self.f.cleanup()

    @patch("scripts.agents.identity.codex_actor_identity.discover")
    def test_binds_report_scope_catalog_and_full_freeze(self, m):
        m.return_value = make_mock_runtime(PRODUCER_SESSION)
        r = submit(
            self.f.root, task_id="LF-TSK-TEST-0001", change_report_id=self.f.report_id
        )
        from scripts.acceptance.evidence.acceptance_record_store import load_submission

        v = load_submission(self.f.root, r["submission_id"])
        self.assertEqual(
            v["task_requirements"]["required_check_ids"], ["fixture.change"]
        )
        self.assertIn("fixture.baseline", v["verification_freeze"]["input_snapshots"])
        self.assertIn("fixture.change", v["verification_freeze"]["input_snapshots"])

    @patch("scripts.agents.identity.codex_actor_identity.discover")
    def test_scope_confirmation_is_not_an_input_and_unknown_task_fails(self, m):
        m.return_value = make_mock_runtime(PRODUCER_SESSION)
        with self.assertRaises(TypeError):
            submit(
                self.f.root,
                task_id="LF-TSK-TEST-0001",
                change_report_id=self.f.report_id,
                confirm_scope_report_id="wrong",
            )
        with self.assertRaises(SubmissionError) as c:
            submit(
                self.f.root,
                task_id="LF-TSK-UNKNOWN-0001",
                change_report_id=self.f.report_id,
            )
        self.assertEqual(c.exception.code, "task-not-found")

    @patch("scripts.agents.identity.codex_actor_identity.discover")
    def test_non_formal_task_cannot_borrow_release_mode(self, m):
        m.return_value = make_mock_runtime(PRODUCER_SESSION)
        with self.assertRaises(SubmissionError) as c:
            submit(self.f.root, task_id="LF-TSK-TEST-0001", release_handoff=True)
        self.assertEqual(c.exception.code, "release-task-not-formal")

    def test_submission_modes_are_mutually_exclusive(self):
        with self.assertRaises(SubmissionError) as c:
            submit(
                self.f.root,
                task_id="LF-TSK-TEST-0001",
                change_report_id=self.f.report_id,
                release_handoff=True,
            )
        self.assertEqual(c.exception.code, "submit-mode-conflict")

    @patch("scripts.agents.identity.codex_actor_identity.discover")
    def test_manually_written_minimal_pass_report_is_not_a_submission_source(self, m):
        m.return_value = make_mock_runtime(PRODUCER_SESSION)
        rid = str(uuid.uuid4())
        path = self.f.root / f"tmp/quality/verification-reports/{rid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        forged = {
            "schema_version": "lexiflow.verification-report.v1",
            "run_id": rid,
            "result": "PASS",
            "reason": "",
            "scope": "change-targeted",
            "checks": [],
            "input_fingerprint": "",
            "configuration_fingerprint": "",
            "coverage_gaps": [],
            "scope_review": {"kind": "no-context", "changed_files": ["src/test.py"]},
            "base": "HEAD",
        }
        path.write_text(json.dumps(forged))
        with self.assertRaises(SubmissionError) as c:
            submit(self.f.root, task_id="LF-TSK-TEST-0001", change_report_id=rid)
        self.assertEqual(c.exception.code, "submission-invalid")

    @patch("scripts.agents.identity.codex_actor_identity.discover")
    def test_daily_report_must_cover_entire_real_diff_including_untracked(self, m):
        m.return_value = make_mock_runtime(PRODUCER_SESSION)
        self.f.write("other-task/untracked.py", "different task")
        with self.assertRaises(SubmissionError) as c:
            submit(
                self.f.root,
                task_id="LF-TSK-TEST-0001",
                change_report_id=self.f.report_id,
            )
        self.assertEqual(c.exception.code, "scope-input-drift")
