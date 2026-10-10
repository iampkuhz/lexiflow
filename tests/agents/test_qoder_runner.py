"""Synthetic checks for the Qoder runner's safety and non-polling boundary."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import yaml

from scripts.agents.qoder import runner as qoder_task
from scripts.agents.qoder import handoff, lifecycle


def valid_task() -> dict[str, Any]:
    return {
        "goal": "one bounded goal",
        "task_id": "LF-TEST-001",
        "task_source": "planning/workstreams.yaml",
        "task_version": 1,
        "change_version": "1.0.0",
        "work_package_id": "LF-WP-TEST-001",
        "task_ids": ["LF-TEST-001", "LF-TEST-002"],
        "task_versions": {"LF-TEST-001": 1, "LF-TEST-002": 1},
        "change_versions": {"LF-TEST-001": "1.0.0", "LF-TEST-002": "1.0.0"},
        "estimated_minutes": 240,
        "primary_owner": "LF-WS-QLT",
        "contract_boundary": "synthetic runner contract",
        "agent_profile": "quality-verifier",
        "harness_manifest": "tmp/qoder-harness.json",
        "allowed_files": "docs/example.md",
        "forbidden_files": "harness/**",
        "required_context": "AGENTS.md",
        "expected_output": "one file",
        "acceptance_criteria": [
            "the file exists",
            "tests pass",
            "scope holds",
            "result exists",
        ],
        "acceptance_evidence": ["test receipt", "scope receipt", "result receipt"],
        "validation_command": "python3 -V",
        "failure_policy": "report BLOCKED",
        "parent_client": "codex",
        "parent_session_id": "11111111-2222-4333-8444-555555555555",
    }


def valid_result(run_id: str) -> dict[str, Any]:
    task = valid_task()
    return {
        "schema_version": "lexiflow.qoder-work-package-result.v1",
        "work_package_id": task["work_package_id"],
        "task_ids": task["task_ids"],
        "task_versions": task["task_versions"],
        "change_versions": task["change_versions"],
        "run_id": run_id,
        "status": "PASS",
        "outcomes": [
            {"task_id": item, "status": "PASS", "acceptance_evidence": ["fixture"]}
            for item in task["task_ids"]
        ],
        "changed_files": [],
        "validation": [],
        "acceptance_evidence": ["fixture package evidence"],
        "effect_checks": [],
        "risks": [],
    }


def terminal_completion(run_id: str, status: str = "finished") -> dict[str, object]:
    return {
        "status": status,
        "exit_code": 0 if status in ("finished", "completed") else 1,
        "run_id": run_id,
        "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "task_id": "LF-TEST-001",
        "task_version": 1,
        "change_version": "1.0.0",
        "agent_id": "agent_test001",
        "client": "qoder",
        "parent_client": "codex",
        "parent_session_id": "11111111-2222-4333-8444-555555555555",
    }


def write_resumable_run(
    task_dir: Path, run_id: str = "00000000-0000-4000-8000-000000000001"
) -> Path:
    run_dir = task_dir / run_id
    run_dir.mkdir()
    task: dict[str, object] = valid_task()
    task.update(
        session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        agent_id="agent_test001",
        run_id=run_id,
        client="qoder",
        parent_session_id="11111111-2222-4333-8444-555555555555",
    )
    qoder_task._atomic_write_json(run_dir / "task.json", task)
    qoder_task._atomic_write_json(
        run_dir / "completion.json", terminal_completion(run_id, status="finished")
    )
    return run_dir


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class QoderRunnerContractTest(unittest.TestCase):
    def setUp(self):
        self.host_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.host_temp.cleanup)
        self.host_lock = Path(self.host_temp.name) / "host" / "qoder-cli.lock"
        patcher = patch.object(
            qoder_task, "_host_lock_path", return_value=self.host_lock
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("mark_qoder_started", "mark_qoder_unavailable"):
            patcher = patch.object(
                qoder_task._fallback, name, return_value={"code": "TERRA_REQUIRED"}
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_token_and_rework_limits_match_machine_contracts(self) -> None:
        root = Path(__file__).resolve().parents[2]
        policy = yaml.safe_load(
            (root / "harness/agent-policy.manifest.yaml").read_text()
        )
        contract = policy["qoder_delegation"]
        self.assertEqual(contract["max_rework_rounds"], 1)
        self.assertEqual(contract["max_total_runs_per_task_before_main_review"], 2)
        self.assertEqual(
            contract["max_prompt_characters"], qoder_task.MAX_PROMPT_CHARACTERS
        )
        self.assertNotIn("estimated_minutes", contract["work_package"])
        self.assertEqual(contract["work_package"]["minimum_catalog_tasks"], 1)
        expected_codex_model_policy = {
            "default_model": "gpt-6-luna",
            "default_reasoning_effort": "medium",
            "routine_model": "gpt-6-luna",
            "routine_reasoning_effort": "low",
            "applies_to": "codex-delegated-work-including-exception-task",
            "explicit_model_argument_required": True,
            "allowed_escalation_models": ["gpt-6-sol"],
            "user_only_models": ["gpt-6-astra"],
            "escalation_requires": [
                "stable_task_id",
                "parent_decision",
                "specific_failure_or_uncovered_risk_evidence",
            ],
            "escalation_reasons": [
                "luna_acceptance_blocked",
                "specific_uncovered_risk",
                "explicit_user_requirement",
            ],
            "qoder_model_policy": "separate-runner-controlled",
        }
        policy_model = dict(policy["subagent_protocol"]["codex_model_policy"])
        policy_model.pop("routine_routing")
        self.assertEqual(policy_model, expected_codex_model_policy)
        package = policy["subagent_protocol"]["delegation_work_package"]
        self.assertEqual(package["identity_fields"], ["work_package_id", "task_ids"])
        self.assertTrue(package["per_task_outcome_evidence_required"])
        self.assertEqual(package["preferred_minutes"], {"min": 10, "max": 360})
        self.assertNotIn("minimum_total_estimated_minutes", package)
        self.assertNotIn("target_maximum_total_estimated_minutes", package)
        self.assertEqual(package["minimum_catalog_tasks"], 1)
        completion_signal = policy["subagent_protocol"]["completion_signal"]
        self.assertEqual(
            completion_signal["allowed_fields"],
            [
                "status",
                "work_package_id",
                "task_ids",
                "run_id",
                "artifact_locators",
                "validation_commands",
                "blocking_findings",
            ],
        )
        self.assertEqual(completion_signal["max_blocking_findings"], 3)
        self.assertEqual(
            completion_signal["full_context_or_logs_in_callback"], "forbidden"
        )

        self.assertEqual(
            policy["subagent_protocol"]["caller_required_input_applies_to"],
            "qoder-task-handoff",
        )

    def test_routine_model_routing_preserves_risk_and_role_boundaries(self) -> None:
        root = Path(__file__).resolve().parents[2]
        policy = yaml.safe_load(
            (root / "harness/agent-policy.manifest.yaml").read_text()
        )
        routing = policy["subagent_protocol"]["codex_model_policy"]["routine_routing"]
        self.assertEqual(routing["decision_owner"], "parent-agent")
        self.assertEqual(routing["classification_scope"], "whole-work-package")
        self.assertTrue(routing["eligible_routine_must_use_routine_model"])
        self.assertTrue(routing["role_does_not_pin_model"])
        self.assertTrue(routing["prompt_text_does_not_select_model"])
        self.assertTrue(routing["frequency_or_agent_name_alone_is_not_eligibility"])
        self.assertEqual(
            set(routing["required_conditions"]),
            {
                "frozen-contract-and-acceptance",
                "bounded-coherent-work-package",
                "deterministic-direct-validation",
            },
        )
        self.assertEqual(routing["default_dispatch"], "luna-first")
        self.assertNotIn("terra_required_for", routing)
        self.assertTrue(routing["category_alone_does_not_escalate"])
        self.assertEqual(
            routing["complex_mixed_or_unclassified"],
            "use-default-model-with-default-reasoning",
        )
        self.assertEqual(
            routing["deterministic_single_command_or_small_fix"],
            "main-agent-no-dispatch",
        )
        self.assertEqual(
            routing["luna_blocker_policy"],
            "stop-and-compact-callback-parent-checks-failure-layer-before-escalation",
        )
        config = tomllib.loads((root / ".codex/config.toml").read_text())
        self.assertEqual(config["agents"]["default_subagent_model"], "gpt-6-luna")
        self.assertEqual(
            config["agents"]["default_subagent_reasoning_effort"], "medium"
        )
        native_limit = policy["codex_dispatch"]["max_active_subagents"]
        self.assertGreater(native_limit, 1)
        self.assertEqual(
            config["agents"]["max_concurrent_threads_per_session"], native_limit
        )
        self.assertEqual(policy["qoder_delegation"]["max_active_runs"], 1)
        self.assertEqual(
            policy["qoder_delegation"]["concurrency_scope"],
            "host-os-user-all-checkouts",
        )
        self.assertEqual(
            policy["qoder_delegation"]["scheduling"]["integration_validation"], "serial"
        )
        self.assertNotIn("model", config)
        entry = root / ".codex" / config["model_instructions_file"]
        self.assertIn("harness/agent-policy.manifest.yaml", entry.read_text())
        self.assertNotRegex(entry.read_text(), r"gpt-\d+(?:\.\d+)?-")
        profiles = list((root / ".codex/agents").glob("*.toml"))
        self.assertGreaterEqual(len(profiles), 5)
        for profile in profiles:
            with self.subTest(profile=profile.name):
                role = tomllib.loads(profile.read_text())
                self.assertNotIn("model", role)

    def test_agent_execution_spec_matches_parallelism_and_acceptance_policy(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        policy = yaml.safe_load(
            (root / "harness/agent-policy.manifest.yaml").read_text()
        )
        spec = (root / "openspec/specs/agent-execution/spec.md").read_text()
        codex_limit = policy["codex_dispatch"]["max_active_subagents"]
        qoder_limit = policy["qoder_delegation"]["max_active_runs"]
        self.assertGreater(codex_limit, 1)
        self.assertEqual(qoder_limit, 1)
        self.assertIn(
            "Codex 原生子代理同时最多 MUST 遵守 `harness/agent-policy.manifest.yaml` 的并发上限",
            spec,
        )
        self.assertIn("Qoder 仍受宿主 OS 用户范围的单运行约束", spec)
        self.assertIn("集成验证 SHALL 串行", spec)
        self.assertIn("实现者 MAY 执行静态、编译和直接测试作为自检", spec)
        self.assertIn("MUST NOT 签发 Formal validation 或 independent review", spec)
        self.assertNotIn("Codex Sub-Agent 同时 MUST 最多一个", spec)
        self.assertNotIn("不少于 120 分钟", spec)

    def test_required_handoff_rejects_missing_field(self) -> None:
        self.assertEqual(
            tuple(valid_task())[: len(handoff.REQUIRED_HANDOFF)],
            handoff.REQUIRED_HANDOFF,
        )
        for field in handoff.REQUIRED_HANDOFF:
            with self.subTest(field=field):
                task = valid_task()
                del task[field]
                with self.assertRaisesRegex(ValueError, field):
                    qoder_task._validate_task(task)

    def test_versioned_acceptance_fields_and_runner_identity_are_enforced(self) -> None:
        task = valid_task()
        task["permission_mode"] = "dont_ask"
        with self.assertRaisesRegex(ValueError, "permission_mode is runner-owned"):
            qoder_task._validate_task(task)

        task = valid_task()
        task["task_version"] = 0
        with self.assertRaisesRegex(ValueError, "task_version"):
            qoder_task._validate_task(task)

        task = valid_task()
        task["change_version"] = "latest"
        with self.assertRaisesRegex(ValueError, "change_version"):
            qoder_task._validate_task(task)

        task = valid_task()
        task["agent_id"] = "caller_owned"
        with self.assertRaisesRegex(ValueError, "runner-bound.*agent_id"):
            qoder_task._validate_task(task)

        task = valid_task()
        task["acceptance_criteria"] = "not a list"
        with self.assertRaisesRegex(ValueError, "acceptance_criteria.*list"):
            qoder_task._validate_task(task)

        task = valid_task()
        task["parent_client"] = "other"
        with self.assertRaisesRegex(ValueError, "parent_client must be codex"):
            qoder_task._validate_task(task)

    def test_prompt_contains_versions_acceptance_and_bound_run_identity(self) -> None:
        task = valid_task()
        task.update(
            agent_id="agent_test001",
            run_id="00000000-0000-4000-8000-000000000001",
            session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            client="qoder",
        )
        qoder_task._validate_task(task, runtime_bound=True)
        prompt = qoder_task._build_prompt(task)
        self.assertIn("Task version: 1", prompt)
        self.assertIn("Change version: 1.0.0", prompt)
        self.assertIn("Acceptance criteria:\n- the file exists", prompt)
        self.assertIn("Acceptance evidence:\n- test receipt", prompt)
        self.assertIn("Work package id: LF-WP-TEST-001", prompt)
        self.assertIn(
            "Required result fields: schema_version, status, work_package_id", prompt
        )
        self.assertIn("acceptance_evidence, effect_checks, risks", prompt)
        self.assertIn("outcomes MUST be an array in task_ids order", prompt)
        self.assertIn("result.template.json", prompt)
        self.assertIn("replace its BLOCKED placeholders with factual results", prompt)
        self.assertIn("validate-result 00000000-0000-4000-8000-000000000001", prompt)
        self.assertIn("Run id: 00000000-0000-4000-8000-000000000001", prompt)

    def test_qoder_cli_is_bound_to_project_agent_profile(self) -> None:
        task = valid_task()
        task.update(
            agent_id="agent_test001",
            run_id="00000000-0000-4000-8000-000000000001",
            session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            client="qoder",
        )
        with patch.object(
            qoder_task, "_find_qoder_cli", return_value=Path("/bin/qodercli")
        ):
            args = qoder_task._build_qodercli_args(task, Path("/repo"))
        self.assertEqual(args[args.index("--agent") + 1], "quality-verifier")
        self.assertEqual(
            args[args.index("--setting-sources") + 1], "user,project,local"
        )
        self.assertNotIn(
            "--model", args
        )  # Preserve the user's configured provider/model.
        policy = yaml.safe_load(
            (
                Path(__file__).resolve().parents[2]
                / "harness/agent-policy.manifest.yaml"
            ).read_text()
        )
        self.assertEqual(
            args[args.index("--permission-mode") + 1],
            policy["qoder_delegation"]["default_permission_mode"],
        )
        self.assertEqual(args[args.index("--disallowed-tools") + 1], "Agent")

    def test_host_lease_blocks_a_different_checkout(self) -> None:
        with tempfile.TemporaryDirectory():
            with qoder_task._host_lease():
                with self.assertRaisesRegex(ValueError, "host-wide Qoder lease"):
                    with qoder_task._host_lease():
                        self.fail("second checkout acquired the lease")
            with qoder_task._host_lease():
                pass

    def test_cli_inherits_host_lease_after_dispatcher_and_worker_exit(self) -> None:
        # Only Python fixture processes. No Qoder CLI/model call or timed polling.
        worker = """
import subprocess,sys
fd=int(sys.argv[1])
child=subprocess.Popen([sys.executable,'-c',"print('CLI_READY',flush=True); input()"],
                       stdin=sys.stdin,stdout=sys.stdout,pass_fds=(fd,))
print('WORKER_EXIT',flush=True)
"""
        with qoder_task._host_lease() as fd:
            proc = subprocess.Popen(
                [sys.executable, "-c", worker, str(fd)],
                pass_fds=(fd,),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        try:
            self.assertEqual(
                proc.wait(timeout=5), 0
            )  # dispatcher/worker both released their copies
            with self.assertRaisesRegex(ValueError, "host-wide Qoder lease"):
                with qoder_task._host_lease():
                    self.fail("inherited lease unexpectedly released")
        finally:
            output, error = proc.communicate(input="release\n", timeout=5)
        self.assertIn("CLI_READY", output, error)
        self.assertIn("WORKER_EXIT", output)
        with qoder_task._host_lease():
            pass

    def test_host_lease_rejects_symlinks(self) -> None:
        with qoder_task._host_lease():
            pass
        self.host_lock.unlink()
        self.host_lock.parent.mkdir(exist_ok=True)
        target = self.host_lock.with_name("target")
        target.write_text("")
        self.host_lock.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            with qoder_task._host_lease():
                pass

    def test_pending_status_is_one_read_only_snapshot_while_result_refuses_polling(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            run_id = "00000000-0000-4000-8000-000000000001"
            run = root / "tmp/qoder-tasks" / run_id
            run.mkdir(parents=True)
            task = valid_task()
            task.update(
                session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                agent_id="agent_test001",
                run_id=run_id,
                client="qoder",
            )
            qoder_task._atomic_write_json(run / "task.json", task)
            qoder_task._atomic_write_json(
                run / "continuation.json",
                {
                    "run_id": run_id,
                    "state": "AWAITING_CALLBACK",
                    "next_action": "end-current-turn-await-callback",
                },
            )
            before = {p.name: p.read_bytes() for p in run.iterdir()}
            with (
                patch.object(Path, "cwd", return_value=root),
                patch("builtins.print") as output,
            ):
                qoder_task.cmd_status(argparse.Namespace(run_id=run_id))
            snapshot = json.loads(output.call_args.args[0])
            self.assertEqual(snapshot["status"], "unknown")
            self.assertEqual(
                snapshot["continuation"]["next_action"],
                "end-current-turn-await-callback",
            )
            with (
                patch.object(Path, "cwd", return_value=root),
                patch("builtins.print") as output,
            ):
                with self.assertRaises(SystemExit) as stopped:
                    qoder_task.cmd_result(argparse.Namespace(run_id=run_id))
            self.assertEqual(stopped.exception.code, 3)
            self.assertEqual(
                json.loads(output.call_args.args[0])["next_action"],
                "end-current-turn-await-callback",
            )
            self.assertEqual(before, {p.name: p.read_bytes() for p in run.iterdir()})

    def test_shared_policy_requires_end_turn_without_llm_fallback(self) -> None:
        root = Path(qoder_task.__file__).resolve().parents[3]
        policy = yaml.safe_load(
            (root / "harness/agent-policy.manifest.yaml").read_text()
        )
        lifecycle_policy = policy["qoder_delegation"]["lifecycle"]
        self.assertEqual(
            lifecycle_policy["parent_after_dispatch"], "end-current-turn-await-callback"
        )
        self.assertEqual(lifecycle_policy["llm_fallback_probes"], "forbidden")
        self.assertNotIn("llm_fallback_first_probe_after_seconds", lifecycle_policy)
        self.assertNotIn(
            "llm_fallback_min_subsequent_probe_interval_seconds", lifecycle_policy
        )

    def test_validate_result_is_read_only_and_does_not_promote_task_status(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            run_id = "00000000-0000-4000-8000-000000000001"
            run = root / "tmp/qoder-tasks" / run_id
            run.mkdir(parents=True)
            task = valid_task()
            task["run_id"] = run_id
            result = valid_result(run_id)
            result["status"] = "BLOCKED"
            result["outcomes"][0]["status"] = "BLOCKED"
            (run / "task.json").write_text(json.dumps(task))
            (run / "result.json").write_text(json.dumps(result))
            before = {p.name: p.read_bytes() for p in run.iterdir()}
            with (
                patch.object(Path, "cwd", return_value=root),
                patch("builtins.print") as output,
            ):
                self.assertEqual(qoder_task.main(["validate-result", run_id]), 0)
            summary = json.loads(output.call_args.args[0])
            self.assertEqual(summary["result_status"], "BLOCKED")
            self.assertEqual(
                summary["scope"], "result-structure-only-not-task-acceptance"
            )
            self.assertEqual(before, {p.name: p.read_bytes() for p in run.iterdir()})
            result["outcomes"] = {x["task_id"]: x for x in result["outcomes"]}
            (run / "result.json").write_text(json.dumps(result))
            with patch.object(Path, "cwd", return_value=root), patch("builtins.print"):
                self.assertEqual(qoder_task.main(["validate-result", run_id]), 1)
            self.assertFalse((run / "completion.json").exists())

    def test_prompt_budget_rejects_repeated_design_payload(self) -> None:
        task = valid_task()
        task["expected_output"] = "x" * qoder_task.MAX_PROMPT_CHARACTERS
        with self.assertRaisesRegex(ValueError, "prompt exceeds"):
            qoder_task._validate_prompt_budget(task)

    def test_persisted_runtime_task_is_accepted_only_in_runtime_mode(self) -> None:
        task = valid_task()
        task.update(
            agent_id="agent_test001",
            run_id="00000000-0000-4000-8000-000000000001",
            session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            client="qoder",
        )
        with self.assertRaisesRegex(ValueError, "runner-bound"):
            qoder_task._validate_task(task)
        self.assertIs(qoder_task._validate_task(task, runtime_bound=True), task)

        task_without_parent = dict(task)
        del task_without_parent["parent_session_id"]
        with self.assertRaisesRegex(ValueError, "parent_session_id"):
            qoder_task._validate_task(task_without_parent, runtime_bound=True)

    def test_worker_accepts_persisted_runtime_task_and_writes_verifiable_completion(
        self,
    ) -> None:
        run_id = "00000000-0000-4000-8000-000000000001"
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            run_dir = task_dir / run_id
            run_dir.mkdir()
            task = valid_task()
            task.update(
                agent_id="agent_test001",
                run_id=run_id,
                session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                client="qoder",
            )
            qoder_task._atomic_write_json(run_dir / "task.json", task)
            qoder_task._atomic_write_json(run_dir / "result.json", valid_result(run_id))
            process = MagicMock(pid=12345)
            process.wait.return_value = 0
            callback_observations: list[str] = []

            def observe_callback(*_args: object) -> None:
                persisted = json.loads((run_dir / "completion.json").read_text())
                callback_observations.append(str(persisted["run_id"]))

            with (
                patch.object(
                    qoder_task, "_build_qodercli_args", return_value=["qodercli"]
                ),
                patch.object(
                    qoder_task.subprocess, "Popen", return_value=process
                ) as spawn,
                patch.object(lifecycle, "record_started"),
                patch.object(
                    qoder_task, "_attempt_codex_callback", side_effect=observe_callback
                ),
            ):
                qoder_task._worker_entry(task_dir, run_id, Path(directory))
            self.assertEqual(len(spawn.call_args.kwargs["pass_fds"]), 1)

            completion = json.loads((run_dir / "completion.json").read_text())
            self.assertEqual(completion["status"], "finished")
            self.assertEqual(completion["task_version"], 1)
            self.assertEqual(completion["change_version"], "1.0.0")
            self.assertEqual(completion["run_id"], run_id)
            self.assertEqual(callback_observations, [run_id])
            lifecycle._verify_completion_identity(completion, task, run_id)

    def test_worker_exit_zero_without_structured_result_is_failed(self) -> None:
        run_id = "00000000-0000-4000-8000-000000000001"
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            run_dir = task_dir / run_id
            run_dir.mkdir()
            task = valid_task()
            task.update(
                agent_id="agent_test001",
                run_id=run_id,
                session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                client="qoder",
            )
            qoder_task._atomic_write_json(run_dir / "task.json", task)
            process = MagicMock(pid=12345)
            process.wait.return_value = 0
            with (
                patch.object(
                    qoder_task, "_build_qodercli_args", return_value=["qodercli"]
                ),
                patch.object(qoder_task.subprocess, "Popen", return_value=process),
                patch.object(lifecycle, "record_started"),
                patch.object(qoder_task, "_attempt_codex_callback"),
            ):
                qoder_task._worker_entry(task_dir, run_id, Path(directory))
            completion = json.loads((run_dir / "completion.json").read_text())
            self.assertEqual(completion["status"], "failed")
            self.assertEqual(completion["qoder_exit_code"], 0)
            self.assertIn("result", completion["result_error"])
            self.assertGreaterEqual(completion["duration_seconds"], 0)

    def test_cli_disables_hidden_model_request_retries(self) -> None:
        with patch.object(
            qoder_task, "_find_qoder_cli", return_value=Path("/bin/qodercli")
        ):
            args = qoder_task._build_qodercli_args(valid_task(), Path("."))
        self.assertEqual(args[args.index("--max-model-request-retries") + 1], "0")

    def test_worker_persists_failure_before_compact_callback(self) -> None:
        run_id = "00000000-0000-4000-8000-000000000001"
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            run_dir = write_resumable_run(task_dir, run_id)
            process = MagicMock(pid=12345)

            def finish():
                (run_dir / "stdout.log").write_text(
                    json.dumps(
                        {
                            "type": "result",
                            "error_code": 118,
                            "subtype": "error_during_execution",
                            "errors": ["SECRET_PAYLOAD"],
                        }
                    )
                )
                return 1

            process.wait.side_effect = finish
            observed = []

            def callback(*_args):
                c = json.loads((run_dir / "completion.json").read_text())
                observed.append(c["failure"]["category"])
                message = qoder_task._build_callback_message(run_id, c, run_dir)
                self.assertNotIn("SECRET_PAYLOAD", message)
                self.assertIn("result_error_code=118", message)

            with (
                patch.object(
                    qoder_task, "_build_qodercli_args", return_value=["qodercli"]
                ),
                patch.object(qoder_task.subprocess, "Popen", return_value=process),
                patch.object(lifecycle, "record_started"),
                patch.object(
                    qoder_task, "_attempt_codex_callback", side_effect=callback
                ),
            ):
                qoder_task._worker_entry(task_dir, run_id, Path(directory))
            self.assertEqual(observed, ["quota"])
            self.assertFalse((run_dir / "result.json").exists())

    def test_watchdog_first_check_300_then_every_600_seconds(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            run_dir.mkdir()
            checks = 0

            def check(*_args: object) -> str:
                nonlocal checks
                checks += 1
                return "acknowledged" if checks == 3 else "continue"

            with patch.object(lifecycle, "watchdog_check", side_effect=check):
                result = lifecycle.watchdog_loop(
                    run_dir,
                    Path(directory),
                    send_callback=MagicMock(),
                    sleep_fn=clock.sleep,
                    clock_fn=clock.time,
                )

        self.assertEqual(result, "acknowledged")
        self.assertEqual(clock.sleeps, [300, 600, 600])
        self.assertEqual(checks, 3)

    def test_completion_callback_has_only_fixed_locator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            completion = {
                "task_id": "LF-TEST-001",
                "status": "finished",
                "exit_code": 0,
                "stdout_tail": "untrusted secret-like payload",
            }
            message = qoder_task._build_callback_message(
                "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", completion, run_dir
            )
        self.assertIn("LF-TEST-001", message)
        self.assertNotIn("untrusted secret-like payload", message)
        self.assertIn("queued 不算任务通过", message)

    def test_fallback_callback_uses_shared_model_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            message = qoder_task._build_callback_message(
                "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                {
                    "task_id": "LF-TEST-001",
                    "status": "finished",
                    "exit_code": 0,
                    "fallback": {
                        "code": "TERRA_REQUIRED",
                        "model": "untrusted-model-value",
                    },
                },
                Path(directory),
            )
        self.assertIn("fallback_model / fallback_reasoning_effort", message)
        self.assertNotIn("Terra/high", message)
        self.assertNotIn("untrusted-model-value", message)

    def test_task_and_completion_storage_remain_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = valid_task()
            qoder_task._atomic_write_json(root / "task.json", task)
            qoder_task._atomic_write_json(
                root / "completion.json", {"status": "finished", "exit_code": 0}
            )
            self.assertEqual(
                json.loads((root / "task.json").read_text())["task_id"], "LF-TEST-001"
            )
            self.assertNotIn("PASS", (root / "completion.json").read_text())

    def test_completion_identity_pins_task_change_and_run_versions(self) -> None:
        run_id = "00000000-0000-4000-8000-000000000001"
        task = valid_task()
        task.update(
            agent_id="agent_test001",
            run_id=run_id,
            session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            client="qoder",
        )
        completion = terminal_completion(run_id)
        lifecycle._verify_completion_identity(completion, task, run_id)
        del completion["change_version"]
        with self.assertRaisesRegex(ValueError, "change_version"):
            lifecycle._verify_completion_identity(completion, task, run_id)


if __name__ == "__main__":
    unittest.main()
