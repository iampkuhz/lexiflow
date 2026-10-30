"""GHCR 发布与安全边界的直接合同测试。"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.release.workflow import release_registry as registry
from ops.release.workflow.release_registry import (
    RegistryError,
    publish_images,
    verify_images,
)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.identity = {
            "schemaVersion": 1,
            "baseVersion": "2.0.0",
            "softwareVersion": "2.0.0",
            "chromeVersion": "2.0.0.1",
            "sourceCommit": "a" * 40,
            "sourceSha256": "b" * 64,
            "buildId": "c" * 64,
            "dirty": False,
            "channel": "release",
        }
        self.artifacts = []
        for role, path, iid in (
            ("runtime-image", "images/linux-arm64/runtime-image.tar", "d"),
        ):
            data = (role + " bytes").encode()
            target = self.root / "payload" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            self.artifacts.append(
                {
                    "role": role,
                    "path": path,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "platform": "linux/arm64",
                    "imageDigest": "sha256:" + iid * 64,
                }
            )
        self.manifest = {
            "buildIdentity": self.identity,
            "softwareVersion": "2.0.0",
            "sourceCommit": self.identity["sourceCommit"],
            "platforms": ["linux/arm64"],
            "artifacts": self.artifacts,
        }
        manifest_raw = json.dumps(self.manifest, separators=(",", ":")).encode()
        (self.root / "payload" / "manifest.json").write_bytes(manifest_raw)
        self.candidate_meta = {
            "buildIdentity": self.identity,
            "softwareVersion": "2.0.0",
            "sourceCommit": self.identity["sourceCommit"],
            "imageCandidates": [{"platform": "linux/arm64"}],
        }
        candidate_raw = json.dumps(self.candidate_meta, separators=(",", ":")).encode()
        (self.root / "candidate.json").write_bytes(candidate_raw)
        self.candidate_sha = hashlib.sha256(candidate_raw).hexdigest()
        self.manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock_path = str(self.root / "podman.sock")
        self.sock.bind(self.sock_path)

    def tearDown(self):
        self.sock.close()
        self.tmp.cleanup()

    def kwargs(self, **changes):
        args = {
            "candidate": self.root,
            "manifest": self.manifest,
            "identity": self.identity,
            "owner": "Example",
            "repo": "LexiFlow",
            "endpoint": "unix://" + self.sock_path,
            "candidate_sha256": self.candidate_sha,
            "manifest_sha256": self.manifest_sha,
            "revalidate": lambda: None,
        }
        args.update(changes)
        return args

    def test_raw_manifest_and_candidate_hashes_required(self):
        with self.assertRaises(RegistryError):
            publish_images(**self.kwargs(manifest_sha256="f" * 64))

    def test_parent_symlink_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "manifest.json").write_text("{}")
        (self.root / "payload").rename(self.root / "payload-real")
        (self.root / "payload").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RegistryError) as exc:
            publish_images(**self.kwargs())
        self.assertEqual(exc.exception.reason, "registry-candidate-invalid")

    def test_rejects_tcp_remote_endpoint(self):
        with self.assertRaises(RegistryError) as exc:
            publish_images(**self.kwargs(endpoint="tcp://127.0.0.1:1234"))
        self.assertEqual(exc.exception.reason, "registry-endpoint-invalid")

    def _publish_mocks(self, push_error=False, revalidate=None):
        body = json.dumps(
            {"schemaVersion": 2, "config": {"digest": self.artifacts[0]["imageDigest"]}}
        ).encode()
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        published = set()
        calls = []

        def fake_manifest(repo, reference, **kwargs):
            role = "runtime-image"
            if reference == "2.0.0" and role not in published:
                return 404, {}, b""
            item = next(x for x in self.artifacts if x["role"] == role)
            item_body = json.dumps(
                {"schemaVersion": 2, "config": {"digest": item["imageDigest"]}}
            ).encode()
            item_digest = "sha256:" + hashlib.sha256(item_body).hexdigest()
            if reference.startswith("sha256:") or reference == "2.0.0":
                return 200, {"docker-content-digest": item_digest}, item_body
            return 404, {}, b""

        def fake_run(argv, *, endpoint, input_stream=None, timeout=300):
            calls.append(argv)

            class Facts:
                returncode = 0
                cleanup_succeeded = True
                stdout = b""

            if "inspect" in argv:
                role = "runtime-image"
                ident = self.identity
                labels = {
                    "org.opencontainers.image.version": ident["softwareVersion"],
                    "org.opencontainers.image.revision": ident["sourceCommit"],
                    "io.lexiflow.build-id": ident["buildId"],
                    "io.lexiflow.source-sha256": ident["sourceSha256"],
                    "io.lexiflow.image-role": "runtime",
                    "io.lexiflow.deployment-version": ident["baseVersion"],
                }
                Facts.stdout = json.dumps(
                    [
                        {
                            "Id": argv[-1].removeprefix("sha256:"),
                            "Architecture": "arm64",
                            "Os": "linux",
                            "Config": {"Labels": labels},
                        }
                    ]
                ).encode()
            if "push" in argv:
                auth_path = Path(
                    next(
                        x.split("=", 1)[1] for x in argv if x.startswith("--authfile=")
                    )
                )
                self.assertEqual(auth_path.stat().st_mode & 0o777, 0o600)
                encoded = json.loads(auth_path.read_text())["auths"]["ghcr.io"]["auth"]
                self.assertEqual(
                    base64.b64decode(encoded).decode(), "example:test-secret"
                )
                if push_error:
                    raise RegistryError("registry-process-failed", "podman")
                role = "runtime-image"
                published.add(role)
                item = next(x for x in self.artifacts if x["role"] == role)
                body = json.dumps(
                    {"schemaVersion": 2, "config": {"digest": item["imageDigest"]}}
                ).encode()
                item_digest = "sha256:" + hashlib.sha256(body).hexdigest()
                digest_arg = next(
                    x.split("=", 1)[1] for x in argv if x.startswith("--digestfile=")
                )
                Path(digest_arg).write_text(item_digest)
            return Facts()

        return body, digest, published, calls, fake_manifest, fake_run

    @patch.dict("os.environ", {"GITHUB_TOKEN": "test-secret"})
    def test_complete_push_flow_fresh_gates_and_no_token_in_argv(self):
        _, _, _, calls, fake_manifest, fake_run = self._publish_mocks()
        gate_calls = []

        def gate():
            gate_calls.append(1)

        with (
            patch.object(registry, "_manifest", side_effect=fake_manifest),
            patch.object(registry, "_run", side_effect=fake_run),
        ):
            result = publish_images(**self.kwargs(revalidate=gate))
        self.assertEqual(len(result["images"]), 1)
        self.assertEqual(len(gate_calls), 3)
        self.assertTrue(all("test-secret" not in " ".join(c) for c in calls))
        self.assertEqual(sum("push" in c for c in calls), 1)

    def test_inspect_accepts_only_exact_config_identity_and_platform(self):
        _, _, _, _, _, fake_run = self._publish_mocks()
        image_id = self.artifacts[0]["imageDigest"]
        original = json.loads(fake_run(["inspect", image_id], endpoint="").stdout)[0]
        for value in [image_id, image_id.removeprefix("sha256:")]:
            with self.subTest(accepted=value), patch.object(registry, "_run") as run:
                run.return_value.stdout = json.dumps(
                    [{**original, "Id": value}]
                ).encode()
                registry._inspect_image(
                    image_id, "runtime-image", self.identity, "unix:///unused"
                )
        for value in [
            None,
            1,
            [],
            "",
            "d" * 12,
            "d" * 63,
            "D" * 64,
            "f" * 64,
            "sha256:" + "f" * 64,
            "sha256:sha256:" + "d" * 64,
            "sha512:" + "d" * 64,
            " " + "d" * 64,
            "d" * 64 + "\n",
        ]:
            with self.subTest(rejected=value), patch.object(registry, "_run") as run:
                run.return_value.stdout = json.dumps(
                    [{**original, "Id": value}]
                ).encode()
                with self.assertRaisesRegex(
                    RegistryError, "registry-image-inspect-mismatch"
                ):
                    registry._inspect_image(
                        image_id, "runtime-image", self.identity, "unix:///unused"
                    )
        for field, value in [("Architecture", "amd64"), ("Os", "windows")]:
            with self.subTest(field=field), patch.object(registry, "_run") as run:
                run.return_value.stdout = json.dumps(
                    [{**original, field: value}]
                ).encode()
                with self.assertRaisesRegex(
                    RegistryError, "registry-image-inspect-mismatch"
                ):
                    registry._inspect_image(
                        image_id, "runtime-image", self.identity, "unix:///unused"
                    )
        for label in original["Config"]["Labels"]:
            changed = {
                **original,
                "Config": {"Labels": {**original["Config"]["Labels"], label: "wrong"}},
            }
            with self.subTest(label=label), patch.object(registry, "_run") as run:
                run.return_value.stdout = json.dumps([changed]).encode()
                with self.assertRaisesRegex(
                    RegistryError, "registry-image-label-mismatch"
                ):
                    registry._inspect_image(
                        image_id, "runtime-image", self.identity, "unix:///unused"
                    )

    @patch.dict("os.environ", {"GITHUB_TOKEN": "test-secret"})
    def test_inspect_mismatch_rejects_before_first_remote_push(self):
        _, _, _, calls, fake_manifest, fake_run = self._publish_mocks()

        def wrong_id(argv, **kwargs):
            facts = fake_run(argv, **kwargs)
            if "inspect" in argv:
                rows = json.loads(facts.stdout)
                rows[0]["Id"] = "f" * 64
                facts.stdout = json.dumps(rows).encode()
            return facts

        with (
            patch.object(registry, "_manifest", side_effect=fake_manifest),
            patch.object(registry, "_run", side_effect=wrong_id),
            self.assertRaisesRegex(RegistryError, "registry-image-inspect-mismatch"),
        ):
            publish_images(**self.kwargs())
        self.assertFalse(any("push" in call for call in calls))

    @patch.dict("os.environ", {"GITHUB_TOKEN": "test-secret"})
    def test_unknown_push_is_flagged_and_not_retried(self):
        _, _, _, calls, fake_manifest, fake_run = self._publish_mocks(push_error=True)
        with (
            patch.object(registry, "_manifest", side_effect=fake_manifest),
            patch.object(registry, "_run", side_effect=fake_run),
            self.assertRaises(RegistryError) as exc,
        ):
            publish_images(**self.kwargs())
        self.assertTrue(exc.exception.write_unknown)
        self.assertEqual(exc.exception.reason, "registry-push-unknown")
        self.assertEqual(sum("push" in c for c in calls), 1)

    def test_private_temp_cleanup_failure_is_sanitized_and_unknown(self):
        private = registry._PrivateDirectory("runtime-image")
        private.path = str(self.root / "private")
        Path(private.path).mkdir(mode=0o700)
        private.write_started = True
        with (
            patch.object(registry.shutil, "rmtree", side_effect=OSError("token-value")),
            self.assertRaises(RegistryError) as exc,
        ):
            private.__exit__(None, None, None)
        self.assertEqual(exc.exception.reason, "registry-cleanup-failed")
        self.assertEqual(exc.exception.role, "runtime-image")
        self.assertTrue(exc.exception.write_unknown)
        self.assertNotIn("token-value", str(exc.exception))

    @patch.dict("os.environ", {"GITHUB_TOKEN": "test-secret"})
    def test_matching_existing_tags_are_read_only_reused(self):
        def existing(repo, reference, **kwargs):
            role = "runtime-image"
            item = next(x for x in self.artifacts if x["role"] == role)
            body = json.dumps({"config": {"digest": item["imageDigest"]}}).encode()
            return (
                200,
                {"docker-content-digest": "sha256:" + hashlib.sha256(body).hexdigest()},
                body,
            )

        verified = []
        api = self.artifacts[0]
        for item in (api, api):
            body = json.dumps({"config": {"digest": item["imageDigest"]}}).encode()
            verified.append(
                ("sha256:" + hashlib.sha256(body).hexdigest(), item["imageDigest"])
            )
        with (
            patch.object(registry, "_manifest", side_effect=existing),
            patch.object(registry, "_run") as runner,
            patch.object(registry, "_image_manifest", side_effect=verified),
        ):
            result = publish_images(**self.kwargs())
        self.assertEqual(len(result["images"]), 1)
        runner.assert_not_called()

    @patch.dict("os.environ", {"GITHUB_TOKEN": "test-secret"})
    def test_preexisting_tag_mismatch_never_pushes(self):
        def existing(repo, reference, **kwargs):
            body = json.dumps({"config": {"digest": "sha256:" + "f" * 64}}).encode()
            return (
                200,
                {"docker-content-digest": "sha256:" + hashlib.sha256(body).hexdigest()},
                body,
            )

        with (
            patch.object(registry, "_manifest", side_effect=existing),
            patch.object(registry, "_run") as runner,
            self.assertRaises(RegistryError) as exc,
        ):
            publish_images(**self.kwargs())
        self.assertEqual(exc.exception.reason, "registry-tag-mismatch")
        runner.assert_not_called()

    @patch.dict("os.environ", {"GITHUB_TOKEN": "test-secret"})
    def test_confirmed_recovery_never_pushes_when_tag_is_absent(self):
        with (
            patch.object(registry, "_manifest", return_value=(404, {}, b"")),
            patch.object(registry, "_run") as runner,
            self.assertRaises(RegistryError) as exc,
        ):
            publish_images(**self.kwargs(existing_only=True))
        self.assertEqual(exc.exception.reason, "registry-recovery-target-not-confirmed")
        self.assertFalse(exc.exception.write_unknown)
        runner.assert_not_called()

    @patch.dict("os.environ", {"GITHUB_TOKEN": "test-secret"})
    def test_fresh_gate_failure_prevents_any_podman_action(self):
        _, _, _, _, fake_manifest, fake_run = self._publish_mocks()

        def gate():
            raise RuntimeError("sensitive context")

        with (
            patch.object(registry, "_manifest", side_effect=fake_manifest),
            patch.object(registry, "_run", side_effect=fake_run) as runner,
            self.assertRaises(RegistryError) as exc,
        ):
            publish_images(**self.kwargs(revalidate=gate))
        self.assertEqual(exc.exception.reason, "registry-operation-failed")
        runner.assert_not_called()

    @patch("ops.release.workflow.release_registry.time.sleep")
    @patch.dict("os.environ", {"GITHUB_TOKEN": "test-secret"})
    def test_anonymous_denial_after_push_is_unknown_and_retained(self, sleep):
        _, _, _, _, fake_manifest, fake_run = self._publish_mocks()

        def private_after_push(repo, reference, **kwargs):
            if kwargs.get("anonymous") and reference.startswith("sha256:"):
                return 403, {}, b"private"
            return fake_manifest(repo, reference, **kwargs)

        with (
            patch.object(registry, "_manifest", side_effect=private_after_push),
            patch.object(registry, "_run", side_effect=fake_run),
            self.assertRaises(RegistryError) as exc,
        ):
            publish_images(**self.kwargs())
        self.assertEqual(exc.exception.reason, "registry-postwrite-verify-unknown")
        self.assertTrue(exc.exception.write_unknown)

    def test_first_public_visibility_window_is_read_only_and_revalidates(self):
        digest, image = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        events = []

        def gate():
            events.append("gate")

        with (
            patch.object(
                registry,
                "_image_manifest",
                side_effect=[RegistryError("registry-not-public"), (digest, image)],
            ) as read,
            patch.object(
                registry.time,
                "sleep",
                side_effect=lambda seconds: events.append(seconds),
            ),
            patch.object(registry, "_run") as write,
        ):
            self.assertEqual(
                registry._first_public_image("owner/lexiflow", digest, gate),
                (digest, image),
            )
        self.assertEqual(events, ["gate", 120, "gate"])
        self.assertEqual(read.call_count, 2)
        write.assert_not_called()

    def test_public_image_does_not_wait(self):
        with (
            patch.object(
                registry, "_image_manifest", return_value=("digest", "config")
            ),
            patch.object(registry.time, "sleep") as sleep,
        ):
            self.assertEqual(
                registry._first_public_image("owner/lexiflow", "digest", lambda: None),
                ("digest", "config"),
            )
        sleep.assert_not_called()

    def test_first_public_window_refuses_unknown_network_and_gate_drift(self):
        for reason in [
            "registry-network-unknown",
            "registry-manifest-digest-invalid",
            "registry-tag-unavailable",
        ]:
            with (
                self.subTest(reason=reason),
                patch.object(
                    registry, "_image_manifest", side_effect=RegistryError(reason)
                ),
                patch.object(registry.time, "sleep") as sleep,
                self.assertRaises(RegistryError),
            ):
                registry._first_public_image("owner/lexiflow", "digest", lambda: None)
            sleep.assert_not_called()

        def drift():
            raise RegistryError("candidate-drift")

        with (
            patch.object(
                registry,
                "_image_manifest",
                side_effect=RegistryError("registry-not-public"),
            ) as read,
            patch.object(registry.time, "sleep") as sleep,
            self.assertRaises(RegistryError) as exc,
        ):
            registry._first_public_image("owner/lexiflow", "digest", drift)
        self.assertEqual(exc.exception.reason, "candidate-drift")
        self.assertEqual(read.call_count, 1)
        sleep.assert_not_called()

    @patch.dict("os.environ", {"GITHUB_TOKEN": "test-secret"})
    def test_missing_digestfile_is_unknown_after_push(self):
        _, _, _, _calls, fake_manifest, fake_run = self._publish_mocks()

        def no_digest(argv, **kwargs):
            facts = fake_run(argv, **kwargs)
            if "push" in argv:
                Path(
                    next(
                        x.split("=", 1)[1]
                        for x in argv
                        if x.startswith("--digestfile=")
                    )
                ).unlink()
            return facts

        with (
            patch.object(registry, "_manifest", side_effect=fake_manifest),
            patch.object(registry, "_run", side_effect=no_digest),
            self.assertRaises(RegistryError) as exc,
        ):
            publish_images(**self.kwargs())
        self.assertTrue(exc.exception.write_unknown)
        self.assertEqual(exc.exception.reason, "registry-push-digest-mismatch")

    def test_authenticated_challenge_uses_basic_only_for_token_exchange(self):
        challenge = 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:example/repo:pull"'
        sequence = [
            (401, {"www-authenticate": challenge}, b""),
            (200, {}, b'{"token":"private"}'),
            (200, {}, b"ok"),
        ]
        seen = []

        def fake(method, path, headers):
            seen.append((path, headers))
            return sequence.pop(0)

        with patch.object(registry, "_https", side_effect=fake):
            status, _, _ = registry._manifest(
                "example/repo", "tag", owner="example", token="secret", anonymous=False
            )
        self.assertEqual(status, 200)
        self.assertNotIn("Authorization", seen[0][1])
        self.assertTrue(seen[1][1]["Authorization"].startswith("Basic "))
        self.assertTrue(seen[2][1]["Authorization"].startswith("Bearer "))
        self.assertNotIn("secret", repr(seen))

    def test_anonymous_challenge_never_sends_basic_credentials(self):
        challenge = 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:example/repo:pull"'
        sequence = [
            (401, {"www-authenticate": challenge}, b""),
            (200, {}, b'{"token":"anon"}'),
            (403, {}, b""),
        ]
        seen = []

        def fake(method, path, headers):
            seen.append(headers)
            return sequence.pop(0)

        with patch.object(registry, "_https", side_effect=fake):
            status, _, _ = registry._manifest("example/repo", "tag", anonymous=True)
        self.assertEqual(status, 403)
        self.assertTrue(all("Authorization" not in h for h in seen[:2]))

    def test_anonymous_403_is_not_misclassified_as_missing_tag(self):
        with (
            patch.object(registry, "_manifest", return_value=(403, {}, b"")),
            self.assertRaises(RegistryError) as exc,
        ):
            registry._image_manifest("example/repo", "tag", anonymous=True)
        self.assertEqual(exc.exception.reason, "registry-not-public")

    def test_verify_images_reads_only_anonymous_digest(self):
        body = b'{"config":{"digest":"sha256:' + b"a" * 64 + b"}}"
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        item_list = [
            {
                "role": "runtime-image",
                "platform": "linux/arm64",
                "reference": "ghcr.io/example/lexiflow:2.0.0",
                "manifestDigest": digest,
                "imageId": "sha256:" + "d" * 64,
                "archiveSha256": "f" * 64,
            }
        ]
        mapping = {
            "schemaVersion": "lexiflow.registry-images.v1",
            "softwareVersion": "2.0.0",
            "sourceCommit": "a" * 40,
            "candidateSha256": "b" * 64,
            "manifestSha256": "c" * 64,
            "images": item_list,
        }
        with patch.object(
            registry,
            "_image_manifest",
            side_effect=[(digest, item_list[0]["imageId"])],
        ) as reader:
            self.assertIs(
                verify_images(
                    mapping, owner="Example", repo="LexiFlow", expected=mapping
                ),
                mapping,
            )
        self.assertEqual(reader.call_count, 1)


if __name__ == "__main__":
    unittest.main()
