import json
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops/podman/fetch-ecdict.sh"
LOCK = ROOT / "ops/dataset/ecdict-source.lock.json"


class EcdictFetchContractTest(unittest.TestCase):
    def test_script_pins_origin_commit_depth_and_safe_publication(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "https://github.com/skywind3000/ECDICT.git",
            "bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b",
            "fetch --depth=1",
            'remote get-url origin',
            'rev-parse HEAD',
            'mktemp -d',
            'archive_member',
            'manifest.json',
            'stardict.csv_sha256=',
            'chmod 755 "$OUT"',
        ):
            self.assertIn(required, text)

    def test_lock_is_exact_pinned_public_input(self):
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        self.assertEqual(lock["repository_url"], "https://github.com/skywind3000/ECDICT.git")
        self.assertEqual(lock["commit"], "bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b")
        self.assertEqual(set(lock["files"]), {"ecdict.csv", "stardict.7z", "LICENSE"})
        self.assertEqual(lock["archive_member"]["member"], "stardict.csv")

    def test_git_failure_preserves_existing_data_and_handles_spaces(self):
        with tempfile.TemporaryDirectory(prefix="ecdict path ") as tmp:
            root = pathlib.Path(tmp)
            kit = root / "kit space"
            parent = root / "private data"
            (kit / "ops/dataset").mkdir(parents=True)
            (kit / "scripts/environment").mkdir(parents=True)
            (kit / "ops/dataset/ecdict-source.lock.json").write_bytes(LOCK.read_bytes())
            (kit / "scripts/environment/ecdict_bundle.py").write_text("pass\n")
            sentinel = parent / "keep"
            parent.mkdir()
            sentinel.write_text("untouched")
            bindir = root / "bin"
            bindir.mkdir()
            git = bindir / "git"
            git.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$GIT_LOG\"\nexit 19\n")
            git.chmod(0o755)
            log = root / "git.log"
            result = subprocess.run(
                ["/bin/sh", str(SCRIPT), str(kit), str(parent)],
                env={"PATH": str(bindir) + ":/usr/bin:/bin", "GIT_LOG": str(log)},
                capture_output=True, text=True, timeout=5,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(sentinel.read_text(), "untouched")
            self.assertEqual(list(parent.iterdir()), [sentinel])
            calls = log.read_text().splitlines()
            self.assertTrue(calls[0].endswith(" init -q"), calls[0])

    def test_rejects_non_absolute_arguments_without_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(["/bin/sh", str(SCRIPT), "relative", tmp],
                                    capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])

    def test_rejects_bad_lock_digest_before_git_or_output_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            kit = root / "kit"
            parent = root / "output"
            (kit / "ops/dataset").mkdir(parents=True)
            (kit / "scripts/environment").mkdir(parents=True)
            lock = json.loads(LOCK.read_text(encoding="utf-8"))
            lock["files"]["LICENSE"] = "not-a-sha256"
            (kit / "ops/dataset/ecdict-source.lock.json").write_text(json.dumps(lock))
            (kit / "scripts/environment/ecdict_bundle.py").write_text("pass\n")
            parent.mkdir()
            bindir = root / "bin"
            bindir.mkdir()
            git = bindir / "git"
            git.write_text("#!/bin/sh\nprintf invoked > \"$GIT_LOG\"\nexit 19\n")
            git.chmod(0o755)
            log = root / "git.log"
            result = subprocess.run(["/bin/sh", str(SCRIPT), str(kit), str(parent)],
                                    env={"PATH": str(bindir) + ":/usr/bin:/bin", "GIT_LOG": str(log)},
                                    capture_output=True, text=True, timeout=3)
            self.assertFalse(log.exists())
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
