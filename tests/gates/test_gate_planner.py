from __future__ import annotations

import copy
import datetime
import hashlib
import hmac
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock

import yaml

import scripts.gates.planner as planner
from scripts.gates.evidence_packet import (
    CODEX_TASK_PROJECTION_SCHEMA_VERSION,
    SNAPSHOT_SCHEMA_VERSION,
    canonical_json as evidence_canonical_json,
    materialize,
    verify_packet_strict,
)
from scripts.gates.issuer_packet import (
    ATTESTATION_SCHEMA,
    IssuerPacketMaterializer,
    canonical_json_bytes as issuer_canonical_json,
    sha256_bytes as issuer_sha256,
)
from scripts.gates.planner import PlannerError, canonical_json_bytes, compile_plan, sha256_bytes
from scripts.harness.qoder_task import REQUIRED_HANDOFF, RUNTIME_IDENTITY_FIELDS
from tests.gates.test_evidence_packet import FixtureRepo as EvidenceFixture
from tests.gates.test_evidence_packet import _build_valid_input, _set_changed_files
from tests.gates import test_issuer_packet as issuer_test_helpers


REPO = pathlib.Path(__file__).resolve().parents[2]
PUBLICATION_ID = "70000000-0000-4000-8000-000000000008"
ISSUER_ID = "14000000-0000-4000-8000-000000000008"


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(root: pathlib.Path, locator: str, data: bytes) -> None:
    path = root / locator
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _qoder_persisted_json(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _tasks(catalog: dict) -> list[dict]:
    return [
        task
        for workstream in catalog["workstreams"]
        for epic in workstream.get("epics", [])
        for capability in epic.get("capabilities", [])
        for task in capability.get("seed_tasks", [])
    ]


def _registry_entry(registry: dict, task_id: str = "LF-TSK-QLT-0008") -> dict:
    return next(entry for entry in registry["entries"] if entry["subject_task_id"] == task_id)


def _snapshot(root: pathlib.Path) -> dict[str, str]:
    result = {}
    for directory, names, files in os.walk(root):
        names.sort()
        for name in sorted(files):
            path = pathlib.Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[relative] = "symlink:" + os.readlink(path)
            else:
                result[relative] = _hash(path.read_bytes())
    return result


class RealPlannerFixture:
    """Hermetic QLT7 + QLT14 fixture over the real 113-task catalog."""

    NOW = issuer_test_helpers.IssuerPacketMaterializerTest.NOW

    def __init__(self) -> None:
        self.evidence_fixture = EvidenceFixture()
        self.root = pathlib.Path(self.evidence_fixture.root)
        self._copy_current_inputs()
        self.catalog = yaml.safe_load((self.root / "planning/workstreams.yaml").read_bytes())
        self.catalog_tasks = {task["id"]: task for task in _tasks(self.catalog)}
        self.record = self.catalog_tasks["LF-TSK-QLT-0008"]
        self.evidence_input = self._build_evidence_input()
        self.evidence_counter = 8
        self.issuer_generation = 0
        self._materialize_evidence(PUBLICATION_ID)
        self._materialize_issuer()

    def cleanup(self) -> None:
        self.evidence_fixture.cleanup()

    def _copy(self, locator: str) -> None:
        _write(self.root, locator, (REPO / locator).read_bytes())

    def _copy_current_inputs(self) -> None:
        for locator in (
            "planning/workstreams.yaml",
            "planning/task-template.yaml",
            "harness/agent-policy.manifest.yaml",
            "harness/agent-runtime.manifest.yaml",
            "harness/manifest.yaml",
            "harness/gate-issuer-authorities.yaml",
            "harness/gate-check-registry.yaml",
            "docs/product/product-brief.md",
            "scripts/gates/planner.py",
            "tests/gates/test_gate_planner.py",
            "scripts/gates/evidence_packet.py",
            "tests/gates/test_evidence_packet.py",
            "scripts/gates/issuer_packet.py",
            "tests/gates/test_issuer_packet.py",
        ):
            self._copy(locator)

    def _qoder_package_records(self) -> list[dict]:
        task_prefix = "-".join(self.record["id"].split("-")[:3]) + "-"
        candidates = [
            self.record,
            *(
                self.catalog_tasks[task_id]
                for task_id in sorted(self.catalog_tasks)
                if task_id.startswith(task_prefix) and task_id != self.record["id"]
            ),
        ]
        records = []
        total = 0
        for record in candidates:
            minutes = record.get("estimated_task_minutes")
            if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0:
                if total >= 180:
                    break
                if total + minutes <= 360:
                    records.append(record)
                    total += minutes
        if len(records) < 2 or not 180 <= total <= 360:
            raise AssertionError(f"cannot construct Qoder package for {self.record['id']}")
        return records

    def _raw_task(self) -> dict:
        old = self.evidence_fixture.task_artifact
        package_records = self._qoder_package_records()
        package_task_ids = [record["id"] for record in package_records]
        domain = self.record["id"].split("-")[2]
        work_package_id = f"LF-WP-{domain}-{self.record['id'].split('-')[-1]}"
        agent_profile = "quality-verifier"
        return {
            "goal": self.record["deliverable"],
            "task_id": self.record["id"],
            "task_source": "planning/workstreams.yaml",
            "task_version": self.record["task_version"],
            "change_version": self.record["change_version"],
            "work_package_id": work_package_id,
            "task_ids": package_task_ids,
            "task_versions": {
                record["id"]: record["task_version"] for record in package_records
            },
            "change_versions": {
                record["id"]: record["change_version"] for record in package_records
            },
            "estimated_minutes": sum(
                record["estimated_task_minutes"] for record in package_records
            ),
            "primary_owner": self.record["owner"],
            "contract_boundary": "one current owner and one frozen Qoder harness",
            "agent_profile": agent_profile,
            "harness_manifest": f"tmp/qoder-work-packages/{work_package_id}/harness.json",
            "allowed_files": ", ".join(self.record["allowed_files"]),
            "forbidden_files": ", ".join(self.record["forbidden_files"]),
            "required_context": "current catalog, real QLT7 packet, real QLT14 packet",
            "expected_output": self.record["deliverable"],
            "acceptance_criteria": copy.deepcopy(self.record["acceptance_criteria"]),
            "acceptance_evidence": copy.deepcopy(self.record["acceptance_evidence"]),
            "validation_command": self.record["validation_command"],
            "failure_policy": "Fail closed on any missing, stale, ambiguous, or changed input.",
            "parent_client": old["parent_client"],
            "agent_id": old["agent_id"],
            "run_id": old["run_id"],
            "session_id": old["session_id"],
            "client": old["client"],
            "harness_manifest_sha256": "d" * 64,
            "harness_context": [
                {"path": "AGENTS.md", "sha256": "a" * 64},
                {"path": ".qoder/AGENTS.md", "sha256": "b" * 64},
                {"path": f".qoder/agents/{agent_profile}.md", "sha256": "c" * 64},
                {"path": "planning/workstreams.yaml", "sha256": "e" * 64},
                {"path": "harness/agent-policy.manifest.yaml", "sha256": "f" * 64},
                {"path": "harness/agent-runtime.manifest.yaml", "sha256": "0" * 64},
            ],
            "parent_session_id": old["parent_session_id"],
            "permission_mode": "bypass_permissions",
        }

    def _build_evidence_input(self) -> dict:
        fixture = self.evidence_fixture
        inp = _build_valid_input(fixture)
        inp["subject"]["main_agent_attestation"]["result_fields"]["effect_checks"] = {
            "behavior": "PASS",
            "regression": "PASS",
        }
        catalog_bytes = (self.root / "planning/workstreams.yaml").read_bytes()
        inp["task"] = {
            "task_id": self.record["id"],
            "task_version": self.record["task_version"],
            "change_version": self.record["change_version"],
            "task_source": {"locator": "planning/workstreams.yaml", "sha256": _hash(catalog_bytes)},
        }
        raw_task = self._raw_task()
        fixture.task_artifact = raw_task
        task_bytes = _qoder_persisted_json(raw_task)
        _write(self.root, fixture.task_artifact_path, task_bytes)
        inp["subject"]["raw_artifacts"]["task"]["sha256"] = _hash(task_bytes)

        completion = copy.deepcopy(fixture.completion_artifact)
        completion.update({
            "task_id": self.record["id"],
            "task_version": self.record["task_version"],
            "change_version": self.record["change_version"],
        })
        fixture.completion_artifact = completion
        completion_bytes = _qoder_persisted_json(completion)
        _write(self.root, fixture.completion_artifact_path, completion_bytes)
        inp["subject"]["raw_artifacts"]["completion"]["sha256"] = _hash(completion_bytes)

        changed = sorted(self.record["allowed_files"])
        snapshot = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "files": {
                locator: {"state": "present", "sha256": _hash((self.root / locator).read_bytes())}
                for locator in changed
            },
        }
        fixture.snapshot = snapshot
        snapshot_bytes = evidence_canonical_json(snapshot)
        _write(self.root, fixture.snapshot_path, snapshot_bytes)
        inp["subject"]["raw_artifacts"]["changed_file_snapshot"]["sha256"] = _hash(snapshot_bytes)
        diff = b"".join(
            (
                f"diff --git a/{locator} b/{locator}\n"
                f"--- a/{locator}\n+++ b/{locator}\n"
                "@@ -1 +1 @@\n-old\n+new\n"
            ).encode()
            for locator in changed
        )
        fixture.diff_content = diff
        _write(self.root, fixture.diff_path, diff)
        inp["subject"]["raw_artifacts"]["diff"]["sha256"] = _hash(diff)
        raw_allowed = raw_task["allowed_files"]
        raw_forbidden = raw_task["forbidden_files"]
        claims = sorted(claim["path"] for claim in self.record["file_claims"])
        inp["scope"] = {
            "changed_files": changed,
            "raw_caller_strings": {"allowed_files": raw_allowed, "forbidden_files": raw_forbidden},
            "normalized": {
                "allowed_files": sorted(self.record["allowed_files"]),
                "forbidden_files": sorted(self.record["forbidden_files"]),
                "canonical_file_claims": claims,
            },
            "three_way_reconciliation": {
                "changed_outside_allowed": [], "changed_matching_forbidden": [],
                "changed_without_claim": [], "claims_outside_allowed": [],
                "claims_intersecting_forbidden": [], "status": "PASS",
            },
        }
        inp["subject"]["main_agent_attestation"]["result_fields"]["changed_files"] = changed
        inp["subject"]["identity"] = {
            field: raw_task[field]
            for field in ("parent_session_id", "agent_id", "run_id", "session_id", "client", "parent_client")
        }
        return inp

    def _materialize_evidence(self, publication_id: str | None = None) -> None:
        if publication_id is None:
            self.evidence_counter += 1
            publication_id = str(uuid.UUID(int=self.evidence_counter, version=4))
        result = materialize(self.root, copy.deepcopy(self.evidence_input), publication_id)
        verify_packet_strict(self.root, result["publication"]["locator"], result["publication"]["sha256"])
        self.evidence_locator = result["publication"]["locator"]
        self.evidence_sha = result["publication"]["sha256"]
        self.evidence_packet = result["packet"]

    def refresh_raw_task(self, mutate=None) -> None:
        raw = self._raw_task()
        if mutate:
            mutate(raw)
        self.evidence_fixture.task_artifact = raw
        data = _qoder_persisted_json(raw)
        _write(self.root, self.evidence_fixture.task_artifact_path, data)
        self.evidence_input["subject"]["raw_artifacts"]["task"]["sha256"] = _hash(data)
        self._materialize_evidence()

    def use_codex_work_package(
        self,
        task_ids=(
            "LF-TSK-QLT-0006", "LF-TSK-QLT-0007",
            "LF-TSK-QLT-0008", "LF-TSK-QLT-0011",
        ),
    ) -> None:
        task_ids = list(task_ids)
        records = [self.catalog_tasks[task_id] for task_id in task_ids]
        old = self.evidence_fixture.task_artifact
        caller = {
            "goal": "Compile one independently reviewable Gate work package.",
            "work_package_id": "LF-WP-QLT-CODEX-REVIEW-PROJECTION-001",
            "task_ids": copy.deepcopy(task_ids),
            "task_versions": {
                record["id"]: record["task_version"] for record in records
            },
            "change_versions": {
                record["id"]: record["change_version"] for record in records
            },
            "estimated_minutes": 240,
            "primary_owner": "LF-WS-QLT",
            "contract_boundary": "runner identity -> evidence packet -> planner -> independent review",
            "allowed_files": ", ".join(sorted({
                path for record in records for path in record["allowed_files"]
            })),
            "forbidden_files": ", ".join(sorted({
                path for record in records for path in record["forbidden_files"]
            })),
            "required_context": "current catalog and immutable Gate packets",
            "expected_outputs_by_task": {
                record["id"]: record["deliverable"] for record in records
            },
            "acceptance_by_task": {
                record["id"]: {
                    "acceptance_criteria": copy.deepcopy(record["acceptance_criteria"]),
                    "acceptance_evidence": copy.deepcopy(record["acceptance_evidence"]),
                }
                for record in records
            },
            "validation_commands": {
                record["id"]: record["validation_command"] for record in records
            },
            "failure_policy": "Fail closed on missing, stale, ambiguous, or changed input.",
            "parent_client": "codex",
        }
        raw = {
            "schema_version": CODEX_TASK_PROJECTION_SCHEMA_VERSION,
            "work_package_id": caller["work_package_id"],
            "task_ids": copy.deepcopy(task_ids),
            "target_task_id": self.record["id"],
            "task_id": self.record["id"],
            "task_source": "planning/workstreams.yaml",
            "task_version": self.record["task_version"],
            "change_version": self.record["change_version"],
            "allowed_files": ", ".join(self.record["allowed_files"]),
            "forbidden_files": ", ".join(self.record["forbidden_files"]),
            "expected_output": self.record["deliverable"],
            "acceptance_criteria": copy.deepcopy(self.record["acceptance_criteria"]),
            "acceptance_evidence": copy.deepcopy(self.record["acceptance_evidence"]),
            "validation_command": self.record["validation_command"],
            "caller_contract": caller,
            "parent_client": "codex",
            "agent_id": "codex_review_agent",
            "run_id": old["run_id"],
            "session_id": old["session_id"],
            "client": "codex",
            "parent_session_id": old["parent_session_id"],
        }
        self.evidence_fixture.task_artifact = raw
        task_bytes = _qoder_persisted_json(raw)
        _write(self.root, self.evidence_fixture.task_artifact_path, task_bytes)
        self.evidence_input["subject"]["raw_artifacts"]["task"]["sha256"] = _hash(task_bytes)
        completion = copy.deepcopy(self.evidence_fixture.completion_artifact)
        completion.update({
            "agent_id": raw["agent_id"],
            "client": "codex",
            "parent_client": "codex",
        })
        self.evidence_fixture.completion_artifact = completion
        completion_bytes = _qoder_persisted_json(completion)
        _write(
            self.root, self.evidence_fixture.completion_artifact_path,
            completion_bytes,
        )
        self.evidence_input["subject"]["raw_artifacts"]["completion"]["sha256"] = _hash(completion_bytes)
        self.evidence_input["subject"]["identity"] = {
            field: raw[field]
            for field in (
                "parent_session_id", "agent_id", "run_id", "session_id",
                "client", "parent_client",
            )
        }
        self._materialize_evidence()
        self._materialize_issuer()

    def refresh_catalog(self, mutate) -> None:
        catalog = yaml.safe_load((self.root / "planning/workstreams.yaml").read_bytes())
        mutate(catalog)
        data = yaml.safe_dump(catalog, sort_keys=False).encode()
        _write(self.root, "planning/workstreams.yaml", data)
        self.evidence_input["task"]["task_source"]["sha256"] = _hash(data)
        self._materialize_evidence()

    def retarget_task(self, task_id, mutate_catalog) -> None:
        catalog = yaml.safe_load((self.root / "planning/workstreams.yaml").read_bytes())
        mutate_catalog(catalog)
        data = yaml.safe_dump(catalog, sort_keys=False).encode()
        _write(self.root, "planning/workstreams.yaml", data)
        self.catalog = catalog
        self.catalog_tasks = {task["id"]: task for task in _tasks(catalog)}
        self.record = self.catalog_tasks[task_id]
        registry = yaml.safe_load((self.root / "harness/gate-check-registry.yaml").read_bytes())
        entry = next((item for item in registry["entries"] if item["subject_task_id"] == task_id), None)
        if entry is not None:
            for locator in entry["consumed_inputs"]:
                if (REPO / locator).is_file():
                    self._copy(locator)
        for locator in self.record["allowed_files"]:
            if (REPO / locator).is_file():
                self._copy(locator)
            elif not (self.root / locator).exists():
                _write(
                    self.root,
                    locator,
                    canonical_json_bytes({"task_id": task_id, "status": "READY_FOR_VALIDATION"}),
                )
        self.evidence_input = self._build_evidence_input()
        self._materialize_evidence()
        self._materialize_issuer()

    def rebind_changed_files(self) -> None:
        snapshot = copy.deepcopy(self.evidence_fixture.snapshot)
        for locator, entry in snapshot["files"].items():
            entry["state"] = "present"
            entry["sha256"] = _hash((self.root / locator).read_bytes())
        self.evidence_fixture.snapshot = snapshot
        data = evidence_canonical_json(snapshot)
        _write(self.root, self.evidence_fixture.snapshot_path, data)
        self.evidence_input["subject"]["raw_artifacts"]["changed_file_snapshot"]["sha256"] = _hash(data)
        self._materialize_evidence()

    def _materialize_issuer(self, receipt_kinds: list[str] | None = None, *, actor_id="codex-main") -> None:
        generation = self.issuer_generation
        def generated(last):
            return f"14000000-0000-4000-8000-{last + generation * 16:012x}"
        context = {
            "actor_id": actor_id,
            "session_id": "14000000-0000-4000-8000-000000000001",
            "parent_session_id": "14000000-0000-4000-8000-000000000002",
            "client": "codex",
        }
        if self.evidence_packet["subject"]["identity"]["client"] == "codex":
            evidence = {
                "schema_version": ATTESTATION_SCHEMA,
                "actor_type": "human",
                "attestation_id": generated(3),
                "audience": "lexiflow-gate",
                "nonce": generated(4),
                "issued_at": "2026-09-16T11:59:00Z",
                "expires_at": "2026-09-16T12:05:00Z",
                "operator_id": "lexiflow-maintainer",
                "session_id": generated(6),
                "client": "human",
            }
            evidence["signature"] = {
                "algorithm": "hmac-sha256",
                "key_id": "lexiflow-human-gate-v1",
                "value": hmac.new(
                    issuer_test_helpers.IssuerPacketMaterializerTest.HUMAN_KEY,
                    issuer_canonical_json(evidence),
                    hashlib.sha256,
                ).hexdigest(),
            }
            locator = f"tmp/quality/authority/human/{generated(5)}.json"
            key_resolver = lambda key_id: (
                issuer_test_helpers.IssuerPacketMaterializerTest.HUMAN_KEY
                if key_id == "lexiflow-human-gate-v1" else None
            )
        else:
            evidence = {
                "schema_version": ATTESTATION_SCHEMA,
                "actor_type": "codex",
                "attestation_id": generated(3),
                "audience": "lexiflow-gate",
                "nonce": generated(4),
                "issued_at": "2026-09-16T11:59:00Z",
                "expires_at": "2026-09-16T12:05:00Z",
                **context,
            }
            locator = f"tmp/quality/authority/codex/{generated(5)}.json"
            key_resolver = None
        data = issuer_canonical_json(evidence)
        _write(self.root, locator, data)
        materializer = IssuerPacketMaterializer(
            self.root,
            trusted_codex_context=context,
            key_resolver=key_resolver,
            now=lambda: self.NOW,
        )
        result = materializer.materialize(
            {
                "issuer_instance_id": generated(8),
                "receipt_kinds": receipt_kinds or ["TASK_VALIDATION"],
            },
            authority_evidence_locator=locator,
            authority_evidence_sha256=issuer_sha256(data),
            subject_identity=copy.deepcopy(self.evidence_packet["subject"]["identity"]),
        )
        self.issuer_locator = result.locator
        self.issuer_sha = result.sha256
        self.issuer_packet = result.packet
        self.issuer_generation += 1

    def compile(self, *, mode="incremental", receipt_kind="TASK_VALIDATION"):
        return compile_plan(
            self.root,
            mode=mode,
            receipt_kind=receipt_kind,
            evidence_packet_locator=self.evidence_locator,
            evidence_packet_sha256=self.evidence_sha,
            issuer_packet_locator=self.issuer_locator,
            issuer_packet_sha256=self.issuer_sha,
        )

    def create_future_inputs(self) -> None:
        registry = yaml.safe_load((self.root / "harness/gate-check-registry.yaml").read_bytes())
        for entry in registry["entries"]:
            for locator in entry["consumed_inputs"]:
                path = self.root / locator
                if not path.exists():
                    _write(self.root, locator, f"# hermetic future input: {locator}\n".encode())

    def mutate_registry(self, mutate, *, recompute=True) -> None:
        path = self.root / "harness/gate-check-registry.yaml"
        value = yaml.safe_load(path.read_bytes())
        mutate(value)
        if recompute:
            for entry in value.get("entries", []):
                entry["entry_hash"] = sha256_bytes(canonical_json_bytes({k:v for k,v in entry.items() if k != "entry_hash"}))
        path.write_bytes(yaml.safe_dump(value, sort_keys=False).encode())
        self.rebind_changed_files()


class PlannerFixtureTest(unittest.TestCase):
    def setUp(self):
        self.fixture = RealPlannerFixture()
        self.addCleanup(self.fixture.cleanup)

    def assertPlannerError(self, code, call):
        with self.assertRaises(PlannerError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(raised.exception.result, "FAIL")


class TestRealMaterializersAndPlan(PlannerFixtureTest):
    def test_real_qlt7_qlt14_current_catalog_positive(self):
        result = self.fixture.compile()
        plan = result["plan"]
        self.assertEqual(len(self.fixture.catalog_tasks), 113)
        self.assertEqual(plan["task"]["task_id"], "LF-TSK-QLT-0008")
        self.assertEqual(plan["issuer_packet"]["packet"], self.fixture.issuer_packet)
        self.assertEqual([c["check_id"] for c in plan["checks"]], ["qlt.planner.validate"])
        self.assertEqual(plan["content_fingerprint"], result["content_fingerprint"])
        self.assertEqual(result["canonical_bytes"], canonical_json_bytes(plan))

    def test_zero_write_packet_compiles_only_for_read_only_receipt_routes(self):
        _set_changed_files(
            self.fixture.evidence_fixture,
            self.fixture.evidence_input,
            [],
        )
        self.fixture._materialize_evidence()

        self.assertPlannerError(
            "evidence-packet-incomplete",
            lambda: self.fixture.compile(receipt_kind="TASK_VALIDATION"),
        )

        self.fixture._materialize_issuer(
            ["INDEPENDENT_REVIEW", "CATALOG_DECISION"]
        )
        for receipt_kind in ("INDEPENDENT_REVIEW", "CATALOG_DECISION"):
            with self.subTest(receipt_kind=receipt_kind):
                plan = self.fixture.compile(receipt_kind=receipt_kind)["plan"]
                self.assertEqual(plan["scope"]["changed_files"], [])
                self.assertEqual(plan["checks"], [])
                self.assertEqual(plan["execution"], {
                    "layer": "evidence-consumption",
                    "checker_execution": "forbidden",
                    "source": "prior-immutable-receipts",
                })

    def test_plan_is_complete_and_fingerprint_covers_every_field(self):
        result = self.fixture.compile(); plan = result["plan"]
        self.assertEqual(set(plan), {"schema_version","mode","receipt_kind","execution","task","subject","issuer_packet","scope","consumed_inputs","checks","expectations","registry","content_fingerprint"})
        self.assertEqual(plan["execution"], {
            "layer": "delivery-validation",
            "checker_execution": "required",
            "source": "selected-registry-checks",
        })
        self.assertIn("changed_file_snapshot", plan["subject"]["raw_artifacts"])
        self.assertIn("tests", plan["subject"]["raw_artifacts"])
        self.assertEqual(len(plan["task"]["dependencies"]), 4)
        self.assertTrue(all("resolved_producer" in dep for dep in plan["task"]["dependencies"]))
        self.assertEqual(plan["checks"][0]["effect_checks"], [
            {"effect_id": "behavior", "explicit_status": "PASS"},
            {"effect_id": "regression", "explicit_status": "PASS"},
        ])
        self.assertEqual(plan["checks"][0]["subject_task"], {
            "task_id": self.fixture.record["id"],
            "task_version": self.fixture.record["task_version"],
            "change_version": self.fixture.record["change_version"],
            "owner": self.fixture.record["owner"],
        })
        self.assertEqual(
            [item["criterion_id"] for item in plan["expectations"]["acceptance"]],
            [f"LF-TSK-QLT-0008.acceptance_criteria[{index}]" for index in range(4)],
        )
        self.assertTrue(all(
            item["catalog_evidence"] == self.fixture.record["acceptance_evidence"]
            and item["attested_evidence"] == self.fixture.evidence_packet["subject"]["main_agent_attestation"]["result_fields"]["acceptance_evidence"]
            for item in plan["expectations"]["acceptance"]
        ))
        self.assertEqual(plan["expectations"]["effect_checks"], [
            {"check_id": "qlt.planner.validate", "effect_id": "behavior", "required": True, "attested_status": "PASS"},
            {"check_id": "qlt.planner.validate", "effect_id": "regression", "required": True, "attested_status": "PASS"},
        ])
        explicit_risks = self.fixture.evidence_packet["subject"]["main_agent_attestation"]["result_fields"]["risks"]
        self.assertEqual([risk["risk_id"] for risk in plan["expectations"]["risks"]], explicit_risks)
        self.assertTrue(all(risk["required_fields"] == ["impact", "mitigation", "fallback", "remaining_limitation"] for risk in plan["expectations"]["risks"]))
        payload = {k:v for k,v in plan.items() if k != "content_fingerprint"}
        self.assertEqual(plan["content_fingerprint"], sha256_bytes(canonical_json_bytes(payload)))
        mutations = (
            lambda changed: changed["expectations"]["acceptance"][0].__setitem__("criterion", "changed"),
            lambda changed: changed["expectations"]["effect_checks"][0].__setitem__("attested_status", "FAIL"),
            lambda changed: changed["expectations"]["risks"][0]["required_fields"].append("changed"),
            lambda changed: changed["checks"][0]["subject_task"].__setitem__("task_version", 999),
        )
        for mutate in mutations:
            changed = copy.deepcopy(plan); mutate(changed)
            self.assertNotEqual(plan["content_fingerprint"], sha256_bytes(canonical_json_bytes({k:v for k,v in changed.items() if k != "content_fingerprint"})))

    def test_deterministic_zero_write(self):
        before = _snapshot(self.fixture.root)
        one = self.fixture.compile(); two = self.fixture.compile()
        self.assertEqual(one, two)
        self.assertEqual(before, _snapshot(self.fixture.root))

    def test_no_process_uuid_clock_scan_or_write(self):
        real_open = planner.os.open
        flags_seen = []
        def audited_open(path, flags, *args, **kwargs):
            flags_seen.append(flags)
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            return real_open(path, flags, *args, **kwargs)
        with mock.patch.object(planner.os, "open", side_effect=audited_open), \
             mock.patch("subprocess.run") as run, mock.patch("subprocess.Popen") as popen, \
             mock.patch("uuid.uuid4") as uuid4, mock.patch("time.time") as clock, \
             mock.patch("os.scandir") as scan:
            self.fixture.compile()
        run.assert_not_called(); popen.assert_not_called(); uuid4.assert_not_called(); clock.assert_not_called(); scan.assert_not_called()
        self.assertTrue(flags_seen)


class TestRawTaskAndCatalogBinding(PlannerFixtureTest):
    def test_same_byte_task_source_alias_is_not_current(self):
        alias_locator = "tmp/aliases/workstreams-copy.yaml"
        _write(
            self.fixture.root,
            alias_locator,
            (self.fixture.root / "planning/workstreams.yaml").read_bytes(),
        )
        self.fixture.evidence_input["task"]["task_source"]["locator"] = alias_locator
        raw = self.fixture._raw_task()
        raw["task_source"] = alias_locator
        self.fixture.evidence_fixture.task_artifact = raw
        raw_bytes = evidence_canonical_json(raw)
        _write(self.fixture.root, self.fixture.evidence_fixture.task_artifact_path, raw_bytes)
        self.fixture.evidence_input["subject"]["raw_artifacts"]["task"]["sha256"] = _hash(raw_bytes)
        self.fixture._materialize_evidence()

        self.assertPlannerError("unsafe-locator", self.fixture.compile)

    def test_each_raw_caller_and_runtime_field_is_bound(self):
        planner_cases = {
            "validation_command": "python3 evil.py",
            "required_context": "",
            "expected_output": "",
            "acceptance_criteria": ["forged"],
            "acceptance_evidence": ["forged"],
            "failure_policy": "",
            "permission_mode": "unregistered",
            "goal": "different goal",
            "estimated_minutes": 7,
            "primary_owner": "LF-WS-ARCH",
            "harness_manifest_sha256": "not-a-hash",
        }
        for field, value in planner_cases.items():
            with self.subTest(field=field):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                fixture.refresh_raw_task(lambda raw, f=field, v=value: raw.__setitem__(f,v))
                expected = (
                    "invalid-entry" if value == ""
                    else "invalid-task" if field in {
                        "permission_mode", "estimated_minutes", "harness_manifest_sha256",
                    }
                    else "task-reconciliation-fail" if field != "goal"
                    else None
                )
                if expected is None:
                    self.assertEqual(fixture.compile()["plan"]["task"]["goal"], value)
                else:
                    self.assertPlannerError(expected, fixture.compile)

    def test_real_qoder_persisted_task_shape_excludes_catalog_only_metadata(self):
        raw = self.fixture._raw_task()
        self.assertEqual(
            set(raw),
            set(REQUIRED_HANDOFF) | set(RUNTIME_IDENTITY_FIELDS) | {"parent_session_id", "permission_mode"},
        )
        self.assertNotIn("owner", raw)
        self.assertNotIn("discovered_from", raw)
        self.assertNotIn("file_claims", raw)
        self.assertEqual(raw["permission_mode"], "bypass_permissions")
        raw_bytes = (self.fixture.root / self.fixture.evidence_fixture.task_artifact_path).read_bytes()
        self.assertTrue(raw_bytes.startswith(b"{\n  \"goal\""))
        self.assertNotEqual(raw_bytes, evidence_canonical_json(raw))
        self.assertEqual(self.fixture.compile()["plan"]["task"]["owner"], "LF-WS-QLT")

        for field in ("owner", "discovered_from", "file_claims"):
            with self.subTest(field=field):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                fixture.refresh_raw_task(lambda value, name=field: value.__setitem__(name, "caller-smuggled"))
                self.assertPlannerError("invalid-task", fixture.compile)

    def test_real_qoder_optional_initial_and_resume_fields_are_bounded(self):
        for field, value in (("title", "Formal current-input validation"), ("_resume_mode", True)):
            with self.subTest(field=field):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                fixture.refresh_raw_task(lambda raw, name=field, item=value: raw.__setitem__(name, item))
                self.assertEqual(fixture.compile()["plan"]["task"]["task_id"], "LF-TSK-QLT-0008")

        for field, value in (("title", ""), ("_resume_mode", False), ("unexpected", "value")):
            with self.subTest(field=field):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                fixture.refresh_raw_task(lambda raw, name=field, item=value: raw.__setitem__(name, item))
                self.assertPlannerError("invalid-task", fixture.compile)

        upstream_fields = {
            "task_id": "LF-TSK-QLT-9999", "task_source": "other.yaml",
            "task_version": 99, "change_version": "9.9.9",
            "allowed_files": "scripts/**", "forbidden_files": "nothing/**",
            "parent_client": "other", "agent_id": "other_agent",
            "run_id": "24000000-0000-4000-8000-000000000001",
            "session_id": "24000000-0000-4000-8000-000000000002",
            "client": "other", "parent_session_id": "24000000-0000-4000-8000-000000000003",
        }
        for field, value in upstream_fields.items():
            with self.subTest(field=field, layer="upstream"):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                raw = fixture._raw_task(); raw[field] = value
                _write(fixture.root, fixture.evidence_fixture.task_artifact_path, evidence_canonical_json(raw))
                self.assertPlannerError("evidence-drift", fixture.compile)


class TestFormalClosureCompileMatrix(PlannerFixtureTest):
    def test_all_thirty_g1_closure_tasks_compile_from_current_catalog(self):
        tasks = self.fixture.catalog_tasks
        self.fixture.create_future_inputs()
        closure: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in closure:
                return
            closure.add(task_id)
            for dependency in tasks[task_id].get("depends_on", []):
                if dependency.get("type") in {"hard", "contract"}:
                    visit(dependency["task_id"])

        visit("LF-TSK-ARCH-0008")
        ordered = [task["id"] for task in _tasks(self.fixture.catalog) if task["id"] in closure]
        self.assertEqual(len(ordered), 30)
        for task_id in ordered:
            with self.subTest(task_id=task_id):
                self.fixture.retarget_task(task_id, lambda catalog: None)
                plan = self.fixture.compile()["plan"]
                self.assertEqual(plan["task"]["task_id"], task_id)
                self.assertIn(task_id, {check["subject_task"]["task_id"] for check in plan["checks"]})

    def test_duplicate_task_id_anywhere_in_seed_tasks_is_rejected(self):
        def duplicate(catalog):
            tasks = _tasks(catalog)
            tasks[-1]["id"] = tasks[0]["id"]
        self.fixture.refresh_catalog(duplicate)
        self.assertPlannerError("duplicate-task-id", self.fixture.compile)

    def test_current_task_and_dependency_versions_are_bound(self):
        def stale_current(catalog):
            next(t for t in _tasks(catalog) if t["id"] == "LF-TSK-QLT-0008")["task_version"] = 99
        self.fixture.refresh_catalog(stale_current)
        self.assertPlannerError("non-current-task-version", self.fixture.compile)

    def test_stale_dependency_is_rejected(self):
        def stale(catalog):
            task = next(t for t in _tasks(catalog) if t["id"] == "LF-TSK-QLT-0008")
            task["depends_on"][0]["required_task_version"] = 999
        self.fixture.refresh_catalog(stale)
        self.assertPlannerError("dependency-version-mismatch", self.fixture.compile)

    def test_malformed_dependency_and_resolved_producer_identity_are_typed(self):
        cases = (
            (lambda catalog: next(task for task in _tasks(catalog) if task["id"] == "LF-TSK-QLT-0008")["depends_on"][0].__setitem__("type", []), "invalid-dependency"),
            (lambda catalog: next(task for task in _tasks(catalog) if task["id"] == "LF-TSK-QLT-0008")["depends_on"][0].__setitem__("task_id", []), "invalid-dependency"),
            (lambda catalog: next(task for task in _tasks(catalog) if task["id"] == "LF-TSK-QLT-0002").__setitem__("owner", []), "invalid-task"),
            (lambda catalog: next(task for task in _tasks(catalog) if task["id"] == "LF-TSK-QLT-0007")["produced_contracts"][0].__setitem__("extra", True), "invalid-dependency"),
        )
        for mutate, code in cases:
            with self.subTest(mutate=mutate):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                fixture.refresh_catalog(mutate)
                self.assertPlannerError(code, fixture.compile)

    def test_catalog_lineage_distinguishes_legacy_absent_from_explicit_null(self):
        result = self.fixture.compile()
        qlt2 = next(dep for dep in result["plan"]["task"]["dependencies"] if dep["task_id"] == "LF-TSK-QLT-0002")
        self.assertEqual(qlt2["resolved_producer"]["owner"], "LF-WS-QLT")

        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        fixture.refresh_catalog(lambda catalog: next(
            task for task in _tasks(catalog) if task["id"] == "LF-TSK-QLT-0002"
        ).__setitem__("owner", None))
        self.assertPlannerError("invalid-task", fixture.compile)

        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        fixture.refresh_catalog(lambda catalog: next(
            task for task in _tasks(catalog) if task["id"] == "LF-TSK-QLT-0002"
        ).__setitem__("validation_command", None))
        self.assertPlannerError("invalid-task", fixture.compile)

        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        fixture.refresh_catalog(lambda catalog: next(
            task for task in _tasks(catalog) if task["id"] == "LF-TSK-QLT-0002"
        ).__setitem__("task_version", 2))
        fixture.refresh_raw_task(lambda raw: raw["task_versions"].__setitem__(
            "LF-TSK-QLT-0002", 2
        ))
        self.assertPlannerError("dependency-version-mismatch", fixture.compile)

    def test_future_registry_task_requires_explicit_current_lineage(self):
        for field in ("owner", "validation_command", "task_version", "change_version"):
            for mutation in ("missing", "null"):
                with self.subTest(field=field, mutation=mutation):
                    fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                    def mutate(catalog, selected=field, action=mutation):
                        task = next(task for task in _tasks(catalog) if task["id"] == "LF-TSK-QLT-0009")
                        if action == "missing":
                            task.pop(selected, None)
                        else:
                            task[selected] = None
                    fixture.refresh_catalog(mutate)
                    expected = "command-mismatch" if field == "validation_command" and mutation == "missing" else "invalid-task"
                    self.assertPlannerError(expected, fixture.compile)

    def test_packet_claims_must_equal_current_catalog_file_claims(self):
        self.fixture.evidence_input["scope"]["normalized"]["canonical_file_claims"] = ["scripts/gates/planner.py"]
        self.fixture.evidence_input["scope"]["three_way_reconciliation"]["changed_without_claim"] = [
            "harness/gate-check-registry.yaml", "tests/gates/test_gate_planner.py"
        ]
        self.fixture.evidence_input["scope"]["three_way_reconciliation"]["status"] = "FAIL"
        with self.assertRaises(Exception):
            self.fixture._materialize_evidence()


class TestEvidenceFreeze(PlannerFixtureTest):
    def test_forged_result_packet_and_stale_hash_fail_closed(self):
        path = self.fixture.root / self.fixture.evidence_locator
        packet = json.loads(path.read_bytes())
        packet["subject"]["main_agent_attestation"]["result_fields"]["status"] = "FAIL"
        data = canonical_json_bytes(packet); path.write_bytes(data); self.fixture.evidence_sha = _hash(data)
        self.assertPlannerError("evidence-packet-incomplete", self.fixture.compile)
        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        self.assertPlannerError("evidence-drift", lambda: compile_plan(
            fixture.root, mode="incremental", receipt_kind="TASK_VALIDATION",
            evidence_packet_locator=fixture.evidence_locator, evidence_packet_sha256="0"*64,
            issuer_packet_locator=fixture.issuer_locator, issuer_packet_sha256=fixture.issuer_sha))

    def test_noncanonical_and_duplicate_evidence_packet_are_rejected(self):
        path = self.fixture.root / self.fixture.evidence_locator
        packet = json.loads(path.read_bytes()); data = json.dumps(packet, indent=2).encode(); path.write_bytes(data); self.fixture.evidence_sha = _hash(data)
        self.assertPlannerError("invalid-canonical-json", self.fixture.compile)
        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        path = fixture.root / fixture.evidence_locator; original = path.read_bytes()
        duplicate = b'{"schema_version":"forged",' + original[1:]
        path.write_bytes(duplicate); fixture.evidence_sha = _hash(duplicate)
        self.assertPlannerError("invalid-duplicate-key", fixture.compile)


class TestIssuerFreeze(PlannerFixtureTest):
    def test_canonical_codex_issuer_actor_compiles_with_real_materializer(self):
        self.fixture._materialize_issuer(actor_id="/root/independent_validator")
        plan = self.fixture.compile()
        self.assertEqual(plan["plan"]["issuer_packet"]["packet"]["actor_id"], "/root/independent_validator")

    def test_canonical_path_does_not_weaken_qoder_issuer_actor_format(self):
        self._forge(lambda packet: packet.update(actor_type="qoder", actor_id="/root/child"))
        self.assertPlannerError("invalid-issuer-packet", self.fixture.compile)

    def _forge(self, mutate):
        packet = json.loads((self.fixture.root / self.fixture.issuer_locator).read_bytes())
        mutate(packet)
        data = canonical_json_bytes(packet)
        (self.fixture.root / self.fixture.issuer_locator).write_bytes(data)
        self.fixture.issuer_sha = _hash(data)

    def test_forged_identity_authority_and_result_fields_fail(self):
        cases = (
            (lambda p: p.__setitem__("actor_id", "forged"), "invalid-issuer-packet"),
            (lambda p: p.__setitem__("role", "catalog-owner"), "invalid-issuer-packet"),
            (lambda p: p["authority"].__setitem__("authority_id", "forged"), "invalid-issuer-packet"),
            (lambda p: p["authority"].__setitem__("verifier_id", "forged"), "invalid-issuer-packet"),
            (lambda p: p["authority"].__setitem__("verifier_abi", True), "invalid-issuer-packet"),
            (lambda p: p["authorized_receipt_kinds"].append("FORGED"), "issuer-unauthorized-receipt"),
            (lambda p: p.__setitem__("result", "PASS"), "invalid-issuer-packet"),
        )
        for i, (mutate, expected_code) in enumerate(cases):
            with self.subTest(case=i):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                packet = json.loads((fixture.root / fixture.issuer_locator).read_bytes()); mutate(packet)
                data = canonical_json_bytes(packet); (fixture.root / fixture.issuer_locator).write_bytes(data); fixture.issuer_sha = _hash(data)
                self.assertPlannerError(expected_code, fixture.compile)

    def test_wrong_registry_evidence_and_provenance_hash_fail(self):
        for field in ("registry_sha256", "evidence_sha256"):
            with self.subTest(field=field):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                packet = json.loads((fixture.root / fixture.issuer_locator).read_bytes())
                packet["authority"][field] = "0" * 64
                data = canonical_json_bytes(packet); (fixture.root / fixture.issuer_locator).write_bytes(data); fixture.issuer_sha = _hash(data)
                self.assertPlannerError("invalid-issuer-packet", fixture.compile)
        self._forge(lambda p: p["authority"]["provenance"][0].__setitem__("kind", "forged-kind"))
        self.assertPlannerError("invalid-issuer-packet", self.fixture.compile)
        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        packet = json.loads((fixture.root / fixture.issuer_locator).read_bytes())
        packet["authority"]["provenance"][0]["sha256"] = "0" * 64
        data = canonical_json_bytes(packet); (fixture.root / fixture.issuer_locator).write_bytes(data); fixture.issuer_sha = _hash(data)
        self.assertPlannerError("invalid-issuer-packet", fixture.compile)

    def test_noncanonical_stale_namespace_and_wrong_instance_fail(self):
        path = self.fixture.root / self.fixture.issuer_locator
        value = json.loads(path.read_bytes()); path.write_bytes(json.dumps(value, indent=2).encode()); self.fixture.issuer_sha = _hash(path.read_bytes())
        self.assertPlannerError("invalid-canonical-json", self.fixture.compile)
        self.assertPlannerError("invalid-issuer-packet", lambda: compile_plan(self.fixture.root, mode="incremental", receipt_kind="TASK_VALIDATION",
            evidence_packet_locator=self.fixture.evidence_locator, evidence_packet_sha256=self.fixture.evidence_sha,
            issuer_packet_locator=self.fixture.issuer_locator.replace("issuers/", "other/"), issuer_packet_sha256=self.fixture.issuer_sha))

    def test_real_pretty_qoder_provenance_can_issue_for_codex_subject(self):
        self.fixture.use_codex_work_package()
        run_id = "15000000-0000-4000-8000-000000000001"
        task = {
            "task_id": "LF-TSK-QLT-0001",
            "task_version": 1,
            "change_version": "1.0.0",
            "agent_id": "agent_qoder_issuer",
            "run_id": run_id,
            "session_id": "15000000-0000-4000-8000-000000000002",
            "client": "qoder",
            "parent_client": "codex",
            "parent_session_id": "15000000-0000-4000-8000-000000000003",
        }
        completion = {**task, "status": "finished", "exit_code": 0}
        base = f"tmp/qoder-tasks/{run_id}"
        task_bytes = _qoder_persisted_json(task)
        completion_bytes = _qoder_persisted_json(completion)
        _write(self.fixture.root, f"{base}/task.json", task_bytes)
        _write(self.fixture.root, f"{base}/completion.json", completion_bytes)
        os.utime(
            self.fixture.root / f"{base}/completion.json",
            (self.fixture.NOW.timestamp(), self.fixture.NOW.timestamp()),
        )
        issuer = IssuerPacketMaterializer(
            self.fixture.root, now=lambda: self.fixture.NOW
        ).materialize(
            {
                "issuer_instance_id": "15000000-0000-4000-8000-000000000004",
                "receipt_kinds": ["TASK_VALIDATION"],
            },
            authority_evidence_locator=f"{base}/completion.json",
            authority_evidence_sha256=issuer_sha256(completion_bytes),
            subject_identity=copy.deepcopy(
                self.fixture.evidence_packet["subject"]["identity"]
            ),
        )
        self.fixture.issuer_locator = issuer.locator
        self.fixture.issuer_sha = issuer.sha256
        plan = self.fixture.compile()["plan"]
        self.assertEqual(plan["issuer_packet"]["packet"]["actor_type"], "qoder")
        self.assertEqual(plan["subject"]["identity"]["client"], "codex")

    def test_entire_authority_registry_is_strict_not_only_selected_actor(self):
        registry_path = self.fixture.root / "harness/gate-issuer-authorities.yaml"
        registry = yaml.safe_load(registry_path.read_bytes())
        registry["authorities"]["human"]["actors"]["lexiflow-maintainer"]["extra"] = True
        registry_bytes = yaml.safe_dump(registry, sort_keys=False).encode(); registry_path.write_bytes(registry_bytes)
        packet_path = self.fixture.root / self.fixture.issuer_locator
        packet = json.loads(packet_path.read_bytes()); packet["authority"]["registry_sha256"] = _hash(registry_bytes)
        data = canonical_json_bytes(packet); packet_path.write_bytes(data); self.fixture.issuer_sha = _hash(data)
        self.assertPlannerError("invalid-issuer-packet", self.fixture.compile)

    def test_packet_and_authority_timestamps_are_strict_rfc3339(self):
        self._forge(lambda packet: packet.__setitem__("materialized_at", "2026-09-16Z"))
        self.assertPlannerError("invalid-issuer-packet", self.fixture.compile)

        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        packet_path = fixture.root / fixture.issuer_locator
        packet = json.loads(packet_path.read_bytes())
        evidence_path = fixture.root / packet["authority"]["evidence_locator"]
        evidence = json.loads(evidence_path.read_bytes())
        evidence["issued_at"] = "2026-09-16 11:59:00Z"
        evidence_bytes = canonical_json_bytes(evidence)
        evidence_path.write_bytes(evidence_bytes)
        evidence_hash = _hash(evidence_bytes)
        packet["authority"]["evidence_sha256"] = evidence_hash
        packet["authority"]["provenance"][0]["sha256"] = evidence_hash
        packet_bytes = canonical_json_bytes(packet)
        packet_path.write_bytes(packet_bytes); fixture.issuer_sha = _hash(packet_bytes)
        self.assertPlannerError("invalid-issuer-packet", fixture.compile)


class TestRegistryAndSelection(PlannerFixtureTest):
    @staticmethod
    def _activate_extension_catalog(catalog):
        task = next(task for task in _tasks(catalog) if task["id"] == "LF-TSK-ADP-0005")
        task["owner"] = "LF-WS-ADP"
        task["validation_command"] = "python3 -m unittest discover -s tests/gates -p 'test_architecture_profile.py'"

    @staticmethod
    def _extension_entry():
        return {
            "check_id": "arch.profile.validate", "check_version": 1,
            "owner": "LF-WS-QLT", "subject_task_id": "LF-TSK-ADP-0005",
            "subject_task_version": 1, "subject_change_version": "1.0.0",
            "modes": ["incremental", "full"],
            "triggers": [{"path": "planning/workstreams.yaml", "terminal": True}],
            "required": True,
            "declared_validation_command": "python3 -m unittest discover -s tests/gates -p 'test_architecture_profile.py'",
            "command_id": "qlt.architecture-profile.validate.v1",
            "fixed_argv": ["python3", "-m", "unittest", "discover", "-s", "tests/gates", "-p", "test_architecture_profile.py"],
            "cwd": ".", "timeout_seconds": 180,
            "consumed_inputs": ["planning/workstreams.yaml"],
            "outcome_contract": {"schema": "lexiflow.check-outcome.v1", "required_fields": ["exit_code", "stdout_locator", "stderr_locator", "typed_result"]},
            "acceptance_criterion_ids": ["LF-TSK-ADP-0005.acceptance_criteria[0]"],
            "effect_check_ids": ["behavior", "regression"], "entry_hash": "0" * 64,
        }

    def test_registry_has_exact_catalog_derived_inventory(self):
        registry = yaml.safe_load((self.fixture.root / "harness/gate-check-registry.yaml").read_bytes())
        self.assertEqual(registry["execution"], {
            "owner": "python-control-plane",
            "executor": "python3",
            "source_scan": "forbidden",
            "task_validation": "executes-selected-checks",
            "independent_review": "consumes-immutable-receipts",
            "catalog_decision": "consumes-immutable-receipts",
        })
        expected = [task["id"] for task in _tasks(self.fixture.catalog) if "validation_command" in task]
        self.assertEqual([e["subject_task_id"] for e in registry["entries"]], expected)
        self.assertEqual(len(expected), 30)
        self.assertEqual(len({e["entry_hash"] for e in registry["entries"]}), 30)
        for entry in registry["entries"]:
            payload = {key:value for key,value in entry.items() if key != "entry_hash"}
            self.assertEqual(entry["entry_hash"], sha256_bytes(canonical_json_bytes(payload)))

    def test_registry_execution_contract_fails_closed_when_source_scan_is_enabled(self):
        self.fixture.mutate_registry(
            lambda registry: registry["execution"].__setitem__("source_scan", "allowed")
        )
        self.assertPlannerError("invalid-registry", self.fixture.compile)

    def test_catalog_derived_registry_inventory_extends_without_planner_change(self):
        self.fixture.refresh_catalog(self._activate_extension_catalog)
        self.fixture.mutate_registry(lambda registry: registry["entries"].append(self._extension_entry()))
        result = self.fixture.compile()
        self.assertEqual(result["plan"]["checks"][0]["subject_task"]["task_id"], "LF-TSK-QLT-0008")

        self.fixture.create_future_inputs()
        full = next(
            check for check in self.fixture.compile(mode="full")["plan"]["checks"]
            if check["subject_task"]["task_id"] == "LF-TSK-ADP-0005"
        )
        self.assertEqual(full["owner"], "LF-WS-QLT")
        self.assertEqual(full["subject_task"]["owner"], "LF-WS-ADP")

    def test_non_qlt_subject_can_compile_a_real_plan(self):
        def activate(catalog):
            self._activate_extension_catalog(catalog)
            task = next(task for task in _tasks(catalog) if task["id"] == "LF-TSK-ADP-0005")
            task.update({
                "allowed_files": ["scripts/gates/planner.py", "tests/gates/test_gate_planner.py"],
                "forbidden_files": ["planning/**", "openspec/**", "docs/**"],
                "file_claims": [
                    {"path": "scripts/gates/planner.py", "mode": "write", "owner": "LF-WS-ADP"},
                    {"path": "tests/gates/test_gate_planner.py", "mode": "write", "owner": "LF-WS-ADP"},
                ],
                "discovered_from": "LF-TSK-ADP-0005",
            })
        self.fixture.retarget_task("LF-TSK-ADP-0005", activate)
        self.fixture.mutate_registry(lambda registry: registry["entries"].append(self._extension_entry()))
        plan = self.fixture.compile()["plan"]
        subject_check = next(
            check for check in plan["checks"]
            if check["subject_task"]["task_id"] == "LF-TSK-ADP-0005"
        )
        self.assertEqual(plan["task"]["owner"], "LF-WS-ADP")
        self.assertEqual(subject_check["owner"], "LF-WS-QLT")
        self.assertEqual(subject_check["subject_task"]["owner"], "LF-WS-ADP")

    def test_catalog_derived_inventory_rejects_missing_extra_mismatch_and_duplicate_subject(self):
        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        fixture.refresh_catalog(self._activate_extension_catalog)
        self.assertPlannerError("invalid-registry", fixture.compile)

        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        fixture.mutate_registry(lambda registry: registry["entries"].append(self._extension_entry()))
        self.assertPlannerError("command-mismatch", fixture.compile)

        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        fixture.refresh_catalog(self._activate_extension_catalog)
        mismatch = self._extension_entry(); mismatch["subject_task_version"] = 2
        fixture.mutate_registry(lambda registry: registry["entries"].append(mismatch))
        self.assertPlannerError("invalid-registry", fixture.compile)

        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        fixture.refresh_catalog(self._activate_extension_catalog)
        one = self._extension_entry(); two = copy.deepcopy(one)
        two["check_id"] = "qlt.dispatch-preflight.second"; two["command_id"] = "qlt.dispatch-preflight.second.v1"
        fixture.mutate_registry(lambda registry: registry["entries"].extend([one, two]))
        self.assertPlannerError("duplicate-subject-task", fixture.compile)

    def test_checker_owner_and_subject_owner_are_independently_bound(self):
        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        fixture.refresh_catalog(self._activate_extension_catalog)
        entry = self._extension_entry(); entry["owner"] = "LF-WS-ADP"
        fixture.mutate_registry(lambda registry: registry["entries"].append(entry))
        self.assertPlannerError("invalid-owner", fixture.compile)

        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        def forge_subject_owner(catalog):
            self._activate_extension_catalog(catalog)
            next(task for task in _tasks(catalog) if task["id"] == "LF-TSK-ADP-0005")["owner"] = "LF-WS-QLT"
        fixture.refresh_catalog(forge_subject_owner)
        fixture.mutate_registry(lambda registry: registry["entries"].append(self._extension_entry()))
        self.assertPlannerError("invalid-task", fixture.compile)

    def test_incremental_reads_only_selected_consumed_inputs(self):
        result = self.fixture.compile()
        self.assertEqual([c["check_id"] for c in result["plan"]["checks"]], ["qlt.planner.validate"])
        locators = {d["locator"] for d in result["plan"]["consumed_inputs"]}
        self.assertNotIn("scripts/gates/executor.py", locators)

    def test_full_live_missing_future_input_is_typed_fail(self):
        self.assertPlannerError("unsafe-locator", lambda: self.fixture.compile(mode="full"))

    def test_full_hermetic_selects_all_entries_in_registry_order(self):
        self.fixture.create_future_inputs()
        result = self.fixture.compile(mode="full")
        registry = yaml.safe_load((self.fixture.root / "harness/gate-check-registry.yaml").read_bytes())
        self.assertEqual([c["check_id"] for c in result["plan"]["checks"]], [e["check_id"] for e in registry["entries"]])
        self.assertTrue(all(c["selection_reasons"] == [{"kind":"full-mode"}] for c in result["plan"]["checks"]))

    def test_full_review_and_catalog_plans_consume_evidence_without_reselecting_the_30_checks(self):
        self.fixture.create_future_inputs()
        self.fixture._materialize_issuer(["INDEPENDENT_REVIEW", "CATALOG_DECISION"])
        for receipt_kind in ("INDEPENDENT_REVIEW", "CATALOG_DECISION"):
            with self.subTest(receipt_kind=receipt_kind):
                plan = self.fixture.compile(mode="full", receipt_kind=receipt_kind)["plan"]
                self.assertEqual(plan["checks"], [])
                self.assertEqual(plan["execution"]["checker_execution"], "forbidden")

    def test_mode_must_be_supported_by_matched_entry(self):
        self.fixture.mutate_registry(lambda r: _registry_entry(r).__setitem__("modes", ["full"]))
        self.assertPlannerError("invalid-mode", self.fixture.compile)

    def test_trigger_specificity_exact_child_subtree(self):
        def mutate(registry):
            entry = _registry_entry(registry)
            entry["triggers"] = [
                {"path":"scripts/gates/**","terminal":True},
                {"path":"scripts/gates/*","terminal":True},
                {"path":"scripts/gates/planner.py","terminal":True},
                {"path":"tests/gates/**","terminal":True},
                {"path":"harness/gate-check-registry.yaml","terminal":True},
            ]
        self.fixture.mutate_registry(mutate)
        result = self.fixture.compile(); reasons = result["plan"]["checks"][0]["selection_reasons"]
        reason = next(r for r in reasons if r.get("changed_file") == "scripts/gates/planner.py")
        self.assertEqual((reason["trigger"], reason["specificity"]), ("scripts/gates/planner.py", "exact"))

    def test_dispatch_child_and_subtree_truth_table(self):
        child = planner._dispatch("scripts/gates/*")
        subtree = planner._dispatch("scripts/gates/**")
        self.assertTrue(planner._match(child, "scripts/gates/planner.py"))
        self.assertFalse(planner._match(child, "scripts/gates/nested/planner.py"))
        self.assertFalse(planner._match(subtree, "scripts/gates"))
        self.assertTrue(planner._match(subtree, "scripts/gates/nested/planner.py"))

    def test_invalid_trigger_matrix(self):
        invalid = ("a/**/b", "a/***", "a/?", "a/[x]", "a/{x}", "a/../b", "a/latest", "a\\b")
        for value in invalid:
            with self.subTest(value=value):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                fixture.mutate_registry(lambda r,v=value: _registry_entry(r)["triggers"].__setitem__(0,{"path":v,"terminal":True}))
                self.assertPlannerError("invalid-trigger", fixture.compile)
        self.fixture.mutate_registry(lambda r: _registry_entry(r)["triggers"][0].__setitem__("terminal", False))
        self.assertPlannerError("invalid-trigger", self.fixture.compile)

    def test_owner_mapping_outcome_argv_cwd_hash_are_strict(self):
        cases = [
            (lambda r: _registry_entry(r).__setitem__("owner","OTHER"), "invalid-owner", True),
            (lambda r: _registry_entry(r).__setitem__("acceptance_criterion_ids",["UNKNOWN"]), "invalid-mapping", True),
            (lambda r: _registry_entry(r)["outcome_contract"].__setitem__("required_fields",["exit_code"]), "invalid-entry", True),
            (lambda r: _registry_entry(r)["fixed_argv"].append("extra"), "invalid-argv", True),
            (lambda r: _registry_entry(r).__setitem__("cwd","../outside"), "invalid-cwd", True),
            (lambda r: _registry_entry(r).__setitem__("entry_hash","0"*64), "entry-hash-mismatch", False),
        ]
        for mutate, code, recompute in cases:
            with self.subTest(code=code):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                fixture.mutate_registry(mutate, recompute=recompute)
                self.assertPlannerError(code, fixture.compile)

    def test_mapping_must_belong_to_the_entry_catalog_task(self):
        for mapping in (
            "LF-GATE-EVIDENCE-001",
            "LF-TSK-QLT-0007.acceptance_criteria[0]",
        ):
            with self.subTest(mapping=mapping):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                fixture.mutate_registry(
                    lambda registry, value=mapping:
                    _registry_entry(registry).__setitem__("acceptance_criterion_ids", [value])
                )
                self.assertPlannerError("invalid-mapping", fixture.compile)

    def test_selected_effect_ids_bind_exact_explicit_statuses(self):
        for effect_id in ("bogus", "../evil"):
            with self.subTest(effect_id=effect_id):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                fixture.mutate_registry(lambda registry, value=effect_id:
                    _registry_entry(registry).__setitem__("effect_check_ids", [value]))
                self.assertPlannerError("invalid-mapping", fixture.compile)

    def test_non_nfc_dispatch_trigger_is_rejected(self):
        self.fixture.mutate_registry(lambda registry:
            registry["entries"][0]["triggers"][0].__setitem__("path", "scripts/gates/e\u0301.py"))
        self.assertPlannerError("invalid-trigger", self.fixture.compile)

    def test_future_tasks_cannot_reclaim_the_registry(self):
        for field in ("allowed_files", "file_claims", "owner"):
            with self.subTest(field=field):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                def mutate(catalog, selected=field):
                    task = next(task for task in _tasks(catalog) if task["id"] == "LF-TSK-QLT-0009")
                    if selected == "allowed_files":
                        task["allowed_files"].append("harness/**")
                    else:
                        if selected == "file_claims":
                            task["file_claims"].append({
                                "path": "harness/**",
                                "mode": "write",
                                "owner": "LF-WS-QLT",
                            })
                        else:
                            task["owner"] = "LF-WS-OTHER"
                fixture.refresh_catalog(mutate)
                expected = "invalid-task" if field == "owner" else "invalid-registry"
                self.assertPlannerError(expected, fixture.compile)

    def test_duplicate_ids_consumed_mappings_timeout_and_future_command_are_strict(self):
        cases = [
            (lambda r: r["entries"][1].__setitem__("check_id", r["entries"][0]["check_id"]), "duplicate-check-id"),
            (lambda r: r["entries"][1].__setitem__("command_id", r["entries"][0]["command_id"]), "duplicate-command-id"),
            (lambda r: _registry_entry(r)["consumed_inputs"].append(_registry_entry(r)["consumed_inputs"][0]), "invalid-consumed-input"),
            (lambda r: _registry_entry(r)["acceptance_criterion_ids"].append(_registry_entry(r)["acceptance_criterion_ids"][0]), "invalid-mapping"),
            (lambda r: _registry_entry(r).__setitem__("timeout_seconds", True), "invalid-timeout"),
            (lambda r: _registry_entry(r, "LF-TSK-QLT-0009").__setitem__("declared_validation_command", "python3 forged.py"), "command-mismatch"),
            (lambda r: _registry_entry(r).__setitem__("fixed_argv", ["python3", "&&", "evil"]), "invalid-argv"),
            (lambda r: _registry_entry(r).__setitem__("consumed_inputs", ["tmp/latest/input.json"]), "invalid-consumed-input"),
            (lambda r: _registry_entry(r)["effect_check_ids"].append(_registry_entry(r)["effect_check_ids"][0]), "invalid-mapping"),
        ]
        for mutate, code in cases:
            with self.subTest(code=code):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                fixture.mutate_registry(mutate)
                self.assertPlannerError(code, fixture.compile)

    def test_missing_reordered_or_extra_inventory_is_rejected(self):
        for mutate in (
            lambda r: r["entries"].pop(),
            lambda r: r["entries"].reverse(),
            lambda r: r["entries"].append(copy.deepcopy(r["entries"][-1])),
        ):
            with self.subTest(mutate=mutate):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                fixture.mutate_registry(mutate)
                self.assertTrue(self._inventory_failure(fixture))

    def _inventory_failure(self, fixture):
        try:
            fixture.compile()
        except PlannerError as exc:
            return exc.code in {"invalid-registry", "duplicate-subject-task", "duplicate-check-id", "duplicate-command-id", "duplicate-declared-command"}
        return False


class TestParsingAndSafeReads(PlannerFixtureTest):
    def test_each_locator_is_captured_once_then_revalidated_once(self):
        real = planner._safe_read
        counts = {}
        def counted(root, inode, locator):
            counts[locator] = counts.get(locator, 0) + 1
            return real(root, inode, locator)
        with mock.patch.object(planner, "_safe_read", side_effect=counted):
            self.fixture.compile()
        self.assertTrue(counts)
        self.assertEqual(set(counts.values()), {2})

    def test_recursive_duplicate_json_is_rejected(self):
        path = self.fixture.root / self.fixture.issuer_locator
        original = path.read_bytes()
        duplicate = original.replace(b'"authority":{', b'"authority":{"authority_id":"forged",', 1)
        path.write_bytes(duplicate); self.fixture.issuer_sha = _hash(duplicate)
        self.assertPlannerError("invalid-duplicate-key", self.fixture.compile)

    def test_duplicate_and_unhashable_yaml_keys_are_typed(self):
        path = self.fixture.root / "harness/gate-check-registry.yaml"
        path.write_bytes(b"schema_version: x\nschema_version: y\n")
        self.fixture.rebind_changed_files()
        self.assertPlannerError("invalid-duplicate-key", self.fixture.compile)
        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        (fixture.root / "harness/gate-check-registry.yaml").write_bytes(b"? [a, b]\n: value\n")
        fixture.rebind_changed_files()
        self.assertPlannerError("invalid-duplicate-key", fixture.compile)

    def test_float_nonfinite_surrogate_and_unsupported_canonical_values_fail(self):
        for value in (1.5, float("nan"), "\ud800", {"x": object()}, {1:"x"}, 2**60):
            with self.subTest(value=repr(value)):
                self.assertPlannerError("invalid-canonical-json", lambda v=value: canonical_json_bytes(v))

    def test_rfc8785_uses_utf16_key_order(self):
        value = {"\ue000": 2, "\U00010000": 1}
        self.assertEqual(canonical_json_bytes(value), '{"\U00010000":1,"\ue000":2}'.encode())

    def test_recursive_yaml_alias_is_typed_fail(self):
        registry = self.fixture.root / "harness/gate-check-registry.yaml"
        registry.write_bytes(b"&a [*a]\n")
        self.fixture.rebind_changed_files()
        self.assertPlannerError("invalid-canonical-json", self.fixture.compile)

    def test_symlink_and_fifo_inputs_fail_without_blocking(self):
        manifest = self.fixture.root / "harness/manifest.yaml"
        outside = self.fixture.root / "outside.yaml"; outside.write_bytes(manifest.read_bytes())
        manifest.unlink(); manifest.symlink_to(outside)
        self.assertPlannerError("symlink-unsafe", self.fixture.compile)
        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
        fifo = fixture.root / "harness/manifest.yaml"; fifo.unlink(); os.mkfifo(fifo)
        code = (
            "import sys\nfrom scripts.gates.planner import compile_plan,PlannerError\n"
            "try:\n compile_plan(sys.argv[1],mode='incremental',receipt_kind='TASK_VALIDATION',evidence_packet_locator=sys.argv[2],evidence_packet_sha256=sys.argv[3],issuer_packet_locator=sys.argv[4],issuer_packet_sha256=sys.argv[5])\n"
            "except PlannerError:\n raise SystemExit(0)\nraise SystemExit(2)\n"
        )
        completed = subprocess.run([sys.executable,"-c",code,str(fixture.root),fixture.evidence_locator,fixture.evidence_sha,fixture.issuer_locator,fixture.issuer_sha],cwd=REPO,timeout=1)
        self.assertEqual(completed.returncode, 0)

    def test_repository_ancestor_symlink_and_root_replacement_fail(self):
        alias_parent = pathlib.Path(tempfile.mkdtemp(prefix="planner-root-alias-"))
        self.addCleanup(lambda: shutil.rmtree(alias_parent, ignore_errors=True))
        (alias_parent / "ancestor").symlink_to(self.fixture.root.parent, target_is_directory=True)
        alias_root = alias_parent / "ancestor" / self.fixture.root.name
        self.assertPlannerError("symlink-unsafe", lambda: compile_plan(
            alias_root, mode="incremental", receipt_kind="TASK_VALIDATION",
            evidence_packet_locator=self.fixture.evidence_locator,
            evidence_packet_sha256=self.fixture.evidence_sha,
            issuer_packet_locator=self.fixture.issuer_locator,
            issuer_packet_sha256=self.fixture.issuer_sha,
        ))

        frozen = planner._Inputs(str(self.fixture.root))
        moved = self.fixture.root.with_name(self.fixture.root.name + "-moved")
        self.fixture.root.rename(moved); self.fixture.root.mkdir()
        try:
            self.assertPlannerError("input-drift", lambda: frozen.get("planning/workstreams.yaml"))
        finally:
            self.fixture.root.rmdir(); moved.rename(self.fixture.root)

    def test_leaf_swap_between_stat_and_open_is_rejected(self):
        path = self.fixture.root / "harness/manifest.yaml"
        moved = path.with_name("manifest.original.yaml")
        real_open = planner.os.open
        swapped = False
        def swapping_open(name, flags, *args, **kwargs):
            nonlocal swapped
            if name == "manifest.yaml" and kwargs.get("dir_fd") is not None and not swapped:
                swapped = True
                path.rename(moved)
                path.write_bytes(b"replacement\n")
            return real_open(name, flags, *args, **kwargs)
        with mock.patch.object(planner.os, "open", side_effect=swapping_open):
            self.assertPlannerError("input-drift", self.fixture.compile)

    def test_observable_swap_restore_and_registry_swap_fail(self):
        real = planner._safe_read; calls = 0
        def swapped(root, inode, locator):
            nonlocal calls
            data = real(root,inode,locator)
            if locator == "harness/manifest.yaml":
                calls += 1
                if calls == 2:
                    return data + b"\nswapped\n"
            return data
        with mock.patch.object(planner,"_safe_read",side_effect=swapped):
            self.assertPlannerError("input-drift", self.fixture.compile)
        fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup); real = planner._safe_read; count = 0
        def registry_swap(root,inode,locator):
            nonlocal count
            data = real(root,inode,locator)
            if locator == "harness/gate-check-registry.yaml":
                count += 1
                if count == 2: return data + b"\n"
            return data
        with mock.patch.object(planner,"_safe_read",side_effect=registry_swap):
            self.assertPlannerError("input-drift", fixture.compile)

    def test_final_revalidation_covers_each_input_class(self):
        targets = (
            "evidence", "raw", "source", "policy", "acceptance", "selected", "issuer", "authority",
        )
        for target in targets:
            with self.subTest(target=target):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                locator = {
                    "evidence": fixture.evidence_locator,
                    "raw": fixture.evidence_fixture.task_artifact_path,
                    "source": "planning/workstreams.yaml",
                    "policy": "harness/agent-policy.manifest.yaml",
                    "acceptance": "docs/product/product-brief.md",
                    "selected": "scripts/gates/planner.py",
                    "issuer": fixture.issuer_locator,
                    "authority": fixture.issuer_packet["authority"]["evidence_locator"],
                }[target]
                real = planner._safe_read; count = 0
                def drift(root, inode, current):
                    nonlocal count
                    data = real(root, inode, current)
                    if current == locator:
                        count += 1
                        if count == 2:
                            return data + b"drift"
                    return data
                with mock.patch.object(planner, "_safe_read", side_effect=drift):
                    self.assertPlannerError("input-drift", fixture.compile)


class TestCodexWorkPackagePlannerProjection(PlannerFixtureTest):
    def test_package_scope_union_uses_dispatch_canonical_coverage(self):
        self.assertEqual(
            planner._minimal_path_union(
                [
                    "harness/**",
                    "harness/agent-policy.manifest.yaml",
                    "harness/agent-runtime.manifest.yaml",
                    "planning/**",
                ],
                "scope",
            ),
            ["harness/**", "planning/**"],
        )

    def _rewrite_codex_task(self, raw):
        self.fixture.evidence_fixture.task_artifact = raw
        data = _qoder_persisted_json(raw)
        _write(
            self.fixture.root,
            self.fixture.evidence_fixture.task_artifact_path,
            data,
        )
        self.fixture.evidence_input["subject"]["raw_artifacts"]["task"]["sha256"] = _hash(data)
        self.fixture._materialize_evidence()

    def test_real_zero_write_independent_review_plan_compiles(self):
        self.fixture.use_codex_work_package()
        _set_changed_files(
            self.fixture.evidence_fixture,
            self.fixture.evidence_input,
            [],
        )
        self.fixture._materialize_evidence()
        self.fixture._materialize_issuer(["INDEPENDENT_REVIEW"])
        before = _snapshot(self.fixture.root)
        result = self.fixture.compile(receipt_kind="INDEPENDENT_REVIEW")
        self.assertEqual(before, _snapshot(self.fixture.root))
        plan = result["plan"]
        projection = plan["task"]["work_package_projection"]
        self.assertEqual(
            projection["work_package_id"],
            "LF-WP-QLT-CODEX-REVIEW-PROJECTION-001",
        )
        self.assertEqual(projection["task_ids"], [
            "LF-TSK-QLT-0006", "LF-TSK-QLT-0007",
            "LF-TSK-QLT-0008", "LF-TSK-QLT-0011",
        ])
        self.assertEqual(projection["target_task_id"], "LF-TSK-QLT-0008")
        self.assertEqual(plan["subject"]["identity"]["client"], "codex")
        self.assertEqual(plan["scope"]["changed_files"], [])
        self.assertEqual(plan["task"]["owner"], self.fixture.record["owner"])
        self.assertEqual(
            plan["task"]["file_claims"], self.fixture.record["file_claims"]
        )

    def test_non_target_caller_contract_drift_is_rejected_against_catalog(self):
        self.fixture.use_codex_work_package()
        raw = copy.deepcopy(self.fixture.evidence_fixture.task_artifact)
        raw["caller_contract"]["expected_outputs_by_task"]["LF-TSK-QLT-0006"] = "forged"
        self._rewrite_codex_task(raw)
        self.assertPlannerError("task-reconciliation-fail", self.fixture.compile)

    def test_ordered_task_identity_is_frozen_in_plan_fingerprint(self):
        self.fixture.use_codex_work_package()
        first = self.fixture.compile()["plan"]
        raw = copy.deepcopy(self.fixture.evidence_fixture.task_artifact)
        raw["task_ids"] = list(reversed(raw["task_ids"]))
        raw["caller_contract"]["task_ids"] = copy.deepcopy(raw["task_ids"])
        self._rewrite_codex_task(raw)
        self.fixture._materialize_issuer()
        second = self.fixture.compile()["plan"]
        self.assertEqual(
            second["task"]["work_package_projection"]["task_ids"],
            raw["task_ids"],
        )
        self.assertNotEqual(first["content_fingerprint"], second["content_fingerprint"])

    def test_qoder_work_package_and_resume_shapes_remain_supported(self):
        initial = self.fixture.compile()["plan"]
        projection = initial["task"]["work_package_projection"]
        self.assertEqual(
            projection["schema_version"],
            "lexiflow.qoder-work-package-projection.v1",
        )
        self.assertEqual(
            projection["estimated_minutes"],
            self.fixture.evidence_fixture.task_artifact["estimated_minutes"],
        )
        self.assertEqual(projection["agent_profile"], "quality-verifier")
        self.assertGreaterEqual(len(projection["task_ids"]), 2)
        self.assertIn(initial["task"]["task_id"], projection["task_ids"])
        self.assertEqual(len(projection["harness_context"]), 6)
        self.fixture.refresh_raw_task(
            lambda raw: raw.update({"title": "resume", "_resume_mode": True})
        )
        resumed = self.fixture.compile()["plan"]
        self.assertEqual(resumed["subject"]["identity"]["client"], "qoder")
        self.assertEqual(
            resumed["task"]["work_package_projection"]["work_package_id"],
            projection["work_package_id"],
        )

    def test_qoder_package_task_versions_and_harness_context_are_bound(self):
        for mutation, expected in (
            (
                lambda raw: raw["task_versions"].__setitem__(raw["task_ids"][1], 999),
                "non-current-task-version",
            ),
            (
                lambda raw: raw["harness_context"].pop(),
                "invalid-task",
            ),
            (
                lambda raw: raw["task_ids"].append(raw["task_ids"][0]),
                "invalid-task",
            ),
        ):
            with self.subTest(expected=expected):
                fixture = RealPlannerFixture(); self.addCleanup(fixture.cleanup)
                fixture.refresh_raw_task(mutation)
                self.assertPlannerError(expected, fixture.compile)


if __name__ == "__main__":
    unittest.main()
