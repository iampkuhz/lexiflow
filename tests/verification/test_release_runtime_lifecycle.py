"""单容器合成候选生命周期桥接的严格合同测试。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ops.release.integrity.candidate_runtime_result_contract import (
    PHASE_IDS,
    REQUIRED_FACTS,
)
from scripts.checks.release.source import release_installation_checks as lifecycle
from scripts.checks.release.source.check_synthetic_release_lifecycle import (
    ConsumerError,
)


def valid_result(
    candidate_sha: str, dataset_sha: str, dataset_version: int = 1
) -> dict:
    identity = {
        "releaseId": "synthetic-" + dataset_sha,
        "preparationId": "synthetic-"
        + hashlib.sha256(b"lexiflow.deterministic-preparation.v1").hexdigest(),
        "ruleId": "synthetic-no-model",
        "sqlVersion": "e" * 64,
        "sha256": dataset_sha,
    }
    api_identity = {
        "softwareVersion": "1.2.3-SNAPSHOT.20261024.gabcdef0",
        "apiContract": "caption-hints.v2",
        "mode": "formal",
        "datasetVersion": dataset_version,
        "publishedDataset": identity,
    }
    phases = []
    for phase_id in PHASE_IDS:
        facts = {field: True for field in REQUIRED_FACTS[phase_id]}
        if phase_id in {
            "target-clean-init",
            "target-repeat-run",
            "target-restart",
            "interrupted-init-retry",
        }:
            facts["publishedDatasetIdentity"] = identity
            facts["apiIdentity"] = api_identity
            facts["extensionIdentity"] = identity
        phases.append({"id": phase_id, "status": "PASS", "facts": facts})
    build_identity = {
        "schemaVersion": 1,
        "baseVersion": "1.2.3-SNAPSHOT",
        "softwareVersion": "1.2.3-SNAPSHOT.20261024.gabcdef0",
        "chromeVersion": "1.2.3.0",
        "sourceCommit": "a" * 40,
        "sourceSha256": "b" * 64,
        "buildId": "c" * 64,
        "dirty": False,
        "channel": "snapshot",
    }
    return {
        "schemaVersion": "lexiflow.candidate-runtime.v2",
        "status": "PASS",
        "phases": phases,
        "failure": None,
        "target": {
            "candidateSha256": candidate_sha,
            "manifestSha256": "d" * 64,
            "buildIdentity": build_identity,
        },
        "identity": {"source": build_identity},
        "cleanup": {"status": "PASS", "settled": True, "ownedResourcesRemoved": True},
        "assertions": {
            "targetOnly": True,
            "realInitializationInterruption": True,
            "sameVolumeRestart": True,
            "realFailureObserved": True,
            "unknownResourcesUntouched": True,
        },
    }


class TestReleaseRuntimeLifecycle(unittest.TestCase):
    def _setup(self, root: Path):
        scratch = root / "scratch"
        scratch.mkdir()
        fixture = root / "fixture"
        checker = fixture / "ops/podman/candidates/check-podman-candidate-lifecycle.mjs"
        checker.parent.mkdir(parents=True)
        checker.write_text("// fixture-owned check", encoding="utf-8")
        parent = scratch / "synthetic-releases"
        parent.mkdir()
        candidates = []
        datasets = []
        for index, data in enumerate((b"generation-one", b"generation-two")):
            directory = parent / f"candidate-{index}"
            directory.mkdir()
            marker = {"kind": "lexiflow-release-candidate", "sourceCommit": "a" * 40}
            payload = json.dumps(marker).encode()
            (directory / "candidate.json").write_bytes(payload)
            candidates.append(
                {
                    "candidateDirectory": str(directory),
                    "candidateSha256": hashlib.sha256(payload).hexdigest(),
                }
            )
            datasets.append(
                {"version": index + 1, "sha256": hashlib.sha256(data).hexdigest()}
            )
        evidence = {
            "releaseCandidates": candidates,
            "datasetRecords": datasets,
            "softwareVersion": "1.2.3-SNAPSHOT.20261024.gabcdef0",
            "sourceCommit": "a" * 40,
            "sqlVersion": "e" * 64,
        }
        return scratch, fixture, evidence

    @patch(
        "ops.release.host_resources.release_docker_preflight.recheck",
        return_value=SimpleNamespace(status="PASS"),
    )
    @patch(
        "scripts.checks.release.source.check_synthetic_release_lifecycle._run_bounded"
    )
    def test_uses_fixture_check_and_verifies_both_generation_results(
        self, run, _preflight
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scratch, fixture, evidence = self._setup(root)
            outputs = [
                json.dumps(
                    valid_result(c["candidateSha256"], d["sha256"], d["version"])
                ).encode()
                for c, d in zip(
                    evidence["releaseCandidates"], evidence["datasetRecords"]
                )
            ]
            run.side_effect = [(payload, b"") for payload in outputs]
            with patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir=str(root))):
                result = lifecycle.execute_lifecycle(
                    scratch,
                    evidence,
                    SimpleNamespace(endpoint="unix:///tmp/private.sock"),
                    {
                        "PATH": "/usr/bin",
                        "HOME": str(root),
                        "XDG_CONFIG_HOME": "/private/secret",
                        "PRIVATE_TOKEN": "never-forward",
                    },
                    fixture_root=fixture,
                )
            self.assertEqual(len(result["generations"]), 2)
            self.assertEqual(result["lifecycleChecks"], 16)
            for call in run.call_args_list:
                self.assertEqual(call.args[2]["HOME"], str(root.resolve()))
                self.assertEqual(
                    set(call.args[2]),
                    {"PATH", "HOME", "LC_ALL", "LANG", "TZ", "DOCKER_HOST"},
                )
                self.assertNotIn("PRIVATE_TOKEN", call.args[2])
                self.assertNotIn("XDG_CONFIG_HOME", call.args[2])
                self.assertEqual(
                    Path(call.args[0][1]),
                    fixture
                    / "ops/podman/candidates/check-podman-candidate-lifecycle.mjs",
                )
                self.assertLessEqual(call.args[3], 1100)

    @patch(
        "ops.release.host_resources.release_docker_preflight.recheck",
        return_value=SimpleNamespace(status="PASS"),
    )
    @patch(
        "scripts.checks.release.source.check_synthetic_release_lifecycle._run_bounded"
    )
    def test_safe_child_environment_uses_os_account_home(self, run, _preflight):
        import os

        from scripts.verification.execution.check_runner import build_child_environment

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            scratch, fixture, evidence = self._setup(root)
            run.side_effect = [
                (
                    json.dumps(
                        valid_result(c["candidateSha256"], d["sha256"], d["version"])
                    ).encode(),
                    b"",
                )
                for c, d in zip(
                    evidence["releaseCandidates"],
                    evidence["datasetRecords"],
                    strict=True,
                )
            ]
            with patch.dict(
                os.environ,
                {
                    "PATH": "/usr/bin",
                    "HOME": str(root / "untrusted-home"),
                    "USER": "untrusted-user",
                    "PRIVATE_TOKEN": "never-forward",
                    "XDG_CONFIG_HOME": str(root / "untrusted-config"),
                },
                clear=True,
            ):
                child = build_child_environment()
            self.assertNotIn("HOME", child)
            self.assertNotIn("PRIVATE_TOKEN", child)
            with patch(
                "pwd.getpwuid", return_value=SimpleNamespace(pw_dir=str(root))
            ) as account:
                result = lifecycle.execute_lifecycle(
                    scratch,
                    evidence,
                    SimpleNamespace(endpoint="unix:///tmp/private.sock"),
                    child,
                    fixture_root=fixture,
                )
            account.assert_called_once_with(os.getuid())
            self.assertEqual(len(result["generations"]), 2)
            for call in run.call_args_list:
                self.assertEqual(call.args[2]["HOME"], str(root))
                self.assertEqual(
                    set(call.args[2]),
                    {"PATH", "HOME", "LC_ALL", "LANG", "TZ", "DOCKER_HOST"},
                )

    def test_invalid_host_home_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _scratch, fixture, evidence = self._setup(root)
            bad_homes = ["", "relative", str(root / "missing"), str(root / "file")]
            (root / "file").write_text("not a directory", encoding="utf-8")
            for index, home in enumerate(bad_homes):
                local_scratch = root / f"scratch-{index}"
                local_scratch.mkdir()
                with (
                    self.subTest(home=home),
                    patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir=home)),
                    self.assertRaisesRegex(ConsumerError, "host-home-invalid"),
                ):
                    lifecycle.execute_lifecycle(
                        local_scratch,
                        evidence,
                        SimpleNamespace(endpoint="unix:///tmp/private.sock"),
                        {"PATH": "/usr/bin", "HOME": home},
                        fixture_root=fixture,
                    )

    def test_os_account_lookup_failure_is_rejected(self):
        for error in (KeyError("missing uid"), OSError("account service unavailable")):
            with tempfile.TemporaryDirectory() as temp:
                scratch, fixture, evidence = self._setup(Path(temp))
                with (
                    self.subTest(error=type(error).__name__),
                    patch("pwd.getpwuid", side_effect=error),
                    self.assertRaisesRegex(ConsumerError, "host-home-invalid"),
                ):
                    lifecycle.execute_lifecycle(
                        scratch,
                        evidence,
                        SimpleNamespace(endpoint="unix:///tmp/private.sock"),
                        {"HOME": temp},
                        fixture_root=fixture,
                    )

    @patch(
        "ops.release.host_resources.release_docker_preflight.recheck",
        return_value=SimpleNamespace(status="PASS"),
    )
    @patch(
        "scripts.checks.release.source.check_synthetic_release_lifecycle._run_bounded",
        return_value=(b'{"status":"PASS"}', b""),
    )
    def test_exit_zero_without_complete_runtime_facts_is_rejected(
        self, _run, _preflight
    ):
        with tempfile.TemporaryDirectory() as temp:
            scratch, fixture, evidence = self._setup(Path(temp))
            with self.assertRaisesRegex(
                ConsumerError, "candidate-runtime-result-invalid"
            ):
                lifecycle.execute_lifecycle(
                    scratch,
                    evidence,
                    SimpleNamespace(endpoint="unix:///tmp/private.sock"),
                    {"PATH": "/usr/bin", "HOME": str(Path.home())},
                    fixture_root=fixture,
                )

    @patch(
        "ops.release.host_resources.release_docker_preflight.recheck",
        return_value=SimpleNamespace(status="PASS"),
    )
    @patch(
        "scripts.checks.release.source.check_synthetic_release_lifecycle._run_bounded"
    )
    def test_dataset_generation_mismatch_is_rejected(self, run, _preflight):
        with tempfile.TemporaryDirectory() as temp:
            scratch, fixture, evidence = self._setup(Path(temp))
            candidate = evidence["releaseCandidates"][0]
            output = json.dumps(
                valid_result(candidate["candidateSha256"], "0" * 64)
            ).encode()
            run.return_value = (output, b"")
            with self.assertRaisesRegex(
                ConsumerError, "candidate-published-dataset-mismatch"
            ):
                lifecycle.execute_lifecycle(
                    scratch,
                    evidence,
                    SimpleNamespace(endpoint="unix:///tmp/private.sock"),
                    {"PATH": "/usr/bin", "HOME": str(Path.home())},
                    fixture_root=fixture,
                )

    @patch(
        "ops.release.host_resources.release_docker_preflight.recheck",
        return_value=SimpleNamespace(status="PASS"),
    )
    @patch(
        "scripts.checks.release.source.check_synthetic_release_lifecycle._run_bounded"
    )
    def test_dataset_version_mismatch_is_rejected(self, run, _preflight):
        with tempfile.TemporaryDirectory() as temp:
            scratch, fixture, evidence = self._setup(Path(temp))
            candidate, dataset = (
                evidence["releaseCandidates"][0],
                evidence["datasetRecords"][0],
            )
            output = json.dumps(
                valid_result(candidate["candidateSha256"], dataset["sha256"], 99)
            ).encode()
            run.return_value = (output, b"")
            with self.assertRaisesRegex(
                ConsumerError, "candidate-published-dataset-mismatch"
            ):
                lifecycle.execute_lifecycle(
                    scratch,
                    evidence,
                    SimpleNamespace(endpoint="unix:///tmp/private.sock"),
                    {"PATH": "/usr/bin", "HOME": str(Path.home())},
                    fixture_root=fixture,
                )

    def test_candidate_source_identity_mismatch_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            scratch, fixture, evidence = self._setup(Path(temp))
            evidence["sourceCommit"] = "f" * 40
            with self.assertRaisesRegex(
                ConsumerError, "release-candidate-source-mismatch"
            ):
                lifecycle.execute_lifecycle(
                    scratch,
                    evidence,
                    SimpleNamespace(endpoint="unix:///tmp/private.sock"),
                    {"PATH": "/usr/bin", "HOME": str(Path.home())},
                    fixture_root=fixture,
                )

    def test_missing_candidates_and_same_generation_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scratch = root / "scratch"
            scratch.mkdir()
            binding = SimpleNamespace(endpoint="unix:///tmp/private.sock")
            with self.assertRaisesRegex(
                ConsumerError, "release-candidate-not-assembled"
            ):
                lifecycle.execute_lifecycle(scratch, {}, binding, {}, fixture_root=root)
            with self.assertRaisesRegex(
                ConsumerError, "synthetic-generations-not-distinct"
            ):
                lifecycle.execute_lifecycle(
                    scratch,
                    {
                        "releaseCandidates": [{}, {}],
                        "datasetRecords": [
                            {"version": 1, "sha256": "a"},
                            {"version": 1, "sha256": "b"},
                        ],
                    },
                    binding,
                    {},
                    fixture_root=root,
                )
