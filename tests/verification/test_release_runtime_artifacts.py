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
