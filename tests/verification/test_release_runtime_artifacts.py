"""合成producer 与产物检查的直接替身测试。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.checks.release.source import release_test_artifact_builder as artifacts


class TestReleaseRuntimeArtifacts(unittest.TestCase):
    def test_producer_validates_exact_two_generation_result(self):
        self._build_synthetic_result()

    def _build_synthetic_result(self, formal=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            for path in (
                "backend/product/api/build/internal/boot",
                "extension/dist",
                "ops/release",
                "ops/docker",
                "infra/postgres",
            ):
                (repo / path).mkdir(parents=True, exist_ok=True)
            (repo / "infra/postgres/schema.sql").write_bytes(b"synthetic-schema")
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
                if argv[:3] == ["npm", "run", "build"]:
                    identity = json.loads(env["LEXIFLOW_EXPECTED_DATASET"])
                    self.assertEqual(
                        identity["releaseId"],
                        "synthetic-" + hashlib.sha256(b"zip-a").hexdigest(),
                    )
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
            self.assertEqual(
                set(built), {"home", "fixtures", "artifact_root", "producer"}
            )
            self.assertIs(built["producer"]["formalReleaseEligible"], False)
            self.assertEqual(
                {row["file"] for row in built["producer"]["datasets"]},
                {"dataset-a.zip", "dataset-b.zip"},
            )

    def test_formal_license_assertion_is_rejected(self):
        # 实际消费者必须拒绝 producer 声称合成资料可正式分发。
        from scripts.checks.release.source.check_synthetic_release_lifecycle import (
            ConsumerError,
        )

        with self.assertRaisesRegex(ConsumerError, "synthetic-producer-result-invalid"):
            self._build_synthetic_result(formal=True)


class TestSyntheticBuilderContract(unittest.TestCase):
    def test_single_runtime_descriptor_binds_each_distinct_dataset(self):
        import subprocess

        root = Path(__file__).resolve().parents[2]
        script = r"""
import { validateSyntheticInput, syntheticDescriptor } from './ops/release/test_tools/build-synthetic-release-candidates.mjs';
const rows = ['a','b'].map((letter,index) => ({file:`dataset-${letter}.zip`,bytes:3,sha256:String(index+1).repeat(64),datasetVersion:index+1,preparationPolicy:'lexiflow.deterministic-preparation.v1'}));
const request = {scratchDirectory:'/tmp/synthetic',endpoint:'unix:///tmp/private.sock',datasets:rows};
validateSyntheticInput(request);
let duplicateRejected=false; try { validateSyntheticInput({...request,datasets:[rows[0],{...rows[1],datasetVersion:rows[0].datasetVersion}]}); } catch { duplicateRejected=true; }
const descriptor=syntheticDescriptor({softwareVersion:'1.2.3',sourceCommit:'f'.repeat(40)},[],[],rows[0],'e'.repeat(64));
process.stdout.write(JSON.stringify({duplicateRejected,descriptor}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=root,
            env={
                "PATH": os.environ.get("PATH", os.defpath),
                "HOME": os.environ.get("HOME", ""),
            },
            check=True,
            capture_output=True,
            timeout=15,
        )
        result = json.loads(completed.stdout)
        self.assertTrue(result["duplicateRejected"])
        descriptor = result["descriptor"]
        self.assertEqual(descriptor["platforms"], ["linux/arm64"])
        self.assertEqual(descriptor["dataset"]["releaseId"], "synthetic-" + "1" * 64)
        self.assertEqual(descriptor["dataset"]["ruleId"], "synthetic-no-model")
        self.assertEqual(descriptor["apiContract"], "caption-hints.v2")
        self.assertEqual(descriptor["sqlVersion"], "e" * 64)
        self.assertEqual(
            [item["role"] for item in descriptor["artifacts"]], ["dataset"]
        )


class TestBuildSourceIdentity(unittest.TestCase):
    def test_shared_identity_selects_resolved_snapshot_paths(self):
        import subprocess

        version = "2.0.0-SNAPSHOT.20261006.gabc1234"
        result = subprocess.CompletedProcess(
            [],
            0,
            json.dumps({"softwareVersion": version, "sourceCommit": "a" * 40}).encode(),
            b"",
        )
        with patch.object(artifacts, "_run", return_value=result) as run:
            self.assertEqual(
                (version, "a" * 40), artifacts._source_identity(Path("/synthetic"), {})
            )
        self.assertEqual(
            ["node", "ops/release/integrity/source-build-identity.mjs", "--release"],
            run.call_args.args[0],
        )

    def test_source_identity_rejects_unsafe_output_paths(self):
        import subprocess

        from scripts.checks.release.source.check_synthetic_release_lifecycle import (
            ConsumerError,
        )

        for payload in [
            b"null",
            b"{}",
            b"not json",
            json.dumps(
                {"softwareVersion": "../../private", "sourceCommit": "a" * 40}
            ).encode(),
        ]:
            with (
                patch.object(
                    artifacts,
                    "_run",
                    return_value=subprocess.CompletedProcess([], 0, payload, b""),
                ),
                self.assertRaises(ConsumerError),
            ):
                artifacts._source_identity(Path("/synthetic"), {})


class TestFixedSyntheticBridge(unittest.TestCase):
    """验证唯一 stdin 请求、归属和真实 marker 摘要，生产不接受 executor 注入。"""

    def test_single_anonymous_bridge_and_owned_result(self):
        self._produce()

    def test_changed_candidate_marker_rejected(self):
        from scripts.checks.release.source.check_synthetic_release_lifecycle import (
            ConsumerError,
        )

        with self.assertRaisesRegex(ConsumerError, "synthetic-bridge-result-invalid"):
            self._produce(corrupt=True)

    def test_foreign_candidate_directory_rejected(self):
        from scripts.checks.release.source.check_synthetic_release_lifecycle import (
            ConsumerError,
        )

        with self.assertRaisesRegex(ConsumerError, "synthetic-bridge-result-invalid"):
            self._produce(foreign=True)

    def _produce(self, *, corrupt=False, foreign=False):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo, scratch = root / "repo", root / "scratch"
            scratch.mkdir()
            for name, data in (
                ("backend/product/api/build/internal/boot/api-internal.jar", b"jar"),
                (
                    "tmp/releases/2.0.0/" + "a" * 40 + "/lexiflow-extension-2.0.0.zip",
                    b"zip",
                ),
            ):
                file = repo / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(data)
            sql = repo / "infra/postgres/schema.sql"
            sql.parent.mkdir(parents=True, exist_ok=True)
            sql.write_bytes(b"schema")
            for name in ("artifacts", "home"):
                (scratch / name).mkdir()
            rows = [
                {
                    "file": name,
                    "bytes": 3,
                    "sha256": str(i) * 64,
                    "datasetVersion": i,
                    "preparationPolicy": "lexiflow.deterministic-preparation.v1",
                }
                for i, name in enumerate(("dataset-a.zip", "dataset-b.zip"), 1)
            ]
            built = {
                "home": scratch / "home",
                "artifact_root": scratch / "artifacts",
                "producer": {"datasets": rows},
            }
            binding = SimpleNamespace(endpoint="unix:///synthetic.sock")

            def bridge(argv, cwd, env, timeout, *, input_stream):
                self.assertEqual(
                    argv,
                    [
                        "node",
                        str(
                            repo
                            / "ops/release/test_tools/build-synthetic-release-candidates.mjs"
                        ),
                    ],
                )
                self.assertEqual(
                    json.load(input_stream),
                    {
                        "scratchDirectory": str(scratch),
                        "endpoint": binding.endpoint,
                        "datasets": rows,
                    },
                )
                parent = root if foreign else scratch / "synthetic-releases"
                parent.mkdir(exist_ok=True)
                candidates = []
                marker = json.dumps(
                    {
                        "kind": "lexiflow-release-candidate",
                        "softwareVersion": "2.0.0",
                        "sourceCommit": "a" * 40,
                        "buildIdentity": {
                            "softwareVersion": "2.0.0",
                            "sourceCommit": "a" * 40,
                            "dirty": False,
                        },
                    }
                ).encode()
                for i in range(2):
                    target = parent / f"candidate-{i}"
                    target.mkdir()
                    (target / "candidate.json").write_bytes(
                        marker + (b" " if corrupt else b"")
                    )
                    candidates.append(
                        {
                            "candidateDirectory": str(target),
                            "candidateSha256": hashlib.sha256(marker).hexdigest(),
                        }
                    )
                return json.dumps(
                    {
                        "candidates": candidates,
                        "datasets": [
                            {"version": row["datasetVersion"], "sha256": row["sha256"]}
                            for row in rows
                        ],
                        "softwareVersion": "2.0.0",
                        "apiContract": "caption-hints.v2",
                    }
                ).encode(), b""

            with (
                patch.object(artifacts, "build_local_inputs", return_value=built),
                patch.object(
                    artifacts, "_source_identity", return_value=("2.0.0", "a" * 40)
                ),
                patch(
                    "ops.release.host_resources.release_docker_preflight.recheck",
                    return_value=SimpleNamespace(status="PASS"),
                ),
                patch(
                    "scripts.checks.release.source.check_synthetic_release_lifecycle._run_bounded",
                    side_effect=bridge,
                ) as run,
            ):
                result = artifacts.produce_candidates(repo, scratch, {}, binding)
            run.assert_called_once()
            self.assertIs(result["formalReleaseEligible"], False)
            self.assertEqual(len(result["releaseCandidates"]), 2)
