"""正式候选晋升消费者的合成端到端边界测试。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.release.workflow import promote_verified_release as p

SUBMISSION = "00000000-0000-0000-0000-000000000001"
COMMIT = "a" * 40


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        (root / "LICENSE").write_bytes(b"MIT\n")
        self.candidate = root / "candidate"
        self.payload = self.candidate / "payload"
        self.payload.mkdir(parents=True)
        self.identity = {
            "schemaVersion": 1,
            "baseVersion": "1.2.3",
            "softwareVersion": "1.2.3",
            "chromeVersion": "1.2.3.1",
            "sourceCommit": COMMIT,
            "sourceSha256": "b" * 64,
            "buildId": "c" * 64,
            "dirty": False,
            "channel": "release",
        }
        self.license = {
            "id": "lexiflow",
            "component": "LexiFlow",
            "licenseId": "MIT",
            "licenseName": "MIT",
            "sourceUrl": "https://example.test/license",
            "noticePath": "LICENSE",
            "noticeBytes": 4,
            "noticeSha256": sha(b"MIT\n"),
        }
        self.artifacts = []
        for role, name, data in (
            ("runtime-entry", "lexiflow.sh", b"#!/bin/sh\n"),
            ("license", "LICENSE", b"MIT\n"),
            ("extension", "extension/package.zip", b"accepted extension zip bytes"),
            ("api-image", "images/api.tar", b"image bytes"),
            ("postgres-image", "images/postgres.tar", b"postgres image bytes"),
        ):
            path = self.payload / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.artifacts.append(
                {"role": role, "path": name, "bytes": len(data), "sha256": sha(data)}
            )
            if role.endswith("-image"):
                self.artifacts[-1].update(
                    platform="linux/arm64", imageDigest="sha256:" + sha(data)
                )
        self.manifest = {
            "buildIdentity": self.identity,
            "softwareVersion": "1.2.3",
            "sourceCommit": COMMIT,
            "platforms": ["linux/arm64"],
            "licenses": [self.license],
            "artifacts": self.artifacts,
        }
        self.marker = {
            "buildIdentity": self.identity,
            "softwareVersion": "1.2.3",
            "sourceCommit": COMMIT,
            "imageCandidates": [{"platform": "linux/arm64", "sha256": "d" * 64}],
        }
        self.table = root / "distribution-licenses.json"
        self.table.write_text(
            json.dumps(
                {
                    "schema_version": "lexiflow.distribution-licenses.v1",
                    "licenses": [self.license],
                }
            )
        )
        self.refresh()

    def refresh(self):
        manifest_bytes = json.dumps(self.manifest).encode()
        marker_bytes = json.dumps(self.marker).encode()
        (self.payload / "manifest.json").write_bytes(manifest_bytes)
        (self.payload / "manifest.json.sha256").write_text(
            f"{sha(manifest_bytes)}  manifest.json\n"
        )
        (self.candidate / "candidate.json").write_bytes(marker_bytes)
        self.proof = {
            "chain": {"synthetic": True},
            "candidate": {
                "candidateSha256": sha(marker_bytes),
                "manifestSha256": sha(manifest_bytes),
                "buildIdentity": self.identity,
            },
        }

    def consumer(self, *_args, **_kwargs):
        return {"result": "PASS", "proof": self.proof}


class FakeGitHub:
    calls = []
    archive = None
    extension = None
    registry = None
    fail = None
    list_assets = None
    immutable = True
    existing = False
    tag_commit = COMMIT
    release_id = 51
    assets = []

    def __init__(self, token, owner, repo, host="api.github.com", *, config=False):
        self.token, self.host, self.config = token, host, config
        self.owner, self.repo = owner, repo

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.archive = None
        cls.extension = None
        cls.registry = None
        cls.fail = None
        cls.list_assets = None
        cls.immutable = True
        cls.existing = False
        cls.tag_commit = COMMIT
        cls.assets = []

    def api(self, method, path, body=None):
        self.calls.append((self.host, self.token, method, path, body))
        if self.fail == (method, path):
            raise OSError("lost response")
        if path.endswith("/immutable-releases"):
            return 200, {"enabled": self.immutable}
        if "/git/ref/tags/" in path:
            return 200, {"object": {"type": "commit", "sha": self.tag_commit}}
        if path.startswith("/repos/o/r/releases?per_page"):
            return 200, [{"id": 99, "tag_name": "v1.2.3"}] if self.existing else []
        if path.endswith("/assets?per_page=100&page=1"):
            return (
                200,
                self.list_assets if self.list_assets is not None else self.assets,
            )
        if path.endswith("/releases/latest"):
            return 200, {"id": self.release_id}
        if method == "POST" and path.endswith("/releases"):
            return 201, {
                "id": self.release_id,
                "tag_name": "v1.2.3",
                "draft": True,
                "prerelease": False,
            }
        if method == "PATCH":
            return 200, {"id": self.release_id, "tag_name": "v1.2.3", "draft": False}
        if method == "GET" and path.endswith("/releases/51"):
            published = any(call[2] == "PATCH" for call in self.calls)
            return 200, {
                "id": self.release_id,
                "tag_name": "v1.2.3",
                "draft": not published,
                "prerelease": False,
                "immutable": published,
            }
        raise AssertionError(path)

    def request(self, method, path, *, content_type, stream):
        self.calls.append((self.host, self.token, method, path, None))
        if self.fail == (method, path):
            raise OSError("lost upload response")
        fd, size, digest = stream
        os.lseek(fd, 0, os.SEEK_SET)
        payload = os.read(fd, size + 1)
        assert len(payload) == size and sha(payload) == digest
        name = path.split("?name=")[1]
        item = {
            "id": len(self.assets) + 1,
            "name": name,
            "state": "uploaded",
            "size": size,
            "digest": "sha256:" + digest,
        }
        self.assets.append(item)
        if name.endswith(".tar.gz"):
            type(self).archive = payload
        if name.endswith(".zip"):
            type(self).extension = payload
        if name == "registry-images.json":
            type(self).registry = payload
        return 201, item


class PromotionTests(unittest.TestCase):
    def registry(self, **kwargs):
        kwargs["revalidate"]()
        return {
            "schemaVersion": "lexiflow.registry-images.v1",
            "softwareVersion": kwargs["identity"]["softwareVersion"],
            "sourceCommit": kwargs["identity"]["sourceCommit"],
            "candidateSha256": kwargs["candidate_sha256"],
            "manifestSha256": kwargs["manifest_sha256"],
            "images": [
                {
                    "role": item["role"],
                    "platform": "linux/arm64",
                    "reference": f"ghcr.io/o/r-{'api' if item['role'] == 'api-image' else 'postgres'}:1.2.3",
                    "manifestDigest": "sha256:" + "f" * 64,
                    "imageId": item["imageDigest"],
                    "archiveSha256": item["sha256"],
                }
                for item in kwargs["manifest"]["artifacts"]
                if item["role"].endswith("-image")
            ],
        }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(Path(self.temp.name))
        self.patches = [
            patch.object(
                p.release_registry, "publish_images", side_effect=self.registry
            ),
            patch.object(p.release_registry, "verify_images", create=True),
            patch.object(p, "ROOT", self.fixture.root),
            patch.object(p, "LICENSE_TABLE", self.fixture.table),
            patch.object(
                p, "consume_candidate_pass", side_effect=self.fixture.consumer
            ),
            patch.object(
                p,
                "_run_bounded",
                return_value=(json.dumps(self.fixture.identity).encode(), b""),
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        FakeGitHub.reset()

    def promote(self, *, publish=False):
        return p.promote(
            submission_id=SUBMISSION,
            candidate_directory=self.fixture.candidate,
            publish=publish,
        )

    def test_prepare_full_asset_set_and_archive_bytes(self):
        with (
            patch.object(p, "_archive") as archive,
            patch.object(p.tempfile, "mkdtemp") as workspace,
        ):
            result = self.promote()
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["scope"], "promotion-precheck")
        self.assertFalse(result["published"])
        self.assertEqual(result["tag"], "v1.2.3")
        self.assertNotIn("assets", result)
        archive.assert_not_called()
        workspace.assert_not_called()
        p.release_registry.publish_images.assert_not_called()
        p.release_registry.verify_images.assert_not_called()
        with (
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
        ):
            published = self.promote(publish=True)
        self.assertTrue(published["published"])
        self.assertEqual(published["release_id"], 51)
        registry = json.loads(FakeGitHub.registry)
        self.assertEqual(
            registry["candidateSha256"],
            self.fixture.proof["candidate"]["candidateSha256"],
        )
        self.assertEqual(
            registry["manifestSha256"],
            self.fixture.proof["candidate"]["manifestSha256"],
        )
        self.assertEqual(
            {item["role"] for item in registry["images"]},
            {"api-image", "postgres-image"},
        )
        self.assertEqual(len(published["assets"]), 9)
        p.release_registry.verify_images.assert_called_once_with(
            registry,
            owner="o",
            repo="r",
            expected={
                "softwareVersion": "1.2.3",
                "sourceCommit": COMMIT,
                "candidateSha256": self.fixture.proof["candidate"]["candidateSha256"],
                "manifestSha256": self.fixture.proof["candidate"]["manifestSha256"],
            },
        )
        extension_bytes = (self.fixture.payload / "extension/package.zip").read_bytes()
        self.assertEqual(FakeGitHub.extension, extension_bytes)
        extension_asset = next(
            item
            for item in published["assets"]
            if item["name"] == "lexiflow-extension-1.2.3.zip"
        )
        self.assertEqual(extension_asset["bytes"], len(extension_bytes))
        self.assertEqual(extension_asset["sha256"], sha(extension_bytes))
        with tarfile.open(fileobj=io.BytesIO(FakeGitHub.archive), mode="r:gz") as tar:
            self.assertEqual(
                set(tar.getnames()),
                {
                    "lexiflow.sh",
                    "LICENSE",
                    "images/api.tar",
                    "images/postgres.tar",
                    "extension/package.zip",
                    "manifest.json",
                    "manifest.json.sha256",
                },
            )
            self.assertEqual(
                tar.extractfile("manifest.json").read(),
                (self.fixture.payload / "manifest.json").read_bytes(),
            )
            self.assertEqual(
                tar.extractfile("manifest.json.sha256").read(),
                (self.fixture.payload / "manifest.json.sha256").read_bytes(),
            )
            for artifact in self.fixture.artifacts:
                self.assertEqual(
                    tar.extractfile(artifact["path"]).read(),
                    (self.fixture.payload / artifact["path"]).read_bytes(),
                )
                self.assertEqual(
                    tar.getmember(artifact["path"]).mode,
                    0o755 if artifact["role"] == "runtime-entry" else 0o644,
                )
        self.assertTrue(
            all(
                call[1]
                == ("config" if call[3].endswith("/immutable-releases") else "write")
                for call in FakeGitHub.calls
            )
        )

    def test_missing_formal_proof_and_identity_rejections(self):
        with patch.object(
            p, "consume_candidate_pass", return_value={"result": "BLOCKED"}
        ):
            with self.assertRaisesRegex(
                p.PromotionError, "formal-candidate-proof-required"
            ):
                self.promote()

        for value in ("1.2.3-SNAPSHOT.20261006.gabc", "1.2.3"):
            self.fixture.identity["softwareVersion"] = value
            self.fixture.refresh()
            if value.endswith("gabc"):
                with self.assertRaises(p.PromotionError):
                    self.promote()
        self.fixture.identity["softwareVersion"] = "1.2.3"
        self.fixture.identity["dirty"] = True
        self.fixture.refresh()
        with self.assertRaisesRegex(
            p.PromotionError, "formal-release-identity-required"
        ):
            self.promote()

    def test_registry_failure_blocks_draft_and_never_retries(self):
        with (
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
            patch.object(
                p.release_registry,
                "publish_images",
                side_effect=p.release_registry.RegistryError(
                    "registry-push-unknown", stage="push", write_unknown=True
                ),
            ) as push,
        ):
            with self.assertRaises(p.PromotionError) as raised:
                self.promote(publish=True)
        self.assertEqual(raised.exception.stage, "registry-push")
        push.assert_called_once()
        self.assertFalse(any(call[2] == "POST" for call in FakeGitHub.calls))

    def test_registry_mapping_mismatch_blocks_draft(self):
        def wrong(**kwargs):
            result = self.registry(**kwargs)
            result["images"][0]["reference"] = "ghcr.io/other/api:1.2.3"
            return result

        with (
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
            patch.object(p.release_registry, "publish_images", side_effect=wrong),
        ):
            with self.assertRaisesRegex(p.PromotionError, "registry-mapping-invalid"):
                self.promote(publish=True)
        self.assertFalse(any(call[2] == "POST" for call in FakeGitHub.calls))

    def test_registry_recheck_failure_preserves_draft(self):
        with (
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
            patch.object(
                p.release_registry,
                "verify_images",
                side_effect=p.release_registry.RegistryError(
                    "registry-not-public", stage="anonymous-read"
                ),
                create=True,
            ),
        ):
            with self.assertRaises(p.PromotionError) as raised:
                self.promote(publish=True)
        self.assertEqual(raised.exception.release_id, 51)
        self.assertEqual(len(FakeGitHub.assets), 9)
        self.assertFalse(
            any(call[2] in ("PATCH", "DELETE") for call in FakeGitHub.calls)
        )

    def test_missing_image_role_blocks_precheck(self):
        self.fixture.manifest["artifacts"] = [
            item for item in self.fixture.artifacts if item["role"] != "postgres-image"
        ]
        self.fixture.refresh()
        with self.assertRaisesRegex(p.PromotionError, "candidate-image-set-invalid"):
            self.promote()

    def test_project_license_drift_and_symlink_rejected(self):
        own = self.fixture.root / "LICENSE"
        own.write_bytes(b"different license")
        with self.assertRaisesRegex(
            p.PromotionError, "project-license-notice-mismatch"
        ):
            self.promote()
        own.unlink()
        own.symlink_to(self.fixture.payload / "LICENSE")
        with self.assertRaisesRegex(p.PromotionError, "project-license-invalid"):
            self.promote()

    def test_repository_self_license_binds_real_approved_notice(self):
        root = Path(__file__).resolve().parents[2]
        table = root / "ops/release/distribution-licenses.json"
        licenses = json.loads(table.read_bytes())["licenses"]
        approved = [
            item
            for item in licenses
            if item["component"] == "LexiFlow" and item["noticePath"] == "LICENSE"
        ]
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["licenseId"], "MIT")
        own = (root / "LICENSE").read_bytes()
        self.assertIn(b"Copyright (c) 2026 LexiFlow contributors", own)
        self.assertEqual(approved[0]["noticeBytes"], len(own))
        self.assertEqual(approved[0]["noticeSha256"], sha(own))
        with patch.object(p, "ROOT", root), patch.object(p, "LICENSE_TABLE", table):
            p._license_check({"licenses": licenses})

    def test_additional_project_notice_preserves_root_license_binding(self):
        own = self.fixture.license
        index = {
            **own,
            "id": "lexiflow-index",
            "noticePath": "licenses/index.txt",
            "noticeBytes": 6,
            "noticeSha256": sha(b"index\n"),
        }
        licenses = [index, own]
        self.fixture.table.write_text(
            json.dumps(
                {
                    "schema_version": "lexiflow.distribution-licenses.v1",
                    "licenses": licenses,
                }
            )
        )
        with (
            patch.object(p, "ROOT", self.fixture.root),
            patch.object(p, "LICENSE_TABLE", self.fixture.table),
        ):
            p._license_check({"licenses": licenses})
            changed = [{**index, "noticeSha256": "0" * 64}, own]
            with self.assertRaisesRegex(
                p.PromotionError, "distribution-licenses-unapproved"
            ):
                p._license_check({"licenses": changed})
            (self.fixture.root / "LICENSE").write_bytes(b"different license")
            with self.assertRaisesRegex(
                p.PromotionError, "project-license-notice-mismatch"
            ):
                p._license_check({"licenses": licenses})

    def test_project_root_license_cannot_be_replaced_or_duplicated(self):
        own = self.fixture.license
        for licenses in (
            [{**own, "noticePath": "licenses/index.txt"}],
            [{**own, "licenseId": "Apache-2.0"}],
            [own, {**own, "id": "another-root-license"}],
        ):
            with self.subTest(licenses=licenses):
                self.fixture.table.write_text(
                    json.dumps(
                        {
                            "schema_version": "lexiflow.distribution-licenses.v1",
                            "licenses": licenses,
                        }
                    )
                )
                with (
                    patch.object(p, "ROOT", self.fixture.root),
                    patch.object(p, "LICENSE_TABLE", self.fixture.table),
                    self.assertRaisesRegex(p.PromotionError, "project-license-invalid"),
                ):
                    p._license_check({"licenses": licenses})

    def test_platform_license_and_payload_rejections(self):
        self.fixture.manifest["platforms"] = ["linux/amd64"]
        self.fixture.refresh()
        with self.assertRaisesRegex(
            p.PromotionError, "formal-release-platform-required"
        ):
            self.promote()
        self.fixture.manifest["platforms"] = ["linux/arm64"]
        self.fixture.table.write_text(
            '{"schema_version":"lexiflow.distribution-licenses.v1","licenses":[]}'
        )
        self.fixture.refresh()
        with self.assertRaisesRegex(
            p.PromotionError, "distribution-licenses-unapproved"
        ):
            self.promote()
        self.fixture.table.write_text(
            json.dumps(
                {
                    "schema_version": "lexiflow.distribution-licenses.v1",
                    "licenses": [{**self.fixture.license, "licenseName": "UNVERIFIED"}],
                }
            )
        )
        with self.assertRaisesRegex(
            p.PromotionError, "distribution-license-table-invalid"
        ):
            self.promote()
        self.fixture.table.write_text(
            json.dumps(
                {
                    "schema_version": "lexiflow.distribution-licenses.v1",
                    "licenses": [self.fixture.license],
                }
            )
        )
        (self.fixture.payload / "images/api.tar").write_bytes(b"changed")
        with self.assertRaisesRegex(
            p.PromotionError, "candidate-artifact-hash-mismatch"
        ):
            self.promote(publish=True)

    def test_license_table_bounded_duplicate_nonfinite_symlink(self):
        for data in (
            '{"schema_version":"lexiflow.distribution-licenses.v1","licenses":[],"licenses":[]}',
            '{"schema_version":"lexiflow.distribution-licenses.v1","licenses":NaN}',
            " " * (p.MAX_LICENSE_TABLE + 1),
        ):
            self.fixture.table.write_text(data)
            with self.assertRaisesRegex(
                p.PromotionError, "distribution-license-table-invalid"
            ):
                self.promote()
        self.fixture.table.unlink()
        self.fixture.table.symlink_to(self.fixture.payload / "manifest.json")
        with self.assertRaisesRegex(
            p.PromotionError, "distribution-license-table-invalid"
        ):
            self.promote()

    def test_remote_guards_and_existing_draft(self):
        with (
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
        ):
            for attr, value, reason in (
                ("immutable", False, "immutable-release-required"),
                ("tag_commit", "e" * 40, "remote-tag-mismatch"),
                ("existing", True, "release-tag-already-exists"),
            ):
                setattr(FakeGitHub, attr, value)
                with self.assertRaisesRegex(p.PromotionError, reason):
                    self.promote(publish=True)
                self.assertFalse(any(c[2] == "POST" for c in FakeGitHub.calls))
                FakeGitHub.reset()

    def test_extra_asset_and_wrong_digest_block_publish(self):
        with (
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
        ):
            for mutate in (
                lambda assets: (
                    assets
                    + [
                        {
                            "id": 99,
                            "name": "extra",
                            "state": "uploaded",
                            "size": 1,
                            "digest": "sha256:" + "0" * 64,
                        }
                    ]
                ),
                lambda assets: [
                    {**assets[0], "digest": "sha256:" + "0" * 64},
                    *assets[1:],
                ],
            ):
                old = FakeGitHub.request

                def request(self, *args, **kwargs):
                    result = old(self, *args, **kwargs)
                    if len(FakeGitHub.assets) == 9:
                        FakeGitHub.list_assets = mutate(FakeGitHub.assets)
                    return result

                with patch.object(FakeGitHub, "request", request):
                    with self.assertRaisesRegex(
                        p.PromotionError, "release-draft-incomplete"
                    ):
                        self.promote(publish=True)
                self.assertFalse(any(c[2] == "PATCH" for c in FakeGitHub.calls))
                FakeGitHub.reset()

    def test_create_upload_publish_unknown_no_retry(self):
        with (
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
        ):
            for method, path, stage in (
                ("POST", "/repos/o/r/releases", "create"),
                ("POST", "/repos/o/r/releases/51/assets?name=candidate.json", "upload"),
                ("PATCH", "/repos/o/r/releases/51", "publish"),
            ):
                FakeGitHub.fail = (method, path)
                with self.assertRaises(p.PromotionError) as caught:
                    self.promote(publish=True)
                self.assertEqual(caught.exception.stage, stage)
                self.assertEqual(
                    caught.exception.release_id, None if stage == "create" else 51
                )
                self.assertEqual(
                    sum(c[2] == method and c[3] == path for c in FakeGitHub.calls), 1
                )
                if stage != "publish":
                    self.assertFalse(any(c[2] == "PATCH" for c in FakeGitHub.calls))
                FakeGitHub.reset()

    def test_proof_drift_before_create_and_publish(self):
        original = self.fixture.consumer
        count = 0

        def drift(*args, **kwargs):
            nonlocal count
            count += 1
            value = original(*args, **kwargs)
            if count >= 3:
                return {
                    **value,
                    "proof": {**value["proof"], "chain": {"synthetic": False}},
                }
            return value

        with (
            patch.object(p, "consume_candidate_pass", side_effect=drift),
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
        ):
            with self.assertRaisesRegex(
                p.PromotionError, "formal-candidate-proof-required"
            ):
                self.promote(publish=True)
        self.assertFalse(any(c[2] == "POST" for c in FakeGitHub.calls))

    def test_archive_drift_paths_and_size_fail_closed(self):
        original = p._VerifiedReader.read
        changed = False
        target = self.fixture.payload / "images/api.tar"
        content = target.read_bytes()

        def drift(reader, size=-1):
            nonlocal changed
            result = original(reader, size)
            if not changed and reader.item["path"] == "images/api.tar":
                changed = True
                target.write_bytes(b"transient")
                target.write_bytes(content)
            return result

        with patch.object(p._VerifiedReader, "read", drift):
            with self.assertRaisesRegex(
                p.PromotionError, "candidate-artifact-hash-mismatch"
            ):
                self.promote(publish=True)
        self.assertTrue(changed)
        for unsafe in ["../outside", "images//x", "/outside", "x/../y", "x\\y"]:
            with self.assertRaises(p.PromotionError):
                p._bounded_item(
                    {"path": unsafe, "role": "sql", "bytes": 1, "sha256": "a" * 64}
                )
        with patch.object(p, "MAX_ASSET", 1), self.assertRaises(p.PromotionError):
            self.promote(publish=True)
        target.unlink()
        target.symlink_to(self.fixture.payload / "lexiflow.sh")
        with self.assertRaisesRegex(p.PromotionError, "candidate-path-unsafe"):
            self.promote(publish=True)

    def test_proof_drift_after_uploads_prevents_publish(self):
        original = self.fixture.consumer

        def drift(*args, **kwargs):
            if len(FakeGitHub.assets) == 9:
                return {"result": "BLOCKED"}
            return original(*args, **kwargs)

        with (
            patch.object(p, "consume_candidate_pass", side_effect=drift),
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
        ):
            with self.assertRaises(p.PromotionError) as caught:
                self.promote(publish=True)
        self.assertEqual(caught.exception.release_id, 51)
        self.assertEqual(caught.exception.stage, "draft")
        self.assertEqual(len(FakeGitHub.assets), 9)
        self.assertFalse(any(call[2] == "PATCH" for call in FakeGitHub.calls))

    def test_cli_unknown_output_and_cleanup_preserve_remote_fact(self):
        from contextlib import redirect_stdout

        argv = [
            "--submission-id",
            SUBMISSION,
            "--candidate-directory",
            str(self.fixture.candidate),
            "--publish",
        ]
        with (
            patch.object(
                p,
                "promote",
                side_effect=p.PromotionError(
                    "release-write-state-unknown", stage="publish", release_id=51
                ),
            ),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(p.main(argv), 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result["published"], "unknown")
        self.assertEqual(result["release_id"], 51)
        actual_cleanup = p.shutil.rmtree
        roots = []

        def cleanup_failure(root):
            roots.append(root)
            raise OSError("private diagnostic not exposed")

        try:
            with (
                patch.object(p, "GitHub", FakeGitHub),
                patch.object(p, "_repository", return_value=("o", "r")),
                patch.dict(
                    os.environ,
                    {
                        "GITHUB_TOKEN": "write",
                        "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config",
                    },
                ),
                patch.object(p.shutil, "rmtree", side_effect=cleanup_failure),
            ):
                result = self.promote(publish=True)
            self.assertEqual(result["result"], "BLOCKED")
            self.assertIs(result["published"], True)
            self.assertEqual(result["release_id"], 51)
        finally:
            for root in roots:
                actual_cleanup(root)

    def test_subprocess_environments_exclude_credentials(self):
        captured = {}

        def node(*args, **kwargs):
            captured["node"] = args[2]
            return json.dumps(self.fixture.identity).encode(), b""

        with (
            patch.object(p, "_run_bounded", side_effect=node),
            patch.dict(
                os.environ,
                {
                    "GITHUB_TOKEN": "write-secret",
                    "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config-secret",
                    "NODE_OPTIONS": "--inspect",
                },
            ),
        ):
            self.promote()
        self.assertNotIn("GITHUB_TOKEN", captured["node"])
        self.assertNotIn("LEXIFLOW_RELEASE_CONFIG_TOKEN", captured["node"])
        self.assertNotIn("NODE_OPTIONS", captured["node"])
        with patch.object(p.subprocess, "run") as git:
            git.return_value.stdout = "https://github.com/o/r.git\n"
            with patch.dict(os.environ, {"GITHUB_TOKEN": "write-secret"}):
                self.assertEqual(p._repository(p.ROOT), ("o", "r"))
            self.assertNotIn("GITHUB_TOKEN", git.call_args.kwargs["env"])
            git.return_value.stdout = "https://evil.example/o/r"
            with self.assertRaisesRegex(p.PromotionError, "repository-origin-invalid"):
                p._repository(p.ROOT)

    def test_asset_set_requires_id_state_and_exact_digest(self):
        digest = sha(b"artifact")
        expected = {"a": (8, digest)}
        valid = {
            "id": 1,
            "name": "a",
            "state": "uploaded",
            "size": 8,
            "digest": "sha256:" + digest,
        }
        self.assertTrue(p._asset_digest_matches([valid], expected))
        for variant in (
            [{**valid, "state": "new"}],
            [{**valid, "id": 0}],
            [{**valid, "digest": "sha256:" + "0" * 64}],
            [valid, valid],
            [valid, {**valid, "id": 2, "name": "extra"}],
        ):
            self.assertFalse(p._asset_digest_matches(variant, expected))

    def test_transport_token_route_redirect_and_exact_stream(self):
        class Response:
            status = 201
            done = False

            def read1(self, _amount):
                if self.done:
                    return b""
                self.done = True
                return b'{"ok":true}'

        class Connection:
            seen = []

            def __init__(self, host, **_kwargs):
                self.host = host
                self.sock = None
                self.headers = {}
                self.body = bytearray()
                Connection.seen.append(self)

            def connect(self):
                pass

            def putrequest(self, *_args, **_kwargs):
                pass

            def putheader(self, key, value):
                self.headers[key] = value

            def endheaders(self, body=b""):
                self.body.extend(body)

            def send(self, body):
                self.body.extend(body)

            def getresponse(self):
                return Response()

            def close(self):
                pass

        with patch.object(p.http.client, "HTTPSConnection", Connection):
            client = p.GitHub("config", "o", "r", config=True)
            with (
                patch.object(Response, "status", 302),
                self.assertRaisesRegex(p.PromotionError, "github-redirect-rejected"),
            ):
                client.api("GET", "/repos/o/r/immutable-releases")
            with (
                patch.object(p, "MAX_JSON", 4),
                self.assertRaisesRegex(p.PromotionError, "github-response-too-large"),
            ):
                client.api("GET", "/repos/o/r/immutable-releases")
            with (
                patch.object(p.time, "monotonic", side_effect=[0, 61]),
                self.assertRaisesRegex(p.PromotionError, "github-request-deadline"),
            ):
                client.api("GET", "/repos/o/r/immutable-releases")
            client.api("GET", "/repos/o/r/immutable-releases")
            self.assertEqual(
                Connection.seen[-1].headers["Authorization"], "Bearer config"
            )
            with self.assertRaisesRegex(p.PromotionError, "github-route-rejected"):
                client.api("POST", "/repos/o/r/releases")
            asset = self.fixture.root / "asset.bin"
            asset.write_bytes(b"one two three")
            fd = os.open(asset, os.O_RDONLY)
            try:
                upload = p.GitHub("write", "o", "r", "uploads.github.com")
                upload.request(
                    "POST",
                    "/repos/o/r/releases/51/assets?name=asset.bin",
                    content_type="application/octet-stream",
                    stream=(fd, 13, sha(b"one two three")),
                )
            finally:
                os.close(fd)
            self.assertEqual(Connection.seen[-1].headers["Content-Length"], "13")
            self.assertEqual(Connection.seen[-1].body, b"one two three")
            self.assertEqual(
                Connection.seen[-1].headers["Authorization"], "Bearer write"
            )
            original_send = Connection.send

            def append_during_send(connection, data):
                original_send(connection, data)
                with asset.open("ab") as target:
                    target.write(b"growth")

            fd = os.open(asset, os.O_RDONLY)
            try:
                with (
                    patch.object(Connection, "send", append_during_send),
                    self.assertRaisesRegex(
                        p.PromotionError, "asset-changed-during-upload"
                    ),
                ):
                    upload.request(
                        "POST",
                        "/repos/o/r/releases/51/assets?name=asset.bin",
                        stream=(fd, 13, sha(b"one two three")),
                    )
                self.assertEqual(Connection.seen[-1].body, b"one two three")
            finally:
                os.close(fd)
                asset.write_bytes(b"one two three")
            fd = os.open(asset, os.O_RDONLY)
            try:
                with self.assertRaisesRegex(
                    p.PromotionError, "asset-changed-before-upload"
                ):
                    upload.request(
                        "POST",
                        "/repos/o/r/releases/51/assets?name=asset.bin",
                        content_type="application/octet-stream",
                        stream=(fd, 10, sha(b"one two three")),
                    )
            finally:
                os.close(fd)


if __name__ == "__main__":
    unittest.main()
