"""Acceptance fixtures for the pure QLT-0005 dispatch preflight."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

from scripts.gates.dispatch_preflight import (
    ACTIVE_SCHEMA,
    CANDIDATE_SCHEMA,
    CATALOG_SCHEMA,
    MATCHER_VERSION,
    check_dispatch,
    parse_path_expression,
    path_contains,
    paths_intersect,
)


def _sha(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(data).hexdigest()


def _task(task_id, allowed, contracts=None, owner="LF-WS-QLT", scopes=None):
    claims = [{"path": path, "mode": "write", "owner": owner} for path in allowed]
    return {
        "schema_version": "lexiflow.task.v1",
        "identity": {
            "task_id": task_id,
            "task_version": 1,
            "change_version": "1.0.0",
            "task_source": "fixture",
        },
        "ownership": {"primary_owner": owner, "contract_owner": owner},
        "scope": {
            "logical_scopes": scopes or ["quality-gates"],
            "allowed_files": allowed,
            "file_claims": claims,
        },
        "produced_contracts": contracts or [],
    }


def _catalog_task(task):
    return {
        "task_id": task["identity"]["task_id"],
        "task_version": task["identity"]["task_version"],
        "change_version": task["identity"]["change_version"],
        "task_source": task["identity"]["task_source"],
        "descriptor_sha256": _sha(task),
        "primary_owner": task["ownership"]["primary_owner"],
        "contract_owner": task["ownership"]["contract_owner"],
        "logical_scopes": copy.deepcopy(task["scope"]["logical_scopes"]),
        "allowed_files": copy.deepcopy(task["scope"]["allowed_files"]),
        "file_claims": copy.deepcopy(task["scope"]["file_claims"]),
        "produced_contracts": copy.deepcopy(task["produced_contracts"]),
    }


class Fixture:
    def __init__(self):
        self.candidate_task = _task(
            "LF-TSK-QLT-0005",
            ["scripts/gates/dispatch_preflight.py", "tests/gates/test_dispatch_preflight.py"],
        )
        self.active_task = _task(
            "LF-TSK-QLT-0009",
            ["scripts/gates/executor.py", "tests/gates/test_gate_executor.py"],
        )
        self.candidate = {
            "schema_version": CANDIDATE_SCHEMA,
            "task": copy.deepcopy(self.candidate_task),
            "handoff": {
                "allowed_files": "scripts/gates/dispatch_preflight.py, tests/gates/test_dispatch_preflight.py",
                "forbidden_files": "planning/**, harness/**",
            },
        }
        self.catalog = {
            "schema_version": CATALOG_SCHEMA,
            "matcher_version": MATCHER_VERSION,
            "repo_root_identity": "fixture-repo-root",
            "catalog_source": {"locator": "planning/workstreams.yaml", "sha256": "1" * 64},
            "tasks": [_catalog_task(self.candidate_task), _catalog_task(self.active_task)],
            "path_ownership": {
                "scopes": [
                    {
                        "scope_id": "quality-gates",
                        "owner": "LF-WS-QLT",
                        "proposed_paths": ["scripts/gates/**", "tests/gates/**"],
                    }
                ]
            },
            "public_contract_producers": [],
            "path_metadata": {
                "repo_root_identity": "fixture-repo-root",
                "source_sha256": "2" * 64,
                "scan_complete": True,
                "symlink_paths": [],
                "casefold_collisions": [],
            },
        }
        self.active = {
            "schema_version": ACTIVE_SCHEMA,
            "snapshot_source": "fixture-scheduler",
            "snapshot_sha256": "3" * 64,
            "selected_at": "2026-09-16T01:00:00Z",
            "complete_for_dispatch_window": True,
            "nonterminal_only": True,
            "instances": [
                {
                    "task_id": "LF-TSK-QLT-0009",
                    "task_version": 1,
                    "change_version": "1.0.0",
                    "task_descriptor_sha256": _sha(self.active_task),
                    "allowed_files": "scripts/gates/executor.py, tests/gates/test_gate_executor.py",
                    "instance_id": "qoder-run-0009",
                    "client": "qoder",
                    "parent_session_id": "parent-session",
                }
            ],
        }


class PathLanguageTests(unittest.TestCase):
    def test_frozen_intersection_truth_table(self):
        rows = [
            ("dir/x", "dir/*", True),
            ("dir/x", "dir/**", True),
            ("dir/x", "dir/x/**", False),
            ("dir/*", "dir/x/**", False),
            ("dir/*", "dir/**", True),
            ("dir/x/*", "dir/**", True),
            ("dir/x/**", "dir/**", True),
            ("foo/**", "foobar/**", False),
        ]
        for left, right, expected in rows:
            with self.subTest(left=left, right=right):
                self.assertEqual(paths_intersect(parse_path_expression(left), parse_path_expression(right)), expected)

    def test_frozen_containment_examples(self):
        self.assertTrue(path_contains(parse_path_expression("dir/**"), parse_path_expression("dir/*")))
        self.assertTrue(path_contains(parse_path_expression("dir/*"), parse_path_expression("dir/x")))
        self.assertFalse(path_contains(parse_path_expression("dir/*"), parse_path_expression("dir/x/**")))

    def test_unsupported_or_unsafe_paths_fail_closed(self):
        bad = ["/abs", "../escape", "a\\b", "a/*/b", "a/[x]", "a//b", "a/", "**"]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_path_expression(value)


class DispatchDecisionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()

    def result(self):
        return check_dispatch(self.fixture.candidate, self.fixture.catalog, self.fixture.active)

    def test_disjoint_canonical_writers_pass(self):
        result = self.result()
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["diagnostics"], [])
        self.assertEqual(result["candidate"]["task_id"], "LF-TSK-QLT-0005")
        self.assertEqual(result["active_snapshot"]["instance_count"], 1)
        self.assertEqual(result, check_dispatch(self.fixture.candidate, self.fixture.catalog, self.fixture.active))

    def test_matching_candidate_and_catalog_still_reject_invalid_numeric_prerelease(self):
        task = copy.deepcopy(self.fixture.candidate_task)
        task["identity"]["change_version"] = "1.0.0-01"
        self.fixture.candidate["task"] = task
        self.fixture.catalog["tasks"][0] = _catalog_task(task)
        result = self.result()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(result["diagnostics"][0]["code"], "input-incomplete")

    def test_zero_and_alphanumeric_prerelease_identifiers_remain_valid(self):
        for change_version in ("1.0.0-0", "1.0.0-alpha.0"):
            with self.subTest(change_version=change_version):
                fixture = Fixture()
                task = copy.deepcopy(fixture.candidate_task)
                task["identity"]["change_version"] = change_version
                fixture.candidate["task"] = task
                fixture.catalog["tasks"][0] = _catalog_task(task)
                result = check_dispatch(fixture.candidate, fixture.catalog, fixture.active)
                self.assertEqual(result["status"], "PASS", result)

    def test_write_overlap_is_blocked_from_canonical_active_claims(self):
        active_entry = self.fixture.catalog["tasks"][1]
        active_entry["allowed_files"] = ["scripts/gates/**"]
        active_entry["file_claims"] = [{"path": "scripts/gates/**", "mode": "write", "owner": "LF-WS-QLT"}]
        self.fixture.active["instances"][0]["allowed_files"] = "scripts/gates/**"
        result = self.result()
        self.assertEqual(result["status"], "BLOCKED", result)
        self.assertIn("write-overlap", {item["code"] for item in result["diagnostics"]})

    def test_active_cannot_narrow_its_catalog_claim(self):
        self.fixture.active["instances"][0]["allowed_files"] = "scripts/gates/executor.py"
        result = self.result()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["diagnostics"][0]["code"], "active-snapshot-invalid")

    def test_missing_active_completeness_declaration_fails(self):
        del self.fixture.active["complete_for_dispatch_window"]
        result = self.result()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["diagnostics"][0]["code"], "active-snapshot-declaration-missing")

    def test_repo_root_identity_mismatch_fails(self):
        self.fixture.catalog["path_metadata"]["repo_root_identity"] = "different-root"
        result = self.result()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["diagnostics"][0]["code"], "input-incomplete")

    def test_descriptor_drift_fails(self):
        self.fixture.candidate["task"]["scope"]["logical_scopes"].append("drift")
        result = self.result()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["diagnostics"][0]["code"], "task-version-mismatch")

    def test_missing_task_source_and_contract_declaration_fail(self):
        del self.fixture.candidate["task"]["identity"]["task_source"]
        self.fixture.catalog["tasks"][0]["descriptor_sha256"] = _sha(self.fixture.candidate["task"])
        result = self.result()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["diagnostics"][0]["code"], "input-incomplete")
        self.fixture = Fixture()
        del self.fixture.candidate["task"]["produced_contracts"]
        self.fixture.catalog["tasks"][0]["descriptor_sha256"] = _sha(self.fixture.candidate["task"])
        result = self.result()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["diagnostics"][0]["code"], "input-incomplete")

    def test_missing_owner_is_an_input_failure_not_a_dispatch_block(self):
        del self.fixture.candidate["task"]["ownership"]["primary_owner"]
        self.fixture.catalog["tasks"][0]["descriptor_sha256"] = _sha(self.fixture.candidate["task"])
        result = self.result()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(result["diagnostics"][0]["code"], "input-incomplete")

    def test_handoff_and_claim_mismatch_is_blocked(self):
        self.fixture.candidate["handoff"]["allowed_files"] = "scripts/gates/dispatch_preflight.py"
        result = self.result()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["diagnostics"][0]["code"], "claim-mismatch")

    def test_owner_crossing_is_blocked(self):
        task = self.fixture.candidate_task
        task["scope"]["allowed_files"] = ["scripts/gates/**"]
        task["scope"]["file_claims"] = [{"path": "scripts/gates/**", "mode": "write", "owner": "LF-WS-QLT"}]
        self.fixture.candidate["task"] = copy.deepcopy(task)
        self.fixture.candidate["handoff"]["allowed_files"] = "scripts/gates/**"
        self.fixture.catalog["tasks"][0] = _catalog_task(task)
        self.fixture.catalog["path_ownership"]["scopes"].append(
            {
                "scope_id": "security-gates",
                "owner": "LF-WS-SEC",
                "proposed_paths": ["scripts/gates/security/**"],
            }
        )
        result = self.result()
        self.assertEqual(result["status"], "BLOCKED", result)
        self.assertIn("owner-crossing", {item["code"] for item in result["diagnostics"]})

    def test_symlink_and_case_collisions_fail(self):
        self.fixture.catalog["path_metadata"]["symlink_paths"] = ["scripts/gates"]
        result = self.result()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["diagnostics"][0]["code"], "path-symlink-unsafe")
        self.fixture = Fixture()
        self.fixture.catalog["path_metadata"]["casefold_collisions"] = [["A", "a"]]
        result = self.result()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["diagnostics"][0]["code"], "path-case-ambiguous")

    def test_owner_pattern_casefold_overlap_fails(self):
        self.fixture.catalog["path_ownership"]["scopes"].append(
            {
                "scope_id": "case-alias",
                "owner": "LF-WS-SEC",
                "proposed_paths": ["Scripts/Gates/**"],
            }
        )
        result = self.result()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["diagnostics"][0]["code"], "path-case-ambiguous")

    def test_candidate_casefold_alias_to_owner_fails(self):
        task = _task("LF-TSK-QLT-0005", ["Scripts/Gates/dispatch_preflight.py"])
        self.fixture.candidate["task"] = task
        self.fixture.candidate["handoff"]["allowed_files"] = "Scripts/Gates/dispatch_preflight.py"
        self.fixture.catalog["tasks"][0] = _catalog_task(task)
        result = self.result()
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("path-case-ambiguous", {item["code"] for item in result["diagnostics"]})

    def test_contract_writer_conflict_is_blocked(self):
        contract = {"name": "gate-api", "version": "1.0.0"}
        candidate_task = _task(
            "LF-TSK-QLT-0005", ["contracts/gate/candidate.json"], [contract], scopes=["contracts"]
        )
        active_task = _task(
            "LF-TSK-QLT-0009", ["contracts/gate/active.json"], [contract], scopes=["contracts"]
        )
        self.fixture.candidate_task = candidate_task
        self.fixture.active_task = active_task
        self.fixture.candidate["task"] = copy.deepcopy(candidate_task)
        self.fixture.candidate["handoff"]["allowed_files"] = "contracts/gate/candidate.json"
        self.fixture.catalog["tasks"] = [_catalog_task(candidate_task), _catalog_task(active_task)]
        self.fixture.catalog["path_ownership"]["scopes"] = [
            {"scope_id": "contracts", "owner": "LF-WS-QLT", "proposed_paths": ["contracts/**"]}
        ]
        self.fixture.catalog["public_contract_producers"] = [
            {
                "name": "gate-api",
                "version": "1.0.0",
                "task_id": "LF-TSK-QLT-0005",
                "task_version": 1,
                "change_version": "1.0.0",
                "paths": ["contracts/gate/**"],
            }
        ]
        self.fixture.active["instances"][0].update(
            {
                "task_descriptor_sha256": _sha(active_task),
                "allowed_files": "contracts/gate/active.json",
            }
        )
        result = self.result()
        self.assertEqual(result["status"], "BLOCKED", result)
        self.assertIn("contract-writer-conflict", {item["code"] for item in result["diagnostics"]})

    def test_same_registered_contract_from_two_canonical_tasks_is_blocked(self):
        # Active and candidate describe different writers for the same name.
        # The frozen registry is a single producer declaration, while preflight
        # still must stop a simultaneous writer with a BLOCKED conflict.
        contract = {"name": "gate-api", "version": "1.0.0"}
        self.fixture.candidate_task["produced_contracts"] = [contract]
        self.fixture.active_task["produced_contracts"] = [contract]
        self.fixture.candidate["task"] = copy.deepcopy(self.fixture.candidate_task)
        self.fixture.catalog["tasks"] = [_catalog_task(self.fixture.candidate_task), _catalog_task(self.fixture.active_task)]
        self.fixture.catalog["public_contract_producers"] = [
            {
                "name": "gate-api", "version": "1.0.0",
                "task_id": "LF-TSK-QLT-0005", "task_version": 1, "change_version": "1.0.0", "paths": [],
            }
        ]
        self.fixture.active["instances"][0]["task_descriptor_sha256"] = _sha(self.fixture.active_task)
        result = self.result()
        self.assertEqual(result["status"], "BLOCKED")
        codes = {item["code"] for item in result["diagnostics"]}
        self.assertIn("contract-writer-conflict", codes)

    def test_invalid_active_contract_declaration_fails_even_with_same_name_conflict(self):
        candidate_contract = {"name": "gate-api", "version": "1.0.0"}
        active_contract = {"name": "gate-api", "version": "2.0.0"}
        self.fixture.candidate_task["produced_contracts"] = [candidate_contract]
        self.fixture.active_task["produced_contracts"] = [active_contract]
        self.fixture.candidate["task"] = copy.deepcopy(self.fixture.candidate_task)
        self.fixture.catalog["tasks"] = [
            _catalog_task(self.fixture.candidate_task), _catalog_task(self.fixture.active_task),
        ]
        self.fixture.catalog["public_contract_producers"] = [{
            "name": "gate-api", "version": "1.0.0",
            "task_id": "LF-TSK-QLT-0005", "task_version": 1,
            "change_version": "1.0.0", "paths": [],
        }]
        self.fixture.active["instances"][0]["task_descriptor_sha256"] = _sha(self.fixture.active_task)
        result = self.result()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertIn("contract-writer-unknown", {item["code"] for item in result["diagnostics"]})

    def test_active_unknown_field_and_client_fail_closed(self):
        self.fixture.active["instances"][0]["file_claims"] = []
        result = self.result()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["diagnostics"][0]["code"], "active-snapshot-invalid")

    def test_candidate_active_casefold_alias_fails(self):
        candidate_task = _task("LF-TSK-QLT-0005", ["scripts/gates/Foo.py"])
        active_task = _task("LF-TSK-QLT-0009", ["scripts/gates/foo.py"])
        self.fixture.candidate["task"] = candidate_task
        self.fixture.candidate["handoff"]["allowed_files"] = "scripts/gates/Foo.py"
        self.fixture.catalog["tasks"] = [_catalog_task(candidate_task), _catalog_task(active_task)]
        self.fixture.active["instances"][0]["task_descriptor_sha256"] = _sha(active_task)
        self.fixture.active["instances"][0]["allowed_files"] = "scripts/gates/foo.py"
        result = self.result()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertIn("path-case-ambiguous", {item["code"] for item in result["diagnostics"]})

    def test_invalid_forbidden_item_and_proven_overlap_fail_in_fixed_order(self):
        task = _task("LF-TSK-QLT-0005", ["scripts/gates/dispatch_preflight.py"])
        self.fixture.candidate["task"] = task
        self.fixture.candidate["handoff"]["allowed_files"] = "scripts/gates/dispatch_preflight.py"
        self.fixture.candidate["handoff"]["forbidden_files"] = "other/**/*.txt, scripts/gates/dispatch_preflight.py"
        self.fixture.catalog["tasks"][0] = _catalog_task(task)
        result = self.result()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(
            [(item["status"], item["code"]) for item in result["diagnostics"]],
            [("FAIL", "path-expression-unsupported"), ("BLOCKED", "forbidden-overlap")],
        )

    def test_redundant_forbidden_item_does_not_hide_proven_overlap(self):
        self.fixture.candidate["handoff"]["forbidden_files"] = (
            "scripts/gates/**, scripts/gates/dispatch_preflight.py"
        )
        result = self.result()
        self.assertEqual(result["status"], "FAIL", result)
        codes = [(item["status"], item["code"]) for item in result["diagnostics"]]
        self.assertIn(("FAIL", "path-expression-invalid"), codes)
        self.assertIn(("BLOCKED", "forbidden-overlap"), codes)

    def test_forbidden_and_symlink_casefold_aliases_fail(self):
        task = _task("LF-TSK-QLT-0005", ["scripts/gates/Foo.py"])
        self.fixture.candidate["task"] = task
        self.fixture.candidate["handoff"]["allowed_files"] = "scripts/gates/Foo.py"
        self.fixture.candidate["handoff"]["forbidden_files"] = "scripts/gates/foo.py"
        self.fixture.catalog["tasks"][0] = _catalog_task(task)
        result = self.result()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertIn("path-case-ambiguous", {item["code"] for item in result["diagnostics"]})
        self.fixture = Fixture()
        self.fixture.catalog["path_metadata"]["symlink_paths"] = ["Scripts/Gates/dispatch_preflight.py"]
        result = self.result()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(result["diagnostics"][0]["code"], "path-case-ambiguous")

    def test_wrong_contract_name_cannot_cover_registered_path(self):
        task = _task(
            "LF-TSK-QLT-0005", ["contracts/gate/api.json"],
            [{"name": "unrelated", "version": "1.0.0"}], scopes=["contracts"],
        )
        self.fixture.candidate["task"] = task
        self.fixture.candidate["handoff"]["allowed_files"] = "contracts/gate/api.json"
        self.fixture.catalog["tasks"][0] = _catalog_task(task)
        self.fixture.catalog["path_ownership"]["scopes"] = [
            {"scope_id": "contracts", "owner": "LF-WS-QLT", "proposed_paths": ["contracts/**"]}
        ]
        self.fixture.catalog["public_contract_producers"] = [
            {"name": "gate-api", "version": "1.0.0", "task_id": "LF-TSK-QLT-0005", "task_version": 1, "change_version": "1.0.0", "paths": ["contracts/gate/**"]},
            {"name": "unrelated", "version": "1.0.0", "task_id": "LF-TSK-QLT-0005", "task_version": 1, "change_version": "1.0.0", "paths": ["contracts/unrelated/**"]},
        ]
        self.fixture.active["instances"] = []
        result = self.result()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertIn("contract-writer-unknown", {item["code"] for item in result["diagnostics"]})
        self.fixture = Fixture()
        self.fixture.active["instances"][0]["client"] = "other"
        result = self.result()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["diagnostics"][0]["code"], "active-snapshot-invalid")

    def test_cli_maps_pass_blocked_and_malformed_to_stable_exit_codes(self):
        with tempfile.TemporaryDirectory() as root:
            paths = {}
            for name, value in (("candidate", self.fixture.candidate), ("catalog", self.fixture.catalog), ("active", self.fixture.active)):
                path = os.path.join(root, f"{name}.json")
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(value, handle)
                paths[name] = path
            command = [
                sys.executable, "scripts/gates/dispatch_preflight.py", "check",
                "--candidate", paths["candidate"], "--catalog", paths["catalog"], "--active", paths["active"],
            ]
            completed = subprocess.run(command, cwd=os.getcwd(), capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["status"], "PASS")
            with open(paths["candidate"], "w", encoding="utf-8") as handle:
                handle.write("{")
            completed = subprocess.run(command, cwd=os.getcwd(), capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(json.loads(completed.stdout)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
