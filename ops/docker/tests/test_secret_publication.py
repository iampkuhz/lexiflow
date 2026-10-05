"""使用真实状态、记录和锁合同验证私有秘密原子发布。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / "ops/release/lifecycle-state.sh"
LIFECYCLE = ROOT / "ops/release/lifecycle.sh"
KEY = "a" * 64
INSTALL = "b" * 32
TOKEN = "c" * 32
VALUE = b"0123456789abcdef" * 4


class SecretPublicationTest(unittest.TestCase):
    """所有故障都限制在独立临时安装树。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="lf secret ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "install root"
        self.release = self.root / "releases" / KEY
        self.release.mkdir(parents=True)
        (self.root / ".lock").mkdir()
        (self.root / ".lock/token").write_text(TOKEN + "\n")
        (self.root / "owner").write_text(INSTALL + "\n")
        (self.root / "state").write_text(
            f"schema=lexiflow-installation-v1\ninstallation_id={INSTALL}\nphase=idle\n"
            "active=none\nprevious=none\ncandidate=none\ndeleting=none\nresume=none\n"
        )
        self.entry = self.base / "entry.sh"
        library = LIFECYCLE.read_text().split('lf_user_main "$@" ||', 1)[0]
        self.entry.write_text(
            "#!/bin/sh\n" + STATE.read_text() + "\n" + library + "\n"
            f"LF_ROOT='{self.root}'\nLF_INSTALL_ID={INSTALL}\nLF_LOCK_TOKEN={TOKEN}\n"
            f"LF_RELEASE_KEY={KEY}\nLF_PLATFORM=linux/amd64\n"
            f"LF_PROJECT=lf_{INSTALL}_{KEY[:16]}\n"
            "lf_secret_write_pending() {\n"
            "  case ${LF_TEST_WRITE:-normal} in\n"
            "    partial) printf partial; return 9;;\n"
            "    short) printf partial; return 0;;\n"
            "    lose_lock) printf '%s' \"$1\"; command rm \"$LF_ROOT/.lock/token\"; return 0;;\n"
            "    *) printf '%s' \"$1\";;\n"
            "  esac\n}\n"
            "if [ \"${LF_TEST_RANDOM:-}\" = fail ]; then lf_random_hex() { return 9; }; fi\n"
            "if [ \"${LF_TEST_LINK:-}\" = fail ]; then ln() { return 9; }; fi\n"
            "if [ \"${LF_TEST_CLEAN:-}\" = fail ]; then\n"
            "  rm() { case \"$1\" in *.pending) return 9;; esac; command rm \"$@\"; };\n"
            "fi\n"
            "case $1 in\n"
            "  sizefail) wc() { printf '64'; return 9; }; lf_secret_file_verify \"$LF_ROOT/releases/$LF_RELEASE_KEY/postgres-password\";;\n"
            "  make) lf_make_record;;\n"
            "  publish) lf_secret_publish \"$LF_ROOT/releases/$LF_RELEASE_KEY\";;\n"
            "  wrongdir) lf_secret_publish \"$LF_ROOT/releases/other\";;\n"
            "  verify) lf_secrets_verify \"$LF_RELEASE_KEY\";;\n"
            "  dircheck) lf_release_dir_check \"$LF_ROOT/releases/$LF_RELEASE_KEY\" record;;\n"
            "  tombclean) lf_tombstone_clean \"$LF_RELEASE_KEY\";;\n"
            "esac\nexit $?\n"
        )
        self.entry.chmod(0o700)
        self.record = self.release / "record"
        self.record.write_text(
            f"key={KEY}\nentry={self.entry}\nsha256={hashlib.sha256(self.entry.read_bytes()).hexdigest()}\n"
            f"platform=linux/amd64\nproject=lf_{INSTALL}_{KEY[:16]}\n"
        )

    def run_shell(self, action: str = "make", **flags: str) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            ["/bin/sh", str(self.entry), action], capture_output=True, timeout=8,
            env={**os.environ, **{f"LF_TEST_{key.upper()}": value for key, value in flags.items()}},
        )
        self.assertEqual(result.stdout + result.stderr, b"", "private tool output escaped")
        return result

    def secret(self, name: str) -> Path:
        return self.release / name

    def write_secret(self, name: str, value: bytes = VALUE) -> Path:
        path = self.secret(name)
        path.write_bytes(value)
        path.chmod(0o600)
        return path

    def test_first_repeat_verify_and_missing_second_recovery(self) -> None:
        self.assertEqual(self.run_shell().returncode, 0)
        before = [self.secret(name).read_bytes() for name in ("postgres-password", "app-password")]
        self.assertEqual(len(before), 2)
        self.assertTrue(all(len(value) == 64 and set(value) <= set(b"0123456789abcdef") for value in before))
        self.assertTrue(all(self.secret(name).stat().st_mode & 0o777 == 0o600 for name in ("postgres-password", "app-password")))
        self.secret("app-password").unlink()
        self.assertEqual(self.run_shell().returncode, 0)
        self.assertEqual(self.run_shell().returncode, 0)
        self.assertEqual(self.run_shell("verify").returncode, 0)
        self.assertEqual(self.secret("postgres-password").read_bytes(), before[0])
        self.assertEqual(len(self.secret("app-password").read_bytes()), 64)

    def test_partial_and_zero_short_write_then_retry(self) -> None:
        for flag in ("partial", "short"):
            with self.subTest(flag=flag):
                self.assertNotEqual(self.run_shell(write=flag).returncode, 0)
                self.assertFalse(self.secret("postgres-password").exists())
                self.assertTrue(self.secret(".postgres-password.pending").exists())
                self.assertEqual(self.run_shell().returncode, 0)
                self.secret("postgres-password").unlink()
                self.secret("app-password").unlink()

    def test_random_and_link_failure(self) -> None:
        self.assertNotEqual(self.run_shell(random="fail").returncode, 0)
        self.assertFalse(self.secret("postgres-password").exists())
        self.assertNotEqual(self.run_shell(link="fail").returncode, 0)
        self.assertFalse(self.secret("postgres-password").exists())
        self.assertEqual(self.run_shell().returncode, 0)

    def test_clean_failure_retries_without_changing_final(self) -> None:
        self.assertNotEqual(self.run_shell(clean="fail").returncode, 0)
        original = self.secret("postgres-password").read_bytes()
        self.assertTrue(self.secret(".postgres-password.pending").exists())
        self.assertEqual(self.run_shell().returncode, 0)
        self.assertEqual(self.secret("postgres-password").read_bytes(), original)
        self.assertFalse(self.secret(".postgres-password.pending").exists())

    def test_bad_final_and_directory_collision_not_overwritten(self) -> None:
        bad = self.write_secret("postgres-password", b"a" * 63)
        self.assertNotEqual(self.run_shell().returncode, 0)
        self.assertEqual(bad.read_bytes(), b"a" * 63)
        bad.unlink()
        bad.mkdir()
        self.assertNotEqual(self.run_shell().returncode, 0)
        self.assertEqual(list(bad.iterdir()), [])

    def test_failed_size_tool_does_not_validate_even_with_correct_output(self) -> None:
        self.write_secret("postgres-password")
        before = self.secret("postgres-password").read_bytes()
        self.assertNotEqual(self.run_shell("sizefail").returncode, 0)
        self.assertEqual(self.secret("postgres-password").read_bytes(), before)

    def test_pending_type_mode_and_leftover_recovery(self) -> None:
        pending = self.secret(".postgres-password.pending")
        pending.symlink_to(self.record)
        self.assertNotEqual(self.run_shell().returncode, 0)
        pending.unlink()
        pending.write_bytes(b"partial")
        pending.chmod(0o644)
        self.assertNotEqual(self.run_shell().returncode, 0)
        pending.chmod(0o600)
        self.assertEqual(self.run_shell().returncode, 0)
        self.assertFalse(pending.exists())

    def test_real_record_lock_and_directory_boundaries(self) -> None:
        self.assertNotEqual(self.run_shell("wrongdir").returncode, 0)
        record = self.record.read_bytes()
        self.record.write_bytes(record.replace(b"key=", b"key=x", 1))
        self.assertNotEqual(self.run_shell().returncode, 0)
        self.record.write_bytes(record)
        (self.root / ".lock/token").write_text("d" * 32 + "\n")
        self.assertNotEqual(self.run_shell().returncode, 0)
        (self.root / ".lock/token").write_text(TOKEN + "\n")
        self.assertNotEqual(self.run_shell(write="lose_lock").returncode, 0)
        self.assertFalse(self.secret("postgres-password").exists())

    def test_verify_is_read_only(self) -> None:
        self.assertNotEqual(self.run_shell("verify").returncode, 0)
        self.assertEqual(self.run_shell().returncode, 0)
        self.secret(".postgres-password.pending").write_bytes(b"partial")
        self.secret(".postgres-password.pending").chmod(0o600)
        self.assertEqual(self.run_shell("verify").returncode, 0)
        self.assertTrue(self.secret(".postgres-password.pending").exists())

    def test_pending_directory_and_tombstone_cleanup(self) -> None:
        self.assertEqual(self.run_shell().returncode, 0)
        pending = self.secret(".postgres-password.pending")
        pending.write_bytes(b"partial")
        pending.chmod(0o600)
        self.assertEqual(self.run_shell("dircheck").returncode, 0)
        pending.chmod(0o644)
        self.assertNotEqual(self.run_shell("dircheck").returncode, 0)
        pending.chmod(0o600)
        tomb = self.root / "releases" / (".deleted-" + KEY)
        self.release.rename(tomb)
        self.assertEqual(self.run_shell("tombclean").returncode, 0)
        self.assertFalse(tomb.exists())

    def test_bad_tombstone_pending_blocks_cleanup(self) -> None:
        self.assertEqual(self.run_shell().returncode, 0)
        pending = self.secret(".app-password.pending")
        pending.symlink_to(self.record)
        tomb = self.root / "releases" / (".deleted-" + KEY)
        self.release.rename(tomb)
        self.assertNotEqual(self.run_shell("tombclean").returncode, 0)
        self.assertTrue(tomb.exists())


if __name__ == "__main__":
    unittest.main()
