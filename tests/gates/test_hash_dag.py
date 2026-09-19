import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.gates.hash_dag import (
    HashDagError,
    _extract_edges,
    _parse_json,
    verify_hash_dag,
    verify_hash_dag_result,
)
from scripts.gates.receipt_store import canonical_json_bytes, sha256_bytes
from scripts.gates.task_source import task_source_descriptor


class GraphFixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.nodes = {}

    def cleanup(self):
        self.temp.cleanup()

    def write_bytes(self, locator, content):
        path = self.root / locator
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.nodes[locator] = content
        return {"locator": locator, "sha256": sha256_bytes(content)}

    def write_json(self, locator, value):
        return self.write_bytes(locator, canonical_json_bytes(value))

    def receipt(self, run_id, started, finished, prior=None, identity="root-leaf"):
        leaf = self.write_bytes(f"tmp/quality/runs/{run_id}/leaf.txt", run_id.encode())
        manifest = self.write_json(
            f"tmp/quality/runs/{run_id}/artifact-manifest.json",
            {
                "schema_version": "lexiflow.gate-artifact-manifest.v1", "run_id": run_id,
                "artifacts": [{"identity": identity, "kind": "test", **leaf}],
            },
        )
        plan_value = {
            "schema_version": "lexiflow.gate-plan.v1", "receipt_kind": "TASK_VALIDATION",
            "task": {"task_id": "LF-TSK-QLT-0010"}, "content_fingerprint": "",
        }
        projected = {key: value for key, value in plan_value.items() if key != "content_fingerprint"}
        plan_value["content_fingerprint"] = sha256_bytes(canonical_json_bytes(projected))
        plan = self.write_json(f"tmp/quality/runs/{run_id}/plan.json", plan_value)
        kind = "INDEPENDENT_REVIEW" if prior else "TASK_VALIDATION"
        receipt = {
            "schema_version": "lexiflow.gate-receipt.v1", "receipt_kind": kind, "run_id": run_id,
            "plan": {**plan, "content_fingerprint": plan_value["content_fingerprint"]},
            "artifact_manifest": manifest, "started_at": started, "finished_at": finished,
            "result": "PASS", "completeness": {"status": "PASS"},
        }
        if prior:
            receipt["subject_validation"] = {**prior, "result": "PASS"}
        return self.write_json(f"tmp/quality/runs/{run_id}/receipt.json", receipt)


class TestHashDag(unittest.TestCase):
    @staticmethod
    def _catalog(task_title="target", extra_task_title="unrelated"):
        return f"""schema_version: lexiflow.workstreams.v1
workstreams:
  - id: LF-WS-QLT
    epics:
      - capabilities:
          - seed_tasks:
              - id: LF-TSK-QLT-0001
                title: {task_title}
              - id: LF-TSK-QLT-0002
                title: {extra_task_title}
""".encode()

    def test_task_projection_is_verified_without_becoming_raw_catalog_edge(self):
        fixture = GraphFixture()
        self.addCleanup(fixture.cleanup)
        catalog = fixture.write_bytes("planning/workstreams.yaml", self._catalog())
        projected = task_source_descriptor(fixture.root, "LF-TSK-QLT-0001")
        leaf = fixture.write_bytes("tmp/leaf.txt", b"subject")
        root = fixture.write_json("tmp/evidence.json", {
            "schema_version": "lexiflow.explicit-evidence-packet.v1",
            "publication_id": "publication",
            "task": {"task_id": "LF-TSK-QLT-0001", "task_source": projected},
            "subject": leaf,
        })
        self.assertEqual(verify_hash_dag(str(fixture.root), root)["result"], "PASS")

        # A different task's catalog edit does not stale this task projection.
        (fixture.root / catalog["locator"]).write_bytes(self._catalog(extra_task_title="changed"))
        self.assertEqual(verify_hash_dag(str(fixture.root), root)["result"], "PASS")

        # Editing the named task does stale it, even though no raw catalog edge
        # is followed for the projected descriptor.
        (fixture.root / catalog["locator"]).write_bytes(self._catalog(task_title="changed"))
        with self.assertRaisesRegex(HashDagError, "task source projection is stale"):
            verify_hash_dag(str(fixture.root), root)

    def test_plan_keeps_explicit_raw_catalog_consumed_input_as_byte_edge(self):
        fixture = GraphFixture()
        self.addCleanup(fixture.cleanup)
        catalog = fixture.write_bytes("planning/workstreams.yaml", self._catalog())
        projected = task_source_descriptor(fixture.root, "LF-TSK-QLT-0001")
        root = fixture.write_json("tmp/plan.json", {
            "schema_version": "lexiflow.gate-plan.v1",
            "task": {"task_id": "LF-TSK-QLT-0001", "task_source": projected},
            "consumed_inputs": [catalog],
        })
        self.assertEqual(verify_hash_dag(str(fixture.root), root)["result"], "PASS")
        (fixture.root / catalog["locator"]).write_bytes(self._catalog(extra_task_title="changed"))
        with self.assertRaisesRegex(HashDagError, "changed node bytes: planning/workstreams.yaml"):
            verify_hash_dag(str(fixture.root), root)

    def test_historical_verification_preserves_frozen_plan_inputs_only(self):
        fixture = GraphFixture()
        self.addCleanup(fixture.cleanup)
        document = fixture.write_bytes("docs/control.md", b"issued input")
        evidence = fixture.write_bytes("tmp/evidence.txt", b"immutable evidence")
        root = fixture.write_json("tmp/plan.json", {
            "schema_version": "lexiflow.gate-plan.v1",
            "task": {"task_id": "LF-TSK-QLT-0001"},
            "consumed_inputs": [document],
            "subject": {"raw_artifacts": {"stdout": evidence}},
        })
        (fixture.root / document["locator"]).write_bytes(b"next phase input")
        with self.assertRaisesRegex(HashDagError, "changed node bytes: docs/control.md"):
            verify_hash_dag(str(fixture.root), root)
        self.assertEqual(
            verify_hash_dag(
                str(fixture.root), root, historical_workspace_inputs=True,
            )["result"],
            "PASS",
        )
        (fixture.root / evidence["locator"]).write_bytes(b"mutated evidence")
        with self.assertRaisesRegex(HashDagError, "changed node bytes: tmp/evidence.txt"):
            verify_hash_dag(
                str(fixture.root), root, historical_workspace_inputs=True,
            )

    def test_historical_verification_does_not_reread_receipt_workspace_metadata(self):
        fixture = GraphFixture()
        self.addCleanup(fixture.cleanup)
        registry = fixture.write_bytes("harness/gate-check-registry.yaml", b"issued registry")
        policy = fixture.write_bytes("harness/agent-policy.manifest.yaml", b"issued policy")
        root = fixture.receipt(
            "44444444-4444-4444-8444-444444444444",
            "2026-09-16T00:00:00Z", "2026-09-16T00:00:01Z",
        )
        receipt = json.loads((fixture.root / root["locator"]).read_bytes())
        receipt["current_inputs"] = {"registry": registry, "policy": policy}
        root = self._store_receipt(fixture, root["locator"], receipt)
        (fixture.root / registry["locator"]).write_bytes(b"next-phase registry")
        (fixture.root / policy["locator"]).write_bytes(b"next-phase policy")
        with self.assertRaisesRegex(HashDagError, "changed node bytes: harness/(agent-policy\\.manifest|gate-check-registry\\.yaml)"):
            verify_hash_dag(str(fixture.root), root)
        self.assertEqual(
            verify_hash_dag(
                str(fixture.root), root, historical_workspace_inputs=True,
            )["result"],
            "PASS",
        )
        manifest = json.loads((fixture.root / receipt["artifact_manifest"]["locator"]).read_bytes())
        leaf = manifest["artifacts"][0]
        (fixture.root / leaf["locator"]).write_bytes(b"tampered immutable evidence")
        with self.assertRaisesRegex(HashDagError, "changed node bytes: tmp/quality/runs"):
            verify_hash_dag(
                str(fixture.root), root, historical_workspace_inputs=True,
            )

    def test_repository_schema_is_hash_bound_opaque_source(self):
        fixture = GraphFixture()
        self.addCleanup(fixture.cleanup)
        schema = fixture.write_json("harness/example.schema.json", {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {"locator": {"type": "string"}, "sha256": {"type": "string"}},
        })
        root = fixture.write_json("tmp/source-proof.json", {"source_contract": schema})
        result = verify_hash_dag(str(fixture.root), root)
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["nodes_verified"], 2)
        (fixture.root / schema["locator"]).write_bytes(b"changed")
        with self.assertRaisesRegex(HashDagError, "changed node bytes"):
            verify_hash_dag(str(fixture.root), root)

    def test_task_registry_entry_projection_ignores_unrelated_registry_edits(self):
        fixture = GraphFixture()
        self.addCleanup(fixture.cleanup)
        task_id = "LF-TSK-QLT-0010"

        def entry(subject, command):
            value = {"subject_task_id": subject, "command": command}
            return {**value, "entry_hash": sha256_bytes(canonical_json_bytes(value))}

        selected = entry(task_id, "selected")
        registry_locator = "harness/gate-check-registry.yaml"
        fixture.write_json(registry_locator, {
            "schema_version": "lexiflow.gate-check-registry.v1",
            "entries": [selected, entry("LF-TSK-QLT-0001", "other")],
        })
        root = fixture.receipt(
            "55555555-5555-4555-8555-555555555555",
            "2026-09-16T00:00:00Z", "2026-09-16T00:00:01Z",
        )
        receipt = json.loads((fixture.root / root["locator"]).read_bytes())
        receipt["task"] = {"task_id": task_id}
        receipt["current_inputs"] = {
            "registry": {"locator": registry_locator, "sha256": selected["entry_hash"]},
        }
        root = self._store_receipt(fixture, root["locator"], receipt)
        self.assertEqual(verify_hash_dag(str(fixture.root), root)["result"], "PASS")

        # A different Task's entry is not part of this Task's projection.
        fixture.write_json(registry_locator, {
            "schema_version": "lexiflow.gate-check-registry.v1",
            "entries": [selected, entry("LF-TSK-QLT-0001", "changed")],
        })
        self.assertEqual(verify_hash_dag(str(fixture.root), root)["result"], "PASS")

        # A changed named entry must still invalidate the receipt.
        fixture.write_json(registry_locator, {
            "schema_version": "lexiflow.gate-check-registry.v1",
            "entries": [entry(task_id, "changed"), entry("LF-TSK-QLT-0001", "changed")],
        })
        with self.assertRaisesRegex(HashDagError, "task registry projection is stale"):
            verify_hash_dag(str(fixture.root), root)

    def test_plan_uses_its_subject_registry_entry_not_registry_file_bytes(self):
        fixture = GraphFixture()
        self.addCleanup(fixture.cleanup)
        task_id = "LF-TSK-QLT-0001"

        def entry(subject, command):
            value = {"subject_task_id": subject, "command": command}
            return {**value, "entry_hash": sha256_bytes(canonical_json_bytes(value))}

        selected = entry(task_id, "selected")
        registry = fixture.write_json("harness/gate-check-registry.yaml", {
            "schema_version": "lexiflow.gate-check-registry.v1",
            "entries": [selected, entry("LF-TSK-QLT-0002", "other")],
        })
        root = fixture.write_json("tmp/plan.json", {
            "schema_version": "lexiflow.gate-plan.v1",
            "task": {"task_id": task_id},
            "registry": {**registry, "subject_entry_sha256": selected["entry_hash"]},
        })
        self.assertEqual(verify_hash_dag(str(fixture.root), root)["result"], "PASS")
        fixture.write_json("harness/gate-check-registry.yaml", {
            "schema_version": "lexiflow.gate-check-registry.v1",
            "entries": [selected, entry("LF-TSK-QLT-0002", "changed")],
        })
        self.assertEqual(verify_hash_dag(str(fixture.root), root)["result"], "PASS")

    def test_schema_suffix_does_not_hide_runner_evidence(self):
        fixture = GraphFixture()
        self.addCleanup(fixture.cleanup)
        root = fixture.write_json("tmp/runner.schema.json", {
            "locator": {"type": "string"}, "sha256": {"type": "string"},
        })
        with self.assertRaisesRegex(HashDagError, "locator must be"):
            verify_hash_dag(str(fixture.root), root)

    def setUp(self):
        self.fixture = GraphFixture()
        self.addCleanup(self.fixture.cleanup)
        self.prior_run = "11111111-1111-4111-8111-111111111111"
        self.root_run = "22222222-2222-4222-8222-222222222222"
        self.prior = self.fixture.receipt(
            self.prior_run, "2026-09-16T00:00:00Z", "2026-09-16T00:00:01Z", identity="prior-leaf"
        )
        self.root = self.fixture.receipt(
            self.root_run, "2026-09-16T00:01:00Z", "2026-09-16T00:01:01Z", prior=self.prior,
        )

    def rewrite(self, locator, value):
        content = canonical_json_bytes(value)
        (self.fixture.root / locator).write_bytes(content)
        return {"locator": locator, "sha256": sha256_bytes(content)}

    def test_complete_graph_passes_without_writes(self):
        before = {path: content for path, content in self.fixture.nodes.items()}
        result = verify_hash_dag(str(self.fixture.root), self.root)
        self.assertEqual(result["result"], "PASS")
        self.assertTrue(result["complete"] and result["finite"] and result["acyclic"])
        for locator, content in before.items():
            self.assertEqual((self.fixture.root / locator).read_bytes(), content)

    def test_self_edge_is_rejected(self):
        locator = self.root["locator"]
        receipt = json.loads((self.fixture.root / locator).read_bytes())
        receipt["artifact_manifest"] = {"locator": locator, "sha256": "0" * 64}
        root = self.rewrite(locator, receipt)
        with self.assertRaisesRegex(HashDagError, "self-edge"):
            verify_hash_dag(str(self.fixture.root), root)

    def test_back_edge_and_cycle_are_rejected(self):
        prior_locator = self.prior["locator"]
        prior = json.loads((self.fixture.root / prior_locator).read_bytes())
        prior["cycle"] = dict(self.root)
        changed_prior = self.rewrite(prior_locator, prior)
        root_locator = self.root["locator"]
        root = json.loads((self.fixture.root / root_locator).read_bytes())
        root["subject_validation"].update(changed_prior)
        changed_root = self.rewrite(root_locator, root)
        prior["cycle"].update(changed_root)
        changed_prior = self.rewrite(prior_locator, prior)
        root["subject_validation"].update(changed_prior)
        changed_root = self.rewrite(root_locator, root)
        with self.assertRaisesRegex(HashDagError, "cycle detected"):
            verify_hash_dag(str(self.fixture.root), changed_root)

    def test_alias_missing_and_changed_leaf_are_rejected(self):
        for mutation, expected in (("alias", "alias"), ("missing", "missing"), ("changed", "changed")):
            with self.subTest(mutation=mutation):
                fixture = GraphFixture()
                self.addCleanup(fixture.cleanup)
                root = fixture.receipt(
                    self.root_run, "2026-09-16T00:01:00Z", "2026-09-16T00:01:01Z"
                )
                receipt = json.loads((fixture.root / root["locator"]).read_bytes())
                if mutation == "alias":
                    receipt["alias"] = {"locator": "tmp/quality/runs/latest/receipt.json", "sha256": "0" * 64}
                    root = {"locator": root["locator"], "sha256": sha256_bytes(canonical_json_bytes(receipt))}
                    (fixture.root / root["locator"]).write_bytes(canonical_json_bytes(receipt))
                elif mutation == "missing":
                    receipt["missing"] = {"locator": "tmp/missing.txt", "sha256": "0" * 64}
                    root = self._store_receipt(fixture, root["locator"], receipt)
                else:
                    manifest = json.loads((fixture.root / receipt["artifact_manifest"]["locator"]).read_bytes())
                    (fixture.root / manifest["artifacts"][0]["locator"]).write_bytes(b"changed")
                with self.assertRaises(HashDagError) as caught:
                    verify_hash_dag(str(fixture.root), root)
                self.assertIn(expected, caught.exception.detail)

    @staticmethod
    def _store_receipt(fixture, locator, receipt):
        content = canonical_json_bytes(receipt)
        (fixture.root / locator).write_bytes(content)
        return {"locator": locator, "sha256": sha256_bytes(content)}

    def test_duplicate_identity_with_different_bytes_is_rejected(self):
        locator = self.root["locator"]
        receipt = json.loads((self.fixture.root / locator).read_bytes())
        manifest_locator = receipt["artifact_manifest"]["locator"]
        manifest = json.loads((self.fixture.root / manifest_locator).read_bytes())
        other = self.fixture.write_bytes("tmp/other.txt", b"different")
        manifest["artifacts"].append({"identity": "root-leaf", "kind": "test", **other})
        new_manifest = self.rewrite(manifest_locator, manifest)
        receipt["artifact_manifest"].update(new_manifest)
        root = self.rewrite(locator, receipt)
        with self.assertRaisesRegex(HashDagError, "identity collision"):
            verify_hash_dag(str(self.fixture.root), root)

    def test_forward_receipt_edge_is_rejected_even_when_acyclic(self):
        later = self.fixture.receipt(
            "33333333-3333-4333-8333-333333333333",
            "2026-09-16T00:02:00Z", "2026-09-16T00:02:01Z", identity="later-leaf",
        )
        locator = self.root["locator"]
        receipt = json.loads((self.fixture.root / locator).read_bytes())
        receipt["future"] = later
        root = self.rewrite(locator, receipt)
        with self.assertRaisesRegex(HashDagError, "not prior"):
            verify_hash_dag(str(self.fixture.root), root)

    def test_typed_failure_never_promotes_invalid_graph(self):
        bad = dict(self.root, sha256="0" * 64)
        result = verify_hash_dag_result(str(self.fixture.root), bad)
        self.assertEqual(result["result"], "FAIL")
        self.assertFalse(result["complete"])

    def test_verified_input_observation_is_not_an_artifact_edge(self):
        digest = "a" * 64
        value = {
            "locator": "planning/workstreams.yaml",
            "expected_sha256": digest,
            "actual_sha256": digest,
            "status": "verified",
        }
        self.assertEqual(_extract_edges(value, "observation"), [])

    def test_malformed_verified_input_observation_fails_closed(self):
        digest = "a" * 64
        for value in (
            {"locator": "planning/workstreams.yaml", "status": "verified"},
            {
                "locator": "planning/workstreams.yaml",
                "expected_sha256": digest,
                "actual_sha256": "b" * 64,
                "status": "verified",
            },
        ):
            with self.subTest(value=value), self.assertRaises(HashDagError):
                _extract_edges(value, "observation")

    def test_absent_file_pre_state_is_not_an_artifact_edge(self):
        value = {
            "locator": "tmp/quality/task-evidence/QLT/result.json",
            "state": "absent",
        }
        self.assertEqual(_extract_edges(value, "pre_state"), [])

    def test_non_absent_file_without_hash_fails_closed(self):
        value = {
            "locator": "tmp/quality/task-evidence/QLT/result.json",
            "state": "present",
        }
        with self.assertRaisesRegex(HashDagError, "invalid absent-file observation"):
            _extract_edges(value, "pre_state")

    def test_non_json_command_stream_is_opaque(self):
        content = canonical_json_bytes(
            {"locator": "tmp/hidden.json", "sha256": "a" * 64}
        ) + b"\n"
        self.assertIsNone(_parse_json(content, "tmp/commands/task.stdout"))

    def test_pretty_auxiliary_json_preserves_edges(self):
        value = {"locator": "tmp/leaf.txt", "sha256": "a" * 64}
        parsed = _parse_json(json.dumps(value, indent=2).encode(), "tmp/outcome.json")
        self.assertEqual(len(_extract_edges(parsed)), 1)

    def test_pretty_official_node_and_duplicate_auxiliary_keys_fail(self):
        official = {"schema_version": "lexiflow.gate-receipt.v1"}
        with self.assertRaisesRegex(HashDagError, "not canonical"):
            _parse_json(json.dumps(official, indent=2).encode(), "tmp/receipt.json")
        with self.assertRaisesRegex(HashDagError, "duplicate JSON key"):
            _parse_json(b'{"locator":"a","locator":"b"}', "tmp/outcome.json")

    def test_manifest_artifact_role_identity_is_scoped_to_run(self):
        def manifest(run):
            return {
                "schema_version": "lexiflow.gate-artifact-manifest.v1",
                "run_id": run,
                "artifacts": [{"identity": "subject:diff:0", "locator": "tmp/diff.txt", "sha256": "a" * 64}],
            }
        first = _extract_edges(manifest("first"))[0].identity
        second = _extract_edges(manifest("second"))[0].identity
        self.assertNotEqual(first, second)



if __name__ == "__main__":
    unittest.main()
