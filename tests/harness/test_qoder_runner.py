"""Synthetic checks for the Qoder runner's safety and non-polling boundary."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import yaml

from scripts.harness import qoder_task
from scripts.harness import qoder_task_lifecycle as lifecycle


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
        "acceptance_criteria": ["the file exists", "tests pass", "scope holds", "result exists"],
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
    def _write_catalog(self, root: Path, task: dict[str, Any]) -> None:
        anchor = {
            "id": task["task_ids"][0],
            "task_version": task["task_versions"][task["task_ids"][0]],
            "change_version": task["change_versions"][task["task_ids"][0]],
            "estimated_task_minutes": 120,
            "deliverable": task["expected_output"],
            "acceptance_criteria": task["acceptance_criteria"],
            "acceptance_evidence": task["acceptance_evidence"],
            "validation_command": task["validation_command"],
            "allowed_files": ["docs/example.md"],
            "forbidden_files": ["harness/**"],
            "file_claims": [{"path": "docs/example.md", "mode": "write", "owner": task["primary_owner"]}],
        }
        partner = {
            "id": task["task_ids"][1],
            "task_version": task["task_versions"][task["task_ids"][1]],
            "change_version": task["change_versions"][task["task_ids"][1]],
            "estimated_task_minutes": 120,
            "deliverable": "partner output",
            "acceptance_criteria": ["partner criterion"],
            "acceptance_evidence": ["partner evidence"],
            "validation_command": "python3 -V",
            "allowed_files": ["docs/example.md"],
            "forbidden_files": ["harness/**"],
            "file_claims": [{"path": "docs/example.md", "mode": "write", "owner": task["primary_owner"]}],
        }
        catalog = {
            "workstreams": [{
                "id": task["primary_owner"],
                "epics": [{"capabilities": [{"seed_tasks": [anchor, partner]}]}],
            }],
        }
        path = root / "planning/workstreams.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(catalog), encoding="utf-8")

    def _write_harness_manifest(self, root: Path, task: dict[str, Any]) -> Path:
        if not (root / "planning/workstreams.yaml").exists():
            self._write_catalog(root, task)
        files = {
            "AGENTS.md": "root rules\n",
            ".qoder/AGENTS.md": "qoder rules\n",
            ".qoder/agents/quality-verifier.md": "profile rules\n",
            "planning/workstreams.yaml": "schema_version: test\n",
            "harness/agent-policy.manifest.yaml": "schema_version: test\n",
            "harness/agent-runtime.manifest.yaml": "schema_version: test\n",
            "docs/context.md": "bounded context\n",
        }
        context = []
        for locator, content in files.items():
            path = root / locator
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(content, encoding="utf-8")
            context.append({"path": locator, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        manifest = {
            "schema_version": "lexiflow.qoder-harness.v1",
            "identity": {
                "work_package_id": task["work_package_id"],
                "task_ids": task["task_ids"],
                "task_versions": task["task_versions"],
                "change_versions": task["change_versions"],
                "agent_profile": task["agent_profile"],
            },
            "required_context": context,
            "required_tools": [{
                "name": "python",
                "probe_argv": ["python3", "--version"],
                "expected_output_regex": "Python 3",
                "timeout_seconds": 10,
            }],
            "validation_commands": [{"argv": ["python3", "-V"], "cwd": ".", "timeout_seconds": 30}],
        }
        target = root / task["harness_manifest"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest), encoding="utf-8")
        return target

    def test_token_and_rework_limits_match_machine_contracts(self) -> None:
        root = Path(__file__).resolve().parents[2]
        policy = yaml.safe_load((root / "harness/agent-policy.manifest.yaml").read_text())
        runtime = yaml.safe_load((root / "harness/agent-runtime.manifest.yaml").read_text())
        workstreams = yaml.safe_load((root / "planning/workstreams.yaml").read_text())
        template = yaml.safe_load((root / "planning/task-template.yaml").read_text())

        qoder_contracts = (
            policy["qoder_delegation"],
            workstreams["orchestration_policy"]["qoder"],
            template["execution"]["qoder"],
        )
        for contract in qoder_contracts:
            self.assertEqual(contract["max_rework_rounds"], 1)
            self.assertEqual(contract["max_total_runs_per_task_before_main_review"], 2)
            self.assertEqual(contract["max_prompt_characters"], qoder_task.MAX_PROMPT_CHARACTERS)
        self.assertEqual(policy["qoder_delegation"]["work_package"]["estimated_minutes"], {"min": 180, "max": 360})
        self.assertEqual(policy["qoder_delegation"]["work_package"]["minimum_catalog_tasks"], 2)
        self.assertEqual(workstreams["orchestration_policy"]["qoder"]["work_package_estimated_minutes"], {"min": 180, "max": 360})
        self.assertEqual(template["execution"]["qoder"]["work_package_estimated_minutes"], {"min": 180, "max": 360})
        self.assertEqual(
            policy["subagent_protocol"]["caller_required_input"],
            runtime["subagent_protocol"]["caller_required_input"],
        )
        expected_codex_model_policy = {
            "default_model": "gpt-5.6-luna",
            "default_reasoning_effort": "medium",
            "routine_model": "gpt-5.6-luna",
            "routine_reasoning_effort": "low",
            "applies_to": "codex-sub-agent",
            "explicit_model_argument_required": True,
            "allowed_escalation_models": ["gpt-5.6-terra", "gpt-5.6-sol"],
            "user_only_models": ["gpt-6-astra"],
            "escalation_requires": [
                "stable_task_id",
                "parent_decision",
                "specific_failure_or_uncovered_risk_evidence",
            ],
            "escalation_reasons": [
                "luna_acceptance_blocked",
                "specific_uncovered_risk",
                "terra_insufficient",
                "explicit_user_requirement",
            ],
            "sol_escalation_requirement": "terra_insufficiency_or_predeclared_high_risk_reason",
            "qoder_model_policy": "separate-runner-controlled",
        }
        policy_model = dict(policy["subagent_protocol"]["codex_model_policy"])
        policy_model.pop("routine_routing")
        self.assertEqual(policy_model, expected_codex_model_policy)
        routing_source = "harness/agent-policy.manifest.yaml#subagent_protocol.codex_model_policy"
        self.assertEqual(runtime["subagent_protocol"]["codex_model_policy"], {
            **expected_codex_model_policy, "routing_source": routing_source,
        })

        codex_contracts = (
            workstreams["orchestration_policy"]["codex_subagents"],
            template["execution"]["codex_subagent"],
        )
        policy_package = policy["subagent_protocol"]["delegation_work_package"]
        completion_signal = policy["subagent_protocol"]["completion_signal"]
        self.assertEqual(completion_signal["allowed_fields_applies_to"], "qoder-task-signal")
        self.assertEqual(completion_signal["allowed_fields"], completion_signal["qoder_task_allowed_fields"])
        self.assertEqual(policy["subagent_protocol"]["caller_required_input_applies_to"], "qoder-task-handoff")
        self.assertEqual(
            runtime["subagent_protocol"]["codex_work_package_caller_required_input"],
            policy["subagent_protocol"]["codex_work_package_caller_required_input"],
        )
        self.assertEqual(
            runtime["subagent_protocol"]["codex_work_package_runner_identity"],
            policy["subagent_protocol"]["codex_work_package_runner_identity"],
        )
        for contract in codex_contracts:
            self.assertEqual(contract["model_policy"]["default_model"], "gpt-5.6-luna")
            self.assertEqual(contract["model_policy"]["routine_model"], "gpt-5.6-luna")
            self.assertEqual(contract["model_policy"]["routine_reasoning_effort"], "low")
            self.assertEqual(contract["model_policy"]["routing_source"], routing_source)
            self.assertTrue(contract["model_policy"]["explicit_model_argument_required"])
            self.assertEqual(contract["model_policy"]["allowed_escalation_models"], ["gpt-5.6-terra", "gpt-5.6-sol"])
            self.assertEqual(contract["model_policy"]["user_only_models"], ["gpt-6-astra"])
            self.assertEqual(contract["max_active_subagents"], 1)
            self.assertEqual(contract["default_fork_turns"], "none")
            self.assertEqual(
                contract["completion_signal_fields"],
                policy["subagent_protocol"]["completion_signal"]["codex_work_package_allowed_fields"],
            )
            self.assertEqual(contract["max_blocking_findings"], 3)
            self.assertFalse(contract["full_context_or_logs_in_callback"])
            package = contract["delegation_work_package"]
            self.assertEqual(package, policy_package)
            self.assertEqual(package["identity_fields"], ["work_package_id", "task_ids"])
            self.assertTrue(package["per_task_outcome_evidence_required"])
            self.assertEqual(package["preferred_minutes"], {"min": 120, "max": 360})
            self.assertEqual(package["minimum_total_estimated_minutes"], 120)
            self.assertEqual(package["target_maximum_total_estimated_minutes"], 360)
            self.assertGreaterEqual(package["minimum_catalog_tasks"], 2)

        runtime_package = runtime["subagent_protocol"]["delegation_work_package"]
        self.assertEqual(runtime_package["source"], "harness/agent-policy.manifest.yaml")
        self.assertEqual(runtime_package["identity_fields"], policy_package["identity_fields"])
        self.assertEqual(runtime_package["preferred_minutes"], policy_package["preferred_minutes"])
        self.assertEqual(
            runtime_package["minimum_total_estimated_minutes"],
            policy_package["minimum_total_estimated_minutes"],
        )
        self.assertEqual(
            runtime_package["target_maximum_total_estimated_minutes"],
            policy_package["target_maximum_total_estimated_minutes"],
        )
        self.assertEqual(runtime_package["minimum_catalog_tasks"], policy_package["minimum_catalog_tasks"])
        self.assertEqual(
            runtime_package["per_task_outcome_evidence_required"],
            policy_package["per_task_outcome_evidence_required"],
        )
        self.assertEqual(runtime_package["completion_callback"], "compact-signal-only")

    def test_routine_model_routing_preserves_risk_and_role_boundaries(self) -> None:
        root = Path(__file__).resolve().parents[2]
        policy = yaml.safe_load((root / "harness/agent-policy.manifest.yaml").read_text())
        routing = policy["subagent_protocol"]["codex_model_policy"]["routine_routing"]
        self.assertEqual(routing["decision_owner"], "parent-agent")
        self.assertEqual(routing["classification_scope"], "whole-work-package")
        self.assertTrue(routing["eligible_routine_must_use_routine_model"])
        self.assertTrue(routing["role_does_not_pin_model"])
        self.assertTrue(routing["prompt_text_does_not_select_model"])
        self.assertTrue(routing["frequency_or_agent_name_alone_is_not_eligibility"])
        self.assertEqual(set(routing["required_conditions"]), {
            "frozen-contract-and-acceptance", "bounded-coherent-work-package",
            "deterministic-direct-validation",
        })
        self.assertEqual(routing["default_dispatch"], "luna-first")
        self.assertNotIn("terra_required_for", routing)
        self.assertTrue(routing["category_alone_does_not_escalate"])
        self.assertEqual(routing["complex_mixed_or_unclassified"], "use-default-model-with-default-reasoning")
        self.assertEqual(routing["deterministic_single_command_or_small_fix"], "main-agent-no-dispatch")
        self.assertEqual(routing["luna_blocker_policy"], "stop-and-compact-callback-parent-checks-failure-layer-before-escalation")
        config = tomllib.loads((root / ".codex/config.toml").read_text())
        self.assertEqual(config["agents"]["default_subagent_model"], "gpt-5.6-luna")
        self.assertEqual(config["agents"]["default_subagent_reasoning_effort"], "medium")
        self.assertEqual(config["agents"]["max_concurrent_threads_per_session"], 1)
        self.assertNotIn("model", config)
        entry = root / ".codex" / config["model_instructions_file"]
        self.assertIn("harness/agent-policy.manifest.yaml", entry.read_text())
        self.assertNotIn("gpt-5.6-", entry.read_text())
        profiles = list((root / ".codex/agents").glob("*.toml"))
        self.assertGreaterEqual(len(profiles), 5)
        for profile in profiles:
            with self.subTest(profile=profile.name):
                role = tomllib.loads(profile.read_text())
                self.assertNotIn("model", role)

    def test_required_handoff_rejects_missing_field(self) -> None:
        self.assertEqual(tuple(valid_task())[: len(qoder_task.REQUIRED_HANDOFF)], qoder_task.REQUIRED_HANDOFF)
        for field in qoder_task.REQUIRED_HANDOFF:
            with self.subTest(field=field):
                task = valid_task()
                del task[field]
                with self.assertRaisesRegex(ValueError, field):
                    qoder_task._validate_task(task)

    def test_versioned_acceptance_fields_and_runner_identity_are_enforced(self) -> None:
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
        self.assertIn("Required result fields: schema_version, status, work_package_id", prompt)
        self.assertIn("acceptance_evidence, effect_checks, risks", prompt)
        self.assertIn("Run id: 00000000-0000-4000-8000-000000000001", prompt)

    def test_qoder_cli_is_bound_to_project_agent_profile(self) -> None:
        task = valid_task()
        task.update(
            agent_id="agent_test001",
            run_id="00000000-0000-4000-8000-000000000001",
            session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            client="qoder",
        )
        with patch.object(qoder_task, "_find_qoder_cli", return_value=Path("/bin/qodercli")):
            args = qoder_task._build_qodercli_args(task, Path("/repo"))
        self.assertEqual(args[args.index("--agent") + 1], "quality-verifier")
        self.assertEqual(args[args.index("--setting-sources") + 1], "project")
        self.assertEqual(args[args.index("--disallowed-tools") + 1], "Agent")

    def test_harness_preflight_binds_context_tools_commands_and_identity(self) -> None:
        task = valid_task()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self._write_harness_manifest(root, task)
            manifest = qoder_task._validate_harness_manifest(task, root)
            self.assertEqual(manifest["identity"]["work_package_id"], "LF-WP-TEST-001")

            altered = json.loads(manifest_path.read_text())
            altered["required_context"][-1]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                qoder_task._validate_harness_manifest(task, root)

    def test_catalog_preflight_binds_actual_size_owner_versions_and_scope(self) -> None:
        task = valid_task()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_catalog(root, task)
            result = qoder_task._validate_catalog_package(task, root)
            self.assertEqual(result["total_minutes"], 240)
            self.assertEqual(result["task_count"], 2)

            stale = json.loads(json.dumps(task))
            stale["task_versions"][stale["task_ids"][1]] = 2
            with self.assertRaisesRegex(ValueError, "task_version is stale"):
                qoder_task._validate_catalog_package(stale, root)

            undersized = json.loads(json.dumps(task))
            undersized["estimated_minutes"] = 180
            with self.assertRaisesRegex(ValueError, "catalog task sum"):
                qoder_task._validate_catalog_package(undersized, root)

            escaped_scope = json.loads(json.dumps(task))
            escaped_scope["allowed_files"] = "docs/example.md, backend/**"
            with self.assertRaisesRegex(ValueError, "derived catalog scope"):
                qoder_task._validate_catalog_package(escaped_scope, root)

            catalog_path = root / "planning/workstreams.yaml"
            catalog = yaml.safe_load(catalog_path.read_text())
            catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][1][
                "allowed_files"
            ] = ["backend/**"]
            catalog_path.write_text(yaml.safe_dump(catalog), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "claim outside Task scope"):
                qoder_task._validate_catalog_package(task, root)

    def test_package_acceptance_size_comes_from_all_current_tasks(self) -> None:
        task = valid_task()
        task["acceptance_criteria"] = ["anchor criterion"]
        task["acceptance_evidence"] = ["anchor evidence"]
        qoder_task._validate_task(task)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_catalog(root, task)
            path = root / "planning/workstreams.yaml"
            catalog = yaml.safe_load(path.read_text())
            partner = catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][1]
            partner["acceptance_criteria"] = ["partner A", "partner B", "partner C"]
            partner["acceptance_evidence"] = ["partner evidence A", "partner evidence B"]
            path.write_text(yaml.safe_dump(catalog))
            result = qoder_task._validate_catalog_package(task, root)
            self.assertEqual((result["criteria_count"], result["evidence_count"]), (4, 3))
            partner["acceptance_criteria"].pop()
            path.write_text(yaml.safe_dump(catalog))
            with self.assertRaisesRegex(ValueError, "4 catalog criteria"):
                qoder_task._validate_catalog_package(task, root)

    def test_distinct_task_paths_form_exact_derived_package_scope(self) -> None:
        task = valid_task()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_catalog(root, task)
            path = root / "planning/workstreams.yaml"
            catalog = yaml.safe_load(path.read_text())
            partner = catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][1]
            partner["allowed_files"] = ["docs/partner.md"]
            partner["file_claims"][0]["path"] = "docs/partner.md"
            path.write_text(yaml.safe_dump(catalog))
            task["allowed_files"] = "docs/example.md, docs/partner.md"
            normalized = qoder_task._validate_catalog_package(task, root)
            self.assertEqual(normalized["task_scopes"][partner["id"]]["allowed_files"], ["docs/partner.md"])
            task["allowed_files"] = "docs/example.md"
            with self.assertRaisesRegex(ValueError, "derived catalog scope"):
                qoder_task._validate_catalog_package(task, root)

    def test_cross_task_forbidden_scope_conflict_is_rejected(self) -> None:
        task = valid_task()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_catalog(root, task)
            path = root / "planning/workstreams.yaml"
            catalog = yaml.safe_load(path.read_text())
            partner = catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][1]
            partner["forbidden_files"] = ["docs/**", "harness/**"]
            path.write_text(yaml.safe_dump(catalog))
            task["forbidden_files"] = "docs/**, harness/**"
            with self.assertRaisesRegex(ValueError, "scopes intersect"):
                qoder_task._validate_catalog_package(task, root)

    def test_harness_rejects_incomplete_extra_duplicate_and_cwd_plans_before_probes(self) -> None:
        command = {"argv": ["python3", "-V"], "cwd": ".", "timeout_seconds": 30}
        cases = {
            "missing": [],
            "extra": [command, {**command, "argv": ["python3", "--version"]}],
            "duplicate": [command, command],
            "cwd": [{**command, "cwd": "docs"}],
        }
        for name, commands in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                task = valid_task()
                path = self._write_harness_manifest(root, task)
                manifest = json.loads(path.read_text())
                manifest["validation_commands"] = commands
                path.write_text(json.dumps(manifest), encoding="utf-8")
                with patch.object(qoder_task.subprocess, "run") as probe:
                    with self.assertRaises(ValueError):
                        qoder_task._validate_harness_manifest(task, root)
                    probe.assert_not_called()

    def test_harness_binds_non_anchor_command_and_reuses_catalog_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = valid_task()
            self._write_catalog(root, task)
            catalog_path = root / "planning/workstreams.yaml"
            catalog = yaml.safe_load(catalog_path.read_text())
            partner = catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][1]
            partner["validation_command"] = "python3 --version"
            catalog_path.write_text(yaml.safe_dump(catalog), encoding="utf-8")
            path = self._write_harness_manifest(root, task)
            normalized = qoder_task._validate_catalog_package(task, root)
            with patch.object(qoder_task.subprocess, "run") as probe:
                with self.assertRaisesRegex(ValueError, "exactly cover"):
                    qoder_task._validate_harness_manifest(task, root, catalog_package=normalized)
                probe.assert_not_called()
            manifest = json.loads(path.read_text())
            manifest["validation_commands"].append(
                {"argv": ["python3", "--version"], "cwd": ".", "timeout_seconds": 30}
            )
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with patch.object(qoder_task, "_validate_catalog_package") as normalize:
                qoder_task._validate_harness_manifest(task, root, catalog_package=normalized)
                normalize.assert_not_called()
            manifest["validation_commands"].reverse()
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly cover"):
                qoder_task._validate_harness_manifest(task, root, catalog_package=normalized)

    def test_harness_shares_identical_task_command_and_rejects_catalog_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = valid_task()
            self._write_harness_manifest(root, task)
            normalized = qoder_task._validate_catalog_package(task, root)
            manifest = qoder_task._validate_harness_manifest(task, root, catalog_package=normalized)
            self.assertEqual(len(manifest["validation_commands"]), 1)
            self.assertEqual(len(normalized["validation_argv_by_task"]), 2)
            stale = {**normalized, "catalog_sha256": "0" * 64}
            with patch.object(qoder_task.subprocess, "run") as probe:
                with self.assertRaisesRegex(ValueError, "changed after"):
                    qoder_task._validate_harness_manifest(task, root, catalog_package=stale)
                probe.assert_not_called()

    def test_catalog_rejects_missing_or_malformed_non_anchor_validation_command(self) -> None:
        for command in (None, " ", 'python3 "', 'python3 ""'):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                task = valid_task()
                self._write_catalog(root, task)
                path = root / "planning/workstreams.yaml"
                catalog = yaml.safe_load(path.read_text())
                partner = catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][1]
                partner["validation_command"] = command
                path.write_text(yaml.safe_dump(catalog), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "validation (command|argv)"):
                    qoder_task._validate_catalog_package(task, root)

    def test_preflight_command_validates_without_allocating_a_run(self) -> None:
        task = valid_task()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            self._write_catalog(root, task)
            self._write_harness_manifest(root, task)
            task_path = root / "task.json"
            task_path.write_text(json.dumps(task), encoding="utf-8")
            args = MagicMock(task=str(task_path))
            with (
                patch.object(Path, "cwd", return_value=root),
                patch.object(qoder_task, "_find_qoder_cli", return_value=Path("/bin/qodercli")),
                patch("builtins.print") as output,
            ):
                qoder_task.cmd_preflight(args)
            result = json.loads(output.call_args.args[0])
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["estimated_minutes"], 240)
            self.assertEqual(result["context_count"], 7)
            self.assertFalse(result["model_access_checked"])
            self.assertIn("not account/model availability", result["scope"])
            self.assertFalse((root / "tmp/qoder-tasks").exists())

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

    def test_worker_accepts_persisted_runtime_task_and_writes_verifiable_completion(self) -> None:
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
                patch.object(qoder_task, "_build_qodercli_args", return_value=["qodercli"]),
                patch.object(qoder_task.subprocess, "Popen", return_value=process),
                patch.object(lifecycle, "record_started"),
                patch.object(qoder_task, "_attempt_codex_callback", side_effect=observe_callback),
            ):
                qoder_task._worker_entry(task_dir, run_id, Path(directory))

            completion = json.loads((run_dir / "completion.json").read_text())
            self.assertEqual(completion["status"], "finished")
            self.assertEqual(completion["task_version"], 1)
            self.assertEqual(completion["change_version"], "1.0.0")
            self.assertEqual(completion["run_id"], run_id)
            self.assertEqual(callback_observations, [run_id])
            self.assertEqual(qoder_task._verify_terminal_completion(run_dir), (True, "finished"))

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
                patch.object(qoder_task, "_build_qodercli_args", return_value=["qodercli"]),
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
        with patch.object(qoder_task, "_find_qoder_cli", return_value=Path("/bin/qodercli")):
            args = qoder_task._build_qodercli_args(valid_task(), Path("."))
        self.assertEqual(args[args.index("--max-model-request-retries") + 1], "0")

    def test_stable_task_attempt_budget_cannot_be_reset(self) -> None:
        task = valid_task()
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            first = write_resumable_run(task_dir)
            c = json.loads((first / "completion.json").read_text())
            c.update(task_ids=task["task_ids"], task_versions=task["task_versions"],
                     change_versions=task["change_versions"])
            qoder_task._atomic_write_json(first / "completion.json", c)
            task.update(task_id="LF-TEST-002", work_package_id="LF-WP-RENAMED", task_version=2,
                        change_version="2.0.0", task_versions={x: 2 for x in task["task_ids"]},
                        change_versions={x: "2.0.0" for x in task["task_ids"]})
            with self.assertRaisesRegex(ValueError, "ATTEMPT_BUDGET.*explicit resume"):
                qoder_task._assert_dispatch_budget(task_dir, task, correction=False)
            qoder_task._assert_dispatch_budget(task_dir, task, correction=True)
            second = write_resumable_run(task_dir, "00000000-0000-4000-8000-000000000002")
            c["run_id"] = second.name
            qoder_task._atomic_write_json(second / "completion.json", c)
            with self.assertRaisesRegex(ValueError, "ATTEMPT_BUDGET.*exhausted"):
                qoder_task._assert_dispatch_budget(task_dir, task, correction=True)

    def test_recovery_assertion_cannot_bypass_initial_budget_before_probes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            task_dir = root / "tmp/qoder-tasks"
            task_dir.mkdir(parents=True)
            write_resumable_run(task_dir)
            task_path = root / "task.json"
            task_path.write_text(json.dumps(valid_task()))
            args = MagicMock(task=str(task_path), runtime_recovery_confirmed=True)
            with (patch.object(Path, "cwd", return_value=root),
                  patch.object(qoder_task, "_validate_catalog_package"),
                  patch.object(qoder_task, "_find_qoder_cli") as find_cli,
                  patch.object(qoder_task.subprocess, "Popen") as spawn):
                with self.assertRaisesRegex(ValueError, "ATTEMPT_BUDGET"):
                    qoder_task.cmd_start(args)
            find_cli.assert_not_called()
            spawn.assert_not_called()

    def test_legacy_anchor_counts_and_unrelated_task_is_not_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            write_resumable_run(task_dir)
            with self.assertRaisesRegex(ValueError, "ATTEMPT_BUDGET"):
                qoder_task._assert_dispatch_budget(task_dir, valid_task(), correction=False)
            task = valid_task()
            task["task_ids"] = ["LF-OTHER-001", "LF-OTHER-002"]
            qoder_task._assert_dispatch_budget(task_dir, task, correction=False)

    def test_account_blocker_requires_explicit_recovery_even_after_ack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            run = write_resumable_run(task_dir)
            qoder_task._atomic_write_json(run / "completion.json", {
                **terminal_completion(run.name, "failed"),
                "failure": {"category": "billing_access_rejected", "service_code": 112, "http_status": 403},
            })
            qoder_task._atomic_write_json(run / "ack.json", {"run_id": run.name})
            with self.assertRaisesRegex(ValueError, "RUNTIME_BLOCKED.*runtime-recovery-confirmed"):
                qoder_task._assert_runtime_recovery(task_dir, confirmed=False)
            self.assertEqual(qoder_task._assert_runtime_recovery(task_dir, confirmed=True), run.name)
            qoder_task._atomic_write_json(run / "completion.json", terminal_completion(run.name))
            self.assertIsNone(qoder_task._assert_runtime_recovery(task_dir, confirmed=False))

    def test_startup_failure_does_not_clear_a_previous_account_denial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            old = write_resumable_run(task_dir)
            qoder_task._atomic_write_json(old / "completion.json", {
                **terminal_completion(old.name, "failed"), "failure": {"category": "quota"},
            })
            later = write_resumable_run(task_dir, "00000000-0000-4000-8000-000000000002")
            qoder_task._atomic_write_json(later / "completion.json", {
                **terminal_completion(later.name, "failed"), "failure": {"category": "runtime_failure"},
            })
            os.utime(old / "completion.json", (1, 1))
            os.utime(later / "completion.json", (2, 2))
            with self.assertRaisesRegex(ValueError, "RUNTIME_BLOCKED"):
                qoder_task._assert_runtime_recovery(task_dir, confirmed=False)
            self.assertEqual(qoder_task._assert_runtime_recovery(task_dir, confirmed=True), old.name)

    def test_legacy_pricing_only_failure_is_also_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            run = write_resumable_run(task_dir)
            qoder_task._atomic_write_json(run / "completion.json", terminal_completion(run.name, "failed"))
            (run / "stdout.log").write_text(json.dumps({"type": "result", "errors": [
                json.dumps({"pricingUrl": "https://qoder.com/pricing?client=qodercli"})
            ]}))
            with self.assertRaisesRegex(ValueError, "RUNTIME_BLOCKED"):
                qoder_task._assert_runtime_recovery(task_dir, confirmed=False)

    def test_worker_persists_failure_before_compact_callback(self) -> None:
        run_id = "00000000-0000-4000-8000-000000000001"
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            run_dir = write_resumable_run(task_dir, run_id)
            process = MagicMock(pid=12345)
            def finish():
                (run_dir / "stdout.log").write_text(json.dumps({"type": "result", "error_code": 118,
                    "subtype": "error_during_execution", "errors": ["SECRET_PAYLOAD"]}))
                return 1
            process.wait.side_effect = finish
            observed = []
            def callback(*_args):
                c = json.loads((run_dir / "completion.json").read_text())
                observed.append(c["failure"]["category"])
                message = qoder_task._build_callback_message(run_id, c, run_dir)
                self.assertNotIn("SECRET_PAYLOAD", message)
                self.assertIn("result_error_code=118", message)
            with (patch.object(qoder_task, "_build_qodercli_args", return_value=["qodercli"]),
                  patch.object(qoder_task.subprocess, "Popen", return_value=process),
                  patch.object(lifecycle, "record_started"),
                  patch.object(qoder_task, "_attempt_codex_callback", side_effect=callback)):
                qoder_task._worker_entry(task_dir, run_id, Path(directory))
            self.assertEqual(observed, ["quota"])
            self.assertFalse((run_dir / "result.json").exists())

    def test_failed_account_run_can_be_explicitly_resumed_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            task_dir = root / "tmp/qoder-tasks"
            task_dir.mkdir(parents=True)
            old = write_resumable_run(task_dir)
            qoder_task._atomic_write_json(old / "completion.json", {
                **terminal_completion(old.name, "failed"),
                "failure": {"category": "billing_check_required"},
            })
            followup = root / "followup.json"
            followup.write_text("{}")
            args = MagicMock(run_id=old.name, followup=str(followup), runtime_recovery_confirmed=True)
            harness = {"required_context": [], "_manifest_sha256": "b" * 64}
            with (patch.object(Path, "cwd", return_value=root),
                  patch.object(qoder_task, "_find_qoder_cli", return_value=Path("/bin/qodercli")),
                  patch.object(qoder_task, "_check_qoder_idle"),
                  patch.object(qoder_task, "_validate_catalog_package"),
                  patch.object(qoder_task, "_validate_harness_manifest", return_value=harness),
                  patch.object(qoder_task, "_maybe_start_watchdog"),
                  patch.object(qoder_task.subprocess, "Popen", return_value=MagicMock(pid=12345)),
                  patch("builtins.print") as output):
                qoder_task.cmd_resume(args)
            new_run = task_dir / output.call_args.args[0]
            note = json.loads((new_run / "runtime-recovery.json").read_text())
            self.assertEqual(note["from_run_id"], old.name)
            self.assertFalse(note["account_access_verified"])
            self.assertNotIn("runtime_recovery_confirmed", json.loads((new_run / "task.json").read_text()))

    def test_global_preflight_blocks_second_cli(self) -> None:
        with patch.object(
            qoder_task.subprocess,
            "check_output",
            return_value="123 /Users/test/bin/qodercli\n",
        ) as snapshot:
            with self.assertRaisesRegex(ValueError, "BUSY.*123"):
                qoder_task._check_qoder_idle()
        snapshot.assert_called_once_with(
            ["ps", "-U", str(os.getuid()), "-o", "pid=,args="],
            text=True,
            timeout=5,
        )

    def test_global_preflight_detects_absolute_qoder_path_when_comm_would_be_truncated(self) -> None:
        with patch.object(
            qoder_task.subprocess,
            "check_output",
            return_value="456 /Users/zhehan/.local/bin/qodercli -p prompt\n",
        ):
            with self.assertRaisesRegex(ValueError, "BUSY.*456"):
                qoder_task._check_qoder_idle()

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

    def test_task_and_completion_storage_remain_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = valid_task()
            qoder_task._atomic_write_json(root / "task.json", task)
            qoder_task._atomic_write_json(
                root / "completion.json", {"status": "finished", "exit_code": 0}
            )
            self.assertEqual(json.loads((root / "task.json").read_text())["task_id"], "LF-TEST-001")
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

    def test_existing_unknown_or_missing_completion_blocks_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            missing_run = task_dir / "missing-run"
            missing_run.mkdir()
            with self.assertRaisesRegex(ValueError, "BUSY.*completion missing"):
                qoder_task._assert_no_unfinished_runs(task_dir)

            missing_run.rmdir()
            unknown_run = task_dir / "unknown-run"
            unknown_run.mkdir()
            qoder_task._atomic_write_json(
                unknown_run / "completion.json",
                terminal_completion(unknown_run.name, status="unknown"),
            )
            with self.assertRaisesRegex(ValueError, "BUSY.*unknown.*not terminal"):
                qoder_task._assert_no_unfinished_runs(task_dir)

    def test_verifiable_terminal_completion_allows_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            for index, status in enumerate(("finished", "failed", "completed"), start=1):
                run_dir = task_dir / f"terminal-{index}"
                run_dir.mkdir()
                qoder_task._atomic_write_json(
                    run_dir / "completion.json",
                    terminal_completion(run_dir.name, status=status),
                )
            qoder_task._assert_no_unfinished_runs(task_dir)

    def test_dispatch_lock_is_nonblocking_for_concurrent_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            with qoder_task._dispatch_lock(task_dir):
                contender = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        """
import sys
from pathlib import Path
from scripts.harness import qoder_task
try:
    with qoder_task._dispatch_lock(Path(sys.argv[1])):
        pass
except ValueError as exc:
    print(exc)
    raise SystemExit(0 if 'BUSY' in str(exc) else 2)
raise SystemExit(1)
""",
                        str(task_dir),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=str(Path(qoder_task.__file__).resolve().parents[2]),
                    check=False,
                )
            self.assertEqual(contender.returncode, 0, contender.stderr)
            self.assertRegex(contender.stdout, "BUSY.*dispatch")

    def test_worker_spawn_failure_records_terminal_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            (repo_root / ".git").mkdir()
            task_dir = repo_root / "tmp" / "qoder-tasks"
            task_dir.mkdir(parents=True)
            task_file = repo_root / "task.json"
            task_file.write_text(json.dumps(valid_task()), encoding="utf-8")

            args = MagicMock(task=str(task_file))
            with (
                patch.object(qoder_task, "_find_qoder_cli", return_value=Path("/bin/qodercli")),
                patch.object(qoder_task, "_check_qoder_idle"),
                patch.object(qoder_task, "_validate_catalog_package"),
                patch.object(qoder_task, "_validate_harness_manifest", return_value={"required_context": [], "_manifest_sha256": "0" * 64}),
                patch.object(qoder_task.subprocess, "Popen", side_effect=OSError("spawn denied")),
                patch.object(Path, "cwd", return_value=repo_root),
            ):
                with self.assertRaisesRegex(ValueError, "worker spawn failed"):
                    qoder_task.cmd_start(args)

            run_dirs = [entry for entry in task_dir.iterdir() if entry.is_dir()]
            self.assertEqual(len(run_dirs), 1)
            completion = json.loads((run_dirs[0] / "completion.json").read_text())
            self.assertEqual(completion["status"], "failed")
            self.assertEqual(completion["exit_code"], 126)
            self.assertEqual(completion["run_id"], run_dirs[0].name)
            qoder_task._assert_no_unfinished_runs(task_dir)

    def test_successful_start_persists_frozen_harness_and_returns_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            (repo_root / ".git").mkdir()
            task_file = repo_root / "task.json"
            task_file.write_text(json.dumps(valid_task()), encoding="utf-8")
            args = MagicMock(task=str(task_file))
            harness = {
                "required_context": [{"path": "AGENTS.md", "sha256": "a" * 64}],
                "_manifest_sha256": "b" * 64,
            }
            with (
                patch.object(Path, "cwd", return_value=repo_root),
                patch.object(qoder_task, "_find_qoder_cli", return_value=Path("/bin/qodercli")),
                patch.object(qoder_task, "_check_qoder_idle"),
                patch.object(qoder_task, "_validate_catalog_package"),
                patch.object(qoder_task, "_validate_harness_manifest", return_value=harness),
                patch.object(qoder_task, "_maybe_start_watchdog") as watchdog,
                patch.object(qoder_task.subprocess, "Popen", return_value=MagicMock(pid=12345)),
                patch("builtins.print") as output,
            ):
                qoder_task.cmd_start(args)
            run_id = output.call_args.args[0]
            self.assertTrue(qoder_task._is_valid_uuid(run_id))
            persisted = json.loads(
                (repo_root / "tmp/qoder-tasks" / run_id / "task.json").read_text()
            )
            self.assertEqual(persisted["harness_manifest_sha256"], "b" * 64)
            self.assertEqual(persisted["harness_context"], harness["required_context"])
            watchdog.assert_called_once()

    def test_resume_uses_shared_dispatch_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            (repo_root / ".git").mkdir()
            task_dir = repo_root / "tmp" / "qoder-tasks"
            task_dir.mkdir(parents=True)
            old_run = write_resumable_run(task_dir)
            followup = repo_root / "followup.json"
            followup.write_text("{}", encoding="utf-8")
            args = MagicMock(run_id=old_run.name, followup=str(followup))

            with (
                qoder_task._dispatch_lock(task_dir),
                patch.object(Path, "cwd", return_value=repo_root),
                patch.object(qoder_task, "_find_qoder_cli") as find_cli,
            ):
                with self.assertRaisesRegex(ValueError, "BUSY.*dispatch"):
                    qoder_task.cmd_resume(args)
            find_cli.assert_not_called()

    def test_resume_blocks_existing_unknown_before_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            (repo_root / ".git").mkdir()
            task_dir = repo_root / "tmp" / "qoder-tasks"
            task_dir.mkdir(parents=True)
            old_run = write_resumable_run(task_dir)
            (task_dir / "unknown-run").mkdir()
            followup = repo_root / "followup.json"
            followup.write_text("{}", encoding="utf-8")
            args = MagicMock(run_id=old_run.name, followup=str(followup))

            with (
                patch.object(Path, "cwd", return_value=repo_root),
                patch.object(qoder_task, "_find_qoder_cli") as find_cli,
                patch.object(qoder_task.subprocess, "Popen") as spawn,
            ):
                with self.assertRaisesRegex(ValueError, "BUSY.*unknown-run"):
                    qoder_task.cmd_resume(args)
            find_cli.assert_not_called()
            spawn.assert_not_called()

    def test_resume_spawn_failure_releases_reservation_and_records_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            (repo_root / ".git").mkdir()
            task_dir = repo_root / "tmp" / "qoder-tasks"
            task_dir.mkdir(parents=True)
            old_run = write_resumable_run(task_dir)
            followup = repo_root / "followup.json"
            followup.write_text('{"goal": "bounded followup"}', encoding="utf-8")
            args = MagicMock(run_id=old_run.name, followup=str(followup))

            with (
                patch.object(Path, "cwd", return_value=repo_root),
                patch.object(qoder_task, "_find_qoder_cli", return_value=Path("/bin/qodercli")),
                patch.object(qoder_task, "_check_qoder_idle"),
                patch.object(qoder_task, "_validate_catalog_package"),
                patch.object(qoder_task, "_validate_harness_manifest", return_value={"required_context": [], "_manifest_sha256": "0" * 64}),
                patch.object(qoder_task.subprocess, "Popen", side_effect=OSError("spawn denied")),
            ):
                with self.assertRaisesRegex(ValueError, "worker spawn failed"):
                    qoder_task.cmd_resume(args)

            self.assertFalse((old_run / "resume.reservation").exists())
            run_dirs = [entry for entry in task_dir.iterdir() if entry.is_dir()]
            self.assertEqual(len(run_dirs), 2)
            new_run = next(entry for entry in run_dirs if entry != old_run)
            completion = json.loads((new_run / "completion.json").read_text())
            self.assertEqual(completion["status"], "failed")
            self.assertEqual(completion["exit_code"], 126)
            self.assertEqual(completion["run_id"], new_run.name)
            qoder_task._assert_no_unfinished_runs(task_dir)


if __name__ == "__main__":
    unittest.main()
