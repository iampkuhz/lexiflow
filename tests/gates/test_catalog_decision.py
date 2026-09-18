import copy
import datetime
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path

import yaml

from scripts.gates import cli
from scripts.gates.catalog_decision import CatalogDecisionError, verify_catalog_decision
from scripts.gates.issuer_packet import (
    ATTESTATION_SCHEMA,
    IssuerPacketMaterializer,
    canonical_json_bytes as issuer_canonical_json_bytes,
    sha256_bytes as issuer_sha256_bytes,
)
from scripts.gates.receipt_store import canonical_json_bytes, read_bound_bytes, sha256_bytes


VALIDATION_RUN = "11111111-1111-4111-8111-111111111111"
REVIEW_RUN = "22222222-2222-4222-8222-222222222222"
DEPENDENCY_RUN = "33333333-3333-4333-8333-333333333333"
CATALOG_RUN = "44444444-4444-4444-8444-444444444444"
PROCESS_RUN = "55555555-5555-4555-8555-555555555555"
DEP_VALIDATION_RUN = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEP_REVIEW_RUN = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
REPO = Path(__file__).resolve().parents[2]


class CatalogFixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task_id = "LF-TSK-QLT-0099"
        self.dep_id = "LF-TSK-QLT-0098"
        self.write_bytes("scripts/gates/cli.py", b"# fixture cli\n")
        self.write_bytes("subject.py", b"current subject\n")
        self.subject_diff = self.write_bytes(
            "tmp/evidence/subject.diff",
            b"diff --git a/subject.py b/subject.py\n"
            b"--- a/subject.py\n"
            b"+++ b/subject.py\n"
            b"@@ -1 +1 @@\n"
            b"-old subject\n"
            b"+current subject\n",
        )
        self.subject_snapshot = self.write_json(
            "tmp/evidence/subject-snapshot.json",
            {
                "schema_version": "lexiflow.changed-file-snapshot.v1",
                "files": {
                    "subject.py": {
                        "state": "present",
                        "sha256": sha256_bytes((self.root / "subject.py").read_bytes()),
                    }
                },
            },
        )
        self.write_bytes(
            "harness/gate-issuer-authorities.yaml",
            (REPO / "harness/gate-issuer-authorities.yaml").read_bytes(),
        )
        self.policy = self.write_bytes("harness/agent-policy.manifest.yaml", b"policy: 1\n")
        self.registry = self.write_bytes("harness/gate-check-registry.yaml", b"registry: 1\n")
        self.acceptance = self.write_bytes(
            "docs/product/product-brief.md",
            b"# Cases\n\n## LF-CASE-001 - Current\n\n- **Status:** implementation-reviewed\n",
        )
        catalog = {
            "workstreams": [{
                "id": "LF-WS-QLT", "epics": [{
                    "id": "LF-EP-QLT-001", "capabilities": [{
                        "id": "LF-CP-QLT-001", "seed_tasks": [
                            {"id": self.dep_id, "task_version": 1, "change_version": "1.0.0"},
                            {
                                "id": self.task_id, "task_version": 1, "change_version": "1.0.0",
                                "acceptance_case_ids": ["LF-CASE-001"],
                                "depends_on": [{
                                    "task_id": self.dep_id, "type": "hard", "required_task_version": 1,
                                    "required_change_version": "1.0.0", "required_result": "PASS",
                                }],
                            },
                        ],
                    }],
                }],
            }],
        }
        self.source = self.write_bytes("planning/workstreams.yaml", yaml.safe_dump(catalog, sort_keys=False).encode())
        self.validation = self.make_receipt(
            VALIDATION_RUN, "TASK_VALIDATION", self.task_id,
            "2026-09-16T00:00:00Z", "2026-09-16T00:00:01Z",
        )
        self.review = self.make_receipt(
            REVIEW_RUN, "INDEPENDENT_REVIEW", self.task_id,
            "2026-09-16T00:01:00Z", "2026-09-16T00:01:01Z",
            validation=self.validation,
        )
        self.dep_validation = self.make_receipt(
            DEP_VALIDATION_RUN, "TASK_VALIDATION", self.dep_id,
            "2026-09-16T00:00:10Z", "2026-09-16T00:00:11Z",
        )
        self.dep_review = self.make_receipt(
            DEP_REVIEW_RUN, "INDEPENDENT_REVIEW", self.dep_id,
            "2026-09-16T00:01:10Z", "2026-09-16T00:01:11Z",
            validation=self.dep_validation,
        )
        self.dependency = self.make_receipt(
            DEPENDENCY_RUN, "CATALOG_DECISION", self.dep_id,
            "2026-09-16T00:02:00Z", "2026-09-16T00:02:01Z",
            validation=self.dep_validation, review=self.dep_review,
        )
        self.plan = self.make_current_plan()
        self.evidence = {
            "schema_version": "lexiflow.catalog-decision-evidence.v1",
            "task_validation": dict(self.validation),
            "independent_review": dict(self.review),
            "required_dependency_receipts": [{
                "task_id": self.dep_id, "task_version": 1, "change_version": "1.0.0", **self.dependency,
            }],
            "acceptance_case_registry": dict(self.acceptance),
        }

    def cleanup(self):
        self.temp.cleanup()

    def write_bytes(self, locator, content):
        path = self.root / locator
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"locator": locator, "sha256": sha256_bytes(content)}

    def write_json(self, locator, value):
        return self.write_bytes(locator, canonical_json_bytes(value))

    def make_plan_artifact(self, run_id, task_id):
        plan = {
            "schema_version": "lexiflow.gate-plan.v1", "receipt_kind": "TASK_VALIDATION",
            "task": {"task_id": task_id}, "content_fingerprint": "",
        }
        projected = {key: value for key, value in plan.items() if key != "content_fingerprint"}
        plan["content_fingerprint"] = sha256_bytes(canonical_json_bytes(projected))
        descriptor = self.write_json(f"tmp/quality/runs/{run_id}/plan.json", plan)
        return {**descriptor, "content_fingerprint": plan["content_fingerprint"]}

    def make_manifest(self, run_id):
        leaf = self.write_bytes(f"tmp/quality/runs/{run_id}/leaf.txt", run_id.encode())
        return self.write_json(
            f"tmp/quality/runs/{run_id}/artifact-manifest.json",
            {
                "schema_version": "lexiflow.gate-artifact-manifest.v1", "run_id": run_id,
                "artifacts": [{"identity": f"leaf:{run_id}", "kind": "test", **leaf}],
            },
        )

    @staticmethod
    def derived_uuid(run_id, offset):
        return str(uuid.UUID(int=(uuid.UUID(run_id).int + offset) % (1 << 128), version=4))

    def make_issuer(self, run_id, kind):
        subject_identity = {
            "parent_session_id": self.derived_uuid(run_id, 8),
            "agent_id": "producer",
            "run_id": self.derived_uuid(run_id, 9),
            "session_id": self.derived_uuid(run_id, 10),
            "client": "qoder",
            "parent_client": "codex",
        }
        context = {
            "actor_id": f"actor-{run_id[:8]}",
            "session_id": self.derived_uuid(run_id, 1),
            "parent_session_id": self.derived_uuid(run_id, 2),
            "client": "codex",
        }
        evidence = {
            "schema_version": ATTESTATION_SCHEMA,
            "actor_type": "codex",
            "attestation_id": self.derived_uuid(run_id, 3),
            "audience": "lexiflow-gate",
            "nonce": self.derived_uuid(run_id, 4),
            "issued_at": "2026-09-16T11:59:00Z",
            "expires_at": "2026-09-16T12:05:00Z",
            **context,
        }
        evidence_locator = f"tmp/quality/authority/codex/{self.derived_uuid(run_id, 5)}.json"
        evidence_bytes = issuer_canonical_json_bytes(evidence)
        self.write_bytes(evidence_locator, evidence_bytes)
        materializer = IssuerPacketMaterializer(
            self.root,
            trusted_codex_context=context,
            now=lambda: datetime.datetime(2026, 9, 16, 12, 0, tzinfo=datetime.timezone.utc),
        )
        result = materializer.materialize(
            {"issuer_instance_id": self.derived_uuid(run_id, 6), "receipt_kinds": [kind]},
            authority_evidence_locator=evidence_locator,
            authority_evidence_sha256=issuer_sha256_bytes(evidence_bytes),
            subject_identity=subject_identity,
        )
        return {"locator": result.locator, "sha256": result.sha256}, result.packet, subject_identity

    def make_receipt(self, run_id, kind, task_id, started, finished, validation=None, review=None, result="PASS"):
        issuer_descriptor, issuer_packet, subject_identity = self.make_issuer(run_id, kind)
        actor_fields = (
            "issuer_instance_id", "actor_type", "actor_id", "parent_session_id",
            "session_id", "client", "role",
        )
        receipt = {
            "schema_version": "lexiflow.gate-receipt.v1", "receipt_kind": kind, "run_id": run_id,
            "plan": self.make_plan_artifact(run_id, task_id),
            "issuer": {
                "trusted_issuer_packet": issuer_descriptor,
                "actor_identity": {field: issuer_packet[field] for field in actor_fields},
                "process_identity": {
                    "process_instance_id": self.derived_uuid(run_id, 7),
                    "gate_run_id": run_id,
                    "executable_locator": "scripts/gates/cli.py",
                    "executable_sha256": sha256_bytes((self.root / "scripts/gates/cli.py").read_bytes()),
                    "issuer_packet_sha256": issuer_descriptor["sha256"],
                },
            },
            "started_at": started, "finished_at": finished,
            "task": {"task_id": task_id, "task_version": 1, "change_version": "1.0.0"},
            "current_inputs": {
                "source_snapshot_fingerprint": "b" * 64,
                "task_source": dict(self.source), "registry": dict(self.registry), "policy": dict(self.policy),
            },
            "artifact_manifest": self.make_manifest(run_id),
            "completeness": {"status": "PASS"}, "result": result, "reasons": [],
        }
        if kind == "TASK_VALIDATION":
            receipt["validation"] = {
                "subject_identity": subject_identity,
                "raw_artifacts": {
                    "changed_file_snapshot": dict(self.subject_snapshot),
                    "diff": dict(self.subject_diff),
                },
                "scope_reconciliation": {"changed_files": ["subject.py"]},
            }
        elif kind == "INDEPENDENT_REVIEW":
            receipt["subject_validation"] = {**validation, "result": "PASS"}
            issuer_sha = receipt["issuer"]["trusted_issuer_packet"]["sha256"]
            receipt["reviewer_independence"] = {
                "reviewer_issuer_packet_sha256": issuer_sha,
                "reviewer_identity": copy.deepcopy(receipt["issuer"]["actor_identity"]),
                "producer_identity": {"agent_id": "producer"},
                "producer_and_reviewer_differ": True, "review_run_is_distinct": True,
                "reviewer_write_set_source": "current-plan-scope-reconciliation",
                "subject_snapshot": dict(self.subject_snapshot),
                "subject_snapshot_reverified": True,
                "reviewer_wrote_subject_files": False, "status": "PASS",
            }
        else:
            receipt["subject_receipts"] = {
                "task_validation": {**validation, "result": "PASS"},
                "independent_review": {**review, "result": "PASS"},
            }
            receipt["required_dependency_receipts"] = []
            receipt["current_inputs"]["acceptance_case_registry"] = {
                **self.acceptance, "orphan_cases": [], "duplicate_cases": [], "current_mapping_status": "PASS"
            }
            receipt["reviewer_independence"] = {"status": "PASS"}
            receipt["freshness_reconciliation"] = {"status": "PASS"}
            receipt["hash_dag_verifications"] = [{"result": "PASS"}]
            receipt["catalog_task_status"] = result
        return self.write_json(f"tmp/quality/runs/{run_id}/receipt.json", receipt)

    def make_current_plan(self):
        evidence_packet = self.write_bytes("tmp/quality/evidence/current.json", b"{}")
        issuer_packet_content = canonical_json_bytes({"issuer": "catalog"})
        issuer_packet = self.write_bytes("tmp/quality/issuers/catalog.json", issuer_packet_content)
        plan = {
            "schema_version": "lexiflow.gate-plan.v1", "mode": "incremental", "receipt_kind": "CATALOG_DECISION",
            "execution": {
                "layer": "evidence-consumption",
                "checker_execution": "forbidden",
                "source": "prior-immutable-receipts",
            },
            "task": {
                "task_id": self.task_id, "task_version": 1, "change_version": "1.0.0",
                "task_source": dict(self.source),
                "dependencies": [{
                    "task_id": self.dep_id, "type": "hard", "required_task_version": 1,
                    "required_change_version": "1.0.0", "required_result": "PASS",
                    "resolved_producer": {"task_id": self.dep_id, "task_version": 1, "change_version": "1.0.0"},
                }],
            },
            "registry": dict(self.registry),
            "consumed_inputs": [{**self.policy, "state": "present"}],
            "subject": {"explicit_evidence_packet": evidence_packet, "raw_artifacts": {"tests": []}},
            "issuer_packet": {
                **issuer_packet,
                "packet": {
                    "issuer_instance_id": "99999999-9999-4999-8999-999999999999",
                    "actor_type": "codex", "actor_id": "catalog-issuer",
                    "parent_session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    "client": "codex", "role": "gate-receipt-issuer",
                },
            },
            "checks": [], "content_fingerprint": "",
        }
        self.refingerprint(plan)
        return plan

    @staticmethod
    def refingerprint(plan):
        projected = {key: value for key, value in plan.items() if key != "content_fingerprint"}
        plan["content_fingerprint"] = sha256_bytes(canonical_json_bytes(projected))

    def verify(self, evidence=None, plan=None, run_id=CATALOG_RUN):
        return verify_catalog_decision(
            str(self.root), current_plan=plan or self.plan,
            evidence=copy.deepcopy(evidence or self.evidence), current_run_id=run_id,
        )


class TestCatalogDecision(unittest.TestCase):
    def setUp(self):
        self.fixture = CatalogFixture()
        self.addCleanup(self.fixture.cleanup)

    def test_complete_current_chain_passes(self):
        result = self.fixture.verify()
        self.assertEqual(result["catalog_result"], "PASS")
        self.assertEqual(result["current_inputs"]["acceptance_case_registry"]["current_mapping_status"], "PASS")
        self.assertTrue(all(item["result"] == "PASS" for item in result["hash_dag_verifications"]))

    def test_explicit_evidence_packet_hash_graph_must_be_current(self):
        plan = copy.deepcopy(self.fixture.plan)
        packet_descriptor = plan["subject"]["explicit_evidence_packet"]
        packet_path = self.fixture.root / packet_descriptor["locator"]
        packet = {
            "stale_artifact": {
                "locator": self.fixture.acceptance["locator"],
                "sha256": "0" * 64,
            }
        }
        content = canonical_json_bytes(packet)
        packet_path.write_bytes(content)
        packet_descriptor["sha256"] = sha256_bytes(content)
        self.fixture.refingerprint(plan)
        with self.assertRaisesRegex(CatalogDecisionError, "hash-graph-invalid"):
            self.fixture.verify(plan=plan)

    def test_orphan_duplicate_and_unknown_acceptance_mapping_fail(self):
        cases = self.fixture.root / self.fixture.acceptance["locator"]
        cases.write_bytes(cases.read_bytes() + b"\n## LF-CASE-ORPHAN - Orphan\n")
        evidence = copy.deepcopy(self.fixture.evidence)
        evidence["acceptance_case_registry"]["sha256"] = sha256_bytes(cases.read_bytes())
        with self.assertRaisesRegex(CatalogDecisionError, "invalid-mapping"):
            self.fixture.verify(evidence=evidence)

    def test_missing_or_stale_dependency_fails(self):
        evidence = copy.deepcopy(self.fixture.evidence)
        evidence["required_dependency_receipts"] = []
        with self.assertRaisesRegex(CatalogDecisionError, "missing-dependency"):
            self.fixture.verify(evidence=evidence)
        evidence = copy.deepcopy(self.fixture.evidence)
        evidence["required_dependency_receipts"][0]["task_version"] = 2
        with self.assertRaisesRegex(CatalogDecisionError, "stale-dependency"):
            self.fixture.verify(evidence=evidence)

    def test_subject_identity_and_stale_input_fail(self):
        validation_path = self.fixture.root / self.fixture.validation["locator"]
        validation = json.loads(validation_path.read_bytes())
        validation["task"]["task_id"] = "LF-TSK-QLT-0001"
        content = canonical_json_bytes(validation)
        validation_path.write_bytes(content)
        evidence = copy.deepcopy(self.fixture.evidence)
        evidence["task_validation"]["sha256"] = sha256_bytes(content)
        review_path = self.fixture.root / self.fixture.review["locator"]
        review = json.loads(review_path.read_bytes())
        review["subject_validation"]["sha256"] = sha256_bytes(content)
        review_content = canonical_json_bytes(review)
        review_path.write_bytes(review_content)
        evidence["independent_review"]["sha256"] = sha256_bytes(review_content)
        with self.assertRaisesRegex(CatalogDecisionError, "identity-mismatch"):
            self.fixture.verify(evidence=evidence)

    def test_same_byte_registry_or_policy_alias_is_not_current(self):
        for field in ("registry", "policy"):
            with self.subTest(field=field):
                fixture = CatalogFixture()
                self.addCleanup(fixture.cleanup)
                original = fixture.plan[field] if field == "registry" else fixture.plan["consumed_inputs"][0]
                alias = fixture.write_bytes(f"tmp/aliases/{field}.yaml", (fixture.root / original["locator"]).read_bytes())
                plan = copy.deepcopy(fixture.plan)
                if field == "registry":
                    plan["registry"] = alias
                else:
                    plan["consumed_inputs"][0] = {**alias, "state": "present"}
                fixture.refingerprint(plan)
                expected = "stale-input" if field == "registry" else "evidence-incomplete"
                with self.assertRaisesRegex(CatalogDecisionError, expected):
                    fixture.verify(plan=plan)

    def test_same_byte_task_source_alias_is_not_current(self):
        alias = self.fixture.write_bytes(
            "tmp/aliases/workstreams-copy.yaml",
            (self.fixture.root / self.fixture.source["locator"]).read_bytes(),
        )
        plan = copy.deepcopy(self.fixture.plan)
        plan["task"]["task_source"] = alias
        self.fixture.refingerprint(plan)

        with self.assertRaisesRegex(CatalogDecisionError, "unsafe-locator"):
            self.fixture.verify(plan=plan)

    def test_tamper_and_self_reference_fail(self):
        validation = json.loads((self.fixture.root / self.fixture.validation["locator"]).read_bytes())
        manifest = json.loads((self.fixture.root / validation["artifact_manifest"]["locator"]).read_bytes())
        (self.fixture.root / manifest["artifacts"][0]["locator"]).write_bytes(b"tampered")
        with self.assertRaisesRegex(CatalogDecisionError, "hash-graph-invalid"):
            self.fixture.verify()
        fresh = CatalogFixture()
        self.addCleanup(fresh.cleanup)
        with self.assertRaisesRegex(CatalogDecisionError, "self-reference"):
            fresh.verify(run_id=VALIDATION_RUN)

    def test_forged_approval_or_bootstrap_is_rejected(self):
        for key in ("user_approval", "bootstrap", "backfill"):
            evidence = copy.deepcopy(self.fixture.evidence)
            evidence[key] = True
            with self.subTest(key=key), self.assertRaisesRegex(CatalogDecisionError, "forged-approval"):
                self.fixture.verify(evidence=evidence)

    def test_untrusted_dependency_issuer_is_rejected(self):
        dependency_path = self.fixture.root / self.fixture.dependency["locator"]
        dependency = json.loads(dependency_path.read_bytes())
        forged = self.fixture.write_json(
            "tmp/quality/issuers/forged.json",
            {"schema_version": "fixture.issuer.v1", "actor_id": "forged"},
        )
        dependency["issuer"]["trusted_issuer_packet"] = forged
        dependency["issuer"]["process_identity"]["issuer_packet_sha256"] = forged["sha256"]
        content = canonical_json_bytes(dependency)
        dependency_path.write_bytes(content)
        evidence = copy.deepcopy(self.fixture.evidence)
        evidence["required_dependency_receipts"][0]["sha256"] = sha256_bytes(content)
        with self.assertRaisesRegex(CatalogDecisionError, "issuer-authority-invalid"):
            self.fixture.verify(evidence=evidence)

    def test_current_subject_mutation_is_rejected_even_when_review_claims_no_writes(self):
        (self.fixture.root / "subject.py").write_bytes(b"mutated after validation\n")
        with self.assertRaisesRegex(CatalogDecisionError, "stale-input"):
            self.fixture.verify()

    def test_review_without_current_subject_snapshot_proof_is_rejected(self):
        review_path = self.fixture.root / self.fixture.review["locator"]
        review = json.loads(review_path.read_bytes())
        review["reviewer_independence"].pop("subject_snapshot_reverified")
        content = canonical_json_bytes(review)
        review_path.write_bytes(content)
        evidence = copy.deepcopy(self.fixture.evidence)
        evidence["independent_review"]["sha256"] = sha256_bytes(content)
        with self.assertRaisesRegex(CatalogDecisionError, "reviewer-not-independent"):
            self.fixture.verify(evidence=evidence)

    def test_blocked_and_fail_subject_results_are_never_promoted(self):
        for result in ("BLOCKED", "FAIL"):
            with self.subTest(result=result):
                fixture = CatalogFixture()
                self.addCleanup(fixture.cleanup)
                review_path = fixture.root / fixture.review["locator"]
                review = json.loads(review_path.read_bytes())
                review["result"] = result
                review["reasons"] = [f"review-{result.lower()}"]
                review_content = canonical_json_bytes(review)
                review_path.write_bytes(review_content)
                review_descriptor = {"locator": fixture.review["locator"], "sha256": sha256_bytes(review_content)}

                evidence = copy.deepcopy(fixture.evidence)
                evidence["independent_review"] = review_descriptor
                verified = fixture.verify(evidence=evidence)
                self.assertEqual(verified["catalog_result"], result)

    def test_cli_route_publishes_immutable_catalog_receipt(self):
        evidence_bytes = canonical_json_bytes(self.fixture.evidence)
        descriptor = self.fixture.write_bytes("tmp/evidence/catalog.json", evidence_bytes)
        self.fixture.plan["subject"]["raw_artifacts"]["tests"] = [descriptor]
        self.fixture.refingerprint(self.fixture.plan)

        def compiler(*args, **kwargs):
            plan = copy.deepcopy(self.fixture.plan)
            return {"plan": plan, "canonical_bytes": canonical_json_bytes(plan), "content_fingerprint": plan["content_fingerprint"]}

        def executor(*args, **kwargs):
            self.fail("catalog decision must not rerun delivery validation")

        ids = iter((uuid.UUID(CATALOG_RUN), uuid.UUID(PROCESS_RUN)))
        receipt = cli.run_gate(
            self.fixture.root, mode="incremental", receipt_kind="CATALOG_DECISION",
            evidence_packet=self.fixture.plan["subject"]["explicit_evidence_packet"]["locator"],
            issuer_packet=self.fixture.plan["issuer_packet"]["locator"],
            compiler=compiler, executor=executor, uuid_factory=lambda: next(ids), stderr=io.StringIO(),
            now=lambda: "2026-09-16T00:03:02Z",
        )
        self.assertEqual((receipt["receipt_kind"], receipt["catalog_task_status"], receipt["result"]), ("CATALOG_DECISION", "PASS", "PASS"))
        stored = json.loads(read_bound_bytes(self.fixture.root, f"tmp/quality/runs/{CATALOG_RUN}/receipt.json"))
        self.assertEqual(stored, receipt)
        self.assertEqual(receipt["catalog_checks"]["execution"], "evidence-consumption")
        self.assertEqual(receipt["catalog_checks"]["outcomes"], [])
        self.assertNotIn("user_approval", receipt)


if __name__ == "__main__":
    unittest.main()
