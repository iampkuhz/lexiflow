"""同次 Delivery Gate 求值的 DAG 复用与 fail-closed 行为。"""

from __future__ import annotations

import unittest
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import yaml

from scripts.delivery_gate.check import (
    _dependency_status,
    _EvaluationContext,
    check_conditions,
)
from scripts.delivery_gate.records import (
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
                    "dependencies": [{"task_id": dep} for dep in dependencies],
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

    def _run_diamond(self, *, use_context: bool):
        nodes, records = self._diamond()
        reads = {
            "submissions": 0,
            "layer_scans": 0,
            "requirements": 0,
            "records": 0,
            "evidence": 0,
            "bound": 0,
        }
        visited: set[str] = set()
        indexed_contexts: set[int] = set()

        def requirements(_repo: Path, task_id: str):
            reads["requirements"] += 1
            return nodes[task_id]["task_requirements"]

        def get_records(_repo: Path, node, context=None):
            reads["records"] += 1
            visited.add(node["submission_id"])
            if context is None:
                reads["layer_scans"] += 3
            elif id(context) not in indexed_contexts:
                indexed_contexts.add(id(context))
                reads["layer_scans"] += 3
            return records[node["submission_id"]]

        def submissions(_repo: Path):
            reads["submissions"] += 1
            return list(nodes.values())

        def evidence(*_args):
            reads["evidence"] += 1
            return []

        def bound(*_args):
            reads["bound"] += 1
            return []

        context = (
            _EvaluationContext(submissions=list(nodes.values()))
            if use_context
            else None
        )
        if use_context:
            reads["submissions"] += 1
            reads["layer_scans"] += 3
            indexed_contexts.add(id(context))
        started = perf_counter()
        with (
            patch(
                "scripts.delivery_gate.check.load_task_requirements",
                side_effect=requirements,
            ),
            patch(
                "scripts.delivery_gate.check.list_submissions",
                side_effect=submissions,
            ),
            patch("scripts.delivery_gate.check._records", side_effect=get_records),
            patch(
                "scripts.delivery_gate.check._existing_evidence_errors",
                side_effect=evidence,
            ),
            patch("scripts.delivery_gate.check._bound_chain", side_effect=bound),
            patch("scripts.delivery_gate.check.review_required", return_value=False),
        ):
            result = _dependency_status(Path("/unused"), nodes["D"], context=context)
            # Both paths include a final no-cache-independent snapshot and source recheck.
            if use_context:
                reads["submissions"] += 1
                reads["layer_scans"] += 3
            fresh_context = (
                _EvaluationContext(submissions=list(nodes.values()))
                if use_context
                else None
            )
            if use_context:
                indexed_contexts.add(id(fresh_context))
            fresh = _dependency_status(
                Path("/unused"), nodes["D"], context=fresh_context
            )
            for sid in sorted(visited):
                node = next(
                    item for item in nodes.values() if item["submission_id"] == sid
                )
                validation, review, check = get_records(
                    Path("/unused"), node, fresh_context
                )
                evidence(Path("/unused"), node, validation, review, check)
                if check and check.get("result") == "PASS":
                    bound(Path("/unused"), node, validation, review)
            elapsed = perf_counter() - started
        self.assertEqual(fresh, result)
        return result, reads, elapsed

    def test_shared_diamond_reuses_receipt_and_hash_chain_once_per_evaluation(
        self,
    ) -> None:
        baseline, before, before_seconds = self._run_diamond(use_context=False)
        optimized, after, after_seconds = self._run_diamond(use_context=True)
        self.assertEqual(optimized, baseline)
        self.assertTrue(optimized[0])
        self.assertEqual(before["requirements"], 10)
        self.assertEqual(after["requirements"], 8)
        self.assertEqual(before["records"], 11)
        self.assertEqual(after["records"], 9)
        self.assertEqual(before["evidence"], 11)
        self.assertEqual(after["evidence"], 9)
        self.assertEqual(before["bound"], 11)
        self.assertEqual(after["bound"], 9)
        self.assertEqual(before["submissions"], 8)
        self.assertEqual(after["submissions"], 2)
        self.assertEqual(before["layer_scans"], 33)
        self.assertEqual(after["layer_scans"], 6)
        self.assertGreater(before_seconds, 0)
        self.assertGreater(after_seconds, 0)

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
            task["version"] += 1
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
