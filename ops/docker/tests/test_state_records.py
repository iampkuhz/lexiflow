"""发行状态与记录私有文件边界的 POSIX shell 合成测试。"""
from __future__ import annotations

import hashlib
import os
import shlex
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / "ops/release/lifecycle-state.sh"
LIFECYCLE = ROOT / "ops/release/lifecycle.sh"
KEY = "a" * 64
TOKEN = "b" * 32
INSTALL = "c" * 32


class StateRecordTests(unittest.TestCase):
    """以全新临时安装目录验证状态、路径与发行记录。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="lf state records ")
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "install root"
        self.root.mkdir()
        (self.root / "releases" / KEY).mkdir(parents=True)
        (self.root / ".lock").mkdir()
        (self.root / "owner").write_text(INSTALL + "\n")
        (self.root / ".lock/token").write_text(TOKEN + "\n")
        self.entry = self.base / "test.sh"
        self.script = self.base / "test.sh"
        lifecycle = LIFECYCLE.read_text().split("lf_load_release()", 1)[0]
        self.script.write_text(
            "#!/bin/sh\nset -eu\n" + STATE.read_text() + "\n" + lifecycle + "\n"
            f"LF_ROOT='{self.root!s}'\nLF_INSTALL_ID={INSTALL}\nLF_LOCK_TOKEN={TOKEN}\n"
            f"LF_RELEASE_KEY={KEY}\nLF_PLATFORM=linux/amd64\n"
            f"LF_PROJECT=lf_{INSTALL}_{KEY[:16]}\n"
            f"lf_abs_root \"$1\" || exit 21\n"
            "lf_make_record\n"
        )
        self.script.chmod(0o700)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_script(self, argument: str | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["/bin/sh", str(self.script), argument or str(self.root)], text=True, capture_output=True, timeout=10, env={**os.environ, **(env or {})})

    def seed_record(self) -> bytes:
        digest = hashlib.sha256(self.entry.read_bytes()).hexdigest()
        rec = (f"key={KEY}\nentry={self.entry}\nsha256={digest}\nplatform=linux/amd64\nproject=lf_{INSTALL}_{KEY[:16]}\n").encode()
        path = self.root / "releases" / KEY / "record"
        path.write_bytes(rec)
        return rec

    def state_shell(self, body: str) -> subprocess.CompletedProcess[str]:
        prefix = STATE.read_text() + "\n" + f"LF_ROOT={shlex.quote(str(self.root))}\nLF_INSTALL_ID={INSTALL}\nLF_LOCK_TOKEN={TOKEN}\n"
        return subprocess.run(["/bin/sh", "-c", prefix + body], text=True, capture_output=True, timeout=10)

    def state_bytes(self, phase: str = "idle", active: str = "none", previous: str = "none", candidate: str = "none", deleting: str = "none", resume: str = "none") -> bytes:
        return (f"schema=lexiflow-installation-v1\ninstallation_id={INSTALL}\nphase={phase}\nactive={active}\nprevious={previous}\ncandidate={candidate}\ndeleting={deleting}\nresume={resume}\n").encode()

    def test_identity_accepts_only_exact_ascii_lowercase_hex(self) -> None:
        for function, length in (("lf_valid_key", 64), ("lf_valid_token", 32)):
            for value in (("0123456789abcdef" * 4)[:length], "a" * length):
                self.assertEqual(self.state_shell(f"{function} {shlex.quote(value)}").returncode, 0)
            for value in ("", "a" * (length - 1), "a" * (length + 1),
                          "A" * length, "é" * length, "a" * (length - 1) + ":",
                          "a" * (length - 1) + "\n", "ａ" * length):
                with self.subTest(function=function, value=value):
                    result = self.state_shell(f"{function} {shlex.quote(value)}")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout + result.stderr, "")

    def test_engine_id_uses_bounded_ascii_identity_alphabet(self) -> None:
        for value in ("a", "x" * 128, "Docker.Engine-01:local_2"):
            result = self.state_shell(f"lf_valid_engine_id {shlex.quote(value)}")
            self.assertEqual(result.returncode, 0)
        for value in ("", "x" * 129, "x y", "x\n", "é", "a/b", "a\\b"):
            with self.subTest(value=value):
                result = self.state_shell(f"lf_valid_engine_id {shlex.quote(value)}")
                self.assertNotEqual(result.returncode, 0)

    def test_engine_metadata_and_exact_record_rejection(self) -> None:
        file = self.root / "engine"
        original = b"schema=lexiflow-engine-v1\nendpoint=unix:///synthetic.sock\ndaemon_id=synthetic-id\n"
        file.write_bytes(original)
        file.chmod(0o600)
        self.assertEqual(self.state_shell("lf_engine_read").returncode, 0)
        for mode in (0o644, 0o400, 0o700):
            file.chmod(mode)
            self.assertNotEqual(self.state_shell("lf_engine_read").returncode, 0)
        file.chmod(0o600)
        other = self.root / "engine-link"
        os.link(file, other)
        self.assertNotEqual(self.state_shell("lf_engine_read").returncode, 0)
        other.unlink()
        for invalid in (original + b"x", original[:-1], original + b"\n", b"x" * 4097):
            file.write_bytes(invalid)
            self.assertNotEqual(self.state_shell("lf_engine_read").returncode, 0)
        file.write_bytes(original)
        file.rename(other)
        file.symlink_to(other)
        self.assertNotEqual(self.state_shell("lf_engine_read").returncode, 0)
        file.unlink()
        other.rename(file)
        for metadata in ("", "1:600:", "2:600:80", "1:644:80", "1:600:4097",
                         "1:600:-1", "1:600:80:extra", "1:600:8x", "1:600:9" * 100):
            result = self.state_shell(f"stat() {{ printf '%s' {shlex.quote(metadata)}; }}; lf_engine_read")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout + result.stderr, "")
        # Each supported stat format is exercised, with a failing GNU probe for BSD.
        for flavor, expected in (("-c", "%h:%a:%s"), ("-f", "%l:%Lp:%z")):
            result = self.state_shell(
                f'stat() {{ [ "$1" = {shlex.quote(flavor)} ] && [ "$2" = {shlex.quote(expected)} ] '
                f'|| return 1; printf "%s\\n" 1:600:{len(original)}; }}; lf_engine_read'
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        result = self.state_shell("stat() { return 1; }; lf_engine_read")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, "")

    def test_invalid_identity_in_owner_state_and_lock_does_not_load(self) -> None:
        state = self.root / "state"
        owner = self.root / "owner"
        lock = self.root / ".lock/token"
        state.write_bytes(self.state_bytes())
        for value in ("A" * 32, "é" * 32):
            owner.write_text(value + "\n")
            state.write_bytes(self.state_bytes().replace(INSTALL.encode(), value.encode()))
            result = self.state_shell('LF_INSTALL_ID=sentinel; if lf_state_load; then exit 91; fi; [ "$LF_INSTALL_ID" = sentinel ]')
            self.assertEqual(result.returncode, 0, result.stderr)
            lock.write_text(value + "\n")
            result = self.state_shell(f"LF_LOCK_TOKEN={shlex.quote(value)}; lf_lock_check")
            self.assertNotEqual(result.returncode, 0)
        owner.write_text(INSTALL + "\n")
        for key in ("A" * 64, "é" * 64):
            state.write_bytes(self.state_bytes(active=key))
            result = self.state_shell('LF_ACTIVE=sentinel; if lf_state_load; then exit 91; fi; [ "$LF_ACTIVE" = sentinel ]')
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_random_and_digest_reject_non_ascii_or_failed_tool_output(self) -> None:
        for value, code in (("A" * 64, 0), ("é" * 64, 0), ("a" * 63, 0), ("a" * 64, 1)):
            with self.subTest(value=value, code=code):
                quoted = shlex.quote(value)
                for body in (f"od() {{ printf '%s' {quoted}; return {code}; }}; lf_random_hex 32",
                             f"sha256sum() {{ printf '%s  synthetic' {quoted}; return {code}; }}; lf_sha synthetic"):
                    result = self.state_shell(body)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout + result.stderr, "")
        value = "abcdef0123456789" * 4
        result = self.state_shell(f"od() {{ printf '%s' {value}; }}; lf_random_hex 32")
        self.assertEqual((result.returncode, result.stdout), (0, value + "\n"))
        result = self.state_shell(f"sha256sum() {{ printf '%s  synthetic' {value}; }}; lf_sha synthetic")
        self.assertEqual((result.returncode, result.stdout), (0, value + "\n"))

    def test_state_load_exact_bytes_and_atomic_failure(self) -> None:
        path = self.root / "state"
        good = self.state_bytes()
        path.write_bytes(good)
        self.assertEqual(self.state_shell("lf_state_load").returncode, 0)
        variants = [good[:-1], good + b"extra\n", good + b"\0", good.replace(b"\n", b"\r\n"), good.replace(b"phase=idle", b"phase=bad"), good.replace(b"candidate=none", b"candidate=" + KEY.encode()), good + b"x" * 1024]
        for bad in variants:
            path.write_bytes(bad)
            result = self.state_shell('LF_INSTALL_ID=sentinel; LF_ACTIVE=sentinel; if lf_state_load; then exit 91; fi; [ "$LF_ACTIVE:$LF_INSTALL_ID" = sentinel:sentinel ]')
            self.assertEqual(result.returncode, 0, result.stderr)
        path.write_bytes(good)
        collision = self.root / f".state-{TOKEN}"
        collision.write_bytes(b"other")
        self.assertNotEqual(self.state_shell("lf_state_write stopped none none none none none").returncode, 0)
        self.assertEqual(path.read_bytes(), good)
        self.assertEqual(collision.read_bytes(), b"other")
        collision.unlink()
        self.assertEqual(self.state_shell("lf_state_write stopped none none none none none").returncode, 0)
        self.assertEqual(path.read_bytes(), self.state_bytes(phase="stopped"))
        self.assertFalse(list(self.root.glob(".state-*")))

    def test_state_combinations_and_lock_exactness(self) -> None:
        path = self.root / "state"
        key2 = "d" * 64
        good = [self.state_bytes("idle", KEY), self.state_bytes("stopped", KEY), self.state_bytes("prepared", KEY, "none", key2), self.state_bytes("switching", KEY, key2, key2), self.state_bytes("deleting", KEY, key2, "none", key2, "idle")]
        for data in good:
            path.write_bytes(data)
            self.assertEqual(self.state_shell("lf_state_load").returncode, 0)
        bad = [self.state_bytes("idle", KEY, "none", key2), self.state_bytes("prepared", KEY, key2, key2), self.state_bytes("switching", KEY, "none", KEY), self.state_bytes("switching", KEY, key2, "e" * 64), self.state_bytes("deleting", KEY, "none", "none", KEY, "idle")]
        for data in bad:
            path.write_bytes(data)
            self.assertNotEqual(self.state_shell("lf_state_load").returncode, 0)
        token = self.root / ".lock/token"
        for data in (TOKEN.encode(), (TOKEN + "\r\n").encode(), (TOKEN + "\n\n").encode(), (TOKEN + "\n\0").encode()):
            token.write_bytes(data)
            self.assertNotEqual(self.state_shell("lf_lock_check").returncode, 0)

    def test_atomic_state_failures_preserve_old_bytes_and_clean_owned_temporary(self) -> None:
        state = self.root / "state"
        good = self.state_bytes()
        state.write_bytes(good)
        for failure in (
            'lf_state_text() { printf partial; return 1; }',
            'mv() { printf private-error >&2; return 1; }',
            'lf_state_parse() { return 1; }',
        ):
            result = self.state_shell(failure + '\nlf_state_write stopped none none none none none')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(state.read_bytes(), good)
            self.assertFalse(list(self.root.glob(".state-*")))
            self.assertEqual(result.stdout + result.stderr, "")
        state.unlink()
        missing = self.base / "must-not-create"
        state.symlink_to(missing)
        self.assertNotEqual(self.state_shell("lf_state_write stopped none none none none none").returncode, 0)
        self.assertTrue(state.is_symlink())
        self.assertFalse(missing.exists())

    def test_lock_traps_release_only_on_ordinary_exit(self) -> None:
        lock = self.root / ".lock"
        lock.joinpath("token").unlink()
        lock.rmdir()
        result = self.state_shell('lf_lock_acquire; exit 0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(lock.exists())

        result = self.state_shell('lf_lock_acquire; exit 7')
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertFalse(lock.exists())

    def test_lock_signal_exit_preserves_journal_and_blocks_followup(self) -> None:
        state = self.root / "state"
        original = self.state_bytes(phase="stopped", active=KEY)
        state.write_bytes(original)
        # setUp 的锁只是常规测试夹具；此用例必须先释放它才能真实获取锁。
        (self.root / ".lock" / "token").unlink()
        (self.root / ".lock").rmdir()
        ready = self.base / "locked.ready"
        sentinel = self.base / "sentinel"
        source = STATE.read_text() + "\n" + f"LF_ROOT={shlex.quote(str(self.root))}\nLF_INSTALL_ID={INSTALL}\n"

        for sig, status in ((signal.SIGHUP, 129), (signal.SIGINT, 130), (signal.SIGTERM, 143)):
            with self.subTest(signal=sig):
                proc = subprocess.Popen(
                    ["/bin/sh", "-c", source +
                     f"lf_lock_acquire || exit 90; : > {shlex.quote(str(ready))}; read -r _; : > {shlex.quote(str(sentinel))}"],
                    text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                try:
                    # 以锁后创建的 ready 文件握手，不以 sleep 猜测启动时序。
                    import time
                    deadline = time.monotonic() + 5
                    while not ready.exists() and time.monotonic() < deadline and proc.poll() is None:
                        time.sleep(0.01)
                    self.assertTrue(ready.exists(), "shell 未在限定时间内取得锁")
                    os.kill(proc.pid, sig)
                    stdout, stderr = proc.communicate(timeout=5)
                    self.assertEqual(proc.returncode, status, stderr)
                    self.assertFalse(sentinel.exists(), stdout + stderr)
                    self.assertEqual(state.read_bytes(), original)
                    self.assertTrue((self.root / ".lock" / "token").is_file())
                    second = self.state_shell('lf_lock_acquire')
                    self.assertNotEqual(second.returncode, 0, second.stderr)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.communicate(timeout=5)
                    ready.unlink(missing_ok=True)
                    sentinel.unlink(missing_ok=True)
                    # 仅清理由该用例持有、且在当前临时根目录内的锁。
                    token = self.root / ".lock" / "token"
                    if token.exists():
                        token.unlink()
                        token.parent.rmdir()

        # SIGKILL 不可捕获；只杀本用例创建并由 Popen 持有的 shell PID。
        proc = subprocess.Popen(
            ["/bin/sh", "-c", source +
             f"lf_lock_acquire || exit 90; : > {shlex.quote(str(ready))}; read -r _; : > {shlex.quote(str(sentinel))}"],
            text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            import time
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline and proc.poll() is None:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "shell 未在限定时间内取得锁")
            os.kill(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, -signal.SIGKILL)
            self.assertFalse(sentinel.exists())
            self.assertEqual(state.read_bytes(), original)
            self.assertTrue((self.root / ".lock" / "token").is_file())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)
            ready.unlink(missing_ok=True)
            sentinel.unlink(missing_ok=True)
            token = self.root / ".lock" / "token"
            if token.exists():
                token.unlink()
                token.parent.rmdir()

    def test_secret_permission_detection_discards_failed_platform_output(self) -> None:
        self.assertEqual(self.run_script().returncode, 0)
        tools = self.base / "stat-tools"
        tools.mkdir()
        stub = tools / "stat"
        stub.write_text('#!/bin/sh\nif [ "$1" = -c ]; then printf "partial failed output\\n"; exit 1; fi\nprintf "600\\n"\n')
        stub.chmod(0o700)
        env = {"PATH": str(tools) + os.pathsep + os.environ["PATH"]}
        self.assertEqual(self.run_script(env=env).returncode, 0)
        stub.write_text('#!/bin/sh\nprintf "644\\n"\n')
        self.assertNotEqual(self.run_script(env=env).returncode, 0)

    def test_symlink_ancestors_and_record_identity(self) -> None:
        self.assertEqual(self.run_script().returncode, 0)
        record = self.root / "releases" / KEY / "record"
        valid = record.read_bytes()
        for malformed in (valid.replace(b"platform=linux/amd64", b"platform=linux/other"), valid.replace(b"project=lf_", b"project=xx_"), valid + b"\n", valid + b"\0"):
            record.write_bytes(malformed)
            self.assertNotEqual(self.run_script().returncode, 0)
            self.assertEqual(record.read_bytes(), malformed)
        record.write_bytes(valid)
        releases = self.root / "releases"
        releases.rename(self.root / "releases-real")
        releases.symlink_to(self.root / "releases-real", target_is_directory=True)
        self.assertNotEqual(self.run_script().returncode, 0)
        releases.unlink()
        (self.root / "releases-real").rename(releases)
        ancestor = self.base / "linked"
        ancestor.symlink_to(self.root, target_is_directory=True)
        self.assertNotEqual(self.run_script(str(ancestor)).returncode, 0)

    def test_owner_state_symlink_and_short_random_retry(self) -> None:
        self.assertEqual(self.run_script().returncode, 0)
        state = self.root / "state"
        state.write_bytes(self.state_bytes())
        owner = self.root / "owner"
        for bad in ((INSTALL + "\n\n").encode(), (INSTALL + "\r\n").encode(), INSTALL.encode(), (INSTALL + "\n\0").encode()):
            owner.write_bytes(bad)
            self.assertNotEqual(self.state_shell("lf_state_load").returncode, 0)
        owner.write_text(INSTALL + "\n")
        state.rename(self.root / "state-real")
        state.symlink_to(self.root / "state-real")
        self.assertNotEqual(self.state_shell("lf_state_write stopped none none none none none").returncode, 0)
        state.unlink()
        (self.root / "state-real").rename(state)
        d = self.root / "releases" / KEY
        record = (d / "record").read_bytes()
        (d / "app-password").unlink()
        tools = self.base / "tools"
        tools.mkdir()
        fake_od = tools / "od"
        fake_od.write_text("#!/bin/sh\nprintf 'ab\\n'\n")
        fake_od.chmod(0o700)
        self.assertNotEqual(self.run_script(env={"PATH": str(tools) + os.pathsep + os.environ["PATH"]}).returncode, 0)
        self.assertEqual((d / "record").read_bytes(), record)
        self.assertFalse((d / "app-password").exists())

    def test_first_write_repeat_and_missing_secret_recovery(self) -> None:
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        d = self.root / "releases" / KEY
        rec = (d / "record").read_bytes()
        self.assertEqual(len(rec.splitlines()), 5)
        self.assertEqual(self.run_script().returncode, 0)
        (d / "postgres-password").unlink()
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(len((d / "postgres-password").read_text()), 64)
        (d / "app-password").write_text("malformed")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((d / "record").read_bytes(), rec)
        self.assertFalse(list(d.glob(".record-*")))

    def test_abs_root_rejects_bad_paths_and_allows_spaces(self) -> None:
        for value in ("relative", "/", str(self.base) + "//x", str(self.base) + "/./x", str(self.base) + "/../x", str(self.base) + "/line\nbreak"):
            p = subprocess.run(["/bin/sh", "-c", STATE.read_text() + "\n" + LIFECYCLE.read_text().split("lf_select_runtime()", 1)[0] + "\nlf_abs_root \"$1\"", "sh", value], capture_output=True, timeout=10)
            self.assertNotEqual(p.returncode, 0, value)
        self.assertEqual(self.run_script().returncode, 0)

    def test_existing_record_is_not_overwritten_and_lock_token_replacement_fails(self) -> None:
        record = self.seed_record()
        (self.root / ".lock/token").write_text("e" * 32 + "\n")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / "releases" / KEY / "record").read_bytes(), record)

    def test_record_rejects_extra_line_cr_nul_and_symlink_entry(self) -> None:
        record = self.seed_record()
        path = self.root / "releases" / KEY / "record"
        for malformed in (record + b"extra\n", record[:-1], record.replace(b"\n", b"\r\n"), record + b"\0"):
            path.write_bytes(malformed)
            self.assertNotEqual(self.run_script().returncode, 0)
        path.write_bytes(record)
        self.entry.rename(self.entry.with_suffix(".real"))
        self.entry.symlink_to(self.entry.with_suffix(".real"))
        self.assertNotEqual(self.run_script().returncode, 0)


if __name__ == "__main__":
    unittest.main()
