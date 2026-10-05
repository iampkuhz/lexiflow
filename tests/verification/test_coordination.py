"""验证窗口的互斥、路径安全与异常清理回归。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.verification.coordination import WindowError, verification_window


class VerificationWindowTests(unittest.TestCase):
    def test_two_processes_compete_and_second_succeeds_after_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = """from pathlib import Path
import sys, time
from scripts.verification.coordination import verification_window
with verification_window(Path(sys.argv[1])):
    print('HELD', flush=True)
    time.sleep(1.2)
"""
            first = subprocess.Popen(
                [sys.executable, "-c", script, str(root)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=Path(__file__).resolve().parents[2],
            )
            try:
                self.assertEqual(first.stdout.readline().strip(), "HELD")
                with (
                    self.assertRaises(WindowError) as caught,
                    verification_window(root),
                ):
                    self.fail("busy window unexpectedly acquired")
                self.assertEqual(caught.exception.code, "verification-window-busy")
                self.assertEqual(caught.exception.status, "BLOCKED")
                self.assertEqual(first.wait(timeout=5), 0, first.stderr.read())
                with verification_window(root):
                    pass
            finally:
                if first.poll() is None:
                    first.kill()
                    first.wait()
                if first.stdout is not None:
                    first.stdout.close()
                if first.stderr is not None:
                    first.stderr.close()

    def test_public_api_never_executes_while_another_window_is_held(self):
        from unittest.mock import Mock
        from scripts.verification import verify_repository
        from tests.verification.test_work_package import fixture
        from tests.verification.test_scenarios import _pass_runner

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            runner = Mock(side_effect=_pass_runner)
            with verification_window(root) as window:
                blocked = verify_repository(root, runner=runner)
                self.assertEqual(blocked["result"], "BLOCKED")
                self.assertEqual(blocked["reason"], "verification-window-busy")
                runner.assert_not_called()
                report = verify_repository(root, runner=runner, _window=window)
                self.assertEqual(report["result"], "PASS", report)
                self.assertEqual(runner.call_count, 1)
            runner.reset_mock()
            stale = verify_repository(root, runner=runner, _window=window)
            self.assertEqual(stale["result"], "BLOCKED")
            runner.assert_not_called()

    def test_held_window_cannot_cross_checkout_or_thread(self):
        import threading

        with (
            tempfile.TemporaryDirectory() as temporary,
            tempfile.TemporaryDirectory() as other,
        ):
            root = Path(temporary)
            with verification_window(root) as window:
                with self.assertRaises(WindowError):
                    window.check(Path(other))
                errors = []

                def check():
                    try:
                        window.check(root)
                    except WindowError as exc:
                        errors.append(exc)

                thread = threading.Thread(target=check)
                thread.start()
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(errors), 1)

    def test_changed_lock_permissions_are_not_accepted_at_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = root / "tmp/quality/verification-window/execution.lock"
            with self.assertRaises(WindowError), verification_window(root):
                lock.chmod(0o666)
            self.assertTrue(lock.exists())
            self.assertEqual(lock.stat().st_mode & 0o777, 0o666)

    def test_constructed_or_duck_typed_window_cannot_authorize_execution(self):
        from unittest.mock import Mock
        from scripts.verification import verify_repository
        from scripts.verification.coordination import VerificationWindow

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with verification_window(root) as held:
                forged = VerificationWindow(root, held._directory_fd, held._lock_fd)
                for value in (forged, Mock()):
                    runner = Mock()
                    result = verify_repository(root, runner=runner, _window=value)
                    self.assertEqual(result["result"], "BLOCKED")
                    runner.assert_not_called()

    def test_exception_releases_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                self.assertRaisesRegex(RuntimeError, "sentinel"),
                verification_window(root),
            ):
                raise RuntimeError("sentinel")
            with verification_window(root):
                pass

    def test_symlink_special_file_and_hardlink_are_rejected_without_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "tmp/quality/verification-window"
            directory.mkdir(parents=True)
            lock = directory / "execution.lock"

            target = root / "target"
            target.write_text("untouched")
            lock.symlink_to(target)
            with self.assertRaises(WindowError), verification_window(root):
                pass
            self.assertTrue(lock.is_symlink())
            self.assertEqual(target.read_text(), "untouched")
            lock.unlink()

            os.mkfifo(lock)
            with self.assertRaises(WindowError), verification_window(root):
                pass
            self.assertTrue(lock.exists())
            lock.unlink()

            lock.write_text("")
            second_link = root / "second-link"
            os.link(lock, second_link)
            with self.assertRaises(WindowError), verification_window(root):
                pass
            self.assertTrue(lock.exists())
            self.assertTrue(second_link.exists())

    def test_replaced_lock_is_rejected_and_neither_file_is_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "tmp/quality/verification-window"
            lock = directory / "execution.lock"
            displaced = directory / "displaced.lock"
            with self.assertRaises(WindowError), verification_window(root):
                lock.rename(displaced)
                lock.write_text("replacement")
            self.assertTrue(lock.exists())
            self.assertTrue(displaced.exists())

    def test_unknown_existing_lock_content_is_never_used_as_identity_or_cleaned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "tmp/quality/verification-window"
            directory.mkdir(parents=True)
            lock = directory / "execution.lock"
            lock.write_text("foreign metadata with pid=12345")
            with verification_window(root):
                pass
            self.assertEqual(lock.read_text(), "foreign metadata with pid=12345")


if __name__ == "__main__":
    unittest.main()
