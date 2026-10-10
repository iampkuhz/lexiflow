"""合成producer 与产物检查的直接替身测试。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.environment import release_runtime_artifacts as artifacts


class TestReleaseRuntimeArtifacts(unittest.TestCase):
    def test_producer_validates_exact_two_generation_result(self):
        self._build_synthetic_result()

    def _build_synthetic_result(self, formal=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            for path in (
                "backend/product/api/build/libs",
                "extension/dist",
                "ops/release",
                "ops/docker",
                "infra/postgres",
            ):
                (repo / path).mkdir(parents=True, exist_ok=True)
            scratch = root / "scratch"
            scratch.mkdir()
            cache = root / "cache"
            for path in ("caches/modules-2", "wrapper/dists"):
                (cache / path).mkdir(parents=True, exist_ok=True)
            npm = root / "npm-cache"
            npm.mkdir()
            java = root / "jdk/bin"
            java.mkdir(parents=True)
            (java / "java").write_text("", encoding="utf-8")

            def fake_run(argv, *, cwd, env, timeout=1800):
                if argv[0] == "npm":
                    self.assertEqual(os.devnull, env["npm_config_globalconfig"])
                    self.assertEqual(
                        str(scratch / "home/npmrc"), env["npm_config_userconfig"]
                    )
                if any("produceSyntheticReleaseDatasets" in item for item in argv):
                    output = Path(
                        next(
                            item.split("=", 1)[1]
                            for item in argv
                            if item.startswith("-Dlexiflow.release.fixture.output=")
                        )
                    )
                    output.mkdir(parents=True, exist_ok=False)
                    rows = []
                    for index, (name, payload) in enumerate(
                        (("dataset-a.zip", b"zip-a"), ("dataset-b.zip", b"zip-b")), 1
                    ):
                        (output / name).write_bytes(payload)
                        rows.append(
                            {
                                "file": name,
                                "bytes": len(payload),
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "datasetVersion": index,
                                "preparationPolicy": "lexiflow.deterministic-preparation.v1",
                            }
                        )
                    report = {
                        "schemaVersion": 1,
                        "synthetic": True,
                        "formalReleaseEligible": formal,
                        "datasets": rows,
                    }
                    (output / "result.json").write_text(
                        json.dumps(report), encoding="utf-8"
                    )
                return __import__("subprocess").CompletedProcess(argv, 0, b"", b"")

            env = {
                "PATH": "/usr/bin:/bin",
                "npm_config_globalconfig": str(root / "untrusted-global-npmrc"),
                "npm_config_userconfig": str(root / "untrusted-user-npmrc"),
                "JAVA_HOME": str(java.parent.parent),
                "LEXIFLOW_RELEASE_GRADLE_CACHE": str(cache),
                "LEXIFLOW_RELEASE_NPM_CACHE": str(npm),
                "LEXIFLOW_POSTGRES_TEST_JDBC_URL": "jdbc:postgresql://isolated.invalid/test",
            }
            with patch.object(artifacts, "_run", side_effect=fake_run):
                built = artifacts.build_local_inputs(repo, scratch, env)
            self.assertEqual(set(built), {"home", "fixtures", "artifact_root", "producer"})
            self.assertIs(built["producer"]["formalReleaseEligible"], False)
            self.assertEqual(
                {row["file"] for row in built["producer"]["datasets"]},
                {"dataset-a.zip", "dataset-b.zip"},
            )

    def test_formal_license_assertion_is_rejected(self):
        # 实际消费者必须拒绝 producer 声称合成资料可正式分发。
        from scripts.environment.release_runtime_check import ConsumerError

        with self.assertRaisesRegex(ConsumerError, "synthetic-producer-result-invalid"):
            self._build_synthetic_result(formal=True)


class TestBuildSourceIdentity(unittest.TestCase):
    def test_shared_identity_selects_resolved_snapshot_paths(self):
        import subprocess

        version = "2.0.0-SNAPSHOT.20261006.gabc1234"
        result = subprocess.CompletedProcess(
            [], 0,
            json.dumps({"softwareVersion": version, "sourceCommit": "a" * 40}).encode(),
            b"",
        )
        with patch.object(artifacts, "_run", return_value=result) as run:
            self.assertEqual((version, "a" * 40), artifacts._source_identity(Path("/synthetic"), {}))
        self.assertEqual(["node", "ops/release/version.mjs", "--release"], run.call_args.args[0])

    def test_source_identity_rejects_unsafe_output_paths(self):
        import subprocess
        from scripts.environment.release_runtime_check import ConsumerError

        for payload in [b"null", b"{}", b"not json", json.dumps({"softwareVersion": "../../private", "sourceCommit": "a" * 40}).encode()]:
            with patch.object(artifacts, "_run", return_value=subprocess.CompletedProcess([], 0, payload, b"")):
                with self.assertRaises(ConsumerError):
                    artifacts._source_identity(Path("/synthetic"), {})


class TestCanonicalPipelineRequests(unittest.TestCase):
    """只用合成字节验证 producer 请求与 shard 绑定，不运行真实发行。"""

    def test_requests_derive_image_metadata_from_bound_candidate(self):
        self._produce()

    def test_changed_candidate_marker_is_rejected_before_assemble(self):
        from scripts.environment.release_runtime_check import ConsumerError

        with self.assertRaisesRegex(ConsumerError, "image-candidate-hash-mismatch"):
            self._produce(corrupt_marker=True)

    def test_changed_candidate_archive_is_rejected_before_assemble(self):
        from scripts.environment.release_runtime_check import ConsumerError

        with self.assertRaisesRegex(ConsumerError, "image-candidate-artifact-mismatch"):
            self._produce(corrupt_archive=True)

    def _produce(self, *, corrupt_marker=False, corrupt_archive=False):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, scratch = root / "repo", root / "scratch"
            scratch.mkdir()
            artifact_root, fixtures, home = scratch / "artifacts", scratch / "datasets", scratch / "home"
            for target in (artifact_root, fixtures, home):
                target.mkdir()
            version, commit = "2.0.0", "a" * 40
            for name, payload in (
                (f"backend/product/api/build/libs/api-{version}.jar", b"jar"),
                (f"tmp/releases/{version}/{commit}/lexiflow-extension-{version}.zip", b"extension"),
                ("ops/docker/compose.yaml", b"compose"),
                ("infra/postgres/schema.sql", b"sql"),
            ):
                target = repo / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            datasets = []
            for index, name in enumerate(("dataset-a.zip", "dataset-b.zip"), 1):
                payload = name.encode()
                (fixtures / name).write_bytes(payload)
                datasets.append({"file": name, "sha256": hashlib.sha256(payload).hexdigest(), "datasetVersion": index, "preparationPolicy": "lexiflow.deterministic-preparation.v1"})
            built = {"home": home, "fixtures": fixtures, "artifact_root": artifact_root, "producer": {"datasets": datasets}}
            binding = SimpleNamespace(target_platform="linux/arm64", endpoint="unix:///synthetic.sock")
            calls = []

            def pipeline(argv, cwd, env, timeout, output_parent):
                self.assertEqual(cwd, repo)
                request = json.loads(Path(argv[-1]).read_text())
                calls.append(argv[-2])
                if argv[-2] == "images":
                    self.assertEqual(set(request["descriptor"]), {"schemaVersion", "softwareVersion", "sourceCommit", "jar"})
                    self.assertEqual(request["descriptor"]["jar"], {"path": "api.jar", "bytes": 3, "sha256": hashlib.sha256(b"jar").hexdigest()})
                    candidate = output_parent / "image-shard"
                    candidate.mkdir()
                    records = []
                    for role in ("api-image", "postgres-image"):
                        path = f"images/linux-arm64/{role}.tar"
                        payload = role.encode()
                        target = candidate / path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(payload)
                        records.append({"role": role, "platform": "linux/arm64", "path": path, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "imageDigest": "sha256:" + "b" * 64})
                    marker = json.dumps({"schemaVersion": 1, "kind": "lexiflow-image-candidate", "platform": "linux/arm64", "buildIdentity": {"softwareVersion": version, "sourceCommit": commit, "dirty": False}, "artifacts": records}).encode()
                    (candidate / "candidate.json").write_bytes(marker + (b" " if corrupt_marker else b""))
                    if corrupt_archive:
                        (candidate / records[0]["path"]).write_bytes(b"changed")
                    return {"candidateDirectory": str(candidate), "candidateSha256": hashlib.sha256(marker).hexdigest()}
                self.assertEqual(argv[-2], "assemble")
                images = [item for item in request["descriptor"]["artifacts"] if item["role"].endswith("-image")]
                self.assertEqual({row["role"] for row in images}, {"api-image", "postgres-image"})
                self.assertTrue(all(set(row) == {"role", "platform", "licenseIds"} for row in images))
                self.assertFalse((artifact_root / "images").exists())
                self.assertEqual(len(request["imageCandidates"]), 1)
                self.assertEqual(set(request["imageCandidates"][0]), {"directory", "sha256"})
                return {"candidateDirectory": str(output_parent / f"release-{len(calls)}")}

            with patch.object(artifacts, "build_local_inputs", return_value=built), patch.object(artifacts, "_source_identity", return_value=(version, commit)), patch("scripts.environment.release_runtime_check._run_json_command", side_effect=pipeline):
                result = artifacts.produce_candidates(repo, scratch, {"PATH": "/usr/bin:/bin"}, (binding,))
            self.assertEqual(calls, ["images", "assemble", "assemble"])
            self.assertEqual(len(result["releaseCandidates"]), 2)
            self.assertIs(result["formalReleaseEligible"], False)
