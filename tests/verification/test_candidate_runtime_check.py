"""验证实际候选 Check 的冻结输入、请求与结果绑定边界。"""

from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.environment import candidate_runtime_check as check
from scripts.environment import runtime as environment
from scripts.verification.kernel import fingerprint_json, snapshot_check_inputs

ROOT = Path(__file__).resolve().parents[2]


def request(root: Path) -> dict:
    return {name: {"candidateDirectory": str(root / name), "candidateSha256": letter * 64}
            for name, letter in (("previous", "a"), ("target", "b"))}


def result(data: dict) -> dict:
    identity = {"schemaVersion": 1, "baseVersion": "2.0.0-SNAPSHOT",
                "softwareVersion": "2.0.0-SNAPSHOT.gaaaaaaa", "chromeVersion": "2.0.0.0",
                "sourceCommit": "a" * 40, "sourceSha256": "b" * 64,
                "dirty": False, "buildId": "c" * 64, "channel": "snapshot"}
    return {
        "schemaVersion": "lexiflow.candidate-runtime.v1", "status": "PASS",
        "candidates": {name: {"candidateSha256": value["candidateSha256"],
                               "manifestSha256": "d" * 64, "buildIdentity": identity}
                       for name, value in data.items()},
        "identity": {"source": identity},
        "phases": [{"id": name, "status": "PASS"} for name in check.PHASE_IDS],
        "cleanup": {"status": "PASS", "settled": True}, "failure": None,
    }


class CandidateRuntimeCheckTests(unittest.TestCase):
    """所有替身仅存在于测试，不暴露运行注入入口。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.request = request(self.root)
        self.file = self.root / "request.json"
        self.file.write_text(json.dumps(self.request))
        self.env = {check.REQUEST_ENV: str(self.file), "PATH": os.environ.get("PATH", ""),
                    "OPENAI_API_KEY": "must-not-pass", "NODE_OPTIONS": "must-not-pass"}
        self.declared = {
            "check_id": "eng.release.candidate-runtime", "scope": "repository-baseline",
            "input_paths": ["source.txt"], "triggers": [{"path": "source.txt"}],
        }
        (self.root / "harness").mkdir()
        (self.root / "source.txt").write_text("frozen")
        self.envelope = self._envelope(self.declared)

    def _envelope(self, declared):
        (self.root / "harness/module-checks.yaml").write_text(yaml.safe_dump({"checks": [declared]}))
        effective = {**declared, "input_paths": [*declared["input_paths"], "harness/module-checks.yaml"]}
        return {"schema_version": "lexiflow.verify-child-input.v1", "run_id": "unit-only",
                "check_id": declared["check_id"], "effective_check": effective,
                "check_config_fingerprint": fingerprint_json(effective),
                "snapshot": snapshot_check_inputs(self.root, effective)}

    def test_request_is_exact_bounded_regular_and_hash_bound(self):
        parsed, sha = check._read_request(self.env)
        self.assertEqual(parsed, self.request)
        self.assertRegex(sha, r"^[a-f0-9]{64}$")
        with self.assertRaisesRegex(check.ConsumerError, "BLOCKED"):
            check._read_request({})
        for raw in [b'{"previous":{},"previous":{}}', b'NaN', b' ' * (check.MAX_REQUEST_BYTES + 1),
                    json.dumps({**self.request, "status": "PASS"}).encode(),
                    json.dumps({**self.request, "previous": self.request["target"]}).encode()]:
            self.file.write_bytes(raw)
            with self.subTest(raw=raw[:50]), self.assertRaises(check.ConsumerError):
                check._read_request(self.env)
        self.file.unlink()
        self.file.symlink_to(self.root / "source.txt")
        with self.assertRaises(check.ConsumerError):
            check._read_request(self.env)

    def test_environment_forwards_only_explicit_locator_without_reporting_it(self):
        decl = {"required_environment": ["candidate-runtime-request"]}
        values = environment.execution_environment(self.root, decl, self.env)
        self.assertEqual(values, {check.REQUEST_ENV: str(self.file)})
        diag = environment.diagnose(self.root, decl["required_environment"], self.env)
        self.assertEqual(diag["status"], "PASS")
        self.assertNotIn(str(self.file), json.dumps(diag))
        self.assertEqual(environment.diagnose(self.root, decl["required_environment"], {})["status"], "BLOCKED")
        with self.assertRaises(Exception):
            environment.execution_environment(self.root, decl, {check.REQUEST_ENV: "relative.json"})

    def test_registry_snapshot_and_request_bound_to_fixed_child(self):
        expected = result(self.request)
        def child(argv, root, env, timeout, *, input_stream):
            self.assertEqual(argv, ["node", "ops/podman/candidate-runtime.mjs"])
            self.assertEqual(root, self.root)
            self.assertEqual(timeout, 2100)
            self.assertEqual(json.load(input_stream), self.request)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("NODE_OPTIONS", env)
            return json.dumps(expected).encode(), b"private"
        with patch.object(check, "load_declarations", return_value={"checks": [self.declared]}), patch.object(check.process_runtime, "_run_bounded", side_effect=child):
            report = check.run_consumer(self.root, self.envelope, self.env)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["verify_input_fingerprint"], self.envelope["snapshot"]["fingerprint"])
        self.assertEqual(report["runtime"], expected)
        self.assertEqual(report["checks_run"], 13)

    def test_envelope_accepts_only_candidate_ids_for_candidate_check(self):
        stream = type("Stream", (), {"buffer": io.BytesIO(json.dumps(self.envelope).encode())})()
        got = check.process_runtime._read_envelope(stream, check_ids=check.CANDIDATE_RUNTIME_CHECK_IDS)
        self.assertEqual(got, self.envelope)
        self.envelope["check_id"] = "eng.release.lifecycle-runtime"
        stream.buffer = io.BytesIO(json.dumps(self.envelope).encode())
        with self.assertRaises(check.ConsumerError):
            check.process_runtime._read_envelope(stream, check_ids=check.CANDIDATE_RUNTIME_CHECK_IDS)

    def test_snapshot_drift_and_forged_effective_check_prevent_spawn(self):
        with patch.object(check, "load_declarations", return_value={"checks": [self.declared]}), patch.object(check.process_runtime, "_run_bounded") as child:
            altered = copy.deepcopy(self.envelope)
            altered["effective_check"]["input_paths"] = []
            altered["check_config_fingerprint"] = fingerprint_json(altered["effective_check"])
            with self.assertRaises(check.ConsumerError):
                check.run_consumer(self.root, altered, self.env)
            (self.root / "source.txt").write_text("drift")
            with self.assertRaisesRegex(check.ConsumerError, "snapshot-mismatch"):
                check.run_consumer(self.root, self.envelope, self.env)
            child.assert_not_called()

    def test_mutation_during_child_invalidates_success(self):
        for source in (True, False):
            with self.subTest(source=source):
                (self.root / "source.txt").write_text("frozen")
                self.file.write_text(json.dumps(self.request))
                def child(*_args, **_kwargs):
                    (self.root / "source.txt" if source else self.file).write_text("changed")
                    return json.dumps(result(self.request)).encode(), b""
                with patch.object(check, "load_declarations", return_value={"checks": [self.declared]}), patch.object(check.process_runtime, "_run_bounded", side_effect=child):
                    with self.assertRaises(check.ConsumerError):
                        check.run_consumer(self.root, self.envelope, self.env)

    def test_success_requires_complete_phases_cleanup_and_exact_candidate_binding(self):
        good = result(self.request)
        check._validate_result(good, self.request)
        bad_values = []
        for mutate in (
            lambda v: v["phases"].pop(),
            lambda v: v["phases"][0].update(status="BLOCKED"),
            lambda v: v["phases"].reverse(),
            lambda v: v["cleanup"].update(settled=False),
            lambda v: v["cleanup"].update(status="FAIL"),
            lambda v: v["candidates"]["target"].update(candidateSha256="e" * 64),
            lambda v: v["candidates"]["previous"].update(manifestSha256="bad"),
            lambda v: v["identity"].update(source={"dirty": True}),
        ):
            bad = copy.deepcopy(good)
            mutate(bad)
            bad_values.append(bad)
        for bad in bad_values:
            with self.subTest(bad=bad), self.assertRaises(check.ConsumerError):
                check._validate_result(bad, self.request)
        for status in ("BLOCKED", "FAIL"):
            check._validate_result({"schemaVersion": "lexiflow.candidate-runtime.v1", "status": status}, self.request)

    def test_both_declarations_are_complete_and_excluded_from_quick(self):
        document = yaml.safe_load((ROOT / "harness/module-checks.yaml").read_text())
        for identifier in check.CANDIDATE_RUNTIME_CHECK_IDS:
            declared = next(item for item in document["checks"] if item["check_id"] == identifier)
            self.assertEqual(declared["command"], ["python3", "-m", "scripts.environment.candidate_runtime_check"])
            self.assertIn("candidate-runtime-request", declared["required_environment"])
            self.assertIn("ops/podman", declared["input_paths"])
            self.assertEqual(declared["result_contract"]["equals"]["skipped"], 0)
        policy = yaml.safe_load((ROOT / "harness/ci-policy.yaml").read_text())
        self.assertIn("eng.release.candidate-runtime", policy["formal_only_check_ids"])
