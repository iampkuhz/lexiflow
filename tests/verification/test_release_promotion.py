"""正式候选晋升消费者的合成端到端边界测试。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import tempfile
import tarfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.release.workflow import promote_verified_release as p
from ops.release.workflow import release_registry as registry

SUBMISSION = "00000000-0000-0000-0000-000000000001"
COMMIT = "a" * 40


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, root: Path):
        root = root.resolve()
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
        image_labels = {
            "org.opencontainers.image.version": self.identity["softwareVersion"],
            "org.opencontainers.image.revision": self.identity["sourceCommit"],
            "io.lexiflow.build-id": self.identity["buildId"],
            "io.lexiflow.source-sha256": self.identity["sourceSha256"],
            "io.lexiflow.image-role": "runtime",
            "io.lexiflow.deployment-version": self.identity["baseVersion"],
        }
        config = json.dumps(
            {"architecture": "arm64", "os": "linux", "config": {"Labels": image_labels}}
        ).encode()
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:") as bundle:
            for member_name, content in (
                ("manifest.json", b'[{"Config":"config.json"}]'),
                ("config.json", config),
            ):
                info = tarfile.TarInfo(member_name)
                info.size = len(content)
                bundle.addfile(info, io.BytesIO(content))
        for role, name, data in (
            ("runtime-entry", "lexiflow.sh", b"#!/bin/sh\n"),
            ("license", "LICENSE", b"MIT\n"),
            ("extension", "extension.zip", b"accepted extension zip bytes"),
            ("dataset", "data/dataset.bin", b"dataset bytes"),
            (
                "runtime-image",
                "images/linux-arm64/runtime-image.tar",
                archive.getvalue(),
            ),
            (
                "corresponding-sources",
                "sources/lexiflow-third-party-sources-1.2.3.tar.gz",
                b"source archive",
            ),
        ):
            path = self.payload / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.artifacts.append(
                {"role": role, "path": name, "bytes": len(data), "sha256": sha(data)}
            )
            if role == "runtime-image":
                self.artifacts[-1].update(
                    platform="linux/arm64", imageDigest="sha256:" + sha(config)
                )
            if role == "license":
                self.artifacts[-1].update(licenseIds=["lexiflow"])
            if role == "corresponding-sources":
                self.artifacts[-1].update(
                    licenseIds=["lexiflow"],
                    metadata={
                        "indexSha256": "e" * 64,
                        "items": [
                            {
                                "id": "lexiflow",
                                "component": "LexiFlow",
                                "licenseId": "MIT",
                                "licenseName": "MIT",
                                "sourceUrl": self.license["sourceUrl"],
                                "noticePath": "LICENSE",
                                "archivePath": "materials/LICENSE",
                                "bytes": 4,
                                "sha256": sha(b"MIT\n"),
                            }
                        ],
                    },
                )
            if role == "dataset":
                self.artifacts[-1].update(
                    metadata={
                        "releaseId": "rel",
                        "preparationId": "prep",
                        "ruleId": "rule",
                        "sqlVersion": "sql-v1",
                    }
                )
            if role == "extension":
                self.artifacts[-1].update(
                    metadata={
                        "softwareVersion": "1.2.3",
                        "sourceCommit": COMMIT,
                        "expectedDataset": {
                            "releaseId": "rel",
                            "preparationId": "prep",
                            "ruleId": "rule",
                            "sqlVersion": "sql-v1",
                            "sha256": sha(b"dataset bytes"),
                        },
                    }
                )
        self.manifest = {
            "buildIdentity": self.identity,
            "softwareVersion": "1.2.3",
            "sourceCommit": COMMIT,
            "apiContract": "api-v1",
            "sqlVersion": "sql-v1",
            "dataset": {"releaseId": "rel", "preparationId": "prep", "ruleId": "rule"},
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
    created_body = None

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
        cls.created_body = None

    def api(self, method, path, body=None):
        self.calls.append((self.host, self.token, method, path, body))
        if self.fail == (method, path):
            raise OSError("lost response")
        if path.endswith("/immutable-releases"):
            return 200, {"enabled": self.immutable}
        if "/git/ref/tags/" in path:
            return 200, {"object": {"type": "commit", "sha": self.tag_commit}}
        if path.startswith("/repos/o/r/releases?per_page"):
            return 200, [
                {"id": self.release_id, "tag_name": "v1.2.3"}
            ] if self.existing else []
        if path.endswith("/assets?per_page=100&page=1"):
            return (
                200,
                self.list_assets if self.list_assets is not None else self.assets,
            )
        if path.endswith("/releases/latest"):
            return 200, {"id": self.release_id}
        if method == "POST" and path.endswith("/releases"):
            type(self).created_body = body["body"]
            return 201, {
                "id": self.release_id,
                "tag_name": "v1.2.3",
                "draft": True,
                "prerelease": False,
                "body": body["body"],
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
                "body": self.created_body,
                "target_commitish": COMMIT,
                "name": "v1.2.3",
            }
        if method == "GET" and path.endswith("/releases/99"):
            return 200, {
                "id": 99,
                "tag_name": "v1.2.3",
                "draft": True,
                "prerelease": False,
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
        if name == "release.json":
            type(self).registry = payload
        return 201, item


class PromotionTests(unittest.TestCase):
    def test_live_authorization_requires_single_platform_approved_attempt_and_protection(
        self,
    ):
        self.patches[-1].stop()
        identity = {"softwareVersion": "1.2.3", "sourceCommit": COMMIT}
        run_id = "12345"
        run = {
            "id": 12345,
            "run_attempt": 1,
            "head_sha": COMMIT,
            "head_branch": "v1.2.3",
            "status": "in_progress",
            "event": "workflow_dispatch",
            "path": ".github/workflows/release.yml",
            "repository": {"full_name": "o/r"},
        }
        approval = [
            {
                "state": "approved",
                "user": {"id": 7, "login": "o"},
                "environments": [
                    {
                        "id": 55,
                        "name": "lexiflow-release-promotion",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        ]
        protection = {
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "reviewers": [
                        {"type": "User", "reviewer": {"id": 7, "login": "o"}}
                    ],
                }
            ],
            "id": 55,
            "can_admins_bypass": False,
            "deployment_branch_policy": {
                "protected_branches": False,
                "custom_branch_policies": True,
            },
        }
        policies = {
            "total_count": 1,
            "branch_policies": [{"type": "tag", "name": "v*"}],
        }

        class Api:
            def api(self, method, path):
                if path.endswith("/approvals"):
                    return 200, approval
                return 200, run

        class Config:
            def api(self, method, path):
                if path.endswith("deployment-branch-policies"):
                    return 200, policies
                return 200, protection

        env = {
            "GITHUB_RUN_ID": run_id,
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/tags/v1.2.3",
            "GITHUB_SHA": COMMIT,
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_RUN_ATTEMPT": "1",
        }
        with patch.dict(os.environ, env):
            p._assert_live_promotion_authorization(Api(), Config(), "o", "r", identity)
            with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "2"}):
                self.assertEqual(os.environ["GITHUB_RUN_ATTEMPT"], "2")
                with self.assertRaisesRegex(
                    p.PromotionError, "promotion-run-context-invalid"
                ):
                    p._assert_live_promotion_authorization(
                        Api(), Config(), "o", "r", identity
                    )
            original_api = Api.api

            def no_approval(self, method, path):
                return (
                    (200, [])
                    if path.endswith("/approvals")
                    else original_api(self, method, path)
                )

            with (
                patch.object(Api, "api", no_approval),
                self.assertRaisesRegex(
                    p.PromotionError, "promotion-human-approval-missing"
                ),
            ):
                p._assert_live_promotion_authorization(
                    Api(), Config(), "o", "r", identity
                )
            approval[:] = [
                dict(
                    approval[0],
                    state="rejected",
                    environments=[
                        {
                            "id": 55,
                            "name": "lexiflow-release-promotion",
                            "updated_at": "2026-01-01T00:00:00Z",
                        }
                    ],
                ),
                dict(
                    approval[0],
                    state="approved",
                    environments=[
                        {
                            "id": 55,
                            "name": "lexiflow-release-promotion",
                            "updated_at": "2099-01-01T00:00:00Z",
                        }
                    ],
                ),
            ]
            with self.assertRaisesRegex(
                p.PromotionError, "promotion-approval-history-ambiguous"
            ):
                p._assert_live_promotion_authorization(
                    Api(), Config(), "o", "r", identity
                )
            approval[:] = [
                dict(approval[0], state="approved", user={"id": 7, "login": "o"}),
                dict(
                    approval[0],
                    state="rejected",
                    user={"id": 8, "login": "intruder"},
                    environments=[
                        {
                            "id": 55,
                            "name": "lexiflow-release-promotion",
                            "updated_at": "2026-01-02T00:00:00Z",
                        }
                    ],
                ),
            ]
            with self.assertRaisesRegex(
                p.PromotionError, "promotion-approval-history-ambiguous"
            ):
                p._assert_live_promotion_authorization(
                    Api(), Config(), "o", "r", identity
                )
            approval[:] = [
                dict(approval[0], state="approved", user={"id": 8, "login": "intruder"})
            ]
            with self.assertRaisesRegex(
                p.PromotionError, "promotion-human-approval-reviewer-mismatch"
            ):
                p._assert_live_promotion_authorization(
                    Api(), Config(), "o", "r", identity
                )
            approval[:] = [dict(approval[0], user={"id": 7, "login": "o"})]
            approval[0]["environments"] = [
                {
                    "id": 56,
                    "name": "lexiflow-release-promotion",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ]
            with self.assertRaisesRegex(
                p.PromotionError, "promotion-environment-id-mismatch"
            ):
                p._assert_live_promotion_authorization(
                    Api(), Config(), "o", "r", identity
                )
            approval[0]["environments"] = [
                {
                    "id": 55,
                    "name": "lexiflow-release-promotion",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ]
            run["status"] = "completed"
            with self.assertRaisesRegex(
                p.PromotionError, "promotion-run-platform-check-failed"
            ):
                p._assert_live_promotion_authorization(
                    Api(), Config(), "o", "r", identity
                )
            run["status"] = "in_progress"
            protection["protection_rules"][0]["reviewers"][0]["reviewer"] = {
                "id": 8,
                "login": "wrong-owner",
            }
            with self.assertRaisesRegex(
                p.PromotionError, "promotion-environment-reviewer-mismatch"
            ):
                p._assert_live_promotion_authorization(
                    Api(), Config(), "o", "r", identity
                )
            protection["protection_rules"][0]["reviewers"][0]["reviewer"] = {
                "id": 7,
                "login": "o",
            }
            policies["branch_policies"][0]["type"] = "branch"
            with self.assertRaisesRegex(
                p.PromotionError, "promotion-environment-tag-policy-mismatch"
            ):
                p._assert_live_promotion_authorization(
                    Api(), Config(), "o", "r", identity
                )
            policies["branch_policies"][0]["type"] = "tag"
            run["repository"] = None
            with self.assertRaisesRegex(
                p.PromotionError, "promotion-run-platform-check-failed"
            ):
                p._assert_live_promotion_authorization(
                    Api(), Config(), "o", "r", identity
                )

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
                    "reference": "ghcr.io/o/r:1.2.3",
                    "manifestDigest": "sha256:" + "f" * 64,
                    "imageId": item["imageDigest"],
                    "archiveSha256": item["sha256"],
                }
                for item in kwargs["manifest"]["artifacts"]
                if item["role"] == "runtime-image"
            ],
        }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(Path(self.temp.name))
        self.notes = Path(self.temp.name) / "notes"
        self.notes.mkdir()
        (self.notes / "1.2.3.md").write_text(
            "# 1. 1.2.3\n\n前置：Podman。\n\n两步：启动并安装扩展。\n\n限制：ARM64。\n"
        )
        self.patches = [
            patch.object(
                p.release_registry, "publish_images", side_effect=self.registry
            ),
            patch.object(p.release_registry, "verify_images", create=True),
            patch.object(p, "ROOT", self.fixture.root),
            patch.object(p, "LICENSE_TABLE", self.fixture.table),
            patch.object(p, "NOTES_ROOT", self.notes),
            patch.object(
                p, "consume_candidate_pass", side_effect=self.fixture.consumer
            ),
            patch.object(
                p,
                "_run_bounded",
                return_value=(json.dumps(self.fixture.identity).encode(), b""),
            ),
            patch.object(p, "_assert_live_promotion_authorization"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        FakeGitHub.reset()

    def promote(self, *, publish=False, confirmed_recovery=False):
        return p.promote(
            submission_id=SUBMISSION,
            candidate_directory=self.fixture.candidate,
            publish=publish,
            confirmed_recovery=confirmed_recovery,
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
        self.assertIn(
            "podman run --image-volume=ignore --pull=always", FakeGitHub.created_body
        )
        self.assertIn(
            "--volume lexiflow-release-data:/var/lib/lexiflow", FakeGitHub.created_body
        )
        self.assertIn("ghcr.io/o/r@sha256:" + "f" * 64, FakeGitHub.created_body)
        self.assertEqual(published["release_id"], 51)
        release_json = json.loads(FakeGitHub.registry)
        self.assertEqual(release_json["schemaVersion"], "lexiflow.release.v1")
        self.assertEqual(release_json["image"]["platform"], "linux/arm64")
        self.assertEqual(
            release_json["image"]["configId"], self.fixture.artifacts[4]["imageDigest"]
        )
        self.assertEqual(
            release_json["assets"]["thirdPartySources"]["sha256"],
            sha(FakeGitHub.archive),
        )
        self.assertEqual(len(published["assets"]), 3)
        registry_mapping = p.release_registry.verify_images.call_args.args[0]
        p.release_registry.verify_images.assert_called_once_with(
            registry_mapping,
            owner="o",
            repo="r",
            expected={
                "softwareVersion": "1.2.3",
                "sourceCommit": COMMIT,
                "candidateSha256": self.fixture.proof["candidate"]["candidateSha256"],
                "manifestSha256": self.fixture.proof["candidate"]["manifestSha256"],
            },
        )
        extension_bytes = (self.fixture.payload / "extension.zip").read_bytes()
        self.assertEqual(FakeGitHub.extension, extension_bytes)
        extension_asset = next(
            item
            for item in published["assets"]
            if item["name"] == "lexiflow-extension-1.2.3.zip"
        )
        self.assertEqual(extension_asset["bytes"], len(extension_bytes))
        self.assertEqual(extension_asset["sha256"], sha(extension_bytes))
        source_item = next(
            item
            for item in self.fixture.artifacts
            if item["role"] == "corresponding-sources"
        )
        self.assertEqual(
            FakeGitHub.archive,
            (self.fixture.payload / source_item["path"]).read_bytes(),
        )
        names = {item["name"] for item in published["assets"]}
        self.assertEqual(
            names,
            {
                "lexiflow-extension-1.2.3.zip",
                "lexiflow-third-party-sources-1.2.3.tar.gz",
                "release.json",
            },
        )
        self.assertFalse(
            any(
                name.endswith(".sha256") or name in {"manifest.json", "candidate.json"}
                for name in names
            )
        )
        self.assertTrue(
            all(
                call[1]
                == ("config" if call[3].endswith("/immutable-releases") else "write")
                for call in FakeGitHub.calls
            )
        )

    def test_precheck_reads_actual_archive_labels_without_write_credentials(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(registry, "_run") as podman,
            patch.object(p, "GitHub") as remote,
        ):
            result = self.promote()
        self.assertEqual(result["result"], "PASS")
        self.assertFalse(result["published"])
        podman.assert_not_called()
        remote.assert_not_called()
        item = self.fixture.artifacts[4]
        labels = registry._image_labels(self.fixture.identity)
        del labels["io.lexiflow.deployment-version"]
        config = json.dumps(
            {"architecture": "arm64", "os": "linux", "config": {"Labels": labels}}
        ).encode()
        target = self.fixture.payload / item["path"]
        with tarfile.open(target, "w:") as bundle:
            for name, content in (
                ("config.json", config),
                ("manifest.json", b'[{"Config":"config.json"}]'),
            ):
                member = tarfile.TarInfo(name)
                member.size = len(content)
                bundle.addfile(member, io.BytesIO(content))
        raw = target.read_bytes()
        item.update(
            bytes=len(raw), sha256=sha(raw), imageDigest="sha256:" + sha(config)
        )
        self.fixture.refresh()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(registry, "_run") as podman,
            patch.object(p, "GitHub") as remote,
            self.assertRaisesRegex(p.PromotionError, "registry-image-label-mismatch"),
        ):
            self.promote()
        podman.assert_not_called()
        remote.assert_not_called()
        p.release_registry.publish_images.assert_not_called()

    def test_extension_path_comes_only_from_bound_manifest(self):
        extension = next(
            item for item in self.fixture.artifacts if item["role"] == "extension"
        )
        original = self.fixture.payload / extension["path"]
        target = self.fixture.payload / "artifacts/chrome.zip"
        target.parent.mkdir()
        original.rename(target)
        extension["path"] = "artifacts/chrome.zip"
        self.fixture.refresh()
        # Exercise both precheck and actual asset copying against the declared path.
        self._assert_declared_extension_assets(target)

    def _assert_declared_extension_assets(self, target):
        self.assertEqual(self.promote()["result"], "PASS")
        with (
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
        ):
            result = self.promote(publish=True)
        self.assertTrue(result["published"])
        self.assertEqual(len(result["assets"]), 3)
        self.assertEqual(FakeGitHub.extension, target.read_bytes())
        self.assertEqual(len(result["assets"]), 3)

    def test_extension_set_size_and_path_rejections(self):
        import copy

        original = copy.deepcopy(self.fixture.artifacts)
        extension = next(item for item in original if item["role"] == "extension")
        others = [item for item in original if item["role"] != "extension"]
        cases = [
            (others, "candidate-extension-invalid"),
            (
                [*original, {**extension, "path": "second.zip"}],
                "candidate-extension-invalid",
            ),
            (
                [*others, {**extension, "bytes": p.MAX_EXTENSION + 1}],
                "candidate-extension-invalid",
            ),
            (
                [*others, {**extension, "path": "../extension.zip"}],
                "candidate-manifest-invalid",
            ),
        ]
        for artifacts, reason in cases:
            with self.subTest(reason=reason, count=len(artifacts)):
                self.fixture.manifest["artifacts"] = artifacts
                self.fixture.refresh()
                with self.assertRaisesRegex(p.PromotionError, reason):
                    self.promote()
        self.assertFalse(any(call[2] == "POST" for call in FakeGitHub.calls))

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
        self.assertEqual(len(FakeGitHub.assets), 3)
        self.assertFalse(
            any(call[2] in ("PATCH", "DELETE") for call in FakeGitHub.calls)
        )

    def test_missing_image_role_blocks_precheck(self):
        self.fixture.manifest["artifacts"] = [
            item for item in self.fixture.artifacts if item["role"] != "runtime-image"
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
        (
            self.fixture.payload / "sources/lexiflow-third-party-sources-1.2.3.tar.gz"
        ).write_bytes(b"changed")
        with (
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
            self.assertRaisesRegex(
                p.PromotionError, "candidate-artifact-hash-mismatch"
            ),
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
                ("existing", True, "release-existing-content-conflict"),
            ):
                setattr(FakeGitHub, attr, value)
                with self.assertRaisesRegex(p.PromotionError, reason):
                    self.promote(publish=True)
                self.assertFalse(any(c[2] == "POST" for c in FakeGitHub.calls))
                FakeGitHub.reset()

    def test_release_notes_version_mismatch_prevents_remote_calls(self):
        (self.notes / "1.2.3.md").write_text("# 1. 9.9.9\n\n前置 两步 限制\n")
        with (
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
            self.assertRaisesRegex(p.PromotionError, "release-notes-version-mismatch"),
        ):
            self.promote(publish=True)
        self.assertEqual(FakeGitHub.calls, [])
        p.release_registry.publish_images.assert_not_called()

    def test_unknown_upload_is_resolved_by_matching_draft_resume(self):
        with (
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
        ):
            FakeGitHub.fail = (
                "POST",
                "/repos/o/r/releases/51/assets?name=lexiflow-third-party-sources-1.2.3.tar.gz",
            )
            with self.assertRaises(p.PromotionError) as caught:
                self.promote(publish=True)
            self.assertEqual(caught.exception.stage, "upload")
            self.assertEqual(len(FakeGitHub.assets), 1)
            calls_before = len(FakeGitHub.calls)
            with self.assertRaisesRegex(
                p.PromotionError, "promotion-recovery-confirmation-required"
            ):
                self.promote(publish=True)
            self.assertEqual(len(FakeGitHub.calls), calls_before)
            FakeGitHub.fail = None
            FakeGitHub.existing = True
            result = self.promote(publish=True, confirmed_recovery=True)
        self.assertTrue(result["published"])
        self.assertEqual(len(FakeGitHub.assets), 3)
        self.assertEqual(
            sum(
                call[2] == "POST" and call[3].endswith("/releases")
                for call in FakeGitHub.calls
            ),
            1,
        )
        self.assertTrue(
            p.release_registry.publish_images.call_args.kwargs["existing_only"]
        )
        event_root = (
            self.fixture.root
            / "tmp/quality/release-workflow/1.2.3/promotion"
            / self.fixture.proof["candidate"]["candidateSha256"]
            / "events"
        )
        states = [
            json.loads(item.read_bytes())["state"] for item in event_root.glob("*.json")
        ]
        self.assertIn("unknown", states)
        self.assertIn("confirmed", states)

    def test_unknown_registry_write_requires_explicit_recovery(self):
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
                    "registry-push-unknown", "push", "runtime-image", True
                ),
            ) as publish,
        ):
            with self.assertRaisesRegex(p.PromotionError, "registry-push-unknown"):
                self.promote(publish=True)
            with self.assertRaisesRegex(
                p.PromotionError, "promotion-recovery-confirmation-required"
            ):
                self.promote(publish=True)
        publish.assert_called_once()

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
                    if len(FakeGitHub.assets) == 3:
                        FakeGitHub.list_assets = mutate(FakeGitHub.assets)
                    return result

                with patch.object(FakeGitHub, "request", request):
                    with self.assertRaisesRegex(
                        p.PromotionError, "release-draft-incomplete"
                    ):
                        self.promote(publish=True)
                self.assertFalse(any(c[2] == "PATCH" for c in FakeGitHub.calls))
                event_root = (
                    self.fixture.root
                    / "tmp/quality/release-workflow/1.2.3/promotion"
                    / self.fixture.proof["candidate"]["candidateSha256"]
                    / "events"
                )
                self.assertFalse(
                    p._has_unknown_write(
                        "1.2.3", self.fixture.proof["candidate"]["candidateSha256"]
                    ),
                    [
                        (
                            e.get("stage"),
                            e.get("state"),
                            e.get("releaseId"),
                            e.get("asset"),
                        )
                        for e in map(
                            json.loads,
                            (f.read_bytes() for f in event_root.glob("*.json")),
                        )
                    ],
                )
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
                ("POST", "/repos/o/r/releases/51/assets?name=release.json", "upload"),
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
                p.shutil.rmtree(self.fixture.root / "tmp", ignore_errors=True)

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
        target = (
            self.fixture.payload / "sources/lexiflow-third-party-sources-1.2.3.tar.gz"
        )
        target.write_bytes(b"tampered")
        with (
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
            self.assertRaisesRegex(
                p.PromotionError, "candidate-artifact-hash-mismatch"
            ),
        ):
            self.promote(publish=True)
        for unsafe in ["../outside", "images//x", "/outside", "x/../y", "x\\y"]:
            with self.assertRaises(p.PromotionError):
                p._bounded_item(
                    {"path": unsafe, "role": "sql", "bytes": 1, "sha256": "a" * 64}
                )
        with patch.object(p, "MAX_ASSET", 1), self.assertRaises(p.PromotionError):
            self.promote(publish=True)
        target.unlink()
        target.symlink_to(self.fixture.payload / "lexiflow.sh")
        with (
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "write", "LEXIFLOW_RELEASE_CONFIG_TOKEN": "config"},
            ),
            self.assertRaisesRegex(p.PromotionError, "candidate-path-unsafe"),
        ):
            self.promote(publish=True)

    def test_proof_drift_after_uploads_prevents_publish(self):
        original = self.fixture.consumer

        def drift(*args, **kwargs):
            if len(FakeGitHub.assets) == 3:
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
        self.assertEqual(len(FakeGitHub.assets), 3)
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

    def test_upload_has_fixed_absolute_transfer_budget(self):
        clock = [0]
        step = 30

        class Response:
            status = 201

            def read1(self, _amount):
                return b""

        class Connection:
            instances: list[object] = []

            def __init__(self, host, **kwargs):
                self.host = host
                self.kwargs = kwargs
                self.sock = None
                self.sent = 0
                self.closed = False
                self.clock = clock
                self.step = step
                Connection.instances.append(self)

            def connect(self):
                pass

            def putrequest(self, *_args, **_kwargs):
                pass

            def putheader(self, *_args):
                pass

            def endheaders(self, *_args):
                pass

            def send(self, _data):
                self.sent += 1
                self.clock[0] += self.step

            def getresponse(self):
                return Response()

            def close(self):
                self.closed = True

        class Timer:
            instances: list[object] = []

            def __init__(self, interval, callback):
                self.interval, self.callback, self.cancelled = interval, callback, False
                self.joined = False
                Timer.instances.append(self)

            def start(self):
                pass

            def cancel(self):
                self.cancelled = True

            def join(self):
                self.joined = True

        with (
            patch.object(p.http.client, "HTTPSConnection", Connection),
            patch.object(p.threading, "Timer", Timer),
        ):
            asset = self.fixture.root / "budget.bin"
            asset.write_bytes(b"x" * (3 * 1024 * 1024))
            fd = os.open(asset, os.O_RDONLY)
            try:
                client = p.GitHub("write", "o", "r", "uploads.github.com")
                # A steady stream taking 90 seconds succeeds; elapsed time is not
                # allowed to refresh the 900-second absolute deadline.
                with patch.object(p.time, "monotonic", side_effect=lambda: clock[0]):
                    client.request(
                        "POST",
                        "/repos/o/r/releases/51/assets?name=a.bin",
                        stream=(fd, 3 * 1024 * 1024, sha(b"x" * (3 * 1024 * 1024))),
                    )
                self.assertEqual(Connection.instances[-1].kwargs["timeout"], 20)
                self.assertEqual(Connection.instances[-1].sent, 3)
                self.assertTrue(Connection.instances[-1].closed)
                self.assertEqual(Timer.instances[-1].interval, 900)
                self.assertTrue(Timer.instances[-1].cancelled)
                self.assertTrue(Timer.instances[-1].joined)
                self.assertEqual(clock[0], 90)
                # Expiration after 900 seconds fails, even though an upload made
                # progress; ordinary JSON continues to enforce its 60s budget.
                clock[0] = 0
                step = 301
                with (
                    patch.object(p.time, "monotonic", side_effect=lambda: clock[0]),
                    self.assertRaisesRegex(p.PromotionError, "github-request-deadline"),
                ):
                    client.request(
                        "POST",
                        "/repos/o/r/releases/51/assets?name=a.bin",
                        stream=(fd, 3 * 1024 * 1024, sha(b"x" * (3 * 1024 * 1024))),
                    )
                self.assertEqual(Timer.instances[-1].interval, 900)
                self.assertEqual(Connection.instances[-1].sent, 3)
                self.assertTrue(Connection.instances[-1].closed)
                self.assertTrue(Timer.instances[-1].cancelled)
                self.assertTrue(Timer.instances[-1].joined)
                self.assertEqual(clock[0], 903)
                api = p.GitHub("config", "o", "r", config=True)
                clock[0] = 0
                with (
                    patch.object(p.time, "monotonic", side_effect=[0, 61]),
                    self.assertRaisesRegex(p.PromotionError, "github-request-deadline"),
                ):
                    api.api("GET", "/repos/o/r/immutable-releases")
                self.assertEqual(Timer.instances[-1].interval, 60)
                self.assertTrue(Timer.instances[-1].cancelled)
                self.assertTrue(Timer.instances[-1].joined)
                self.assertTrue(Connection.instances[-1].closed)
            finally:
                os.close(fd)


class RealRegistryPromotionBoundaryTests(unittest.TestCase):
    """从真实 promote 入口贯穿公开镜像发布器；仅传输边界使用合成替身。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(Path(self.temp.name))
        self.notes = Path(self.temp.name) / "notes"
        self.notes.mkdir()
        (self.notes / "1.2.3.md").write_text(
            "# 1. 1.2.3\n\n前置：Podman。\n\n两步：启动与安装。\n\n限制：合成测试。\n"
        )
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock_path = str(Path(self.temp.name) / "podman.sock")
        self.sock.bind(self.sock_path)
        self.remote_present = False
        self.push_calls = 0
        self.write_failure = None
        self.image_body = json.dumps(
            {
                "schemaVersion": 2,
                "config": {"digest": self.fixture.artifacts[4]["imageDigest"]},
            }
        ).encode()
        self.image_manifest_digest = "sha256:" + sha(self.image_body)
        self.patches = [
            patch.object(p, "ROOT", self.fixture.root),
            patch.object(p, "LICENSE_TABLE", self.fixture.table),
            patch.object(p, "NOTES_ROOT", self.notes),
            patch.object(
                p, "consume_candidate_pass", side_effect=self.fixture.consumer
            ),
            patch.object(
                p,
                "_run_bounded",
                return_value=(json.dumps(self.fixture.identity).encode(), b""),
            ),
            patch.object(p, "_repository", return_value=("o", "r")),
            patch.object(p, "_verify_local_tag"),
            patch.object(p, "_assert_live_promotion_authorization"),
            patch.object(p, "GitHub", FakeGitHub),
            patch.object(registry, "_manifest", side_effect=self.manifest),
            patch.object(registry, "_run", side_effect=self.podman),
            patch.dict(
                os.environ,
                {
                    "GITHUB_TOKEN": "synthetic",
                    "LEXIFLOW_RELEASE_CONFIG_TOKEN": "synthetic-config",
                    "LEXIFLOW_RELEASE_PODMAN_ENDPOINT": "unix://" + self.sock_path,
                },
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.sock.close)
        FakeGitHub.reset()

    def manifest(self, repo, reference, **kwargs):
        if reference == "1.2.3":
            if self.remote_present:
                return (
                    200,
                    {"docker-content-digest": self.image_manifest_digest},
                    self.image_body,
                )
            return 404, {}, b""
        if reference == self.image_manifest_digest:
            return (
                200,
                {"docker-content-digest": self.image_manifest_digest},
                self.image_body,
            )
        raise AssertionError("unexpected synthetic manifest reference")

    def podman(self, argv, *, endpoint, input_stream=None, timeout=300):
        class Facts:
            returncode = 0
            cleanup_succeeded = True
            stdout = b""

        if "inspect" in argv:
            image = self.fixture.artifacts[4]["imageDigest"]
            ident = self.fixture.identity
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
                        "Id": image,
                        "Architecture": "arm64",
                        "Os": "linux",
                        "Config": {"Labels": labels},
                    }
                ]
            ).encode()
        if "load" in argv and self.write_failure == "before":
            raise registry.RegistryError("synthetic-prewrite", "podman")
        if "push" in argv:
            self.push_calls += 1
            if self.write_failure == "before":
                raise registry.RegistryError("synthetic-prewrite", "podman")
            self.remote_present = True
            if self.write_failure == "after":
                raise OSError("synthetic response loss")
            digestfile = Path(
                next(x.split("=", 1)[1] for x in argv if x.startswith("--digestfile="))
            )
            digestfile.write_text(self.image_manifest_digest)
        return Facts()

    def promote(self, *, confirmed_recovery=False):
        return p.promote(
            submission_id=SUBMISSION,
            candidate_directory=self.fixture.candidate,
            publish=True,
            confirmed_recovery=confirmed_recovery,
        )

    def test_public_registry_chain_first_publish_emits_three_release_assets(self):
        result = self.promote()
        self.assertEqual(result["result"], "PASS")
        self.assertTrue(result["published"])
        self.assertEqual(self.push_calls, 1)
        self.assertEqual(len(result["assets"]), 3)
        self.assertTrue(self.remote_present)

    def test_same_digest_recovery_does_not_push_again(self):
        first = self.promote()
        self.assertEqual(first["result"], "PASS")
        self.assertEqual(self.push_calls, 1)
        FakeGitHub.reset()
        recovered = self.promote()
        self.assertEqual(recovered["result"], "PASS")
        self.assertEqual(self.push_calls, 1)

    def test_prewrite_failure_is_blocked_without_push(self):
        self.write_failure = "before"
        with self.assertRaises(p.PromotionError) as raised:
            self.promote()
        self.assertEqual(raised.exception.stage, "registry-podman")
        self.assertEqual(self.push_calls, 0)
        self.assertFalse(self.remote_present)
        event_dir = p._event_directory(
            "1.2.3", self.fixture.proof["candidate"]["candidateSha256"]
        )
        events = [json.loads(item.read_text()) for item in event_dir.glob("*.json")]
        image_events = [item for item in events if item.get("stage") == "ghcr-image"]
        self.assertEqual(
            max(image_events, key=lambda item: item["createdAtEpochNs"])["state"],
            "blocked",
        )
        self.assertFalse(any(call[0] in ("POST", "PATCH") for call in FakeGitHub.calls))

    def test_postwrite_failure_is_unknown(self):
        self.write_failure = "after"
        with self.assertRaises(p.PromotionError) as raised:
            self.promote()
        self.assertEqual(raised.exception.stage, "registry-publish")
        self.assertEqual(raised.exception.registry_role, "runtime-image")
        self.assertEqual(self.push_calls, 1)
        self.assertTrue(self.remote_present)
        event_dir = p._event_directory(
            "1.2.3", self.fixture.proof["candidate"]["candidateSha256"]
        )
        events = [json.loads(item.read_text()) for item in event_dir.glob("*.json")]
        image_events = [item for item in events if item.get("stage") == "ghcr-image"]
        self.assertEqual(
            max(image_events, key=lambda item: item["createdAtEpochNs"])["state"],
            "unknown",
        )
        self.assertFalse(any(call[0] in ("POST", "PATCH") for call in FakeGitHub.calls))

    def test_public_registry_unknown_requires_confirmation_and_recovers_same_image(
        self,
    ):
        self.write_failure = "after"
        with self.assertRaises(p.PromotionError) as raised:
            self.promote()
        self.assertEqual(raised.exception.stage, "registry-publish")
        event_dir = p._event_directory(
            "1.2.3", self.fixture.proof["candidate"]["candidateSha256"]
        )
        unknown_files = [
            item
            for item in event_dir.glob("*.json")
            if json.loads(item.read_bytes()).get("stage") == "ghcr-image"
            and json.loads(item.read_bytes()).get("state") == "unknown"
        ]
        self.assertTrue(unknown_files)
        old_unknown = {item.name: item.read_bytes() for item in unknown_files}
        with self.assertRaisesRegex(
            p.PromotionError, "promotion-recovery-confirmation-required"
        ):
            self.promote()
        self.assertEqual(self.push_calls, 1)
        self.write_failure = None
        FakeGitHub.reset()
        with patch.object(
            registry, "publish_images", wraps=registry.publish_images
        ) as publish_images:
            recovered = self.promote(confirmed_recovery=True)
        self.assertEqual(recovered["result"], "PASS")
        self.assertEqual(self.push_calls, 1)
        self.assertEqual(len(recovered["assets"]), 3)
        self.assertTrue(publish_images.call_args.kwargs["existing_only"])
        self.assertEqual(
            {name: (event_dir / name).read_bytes() for name in old_unknown}, old_unknown
        )

    def test_confirmed_recovery_rejects_missing_or_conflicting_remote_without_push(
        self,
    ):
        self.write_failure = "after"
        with self.assertRaises(p.PromotionError):
            self.promote()
        self.write_failure = None
        self.remote_present = False
        FakeGitHub.reset()
        with self.assertRaises(p.PromotionError):
            self.promote(confirmed_recovery=True)
        self.assertEqual(self.push_calls, 1)
        self.remote_present = True
        FakeGitHub.reset()
        original_manifest = registry._manifest

        def conflicting_manifest(repo, reference, **kwargs):
            if reference == "1.2.3":
                return (
                    200,
                    {"docker-content-digest": "sha256:" + "0" * 64},
                    self.image_body,
                )
            return original_manifest(repo, reference, **kwargs)

        with patch.object(registry, "_manifest", side_effect=conflicting_manifest):
            with self.assertRaises(p.PromotionError):
                self.promote(confirmed_recovery=True)
        self.assertEqual(self.push_calls, 1)


if __name__ == "__main__":
    unittest.main()
