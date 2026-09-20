import copy
import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from scripts.gates import formal_gate as cli
from scripts.gates import receipt_store
from scripts.gates.receipt_store import (
    ImmutableReceiptStore,
    ReceiptStoreError,
    canonical_json_bytes,
    read_status,
    sha256_bytes,
)


RUN_ID = "11111111-1111-4111-8111-111111111111"
PROCESS_ID = "22222222-2222-4222-8222-222222222222"


def descriptor(locator, content):
    return {"locator": locator, "sha256": sha256_bytes(content)}


class Fixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.write("scripts/gates/formal_gate.py", b"# fixture cli\n")
        self.evidence = self.write("tmp/quality/evidence/e.json", b"{}")
        self.issuer = self.write("tmp/quality/issuers/i.json", b"{}")
        source = self.write("planning/workstreams.yaml", b"workstreams: []\n")
        registry = self.write("harness/gate-check-registry.yaml", b"registry: 1\n")
        policy = self.write("harness/agent-policy.manifest.yaml", b"policy: 1\n")
        raw = {}
        for name in ("task", "completion", "stdout", "stderr"):
            raw[name] = self.write(f"tmp/evidence/{name}.json", (name + "\n").encode())
        raw["diff"] = self.write(
            "tmp/evidence/diff.patch",
            b"diff --git a/scripts/gates/formal_gate.py b/scripts/gates/formal_gate.py\n"
            b"--- a/scripts/gates/formal_gate.py\n"
            b"+++ b/scripts/gates/formal_gate.py\n"
            b"@@ -1 +1 @@\n"
            b"-old\n"
            b"+# fixture cli\n",
        )
        raw["changed_file_snapshot"] = self.write(
            "tmp/evidence/changed-file-snapshot.json",
            canonical_json_bytes({
                "schema_version": "lexiflow.changed-file-snapshot.v1",
                "files": {
                    "scripts/gates/formal_gate.py": {
                        "state": "present",
                        "sha256": sha256_bytes((self.root / "scripts/gates/formal_gate.py").read_bytes()),
                    }
                },
            }),
        )
        raw["tests"] = [self.write("tmp/evidence/tests.json", b"tests\n")]
        self.plan = {
            "schema_version": "lexiflow.gate-plan.v1",
            "mode": "incremental",
            "receipt_kind": "TASK_VALIDATION",
            "execution": {
                "layer": "delivery-validation",
                "checker_execution": "required",
                "source": "selected-registry-checks",
            },
            "task": {
                "task_id": "LF-TSK-QLT-0010", "task_version": 2, "change_version": "2.0.0",
                "task_source": source,
            },
            "subject": {
                "explicit_evidence_packet": {**self.evidence, "content_fingerprint": "2" * 64},
                "raw_artifacts": raw,
                "main_agent_attestation": {
                    "actor_id": "main", "reviewed_at": "2026-09-16T00:00:00Z",
                    "result_fields": {
                        "status": "PASS", "changed_files": ["scripts/gates/formal_gate.py"],
                        "validation": {"status": "PASS", "evidence_locator": raw["tests"][0]["locator"]},
                        "acceptance_evidence": [raw["tests"][0]["locator"]],
                        "effect_checks": {"behavior": "PASS"}, "risks": ["none"],
                    },
                    "field_source_bindings": {},
                },
                "identity": {
                    "parent_session_id": "3" * 8 + "-3333-4333-8333-333333333333",
                    "agent_id": "agent", "run_id": "4" * 8 + "-4444-4444-8444-444444444444",
                    "session_id": "5" * 8 + "-5555-4555-8555-555555555555",
                    "client": "codex", "parent_client": "codex",
                },
            },
            "issuer_packet": {
                **self.issuer,
                "packet": {
                    "issuer_instance_id": "6" * 8 + "-6666-4666-8666-666666666666",
                    "actor_type": "codex", "actor_id": "issuer",
                    "parent_session_id": "7" * 8 + "-7777-4777-8777-777777777777",
                    "session_id": "8" * 8 + "-8888-4888-8888-888888888888",
                    "client": "codex", "role": "gate-receipt-issuer",
                },
            },
            "scope": {"changed_files": ["scripts/gates/formal_gate.py"], "three_way_reconciliation": {"status": "PASS"}},
            "consumed_inputs": [
                {**policy, "state": "present"}
            ],
            "checks": [{
                "check_id": "qlt.lifecycle.validate", "required": True,
                "declared_validation_command": "python3 -m unittest", "command_id": "qlt.lifecycle.validate.v1",
                "fixed_argv": ["python3", "-m", "unittest"], "registry_entry_sha256": "a" * 64,
            }],
            "expectations": {"acceptance": [{}], "effect_checks": [{}], "risks": [{}]},
            "registry": registry,
            "content_fingerprint": "",
        }
        payload = {key: value for key, value in self.plan.items() if key != "content_fingerprint"}
        self.plan["content_fingerprint"] = sha256_bytes(canonical_json_bytes(payload))

    def write(self, locator, content):
        path = self.root / locator
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return descriptor(locator, content)

    def compiler(self, *args, **kwargs):
        plan = copy.deepcopy(self.plan)
        return {"plan": plan, "canonical_bytes": canonical_json_bytes(plan), "content_fingerprint": plan["content_fingerprint"]}

    @staticmethod
    def execution(plan, repo_root):
        argv = ["python3", "-m", "unittest"]
        process = {
            "started_at": "2026-09-16T00:00:00Z", "finished_at": "2026-09-16T00:00:01Z",
            "duration_seconds": 1.0, "argv": argv,
            "argv_fingerprint": sha256_bytes(canonical_json_bytes(argv)),
            "cwd": repo_root, "cwd_fingerprint": sha256_bytes(repo_root.encode("utf-8")),
            "return_code": 0, "signal": None,
            "exit_reason": "EXITED", "stdout_bytes": 1, "stderr_bytes": 0,
            "stdout_sha256": "e" * 64, "stderr_sha256": sha256_bytes(b""),
            "stdout_truncated": False, "stderr_truncated": False, "capture_error": False,
            "environment_fingerprint": "f" * 64, "child_pid": 1, "timeout_seconds": 60,
        }
        check = {
            "check_id": "qlt.lifecycle.validate", "check_version": 1, "owner": "LF-WS-QLT",
            "subject_task": {"task_id": "LF-TSK-QLT-0010", "task_version": 2, "change_version": "2.0.0", "owner": "LF-WS-QLT"},
            "required": True, "command_id": "qlt.lifecycle.validate.v1", "selection_reasons": [],
            "process": process,
            "outcome": {
                "schema": "lexiflow.check-outcome.v1", "status": "PASS", "reason": "",
                "assertions": 1, "suites": None, "failures": 0, "errors": 0,
                "skipped": 0, "evidence": {},
            },
            "consumed_input_verification": {"pre_execution": [], "post_execution": []},
        }
        return {
            "schema_version": "lexiflow.gate-check-outcome.v1", "plan_content_fingerprint": plan["content_fingerprint"],
            "started_at": process["started_at"], "finished_at": process["finished_at"], "duration_seconds": 1.0,
            "checks": [check], "run_status": "PASS", "run_reason": "",
            "aggregation": {"total": 1, "passed": 1, "blocked": 0, "failed": 0, "required_total": 1, "required_passed": 1},
        }


class RecordingStderr(io.StringIO):
    def __init__(self, events):
        super().__init__()
        self.events = events

    def flush(self):
        self.events.append("caller-flush")
        return super().flush()


class TestGateLifecycle(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.temp.cleanup)

    def run_gate(self, executor=None, stderr=None, repository_verifier=None):
        ids = iter((uuid.UUID(RUN_ID), uuid.UUID(PROCESS_ID)))
        return cli.run_gate(
            self.fixture.root, mode="incremental", receipt_kind="TASK_VALIDATION",
            evidence_packet=self.fixture.evidence["locator"], issuer_packet=self.fixture.issuer["locator"],
            compiler=self.fixture.compiler, executor=executor or self.fixture.execution,
            uuid_factory=lambda: next(ids), stderr=stderr or io.StringIO(),
            now=lambda: "2026-09-16T00:00:02Z",
            repository_verifier=repository_verifier,
        )

    def test_start_is_durable_and_caller_visible_before_checker(self):
        events = []
        stderr = RecordingStderr(events)

        def executor(plan, repo_root):
            events.append("checker")
            running = read_status(repo_root, RUN_ID)
            self.assertEqual(running["status"], "RUNNING")
            return self.fixture.execution(plan, repo_root)

        receipt = self.run_gate(executor=executor, stderr=stderr)
        self.assertEqual(events, ["caller-flush", "checker"])
        caller = json.loads(stderr.getvalue())
        self.assertEqual(caller["event"], "START")
        self.assertEqual(caller["run_id"], RUN_ID)
        self.assertEqual(receipt["result"], "PASS")
        self.assertEqual(receipt["started_at"], "2026-09-16T00:00:02Z")
        final = read_status(self.fixture.root, RUN_ID)
        self.assertEqual((final["status"], final["result"]), ("FINALIZED", "PASS"))

    def test_caller_flush_failure_prevents_checker(self):
        calls = []

        class Broken(io.StringIO):
            def flush(self):
                raise OSError("broken")

        receipt = self.run_gate(executor=lambda *a, **k: calls.append(1), stderr=Broken())
        self.assertEqual(calls, [])
        self.assertEqual((receipt["result"], receipt["reasons"]), ("FAIL", ["start-event-unavailable"]))
        self.assertEqual(read_status(self.fixture.root, RUN_ID)["status"], "FINALIZED")

    def test_evidence_only_receipt_kind_never_calls_delivery_executor(self):
        plan = copy.deepcopy(self.fixture.plan)
        plan["receipt_kind"] = "INDEPENDENT_REVIEW"
        plan["execution"] = {
            "layer": "evidence-consumption",
            "checker_execution": "forbidden",
            "source": "prior-immutable-receipts",
        }
        plan["checks"] = []
        payload = {key: value for key, value in plan.items() if key != "content_fingerprint"}
        plan["content_fingerprint"] = sha256_bytes(canonical_json_bytes(payload))

        def compiler(*args, **kwargs):
            return {
                "plan": plan,
                "canonical_bytes": canonical_json_bytes(plan),
                "content_fingerprint": plan["content_fingerprint"],
            }

        observed = {}
        def handler(**kwargs):
            observed["execution"] = kwargs["execution"]
            return {"result": "PASS"}

        ids = iter((uuid.UUID(RUN_ID), uuid.UUID(PROCESS_ID)))
        with mock.patch("scripts.gates.formal_gate._installed_handler", return_value=handler):
            receipt = cli.run_gate(
                self.fixture.root, mode="incremental", receipt_kind="INDEPENDENT_REVIEW",
                evidence_packet=self.fixture.evidence["locator"], issuer_packet=self.fixture.issuer["locator"],
                compiler=compiler,
                executor=lambda *args, **kwargs: self.fail("delivery executor must not run"),
                uuid_factory=lambda: next(ids), stderr=io.StringIO(),
                now=lambda: "2026-09-16T00:00:02Z",
            )
        self.assertEqual(receipt, {"result": "PASS"})
        self.assertEqual(observed["execution"]["checks"], [])
        self.assertEqual(observed["execution"]["aggregation"]["total"], 0)

    def test_receipt_kind_rejects_a_mismatched_execution_layer_before_executor(self):
        plan = copy.deepcopy(self.fixture.plan)
        plan["execution"] = {
            "layer": "evidence-consumption",
            "checker_execution": "forbidden",
            "source": "prior-immutable-receipts",
        }
        payload = {key: value for key, value in plan.items() if key != "content_fingerprint"}
        plan["content_fingerprint"] = sha256_bytes(canonical_json_bytes(payload))

        def compiler(*args, **kwargs):
            return {
                "plan": plan,
                "canonical_bytes": canonical_json_bytes(plan),
                "content_fingerprint": plan["content_fingerprint"],
            }

        calls = []
        with self.assertRaisesRegex(cli.GateCliError, "execution layer"):
            cli.run_gate(
                self.fixture.root, mode="incremental", receipt_kind="TASK_VALIDATION",
                evidence_packet=self.fixture.evidence["locator"], issuer_packet=self.fixture.issuer["locator"],
                compiler=compiler, executor=lambda *args, **kwargs: calls.append(1),
                stderr=io.StringIO(),
            )
        self.assertEqual(calls, [])

    def test_receipt_is_schema_complete_and_immutable(self):
        receipt = self.run_gate()
        self.assertEqual(receipt["receipt_kind"], "TASK_VALIDATION")
        self.assertEqual(receipt["completeness"]["status"], "PASS")
        self.assertEqual(receipt["validation"]["repository_verification"]["result"], "PASS")
        self.assertEqual(receipt["validation"]["checks"][0]["typed_outcome"]["status"], "PASS")
        store = ImmutableReceiptStore(self.fixture.root, RUN_ID)
        store._created = True
        with self.assertRaisesRegex(ReceiptStoreError, "artifact-collision"):
            store.publish_receipt(receipt)

    def test_incomplete_executor_payload_finalizes_fail(self):
        def incomplete(plan, repo_root):
            execution = self.fixture.execution(plan, repo_root)
            del execution["checks"][0]["process"]["stdout_sha256"]
            return execution

        receipt = self.run_gate(executor=incomplete)
        self.assertEqual(receipt["result"], "FAIL")
        self.assertEqual(receipt["reasons"], ["evidence-incomplete"])
        self.assertEqual(read_status(self.fixture.root, RUN_ID)["result"], "FAIL")

    def test_non_pass_preflight_check_cannot_publish_a_pass_receipt(self):
        for status in ("BLOCKED", "FAIL"):
            with self.subTest(status=status):
                fixture = Fixture()
                self.addCleanup(fixture.temp.cleanup)
                ids = iter((uuid.uuid4(), uuid.uuid4()))

                def preflight_non_pass(plan, repo_root):
                    execution = fixture.execution(plan, repo_root)
                    check = execution["checks"][0]
                    check["outcome"]["status"] = status
                    check["outcome"]["reason"] = "dispatch-preflight"
                    execution["run_status"] = status
                    execution["run_reason"] = "dispatch-preflight"
                    execution["aggregation"].update({
                        "passed": 0,
                        "blocked": int(status == "BLOCKED"),
                        "failed": int(status == "FAIL"),
                        "required_passed": 0,
                    })
                    return execution

                receipt = cli.run_gate(
                    fixture.root, mode="incremental", receipt_kind="TASK_VALIDATION",
                    evidence_packet=fixture.evidence["locator"], issuer_packet=fixture.issuer["locator"],
                    compiler=fixture.compiler, executor=preflight_non_pass,
                    uuid_factory=lambda: next(ids), stderr=io.StringIO(),
                    now=lambda: "2026-09-16T00:00:02Z",
                )
                self.assertEqual(receipt["result"], status)

    def test_repository_baseline_is_required_before_delivery_execution(self):
        executor = mock.Mock()
        receipt = self.run_gate(
            executor=executor,
            repository_verifier=lambda _: {"result": "BLOCKED", "blocking_scope": "repository-readiness"},
        )
        self.assertEqual(receipt["result"], "BLOCKED")
        self.assertEqual(receipt["reasons"], ["repository-baseline-not-pass"])
        executor.assert_not_called()

    def test_executor_payload_must_match_frozen_plan(self):
        def drifted(plan, repo_root):
            execution = self.fixture.execution(plan, repo_root)
            execution["checks"][0]["command_id"] = "forged.command.v1"
            return execution

        receipt = self.run_gate(executor=drifted)
        self.assertEqual(receipt["result"], "FAIL")
        self.assertEqual(receipt["reasons"], ["evidence-incomplete"])

    def test_run_collision_and_unsafe_status_are_rejected(self):
        store = ImmutableReceiptStore(self.fixture.root, RUN_ID)
        store.create()
        with self.assertRaisesRegex(ReceiptStoreError, "run-collision"):
            ImmutableReceiptStore(self.fixture.root, RUN_ID).create()
        for value in ("latest", "../escape", "11111111-1111-1111-1111-111111111111"):
            with self.assertRaises(ReceiptStoreError):
                read_status(self.fixture.root, value)

    def test_context_must_be_explicit_single_and_equal(self):
        with self.assertRaisesRegex(cli.GateCliError, "missing-evidence-context"):
            cli.resolve_context(evidence_packet=None, issuer_packet=None, env={})
        with self.assertRaisesRegex(cli.GateCliError, "evidence-context-conflict"):
            cli.resolve_context(
                evidence_packet="tmp/a.json", issuer_packet="tmp/b.json",
                env={"LEXIFLOW_GATE_EVIDENCE_PACKET": "tmp/other.json"},
            )
        self.assertEqual(
            cli.resolve_context(
                evidence_packet="tmp/a.json", issuer_packet="tmp/b.json",
                env={"LEXIFLOW_GATE_EVIDENCE_PACKET": "tmp/a.json", "LEXIFLOW_GATE_ISSUER_PACKET": "tmp/b.json"},
            ),
            ("tmp/a.json", "tmp/b.json"),
        )

    def test_plan_compile_does_not_write(self):
        before = sorted(str(path.relative_to(self.fixture.root)) for path in self.fixture.root.rglob("*"))
        result = cli.compile_from_context(
            self.fixture.root, mode="incremental", receipt_kind="TASK_VALIDATION",
            evidence_packet=self.fixture.evidence["locator"], issuer_packet=self.fixture.issuer["locator"],
            compiler=self.fixture.compiler,
        )
        after = sorted(str(path.relative_to(self.fixture.root)) for path in self.fixture.root.rglob("*"))
        self.assertEqual(result["plan"]["content_fingerprint"], self.fixture.plan["content_fingerprint"])
        self.assertEqual(before, after)

    def test_symlink_store_component_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            shutil.rmtree(self.fixture.root / "tmp" / "quality")
            (self.fixture.root / "tmp" / "quality").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ReceiptStoreError, "unsafe-locator"):
                ImmutableReceiptStore(self.fixture.root, RUN_ID).create()

    def test_partial_write_and_link_interruption_never_publish_final_name(self):
        for failure in ("write", "link"):
            with self.subTest(failure=failure):
                run_id = str(uuid.uuid4())
                store = ImmutableReceiptStore(self.fixture.root, run_id)
                store.create()
                if failure == "write":
                    patcher = mock.patch.object(receipt_store.os, "write", side_effect=OSError("interrupted"))
                else:
                    real_link = receipt_store.os.link

                    def link_then_raise(*args, **kwargs):
                        real_link(*args, **kwargs)
                        raise OSError("interrupted")

                    patcher = mock.patch.object(receipt_store.os, "link", side_effect=link_then_raise)
                with patcher, self.assertRaises(ReceiptStoreError):
                    store.publish_json("receipt.json", {"result": "PASS"})
                self.assertFalse((self.fixture.root / store.locator("receipt.json")).exists())

    def test_input_drift_after_checker_finalizes_fail(self):
        test_locator = self.fixture.plan["subject"]["raw_artifacts"]["tests"][0]["locator"]

        def drifting_executor(plan, repo_root):
            result = self.fixture.execution(plan, repo_root)
            (self.fixture.root / test_locator).write_bytes(b"changed\n")
            return result

        receipt = self.run_gate(executor=drifting_executor)
        self.assertEqual((receipt["result"], receipt["reasons"]), ("FAIL", ["input-drift"]))
        self.assertEqual(read_status(self.fixture.root, RUN_ID)["status"], "FINALIZED")

    def test_status_rejects_changed_persisted_plan(self):
        self.run_gate()
        plan_path = self.fixture.root / f"tmp/quality/runs/{RUN_ID}/plan.json"
        plan_path.write_bytes(plan_path.read_bytes() + b" ")
        with self.assertRaisesRegex(ReceiptStoreError, "status-invalid"):
            read_status(self.fixture.root, RUN_ID)

    def test_status_rejects_unsafe_receipt_entry_instead_of_reporting_running(self):
        events = []

        def executor(plan, repo_root):
            receipt_path = self.fixture.root / f"tmp/quality/runs/{RUN_ID}/receipt.json"
            receipt_path.symlink_to(self.fixture.root / "tmp/quality/evidence/e.json")
            with self.assertRaisesRegex(ReceiptStoreError, "artifact-unavailable"):
                read_status(repo_root, RUN_ID)
            receipt_path.unlink()
            events.append("rejected")
            return self.fixture.execution(plan, repo_root)

        receipt = self.run_gate(executor=executor)
        self.assertEqual(events, ["rejected"])
        self.assertEqual(receipt["result"], "PASS")


if __name__ == "__main__":
    unittest.main()
