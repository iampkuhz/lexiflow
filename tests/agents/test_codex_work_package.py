from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path

import yaml

from scripts.agents.codex.work_package import (
    CodexWorkPackageError, CodexWorkPackagePublisher, build_codex_main_task_projection,
)


class CodexWorkPackagePublisherTest(unittest.TestCase):
    def setUp(self) -> None:
        runtime_root = Path.cwd() / "tmp/quality/codex-work-packages"
        runtime_root.mkdir(parents=True, exist_ok=True)
        self.repo = Path(tempfile.mkdtemp(prefix="codex-publisher-", dir=runtime_root))
        (self.repo / "tmp/quality/codex-work-packages").mkdir(parents=True)
        self.run_id = str(uuid.uuid4())
        self.tasks = ["LF-TSK-QLT-9001", "LF-TSK-QLT-9002"]
        self._catalog()

    def tearDown(self) -> None:
        # Tests only create repository-local runtime fixtures.  Remove files one by one
        # to keep this cleanup independent of the publisher's immutability semantics.
        for path in sorted(self.repo.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        self.repo.rmdir()

    def _catalog(self) -> None:
        records = []
        for index, task_id in enumerate(self.tasks, 1):
            records.append({
                "id": task_id, "task_version": 2, "change_version": "1.0.0",
                "owner": "LF-WS-QLT", "deliverable": f"deliverable {index}",
                "acceptance_criteria": [f"criterion {index}"],
                "acceptance_evidence": [f"evidence {index}"],
                "validation_command": f"python3 -m unittest case{index}",
                "allowed_files": ["scripts/agents/codex/work_package.py"],
                "forbidden_files": ["secrets/**"],
            })
        path = self.repo / "planning/workstreams.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump({"workstreams": [{"epics": [{"capabilities": [{"seed_tasks": records}]}]}]}), encoding="utf-8")

    def _caller(self) -> dict:
        return {
            "goal": "publish fixture", "work_package_id": "LF-WP-QLT-PUBLISHER-TEST-001", "task_ids": self.tasks,
            "task_versions": {task: 2 for task in self.tasks}, "change_versions": {task: "1.0.0" for task in self.tasks},
            "estimated_minutes": 180, "primary_owner": "LF-WS-QLT", "contract_boundary": "fixture",
            "allowed_files": "scripts/agents/codex/work_package.py", "forbidden_files": "secrets/**",
            "required_context": "AGENTS.md", "expected_outputs_by_task": {task: f"deliverable {i}" for i, task in enumerate(self.tasks, 1)},
            "acceptance_by_task": {task: {"acceptance_criteria": [f"criterion {i}"], "acceptance_evidence": [f"evidence {i}"]} for i, task in enumerate(self.tasks, 1)},
            "validation_commands": {task: f"python3 -m unittest case{i}" for i, task in enumerate(self.tasks, 1)},
            "failure_policy": "record results", "parent_client": "codex",
        }

    def _runtime(self) -> dict:
        return {"parent_session_id": str(uuid.uuid4()), "agent_id": "codex_fixture", "run_id": self.run_id, "session_id": str(uuid.uuid4()), "client": "codex", "parent_client": "codex"}

    def _outcomes(self, statuses=("PASS", "PASS")) -> dict:
        folder = self.repo / "tmp/quality/test-artifacts"
        folder.mkdir(parents=True, exist_ok=True)
        values = {}
        for index, (task, status) in enumerate(zip(self.tasks, statuses), 1):
            path = folder / f"{task}.json"
            path.write_text(json.dumps({"task": task, "status": status}), encoding="utf-8")
            locator = path.relative_to(self.repo).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            values[task] = {"result_fields": {"status": status, "changed_files": [], "validation": {"status": status, "evidence_locator": locator}, "acceptance_evidence": [locator], "effect_checks": {"behavior": status}, "risks": ["fixture"]}, "artifacts": [{"locator": locator, "sha256": digest}], "validation_summary": {"command": f"python3 -m unittest case{index}", "exit_code": 0 if status == "PASS" else 1}}
        return values

    def test_publishes_exact_layout_and_verifies_hashes(self) -> None:
        completion = CodexWorkPackagePublisher(self.repo).publish(self._caller(), self._runtime(), self._outcomes())
        self.assertEqual(completion["status"], "PASS")
        base = self.repo / "tmp/quality/codex-work-packages" / self.run_id
        for task in self.tasks:
            for name in ("task-projection.json", "outcome.json", "completion.json", "signal.json"):
                self.assertTrue((base / "tasks" / task / name).is_file())
        verified = CodexWorkPackagePublisher(self.repo).verify(self.run_id)
        self.assertEqual(verified["status"], "PASS")

    def test_fail_dominates_blocked_and_collision_cannot_overwrite(self) -> None:
        publisher = CodexWorkPackagePublisher(self.repo)
        completion = publisher.publish(self._caller(), self._runtime(), self._outcomes(("BLOCKED", "FAIL")))
        self.assertEqual(completion["status"], "FAIL")
        with self.assertRaisesRegex(CodexWorkPackageError, "run-collision"):
            publisher.publish(self._caller(), self._runtime(), self._outcomes())

    def test_rejects_hash_drift_and_catalog_drift_before_publication(self) -> None:
        outcomes = self._outcomes()
        artifact = self.repo / outcomes[self.tasks[0]]["artifacts"][0]["locator"]
        artifact.write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(CodexWorkPackageError, "artifact-hash-mismatch"):
            CodexWorkPackagePublisher(self.repo).publish(self._caller(), self._runtime(), outcomes)
        self.assertFalse((self.repo / "tmp/quality/codex-work-packages" / self.run_id).exists())

    def test_rejects_missing_runtime_context_without_fabricating_identity(self) -> None:
        runtime = self._runtime()
        runtime.pop("session_id")
        with self.assertRaisesRegex(CodexWorkPackageError, "runtime-context-invalid"):
            CodexWorkPackagePublisher(self.repo).publish(self._caller(), runtime, self._outcomes())

    def test_verify_rechecks_external_artifacts_and_current_catalog(self) -> None:
        outcomes = self._outcomes()
        publisher = CodexWorkPackagePublisher(self.repo)
        publisher.publish(self._caller(), self._runtime(), outcomes)
        artifact = self.repo / outcomes[self.tasks[0]]["artifacts"][0]["locator"]
        original = artifact.read_bytes()
        artifact.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(CodexWorkPackageError, "artifact-hash-mismatch"):
            publisher.verify(self.run_id)
        artifact.write_bytes(original)
        catalog_path = self.repo / "planning/workstreams.yaml"
        catalog_path.write_text(catalog_path.read_text().replace("task_version: 2", "task_version: 3"))
        with self.assertRaisesRegex(CodexWorkPackageError, "catalog-drift"):
            publisher.verify(self.run_id)

    def test_verify_rejects_empty_artifact_map_and_relocated_artifact(self) -> None:
        publisher = CodexWorkPackagePublisher(self.repo)
        completion = publisher.publish(self._caller(), self._runtime(), self._outcomes())
        path = self.repo / "tmp/quality/codex-work-packages" / self.run_id / "package-completion.json"
        original = json.dumps(completion)
        completion["tasks"][self.tasks[0]]["artifacts"] = {}
        path.write_text(json.dumps(completion))
        with self.assertRaisesRegex(CodexWorkPackageError, "completion-invalid"):
            publisher.verify(self.run_id)
        completion = json.loads(original)
        descriptor = completion["tasks"][self.tasks[0]]["artifacts"]["outcome"]
        descriptor["locator"] = completion["tasks"][self.tasks[1]]["artifacts"]["outcome"]["locator"]
        path.write_text(json.dumps(completion))
        with self.assertRaisesRegex(CodexWorkPackageError, "completion-invalid"):
            publisher.verify(self.run_id)

    def test_ordered_task_list_does_not_depend_on_json_object_key_order(self) -> None:
        caller = self._caller()
        caller["task_ids"] = list(reversed(self.tasks))
        publisher = CodexWorkPackagePublisher(self.repo)
        publisher.publish(caller, self._runtime(), self._outcomes())
        self.assertEqual(publisher.verify(self.run_id)["task_ids"], caller["task_ids"])

    def test_scope_uses_dispatch_coverage_instead_of_substring_matching(self) -> None:
        caller = self._caller()
        caller["allowed_files"] = "scripts/agents/codex/work_package.py.extra"
        with self.assertRaisesRegex(CodexWorkPackageError, "catalog-drift"):
            CodexWorkPackagePublisher(self.repo).publish(caller, self._runtime(), self._outcomes())

    def test_main_projection_reads_one_current_catalog_task_without_subagent_package_fields(self) -> None:
        runtime = {**self._runtime(), "agent_id": "/root"}
        projection = build_codex_main_task_projection(
            self.repo, self.tasks[0], runtime, goal="Main fixture",
            required_context="current catalog", failure_policy="fail closed",
        )
        self.assertEqual(projection["task_id"], self.tasks[0])
        self.assertNotIn("task_ids", projection)
        self.assertNotIn("work_package_id", projection)
        catalog = self.repo / "planning/workstreams.yaml"
        catalog.write_text(catalog.read_text().replace("task_version: 2", "task_version: 3"))
        changed = build_codex_main_task_projection(
            self.repo, self.tasks[0], runtime, goal="Main fixture",
            required_context="current catalog", failure_policy="fail closed",
        )
        self.assertEqual(changed["task_version"], 3)

    def test_main_projection_rejects_child_runtime_actor(self) -> None:
        with self.assertRaisesRegex(CodexWorkPackageError, "canonical Main actor"):
            build_codex_main_task_projection(
                self.repo, self.tasks[0], {**self._runtime(), "agent_id": "/root/child"},
                goal="child cannot claim Main route", required_context="current catalog",
                failure_policy="fail closed",
            )

    def test_pass_rejects_unrun_validation_and_non_catalog_command(self) -> None:
        for mutation in ({"command": "arbitrary"}, {"exit_code": None}, {"exit_code": 1}):
            with self.subTest(mutation=mutation):
                outcomes = self._outcomes()
                outcomes[self.tasks[0]]["validation_summary"].update(mutation)
                with self.assertRaisesRegex(CodexWorkPackageError, "outcome-invalid"):
                    CodexWorkPackagePublisher(self.repo).publish(self._caller(), self._runtime(), outcomes)


if __name__ == "__main__":
    unittest.main()
