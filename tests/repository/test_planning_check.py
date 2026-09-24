"""Tests for the planning catalog validator.

Each test builds a minimal fixture, injects a specific defect, and asserts
the validator produces a precise diagnostic.
"""

import copy
import os
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.repository.planning_check import (
    PlanningValidator,
    _load_canonical_from_policy,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = yaml.safe_load((ROOT / "harness" / "agent-policy.manifest.yaml").read_text())
CANONICAL = _load_canonical_from_policy(POLICY)


def _base_handoff_contract():
    return {
        "caller_required_input": list(CANONICAL["caller_fields"]),
        "runner_bound_identity": copy.deepcopy(CANONICAL["runner_identity"]),
        "result_required_output": list(CANONICAL["result_fields"]),
        "current_runner_adapter": {
            **copy.deepcopy(CANONICAL["adapter_flags"]),
            **copy.deepcopy(CANONICAL["adapter_metadata"]),
            "enforced_caller_fields": list(CANONICAL["adapter_enforced_fields"]),
        },
        "caller_field_schema": copy.deepcopy(CANONICAL["caller_field_schema"]),
    }


def _base_valid_catalog():
    return {
        "schema_version": "lexiflow.workstreams.v1",
        "program": {
            "id": "LF-PRG-001",
            "name": "Test",
            "status": "ACTIVE",
            "current_phase": "P1",
            "current_gate": "G1",
        },
        "id_policy": {
            "program": "LF-PRG-NNN",
            "workstream": "LF-WS-{DOMAIN}",
            "epic": "LF-EP-{DOMAIN}-NNN",
            "capability": "LF-CP-{DOMAIN}-NNN",
            "task": "LF-TSK-{DOMAIN}-NNNN",
        },
        "domain_codes": {"ARCH": "arch", "QLT": "quality", "PRD": "product"},
        "path_ownership": {
            "resolution": "Most-specific wins",
            "scopes": [
                {
                    "scope": "governance.catalog",
                    "owner": "LF-WS-QLT",
                    "proposed_paths": [
                        "planning/**",
                        "scripts/gates/**",
                        "tests/gates/**",
                        "tests/harness/**",
                        "harness/**",
                        "scripts/harness/**",
                    ],
                },
                {
                    "scope": "governance.roadmap",
                    "owner": "LF-WS-ARCH",
                    "proposed_paths": ["docs/roadmap/**"],
                },
                {
                    "scope": "governance.openspec",
                    "owner": "LF-WS-ARCH",
                    "proposed_paths": ["openspec/**"],
                },
                {
                    "scope": "governance.architecture",
                    "owner": "LF-WS-ARCH",
                    "proposed_paths": ["docs/architecture/**", "docs/adr/**"],
                },
                {
                    "scope": "governance.development",
                    "owner": "LF-WS-QLT",
                    "proposed_paths": ["docs/development/**"],
                },
                {
                    "scope": "governance.product",
                    "owner": "LF-WS-PRD",
                    "proposed_paths": ["docs/product/**"],
                },
                {
                    "scope": "governance.acceptance",
                    "owner": "LF-WS-QLT",
                    "proposed_paths": ["docs/product/product-brief.md"],
                },
                {
                    "scope": "governance.entrypoints",
                    "owner": "LF-WS-ARCH",
                    "proposed_paths": [
                        "AGENTS.md",
                        "README.md",
                        ".gitignore",
                        "docs/README.md",
                        "harness/README.md",
                        "harness/manifest.yaml",
                        "harness/module-boundaries.yaml",
                        "harness/agent-policy.manifest.yaml",
                        "harness/agent-runtime.manifest.yaml",
                        "scripts/README.md",
                    ],
                },
                {
                    "scope": "governance.agent-entrypoints",
                    "owner": "LF-WS-QLT",
                    "proposed_paths": [".codex/**", ".qoder/**"],
                },
            ],
        },
        "phase_entry_validation": {
            "effective_phase": "task.phase overrides capability.phase",
            "entry_task_definition": "P2+ task",
            "approval_evidence_inheritance": {},
            "rules": [],
            "entry_tasks": {
                "P2": ["LF-TSK-ARCH-0003"],
            },
        },
        "phase_gates": [
            {
                "id": "G1",
                "phase": "P1",
                "name": "Architecture Accepted",
                "status": "ACTIVE",
                "approval": "explicit-user-confirmation",
                "exit_task": "LF-TSK-ARCH-0002",
                "entry_tasks": ["LF-TSK-ARCH-0001"],
                "entry_requires": None,
            },
            {
                "id": "G2",
                "phase": "P2",
                "name": "Data Contract",
                "status": "DRAFT",
                "approval": "explicit-user-confirmation",
                "exit_task": "LF-TSK-ARCH-0004",
                "entry_tasks": ["LF-TSK-ARCH-0003"],
                "entry_requires": {
                    "previous_gate_id": "G1",
                    "previous_gate_exit_task_id": "LF-TSK-ARCH-0002",
                    "required_exit_task_version": 1,
                    "required_exit_change_version": "1.0.0",
                    "required_gate_result": "PASS",
                    "required_user_approval": "APPROVED",
                    "approval_evidence_type": "phase-gate-approval-receipt",
                },
            },
        ],
        "orchestration_policy": {
            "handoff_contract": _base_handoff_contract(),
        },
        "workstreams": [
            {
                "id": "LF-WS-ARCH",
                "code": "ARCH",
                "name": "Architecture",
                "mission": "test",
                "epics": [
                    {
                        "id": "LF-EP-ARCH-001",
                        "name": "test",
                        "capabilities": [
                            {
                                "id": "LF-CP-ARCH-001",
                                "name": "test",
                                "phase": "P1",
                                "seed_tasks": [
                                    {
                                        "id": "LF-TSK-ARCH-0001",
                                        "title": "Freeze assumptions",
                                        "priority": "P0",
                                        "depends_on": [],
                                        "task_version": 1,
                                        "change_version": "1.0.0",
                                    },
                                    {
                                        "id": "LF-TSK-ARCH-0002",
                                        "title": "G1 exit review",
                                        "priority": "P0",
                                        "depends_on": [
                                            {
                                                "task_id": "LF-TSK-ARCH-0001",
                                                "type": "hard",
                                                "required_task_version": 1,
                                                "required_change_version": "1.0.0",
                                                "required_result": "PASS",
                                            },
                                        ],
                                        "task_version": 1,
                                        "change_version": "1.0.0",
                                    },
                                ],
                            },
                            {
                                "id": "LF-CP-ARCH-002",
                                "name": "test2",
                                "phase": "P2",
                                "seed_tasks": [
                                    {
                                        "id": "LF-TSK-ARCH-0003",
                                        "title": "P2 entry task",
                                        "priority": "P0",
                                        "depends_on": [
                                            {
                                                "task_id": "LF-TSK-ARCH-0002",
                                                "type": "hard",
                                                "required_task_version": 1,
                                                "required_change_version": "1.0.0",
                                                "required_result": "PASS",
                                            },
                                        ],
                                        "task_version": 1,
                                        "change_version": "1.0.0",
                                        "phase_entry_prerequisite": {
                                            "previous_gate_id": "G1",
                                            "previous_gate_exit_task_id": "LF-TSK-ARCH-0002",
                                            "required_exit_task_version": 1,
                                            "required_exit_change_version": "1.0.0",
                                            "required_gate_result": "PASS",
                                            "required_user_approval": "APPROVED",
                                        },
                                    },
                                    {
                                        "id": "LF-TSK-ARCH-0004",
                                        "title": "P2 non-entry",
                                        "priority": "P0",
                                        "depends_on": [
                                            {
                                                "task_id": "LF-TSK-ARCH-0003",
                                                "type": "hard",
                                                "required_task_version": 1,
                                                "required_change_version": "1.0.0",
                                                "required_result": "PASS",
                                            },
                                        ],
                                        "task_version": 1,
                                        "change_version": "1.0.0",
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
            {
                "id": "LF-WS-QLT",
                "code": "QLT",
                "name": "Quality",
                "mission": "test",
                "epics": [],
            },
            {
                "id": "LF-WS-PRD",
                "code": "PRD",
                "name": "Product",
                "mission": "test",
                "epics": [],
            },
        ],
    }


def _base_template():
    return {
        "schema_version": "lexiflow.task.v1",
        "handoff_contract": _base_handoff_contract(),
    }


def _base_policy():
    return {
        "schemaVersion": 1,
        "subagent_protocol": _base_handoff_contract(),
    }


def _base_runtime():
    return {
        "schemaVersion": 1,
        "subagent_protocol": _base_handoff_contract(),
    }


def _validate(
    data,
    template=None,
    policy=None,
    runtime=None,
    use_default_runtime=True,
    fill_required=True,
):
    if fill_required:
        for ws in data.get("workstreams", []):
            for epic in ws.get("epics", []):
                for capability in epic.get("capabilities", []):
                    for task in capability.get("seed_tasks", []):
                        task.setdefault(
                            "required_check_ids", ["eng.repository.planning"]
                        )
    v = PlanningValidator.from_data(
        data,
        template_data=template if template is not None else _base_template(),
        policy_data=policy if policy is not None else _base_policy(),
        runtime_data=(
            runtime
            if not use_default_runtime or runtime is not None
            else _base_runtime()
        ),
        module_checks_data={
            "checks": [
                {"check_id": "eng.repository.planning", "scope": "repository-baseline"}
            ]
        },
    )
    # In-memory catalog fixtures must not inherit unrelated files from the real
    # checkout when the ownership check walks the repository root.
    with tempfile.TemporaryDirectory(prefix="lexiflow-planning-fixture-") as root:
        v.root = Path(root)
        return v.run_all()


def _find_task(data, task_id):
    for ws in data["workstreams"]:
        for epic in ws["epics"]:
            for cap in epic["capabilities"]:
                for task in cap["seed_tasks"]:
                    if task["id"] == task_id:
                        return task
    return None


class TestValidCatalog(unittest.TestCase):
    def test_valid_catalog_passes(self):
        result = _validate(_base_valid_catalog())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["task_count"], 4)
        self.assertEqual(len(result["checks_run"]), 12)


class TestPlanningOnlyCatalog(unittest.TestCase):
    def empty_catalog(self):
        data = _base_valid_catalog()
        data["program"] = {"id": "LF-PRG-001", "catalog_mode": "planning-only"}
        data.pop("phase_gates")
        data.pop("phase_entry_validation")
        for workstream in data["workstreams"]:
            workstream["epics"] = []
        return data

    def test_explicit_empty_planning_catalog_runs_all_static_checks(self):
        result = _validate(self.empty_catalog())
        self.assertEqual("PASS", result["status"], result["errors"])
        self.assertEqual(0, result["task_count"])
        self.assertEqual(12, len(result["checks_run"]))

    def test_empty_catalog_without_explicit_mode_still_fails(self):
        data = self.empty_catalog()
        data["program"].pop("catalog_mode")
        self.assertIn("no-tasks-found-in-catalog", _validate(data)["errors"])

    def test_malformed_program_returns_structured_failure(self):
        data = self.empty_catalog()
        data["program"] = "planning-only"
        self.assertIn("invalid-program-structure", _validate(data)["errors"])

    def test_empty_catalog_cannot_retain_gate_or_entry_task(self):
        for field in ("phase_gates", "phase_entry_validation"):
            with self.subTest(field=field):
                data = self.empty_catalog()
                data[field] = _base_valid_catalog()[field]
                self.assertIn(
                    "planning-only-catalog-has-execution-state",
                    _validate(data)["errors"],
                )

    def test_planning_mode_cannot_hide_tasks_or_phase_execution(self):
        for field, value in (
            ("current_phase", "P2"),
            ("current_gate", "G2"),
            ("phase_2_to_6_dispatch_requires_g1_user_approval", True),
        ):
            with self.subTest(field=field):
                data = self.empty_catalog()
                data["program"][field] = value
                self.assertIn(
                    "planning-only-catalog-has-execution-state",
                    _validate(data)["errors"],
                )
        data = _base_valid_catalog()
        data["program"] = {"id": "LF-PRG-001", "catalog_mode": "planning-only"}
        self.assertIn(
            "planning-only-catalog-has-execution-state", _validate(data)["errors"]
        )

    def test_empty_planning_mode_preserves_ownership_and_policy_checks(self):
        data = self.empty_catalog()
        data["path_ownership"]["scopes"][0]["owner"] = "LF-WS-MISSING"
        result = _validate(data)
        self.assertNotEqual("PASS", result["status"])
        self.assertTrue(any("owner-not-found" in error for error in result["errors"]))
        data = self.empty_catalog()
        data["orchestration_policy"]["handoff_contract"]["caller_required_input"] = []
        self.assertNotEqual("PASS", _validate(data)["status"])


class TestPopulatedCatalog(unittest.TestCase):

    def test_valid_catalog_does_not_hardcode_count(self):
        data = _base_valid_catalog()
        r1 = _validate(data)
        self.assertEqual(r1["task_count"], 4)

        caps = data["workstreams"][0]["epics"][0]["capabilities"]
        caps[0]["seed_tasks"].append(
            {
                "id": "LF-TSK-ARCH-0005",
                "title": "extra",
                "priority": "P1",
                "depends_on": [],
                "task_version": 1,
                "change_version": "1.0.0",
            }
        )
        r2 = _validate(data)
        self.assertEqual(r2["status"], "PASS")
        self.assertEqual(r2["task_count"], 5)

    def test_missing_required_check_mapping_fails(self):
        data = _base_valid_catalog()
        result = _validate(data, fill_required=False)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("required-check-ids-missing" in e for e in result["errors"])
        )

    def test_unknown_required_check_mapping_fails(self):
        data = _base_valid_catalog()
        task = data["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][0]
        task["required_check_ids"] = ["eng.unknown"]
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("required-check-ids-unknown" in e for e in result["errors"])
        )


class TestDuplicateId(unittest.TestCase):
    def test_duplicate_task_id_fails(self):
        data = _base_valid_catalog()
        caps = data["workstreams"][0]["epics"][0]["capabilities"]
        dup = copy.deepcopy(caps[0]["seed_tasks"][0])
        caps[1]["seed_tasks"].append(dup)
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("duplicate-task-id" in e for e in result["errors"]))


class TestMissingDependency(unittest.TestCase):
    def test_missing_dependency_fails(self):
        data = _base_valid_catalog()
        task = _find_task(data, "LF-TSK-ARCH-0002")
        task["depends_on"].append(
            {
                "task_id": "LF-TSK-ARCH-9999",
                "type": "hard",
                "required_task_version": 1,
                "required_change_version": "1.0.0",
                "required_result": "PASS",
            }
        )
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("missing-dependency" in e for e in result["errors"]))


class TestVersionMismatch(unittest.TestCase):
    def test_task_version_mismatch_fails(self):
        data = _base_valid_catalog()
        task = _find_task(data, "LF-TSK-ARCH-0002")
        task["depends_on"][0]["required_task_version"] = 99
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("dep-version-mismatch" in e for e in result["errors"]))

    def test_change_version_mismatch_fails(self):
        data = _base_valid_catalog()
        task = _find_task(data, "LF-TSK-ARCH-0002")
        task["depends_on"][0]["required_change_version"] = "9.9.9"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("dep-change-version-mismatch" in e for e in result["errors"])
        )


class TestContractProducerMismatch(unittest.TestCase):
    def test_no_producer_fails(self):
        data = _base_valid_catalog()
        caps = data["workstreams"][0]["epics"][0]["capabilities"]
        caps[1]["seed_tasks"][0]["depends_on"].append(
            {
                "task_id": "LF-TSK-ARCH-0002",
                "type": "contract",
                "required_task_version": 1,
                "required_change_version": "1.0.0",
                "contract_name": "phantom-contract",
                "required_contract_version": "1.0.0",
                "required_result": "PASS",
            }
        )
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("no-contract-producer" in e for e in result["errors"]))

    def test_contract_version_mismatch_fails(self):
        data = _base_valid_catalog()
        caps = data["workstreams"][0]["epics"][0]["capabilities"]
        caps[0]["seed_tasks"][0]["produced_contracts"] = [
            {"name": "my-contract", "version": "1.0.0"},
        ]
        caps[1]["seed_tasks"][0]["depends_on"].append(
            {
                "task_id": "LF-TSK-ARCH-0001",
                "type": "contract",
                "required_task_version": 1,
                "required_change_version": "1.0.0",
                "contract_name": "my-contract",
                "required_contract_version": "2.0.0",
                "required_result": "PASS",
            }
        )
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("contract-version-mismatch" in e for e in result["errors"]))

    def test_contract_producer_identity_mismatch_fails(self):
        data = _base_valid_catalog()
        caps = data["workstreams"][0]["epics"][0]["capabilities"]
        caps[0]["seed_tasks"][0]["produced_contracts"] = [
            {"name": "my-contract", "version": "1.0.0"},
        ]
        caps[1]["seed_tasks"][0]["depends_on"].append(
            {
                "task_id": "LF-TSK-ARCH-0002",
                "type": "contract",
                "required_task_version": 1,
                "required_change_version": "1.0.0",
                "contract_name": "my-contract",
                "required_contract_version": "1.0.0",
                "required_result": "PASS",
            }
        )
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("contract-producer-identity-mismatch" in e for e in result["errors"])
        )


class TestCycle(unittest.TestCase):
    def test_cycle_fails(self):
        data = _base_valid_catalog()
        t1 = _find_task(data, "LF-TSK-ARCH-0001")
        t1["depends_on"].append(
            {
                "task_id": "LF-TSK-ARCH-0002",
                "type": "hard",
                "required_task_version": 1,
                "required_change_version": "1.0.0",
                "required_result": "PASS",
            }
        )
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("cycle-detected" in e for e in result["errors"]))


class TestLaterPhaseEdge(unittest.TestCase):
    def test_later_phase_dependency_fails(self):
        data = _base_valid_catalog()
        t1 = _find_task(data, "LF-TSK-ARCH-0001")
        t1["depends_on"].append(
            {
                "task_id": "LF-TSK-ARCH-0003",
                "type": "hard",
                "required_task_version": 1,
                "required_change_version": "1.0.0",
                "required_result": "PASS",
            }
        )
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("later-phase-edge" in e for e in result["errors"]))


class TestPhaseEntryAncestryGap(unittest.TestCase):
    def test_ancestry_gap_fails(self):
        data = _base_valid_catalog()
        orphan = _find_task(data, "LF-TSK-ARCH-0004")
        orphan["depends_on"] = [
            {
                "task_id": "LF-TSK-ARCH-0002",
                "type": "hard",
                "required_task_version": 1,
                "required_change_version": "1.0.0",
                "required_result": "PASS",
            },
        ]
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("phase-entry-ancestry-gap" in e for e in result["errors"]))

    def test_soft_edge_does_not_satisfy_ancestry(self):
        data = _base_valid_catalog()
        orphan = _find_task(data, "LF-TSK-ARCH-0004")
        orphan["depends_on"] = [
            {
                "task_id": "LF-TSK-ARCH-0003",
                "type": "soft",
                "required_task_version": 1,
                "required_change_version": "1.0.0",
            },
        ]
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("phase-entry-ancestry-gap" in e for e in result["errors"]))

    def test_missing_phase_entry_prerequisite_fails(self):
        data = _base_valid_catalog()
        entry = _find_task(data, "LF-TSK-ARCH-0003")
        del entry["phase_entry_prerequisite"]
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("phase-entry-missing-prerequisite" in e for e in result["errors"])
        )

    def test_redeclare_approval_evidence_type_fails(self):
        data = _base_valid_catalog()
        entry = _find_task(data, "LF-TSK-ARCH-0003")
        entry["phase_entry_prerequisite"]["approval_evidence_type"] = "custom"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any(
                "phase-entry-redeclares-approval-evidence" in e
                for e in result["errors"]
            )
        )

    def test_hard_dep_result_not_pass_fails(self):
        data = _base_valid_catalog()
        entry = _find_task(data, "LF-TSK-ARCH-0003")
        entry["depends_on"][0]["required_result"] = "FAIL"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("phase-entry-hard-dep-result-not-pass" in e for e in result["errors"])
        )


class TestAmbiguousOwner(unittest.TestCase):
    def test_ambiguous_path_ownership_fails(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"].append(
            {
                "scope": "governance.overlap",
                "owner": "LF-WS-ARCH",
                "proposed_paths": ["planning/**"],
            }
        )
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("ambiguous-path-ownership" in e for e in result["errors"]))

    def test_owner_not_in_workstreams_fails(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"][0]["owner"] = "LF-WS-NONEXISTENT"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("owner-not-found" in e for e in result["errors"]))

    def test_empty_scope_name_fails(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"][0]["scope"] = ""
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("empty-scope-name" in e for e in result["errors"]))


class TestCrossSourceHandoff(unittest.TestCase):
    def test_all_four_sources_required(self):
        result = _validate(
            _base_valid_catalog(), runtime=None, use_default_runtime=False
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("cross-source-incomplete" in e for e in result["errors"]))

    def test_canonical_caller_count_enforced(self):
        policy = _base_policy()
        policy["subagent_protocol"]["caller_required_input"] = list(
            CANONICAL["caller_fields"]
        )[:-1]
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("canonical-caller-count" in e for e in result["errors"]))

    def test_canonical_result_count_enforced(self):
        policy = _base_policy()
        policy["subagent_protocol"]["result_required_output"] = [
            "status",
            "changed_files",
        ]
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("canonical-result-count" in e for e in result["errors"]))

    def test_caller_field_mismatch_fails(self):
        policy = _base_policy()
        policy["subagent_protocol"]["caller_required_input"] = list(
            CANONICAL["caller_fields"]
        )[:-1]
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("cross-source-caller-mismatch" in e for e in result["errors"])
        )

    def test_runner_identity_metadata_mismatch_fails(self):
        policy = _base_policy()
        policy["subagent_protocol"]["runner_bound_identity"]["client"][
            "value"
        ] = "wrong"
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("cross-source-identity-mismatch" in e for e in result["errors"])
        )

    def test_adapter_metadata_mismatch_fails(self):
        policy = _base_policy()
        policy["subagent_protocol"]["current_runner_adapter"][
            "target_schema_enforced"
        ] = False
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("cross-source-adapter-mismatch" in e for e in result["errors"])
        )

    def test_schema_key_value_mismatch_fails(self):
        policy = _base_policy()
        policy["subagent_protocol"]["caller_field_schema"]["goal"] = "different-type"
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("cross-source-schema-mismatch" in e for e in result["errors"])
        )

    def test_consistent_sources_pass(self):
        result = _validate(
            _base_valid_catalog(),
            template=_base_template(),
            policy=_base_policy(),
            runtime=_base_runtime(),
        )
        self.assertEqual(result["status"], "PASS")


class TestInvalidDepType(unittest.TestCase):
    def test_invalid_dep_type_fails(self):
        data = _base_valid_catalog()
        task = _find_task(data, "LF-TSK-ARCH-0002")
        task["depends_on"][0]["type"] = "magic"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("invalid-dep-type" in e for e in result["errors"]))

    def test_soft_dep_missing_common_field_fails(self):
        data = _base_valid_catalog()
        caps = data["workstreams"][0]["epics"][0]["capabilities"]
        caps[0]["seed_tasks"][0]["depends_on"].append(
            {
                "task_id": "LF-TSK-ARCH-0002",
                "type": "soft",
            }
        )
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("dep-missing-common-field" in e for e in result["errors"]))

    def test_hard_dep_result_not_pass_fails(self):
        data = _base_valid_catalog()
        task = _find_task(data, "LF-TSK-ARCH-0002")
        task["depends_on"][0]["required_result"] = "FAIL"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("dep-result-not-pass" in e for e in result["errors"]))


class TestAllEntityIds(unittest.TestCase):
    def test_invalid_program_id_fails(self):
        data = _base_valid_catalog()
        data["program"]["id"] = "BAD-ID"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("invalid-program-id" in e for e in result["errors"]))

    def test_invalid_workstream_id_fails(self):
        data = _base_valid_catalog()
        data["workstreams"][0]["id"] = "BAD-WS"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("invalid-workstream-id" in e for e in result["errors"]))

    def test_invalid_epic_id_fails(self):
        data = _base_valid_catalog()
        data["workstreams"][0]["epics"][0]["id"] = "BAD-EPIC"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("invalid-epic-id" in e for e in result["errors"]))

    def test_invalid_capability_id_fails(self):
        data = _base_valid_catalog()
        data["workstreams"][0]["epics"][0]["capabilities"][0]["id"] = "BAD-CAP"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("invalid-capability-id" in e for e in result["errors"]))

    def test_invalid_gate_id_fails(self):
        data = _base_valid_catalog()
        data["phase_gates"][0]["id"] = "BAD-GATE"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("invalid-gate-id" in e for e in result["errors"]))


class TestLoadErrors(unittest.TestCase):
    def test_missing_workstreams_fails(self):
        v = PlanningValidator("/nonexistent/path")
        v.load_sources()
        result = v.run_all()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("Required file not found" in e for e in result["errors"]))


class TestRepoIntegration(unittest.TestCase):
    def test_real_repo_passes_structural_checks(self):
        """The real catalog has complete ownership and fixed-check mappings."""
        repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
        v = PlanningValidator(repo_root)
        v.load_sources()
        result = v.run_all()
        self.assertEqual("PASS", result["status"])
        self.assertEqual([], result["errors"])
        self.assertEqual("execution", v.ws["program"]["catalog_mode"])
        self.assertGreater(result["task_count"], 0)
        self.assertTrue(all(task["phase"] == "P1" for task in v.tasks.values()))
        self.assertFalse(v.gates, "No historical gates may authorize foundation work")
        self.assertEqual(len(result["checks_run"]), 12)


class TestOwnerResolver(unittest.TestCase):
    def test_finder_metadata_is_not_an_ownership_subject(self):
        validator = PlanningValidator.from_data(_base_valid_catalog())
        with tempfile.TemporaryDirectory() as directory:
            validator.root = Path(directory)
            (validator.root / ".DS_Store").write_bytes(b"fixture")
            nested = validator.root / "unowned"
            nested.mkdir()
            (nested / ".DS_Store").write_bytes(b"fixture")
            validator.check_repo_path_ownership()
            self.assertEqual([], validator.errors)
            # 排除操作系统元数据不应放过普通的未声明文件。
            (nested / "source.py").write_text("pass\n")
            validator.check_repo_path_ownership()
            self.assertTrue(
                any(
                    "repo-unowned-file: unowned/source.py" in e
                    for e in validator.errors
                )
            )

    def test_most_specific_owner_wins(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"] = [
            {
                "scope": "governance",
                "owner": "LF-WS-ARCH",
                "proposed_paths": ["docs/**"],
            },
            {
                "scope": "governance.catalog",
                "owner": "LF-WS-QLT",
                "proposed_paths": ["docs/catalog/**"],
            },
        ]
        v = PlanningValidator.from_data(data)
        owner = v.resolve_owner("docs/catalog/file.yaml")
        self.assertEqual(owner, "LF-WS-QLT")

    def test_same_specificity_different_owners_ambiguous(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"] = [
            {"scope": "scope.a", "owner": "LF-WS-ARCH", "proposed_paths": ["docs/**"]},
            {"scope": "scope.b", "owner": "LF-WS-QLT", "proposed_paths": ["docs/**"]},
        ]
        v = PlanningValidator.from_data(data)
        owner = v.resolve_owner("docs/file.yaml")
        self.assertEqual(owner, "AMBIGUOUS")


if __name__ == "__main__":
    unittest.main()
