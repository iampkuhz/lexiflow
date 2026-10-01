"""产品生命周期驱动在无Docker副作用下的完整阶段回归。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.environment.release_runtime_check import ConsumerError
from scripts.environment import release_runtime_lifecycle as lifecycle


class TestReleaseRuntimeLifecycle(unittest.TestCase):
    def _release(
        self, root: Path, name: str, key: str, platform: str
    ) -> dict[str, str]:
        directory = root / name
        payload = directory / "payload"
        payload.mkdir(parents=True)
        entry = payload / "lexiflow.sh"
        entry.write_text(f"  LF_RELEASE_KEY='{key}'\n", encoding="ascii")
        api = "sha256:" + "a" * 64
        db = "sha256:" + "b" * 64
        manifest = {
            "artifacts": [
                item
                for target in (platform,)
                for item in (
                    {"role": "api-image", "platform": target, "imageDigest": api},
                    {"role": "postgres-image", "platform": target, "imageDigest": db},
                )
            ]
        }
        (payload / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        marker = directory / "candidate.json"
        marker.write_text(
            json.dumps(
                {
                    "kind": "lexiflow-release-candidate",
                    "manifestPath": "payload/manifest.json",
                }
            ),
            encoding="utf-8",
        )
        return {
            "candidateDirectory": str(directory),
            "candidateSha256": hashlib.sha256(marker.read_bytes()).hexdigest(),
        }

    def test_arm64_executes_two_generations_recovery_stop_and_delete(self):
        with tempfile.TemporaryDirectory(prefix="runtime-lifecycle-test-") as tmp:
            root = Path(tmp)
            datasets = [
                {"version": 1, "sha256": "1" * 64},
                {"version": 2, "sha256": "2" * 64},
            ]
            candidates = []
            bindings = []
            for index, platform in enumerate(("linux/arm64",)):
                candidate_dir = root / f"candidate-{index}"
                candidate_dir.mkdir()
                if index == 0:
                    candidates = [
                        self._release(candidate_dir, "release-a", "a" * 64, platform),
                        self._release(candidate_dir, "release-b", "b" * 64, platform),
                    ]
                bindings.append(
                    SimpleNamespace(
                        endpoint=f"unix:///tmp/{index}.sock",
                        daemon_id=f"daemon-{index}",
                        target_platform=platform,
                    )
                )
            state = ["idle", "none", "none", "none"]
            commands: list[tuple[str, ...]] = []
            install_roots: list[Path] = []

            def fake_run(argv, cwd, env, timeout=180):
                args = tuple(argv[2:])
                commands.append(args)
                command = args[0]
                entry = Path(argv[1])
                key = "a" * 64 if "release-a" in str(entry) else "b" * 64
                if command == "prepare":
                    installation = Path(args[1])
                    installation.mkdir(mode=0o700, exist_ok=True)
                    if state[0] == "idle" and state[1] == "none":
                        state[:] = ["prepared", "none", "none", key]
                    else:
                        state[:] = ["prepared", state[1], "none", key]
                    if installation not in install_roots:
                        install_roots.append(installation)
                elif command == "activate":
                    if state[1] == "none":
                        state[:] = ["idle", key, "none", "none"]
                    else:
                        state[:] = ["idle", key, state[1], "none"]
                elif command == "recover":
                    state[:] = ["idle", "a" * 64, "b" * 64, "none"]
                elif command == "stop":
                    state[:] = ["stopped", state[1], state[2], state[3]]
                elif command == "delete":
                    if args[2] == "b" * 64:
                        state[:] = ["stopped", state[1], "none", state[3]]
                    else:
                        state[:] = ["stopped", "none", "none", "none"]
                        installation = Path(args[1])
                        owner = "a" * 32
                        (installation / "owner").write_text(
                            owner + "\n", encoding="ascii"
                        )
                        daemon_id = (
                            "daemon-0"
                            if env["DOCKER_HOST"].endswith("0.sock")
                            else "daemon-1"
                        )
                        (installation / "engine").write_text(
                            "schema=lexiflow-engine-v1\n"
                            f"endpoint={env['DOCKER_HOST']}\n"
                            f"daemon_id={daemon_id}\n",
                            encoding="ascii",
                        )
                        (installation / "state").write_text(
                            "schema=lexiflow-installation-v1\n"
                            f"installation_id={owner}\nphase=stopped\nactive=none\n"
                            "previous=none\ncandidate=none\ndeleting=none\nresume=none\n",
                            encoding="ascii",
                        )
                        (installation / "releases").mkdir(exist_ok=True)
                return b""

            def fake_status(entry, install_root, env):
                return tuple(state)

            def fake_probe(
                binding, config, install_root, key, version, dataset, api, postgres, env
            ):
                return {
                    "runtime": key,
                    "datasetVersion": dataset,
                    "softwareVersion": version,
                    "images": [api, postgres],
                    "daemon": binding.daemon_id,
                }

            evidence = {
                "status": "PASS",
                "releaseCandidates": candidates,
                "datasetRecords": datasets,
                "softwareVersion": "2.0.0",
            }
            with (
                patch.object(lifecycle, "_run", side_effect=fake_run),
                patch.object(lifecycle, "_status", side_effect=fake_status),
                patch.object(lifecycle, "_assert_runtime", side_effect=fake_probe),
                patch(
                    "scripts.environment.release_docker_preflight.recheck",
                    return_value=SimpleNamespace(status="PASS"),
                ),
            ):
                result = lifecycle.execute_lifecycle(
                    root, root, evidence, tuple(bindings), {"PATH": "/usr/bin:/bin"}
                )
            self.assertEqual(len(result["platforms"]), 1)
            self.assertEqual(result["platforms"][0]["platform"], "linux/arm64")
            self.assertEqual(result["lifecycleChecks"], 9)
            self.assertEqual(
                [item["daemonId"] for item in result["platforms"]],
                ["daemon-0"],
            )
            for item in result["platforms"]:
                self.assertEqual(item["firstHealth"]["datasetVersion"], 1)
                self.assertEqual(item["secondHealth"]["datasetVersion"], 2)
                self.assertEqual(item["recoveredHealth"]["datasetVersion"], 1)
            self.assertEqual(sum(command[0] == "verify" for command in commands), 2)
            self.assertEqual(sum(command[0] == "prepare" for command in commands), 2)
            self.assertEqual(sum(command[0] == "activate" for command in commands), 2)
            self.assertEqual(sum(command[0] == "recover" for command in commands), 1)
            self.assertEqual(sum(command[0] == "stop" for command in commands), 1)
            self.assertEqual(sum(command[0] == "delete" for command in commands), 2)

    def test_owned_root_cleanup_requires_real_product_delete_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "installation"
            root.mkdir()
            binding = SimpleNamespace(
                endpoint="unix:///tmp/runtime.sock", daemon_id="daemon-123"
            )
            owner = "a" * 32
            (root / "owner").write_text(owner + "\n", encoding="ascii")
            (root / "engine").write_text(
                "schema=lexiflow-engine-v1\nendpoint=unix:///tmp/runtime.sock\ndaemon_id=daemon-123\n",
                encoding="ascii",
            )
            (root / "state").write_text(
                "schema=lexiflow-installation-v1\n"
                f"installation_id={owner}\nphase=stopped\nactive=none\n"
                "previous=none\ncandidate=none\ndeleting=none\nresume=none\n",
                encoding="ascii",
            )
            (root / "releases").mkdir()
            lifecycle._remove_owned_installation_root(root, binding)
            self.assertFalse(root.exists())

    def test_owned_root_cleanup_preserves_unknown_or_nonempty_product_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "installation"
            root.mkdir()
            binding = SimpleNamespace(
                endpoint="unix:///tmp/runtime.sock", daemon_id="daemon-123"
            )
            owner = "a" * 32
            (root / "owner").write_text(owner + "\n", encoding="ascii")
            (root / "engine").write_text(
                "schema=lexiflow-engine-v1\nendpoint=unix:///tmp/runtime.sock\ndaemon_id=daemon-123\n",
                encoding="ascii",
            )
            (root / "state").write_text(
                "schema=lexiflow-installation-v1\n"
                f"installation_id={owner}\nphase=stopped\nactive=none\n"
                "previous=none\ncandidate=none\ndeleting=none\nresume=none\n",
                encoding="ascii",
            )
            releases = root / "releases"
            releases.mkdir()
            (releases / "unexpected").write_text("retain", encoding="ascii")
            with self.assertRaisesRegex(
                ConsumerError, "release-installation-cleanup-failed"
            ):
                lifecycle._remove_owned_installation_root(root, binding)
            self.assertTrue((releases / "unexpected").exists())

    def test_real_product_entry_status_and_delete_cleanup_contract_fixture(self):
        """通过实际生成的发行入口核对status及delete保留结构。"""
        with tempfile.TemporaryDirectory(prefix="release-entry-product-test-") as tmp:
            root = Path(tmp).resolve()
            fixture_script = root / "exercise.mjs"
            fixture_script.write_text(
                "import { lifecycleFixture } from "
                + json.dumps(
                    (
                        Path(__file__).resolve().parents[2]
                        / "ops/release/tests/lifecycle-fixture.mjs"
                    ).as_uri()
                )
                + ";\n"
                + "const t={after:fn=>{}};\n"
                + "try {\n"
                + " const f=await lifecycleFixture(t,true);\n"
                + " const installRoot=f.tmp+'/consumer installation';\n"
                + " f.run('prepare',installRoot);\n"
                + " const prepared=f.run('status',installRoot).trim();\n"
                + " const key=prepared.split(' ')[3];\n"
                + " f.run('activate',installRoot);\n"
                + " f.run('stop',installRoot);\n"
                + " f.run('delete',installRoot,key,'--confirm-delete-data');\n"
                + " console.log(JSON.stringify({entry:f.pkg+'/lexiflow.sh',installRoot,fixtureTmp:f.tmp,"
                + "endpoint:'unix:///lexiflow-synthetic-docker.sock',daemonId:'synthetic-daemon-id',status:f.run('status',installRoot).trim()}));\n"
                + "} finally {}\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["TMPDIR"] = str(root)
            completed = subprocess.run(
                ["node", str(fixture_script)],
                cwd=Path(__file__).resolve().parents[2],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
                check=True,
            )
            product = json.loads(completed.stdout.decode("utf-8").strip())
            entry = Path(product["entry"])
            install_root = Path(product["installRoot"])
            fixture_tmp = Path(product["fixtureTmp"])
            self.assertEqual(fixture_tmp.parent, root)
            binding = SimpleNamespace(
                endpoint=product["endpoint"], daemon_id=product["daemonId"]
            )
            self.assertEqual(
                lifecycle._status(entry, install_root, {"PATH": env.get("PATH", "")}),
                ("stopped", "none", "none", "none"),
            )
            lifecycle._remove_owned_installation_root(install_root, binding)
            self.assertFalse(install_root.exists())
            shutil.rmtree(fixture_tmp)

    def test_missing_candidates_rejected(self):
        with self.assertRaisesRegex(ConsumerError, "release-candidate-not-assembled"):
            lifecycle.execute_lifecycle(None, None, {}, (), {})

    def test_postgres_unhealthy_is_not_accepted_when_api_is_healthy(self):
        binding = SimpleNamespace(
            endpoint="unix:///tmp/runtime.sock", daemon_id="daemon-1"
        )
        owner = "a" * 32
        release_key = "b" * 64
        api_id = "1" * 64
        postgres_id = "2" * 64
        api_image = "sha256:" + "c" * 64
        postgres_image = "sha256:" + "d" * 64

        def inspect(container_id, name, image_id, status, health):
            return {
                "Id": container_id,
                "Name": "/project-" + name + "-1",
                "Image": image_id,
                "Config": {
                    "Image": image_id,
                    "Labels": {
                        "lexiflow.installation": owner,
                        "lexiflow.release": release_key,
                        "com.docker.compose.project": f"lf_{owner}_{release_key[:16]}",
                    },
                },
                "State": {"Status": status, "Health": {"Status": health}},
            }

        responses = iter(
            [
                (api_id + "\n" + postgres_id + "\n").encode(),
                json.dumps(
                    [inspect(api_id, "api", api_image, "running", "healthy")]
                ).encode(),
                json.dumps(
                    [
                        inspect(
                            postgres_id,
                            "postgres",
                            postgres_image,
                            "exited",
                            "unhealthy",
                        )
                    ]
                ).encode(),
            ]
        )
        with (
            tempfile.TemporaryDirectory(prefix="runtime-pg-health-") as tmp,
            patch.object(
                lifecycle, "_docker", side_effect=lambda *args: next(responses)
            ),
        ):
            installation = Path(tmp) / "installation"
            installation.mkdir()
            (installation / "owner").write_text(owner + "\n", encoding="ascii")
            with self.assertRaisesRegex(
                ConsumerError, "release-postgres-container-unhealthy"
            ):
                lifecycle._assert_runtime(
                    binding,
                    Path("/tmp/docker-config"),
                    installation,
                    release_key,
                    "2.0.0",
                    1,
                    api_image,
                    postgres_image,
                    {"PATH": "/usr/bin:/bin"},
                )

    def test_release_key_parser_rejects_ambiguous_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = Path(tmp) / "entry"
            entry.write_text("  LF_RELEASE_KEY='bad'\n", encoding="ascii")
            with self.assertRaisesRegex(
                ConsumerError, "release-entry-identity-invalid"
            ):
                lifecycle._release_key(entry)
