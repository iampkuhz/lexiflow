from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.agents.codex.work_package import (
    CodexWorkPackageError,
    CodexWorkPackagePublisher,
    build_codex_main_task_projection,
)
from scripts.agents.local_codex_runtime import discover


class CodexWorkPackagePublisherTest(unittest.TestCase):
    def setUp(self) -> None:
        runtime_root = Path.cwd() / "tmp/quality/codex-work-packages"
        runtime_root.mkdir(parents=True, exist_ok=True)
        self.repo = Path(tempfile.mkdtemp(prefix="codex-publisher-", dir=runtime_root))
        (self.repo / "tmp/quality/codex-work-packages").mkdir(parents=True)
        self.run_id = str(uuid.uuid4())
        self.parent_id, self.child_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.home = self.repo / ".codex"
        sessions = self.home / "sessions"
        sessions.mkdir(parents=True)
        for metadata in (
            {
                "id": self.parent_id,
                "session_id": self.parent_id,
                "cwd": str(self.repo),
                "source": "vscode",
                "thread_source": "user",
                "originator": "Codex Desktop",
            },
            {
                "id": self.child_id,
                "session_id": self.parent_id,
                "cwd": str(self.repo),
                "thread_source": "subagent",
                "originator": "Codex Desktop",
                "parent_thread_id": self.parent_id,
                "agent_path": "/root/worker",
                "source": {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": self.parent_id,
                            "agent_path": "/root/worker",
                            "depth": 1,
                        }
                    }
                },
            },
        ):
            path = sessions / f"rollout-{metadata['id']}.jsonl"
            path.write_text(
                json.dumps({"type": "session_meta", "payload": metadata}) + "\n"
            )
            path.chmod(0o600)
        self.env = patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(self.home),
                "CODEX_THREAD_ID": self.child_id,
                "CODEX_SESSION_ID": self.parent_id,
            },
        )
        self.env.start()
        self.runtime_identity = None
        self.tasks = ["LF-TSK-QLT-9001", "LF-TSK-QLT-9002"]
        self._catalog()

    def tearDown(self) -> None:
        self.env.stop()
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
            records.append(
                {
                    "id": task_id,
                    "task_version": 2,
                    "change_version": "1.0.0",
                    "owner": "LF-WS-QLT",
                    "deliverable": f"deliverable {index}",
                    "acceptance_criteria": [f"criterion {index}"],
                    "acceptance_evidence": [f"evidence {index}"],
                    "validation_command": f"python3 -m unittest case{index}",
                    "allowed_files": ["scripts/agents/codex/work_package.py"],
                    "forbidden_files": ["secrets/**"],
                }
            )
        path = self.repo / "planning/workstreams.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(
            yaml.safe_dump(
                {
                    "workstreams": [
                        {"epics": [{"capabilities": [{"seed_tasks": records}]}]}
                    ]
                }
            ),
            encoding="utf-8",
        )

    def _caller(self) -> dict:
        return {
            "goal": "publish fixture",
            "work_package_id": "LF-WP-QLT-PUBLISHER-TEST-001",
            "task_ids": self.tasks,
            "task_versions": {task: 2 for task in self.tasks},
            "change_versions": {task: "1.0.0" for task in self.tasks},
            "estimated_minutes": 180,
            "primary_owner": "LF-WS-QLT",
            "contract_boundary": "fixture",
            "allowed_files": "scripts/agents/codex/work_package.py",
            "forbidden_files": "secrets/**",
            "required_context": "AGENTS.md",
            "expected_outputs_by_task": {
                task: f"deliverable {i}" for i, task in enumerate(self.tasks, 1)
            },
            "acceptance_by_task": {
                task: {
                    "acceptance_criteria": [f"criterion {i}"],
                    "acceptance_evidence": [f"evidence {i}"],
                }
                for i, task in enumerate(self.tasks, 1)
            },
            "validation_commands": {
                task: f"python3 -m unittest case{i}"
                for i, task in enumerate(self.tasks, 1)
            },
            "failure_policy": "record results",
            "parent_client": "codex",
        }

    def _runtime(self) -> dict:
        if self.runtime_identity is None:
            runtime = discover(self.repo)
            binding = runtime.bind({"parent_client": "codex"}, self.run_id)
            binding.persist_immutable(self.repo)
            self.runtime_identity = dict(binding.identity)
        return dict(self.runtime_identity)

    def _outcomes(self, statuses=("PASS", "PASS")) -> dict:
        folder = self.repo / "tmp/quality/test-artifacts"
        folder.mkdir(parents=True, exist_ok=True)
        values = {}
        for index, (task, status) in enumerate(zip(self.tasks, statuses), 1):
            path = folder / f"{task}.json"
            path.write_text(
                json.dumps({"task": task, "status": status}), encoding="utf-8"
            )
            locator = path.relative_to(self.repo).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            values[task] = {
                "result_fields": {
                    "status": status,
                    "changed_files": [],
                    "validation": {"status": status, "evidence_locator": locator},
                    "acceptance_evidence": [locator],
                    "effect_checks": {"behavior": status},
                    "risks": ["fixture"],
                },
                "artifacts": [{"locator": locator, "sha256": digest}],
                "validation_summary": {
                    "command": f"python3 -m unittest case{index}",
                    "exit_code": 0 if status == "PASS" else 1,
                },
            }
        return values

    def test_publishes_exact_layout_and_verifies_hashes(self) -> None:
        completion = CodexWorkPackagePublisher(self.repo).publish(
            self._caller(), self._runtime(), self._outcomes()
        )
        self.assertEqual(completion["status"], "PASS")
        base = self.repo / "tmp/quality/codex-work-packages" / self.run_id
        for task in self.tasks:
            for name in (
                "task-projection.json",
                "outcome.json",
                "completion.json",
                "signal.json",
            ):
                self.assertTrue((base / "tasks" / task / name).is_file())
        verified = CodexWorkPackagePublisher(self.repo).verify(self.run_id)
        self.assertEqual(verified["status"], "PASS")

    def test_fail_dominates_blocked_and_collision_cannot_overwrite(self) -> None:
        publisher = CodexWorkPackagePublisher(self.repo)
        completion = publisher.publish(
            self._caller(), self._runtime(), self._outcomes(("BLOCKED", "FAIL"))
        )
        self.assertEqual(completion["status"], "FAIL")
        with self.assertRaisesRegex(CodexWorkPackageError, "run-collision"):
            publisher.publish(self._caller(), self._runtime(), self._outcomes())

    def test_rejects_hash_drift_and_catalog_drift_before_publication(self) -> None:
        outcomes = self._outcomes()
        artifact = self.repo / outcomes[self.tasks[0]]["artifacts"][0]["locator"]
        artifact.write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(CodexWorkPackageError, "artifact-hash-mismatch"):
            CodexWorkPackagePublisher(self.repo).publish(
                self._caller(), self._runtime(), outcomes
            )
        self.assertFalse(
            (
                self.repo
                / "tmp/quality/codex-work-packages"
                / self.run_id
                / "package-completion.json"
            ).exists()
        )

    def test_rejects_missing_runtime_context_without_fabricating_identity(self) -> None:
        runtime = self._runtime()
        runtime.pop("session_id")
        with self.assertRaisesRegex(CodexWorkPackageError, "runtime-context-invalid"):
            CodexWorkPackagePublisher(self.repo).publish(
                self._caller(), runtime, self._outcomes()
            )

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
        catalog_path.write_text(
            catalog_path.read_text().replace("task_version: 2", "task_version: 3")
        )
        with self.assertRaisesRegex(CodexWorkPackageError, "catalog-drift"):
            publisher.verify(self.run_id)

    def test_verify_rejects_empty_artifact_map_and_relocated_artifact(self) -> None:
        publisher = CodexWorkPackagePublisher(self.repo)
        completion = publisher.publish(
            self._caller(), self._runtime(), self._outcomes()
        )
        path = (
            self.repo
            / "tmp/quality/codex-work-packages"
            / self.run_id
            / "package-completion.json"
        )
        original = json.dumps(completion)
        completion["tasks"][self.tasks[0]]["artifacts"] = {}
        path.write_text(json.dumps(completion))
        with self.assertRaisesRegex(CodexWorkPackageError, "completion-invalid"):
            publisher.verify(self.run_id)
        completion = json.loads(original)
        descriptor = completion["tasks"][self.tasks[0]]["artifacts"]["outcome"]
        descriptor["locator"] = completion["tasks"][self.tasks[1]]["artifacts"][
            "outcome"
        ]["locator"]
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
            CodexWorkPackagePublisher(self.repo).publish(
                caller, self._runtime(), self._outcomes()
            )

    def test_main_projection_reads_one_current_catalog_task_without_subagent_package_fields(
        self,
    ) -> None:
        runtime = {**self._runtime(), "agent_id": "/root"}
        projection = build_codex_main_task_projection(
            self.repo,
            self.tasks[0],
            runtime,
            goal="Main fixture",
            required_context="current catalog",
            failure_policy="fail closed",
        )
        self.assertEqual(projection["task_id"], self.tasks[0])
        self.assertNotIn("task_ids", projection)
        self.assertNotIn("work_package_id", projection)
        catalog = self.repo / "planning/workstreams.yaml"
        catalog.write_text(
            catalog.read_text().replace("task_version: 2", "task_version: 3")
        )
        changed = build_codex_main_task_projection(
            self.repo,
            self.tasks[0],
            runtime,
            goal="Main fixture",
            required_context="current catalog",
            failure_policy="fail closed",
        )
        self.assertEqual(changed["task_version"], 3)

    def test_main_projection_rejects_child_runtime_actor(self) -> None:
        with self.assertRaisesRegex(CodexWorkPackageError, "canonical Main actor"):
            build_codex_main_task_projection(
                self.repo,
                self.tasks[0],
                {**self._runtime(), "agent_id": "/root/child"},
                goal="child cannot claim Main route",
                required_context="current catalog",
                failure_policy="fail closed",
            )

    def test_single_task_package_dispatches_and_publishes_projection(self) -> None:
        task = self.tasks[0]
        caller = self._caller()
        caller["task_ids"] = [task]
        for field in (
            "task_versions",
            "change_versions",
            "expected_outputs_by_task",
            "acceptance_by_task",
            "validation_commands",
        ):
            caller[field] = {task: caller[field][task]}
        caller["estimated_minutes"] = 30
        outcomes = self._outcomes()
        outcomes = {task: outcomes[task]}
        completion = CodexWorkPackagePublisher(self.repo).publish(
            caller, self._runtime(), outcomes
        )
        self.assertEqual(completion["status"], "PASS")
        projection = (
            self.repo
            / "tmp/quality/codex-work-packages"
            / self.run_id
            / "tasks"
            / task
            / "task-projection.json"
        )
        raw = json.loads(projection.read_text(encoding="utf-8"))
        self.assertEqual(raw["task_ids"], [task])
        self.assertEqual(raw["target_task_id"], task)
        self.assertEqual(
            CodexWorkPackagePublisher(self.repo).verify(self.run_id)["status"], "PASS"
        )

    def test_main_actor_cannot_dispatch_singleton_package_or_inject_identity(
        self,
    ) -> None:
        task = self.tasks[0]
        caller = self._caller()
        caller["task_ids"] = [task]
        for field in (
            "task_versions",
            "change_versions",
            "expected_outputs_by_task",
            "acceptance_by_task",
            "validation_commands",
        ):
            caller[field] = {task: caller[field][task]}
        caller["estimated_minutes"] = 30
        with self.assertRaisesRegex(CodexWorkPackageError, "runtime-identity-drift"):
            CodexWorkPackagePublisher(self.repo).publish(
                caller,
                {**self._runtime(), "agent_id": "/root"},
                {task: self._outcomes()[task]},
            )
        caller["agent_id"] = "codex-session-forged"
        with self.assertRaisesRegex(
            CodexWorkPackageError, "caller owns no runner identity"
        ):
            CodexWorkPackagePublisher(self.repo).publish(
                caller, self._runtime(), {task: self._outcomes()[task]}
            )

    def test_forged_worker_name_cannot_publish_with_native_binding(self) -> None:
        runtime = {**self._runtime(), "agent_id": "worker-1"}
        with self.assertRaisesRegex(CodexWorkPackageError, "runtime-identity-drift"):
            CodexWorkPackagePublisher(self.repo).publish(
                self._caller(), runtime, self._outcomes()
            )

    def test_missing_binding_blocks_publication_and_historical_verification(
        self,
    ) -> None:
        runtime = self._runtime()
        binding = (
            self.repo
            / "tmp/quality/codex-work-packages"
            / self.run_id
            / "runtime-binding.json"
        )
        binding.unlink()
        publisher = CodexWorkPackagePublisher(self.repo)
        with self.assertRaises(CodexWorkPackageError) as caught:
            publisher.publish(self._caller(), runtime, self._outcomes())
        self.assertEqual(caught.exception.status, "BLOCKED")

        # A publication made while the source existed must not remain valid
        # after its native provenance is removed.
        discover(self.repo).bind(
            {"parent_client": "codex"}, self.run_id
        ).persist_immutable(self.repo)
        publisher.publish(self._caller(), runtime, self._outcomes())
        binding.unlink()
        with self.assertRaises(CodexWorkPackageError) as caught:
            publisher.verify(self.run_id)
        self.assertEqual(caught.exception.status, "BLOCKED")

    def test_tampered_native_proof_invalidates_historical_completion(self) -> None:
        runtime = self._runtime()
        publisher = CodexWorkPackagePublisher(self.repo)
        publisher.publish(self._caller(), runtime, self._outcomes())
        binding = (
            self.repo
            / "tmp/quality/codex-work-packages"
            / self.run_id
            / "runtime-binding.json"
        )
        value = json.loads(binding.read_text())
        value["proof"]["thread_id"] = self.parent_id
        binding.write_text(json.dumps(value))
        with self.assertRaisesRegex(CodexWorkPackageError, "runtime-proof-drift"):
            publisher.verify(self.run_id)

    def test_current_main_cannot_publish_using_child_identity(self) -> None:
        runtime = self._runtime()
        with (
            patch.dict(os.environ, {"CODEX_THREAD_ID": self.parent_id}),
            self.assertRaisesRegex(CodexWorkPackageError, "runtime-identity-drift"),
        ):
            CodexWorkPackagePublisher(self.repo).publish(
                self._caller(), runtime, self._outcomes()
            )

    def test_pass_rejects_unrun_validation_and_non_catalog_command(self) -> None:
        for mutation in (
            {"command": "arbitrary"},
            {"exit_code": None},
            {"exit_code": 1},
        ):
            with self.subTest(mutation=mutation):
                outcomes = self._outcomes()
                outcomes[self.tasks[0]]["validation_summary"].update(mutation)
                with self.assertRaisesRegex(CodexWorkPackageError, "outcome-invalid"):
                    CodexWorkPackagePublisher(self.repo).publish(
                        self._caller(), self._runtime(), outcomes
                    )


if __name__ == "__main__":
    unittest.main()
