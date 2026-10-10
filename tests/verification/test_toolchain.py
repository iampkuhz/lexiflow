"""验证真实执行器、PATH 分支与有界包身份的字节级证据。"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.environment.toolchains.toolchain_identity import ToolchainUnavailable, resolve_toolchain


def _script(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


class ToolchainTests(unittest.TestCase):
    def test_path_order_same_path_bytes_and_symlink_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a, b = root / "a", root / "b"
            a.mkdir()
            b.mkdir()
            _script(a / "node", "exit 0")
            _script(b / "node", "exit 1")
            check = {
                "command": ["node", "-e", "pass"],
                "required_environment": ["node"],
            }
            first = resolve_toolchain(check, {"PATH": f"{a}:{b}"})
            reordered = resolve_toolchain(check, {"PATH": f"{b}:{a}"})
            self.assertNotEqual(first, reordered)
            _script(a / "node", "exit 2")
            self.assertNotEqual(first, resolve_toolchain(check, {"PATH": f"{a}:{b}"}))
            (a / "node").unlink()
            (a / "node").symlink_to(b / "node")
            linked = resolve_toolchain(check, {"PATH": f"{a}:{b}"})
            self.assertEqual(
                linked["executor"]["realpath"], str((b / "node").resolve())
            )
            self.assertNotEqual(first, linked)

    def test_executable_mode_only_change_is_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / "node"
            _script(program, "exit 0")
            check = {"command": ["node"], "required_environment": ["node"]}
            env = {"PATH": directory}
            original = resolve_toolchain(check, env)
            for mode in (0o750, 0o4755, 0o2755):
                program.chmod(mode)
                changed = resolve_toolchain(check, env)
                self.assertEqual(
                    original["executor"]["sha256"], changed["executor"]["sha256"]
                )
                self.assertEqual(mode, changed["executor"]["mode"])
                self.assertNotEqual(original, changed)

    def test_python_executor_is_sys_executable_not_path_python3(self):
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory)
            _script(bin_dir / "python3", "exit 9")
            check = {
                "command": ["python3", "-c", "pass"],
                "executable": "python3",
                "required_environment": ["python3"],
            }
            stamp = resolve_toolchain(check, {"PATH": str(bin_dir)})
            self.assertEqual(stamp["executor"], stamp["tools"]["python3"])
            self.assertNotEqual(stamp["executor"]["realpath"], str(bin_dir / "python3"))

    def test_script_shebang_interpreter_bytes_are_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / "interpreter"
            _script(interpreter, "exit 0")
            program = root / "node"
            program.write_text(f"#!{interpreter}\nexit 0\n", encoding="utf-8")
            program.chmod(0o755)
            check = {"command": ["node"], "required_environment": ["node"]}
            before = resolve_toolchain(check, {"PATH": str(root)})
            self.assertEqual(
                before["executor"]["shebang"]["interpreter"]["path"],
                str(interpreter),
            )
            _script(interpreter, "exit 1")
            self.assertNotEqual(before, resolve_toolchain(check, {"PATH": str(root)}))

    def test_yaml_package_source_change_and_pycache_ignore(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "yaml"
            package.mkdir()
            origin = package / "__init__.py"
            origin.write_text("before", encoding="utf-8")
            module = package / "reader.py"
            module.write_text("before", encoding="utf-8")
            spec = SimpleNamespace(
                origin=str(origin), submodule_search_locations=[str(package)]
            )
            check = {
                "command": ["python3", "-c", "pass"],
                "executable": "python3",
                "required_environment": ["python-package-yaml"],
            }
            with patch(
                "scripts.environment.toolchains.toolchain_identity.importlib.util.find_spec",
                return_value=spec,
            ):
                first = resolve_toolchain(check, {"PATH": os.environ.get("PATH", "")})
                cache = package / "__pycache__"
                cache.mkdir()
                (cache / "reader.cpython-312.pyc").write_bytes(b"generated")
                self.assertEqual(
                    first,
                    resolve_toolchain(check, {"PATH": os.environ.get("PATH", "")}),
                )
                module.write_text("after", encoding="utf-8")
                self.assertNotEqual(
                    first,
                    resolve_toolchain(check, {"PATH": os.environ.get("PATH", "")}),
                )

    def test_selected_hash_tool_and_posix_lock_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _script(root / "shasum", "exit 0")
            check = {"command": ["shasum"], "required_environment": ["sha256-tool"]}
            # The fixed command itself is intentionally not an allowed executor.
            check["command"] = ["sh", "-c", "pass"]
            env = {"PATH": f"{root}:/bin:/usr/bin"}
            with patch(
                "scripts.environment.toolchains.toolchain_identity.shutil.which",
                side_effect=lambda name, path: (
                    str(root / name)
                    if (root / name).exists()
                    else ("/bin/sh" if name == "sh" else None)
                ),
            ):
                first = resolve_toolchain(check, env)
                self.assertEqual(first["tools"]["sha256-tool"]["selected"], "shasum")
                _script(root / "sha256sum", "exit 0")
                second = resolve_toolchain(check, env)
                self.assertEqual(
                    second["tools"]["sha256-tool"]["selected"], "sha256sum"
                )
                self.assertNotEqual(first, second)
            lock = {
                "command": ["sh", "-c", "pass"],
                "required_environment": ["posix-lock-tool"],
            }
            stamp = resolve_toolchain(lock, {"PATH": "/bin:/usr/bin"})
            self.assertEqual(
                stamp["tools"]["posix-lock-tool"]["path"],
                "/usr/bin/lockf"
                if os.uname().sysname == "Darwin"
                else "/usr/bin/flock",
            )

    def test_missing_or_unbounded_tool_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            check = {"command": ["node"], "required_environment": ["node"]}
            with self.assertRaises(ToolchainUnavailable):
                resolve_toolchain(check, {"PATH": directory})
            with self.assertRaises(ToolchainUnavailable):
                resolve_toolchain(check, {"PATH": f"relative:{directory}"})
            check = {
                "command": ["sh"],
                "required_environment": ["postgres-test-jdbc-url"],
            }
            with self.assertRaises(ToolchainUnavailable):
                resolve_toolchain(check, {"PATH": "/bin:/usr/bin"})


class AppleGitPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.payload = self.root / "payload-git"
        self.shim = self.root / "shim-git"
        self.dispatcher = self.root / "xcrun"
        for path in (self.payload, self.shim, self.dispatcher):
            _script(path, "exit 0")
        self.check = {"command": ["git"], "required_environment": ["git"]}
        self.env = {"PATH": "/bin:/usr/bin", "DEVELOPER_DIR": "/selected-developer"}
        from scripts.environment.toolchains import toolchain_identity as toolchain

        original = toolchain._executable_stamp

        def system_stamp(locator, env, seen=frozenset()):
            fixture = {
                "/usr/bin/git": self.shim,
                "/usr/bin/xcrun": self.dispatcher,
            }.get(locator)
            if fixture is None:
                return original(locator, env, seen)
            if locator in seen:
                raise ToolchainUnavailable("executable shebang cycle or depth exceeded")
            stamp = original(str(fixture), env, seen)
            stamp.update(path=locator, realpath=locator)
            return stamp

        for patcher in (
            patch.object(toolchain.sys, "platform", "darwin"),
            patch.object(toolchain.shutil, "which", return_value="/usr/bin/git"),
            patch.object(toolchain, "_executable_stamp", side_effect=system_stamp),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(
            toolchain.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                [], 0, str(self.payload) + "\n", ""
            ),
        )
        self.probe = patcher.start()
        self.addCleanup(patcher.stop)

    def resolve(self):
        return resolve_toolchain(self.check, self.env)

    def test_real_payload_bytes_modes_and_dispatcher_are_bound_not_just_shim(self):
        before = self.resolve()
        self.assertEqual(
            before["executor"]["apple_payload"]["realpath"], str(self.payload)
        )
        self.assertEqual(
            before["executor"]["apple_dispatcher"]["path"], "/usr/bin/xcrun"
        )
        _script(self.payload, "exit 1")
        changed = self.resolve()
        self.assertEqual(before["executor"]["sha256"], changed["executor"]["sha256"])
        self.assertNotEqual(
            before["executor"]["apple_payload"]["sha256"],
            changed["executor"]["apple_payload"]["sha256"],
        )
        self.payload.chmod(0o750)
        mode_changed = self.resolve()
        self.assertEqual(mode_changed["executor"]["apple_payload"]["mode"], 0o750)
        self.assertNotEqual(changed, mode_changed)
        _script(self.dispatcher, "exit 2")
        self.assertNotEqual(mode_changed, self.resolve())
        args, kwargs = self.probe.call_args
        self.assertEqual(args[0], ["/usr/bin/xcrun", "--find", "git"])
        self.assertEqual(kwargs["env"], self.env)
        self.assertEqual(kwargs["timeout"], 5)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)

    def test_payload_selection_and_symlink_target_change_are_rechecked(self):
        before = self.resolve()
        other = self.root / "other-git"
        _script(other, "exit 2")
        self.probe.return_value.stdout = str(other)
        after = self.resolve()
        self.assertNotEqual(before, after)
        self.payload.unlink()
        self.payload.symlink_to(other)
        self.probe.return_value.stdout = str(self.payload)
        linked = self.resolve()
        self.assertEqual(linked["executor"]["apple_payload"]["realpath"], str(other))

    def test_unknown_selection_missing_payload_or_recursive_shim_fail_closed(self):
        for result in (
            subprocess.CompletedProcess([], 1, str(self.payload), "failed"),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "relative/git", ""),
            subprocess.CompletedProcess([], 0, str(self.root / "missing"), ""),
            subprocess.CompletedProcess([], 0, "/usr/bin/git", ""),
        ):
            with self.subTest(result=result):
                self.probe.return_value = result
                with self.assertRaises(ToolchainUnavailable):
                    self.resolve()
        self.probe.return_value = subprocess.CompletedProcess(
            [], 0, str(self.payload), ""
        )
        self.payload.chmod(0o644)
        with self.assertRaises(ToolchainUnavailable):
            self.resolve()

    def test_payload_alias_back_to_shim_fails_closed(self):
        alias = self.root / "recursive-alias"
        alias.symlink_to("/usr/bin/git")
        self.probe.return_value = subprocess.CompletedProcess([], 0, str(alias), "")
        with self.assertRaises(ToolchainUnavailable):
            self.resolve()

    def test_xcrun_timeout_or_spawn_failure_never_qualifies_shim_only(self):
        for error in (subprocess.TimeoutExpired("xcrun", 5), OSError("missing")):
            self.probe.side_effect = error
            with self.subTest(error=error), self.assertRaises(ToolchainUnavailable):
                self.resolve()

    def test_non_apple_or_native_path_git_does_not_use_dispatch_probe(self):
        with patch("scripts.environment.toolchains.toolchain_identity.sys.platform", "linux"):
            self.assertNotIn("apple_payload", self.resolve()["executor"])
        self.probe.assert_not_called()
        with patch(
            "scripts.environment.toolchains.toolchain_identity.shutil.which", return_value=str(self.payload)
        ):
            self.assertNotIn("apple_payload", self.resolve()["executor"])
        self.probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
