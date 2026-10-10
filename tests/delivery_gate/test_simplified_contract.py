"""精简入口、canonical Catalog 与冻结要求全值比对回归。"""

from __future__ import annotations

import copy
import json
import unittest
from argparse import Namespace
from unittest.mock import patch

import yaml

from scripts.delivery_gate.__main__ import _cmd_validate, main
from scripts.delivery_gate.acceptance import verify_plan
from scripts.delivery_gate.records import (
    RecordError,
    canonical_bytes,
    content_hash,
    delivery_gate_locator,
    load_layer,
    read_json,
    sha256_bytes,
)
from scripts.delivery_gate.requirements import load_task_requirements
from scripts.delivery_gate.review import ReviewError, review
from scripts.delivery_gate.validate import _verify_frozen
from tests.delivery_gate.fixtures import (
    TASK_ID,
    DeliveryGateFixture,
    REVIEWER_SESSION,
    make_mock_runtime,
)


class TestCanonicalRequirements(unittest.TestCase):
    def setUp(self):
        self.fixture = DeliveryGateFixture()
        self.path = self.fixture.root / "planning/workstreams.yaml"
        self.catalog = yaml.safe_load(self.path.read_text())
        self.task = self.catalog["workstreams"][0]["epics"][0]["capabilities"][0][
            "seed_tasks"
        ][0]

    def tearDown(self):
        self.fixture.cleanup()

    def _load(self):
        self.path.write_text(yaml.safe_dump(self.catalog, sort_keys=False))
        return load_task_requirements(self.fixture.root, TASK_ID)

    def test_version_alias_cannot_replace_canonical_task_version(self):
        self.task["version"] = self.task.pop("task_version")
        with self.assertRaisesRegex(RecordError, "task-version-invalid"):
            self._load()

    def test_missing_depends_on_cannot_default_to_zero_or_legacy_edges(self):
        del self.task["depends_on"]
        for legacy in (None, [], ["LF-TSK-TEST-0002"]):
            with self.subTest(legacy=legacy):
                self.task["dependencies"] = legacy
                with self.assertRaisesRegex(RecordError, "task-dependencies-invalid"):
                    self._load()

    def test_each_dependency_requires_typed_version_bound_edge(self):
        valid = {
            "task_id": "LF-TSK-TEST-0002",
            "required_task_version": 1,
            "required_change_version": "1.0.0",
            "required_result": "PASS",
        }
        invalid = ["LF-TSK-TEST-0002", {"task_id": "LF-TSK-TEST-0002"}]
        for field in ("required_task_version", "required_change_version"):
            missing = dict(valid)
            del missing[field]
            invalid.append(missing)
            invalid.append({**valid, field: None})
        invalid.extend(
            {**valid, field: value}
            for field, value in (
                ("required_task_version", True),
                ("required_task_version", 0),
                ("required_task_version", "1"),
                ("required_change_version", ""),
                ("required_result", "BLOCKED"),
            )
        )
        for edge in invalid:
            with self.subTest(edge=edge):
                self.task["depends_on"] = [edge]
                with self.assertRaisesRegex(RecordError, "task-dependencies-invalid"):
                    self._load()
        self.task["depends_on"] = [valid]
        self.assertEqual(self._load()["dependencies"], [valid])
        self.task["depends_on"] = [valid, valid]
        with self.assertRaisesRegex(RecordError, "task-dependencies-invalid"):
            self._load()

    def test_owner_inheritance_does_not_mutate_raw_task_or_snapshot(self):
        self.catalog["workstreams"][0]["phase"] = "P2"
        original = copy.deepcopy(self.task)
        requirements = self._load()
        self.assertEqual(self.task, original)
        self.assertNotIn("owner", self.task)
        self.assertNotIn("phase", self.task)
        self.assertEqual(
            requirements["source"]["sha256"],
            sha256_bytes(
                canonical_bytes(
                    {
                        "schema_version": "lexiflow.delivery-gate-task-requirements.v2",
                        "owner": "LF-WS-TEST",
                        "task": original,
                    }
                )
            ),
        )


class TestFrozenRequirementEquivalence(unittest.TestCase):
    def setUp(self):
        self.fixture = DeliveryGateFixture()
        self.submission = self.fixture.create_submission()

    def tearDown(self):
        self.fixture.cleanup()

    def test_plan_loads_complete_requirements_exactly_once(self):
        with patch(
            "scripts.delivery_gate.acceptance.load_task_requirements",
            wraps=load_task_requirements,
        ) as load:
            self.assertEqual(
                _verify_frozen(self.fixture.root, self.submission),
                self.submission["verification_freeze"],
            )
        load.assert_called_once_with(self.fixture.root, TASK_ID)

    def test_all_frozen_requirement_fields_still_fail_closed_on_drift(self):
        for field, value in (
            ("task_version", 2),
            ("change_version", "2.0.0"),
            ("dependencies", [{"task_id": "another-task"}]),
            ("required_check_ids", []),
            ("allowed_files", []),
            ("forbidden_files", []),
            ("file_claims", []),
            ("approval_requirement", {"required": True}),
            ("source", {"locator": "planning/workstreams.yaml", "sha256": "a" * 64}),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.submission)
                changed["task_requirements"][field] = value
                with self.assertRaisesRegex(RecordError, "task-requirements-drift"):
                    verify_plan(self.fixture.root, changed)

    def test_invalid_or_missing_catalog_remains_requirement_drift(self):
        path = self.fixture.root / "planning/workstreams.yaml"
        for content in ("workstreams: []\n", "workstreams: [broken\n"):
            with self.subTest(content=content):
                path.write_text(content)
                with self.assertRaisesRegex(RecordError, "task-requirements-drift"):
                    verify_plan(self.fixture.root, self.submission)
        path.unlink()
        with self.assertRaisesRegex(RecordError, "task-requirements-drift"):
            verify_plan(self.fixture.root, self.submission)

    def test_invalid_frozen_requirements_remain_fail_closed(self):
        for requirements in (None, [], {}, {"task_id": 1}):
            with self.subTest(requirements=requirements):
                changed = {**self.submission, "task_requirements": requirements}
                with self.assertRaisesRegex(RecordError, "task-requirements-drift"):
                    verify_plan(self.fixture.root, changed)


class TestSimplifiedEntryPoints(unittest.TestCase):
    def test_single_validation_cli_preserves_single_object_shape(self):
        result = {"submission_id": "one", "validation_id": "v", "result": "PASS"}
        with (
            patch(
                "scripts.delivery_gate.validate.validate_batch", return_value=[result]
            ) as validate,
            patch("scripts.delivery_gate.__main__._output") as output,
        ):
            _cmd_validate(Namespace(repo_root="root", submission_id=["one"]))
        validate.assert_called_once_with("root", submission_ids=["one"])
        output.assert_called_once_with(result)

    def test_review_cli_only_requires_validation_and_explicit_opinion(self):
        with (
            patch(
                "scripts.delivery_gate.review.review", return_value={"result": "PASS"}
            ) as reviewer,
            patch("scripts.delivery_gate.__main__._output", return_value=0),
        ):
            self.assertEqual(
                main(["review", "--validation-id", "v", "--decision", "PASS"]), 0
            )
        self.assertEqual(
            reviewer.call_args.kwargs,
            {"validation_id": "v", "decision": "PASS", "findings": []},
        )

    def test_read_json_never_accepts_unhashed_records(self):
        fixture = DeliveryGateFixture()
        self.addCleanup(fixture.cleanup)
        fixture.write("unhashed.json", '{"result":"PASS"}')
        with self.assertRaisesRegex(RecordError, "record-hash-missing"):
            read_json(fixture.root, "unhashed.json", "fixture")


class TestDerivedReviewBinding(unittest.TestCase):
    def setUp(self):
        self.fixture = DeliveryGateFixture()
        self.fixture.create_submission()
        self.fixture.create_validation()
        self.runtime = patch(
            "scripts.agents.local_codex_runtime.discover",
            return_value=make_mock_runtime(REVIEWER_SESSION),
        )
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.addCleanup(self.fixture.cleanup)

    def _review(self):
        return review(
            self.fixture.root,
            validation_id=self.fixture.validation_id,
            decision="PASS",
            findings=[
                {
                    "finding_id": "f",
                    "severity": "PASS",
                    "code": "reviewed",
                    "evidence": [{"detail": "synthetic frozen diff"}],
                }
            ],
        )

    def test_review_derives_subject_without_rerunning_checks(self):
        with patch(
            "scripts.verification.scenarios.execute_single_check",
            side_effect=AssertionError("review must not run checks"),
        ):
            result = self._review()
        self.assertEqual(result["submission_id"], self.fixture.submission_id)
        receipt = load_layer(self.fixture.root, "reviews", result["review_id"])
        self.assertEqual(receipt["validation_id"], self.fixture.validation_id)
        self.assertFalse(receipt["delivery_rerun"])
        with self.assertRaisesRegex(ReviewError, "review-already-published"):
            self._review()

    def test_validly_rehashed_validation_does_not_bypass_frozen_bindings(self):
        path = self.fixture.root / delivery_gate_locator(
            "validations", self.fixture.validation_id
        )
        original = json.loads(path.read_text())
        second_id = self.fixture.create_submission()["submission_id"]
        for field, replacement in (
            ("submission_id", second_id),
            ("submission_content_hash", "a" * 64),
            ("frozen_input_fingerprint", "a" * 64),
            ("acceptance_plan_hash", "a" * 64),
        ):
            with self.subTest(field=field):
                value = {**original, field: replacement}
                value["content_hash"] = content_hash(value)
                path.chmod(0o600)
                path.write_text(json.dumps(value))
                path.chmod(0o400)
                with self.assertRaises(ReviewError):
                    self._review()
        self.assertFalse(
            (self.fixture.root / "tmp/quality/delivery-gate/reviews").exists()
        )
