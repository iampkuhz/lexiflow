"""同次 Delivery Gate 求值的 DAG 复用与 fail-closed 行为。"""

from __future__ import annotations

import unittest
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import yaml

from scripts.acceptance.task_operations.evaluate_task_acceptance import (
    _dependency_status,
    _EvaluationContext,
    _EvidenceInspection,
    check_conditions,
)
from scripts.acceptance.evidence.acceptance_record_store import (
    delivery_gate_locator,
    load_layer,
    load_submission,
    publish_json,
)
from tests.delivery_gate.fixtures import DeliveryGateFixture


class TestEvaluationContext(unittest.TestCase):
    """验证共享依赖只重复使用本次已验证证据，调用间不共享结果。"""

    def _diamond(self):
        edges = {"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]}
        nodes = {}
        by_task = {}
        for task, dependencies in edges.items():
            sid = f"submission-{task}"
            node = {
                "submission_id": sid,
                "content_hash": f"submission-hash-{task}",
                "task_requirements": {
                    "task_id": task,
                    "task_version": 1,
                    "change_version": "1.0.0",
                    "dependencies": [
                        {
                            "task_id": dep,
                            "type": "hard",
                            "required_task_version": 1,
                            "required_change_version": "1.0.0",
                            "required_result": "PASS",
                        }
                        for dep in dependencies
                    ],
                },
            }
            nodes[task] = node
            by_task[sid] = task
        records = {}
        for task, dependencies in edges.items():
            sid = nodes[task]["submission_id"]
            records[sid] = (
                {"submission_id": sid, "validation_id": f"validation-{task}"},
                None,
                {
                    "result": "PASS",
                    "content_hash": f"check-hash-{task}",
                    "conditions": {
                        "dependency_receipts": [
                            {
                                "task_id": dep,
                                "submission_content_hash": nodes[dep]["content_hash"],
                                "check_content_hash": f"check-hash-{dep}",
                            }
                            for dep in dependencies
                        ]
                    },
                },
            )
        return nodes, records

    def test_shared_diamond_inspects_once_per_stage_with_required_context(self):
        nodes, records = self._diamond()
        inspections = []

        def inspect(_repo, node, *args):
            inspections.append(node["task_requirements"]["task_id"])
            return _EvidenceInspection((), True)

        with (
            patch(
                "scripts.acceptance.task_operations.evaluate_task_acceptance.load_task_requirements",
                side_effect=lambda repo, task: nodes[task]["task_requirements"],
            ) as requirements,
            patch(
                "scripts.acceptance.task_operations.evaluate_task_acceptance._records",
                side_effect=lambda repo, node, context: records[node["submission_id"]],
            ) as reads,
            patch(
                "scripts.acceptance.task_operations.evaluate_task_acceptance._inspect_dependency", side_effect=inspect
            ),
            patch("scripts.acceptance.task_operations.evaluate_task_acceptance.review_required", return_value=False),
        ):
            context = _EvaluationContext(submissions=list(nodes.values()))
            result = _dependency_status(Path("/unused"), nodes["D"], set(), context)
            self.assertTrue(result[0])
            self.assertEqual(inspections, ["B", "A", "C"])
            self.assertEqual(requirements.call_count, 4)
            self.assertEqual(reads.call_count, 3)
            fresh = _EvaluationContext(submissions=list(nodes.values()))
            self.assertEqual(
                _dependency_status(Path("/unused"), nodes["D"], set(), fresh), result
            )
            self.assertEqual(inspections, ["B", "A", "C", "B", "A", "C"])
            self.assertEqual(requirements.call_count, 8)

    def test_cycle_is_fail_closed_and_detected_before_memo_lookup(self) -> None:
        node = {
            "submission_id": "submission-A",
            "task_requirements": {"task_id": "A", "dependencies": []},
        }
        context = _EvaluationContext(completed={"submission-A": (True, [])})
        result = _dependency_status(Path("/unused"), node, {"A"}, context)
        self.assertEqual(
            result, (False, [{"task_id": "A", "status": "dependency-cycle"}])
        )

    def test_context_does_not_cross_invocation_boundary(self) -> None:
        node = {
            "submission_id": "submission-A",
            "task_requirements": {"task_id": "A", "dependencies": []},
        }
        context = _EvaluationContext()
        context.completed[node["submission_id"]] = (True, [])
        fresh = _EvaluationContext()
        self.assertIsNot(context, fresh)
        self.assertNotIn(node["submission_id"], fresh.completed)


class TestEvaluationFreshness(unittest.TestCase):
    """验证求值期间新增候选和附件改写不会穿过最终 freshness 边界。"""

    def setUp(self) -> None:
        self.fixture = DeliveryGateFixture()
        self.submission = self.fixture.create_submission()
        self.validation = self.fixture.create_validation()
        self.fixture.create_review()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_new_submission_during_evaluation_blocks_publication(self) -> None:
        original = _EvaluationContext.fresh_snapshot

        def add_candidate(context, repo):
            record = load_submission(repo, self.submission["submission_id"])
            record.pop("content_hash")
            import uuid

            new_id = str(uuid.uuid4())
            record["submission_id"] = new_id
            publish_json(repo, delivery_gate_locator("submissions", new_id), record)
            return original(context, repo)

        with patch.object(_EvaluationContext, "fresh_snapshot", new=add_candidate):
            result = check_conditions(
                self.fixture.root, submission_id=self.submission["submission_id"]
            )
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["reason"], "current-input-invalid")

    def test_target_report_drift_during_evaluation_blocks_publication(self) -> None:
        original = _EvaluationContext.fresh_snapshot
        validation = load_layer(
            self.fixture.root, "validations", self.validation["validation_id"]
        )

        def drift_attachment(context, repo):
            path = repo / validation["verification_report"]["locator"]
            path.chmod(0o600)
            path.write_bytes(path.read_bytes() + b"drift")
            path.chmod(0o400)
            return original(context, repo)

        with patch.object(_EvaluationContext, "fresh_snapshot", new=drift_attachment):
            result = check_conditions(
                self.fixture.root, submission_id=self.submission["submission_id"]
            )
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["reason"], "current-input-invalid")

    def test_task_policy_drift_during_evaluation_blocks_publication(self) -> None:
        original = _EvaluationContext.fresh_snapshot

        def drift_policy(context, repo):
            catalog_path = repo / "planning/workstreams.yaml"
            catalog = yaml.safe_load(catalog_path.read_text())
            task = catalog["workstreams"][0]["epics"][0]["capabilities"][0][
                "seed_tasks"
            ][0]
            task["task_version"] += 1
            catalog_path.write_text(yaml.safe_dump(catalog, sort_keys=False))
            return original(context, repo)

        with patch.object(_EvaluationContext, "fresh_snapshot", new=drift_policy):
            result = check_conditions(
                self.fixture.root, submission_id=self.submission["submission_id"]
            )
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["reason"], "task-requirements-drift")

    def test_frozen_source_drift_during_evaluation_blocks_publication(self) -> None:
        original = _EvaluationContext.fresh_snapshot

        def drift_source(context, repo):
            source = repo / "protected.py"
            source.write_text("changed during final freshness\n")
            return original(context, repo)

        with patch.object(_EvaluationContext, "fresh_snapshot", new=drift_source):
            result = check_conditions(
                self.fixture.root, submission_id=self.submission["submission_id"]
            )
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["reason"], "risk-assessment-drift")
