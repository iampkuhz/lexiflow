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

from scripts.checks.release.candidate import (
    check_candidate_installation_lifecycle as check,
)
from ops.release.integrity.candidate_runtime_result_contract import (
    REQUIRED_FACTS,
    RuntimeContractError,
    validate_runtime_result,
)
from scripts.verification.planning.check_definitions import load_declarations
from scripts.environment.configuration import verification_environment as environment
from scripts.verification.execution.check_runner import (
    snapshot_check_inputs,
    operation_config,
)

ROOT = Path(__file__).resolve().parents[2]


def request(root: Path) -> dict:
    return {"target": {"candidateDirectory": str(root / "target"), "candidateSha256": "b" * 64}}


def result(data: dict) -> dict:
    identity = {
        "schemaVersion": 1,
        "baseVersion": "2.0.0-SNAPSHOT",
        "softwareVersion": "2.0.0-SNAPSHOT.20261006.gaaaaaaa",
        "chromeVersion": "2.0.0.0",
        "sourceCommit": "a" * 40,
        "sourceSha256": "b" * 64,
        "dirty": False,
        "buildId": "c" * 64,
        "channel": "snapshot",
    }
    dataset = {"releaseId": "release", "preparationId": "preparation", "ruleId": "rule", "sqlVersion": "v1", "sha256": "9" * 64}
    api = {"softwareVersion": identity["softwareVersion"], "apiContract": "caption-hints.v2", "mode": "formal", "datasetVersion": 1, "publishedDataset": dataset}
    return {
        "schemaVersion": "lexiflow.candidate-runtime.v2",
        "status": "PASS",
        "target": {"candidateSha256": data["target"]["candidateSha256"], "manifestSha256": "d" * 64, "buildIdentity": identity},
        "identity": {"source": identity},
        "phases": [
            {
                "id": name,
                "status": "PASS",
                "facts": {
                    **{key: True for key in REQUIRED_FACTS[name]},
                    **({"runtimeImageIdentity": True} if name == "target-clean-init" else {}),
                    **({"apiIdentity": api, "extensionIdentity": dataset,
                        "publishedDatasetIdentity": dataset}
                       if name in {"target-repeat-run", "target-restart", "interrupted-init-retry"} else {}),
                    **({"apiIdentity": api, "extensionIdentity": dataset,
                        "publishedDatasetIdentity": dataset}
                       if name == "target-clean-init" else {}),
                },
            }
            for name in check.PHASE_IDS
        ],
        "cleanup": {"status": "PASS", "settled": True, "ownedResourcesRemoved": True},
        "assertions": {
            "targetOnly": True,
            "realInitializationInterruption": True,
            "sameVolumeRestart": True,
            "realFailureObserved": True,
            "unknownResourcesUntouched": True,
        },
        "failure": None,
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
        self.env = {
            check.REQUEST_ENV: str(self.file),
            "PATH": os.environ.get("PATH", ""),
            "OPENAI_API_KEY": "must-not-pass",
            "NODE_OPTIONS": "must-not-pass",
        }
        self.declared = {
            "check_id": "eng.release.candidate-runtime",
            "scope": "repository-baseline",
            "command": [
                "python3",
                "-m",
                "scripts.checks.release.candidate.check_candidate_installation_lifecycle",
            ],
            "executable": "python3",
            "input_paths": ["source.txt"],
            "triggers": [{"path": "source.txt"}],
        }
        (self.root / "config/engineering").mkdir(parents=True)
        (self.root / "source.txt").write_text("frozen")
        self.envelope = self._envelope(self.declared)

    def _envelope(self, declared):
        (self.root / "config/engineering/module-checks.yaml").write_text(
            yaml.safe_dump({"checks": [declared]})
        )
        effective = {
            **operation_config(declared),
            "input_paths": [
                *declared["input_paths"],
                "config/engineering/module-checks.yaml",
            ],
        }
        return {
            "schema_version": "lexiflow.verify-child-input.v2",
            "run_id": "12345678-1234-4234-8234-123456789012",
            "check_id": declared["check_id"],
            "snapshot": snapshot_check_inputs(self.root, effective),
        }

    def test_request_is_exact_bounded_regular_and_hash_bound(self):
        parsed, sha = check._read_request(self.env)
        self.assertEqual(parsed, self.request)
        self.assertRegex(sha, r"^[a-f0-9]{64}$")
        with self.assertRaisesRegex(check.ConsumerError, "BLOCKED"):
            check._read_request({})
        for raw in [
            b'{"target":{},"target":{}}',
            b"NaN",
            b" " * (check.MAX_REQUEST_BYTES + 1),
            json.dumps({**self.request, "status": "PASS"}).encode(),
            json.dumps({**self.request, "extra": self.request["target"]}).encode(),
        ]:
            self.file.write_bytes(raw)
            with self.subTest(raw=raw[:50]), self.assertRaises(check.ConsumerError):
                check._read_request(self.env)
        self.file.unlink()
        self.file.symlink_to(self.root / "source.txt")
        with self.assertRaises(check.ConsumerError):
            check._read_request(self.env)

    def test_environment_forwards_only_explicit_locator_without_reporting_it(self):
        decl = {"required_environment": ["candidate-runtime-request"]}
        resolved = environment.resolve_check_environment(self.root, decl, self.env)
        values = resolved.values
        self.assertEqual(values, {check.REQUEST_ENV: str(self.file)})
        diag = resolved.diagnostic
        self.assertEqual(diag["status"], "PASS")
        self.assertNotIn(str(self.file), json.dumps(diag))
        self.assertEqual(
            environment.resolve_check_environment(self.root, decl, {}).diagnostic[
                "status"
            ],
            "BLOCKED",
        )
        invalid = environment.resolve_check_environment(
            self.root, decl, {check.REQUEST_ENV: "relative.json"}
        )
        self.assertEqual(invalid.diagnostic["status"], "BLOCKED")
        self.assertEqual(dict(invalid.values), {})

    def test_registry_snapshot_and_request_bound_to_fixed_child(self):
        expected = result(self.request)

        def child(argv, root, env, timeout, *, input_stream):
            self.assertEqual(
                argv,
                ["node", "ops/podman/candidates/check-podman-candidate-lifecycle.mjs"],
            )
            self.assertEqual(root, self.root)
            self.assertEqual(timeout, 2100)
            self.assertEqual(json.load(input_stream), self.request)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("NODE_OPTIONS", env)
            return json.dumps(expected).encode(), b"private"

        with (
            patch(
                "scripts.verification.planning.check_definitions.load_declarations",
                return_value={"checks": [self.declared]},
            ),
            patch.object(check.process_runtime, "_run_bounded", side_effect=child),
        ):
            report = check.run_consumer(self.root, self.envelope, self.env)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(
            report["verify_input_fingerprint"], self.envelope["snapshot"]["fingerprint"]
        )
        self.assertEqual(report["runtime"], expected)
        self.assertEqual(report["checks_run"], 8)

    def test_envelope_accepts_only_candidate_ids_for_candidate_check(self):
        stream = type(
            "Stream", (), {"buffer": io.BytesIO(json.dumps(self.envelope).encode())}
        )()
        got = check.process_runtime._read_envelope(
            stream, check_ids=check.CANDIDATE_RUNTIME_CHECK_IDS
        )
        self.assertEqual(got, self.envelope)
        self.envelope["check_id"] = "eng.release.lifecycle-runtime"
        stream.buffer = io.BytesIO(json.dumps(self.envelope).encode())
        with self.assertRaises(check.ConsumerError):
            check.process_runtime._read_envelope(
                stream, check_ids=check.CANDIDATE_RUNTIME_CHECK_IDS
            )

    def test_snapshot_drift_and_forged_effective_check_prevent_spawn(self):
        with (
            patch(
                "scripts.verification.planning.check_definitions.load_declarations",
                return_value={"checks": [self.declared]},
            ),
            patch.object(check.process_runtime, "_run_bounded") as child,
        ):
            altered = copy.deepcopy(self.envelope)
            altered["effective_check"] = {"input_paths": []}
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
                    (self.root / "source.txt" if source else self.file).write_text(
                        "changed"
                    )
                    return json.dumps(result(self.request)).encode(), b""

                with (
                    patch(
                        "scripts.verification.planning.check_definitions.load_declarations",
                        return_value={"checks": [self.declared]},
                    ),
                    patch.object(
                        check.process_runtime, "_run_bounded", side_effect=child
                    ),
                ):
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
            lambda v: v["target"].update(candidateSha256="e" * 64),
            lambda v: v["identity"].update(source={"dirty": True}),
        ):
            bad = copy.deepcopy(good)
            mutate(bad)
            bad_values.append(bad)
        for bad in bad_values:
            with self.subTest(bad=bad), self.assertRaises(check.ConsumerError):
                check._validate_result(bad, self.request)
        for status in ("BLOCKED", "FAIL"):
            check._validate_result(
                {"schemaVersion": "lexiflow.candidate-runtime.v2", "status": status},
                self.request,
            )

    def test_shared_contract_rejects_every_missing_fact_and_identity(self):
        good = result(self.request)
        validate_runtime_result(good)
        for index, phase in enumerate(good["phases"]):
            for field in REQUIRED_FACTS[phase["id"]]:
                bad = copy.deepcopy(good)
                del bad["phases"][index]["facts"][field]
                with (
                    self.subTest(phase=phase["id"], field=field),
                    self.assertRaises(RuntimeContractError),
                ):
                    validate_runtime_result(bad)
        for mutate in (
            lambda v: v["cleanup"].pop("ownedResourcesRemoved"),
            lambda v: v["assertions"].pop("realFailureObserved"),
            lambda v: v["target"]["buildIdentity"].update(
                channel="unknown"
            ),
            lambda v: v["target"]["buildIdentity"].update(
                sourceCommit="invalid"
            ),
        ):
            bad = copy.deepcopy(good)
            mutate(bad)
            with self.assertRaises(RuntimeContractError):
                validate_runtime_result(bad)

    def test_both_declarations_are_complete_and_excluded_from_quick(self):
        document = load_declarations(ROOT)
        for identifier in check.CANDIDATE_RUNTIME_CHECK_IDS:
            declared = next(
                item for item in document["checks"] if item["check_id"] == identifier
            )
            self.assertEqual(
                declared["command"],
                [
                    "python3",
                    "-m",
                    "scripts.checks.release.candidate.check_candidate_installation_lifecycle",
                ],
            )
            self.assertIn("candidate-runtime-request", declared["required_environment"])
            self.assertIn("ops/podman", declared["input_paths"])
            self.assertEqual(declared["result_contract"]["equals"]["skipped"], 0)
        policy = document["scenario_policy"]["ci_quick"]
        self.assertIn("eng.release.candidate-runtime", policy["formal_only_check_ids"])
