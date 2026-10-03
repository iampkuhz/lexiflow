"""仅用有状态本机端口替身验证两份真实 Shell 入口的生命周期。"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
STATE = (REPO / "ops/release/lifecycle-state.sh").read_text()
DOCKER = (REPO / "ops/release/lifecycle-docker.sh").read_text()
FLOW = (REPO / "ops/release/lifecycle.sh").read_text()
A, B, C = ("a" * 64, "b" * 64, "c" * 64)
PORTS = r'''
LF_PLATFORM=linux/amd64
LF_COMPOSE_PATH=compose.yaml
lf_verify_release() { [ "$LF_RELEASE_KEY" = "$EXPECTED_KEY" ]; }
lf_docker_preflight() { LF_HOST_PLATFORM=linux/amd64; }
lf_docker_endpoint() { LF_DOCKER_ENDPOINT=unix:///synthetic.sock; }
lf_docker_identity_query() { printf '%s\n' synthetic-daemon-id; }
lf_select_platform() { [ "$1" = linux/amd64 ]; }
lf_port() {
  printf '%s:%s\n' "$1" "$LF_CURRENT_KEY" >> "$TEST_EVENTS"
  marker="$TEST_FAULT/$1-$LF_CURRENT_KEY"
  if [ -f "$marker" ]; then rm "$marker"; return 1; fi
}
lf_docker_prepare() { lf_port prepare; }
lf_docker_start() { lf_port start; }
lf_docker_stop() { lf_port stop; }
lf_docker_remove() { lf_port remove; }
'''


class FlowTests(unittest.TestCase):
    """每例均从新的私有安装根运行独立生成入口。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="lf-flow-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "installation"
        self.events = self.base / "events"
        self.events.touch()
        self.faults = self.base / "faults"
        self.faults.mkdir()
        self.sentinel = self.base / "external-sentinel"
        self.sentinel.write_text("untouched")
        self.entries = {}
        for key in (A, B, C):
            directory = self.base / key[:1]
            directory.mkdir()
            entry = directory / "lexiflow.sh"
            entry.write_text(
                "#!/bin/sh\n"
                f"EXPECTED_KEY={key}\nLF_RELEASE_KEY={key}\n"
                + STATE + "\n" + DOCKER + "\n" + PORTS + "\n" + FLOW
            )
            entry.chmod(0o700)
            self.entries[key] = entry
        self.env = {**os.environ, "TEST_EVENTS": str(self.events), "TEST_FAULT": str(self.faults)}
        tools = self.base / "tools"
        tools.mkdir()
        for command in ("mv", "rm"):
            utility = tools / command
            utility.write_text(
                "#!/bin/sh\n"
                f'if [ "${{TEST_FAIL_CMD:-}}" = {command} ] && [ -f "$TEST_FAIL_MARKER" ]; then\n'
                '  case " $* " in *"$TEST_FAIL_MATCH"*) /bin/rm "$TEST_FAIL_MARKER"; printf "private-path-marker\\n" >&2; exit 1;; esac\n'
                "fi\n"
                f'exec /bin/{command} "$@"\n'
            )
            utility.chmod(0o700)
        self.env["PATH"] = str(tools) + os.pathsep + os.environ["PATH"]

    def call(self, key: str, *args: str, ok: bool | None = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["/bin/sh", str(self.entries[key]), *args],
            text=True, capture_output=True, timeout=12, env=self.env,
        )
        if ok is True:
            self.assertEqual(result.returncode, 0, (args, result.stderr))
        elif ok is False:
            self.assertNotEqual(result.returncode, 0, (args, result.stderr))
        self.assertNotIn("private-path-marker", result.stdout + result.stderr)
        return result

    def state(self) -> dict[str, str]:
        return dict(line.split("=", 1) for line in (self.root / "state").read_text().splitlines())

    def put_state(self, **values: str) -> None:
        old = self.state()
        old.update(values)
        (self.root / "state").write_text("".join(f"{key}={value}\n" for key, value in old.items()))

    def fault(self, action: str, key: str) -> None:
        (self.faults / f"{action}-{key}").touch()

    def op_fault(self, command: str, match: str) -> None:
        marker = self.base / "one-shot-utility-fault"
        marker.touch()
        self.env.update(TEST_FAIL_CMD=command, TEST_FAIL_MATCH=match, TEST_FAIL_MARKER=str(marker))

    def clear_op_fault(self) -> None:
        for name in ("TEST_FAIL_CMD", "TEST_FAIL_MATCH", "TEST_FAIL_MARKER"):
            self.env.pop(name, None)

    def events_since(self, before: str) -> list[str]:
        return self.events.read_text()[len(before):].splitlines()

    def install_two(self) -> None:
        self.call(A, "prepare", str(self.root))
        self.call(A, "activate", str(self.root))
        self.call(B, "prepare", str(self.root))
        self.call(B, "activate", str(self.root))

    def assert_sentinel(self) -> None:
        self.assertEqual(self.sentinel.read_text(), "untouched")
        for entry in self.entries.values():
            self.assertTrue(entry.is_file())

    def test_two_releases_recover_stop_restart_and_idempotent_prepare(self) -> None:
        self.call(A, "prepare", str(self.root))
        before = self.events.read_text()
        self.call(A, "prepare", str(self.root))
        self.assertEqual(self.events.read_text(), before)
        self.call(A, "activate", str(self.root))
        self.call(A, "prepare", str(self.root))
        self.assertEqual(self.events.read_text(), before + f"start:{A}\n")
        self.call(B, "prepare", str(self.root))
        self.call(C, "prepare", str(self.root), ok=False)
        self.call(B, "activate", str(self.root))
        self.assertEqual((self.state()["active"], self.state()["previous"]), (B, A))
        self.call(B, "recover", str(self.root))
        self.assertEqual((self.state()["active"], self.state()["previous"]), (A, B))
        self.call(A, "stop", str(self.root))
        self.assertEqual(self.state()["phase"], "stopped")
        self.call(A, "activate", str(self.root))
        self.assertEqual(self.state()["phase"], "idle")
        self.assert_sentinel()

    def test_failed_start_returns_nonzero_and_stop_failure_blocks_old(self) -> None:
        self.call(A, "prepare", str(self.root))
        self.call(A, "activate", str(self.root))
        self.call(B, "prepare", str(self.root))
        self.fault("start", B)
        result = self.call(B, "activate", str(self.root), ok=False)
        self.assertEqual(self.state()["phase"], "prepared")
        self.assertEqual(self.state()["active"], A)
        self.assertNotEqual(result.returncode, 0)
        self.fault("start", B)
        self.fault("stop", B)
        before = self.events.read_text()
        self.call(B, "activate", str(self.root), ok=False)
        self.assertEqual(self.state()["phase"], "switching")
        self.assertNotIn(f"start:{A}", self.events_since(before))
        self.call(B, "recover", str(self.root))
        self.assertEqual(self.state()["phase"], "prepared")

    def test_recover_previous_failure_then_retry_and_intermediate_states(self) -> None:
        self.install_two()
        self.fault("start", A)
        self.call(B, "recover", str(self.root), ok=False)
        self.assertEqual(self.state()["phase"], "idle")
        self.assertEqual(self.state()["active"], B)
        self.put_state(phase="switching", active=B, previous=A, candidate=A)
        self.fault("stop", A)
        before = self.events.read_text()
        self.call(B, "recover", str(self.root), ok=False)
        self.assertNotIn(f"start:{B}", self.events_since(before))
        self.call(B, "recover", str(self.root))
        self.assertEqual(self.state()["phase"], "idle")
        self.put_state(phase="starting")
        self.call(B, "recover", str(self.root))
        self.assertEqual(self.state()["phase"], "stopped")
        self.put_state(phase="stopping")
        self.call(B, "stop", str(self.root))
        self.assertEqual(self.state()["phase"], "stopped")

    def test_journal_failure_no_docker_and_project_binding(self) -> None:
        self.call(A, "prepare", str(self.root))
        self.call(A, "activate", str(self.root))
        self.call(B, "prepare", str(self.root))
        # 原子 state rename 失败必须先于任何切换 Docker 动作。
        self.op_fault("mv", "/state")
        before = self.events.read_text()
        self.call(B, "activate", str(self.root), ok=False)
        self.assertEqual(self.events.read_text(), before)
        self.clear_op_fault()
        token = "d" * 32
        lock = self.root / ".lock"
        lock.mkdir()
        (lock / "token").write_text(token + "\n")
        owner = self.root / "owner"
        install = owner.read_text().strip()
        record = self.root / "releases" / A / "record"
        original = record.read_bytes()
        self.call(A, "_project", str(self.root), A, token, "verify-record")
        for key, secret in ((B, token), (A, "e" * 32)):
            before = self.events.read_text()
            self.call(A, "_project", str(self.root), key, secret, "stop", ok=False)
            self.assertEqual(self.events.read_text(), before)
        record.write_bytes(original.replace(f"lf_{install}_".encode(), b"bad_"))
        self.call(A, "_project", str(self.root), A, token, "stop", ok=False)
        record.write_bytes(original)
        b_entry = self.entries[B]
        import hashlib
        wrong_entry = original.replace(str(self.entries[A]).encode(), str(b_entry).encode())
        wrong_entry = wrong_entry.replace(
            hashlib.sha256(self.entries[A].read_bytes()).hexdigest().encode(),
            hashlib.sha256(b_entry.read_bytes()).hexdigest().encode(),
        )
        record.write_bytes(wrong_entry)
        before = self.events.read_text()
        self.call(A, "_project", str(self.root), A, token, "stop", ok=False)
        self.assertEqual(self.events.read_text(), before)
        record.write_bytes(original)
        (lock / "token").unlink()
        lock.rmdir()
        self.assert_sentinel()

    def test_prepare_phase_rejections_and_stop_failure_retry(self) -> None:
        self.call(A, "prepare", str(self.root))
        for phase in ("starting", "stopping"):
            self.put_state(phase=phase, active="none", candidate="none")
            self.call(A, "status", str(self.root), ok=False)
        self.put_state(phase="prepared", candidate=A)
        self.put_state(phase="switching")
        before = self.events.read_text()
        self.call(A, "prepare", str(self.root), ok=False)
        self.assertEqual(self.events.read_text(), before)
        self.put_state(phase="prepared")
        self.call(A, "activate", str(self.root))
        self.fault("stop", A)
        self.call(A, "stop", str(self.root), ok=False)
        self.assertEqual(self.state()["phase"], "stopping")
        self.call(A, "stop", str(self.root))
        self.assertEqual(self.state()["phase"], "stopped")
        self.call(A, "activate", str(self.root))
        self.assert_sentinel()

    def test_delete_journal_retry_remove_rename_and_cleanup(self) -> None:
        self.install_two()
        self.call(B, "delete", str(self.root), A, "wrong", ok=False)
        self.call(B, "delete", str(self.root), B, "--confirm-delete-data", ok=False)
        self.fault("remove", A)
        self.call(B, "delete", str(self.root), A, "--confirm-delete-data", ok=False)
        self.assertEqual(self.state()["phase"], "deleting")
        self.assertTrue((self.root / "releases" / A / "record").exists())
        self.call(B, "delete", str(self.root), A, "--confirm-delete-data")
        self.assertEqual(self.state()["previous"], "none")
        self.assertFalse((self.root / "releases" / A).exists())
        self.assert_sentinel()

    def test_delete_tombstone_commit_retry_and_unknown_content(self) -> None:
        self.install_two()
        directory = self.root / "releases" / A
        extra = directory / "unknown"
        extra.touch()
        before = self.events.read_text()
        self.call(B, "delete", str(self.root), A, "--confirm-delete-data", ok=False)
        self.assertEqual(self.events.read_text(), before)
        extra.unlink()
        hidden = directory / "..foreign"
        hidden.touch()
        self.call(B, "delete", str(self.root), A, "--confirm-delete-data", ok=False)
        self.assertEqual(self.events.read_text(), before)
        hidden.unlink()
        # 按已提交 journal 和已原子转移的 record 构造 rename 后中断。
        self.put_state(phase="deleting", deleting=A, resume="idle")
        tomb = self.root / "releases" / f".deleted-{A}"
        directory.rename(tomb)
        before = self.events.read_text()
        self.call(B, "delete", str(self.root), A, "--confirm-delete-data")
        self.assertEqual(self.events.read_text(), before)
        self.assertFalse(tomb.exists())
        self.assert_sentinel()

    def test_delete_rename_commit_and_cleanup_faults_retry(self) -> None:
        self.install_two()
        ordinary = self.root / "releases" / A
        tomb = self.root / "releases" / f".deleted-{A}"
        self.op_fault("mv", f"/.deleted-{A}")
        self.call(B, "delete", str(self.root), A, "--confirm-delete-data", ok=False)
        self.assertEqual(self.state()["phase"], "deleting")
        self.assertTrue((ordinary / "record").exists())
        self.assertFalse(tomb.exists())
        self.clear_op_fault()
        self.op_fault("mv", "/state")
        self.call(B, "delete", str(self.root), A, "--confirm-delete-data", ok=False)
        self.assertEqual(self.state()["phase"], "deleting")
        self.assertFalse(ordinary.exists())
        self.assertTrue((tomb / "record").exists())
        self.clear_op_fault()
        self.op_fault("rm", "postgres-password")
        self.call(B, "delete", str(self.root), A, "--confirm-delete-data", ok=False)
        self.assertEqual(self.state()["phase"], "idle")
        self.assertTrue((tomb / "record").exists())
        self.clear_op_fault()
        self.op_fault("rm", "/record")
        self.call(B, "delete", str(self.root), A, "--confirm-delete-data", ok=False)
        self.assertTrue((tomb / "record").exists())
        self.clear_op_fault()
        self.call(B, "delete", str(self.root), A, "--confirm-delete-data")
        self.assertFalse(tomb.exists())
        self.assert_sentinel()

    def test_delete_stopped_active_and_not_found(self) -> None:
        self.call(A, "prepare", str(self.root))
        self.call(A, "activate", str(self.root))
        self.call(A, "stop", str(self.root))
        self.call(A, "delete", str(self.root), A, "--confirm-delete-data")
        self.assertEqual(self.state()["active"], "none")
        result = self.call(A, "delete", str(self.root), A, "--confirm-delete-data", ok=False)
        self.assertIn("LF_NOT_FOUND", result.stderr)
        self.assert_sentinel()

    def test_restart_failure_and_switching_recovery(self) -> None:
        self.call(A, "prepare", str(self.root))
        self.call(A, "activate", str(self.root))
        self.call(A, "stop", str(self.root))
        self.fault("start", A)
        self.call(A, "activate", str(self.root), ok=False)
        self.assertEqual(self.state()["phase"], "stopped")
        self.call(A, "activate", str(self.root))
        self.call(B, "prepare", str(self.root))
        self.put_state(phase="switching")
        self.call(B, "recover", str(self.root))
        self.assertEqual(self.state()["phase"], "prepared")
        before = self.events.read_text()
        self.call(B, "recover", str(self.root))
        self.assertEqual(self.events.read_text(), before)
        self.assert_sentinel()

    def test_delete_symlink_and_dual_directory_refuse_without_remove(self) -> None:
        self.install_two()
        ordinary = self.root / "releases" / A
        outside = self.base / "outside"
        outside.write_text("outside")
        link = ordinary / "unknown-link"
        link.symlink_to(outside)
        before = self.events.read_text()
        self.call(B, "delete", str(self.root), A, "--confirm-delete-data", ok=False)
        self.assertEqual(self.events.read_text(), before)
        self.assertEqual(outside.read_text(), "outside")
        link.unlink()
        tomb = self.root / "releases" / f".deleted-{A}"
        tomb.mkdir()
        self.call(B, "delete", str(self.root), A, "--confirm-delete-data", ok=False)
        self.assertEqual(self.events.read_text(), before)
        tomb.rmdir()
        self.assert_sentinel()

    def test_unlock_rejects_symlink_lock_and_unknown_entries(self) -> None:
        self.call(A, "prepare", str(self.root))
        outside = self.base / "outside-lock"
        outside.mkdir()
        token = "f" * 32
        (outside / "token").write_text(token + "\n")
        lock = self.root / ".lock"
        lock.symlink_to(outside, target_is_directory=True)
        self.call(A, "unlock", str(self.root), token, "--confirm-abandoned-lock", ok=False)
        self.assertEqual((outside / "token").read_text(), token + "\n")
        lock.unlink()
        lock.mkdir()
        (lock / "token").write_text(token + "\n")
        (lock / "..foreign").touch()
        self.call(A, "unlock", str(self.root), token, "--confirm-abandoned-lock", ok=False)
        self.assertTrue((lock / "token").exists())
        (lock / "..foreign").unlink()
        self.call(A, "unlock", str(self.root), token, "--confirm-abandoned-lock")
        self.assertFalse(lock.exists())
        self.assert_sentinel()


if __name__ == "__main__":
    unittest.main()
