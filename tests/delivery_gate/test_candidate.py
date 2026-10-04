"""候选联合消费的有界解析与附件绑定测试。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.delivery_gate.candidate import (
    CandidateProofError,
    REQUIRED_FACTS,
    _bound,
    _extract,
    _json,
    consume_candidate_pass,
)
from scripts.delivery_gate.check import check_conditions
from scripts.delivery_gate.records import canonical_bytes, delivery_gate_locator, publish_bytes, publish_json, load_submission
from scripts.verification import verify_repository
from scripts.environment.candidate_runtime_check import PHASE_IDS
from tests.delivery_gate.fixtures import DeliveryGateFixture, VALIDATOR_SESSION, make_mock_runtime, mock_authority


class CandidateEvidenceTests(unittest.TestCase):
    def test_strict_json_rejects_duplicate_keys_and_non_finite_values(self):
        for payload in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                _json(payload)

    def test_attachment_descriptor_binds_actual_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = b'{"status":"PASS"}\n'
            target = root / "tmp/quality/verification/run/stdout.json"
            target.parent.mkdir(parents=True)
            target.write_bytes(data)
            descriptor = {"locator": "tmp/quality/verification/run/stdout.json",
                          "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            self.assertEqual(_bound(root, descriptor, prefix="tmp/quality/verification/run/", limit=1024)[0], data)
            with self.assertRaises(CandidateProofError):
                _bound(root, {**descriptor, "sha256": "0" * 64}, prefix="tmp/quality/verification/run/", limit=1024)

    def test_attachment_locator_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CandidateProofError):
                _bound(Path(directory), {"locator": "../outside", "bytes": 1, "sha256": "0" * 64},
                       prefix="", limit=1024)


class CandidateChainTests(unittest.TestCase):
    def setUp(self):
        self.f = DeliveryGateFixture()
        self.addCleanup(self.f.cleanup)
        catalog = yaml.safe_load((self.f.root / "planning/workstreams.yaml").read_text())
        task = catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][0]
        task["required_check_ids"] = ["eng.release.candidate-runtime"]
        (self.f.root / "planning/workstreams.yaml").write_text(yaml.safe_dump(catalog, sort_keys=False))
        declared = yaml.safe_load((self.f.root / "harness/module-checks.yaml").read_text())
        candidate = dict(declared["checks"][0])
        candidate.update(check_id="eng.release.candidate-runtime", module="release-candidate-runtime",
                         command=["python3", "-m", "scripts.environment.candidate_runtime_check"],
                         result_contract={"type": "json-stdout", "required_fields": ["status", "checks_run", "failures", "errors", "skipped", "reason"],
                                          "allowed_statuses": ["PASS"], "minimum": {"checks_run": 1},
                                          "equals": {"failures": 0, "errors": 0, "skipped": 0}})
        declared["checks"] = [candidate if item["check_id"] == candidate["check_id"] else item
                               for item in declared["checks"]]
        (self.f.root / "harness/module-checks.yaml").write_text(yaml.safe_dump(declared, sort_keys=False))
        self.f._git("add", "harness/module-checks.yaml", "planning/workstreams.yaml")
        self.f._git("commit", "-m", "candidate declaration fixture")
        self.identity = {"schemaVersion": 1, "baseVersion": "2.0.0-SNAPSHOT",
                         "softwareVersion": "2.0.0-SNAPSHOT.gaaaaaaa", "chromeVersion": "2.0.0.0",
                         "sourceCommit": "a" * 40, "sourceSha256": "b" * 64, "buildId": "c" * 64,
                         "dirty": False, "channel": "snapshot"}
        self.candidate = {"candidateSha256": "d" * 64, "manifestSha256": "e" * 64,
                          "buildIdentity": self.identity}
        self.f.create_submission()
        submission = load_submission(self.f.root, self.f.submission_id)
        self.runtime = {"schemaVersion": "lexiflow.candidate-runtime.v1", "status": "PASS",
                        "candidates": {"previous": {**self.candidate, "candidateSha256": "1" * 64, "buildIdentity": {**self.identity, "buildId": "2" * 64}}, "target": self.candidate},
                        "identity": {"source": self.identity}, "failure": None,
                        "phases": [{"id": name, "status": "PASS", "facts": {key: True for key in REQUIRED_FACTS[name]}} for name in PHASE_IDS],
                        "cleanup": {"status": "PASS", "settled": True, "ownedResourcesRemoved": True},
                        "assertions": {"sourceAndCandidatesUnchanged": True, "realFaultAndRecovery": True,
                                       "samePgAndDataset": True}}

        def runner(argv, _cwd, _env, _timeout, _executable=None, *, stdin_payload=None):
            if stdin_payload is None:
                output = {"status": "PASS", "tests_run": 1, "failures": 0, "errors": 0, "skipped": 0}
            else:
                envelope = json.loads(stdin_payload)
                output = {"status": "PASS", "reason": "", "checks_run": len(PHASE_IDS),
                          "failures": 0, "errors": 0, "skipped": 0,
                          "run_id": envelope["run_id"], "check_id": envelope["check_id"],
                          "verify_input_fingerprint": envelope["snapshot"]["fingerprint"],
                          "check_config_fingerprint": envelope["check_config_fingerprint"],
                          "request_sha256": "f" * 64, "runtime": self.runtime}
            return {"exit_code": 0, "exit_reason": "exited", "timed_out": False,
                    "stdout": json.dumps(output), "stderr": "", "executed_argv": argv,
                    "duration_seconds": 0.1, "started_at": "2026-09-21T00:00:00Z",
                    "finished_at": "2026-09-21T00:00:01Z"}

        report = verify_repository(self.f.root, required_check_ids=tuple(submission["task_requirements"]["required_check_ids"]),
                                   frozen_inputs=submission["verification_freeze"], runner=runner)
        self.assertEqual(report["result"], "PASS", report.get("reason"))
        import uuid
        validation_id = str(uuid.uuid4())
        descriptor = publish_bytes(self.f.root,
                                   f"tmp/quality/delivery-gate/validations/{validation_id}/report.json",
                                   canonical_bytes(report))
        runtime = make_mock_runtime(VALIDATOR_SESSION)
        record = {"schema_version": "lexiflow.delivery-gate-validation.v4",
                  "risk_assessment_hash": __import__("scripts.delivery_gate.acceptance", fromlist=["binding"]).binding(submission)["risk_assessment_hash"],
                  "acceptance_plan_hash": __import__("scripts.delivery_gate.acceptance", fromlist=["binding"]).binding(submission)["acceptance_plan_hash"],
                  "validation_id": validation_id, "submission_id": self.f.submission_id,
                  "submission_content_hash": submission["content_hash"],
                  "validator_identity": runtime.context, "runtime_proof": runtime.proof,
                  "authority": mock_authority(VALIDATOR_SESSION), "verification_report": descriptor,
                  "frozen_input_fingerprint": submission["verification_freeze"]["input_fingerprint"],
                  "gaps": [], "result": "PASS", "created_at": "2026-09-21T00:01:00Z"}
        publish_json(self.f.root, delivery_gate_locator("validations", validation_id), record)
        self.f.validation_id = validation_id
        self.f.create_review()
        self.assertEqual(check_conditions(self.f.root, submission_id=self.f.submission_id)["result"], "PASS")
        self.report = report

    def _consume(self):
        with patch("scripts.delivery_gate.candidate._node_proof",
                   return_value={key: self.candidate[key] for key in ("candidateSha256", "manifestSha256", "buildIdentity")}):
            return consume_candidate_pass(self.f.root, submission_id=self.f.submission_id,
                                          candidate_directory=str(self.f.root / "candidate"))

    def test_full_existing_chain_and_real_verify_report_pass_without_writing(self):
        from tests.delivery_gate.test_consume import _tree
        before = _tree(self.f.root)
        result = self._consume()
        self.assertEqual(result["result"], "PASS", result)
        self.assertEqual(result["proof"]["candidate_runtime_stdout"]["locator"],
                         f"tmp/quality/verification/{self.report['run_id']}/eng.release.candidate-runtime.stdout.log")
        self.assertEqual(before, _tree(self.f.root))

    def test_stdout_tamper_blocks_even_when_chain_remains_intact(self):
        locator = f"tmp/quality/verification/{self.report['run_id']}/eng.release.candidate-runtime.stdout.log"
        (self.f.root / locator).write_text("{}")
        result = self._consume()
        self.assertEqual((result["result"], result["reason"]), ("FAIL", "validation-evidence-drift"))

    def test_missing_review_blocks(self):
        import shutil
        shutil.rmtree(self.f.root / "tmp/quality/delivery-gate/reviews" / self.f.review_id)
        self.assertEqual(self._consume()["result"], "BLOCKED")

    def test_candidate_mismatch_and_mid_consume_drift_block(self):
        good = {key: self.candidate[key] for key in ("candidateSha256", "manifestSha256", "buildIdentity")}
        bad = {**good, "candidateSha256": "0" * 64}
        with patch("scripts.delivery_gate.candidate._node_proof", return_value=bad):
            self.assertEqual(consume_candidate_pass(self.f.root, submission_id=self.f.submission_id,
                                                    candidate_directory=str(self.f.root / "candidate"))["result"], "BLOCKED")
        with patch("scripts.delivery_gate.candidate._node_proof", side_effect=[good, bad]):
            self.assertEqual(consume_candidate_pass(self.f.root, submission_id=self.f.submission_id,
                                                    candidate_directory=str(self.f.root / "candidate"))["result"], "BLOCKED")

    def test_evidence_drift_during_final_node_proof_blocks(self):
        from tests.delivery_gate.test_consume import _tree
        paths = [
            self.f.root / f"tmp/quality/verification/{self.report['run_id']}/eng.release.candidate-runtime.stdout.log",
            self.f.root / delivery_gate_locator("reviews", self.f.review_id),
            self.f.root / f"tmp/quality/delivery-gate/validations/{self.f.validation_id}/report.json",
        ]
        for target in paths:
            with self.subTest(target=target.name):
                original = target.read_bytes()
                mode = target.stat().st_mode & 0o777
                calls = 0
                after_mutation = None

                def proof_during_read(_root, _request):
                    nonlocal calls, after_mutation
                    calls += 1
                    if calls == 2:
                        target.chmod(0o600)
                        target.write_bytes(b"{}\n")
                        after_mutation = _tree(self.f.root)
                    return dict(self.candidate)

                try:
                    with patch("scripts.delivery_gate.candidate._node_proof", side_effect=proof_during_read):
                        result = consume_candidate_pass(self.f.root, submission_id=self.f.submission_id,
                                                        candidate_directory=self.f.root / "candidate")
                    self.assertEqual(calls, 2)
                    self.assertEqual(result["result"], "BLOCKED", result)
                    self.assertEqual(after_mutation, _tree(self.f.root))
                finally:
                    target.write_bytes(original)
                    target.chmod(mode)

    def test_phase_facts_counts_and_correlation_fail_closed(self):
        import copy
        from scripts.delivery_gate.consume import consume_existing_pass
        proof = consume_existing_pass(self.f.root, submission_id=self.f.submission_id)["proof"]
        original = copy.deepcopy(self.report)
        mutations = [
            lambda o: o.update(run_id="00000000-0000-4000-8000-000000000000"),
            lambda o: o.update(check_config_fingerprint="0" * 64),
            lambda o: o.update(verify_input_fingerprint="0" * 64),
            lambda o: o.update(skipped=1),
            lambda o: o["runtime"]["phases"].pop(),
            lambda o: o["runtime"]["phases"][7]["facts"].update(targetApiStoppedOnce=False),
            lambda o: o["runtime"]["phases"][11]["facts"].update(datasetSame=False),
            lambda o: o["runtime"]["assertions"].update(realFaultAndRecovery=False),
            lambda o: o["runtime"]["cleanup"].update(settled=False),
            lambda o: o["runtime"]["candidates"].update(previous=o["runtime"]["candidates"]["target"]),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                report = copy.deepcopy(original)
                check = next(x for x in report["checks"] if x["check_id"] == "eng.release.candidate-runtime")
                output = check["result_contract"]["report"]
                mutate(output)
                data = json.dumps(output).encode()
                desc = check["process"]["output_artifacts"]["stdout"]
                (self.f.root / desc["locator"]).write_bytes(data)
                desc.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                report_data = canonical_bytes(report)
                file = self.f.root / proof["verification_report"]["locator"]
                file.chmod(0o600)
                file.write_bytes(report_data)
                bound = copy.deepcopy(proof)
                bound["verification_report"]["sha256"] = hashlib.sha256(report_data).hexdigest()
                with self.assertRaises((CandidateProofError, ValueError)):
                    _extract(self.f.root, bound)

    def test_cli_exit_codes_and_forbidden_extra_arguments(self):
        from contextlib import redirect_stdout, redirect_stderr
        from io import StringIO
        from scripts.delivery_gate.__main__ import main
        argv = ["consume-candidate", "--submission-id", self.f.submission_id,
                "--candidate-directory", str(self.f.root / "candidate")]
        for status, expected in (("PASS", 0), ("FAIL", 1), ("BLOCKED", 2)):
            with patch("scripts.delivery_gate.candidate.consume_candidate_pass", return_value={"result": status}), redirect_stdout(StringIO()):
                self.assertEqual(main(argv, root=self.f.root), expected)
        for flag in ("--actor", "--candidate-sha256", "--report", "--command"):
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main([*argv, flag, "untrusted"], root=self.f.root)

    def test_cleanup_omission_rejected_even_in_structurally_valid_report(self):
        from scripts.delivery_gate.consume import consume_existing_pass
        proof = consume_existing_pass(self.f.root, submission_id=self.f.submission_id)["proof"]
        report = self.report
        check = next(item for item in report["checks"] if item["check_id"] == "eng.release.candidate-runtime")
        output = check["result_contract"]["report"]
        output["runtime"]["cleanup"]["ownedResourcesRemoved"] = False
        raw_stdout = json.dumps(output).encode()
        stdout_descriptor = check["process"]["output_artifacts"]["stdout"]
        (self.f.root / stdout_descriptor["locator"]).write_bytes(raw_stdout)
        stdout_descriptor.update(bytes=len(raw_stdout), sha256=hashlib.sha256(raw_stdout).hexdigest())
        raw_report = canonical_bytes(report)
        (self.f.root / proof["verification_report"]["locator"]).chmod(0o600)
        (self.f.root / proof["verification_report"]["locator"]).write_bytes(raw_report)
        proof["verification_report"]["sha256"] = hashlib.sha256(raw_report).hexdigest()
        with self.assertRaisesRegex(CandidateProofError, "cleanup-invalid"):
            _extract(self.f.root, proof)


if __name__ == "__main__":
    unittest.main()
