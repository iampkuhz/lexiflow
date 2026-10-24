"""校验精简调用输入、完整冻结工作包及不可变来源链。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.agents.work_packages.codex_work_package_publisher import (
    CodexWorkPackageError,
    CodexWorkPackagePublisher,
)
from scripts.agents.identity.codex_actor_identity import bind_work_package


class CodexWorkPackagePublisherTest(unittest.TestCase):
    def setUp(self) -> None:
        runtime_root = Path.cwd() / "tmp/quality/codex-work-packages"
        runtime_root.mkdir(parents=True, exist_ok=True)
        self.repo = Path(tempfile.mkdtemp(prefix="codex-publisher-", dir=runtime_root))
        subprocess.run(["git", "init", str(self.repo)], check=True, capture_output=True)
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
                    "estimated_minutes": 30,
                    "change_version": "1.0.0",
                    "owner": "LF-WS-QLT",
                    "deliverable": f"deliverable {index}",
                    "acceptance_criteria": [f"criterion {index}"],
                    "acceptance_evidence": [f"evidence {index}"],
                    "validation_command": f"python3 -m unittest case{index}",
                    "allowed_files": ["scripts/agents/work_packages/codex_work_package_publisher.py"],
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
            "contract_boundary": "fixture",
            "additional_forbidden_files": [],
            "required_context": "AGENTS.md",
            "failure_policy": "record results",
        }

    def _bind(self, caller=None):
        bound = bind_work_package(self.repo, caller or self._caller())
        self.run_id = bound["run_id"]
        return self.run_id

    def _publish(self, statuses=("PASS", "PASS"), caller=None):
        run = self._bind(caller)
        outcomes = self._outcomes(statuses)
        outcomes = {
            task: outcomes[task] for task in (caller or self._caller())["task_ids"]
        }
        return CodexWorkPackagePublisher(self.repo).publish(run, outcomes)

    def _base(self):
        return self.repo / "tmp/quality/codex-work-packages" / self.run_id

    def _rewrite(self, path, value=None, content=None):
        content = content or json.dumps(value).encode()
        path.write_bytes(content)
        return {
            "locator": path.relative_to(self.repo).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }

    def _mutate_parent(self, mutate):
        base = self._base()
        path = base / "frozen-caller.json"
        value = json.loads(path.read_bytes())
        mutate(value)
        descriptor = self._rewrite(path, value)
        completion = json.loads((base / "package-completion.json").read_bytes())
        completion["frozen_caller_descriptor"] = descriptor
        for task in self.tasks:
            path = base / "tasks" / task / "task-projection.json"
            value = json.loads(path.read_bytes())
            value["frozen_caller_descriptor"] = descriptor
            completion["tasks"][task]["task_projection"] = self._rewrite(path, value)
        self._rewrite(base / "package-completion.json", completion)

    def _mutate_projection(self, mutate):
        base = self._base()
        task = self.tasks[0]
        path = base / "tasks" / task / "task-projection.json"
        value = json.loads(path.read_bytes())
        mutate(value)
        completion = json.loads((base / "package-completion.json").read_bytes())
        completion["tasks"][task]["task_projection"] = self._rewrite(path, value)
        self._rewrite(base / "package-completion.json", completion)

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

    def test_layout_and_full_frozen_facts_derive_only_in_memory(self):
        view = self._publish()
        base = self._base()
        self.assertEqual(view["producer_view"]["status"], "PASS")
        frozen = json.loads((base / "frozen-caller.json").read_bytes())
        self.assertEqual(frozen["estimated_minutes"], 60)
        self.assertEqual(frozen["primary_owner"], "LF-WS-QLT")
        self.assertEqual(frozen["task_source"], "planning/workstreams.yaml")
        self.assertEqual(frozen["task_ids"], self.tasks)
        self.assertEqual(len(frozen), 19)
        completion = json.loads((base / "package-completion.json").read_bytes())
        self.assertEqual(
            set(completion),
            {
                "schema_version",
                "frozen_caller_descriptor",
                "runtime_binding_descriptor",
                "tasks",
            },
        )
        for task in self.tasks:
            directory = base / "tasks" / task
            self.assertEqual(
                {p.name for p in directory.iterdir()},
                {"task-projection.json", "outcome.json"},
            )
            projection = json.loads((directory / "task-projection.json").read_bytes())
            self.assertEqual(
                set(projection),
                {
                    "schema_version",
                    "target_task_id",
                    "frozen_caller_descriptor",
                    "runtime_binding_descriptor",
                },
            )
            self.assertEqual(
                projection["frozen_caller_descriptor"],
                completion["frozen_caller_descriptor"],
            )
            derived = view["producer_view"]["tasks"][task]["projection"]
            self.assertEqual(derived["caller_contract"], frozen)
            self.assertEqual(
                derived["acceptance_criteria"],
                frozen["acceptance_by_task"][task]["acceptance_criteria"],
            )
            self.assertEqual(
                derived["validation_command"], frozen["validation_commands"][task]
            )
        self.assertEqual(CodexWorkPackagePublisher(self.repo).verify(self.run_id), view)

    def test_rejects_all_removed_caller_inputs_and_runtime_injection(self):
        for field in (
            "allowed_files",
            "forbidden_files",
            "estimated_minutes",
            "primary_owner",
            "expected_outputs_by_task",
            "acceptance_by_task",
            "validation_commands",
            "parent_client",
            "parent_session_id",
            "agent_id",
            "run_id",
            "session_id",
            "client",
        ):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(
                    CodexWorkPackageError, "caller-contract-invalid"
                ),
            ):
                bind_work_package(self.repo, {**self._caller(), field: "forged"})

    def test_current_catalog_never_fills_missing_frozen_facts(self):
        self._publish()
        original = json.loads((self._base() / "frozen-caller.json").read_bytes())
        for field in original:
            with self.subTest(field=field):
                self._mutate_parent(lambda value: value.pop(field))
                with self.assertRaisesRegex(
                    CodexWorkPackageError, "caller-contract-invalid"
                ):
                    CodexWorkPackagePublisher(self.repo).verify(self.run_id)
                self._mutate_parent(lambda value: value.update(original))

    def test_maps_acceptance_source_and_scope_cannot_be_forged(self):
        self._publish()
        original = json.loads((self._base() / "frozen-caller.json").read_bytes())
        mutations = [
            lambda value: value["acceptance_by_task"][self.tasks[0]].pop(
                "acceptance_evidence"
            ),
            lambda value: value["task_versions"].pop(self.tasks[1]),
            lambda value: value.update(task_source="planning/forged.yaml"),
            lambda value: value.update(allowed_files="scripts/**"),
            lambda value: value.update(estimated_minutes=90),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self._mutate_parent(mutate)
                with self.assertRaises(CodexWorkPackageError):
                    CodexWorkPackagePublisher(self.repo).verify(self.run_id)
                self._mutate_parent(lambda value: value.update(original))

    def test_catalog_drift_before_dispatch_and_after_completion_fails(self):
        for field, value in (("task_versions", 1), ("change_versions", "0.0.0")):
            caller = self._caller()
            caller[field][self.tasks[0]] = value
            with self.assertRaisesRegex(CodexWorkPackageError, "catalog-drift"):
                self._bind(caller)
        self._publish()
        path = self.repo / "planning/workstreams.yaml"
        original = path.read_text()
        for old, new in (
            ("deliverable 1", "new output"),
            ("criterion 1", "new criterion"),
            ("evidence 1", "new evidence"),
            ("LF-WS-QLT", "LF-WS-OTHER"),
            ("python3 -m unittest case1", "python3 -m unittest other"),
            ("task_version: 2", "task_version: 3"),
            ("estimated_minutes: 30", "estimated_minutes: 40"),
        ):
            with self.subTest(field=old):
                path.write_text(original.replace(old, new))
                with self.assertRaisesRegex(CodexWorkPackageError, "catalog-drift"):
                    CodexWorkPackagePublisher(self.repo).verify(self.run_id)
        path.write_text(original)

    def test_order_and_singleton_are_preserved_without_aliases(self):
        caller = self._caller()
        caller["task_ids"] = list(reversed(self.tasks))
        view = self._publish(caller=caller)
        self.assertEqual(view["task_ids"], caller["task_ids"])
        caller["task_ids"] = [self.tasks[0]]
        for field in ("task_versions", "change_versions"):
            caller[field] = {self.tasks[0]: caller[field][self.tasks[0]]}
        view = self._publish(caller=caller)
        self.assertEqual(view["task_ids"], caller["task_ids"])
        self.assertEqual(
            view["producer_view"]["caller_contract"]["estimated_minutes"], 30
        )

    def test_mixed_status_aggregation_and_immutable_collision(self):
        publisher = CodexWorkPackagePublisher(self.repo)
        for statuses, expected in (
            (("BLOCKED", "PASS"), "BLOCKED"),
            (("BLOCKED", "FAIL"), "FAIL"),
        ):
            view = self._publish(statuses)
            self.assertEqual(view["producer_view"]["status"], expected)
            with self.assertRaisesRegex(CodexWorkPackageError, "run-collision"):
                publisher.publish(self.run_id, self._outcomes())

    def test_additional_forbidden_is_frozen_and_actual_changed_files_are_checked(self):
        caller = self._caller()
        caller["additional_forbidden_files"] = ["scripts/agents/work_packages/codex_work_package_publisher.py"]
        self._publish(caller=caller)
        frozen = json.loads((self._base() / "frozen-caller.json").read_bytes())
        self.assertEqual(
            frozen["additional_forbidden_files"], caller["additional_forbidden_files"]
        )
        self.assertIn(
            caller["additional_forbidden_files"][0], frozen["forbidden_files"]
        )
        run = self._bind(caller)
        outcomes = self._outcomes()
        outcomes[self.tasks[0]]["result_fields"]["changed_files"] = [
            "scripts/agents/work_packages/codex_work_package_publisher.py"
        ]
        with self.assertRaisesRegex(CodexWorkPackageError, "outcome-scope-invalid"):
            CodexWorkPackagePublisher(self.repo).publish(run, outcomes)
        self.assertFalse((self._base() / "package-completion.json").exists())

    def test_per_task_scope_rejects_non_catalog_changed_file(self):
        run = self._bind()
        outcomes = self._outcomes()
        outcomes[self.tasks[0]]["result_fields"]["changed_files"] = [
            "scripts/agents/other.py"
        ]
        with self.assertRaisesRegex(CodexWorkPackageError, "outcome-scope-invalid"):
            CodexWorkPackagePublisher(self.repo).publish(run, outcomes)

    def test_frozen_extra_forbidden_is_enforced_during_historical_verification(self):
        caller = self._caller()
        caller["additional_forbidden_files"] = ["scripts/agents/work_packages/codex_work_package_publisher.py"]
        self._publish(caller=caller)
        path = self._base() / "tasks" / self.tasks[0] / "outcome.json"
        value = json.loads(path.read_bytes())
        value["result_fields"]["changed_files"] = [
            "scripts/agents/work_packages/codex_work_package_publisher.py"
        ]
        completion_path = self._base() / "package-completion.json"
        completion = json.loads(completion_path.read_bytes())
        completion["tasks"][self.tasks[0]]["outcome"] = self._rewrite(path, value)
        self._rewrite(completion_path, completion)
        with self.assertRaisesRegex(CodexWorkPackageError, "outcome-scope-invalid"):
            CodexWorkPackagePublisher(self.repo).verify(self.run_id)

    def test_owner_scope_and_exact_budget_are_derived_from_catalog(self):
        path = self.repo / "planning/workstreams.yaml"
        root = yaml.safe_load(path.read_text())
        ws = root["workstreams"][0]
        ws["id"] = "LF-WS-QLT"
        records = ws["epics"][0]["capabilities"][0]["seed_tasks"]
        for record in records:
            record.pop("owner")
        records[1]["allowed_files"] = ["scripts/agents/identity/codex_actor_identity.py"]
        path.write_text(yaml.safe_dump(root))
        view = self._publish()
        caller = view["producer_view"]["caller_contract"]
        self.assertEqual(
            caller["allowed_files"],
            "scripts/agents/identity/codex_actor_identity.py, scripts/agents/work_packages/codex_work_package_publisher.py",
        )
        records[0]["owner"] = "LF-WS-OTHER"
        path.write_text(yaml.safe_dump(root))
        with self.assertRaisesRegex(CodexWorkPackageError, "share primary_owner"):
            self._bind()
        records[0].pop("owner")
        records[0]["estimated_minutes"] = 360
        path.write_text(yaml.safe_dump(root))
        with self.assertRaisesRegex(CodexWorkPackageError, "budget"):
            self._bind()

    def test_projection_rejects_wrong_target_cross_run_parent_or_old_schema(self):
        self._publish()
        original = json.loads(
            (
                self._base() / "tasks" / self.tasks[0] / "task-projection.json"
            ).read_bytes()
        )
        for mutate in (
            lambda value: value.update(target_task_id=self.tasks[1]),
            lambda value: value["frozen_caller_descriptor"].update(
                locator="tmp/quality/another-run/frozen-caller.json"
            ),
            lambda value: value["runtime_binding_descriptor"].update(sha256="0" * 64),
            lambda value: value.update(
                schema_version="lexiflow.codex-work-package-task-projection.v1"
            ),
            lambda value: value.update(caller_contract={}),
        ):
            with self.subTest(mutate=mutate):
                self._mutate_projection(mutate)
                with self.assertRaises(CodexWorkPackageError):
                    CodexWorkPackagePublisher(self.repo).verify(self.run_id)
                self._mutate_projection(
                    lambda value: (value.clear(), value.update(original))
                )

    def test_completion_task_keyset_and_canonical_locator_are_exact(self):
        self._publish()
        path = self._base() / "package-completion.json"
        original = json.loads(path.read_bytes())
        for mutate in (
            lambda value: value["tasks"].pop(self.tasks[0]),
            lambda value: value["tasks"][self.tasks[0]].update(signal={}),
            lambda value: value["tasks"][self.tasks[0]]["outcome"].update(
                locator=value["tasks"][self.tasks[1]]["outcome"]["locator"]
            ),
            lambda value: value.update(status="PASS"),
        ):
            value = json.loads(json.dumps(original))
            mutate(value)
            self._rewrite(path, value)
            with self.assertRaisesRegex(CodexWorkPackageError, "completion-invalid"):
                CodexWorkPackagePublisher(self.repo).verify(self.run_id)
        self._rewrite(path, original)

    def test_raw_whitespace_is_hash_bound_and_duplicate_keys_are_rejected(self):
        self._publish()
        base = self._base()
        path = base / "frozen-caller.json"
        value = json.loads(path.read_bytes())
        content = json.dumps(value, indent=4).encode()
        descriptor = self._rewrite(path, content=content)
        completion_path = base / "package-completion.json"
        completion = json.loads(completion_path.read_bytes())
        completion["frozen_caller_descriptor"] = descriptor
        for task in self.tasks:
            projection_path = base / "tasks" / task / "task-projection.json"
            projection = json.loads(projection_path.read_bytes())
            projection["frozen_caller_descriptor"] = descriptor
            completion["tasks"][task]["task_projection"] = self._rewrite(
                projection_path, projection
            )
        self._rewrite(completion_path, completion)
        self.assertEqual(
            CodexWorkPackagePublisher(self.repo).verify(self.run_id)["status"], "PASS"
        )
        duplicate = content[:-1] + b',"goal":"duplicate"}'
        self._rewrite(path, content=duplicate)
        with self.assertRaisesRegex(CodexWorkPackageError, "duplicate-json-key"):
            CodexWorkPackagePublisher(self.repo).verify(self.run_id)

    def test_external_raw_hash_and_missing_binding_are_rechecked(self):
        view = self._publish()
        artifact = (
            self.repo
            / view["producer_view"]["tasks"][self.tasks[0]]["outcome"]["artifacts"][0][
                "locator"
            ]
        )
        original = artifact.read_bytes()
        artifact.write_text("changed")
        with self.assertRaisesRegex(CodexWorkPackageError, "artifact-hash-mismatch"):
            CodexWorkPackagePublisher(self.repo).verify(self.run_id)
        artifact.write_bytes(original)
        (self._base() / "runtime-binding.json").unlink()
        with self.assertRaises(CodexWorkPackageError) as caught:
            CodexWorkPackagePublisher(self.repo).verify(self.run_id)
        self.assertEqual(caught.exception.status, "BLOCKED")

    def test_current_main_cannot_bind_or_publish_child_run(self):
        from scripts.agents.identity.codex_identity_binding import CodexRuntimeError

        run = self._bind()
        with patch.dict(os.environ, {"CODEX_THREAD_ID": self.parent_id}):
            with self.assertRaisesRegex(CodexRuntimeError, "runtime-identity-drift"):
                self._bind()
            with self.assertRaisesRegex(
                CodexWorkPackageError, "runtime-identity-drift"
            ):
                CodexWorkPackagePublisher(self.repo).publish(run, self._outcomes())

    def test_forged_binding_shape_or_changed_native_metadata_never_authenticates(self):
        run = self._bind()
        path = self._base() / "runtime-binding.json"
        value = json.loads(path.read_bytes())
        value["identity"]["agent_id"] = "codex-thread-" + str(uuid.uuid4())
        self._rewrite(path, value)
        with self.assertRaisesRegex(CodexWorkPackageError, "runtime-identity-drift"):
            CodexWorkPackagePublisher(self.repo).publish(run, self._outcomes())
        self._publish()
        path = self.home / "sessions" / f"rollout-{self.parent_id}.jsonl"
        path.write_text(path.read_text().replace("Codex Desktop", "Changed"))
        with self.assertRaisesRegex(CodexWorkPackageError, "runtime-proof-drift"):
            CodexWorkPackagePublisher(self.repo).verify(self.run_id)

    def test_partial_publication_cannot_be_resumed(self):
        run = self._bind()
        (self._base() / "tasks").mkdir()
        with self.assertRaisesRegex(CodexWorkPackageError, "run-collision"):
            CodexWorkPackagePublisher(self.repo).publish(run, self._outcomes())

    def test_pass_requires_real_validation_and_exact_catalog_command(self):
        run = self._bind()
        for mutation in (
            {"command": "arbitrary"},
            {"exit_code": None},
            {"exit_code": 1},
        ):
            outcomes = self._outcomes()
            outcomes[self.tasks[0]]["validation_summary"].update(mutation)
            with self.assertRaisesRegex(CodexWorkPackageError, "outcome-invalid"):
                CodexWorkPackagePublisher(self.repo).publish(run, outcomes)
        self.assertFalse((self._base() / "package-completion.json").exists())

    def test_path_symlink_is_rejected_and_cli_only_prints_compact_result(self):
        import contextlib
        import io
        from scripts.agents.work_packages.verify_codex_work_package import main

        self._publish()
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main(["verify", "--run-id", self.run_id], root=self.repo)
        result = json.loads(stream.getvalue())
        self.assertEqual(code, 0)
        self.assertNotIn("producer_view", result)
        path = self._base() / "tasks" / self.tasks[0] / "task-projection.json"
        original = path.read_bytes()
        other = self._base() / "other.json"
        other.write_bytes(original)
        path.unlink()
        path.symlink_to(other)
        with self.assertRaisesRegex(CodexWorkPackageError, "unsafe-locator"):
            CodexWorkPackagePublisher(self.repo).verify(self.run_id)

    def test_incompatible_catalog_scopes_fail_before_binding(self):
        path = self.repo / "planning/workstreams.yaml"
        value = yaml.safe_load(path.read_bytes())
        records = value["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"]
        records[1]["forbidden_files"] += ["scripts/agents/work_packages/codex_work_package_publisher.py"]
        path.write_text(yaml.safe_dump(value))
        with self.assertRaisesRegex(CodexWorkPackageError, "write scopes conflict"):
            self._bind()

    def test_missing_runtime_blocks_before_publishing(self):
        run = self._bind()
        (self._base() / "runtime-binding.json").unlink()
        with self.assertRaises(CodexWorkPackageError) as caught:
            CodexWorkPackagePublisher(self.repo).publish(run, self._outcomes())
        self.assertEqual(caught.exception.status, "BLOCKED")
        self.assertFalse((self._base() / "package-completion.json").exists())

    def test_malformed_or_skip_results_never_pass_by_exit_zero(self):
        run = self._bind()
        mutations = [
            lambda fields: fields.update(status="skipped"),
            lambda fields: fields.update(validation={}),
            lambda fields: fields.update(
                validation={"status": "PASS", "evidence_locator": []}
            ),
            lambda fields: fields.update(effect_checks={}),
            lambda fields: fields.update(effect_checks={"behavior": "not-run"}),
            lambda fields: fields.update(acceptance_evidence=["unbound.json"]),
            lambda fields: fields.update(status=[]),
        ]
        for mutate in mutations:
            outcomes = self._outcomes()
            mutate(outcomes[self.tasks[0]]["result_fields"])
            with self.assertRaisesRegex(CodexWorkPackageError, "outcome-invalid"):
                CodexWorkPackagePublisher(self.repo).publish(run, outcomes)
        self.assertFalse((self._base() / "package-completion.json").exists())

    def test_completion_published_last_and_no_partial_success_callback(self):
        import scripts.agents.work_packages.codex_work_package_publisher as module

        run = self._bind()
        write = module._write_exclusive
        published = []

        def tracked(path, content):
            published.append(path.name)
            if path.name != "package-completion.json":
                self.assertFalse((self._base() / "package-completion.json").exists())
            return write(path, content)

        with patch.object(module, "_write_exclusive", side_effect=tracked):
            result = CodexWorkPackagePublisher(self.repo).publish(run, self._outcomes())
        self.assertEqual(published[-1], "package-completion.json")
        self.assertEqual(result["status"], "PASS")
        self.assertNotIn("signal.json", published)

    def test_final_publication_rechecks_native_and_external_sources(self):
        import scripts.agents.work_packages.codex_work_package_publisher as module

        run = self._bind()
        outcomes = self._outcomes()
        write = module._write_exclusive
        artifact = self.repo / outcomes[self.tasks[0]]["artifacts"][0]["locator"]

        def tracked(path, content):
            write(path, content)
            if path.name == "outcome.json":
                artifact.write_text("changed during publication")

        with patch.object(module, "_write_exclusive", side_effect=tracked):
            with self.assertRaisesRegex(
                CodexWorkPackageError, "artifact-hash-mismatch"
            ):
                CodexWorkPackagePublisher(self.repo).publish(run, outcomes)
        self.assertFalse((self._base() / "package-completion.json").exists())

    def test_completion_duplicate_keys_and_parent_raw_hash_drift_fail(self):
        self._publish()
        path = self._base() / "package-completion.json"
        content = path.read_bytes()
        path.write_bytes(content[:-1] + b',"tasks":{}}')
        with self.assertRaisesRegex(CodexWorkPackageError, "duplicate-json-key"):
            CodexWorkPackagePublisher(self.repo).verify(self.run_id)
        path.write_bytes(content)
        parent = self._base() / "frozen-caller.json"
        parent.write_bytes(parent.read_bytes() + b"\n")
        with self.assertRaisesRegex(CodexWorkPackageError, "artifact-hash-mismatch"):
            CodexWorkPackagePublisher(self.repo).verify(self.run_id)

    def test_verification_detects_external_drift_after_first_task_read(self):
        import scripts.agents.work_packages.codex_work_package_publisher as module

        view = self._publish()
        original = module._outcome
        artifact = (
            self.repo
            / view["producer_view"]["tasks"][self.tasks[0]]["outcome"]["artifacts"][0][
                "locator"
            ]
        )

        def checked(task, *args):
            result = original(task, *args)
            if task == self.tasks[1]:
                artifact.write_text("changed after first task verification")
            return result

        with patch.object(module, "_outcome", side_effect=checked):
            with self.assertRaisesRegex(
                CodexWorkPackageError, "artifact-hash-mismatch"
            ):
                CodexWorkPackagePublisher(self.repo).verify(self.run_id)


if __name__ == "__main__":
    unittest.main()
