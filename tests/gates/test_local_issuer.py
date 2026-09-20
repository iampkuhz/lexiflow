"""本地生产适配器回归：使用宿主记录 fixture，不注入 trusted_codex_context。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.gates import cli
from scripts.gates.acceptance_cases import case_ids
from scripts.gates.catalog_decision import verify_acceptance_registry
from scripts.gates.issuer_packet import IssuerPacketError, IssuerPacketMaterializer
from scripts.gates.local_issuer import prepare
from scripts.gates.planner import PlannerError, verify_trusted_issuer_packet
from scripts.harness.codex_runtime import CodexRuntimeError
from scripts.harness.local_codex_runtime import discover, verify_proof
from tests.gates.test_gate_planner import RealPlannerFixture, REPO


def host_record(home: Path, root: Path, sid: str) -> Path:
    path = home / "sessions" / f"rollout-{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "session_meta", "payload": {
        "id": sid, "session_id": sid, "cwd": str(root), "source": "vscode",
        "thread_source": "user", "originator": "Codex Desktop",
        "base_instructions": "PRIVATE_SENTINEL_DO_NOT_COPY",
    }}) + "\n")
    return path


class LocalRuntimeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "codex-home"
        self.sid = str(uuid.uuid4())
        self.source = host_record(self.home, self.root, self.sid)
        env = patch.dict(os.environ, {"CODEX_HOME": str(self.home), "CODEX_THREAD_ID": self.sid,
                                      "CODEX_SESSION_ID": self.sid})
        env.start(); self.addCleanup(env.stop)

    def test_actor_is_stable_source_is_private_and_append_is_not_drift(self):
        one = discover(self.root)
        two = discover(self.root)
        self.assertEqual(one.context, two.context)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(one.proof))
        with self.source.open("a") as stream:
            stream.write('{"type":"unrelated-private-message"}\n')
        verify_proof(self.root, one.proof, one.context)
        self.assertEqual(one.bind({}, str(uuid.uuid4())).identity["agent_id"],
                         two.bind({}, str(uuid.uuid4())).identity["agent_id"])

    def test_missing_route_conflict_and_workspace_fail_closed(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "", "CODEX_SESSION_ID": ""}):
            with self.assertRaisesRegex(CodexRuntimeError, "runtime-metadata-unavailable"):
                discover(self.root)
        with patch.dict(os.environ, {"CODEX_THREAD_ID": str(uuid.uuid4())}):
            with self.assertRaisesRegex(CodexRuntimeError, "runtime-route-conflict"):
                discover(self.root)
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-workspace-mismatch"):
            discover(self.root / "other")

    def test_duplicate_symlink_and_world_writable_metadata_rejected(self):
        duplicate = self.source.with_name("duplicate-" + self.sid + ".jsonl")
        shutil.copyfile(self.source, duplicate)
        with self.assertRaisesRegex(CodexRuntimeError, "exactly one"):
            discover(self.root)
        duplicate.unlink()
        self.source.chmod(0o666)
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-source-unsafe"):
            discover(self.root)
        self.source.chmod(0o600)
        saved = self.source.with_suffix(".saved")
        self.source.rename(saved); self.source.symlink_to(saved)
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-source-unsafe"):
            discover(self.root)

    def test_changed_header_and_forged_actor_are_rejected(self):
        runtime = discover(self.root)
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-identity-drift"):
            verify_proof(self.root, runtime.proof, {**runtime.context, "actor_id": "another-reviewer"})
        self.source.write_text(self.source.read_text().replace("Codex Desktop", "Changed Host"))
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-proof-drift"):
            verify_proof(self.root, runtime.proof, runtime.context)

    def test_shared_unknown_actor_is_not_treated_as_independent(self):
        self.source.write_text(self.source.read_text().replace('"user"', '"subagent"'))
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-actor-unavailable"):
            discover(self.root)

    def test_distinct_codex_created_thread_is_a_verifiable_actor(self):
        self.source.write_text(self.source.read_text().replace('"user"', '"agent_created_thread"'))
        runtime = discover(self.root)
        self.assertEqual(runtime.context["actor_id"], "codex-session-" + self.sid)


class LocalIssuerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = RealPlannerFixture()
        self.addCleanup(self.fixture.cleanup)
        self.root = self.fixture.root
        self.home = self.root / "host"
        self.sid = str(uuid.uuid4())
        self.source = host_record(self.home, self.root, self.sid)
        shutil.copyfile(REPO / "harness/gate-issuer-authorities.yaml", self.root / "harness/gate-issuer-authorities.yaml")
        env = patch.dict(os.environ, {"CODEX_HOME": str(self.home), "CODEX_THREAD_ID": self.sid,
                                      "CODEX_SESSION_ID": self.sid})
        env.start(); self.addCleanup(env.stop)

    def test_fresh_issuer_compiles_real_plan_without_host_context_injection(self):
        prepared = prepare(self.root, self.fixture.evidence_locator, "TASK_VALIDATION")
        self.fixture.issuer_locator = prepared.locator
        self.fixture.issuer_sha = prepared.sha256
        plan = self.fixture.compile()["plan"]
        self.assertEqual(plan["issuer_packet"]["packet"]["actor_id"], "codex-session-" + self.sid)
        self.assertEqual(plan["issuer_packet"]["provenance"][0]["kind"], "local-session-attestation")
        self.assertEqual(plan["checks"][0]["subject_task"]["task_id"], "LF-TSK-QLT-0008")
        again = prepare(self.root, self.fixture.evidence_locator, "TASK_VALIDATION")
        self.assertNotEqual(prepared.locator, again.locator)
        self.assertEqual(prepared.packet["actor_id"], again.packet["actor_id"])

    def test_source_drift_and_replay_rejected(self):
        prepared = prepare(self.root, self.fixture.evidence_locator, "TASK_VALIDATION")
        auth = prepared.packet["authority"]
        materializer = IssuerPacketMaterializer(self.root)
        request = {"issuer_instance_id": str(uuid.uuid4()), "receipt_kinds": ["TASK_VALIDATION"]}
        subject = self.fixture.evidence_packet["subject"]["identity"]
        with self.assertRaisesRegex(IssuerPacketError, "replayed-attestation"):
            materializer.materialize(request, authority_evidence_locator=auth["evidence_locator"],
                                     authority_evidence_sha256=auth["evidence_sha256"], subject_identity=subject)
        self.source.write_text(self.source.read_text().replace("Codex Desktop", "Altered"))
        with self.assertRaisesRegex(PlannerError, "runtime-proof-drift"):
            verify_trusted_issuer_packet(self.root, locator=prepared.locator, sha256=prepared.sha256,
                                        receipt_kind="TASK_VALIDATION")

    def test_local_verifier_rejects_legacy_proof_even_with_injected_context(self):
        prepared = prepare(self.root, self.fixture.evidence_locator, "TASK_VALIDATION")
        auth = prepared.packet["authority"]
        data = json.loads((self.root / auth["evidence_locator"]).read_text())
        data.pop("runtime_proof")
        path = self.root / auth["evidence_locator"]
        # Only mutate synthetic test artifacts, never live historical receipts.
        path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")))
        with self.assertRaisesRegex(IssuerPacketError, "runtime proof"):
            IssuerPacketMaterializer(self.root, trusted_codex_context=discover(self.root).context).materialize(
                {"issuer_instance_id": str(uuid.uuid4()), "receipt_kinds": ["TASK_VALIDATION"]},
                authority_evidence_locator=auth["evidence_locator"],
                authority_evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                subject_identity=self.fixture.evidence_packet["subject"]["identity"])

    def test_expired_attestation_is_not_refreshed_and_fresh_prepare_recovers(self):
        prepared = prepare(self.root, self.fixture.evidence_locator, "TASK_VALIDATION")
        auth = prepared.packet["authority"]
        path = self.root / auth["evidence_locator"]
        original = path.read_bytes()
        later = datetime.now(timezone.utc) + timedelta(hours=1)
        with self.assertRaisesRegex(IssuerPacketError, "stale-attestation"):
            IssuerPacketMaterializer(self.root, now=lambda: later).materialize(
                {"issuer_instance_id": str(uuid.uuid4()), "receipt_kinds": ["TASK_VALIDATION"]},
                authority_evidence_locator=auth["evidence_locator"],
                authority_evidence_sha256=auth["evidence_sha256"],
                subject_identity=self.fixture.evidence_packet["subject"]["identity"])
        fresh = prepare(self.root, self.fixture.evidence_locator, "TASK_VALIDATION")
        self.assertNotEqual(fresh.locator, prepared.locator)
        self.assertEqual(path.read_bytes(), original)

    def test_another_current_session_cannot_materialize_recorded_actor(self):
        prepared = prepare(self.root, self.fixture.evidence_locator, "TASK_VALIDATION")
        auth = prepared.packet["authority"]
        other = str(uuid.uuid4())
        host_record(self.home, self.root, other)
        with patch.dict(os.environ, {"CODEX_THREAD_ID": other, "CODEX_SESSION_ID": other}):
            with self.assertRaisesRegex(IssuerPacketError, "identity-drift"):
                IssuerPacketMaterializer(self.root).materialize(
                    {"issuer_instance_id": str(uuid.uuid4()), "receipt_kinds": ["TASK_VALIDATION"]},
                    authority_evidence_locator=auth["evidence_locator"],
                    authority_evidence_sha256=auth["evidence_sha256"],
                    subject_identity=self.fixture.evidence_packet["subject"]["identity"])

    def test_local_plan_is_read_only_and_later_layers_never_select_delivery_checks(self):
        for kind in ("TASK_VALIDATION", "INDEPENDENT_REVIEW", "CATALOG_DECISION"):
            prepared = prepare(self.root, self.fixture.evidence_locator, kind)
            self.fixture.issuer_locator, self.fixture.issuer_sha = prepared.locator, prepared.sha256
            before = set(self.root.rglob("*"))
            plan = self.fixture.compile(receipt_kind=kind)["plan"]
            self.assertEqual(set(self.root.rglob("*")), before)
            if kind != "TASK_VALIDATION":
                self.assertEqual(plan["checks"], [])
                self.assertEqual(plan["execution"]["checker_execution"], "forbidden")

    def test_public_binding_command_derives_current_main_projection(self):
        result = subprocess.run(
            [sys.executable, "-m", "scripts.harness.local_codex_runtime", "--task-id", "LF-TSK-QLT-0008"],
            cwd=self.root, env={**os.environ, "PYTHONPATH": str(REPO)},
            text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        outcome = json.loads(result.stdout)
        projection = json.loads((self.root / outcome["projection"]).read_bytes())
        self.assertEqual(projection["agent_id"], "codex-session-" + self.sid)
        self.assertEqual(projection["session_id"], self.sid)
        self.assertEqual(projection["task_version"], self.fixture.record["task_version"])

    def test_same_actual_session_cannot_be_renamed_to_issue_for_itself(self):
        self.fixture.use_codex_work_package()
        subject_sid = self.fixture.evidence_packet["subject"]["identity"]["session_id"]
        host_record(self.home, self.root, subject_sid)
        before = set(self.root.rglob("*.json"))
        with patch.dict(os.environ, {"CODEX_THREAD_ID": subject_sid, "CODEX_SESSION_ID": subject_sid}):
            with self.assertRaisesRegex(IssuerPacketError, "copied-subject-identity"):
                prepare(self.root, self.fixture.evidence_locator, "TASK_VALIDATION")
        self.assertEqual(before, set(self.root.rglob("*.json")))

    def test_formal_gate_run_executes_fixed_checker_and_publishes_receipt(self):
        for locator in ("scripts/gates/formal_gate.py",):
            shutil.copyfile(REPO / locator, self.root / locator)
        checker = self.root / "tests/gates/test_gate_planner.py"
        checker.write_text("import unittest\nclass Check(unittest.TestCase):\n def test_ok(self): self.assertEqual(1, 1)\n")
        self.fixture.rebind_changed_files()
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts/gates/formal_gate.py"), "--repo-root", str(self.root),
             "run", "--mode", "incremental", "--evidence-packet", self.fixture.evidence_locator],
            env=os.environ.copy(), text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        outcome = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(outcome["result"], "FAIL")
        self.assertEqual(outcome["reasons"], ["repository-baseline-not-pass"])
        self.assertIn("START", result.stdout + result.stderr)

    def test_doctor_and_missing_evidence_are_explicit_and_do_not_publish(self):
        before = set(self.root.rglob("*.json"))
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["--repo-root", str(self.root), "doctor"])
        self.assertEqual(code, 0)
        self.assertIn("not-gate-acceptance", json.loads(output.getvalue())["scope"])
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["--repo-root", str(self.root), "run", "--mode", "incremental"])
        self.assertEqual(code, 2)
        self.assertIn("next_action", json.loads(output.getvalue()))
        self.assertEqual(before, set(self.root.rglob("*.json")))


class AcceptanceHeadingTest(unittest.TestCase):
    def test_current_product_cases_have_exact_catalog_owners(self):
        content = (REPO / "docs/product/product-brief.md").read_bytes()
        result = verify_acceptance_registry((REPO / "planning/workstreams.yaml").read_bytes(), content,
            expected_registry={"locator": "docs/product/product-brief.md", "sha256": hashlib.sha256(content).hexdigest()})
        self.assertEqual(result["current_mapping_status"], "PASS", result)

    def test_numbered_nested_legacy_and_fenced_headings(self):
        text = "## LF-CASE-001 old\n### 1.7.1. LF-CASE-002 new\n```md\n## LF-CASE-999 example\n```\n"
        self.assertEqual(case_ids(text), ["LF-CASE-001", "LF-CASE-002"])
        self.assertEqual(case_ids(text + "### 1.8. LF-CASE-002 duplicate\n").count("LF-CASE-002"), 2)


if __name__ == "__main__":
    unittest.main()
