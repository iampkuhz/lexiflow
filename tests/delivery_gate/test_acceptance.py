"""验证 Task 主体冻结和后续漂移检测。"""

from __future__ import annotations

import unittest

from scripts.acceptance.task_definition.task_acceptance_plan import verify_plan
from scripts.acceptance.evidence.acceptance_record_store import RecordError
from tests.delivery_gate.fixtures import DeliveryGateFixture
from scripts.verification.planning.check_definitions import load_declarations
from tests.delivery_gate.fixtures import write_declarations


class TestTaskSubjectAcceptance(unittest.TestCase):
    def setUp(self):
        self.fixture = DeliveryGateFixture()
        self.submission = self.fixture.create_submission()

    def tearDown(self):
        self.fixture.cleanup()

    def test_unrelated_write_does_not_invalidate_subject(self):
        self.fixture.write("unrelated.txt", "unrelated change\n")
        verify_plan(self.fixture.root, self.submission)

    def test_related_dependency_addition_and_subject_bytes_drift_fail(self):
        self.fixture.write("protected.py", "new relevant input\n")
        with self.assertRaises(RecordError):
            verify_plan(self.fixture.root, self.submission)

    def test_catalog_requirement_drift_fails(self):
        self.fixture.write("planning/workstreams.yaml", "workstreams: []\n")
        with self.assertRaises(RecordError):
            verify_plan(self.fixture.root, self.submission)

    def test_task_bytes_and_policy_drift_fail(self):
        self.fixture.write("src/test.py", "changed after submission\n")
        with self.assertRaises(RecordError):
            verify_plan(self.fixture.root, self.submission)

    def test_policy_drift_fails(self):
        policy = self.fixture.root / "config/engineering/risk.yaml"
        policy.write_bytes(policy.read_bytes() + b"\n")
        with self.assertRaises(RecordError):
            verify_plan(self.fixture.root, self.submission)

    def test_new_related_path_cannot_hide_behind_frozen_path_list(self):
        import yaml

        fixture = DeliveryGateFixture()
        try:
            catalog_path = fixture.root / "planning/workstreams.yaml"
            catalog = yaml.safe_load(catalog_path.read_text())
            task = catalog["workstreams"][0]["epics"][0]["capabilities"][0][
                "seed_tasks"
            ][0]
            task["allowed_files"] = ["src/**"]
            catalog_path.write_text(yaml.safe_dump(catalog))
            declaration_path = fixture.root / "config/engineering/module-checks.yaml"
            declarations = load_declarations(fixture.root)
            for check in declarations["checks"]:
                check["input_paths"].append("src")
            write_declarations(declaration_path, declarations["checks"])
            # 仅对隔离测试仓库固定 Catalog/输入声明，不改真实仓库 Git 状态。
            fixture._git("add", ".")
            fixture._git("commit", "-m", "fixture scope")
            submission = fixture.create_submission()
            fixture.write("src/new.py", "new related file\n")
            with self.assertRaisesRegex(RecordError, "risk-assessment-drift"):
                verify_plan(fixture.root, submission)
        finally:
            fixture.cleanup()

    def test_task_subject_metadata_tampering_fails(self):
        import copy

        for field, value in (
            ("task_paths", []),
            ("dependency_paths", ["unrelated.txt"]),
            ("selected_check_ids", []),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(self.submission)
                tampered["task_subject"][field] = value
                with self.assertRaises(RecordError):
                    verify_plan(self.fixture.root, tampered)
