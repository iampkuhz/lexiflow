"""Regression tests for defect categories found while accepting LF-TSK-QLT-0002.

Each test injects a specific defect and asserts the validator produces
a precise diagnostic.
"""

import copy
import os
import unittest
from pathlib import Path

from scripts.gates.planning import (
    CANONICAL_ADAPTER,
    CANONICAL_ADAPTER_METADATA,
    CANONICAL_CALLER_FIELDS,
    CANONICAL_CALLER_FIELD_SCHEMA,
    CANONICAL_RESULT_FIELDS,
    CANONICAL_RUNNER_IDENTITY,
    PlanningValidator,
    _is_excluded,
)


def _base_handoff_contract():
    return {
        "caller_required_input": list(CANONICAL_CALLER_FIELDS),
        "runner_bound_identity": copy.deepcopy(CANONICAL_RUNNER_IDENTITY),
        "result_required_output": list(CANONICAL_RESULT_FIELDS),
        "current_runner_adapter": {
            "enforced_caller_fields": list(CANONICAL_CALLER_FIELDS),
            **copy.deepcopy(CANONICAL_ADAPTER),
            **copy.deepcopy(CANONICAL_ADAPTER_METADATA),
        },
        "caller_field_schema": copy.deepcopy(CANONICAL_CALLER_FIELD_SCHEMA),
    }


def _base_valid_catalog():
    return {
        "schema_version": "lexiflow.workstreams.v1",
        "program": {"id": "LF-PRG-001", "name": "Test", "status": "ACTIVE",
                     "current_phase": "P1", "current_gate": "G1"},
        "id_policy": {
            "program": "LF-PRG-NNN",
            "workstream": "LF-WS-{DOMAIN}",
            "epic": "LF-EP-{DOMAIN}-NNN",
            "capability": "LF-CP-{DOMAIN}-NNN",
            "task": "LF-TSK-{DOMAIN}-NNNN",
        },
        "domain_codes": {"ARCH": "arch", "QLT": "quality"},
        "path_ownership": {
            "resolution": "Most-specific wins",
            "scopes": [
                {"scope": "governance.catalog", "owner": "LF-WS-QLT",
                 "proposed_paths": ["planning/**", "scripts/gates/**"]},
                {"scope": "governance.roadmap", "owner": "LF-WS-ARCH",
                 "proposed_paths": ["docs/roadmap/**"]},
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
                "id": "G1", "phase": "P1", "name": "Architecture Accepted",
                "status": "ACTIVE", "approval": "explicit-user-confirmation",
                "exit_task": "LF-TSK-ARCH-0002",
                "entry_tasks": ["LF-TSK-ARCH-0001"],
                "entry_requires": None,
            },
            {
                "id": "G2", "phase": "P2", "name": "Data Contract",
                "status": "DRAFT", "approval": "explicit-user-confirmation",
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
                "id": "LF-WS-ARCH", "code": "ARCH", "name": "Architecture",
                "mission": "test",
                "epics": [
                    {
                        "id": "LF-EP-ARCH-001", "name": "test",
                        "capabilities": [
                            {
                                "id": "LF-CP-ARCH-001", "name": "test", "phase": "P1",
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
                                            {"task_id": "LF-TSK-ARCH-0001", "type": "hard",
                                             "required_task_version": 1,
                                             "required_change_version": "1.0.0",
                                             "required_result": "PASS"},
                                        ],
                                        "task_version": 1,
                                        "change_version": "1.0.0",
                                    },
                                ],
                            },
                            {
                                "id": "LF-CP-ARCH-002", "name": "test2", "phase": "P2",
                                "seed_tasks": [
                                    {
                                        "id": "LF-TSK-ARCH-0003",
                                        "title": "P2 entry task",
                                        "priority": "P0",
                                        "depends_on": [
                                            {"task_id": "LF-TSK-ARCH-0002", "type": "hard",
                                             "required_task_version": 1,
                                             "required_change_version": "1.0.0",
                                             "required_result": "PASS"},
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
                                            {"task_id": "LF-TSK-ARCH-0003", "type": "hard",
                                             "required_task_version": 1,
                                             "required_change_version": "1.0.0",
                                             "required_result": "PASS"},
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
                "id": "LF-WS-QLT", "code": "QLT", "name": "Quality",
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
        "subagent_protocol": {
            "caller_required_input": list(CANONICAL_CALLER_FIELDS),
            "runner_bound_identity": copy.deepcopy(CANONICAL_RUNNER_IDENTITY),
            "result_required_output": list(CANONICAL_RESULT_FIELDS),
            "current_runner_adapter": {
                "enforced_caller_fields": list(CANONICAL_CALLER_FIELDS),
                **copy.deepcopy(CANONICAL_ADAPTER),
                **copy.deepcopy(CANONICAL_ADAPTER_METADATA),
            },
            "caller_field_schema": copy.deepcopy(CANONICAL_CALLER_FIELD_SCHEMA),
        },
    }


def _base_runtime():
    return _base_policy()


def _validate(data, template=None, policy=None, runtime=None, use_default_runtime=True):
    v = PlanningValidator.from_data(
        data,
        template_data=template if template is not None else _base_template(),
        policy_data=policy if policy is not None else _base_policy(),
        runtime_data=runtime if not use_default_runtime or runtime is not None else _base_runtime(),
    )
    return v.run_all()


class TestCanonicalCallerFieldsExact(unittest.TestCase):
    def test_caller_fields_wrong_order_fails(self):
        policy = _base_policy()
        fields = list(CANONICAL_CALLER_FIELDS)
        fields[0], fields[1] = fields[1], fields[0]
        policy["subagent_protocol"]["caller_required_input"] = fields
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("canonical-caller-fields" in e for e in result["errors"]))

    def test_caller_fields_wrong_name_fails(self):
        policy = _base_policy()
        fields = list(CANONICAL_CALLER_FIELDS)
        fields[0] = "wrong_field"
        policy["subagent_protocol"]["caller_required_input"] = fields
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("canonical-caller-fields" in e for e in result["errors"]))


class TestCanonicalResultFieldsExact(unittest.TestCase):
    def test_result_fields_wrong_order_fails(self):
        policy = _base_policy()
        fields = list(CANONICAL_RESULT_FIELDS)
        fields[0], fields[1] = fields[1], fields[0]
        policy["subagent_protocol"]["result_required_output"] = fields
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("canonical-result-fields" in e for e in result["errors"]))


class TestRunnerIdentityCanonical(unittest.TestCase):
    def test_runner_identity_missing_field_fails(self):
        policy = _base_policy()
        del policy["subagent_protocol"]["runner_bound_identity"]["client"]
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("canonical-runner-identity" in e for e in result["errors"]))

    def test_runner_identity_extra_field_fails(self):
        policy = _base_policy()
        policy["subagent_protocol"]["runner_bound_identity"]["extra"] = {"source": "x"}
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("canonical-runner-identity" in e for e in result["errors"]))

    def test_runner_identity_client_value_drift_fails(self):
        policy = _base_policy()
        policy["subagent_protocol"]["runner_bound_identity"]["client"]["value"] = "wrong"
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("canonical-runner-identity" in e for e in result["errors"]))


class TestAdapterFullSemantic(unittest.TestCase):
    def test_adapter_enforced_fields_not_canonical_fails(self):
        policy = _base_policy()
        policy["subagent_protocol"]["current_runner_adapter"]["enforced_caller_fields"] = ["goal"]
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("canonical-adapter-enforced-fields" in e for e in result["errors"]))

    def test_adapter_flag_false_fails(self):
        policy = _base_policy()
        policy["subagent_protocol"]["current_runner_adapter"]["target_schema_enforced"] = False
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("canonical-adapter-flags" in e for e in result["errors"]))

    def test_adapter_owner_metadata_drift_fails(self):
        policy = _base_policy()
        policy["subagent_protocol"]["current_runner_adapter"]["owner_task"] = "LF-TSK-QLT-9999"
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("canonical-adapter-metadata" in e for e in result["errors"]))


class TestCallerFieldSchemaCanonical(unittest.TestCase):
    def test_schema_value_drift_fails(self):
        policy = _base_policy()
        policy["subagent_protocol"]["caller_field_schema"]["parent_client"] = "wrong-type"
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("canonical-caller-field-schema" in e for e in result["errors"]))

    def test_schema_missing_from_all_sources_fails(self):
        data = _base_valid_catalog()
        template = _base_template()
        policy = _base_policy()
        runtime = _base_runtime()
        contracts = [
            data["orchestration_policy"]["handoff_contract"],
            template["handoff_contract"],
            policy["subagent_protocol"],
            runtime["subagent_protocol"],
        ]
        for contract in contracts:
            del contract["caller_field_schema"]
        result = _validate(
            data, template=template, policy=policy, runtime=runtime
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("canonical-caller-field-schema" in e for e in result["errors"])
        )


class TestHandoffStructure(unittest.TestCase):
    def test_adapter_non_map_in_all_sources_is_structural_failure(self):
        data = _base_valid_catalog()
        template = _base_template()
        policy = _base_policy()
        runtime = _base_runtime()
        contracts = [
            data["orchestration_policy"]["handoff_contract"],
            template["handoff_contract"],
            policy["subagent_protocol"],
            runtime["subagent_protocol"],
        ]
        for contract in contracts:
            contract["current_runner_adapter"] = []
        result = _validate(
            data, template=template, policy=policy, runtime=runtime
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("invalid-structure" in e for e in result["errors"]))

    def test_caller_field_schema_non_map_is_structural_failure(self):
        policy = _base_policy()
        policy["subagent_protocol"]["caller_field_schema"] = []
        result = _validate(_base_valid_catalog(), policy=policy)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("invalid-structure" in e for e in result["errors"]))


class TestStableUniqueIDsRaw(unittest.TestCase):
    def test_duplicate_workstream_fails(self):
        data = _base_valid_catalog()
        data["workstreams"].append(copy.deepcopy(data["workstreams"][0]))
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("duplicate-workstream-id" in e for e in result["errors"]))

    def test_duplicate_epic_fails(self):
        data = _base_valid_catalog()
        data["workstreams"][0]["epics"].append(copy.deepcopy(data["workstreams"][0]["epics"][0]))
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("duplicate-epic-id" in e for e in result["errors"]))

    def test_duplicate_capability_fails(self):
        data = _base_valid_catalog()
        data["workstreams"][0]["epics"][0]["capabilities"].append(
            copy.deepcopy(data["workstreams"][0]["epics"][0]["capabilities"][0])
        )
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("duplicate-capability-id" in e for e in result["errors"]))

    def test_duplicate_gate_fails(self):
        data = _base_valid_catalog()
        data["phase_gates"].append(copy.deepcopy(data["phase_gates"][0]))
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("duplicate-gate-id" in e for e in result["errors"]))

    def test_epic_ids_are_unique_across_workstreams(self):
        data = _base_valid_catalog()
        duplicate = copy.deepcopy(data["workstreams"][0]["epics"][0])
        duplicate["capabilities"] = []
        data["workstreams"][1]["epics"].append(duplicate)
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("duplicate-epic-id" in e for e in result["errors"]))

    def test_capability_ids_are_unique_across_epics(self):
        data = _base_valid_catalog()
        duplicate = copy.deepcopy(
            data["workstreams"][0]["epics"][0]["capabilities"][0]
        )
        duplicate["seed_tasks"] = []
        data["workstreams"][0]["epics"].append(
            {
                "id": "LF-EP-ARCH-002",
                "name": "second epic",
                "capabilities": [duplicate],
            }
        )
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("duplicate-capability-id" in e for e in result["errors"])
        )


class TestCommonPinValuesValid(unittest.TestCase):
    def test_dep_required_task_version_null_fails(self):
        data = _base_valid_catalog()
        task = None
        for ws in data["workstreams"]:
            for epic in ws["epics"]:
                for cap in epic["capabilities"]:
                    for t in cap["seed_tasks"]:
                        if t["id"] == "LF-TSK-ARCH-0002":
                            task = t
        task["depends_on"][0]["required_task_version"] = None
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("dep-invalid-common-pin" in e for e in result["errors"]))

    def test_dep_required_change_version_not_semver_fails(self):
        data = _base_valid_catalog()
        task = None
        for ws in data["workstreams"]:
            for epic in ws["epics"]:
                for cap in epic["capabilities"]:
                    for t in cap["seed_tasks"]:
                        if t["id"] == "LF-TSK-ARCH-0002":
                            task = t
        task["depends_on"][0]["required_change_version"] = "not-semver"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("dep-invalid-common-pin" in e for e in result["errors"]))


class TestPathOwnershipResolution(unittest.TestCase):
    def test_ignored_ide_metadata_is_outside_governance_ownership(self):
        self.assertTrue(_is_excluded(".idea/workspace.xml"))
        self.assertTrue(_is_excluded(".vscode/settings.json"))

    def test_resolution_unknown_value_fails(self):
        data = _base_valid_catalog()
        data["path_ownership"]["resolution"] = "First-match wins"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("invalid-path-ownership-resolution" in e for e in result["errors"]))

    def test_repo_file_no_owner_fails(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"] = []
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("repo-unowned-file" in e for e in result["errors"]))

    def test_nonexistent_overlapping_patterns_are_rejected_statically(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"].extend(
            [
                {
                    "scope": "future.direct",
                    "owner": "LF-WS-ARCH",
                    "proposed_paths": ["future/conflict/*"],
                },
                {
                    "scope": "future.subtree",
                    "owner": "LF-WS-QLT",
                    "proposed_paths": ["future/conflict/*"],
                },
            ]
        )
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("ambiguous-path-ownership" in e for e in result["errors"])
        )

    def test_deeper_literal_subtree_pattern_owns_nested_path(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"].extend(
            [
                {
                    "scope": "depth.outer",
                    "owner": "LF-WS-ARCH",
                    "proposed_paths": ["zz_depth/**"],
                },
                {
                    "scope": "depth.inner",
                    "owner": "LF-WS-QLT",
                    "proposed_paths": ["zz_depth/deep/**"],
                },
            ]
        )
        validator = PlanningValidator.from_data(
            data,
            template_data=_base_template(),
            policy_data=_base_policy(),
            runtime_data=_base_runtime(),
        )
        validator.root = Path("/nonexistent/lexiflow-depth-fixture")
        result = validator.run_all()
        self.assertEqual(result["status"], "PASS", result["errors"])

        self.assertEqual(validator.resolve_owner("zz_depth/a"), "LF-WS-ARCH")
        self.assertEqual(
            validator.resolve_owner("zz_depth/deep/a"), "LF-WS-QLT"
        )

    def test_direct_child_pattern_does_not_match_grandchild(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"] = [
            {
                "scope": "child.only",
                "owner": "LF-WS-ARCH",
                "proposed_paths": ["zz_child/*"],
            }
        ]
        validator = PlanningValidator.from_data(data)
        self.assertEqual(validator.resolve_owner("zz_child/a"), "LF-WS-ARCH")
        self.assertIsNone(validator.resolve_owner("zz_child/a/b"))

    def test_intermediate_wildcard_is_invalid(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"].append(
            {
                "scope": "invalid.intermediate-wildcard",
                "owner": "LF-WS-ARCH",
                "proposed_paths": ["zz_invalid/*/leaf"],
            }
        )
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("invalid-path-pattern" in e for e in result["errors"]))

    def test_terminal_globstar_requires_a_descendant(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"] = [
            {
                "scope": "descendants.only",
                "owner": "LF-WS-ARCH",
                "proposed_paths": ["zz_desc/**"],
            }
        ]
        validator = PlanningValidator.from_data(data)
        self.assertIsNone(validator.resolve_owner("zz_desc"))
        self.assertEqual(validator.resolve_owner("zz_desc/a"), "LF-WS-ARCH")
        self.assertEqual(
            validator.resolve_owner("zz_desc/a/b"), "LF-WS-ARCH"
        )

    def test_exact_then_direct_then_subtree_precedence(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"] = [
            {
                "scope": "order.subtree",
                "owner": "LF-WS-ARCH",
                "proposed_paths": ["zz_order/**"],
            },
            {
                "scope": "order.direct",
                "owner": "LF-WS-QLT",
                "proposed_paths": ["zz_order/*"],
            },
            {
                "scope": "order.exact",
                "owner": "LF-WS-ARCH",
                "proposed_paths": ["zz_order/item"],
            },
        ]
        validator = PlanningValidator.from_data(data)
        self.assertEqual(validator.resolve_owner("zz_order/item"), "LF-WS-ARCH")
        self.assertEqual(validator.resolve_owner("zz_order/other"), "LF-WS-QLT")
        self.assertEqual(
            validator.resolve_owner("zz_order/a/b"), "LF-WS-ARCH"
        )

    def test_deeper_literal_prefix_has_precedence(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"] = [
            {
                "scope": "prefix.outer",
                "owner": "LF-WS-ARCH",
                "proposed_paths": ["zz_prefix/**"],
            },
            {
                "scope": "prefix.inner",
                "owner": "LF-WS-QLT",
                "proposed_paths": ["zz_prefix/inner/**"],
            },
        ]
        validator = PlanningValidator.from_data(data)
        self.assertEqual(
            validator.resolve_owner("zz_prefix/inner/file"), "LF-WS-QLT"
        )


class TestStructureValidation(unittest.TestCase):
    def test_workstreams_not_list_fails(self):
        data = _base_valid_catalog()
        data["workstreams"] = "not-a-list"
        result = _validate(data)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("invalid-structure" in e for e in result["errors"]))

    def test_phase_gates_not_list_fails(self):
        data = _base_valid_catalog()
        data["phase_gates"] = "not-a-list"
        result = _validate(data)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("invalid-structure" in e for e in result["errors"]))

    def test_malformed_phase_value_fails(self):
        data = _base_valid_catalog()
        data["workstreams"][0]["epics"][0]["capabilities"][0]["phase"] = "INVALID"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("invalid-phase" in e for e in result["errors"]))

    def test_epics_not_list_fails(self):
        data = _base_valid_catalog()
        data["workstreams"][0]["epics"] = "not-a-list"
        result = _validate(data)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("invalid-structure" in e for e in result["errors"]))

    def test_capabilities_not_list_fails(self):
        data = _base_valid_catalog()
        data["workstreams"][0]["epics"][0]["capabilities"] = "not-a-list"
        result = _validate(data)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("invalid-structure" in e for e in result["errors"]))

    def test_seed_tasks_not_list_fails(self):
        data = _base_valid_catalog()
        data["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"] = "not-a-list"
        result = _validate(data)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("invalid-structure" in e for e in result["errors"]))

    def test_path_scopes_not_list_fails(self):
        data = _base_valid_catalog()
        data["path_ownership"]["scopes"] = "not-a-list"
        result = _validate(data)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("invalid-structure" in e for e in result["errors"]))

    def test_phase_entry_validation_not_map_fails(self):
        data = _base_valid_catalog()
        data["phase_entry_validation"] = "not-a-map"
        result = _validate(data)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("invalid-structure" in e for e in result["errors"]))

    def test_phase_entry_registry_not_map_fails(self):
        data = _base_valid_catalog()
        data["phase_entry_validation"]["entry_tasks"] = "not-a-map"
        result = _validate(data)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("invalid-structure" in e for e in result["errors"]))

    def test_phase_entry_task_ids_not_list_fails(self):
        data = _base_valid_catalog()
        data["phase_entry_validation"]["entry_tasks"]["P2"] = "not-a-list"
        result = _validate(data)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("invalid-structure" in e for e in result["errors"]))

    def test_depends_on_not_list_fails(self):
        data = _base_valid_catalog()
        data["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][0][
            "depends_on"
        ] = {}
        result = _validate(data)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("invalid-structure" in e for e in result["errors"]))

    def test_produced_contracts_not_list_fails(self):
        data = _base_valid_catalog()
        data["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][0][
            "produced_contracts"
        ] = "not-a-list"
        result = _validate(data)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("invalid-structure" in e for e in result["errors"]))

    def test_check_exception_is_a_fail_result(self):
        validator = PlanningValidator.from_data(
            _base_valid_catalog(),
            template_data=_base_template(),
            policy_data=_base_policy(),
            runtime_data=_base_runtime(),
        )

        def raise_unexpected_error():
            raise RuntimeError("probe checker failure")

        validator.check_versions = raise_unexpected_error
        result = validator.run_all()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("check-exception" in e for e in result["errors"]))


class TestCatalogPolicyAndGateClosure(unittest.TestCase):
    def test_id_policy_drift_is_blocked(self):
        data = _base_valid_catalog()
        data["id_policy"]["task"] = "ARBITRARY-TASK-ID"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("invalid-id-policy" in e for e in result["errors"]))

    def test_gate_phase_is_validated(self):
        data = _base_valid_catalog()
        data["phase_gates"][0]["phase"] = "PX"
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("invalid-phase" in e for e in result["errors"]))

    def test_g1_exit_must_reach_each_p0_entry(self):
        data = _base_valid_catalog()
        data["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][1][
            "depends_on"
        ] = []
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(
            any("gate-exit-p0-ancestry-gap" in e for e in result["errors"])
        )


class TestGateEntryTasksConsistency(unittest.TestCase):
    def test_gate_entry_tasks_mismatch_fails(self):
        data = _base_valid_catalog()
        data["phase_gates"][1]["entry_tasks"] = ["LF-TSK-ARCH-9999"]
        result = _validate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("gate-entry-tasks-mismatch" in e for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()
