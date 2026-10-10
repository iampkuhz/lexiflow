"""ECDICT 唯一准备流程的隔离测试；不代表公开网络集成。"""

import csv
import hashlib
import io
import json
import os
import pathlib
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from ops.dataset import prepare_ecdict_bundle as bundle

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops/dataset/prepare_ecdict_bundle.py"
LOCK = ROOT / "ops/dataset/ecdict-source.lock.json"


class EcdictFetchContractTest(unittest.TestCase):
    def _kit(self, root):
        kit = root / "kit space"
        (kit / "ops/dataset").mkdir(parents=True)
        (kit / "ops/dataset/ecdict-source.lock.json").write_bytes(LOCK.read_bytes())
        tool = kit / "ops/dataset/prepare_ecdict_bundle.py"
        tool.write_bytes(SCRIPT.read_bytes())
        parent = root / "private data"
        parent.mkdir()
        sentinel = parent / "keep"
        sentinel.write_text("untouched")
        return kit, tool, parent, sentinel

    def _env(self, root, git_body):
        bindir = root / "bin"
        bindir.mkdir()
        git = bindir / "git"
        git.write_text("#!/bin/sh\n" + git_body)
        git.chmod(0o755)
        return {
            "PATH": str(bindir) + ":/usr/bin:/bin",
            "GIT_LOG": str(root / "git.log"),
            "LEXIFLOW_GENERATION_CODE_VERSION": "a" * 64,
            "PYTHONPATH": str(root / "poison"),
        }

    def test_single_python_entrypoint_and_no_legacy_entrypoints(self):
        self.assertFalse((ROOT / "ops/podman/fetch-ecdict.sh").exists())
        self.assertFalse((ROOT / "scripts/environment/ecdict_bundle.py").exists())
        self.assertIn(
            'ENTRYPOINT ["python3", "-I"]',
            (ROOT / "ops/podman/source-tools.Containerfile").read_text(),
        )
        manifest = json.loads((ROOT / "ops/dataset/generation-inputs.json").read_text())
        self.assertIn("ops/dataset/prepare_ecdict_bundle.py", manifest["include"])
        self.assertNotIn("ops/podman/fetch-ecdict.sh", manifest["include"])
        self.assertNotIn("scripts/environment/ecdict_bundle.py", manifest["include"])

    def test_lock_is_exact_pinned_public_input(self):
        lock = bundle._validated_lock()
        self.assertEqual(
            lock["repository_url"], "https://github.com/skywind3000/ECDICT.git"
        )
        self.assertEqual(lock["commit"], "bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b")
        self.assertEqual(set(lock["files"]), {"ecdict.csv", "stardict.7z", "LICENSE"})
        self.assertEqual(lock["archive_member"]["member"], "stardict.csv")

    def test_fetch_uses_only_pinned_origin_commit_depth_and_checks_identity(self):
        with patch.object(bundle, "_run_git") as git:
            git.side_effect = ["", "", "", "", bundle.REPOSITORY_URL, bundle.COMMIT]
            bundle._fetch_source(pathlib.Path("/private/repo"))
            self.assertEqual(
                [call.args[1:] for call in git.call_args_list],
                [
                    ("init", "-q"),
                    ("remote", "add", "origin", bundle.REPOSITORY_URL),
                    ("fetch", "--depth=1", "origin", bundle.COMMIT),
                    ("checkout", "--detach", "FETCH_HEAD"),
                    ("remote", "get-url", "origin"),
                    ("rev-parse", "HEAD"),
                ],
            )
        for identity in ("wrong origin", bundle.REPOSITORY_URL):
            with (
                patch.object(
                    bundle,
                    "_run_git",
                    side_effect=["", "", "", "", identity, "wrong commit"],
                ),
                self.assertRaises(bundle.BundleError),
            ):
                bundle._fetch_source(pathlib.Path("/private/repo"))

    def test_git_failure_preserves_existing_data_and_handles_spaces(self):
        with tempfile.TemporaryDirectory(prefix="ecdict path ") as tmp:
            root = pathlib.Path(tmp).resolve()
            kit, tool, parent, sentinel = self._kit(root)
            env = self._env(root, 'printf "%s\\n" "$*" >> "$GIT_LOG"\nexit 19\n')
            result = subprocess.run(
                check=False,
                args=[sys.executable, "-I", str(tool), str(kit), str(parent)],
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(sentinel.read_text(), "untouched")
            self.assertEqual(list(parent.iterdir()), [sentinel])
            self.assertTrue((root / "git.log").read_text().strip().endswith(" init -q"))

    def test_rejects_non_absolute_arguments_and_old_generator_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            for args in (
                ["relative", tmp],
                ["--source-repo", tmp, "--output", tmp],
                [tmp],
                [tmp, tmp, "extra"],
            ):
                result = subprocess.run(
                    check=False,
                    args=[sys.executable, "-I", str(SCRIPT), *args],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])

    def test_bad_lock_or_missing_generation_never_invokes_git_or_creates_output(self):
        for defect in ("digest", "origin", "commit", "size", "schema", "code-version"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp).resolve()
                kit, tool, parent, sentinel = self._kit(root)
                env = self._env(root, 'printf invoked > "$GIT_LOG"\nexit 19\n')
                lock_path = kit / "ops/dataset/ecdict-source.lock.json"
                lock = json.loads(lock_path.read_text())
                if defect == "digest":
                    lock["files"]["LICENSE"] = "not-a-sha256"
                if defect == "origin":
                    lock["repository_url"] = "file:///private"
                if defect == "commit":
                    lock["commit"] = "a" * 40
                if defect == "size":
                    lock["archive_member"]["bytes"] += 1
                if defect == "schema":
                    lock["schema_version"] = 99
                if defect == "code-version":
                    env.pop("LEXIFLOW_GENERATION_CODE_VERSION")
                lock_path.write_text(json.dumps(lock))
                result = subprocess.run(
                    check=False,
                    args=[sys.executable, "-I", str(tool), str(kit), str(parent)],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertFalse((root / "git.log").exists())
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(list(parent.iterdir()), [sentinel])

    def test_signal_cleans_only_owned_scratch_and_owned_git_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            kit, tool, parent, sentinel = self._kit(root)
            env = self._env(root, 'printf "%s" "$$" > "$GIT_LOG"\nsleep 60\n')
            process = subprocess.Popen(
                args=[sys.executable, "-I", str(tool), str(kit), str(parent)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 5
                while not (root / "git.log").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((root / "git.log").exists())
                child = int((root / "git.log").read_text())
                process.send_signal(signal.SIGTERM)
                process.communicate(timeout=5)
                self.assertEqual(process.returncode, 143)
                self.assertEqual(list(parent.iterdir()), [sentinel])
                self.assertEqual(sentinel.read_text(), "untouched")
                with self.assertRaises(ProcessLookupError):
                    os.kill(child, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
                elif process.stdout and not process.stdout.closed:
                    process.communicate()

    @contextmanager
    def _generated_fixture(self, root):
        # Tiny synthetic bytes prove code paths, not public source retrieval.
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=bundle.HEADER, lineterminator="\n")
        writer.writeheader()
        for word in ("ordinary", *sorted(bundle.CURATED)):
            row = {key: "" for key in bundle.HEADER}
            row.update(word=word, translation="中文释义", bnc="0", frq="0", collins="0")
            writer.writerow(row)
        source = stream.getvalue().encode()
        license_data = b"synthetic public license"
        lock = json.loads(LOCK.read_text())
        lock["files"]["LICENSE"] = hashlib.sha256(license_data).hexdigest()
        lock["archive_member"]["bytes"] = len(source)
        lock["archive_member"]["sha256"] = hashlib.sha256(source).hexdigest()
        repo = root / "repo"
        repo.mkdir()
        (repo / "ecdict.csv").write_bytes(source)
        (repo / "LICENSE").write_bytes(license_data)
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(bundle, "ARCHIVE_MEMBER_BYTES", len(source))
            )
            stack.enter_context(
                patch.object(
                    bundle, "ARCHIVE_MEMBER_SHA256", hashlib.sha256(source).hexdigest()
                )
            )
            stack.enter_context(
                patch.object(
                    bundle, "_source_lock", return_value={"files": lock["files"]}
                )
            )
            stack.enter_context(
                patch.object(
                    bundle,
                    "_read_full_csv",
                    return_value=(source, hashlib.sha256(source).hexdigest()),
                )
            )
            stack.enter_context(patch.object(bundle, "_verify_sources"))
            archive = root / "bundle.zip"
            bundle.build(repo, archive, generation_code_version="a" * 64)
            yield repo, source, lock, archive

    def test_generated_bundle_checks_members_license_and_generation_proof(self):
        defects = (
            None,
            "duplicate",
            "extra",
            "symlink",
            "size",
            "digest",
            "license",
            "code",
            "proof",
            "generator",
            "upstream",
            "header",
        )
        for defect in defects:
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp).resolve()
                with self._generated_fixture(root) as (_repo, _source, lock, archive):
                    with zipfile.ZipFile(archive) as source:
                        members = {
                            i.filename: source.read(i.filename)
                            for i in source.infolist()
                        }
                    manifest = json.loads(members["manifest.json"])
                    if defect == "digest":
                        manifest["members"]["stardict.csv"]["sha256"] = "0" * 64
                    if defect == "license":
                        members["LICENSE"] = b"tampered"
                    if defect == "code":
                        manifest["generation"]["codeVersion"] = "b" * 64
                    if defect == "proof":
                        manifest["generation"]["generationVersion"] = "b" * 64
                    if defect == "generator":
                        manifest["generator_sha256"] = "b" * 64
                    if defect == "upstream":
                        manifest["upstream"]["commit"] = "bad"
                    if defect == "header":
                        manifest["csv_columns"] = ["wrong"]
                    members["manifest.json"] = json.dumps(manifest).encode()
                    if defect == "extra":
                        members["unexpected"] = b"bad"
                    with zipfile.ZipFile(archive, "w") as target:
                        for name, data in members.items():
                            info = bundle._zip_info(name)
                            if defect == "symlink" and name == "LICENSE":
                                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                            target.writestr(info, data)
                        if defect == "duplicate":
                            import warnings

                            with warnings.catch_warnings():
                                warnings.simplefilter("ignore", UserWarning)
                                target.writestr("LICENSE", members["LICENSE"])
                    with patch.object(
                        bundle,
                        "MAX_CSV_BYTES",
                        1 if defect == "size" else bundle.MAX_CSV_BYTES,
                    ):
                        if defect is None:
                            raw, checked = bundle._verified_bundle(
                                archive, lock, "a" * 64
                            )
                            self.assertEqual(raw["LICENSE"], members["LICENSE"])
                            self.assertEqual(
                                checked["generation"]["codeVersion"], "a" * 64
                            )
                        else:
                            with self.assertRaises(bundle.BundleError):
                                bundle._verified_bundle(archive, lock, "a" * 64)

    def test_extraction_is_bounded_and_checks_size_digest_and_exit(self):
        for data, exit_code, message in (
            (b"fixed", 0, None),
            (b"fixedX", 0, "exceeds"),
            (b"fix", 0, "verification"),
            (b"wrong", 0, "verification"),
            (b"fixed", 7, "verification"),
        ):
            with (
                self.subTest(data=data, exit_code=exit_code),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = pathlib.Path(tmp).resolve()
                tool = root / "7z"
                tool.write_text(
                    f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write({data!r})\nsys.exit({exit_code})\n"
                )
                tool.chmod(0o755)
                lock = {
                    "archive_member": {
                        "bytes": 5,
                        "sha256": hashlib.sha256(b"fixed").hexdigest(),
                    }
                }
                with (
                    patch.object(bundle, "_source_lock"),
                    patch.object(bundle.shutil, "which", return_value=str(tool)),
                ):
                    if message is None:
                        bundle._extract_source(root, lock)
                        self.assertEqual((root / "stardict.csv").read_bytes(), b"fixed")
                    else:
                        with self.assertRaisesRegex(bundle.BundleError, message):
                            bundle._extract_source(root, lock)

    def test_archive_lock_failure_prevents_extractor_invocation(self):
        with (
            patch.object(
                bundle, "_source_lock", side_effect=bundle.BundleError("hash mismatch")
            ),
            patch.object(bundle.subprocess, "Popen") as process,
        ):
            with self.assertRaises(bundle.BundleError):
                bundle._extract_source(pathlib.Path("/private"), {})
            process.assert_not_called()

    def test_prepare_atomically_publishes_only_verified_output_and_preserves_caller(
        self,
    ):
        for failure in (None, "fetch", "extract", "build", "verify", "rename"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp).resolve()
                with self._generated_fixture(root) as (
                    _repo,
                    _source,
                    lock,
                    fixture_zip,
                ):
                    parent = root / "outputs"
                    parent.mkdir()
                    sentinel = parent / "caller-existing"
                    sentinel.write_text("keep")

                    def generate(
                        _repo, output, parent=parent, sentinel=sentinel, **_kwargs
                    ):
                        output.write_bytes(fixture_zip.read_bytes())
                        output.with_suffix(".report").mkdir()
                        self.assertEqual(
                            [p for p in parent.iterdir() if not p.name.startswith(".")],
                            [sentinel],
                        )

                    with ExitStack() as stack:
                        stack.enter_context(
                            patch.dict(
                                os.environ,
                                {"LEXIFLOW_GENERATION_CODE_VERSION": "a" * 64},
                            )
                        )
                        stack.enter_context(
                            patch.object(bundle, "_validated_lock", return_value=lock)
                        )
                        for name, target in (
                            ("fetch", "_fetch_source"),
                            ("extract", "_extract_source"),
                        ):
                            stack.enter_context(
                                patch.object(
                                    bundle,
                                    target,
                                    side_effect=bundle.BundleError(name)
                                    if failure == name
                                    else None,
                                )
                            )
                        stack.enter_context(
                            patch.object(
                                bundle,
                                "build",
                                side_effect=bundle.BundleError("build")
                                if failure == "build"
                                else generate,
                            )
                        )
                        if failure == "verify":
                            stack.enter_context(
                                patch.object(
                                    bundle,
                                    "_verified_bundle",
                                    side_effect=bundle.BundleError("verify"),
                                )
                            )
                        if failure == "rename":
                            stack.enter_context(
                                patch.object(
                                    pathlib.Path,
                                    "rename",
                                    side_effect=OSError("publication failure"),
                                )
                            )
                        if failure is None:
                            published = bundle.prepare(ROOT, parent)
                            self.assertEqual(
                                {p.name for p in published.iterdir()},
                                {
                                    "stardict.csv",
                                    "ecdict-core-source.zip",
                                    "source-summary.json",
                                },
                            )
                            self.assertEqual(published.stat().st_mode & 0o777, 0o755)
                            summary = json.loads(
                                (published / "source-summary.json").read_text()
                            )
                            self.assertEqual(
                                summary["csv_sha256"],
                                hashlib.sha256(
                                    (published / "stardict.csv").read_bytes()
                                ).hexdigest(),
                            )
                        else:
                            with self.assertRaises((bundle.BundleError, OSError)):
                                bundle.prepare(ROOT, parent)
                            self.assertEqual(list(parent.iterdir()), [sentinel])
                        self.assertEqual(sentinel.read_text(), "keep")
                        self.assertFalse(
                            any(
                                p.name.startswith(".ecdict-source.")
                                for p in parent.iterdir()
                            )
                        )


if __name__ == "__main__":
    unittest.main()
