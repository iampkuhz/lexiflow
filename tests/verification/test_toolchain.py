"""验证真实执行器、PATH 分支与有界包身份的字节级证据。"""

from __future__ import annotations

import os
import importlib.metadata
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.environment.toolchains.toolchain_identity import (
    ToolchainUnavailable,
    resolve_toolchain,
)


def _script(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


class ToolchainTests(unittest.TestCase):
    def test_python_quality_distribution_payload_and_pin_are_bound(self):
        from scripts.environment.toolchains import toolchain_identity as toolchain

        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "ruff"
            package.mkdir()
            origin = package / "__init__.py"
            origin.write_text("before", encoding="utf-8")
            binary = Path(directory) / "ruff-bin"
            _script(binary, "exit 0")
            spec = SimpleNamespace(
                origin=str(origin), submodule_search_locations=[str(package)]
            )
            distribution = SimpleNamespace(version="0.16.8")
            with (
                patch.object(toolchain, "_exact_pin", return_value="0.16.8"),
                patch.object(
                    toolchain.importlib.metadata,
                    "distribution",
                    return_value=distribution,
                ),
                patch.object(toolchain.importlib.util, "find_spec", return_value=spec),
                patch.object(
                    toolchain,
                    "_distribution_dependency_payload",
                    return_value=[
                        {
                            "distribution": "ruff",
                            "files": [
                                {"realpath": str(origin.resolve()), "sha256": "first"}
                            ],
                        }
                    ],
                ) as dependencies,
                patch("ruff.find_ruff_bin", return_value=str(binary)),
            ):
                root = Path(directory)
                (root / "requirements-dev.txt").write_text("ruff==0.16.8\n")
                first = toolchain._python_distribution(
                    "ruff", "ruff", {"PATH": os.environ.get("PATH", "")}, root
                )
                dependencies.return_value = [
                    {
                        "distribution": "ruff",
                        "files": [
                            {"realpath": str(origin.resolve()), "sha256": "changed"}
                        ],
                    }
                ]
                self.assertNotEqual(
                    first,
                    toolchain._python_distribution(
                        "ruff", "ruff", {"PATH": os.environ.get("PATH", "")}, root
                    ),
                )
                dependencies.return_value = [
                    {
                        "distribution": "ruff",
                        "files": [
                            {"realpath": str(origin.resolve()), "sha256": "first"}
                        ],
                    }
                ]
                origin.write_text("changed", encoding="utf-8")
                self.assertNotEqual(
                    first,
                    toolchain._python_distribution(
                        "ruff", "ruff", {"PATH": os.environ.get("PATH", "")}, root
                    ),
                )
                _script(binary, "exit 1")
                self.assertNotEqual(
                    first,
                    toolchain._python_distribution(
                        "ruff", "ruff", {"PATH": os.environ.get("PATH", "")}, root
                    ),
                )
                with patch.object(toolchain, "_exact_pin", return_value="0.16.9"):
                    with self.assertRaises(ToolchainUnavailable):
                        toolchain._python_distribution(
                            "ruff", "ruff", {"PATH": os.environ.get("PATH", "")}, root
                        )
                with patch.object(toolchain, "_exact_pin", return_value="0.16.8"):
                    with patch.object(
                        toolchain.importlib.metadata,
                        "distribution",
                        side_effect=importlib.metadata.PackageNotFoundError("ruff"),
                    ):
                        with self.assertRaises(ToolchainUnavailable):
                            toolchain._python_distribution(
                                "ruff",
                                "ruff",
                                {"PATH": os.environ.get("PATH", "")},
                                root,
                            )

    def test_python_quality_distribution_symlink_escape_and_scan_limit_fail_closed(
        self,
    ):
        from scripts.environment.toolchains import toolchain_identity as toolchain

        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory() as outside,
        ):
            package = Path(directory) / "pylint"
            package.mkdir()
            origin = package / "__init__.py"
            origin.write_text("ok", encoding="utf-8")
            escaped = Path(outside) / "module.py"
            escaped.write_text("outside", encoding="utf-8")
            (package / "external.py").symlink_to(escaped)
            root = Path(directory)
            (root / "requirements-dev.txt").write_text("pylint==4.0.9\n")
            with (
                patch.object(toolchain, "_exact_pin", return_value="4.0.9"),
                patch.object(
                    toolchain.importlib.metadata,
                    "distribution",
                    return_value=SimpleNamespace(version="4.0.9"),
                ),
                patch.object(
                    toolchain.importlib.util,
                    "find_spec",
                    return_value=SimpleNamespace(
                        origin=str(origin), submodule_search_locations=[str(package)]
                    ),
                ),
            ):
                with self.assertRaises(ToolchainUnavailable):
                    toolchain._python_distribution(
                        "pylint", "pylint", {"PATH": os.environ.get("PATH", "")}, root
                    )
                (package / "external.py").unlink()
                with patch.object(toolchain, "_MAX_PACKAGE_FILES", 0):
                    with self.assertRaises(ToolchainUnavailable):
                        toolchain._python_distribution(
                            "pylint",
                            "pylint",
                            {"PATH": os.environ.get("PATH", "")},
                            root,
                        )

    def test_quality_entry_requires_exact_command_and_target_root(self):
        quality = {
            "command": [
                "python3",
                "-m",
                "scripts.checks.repository.run_repository_check_group",
                "--mode",
                "python",
            ],
            "executable": "python3",
            "required_environment": [],
        }
        env = {"PATH": os.environ.get("PATH", "/bin:/usr/bin")}
        with self.assertRaisesRegex(ToolchainUnavailable, "repo_root"):
            resolve_toolchain(quality, env)
        unknown = {**quality, "command": [*quality["command"], "--unknown"]}
        with patch(
            "scripts.environment.toolchains.toolchain_identity._python_distribution"
        ) as provider:
            with self.assertRaisesRegex(ToolchainUnavailable, "unrecognized"):
                resolve_toolchain(unknown, env)
            provider.assert_not_called()

    def test_other_repository_modes_keep_generic_identity_without_quality_tools(self):
        """共享入口的 docs/planning 是已知独立模式，不要求质量工具身份。"""
        from scripts.environment.toolchains import toolchain_identity as toolchain

        env = {"PATH": os.environ.get("PATH", "/bin:/usr/bin")}
        with patch.object(toolchain, "_python_distribution") as provider:
            for mode in ("docs", "planning"):
                with self.subTest(mode=mode):
                    check = {
                        "executable": "python3",
                        "command": [*toolchain._PYTHON_QUALITY_COMMAND[:-1], mode],
                        "required_environment": [],
                    }
                    result = resolve_toolchain(check, env)
                    self.assertEqual(set(result["tools"]), {"python-package-yaml"})
            provider.assert_not_called()

    def test_exact_pin_comes_from_explicit_target_checkout(self):
        from scripts.environment.toolchains import toolchain_identity as toolchain

        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "one", Path(directory) / "two"
            first.mkdir()
            second.mkdir()
            (first / "requirements-dev.txt").write_text("ruff==0.16.8\n")
            (second / "requirements-dev.txt").write_text("ruff==0.16.9\n")
            self.assertEqual(toolchain._exact_pin("ruff", first), "0.16.8")
            self.assertEqual(toolchain._exact_pin("ruff", second), "0.16.9")

    def test_quality_command_derives_private_tool_identities_from_target_root(self):
        quality = {
            "command": [
                "python3",
                "-m",
                "scripts.checks.repository.run_repository_check_group",
                "--mode",
                "python",
            ],
            "executable": "python3",
            "required_environment": [],
        }
        env = {"PATH": os.environ.get("PATH", "/bin:/usr/bin")}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "scripts.environment.toolchains.toolchain_identity._python_distribution",
                side_effect=lambda name, module, actual_env, repo_root: {
                    "name": name,
                    "root": str(repo_root),
                },
            ) as provider:
                result = resolve_toolchain(quality, env, repo_root=root)
            self.assertEqual(
                set(result["tools"]),
                {
                    "python-package-yaml",
                    "python-package-ruff",
                    "python-package-pylint",
                    "python-package-astroid",
                },
            )
            self.assertEqual(provider.call_count, 3)
            self.assertTrue(
                all(call.args[3] == root.resolve() for call in provider.call_args_list)
            )

    def test_dependency_closure_binds_files_markers_and_fails_closed(self):
        from scripts.environment.toolchains import toolchain_identity as toolchain

        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory() as escaped_dir,
        ):
            root = Path(directory)
            (root / "tool.py").write_text("tool", encoding="utf-8")
            (root / "dep.py").write_text("dep-v1", encoding="utf-8")
            dep = SimpleNamespace(
                metadata={"Name": "dep"},
                files=[Path("dep.py")],
                locate_file=lambda item: root / item,
                version="1.0",
                requires=[],
            )
            selected = SimpleNamespace(
                metadata={"Name": "tool"},
                files=[Path("tool.py")],
                locate_file=lambda item: root / item,
                version="1.0",
                requires=[
                    "dep>=1; python_version >= '3.0'",
                    "unused==1; extra == 'optional'",
                ],
            )
            pip_dist = importlib.metadata.distribution("pip")
            with (
                patch.object(
                    toolchain,
                    "_python_install_roots",
                    return_value={
                        root.resolve(),
                        Path(sys.prefix).resolve(),
                        Path(sys.base_prefix).resolve(),
                    },
                ),
                patch.object(
                    toolchain.importlib.metadata,
                    "distribution",
                    side_effect=lambda name: pip_dist if name == "pip" else dep,
                ),
            ):
                before = toolchain._distribution_dependency_payload(selected)
                self.assertEqual(
                    {x["distribution"] for x in before}, {"tool", "dep", "pip"}
                )
                self.assertNotIn("unused", {x["distribution"] for x in before})
                (root / "dep.py").chmod(0o600)
                self.assertNotEqual(
                    before, toolchain._distribution_dependency_payload(selected)
                )
                (root / "dep.py").chmod(0o644)
                (root / "dep.py").write_text("dep-v2", encoding="utf-8")
                after = toolchain._distribution_dependency_payload(selected)
                self.assertNotEqual(before, after)
                (root / "dep.py").unlink()
                with self.assertRaisesRegex(ToolchainUnavailable, "missing"):
                    toolchain._distribution_dependency_payload(selected)
                outside = Path(escaped_dir) / "dep.py"
                outside.write_text("escaped", encoding="utf-8")
                (root / "dep.py").symlink_to(outside)
                with self.assertRaisesRegex(ToolchainUnavailable, "escaped"):
                    toolchain._distribution_dependency_payload(selected)
                (root / "dep.py").unlink()
                selected.files = [Path("tool.py"), Path("../outside.py")]
                parent_escape = root.parent / "outside.py"
                parent_escape.write_text("outside", encoding="utf-8")
                try:
                    with self.assertRaisesRegex(ToolchainUnavailable, "escaped"):
                        toolchain._distribution_dependency_payload(selected)
                finally:
                    parent_escape.unlink(missing_ok=True)

    def test_dependency_closure_limits_and_missing_pip_fail_closed(self):
        from scripts.environment.toolchains import toolchain_identity as toolchain

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("a", encoding="utf-8")
            fixture = SimpleNamespace(metadata={"Name": "tool"}, files=[Path("a.py")])
            fixture.locate_file = lambda item: root / item
            fixture.version = "1"
            fixture.requires = []
            pip_dist = importlib.metadata.distribution("pip")
            with (
                patch.object(
                    toolchain,
                    "_python_install_roots",
                    return_value={
                        root.resolve(),
                        Path(sys.prefix).resolve(),
                        Path(sys.base_prefix).resolve(),
                    },
                ),
                patch.object(
                    toolchain.importlib.metadata, "distribution", return_value=pip_dist
                ),
                patch.object(toolchain, "_MAX_PACKAGE_FILES", 0),
            ):
                with self.assertRaisesRegex(ToolchainUnavailable, "limit"):
                    toolchain._distribution_dependency_payload(fixture)
        with patch.object(
            toolchain.importlib.metadata,
            "distribution",
            side_effect=importlib.metadata.PackageNotFoundError("pip"),
        ):
            with self.assertRaisesRegex(ToolchainUnavailable, "pip is required"):
                toolchain._distribution_dependency_payload(fixture)

    def test_dependency_distribution_count_limit_fails_closed(self):
        from scripts.environment.toolchains import toolchain_identity as toolchain

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = {}
            for index in range(64):
                name = f"dep{index}"
                (root / f"{name}.py").write_text(name, encoding="utf-8")
                entries[name] = SimpleNamespace(
                    metadata={"Name": name},
                    files=[Path(f"{name}.py")],
                    locate_file=lambda item, root=root: root / item,
                    version="1.0",
                    requires=[f"dep{index + 1}>=1"] if index < 63 else [],
                )
            (root / "tool.py").write_text("tool", encoding="utf-8")
            selected = SimpleNamespace(
                metadata={"Name": "tool"},
                files=[Path("tool.py")],
                locate_file=lambda item: root / item,
                version="1.0",
                requires=["dep0>=1"],
            )
            pip_dist = importlib.metadata.distribution("pip")
            with (
                patch.object(
                    toolchain,
                    "_python_install_roots",
                    return_value={
                        root.resolve(),
                        Path(sys.prefix).resolve(),
                        Path(sys.base_prefix).resolve(),
                    },
                ),
                patch.object(
                    toolchain.importlib.metadata,
                    "distribution",
                    side_effect=lambda name: (
                        pip_dist if name == "pip" else entries[name]
                    ),
                ),
            ):
                with self.assertRaisesRegex(ToolchainUnavailable, "closure limit"):
                    toolchain._distribution_dependency_payload(selected)

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
            # macOS /tmp can inherit a non-member group; chmod then clears SGID.
            # Set this owned fixture's group before capturing the original identity.
            os.chown(program, -1, os.getegid())
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

    def test_node_python_child_binds_path_interpreter(self):
        """Node 的 python3 子进程身份来自真实 PATH，不冒用验证器解释器。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _script(root / "node", "exit 0")
            _script(root / "python3", "exit 0")
            check = {
                "command": ["node", "fixture.mjs"],
                "required_environment": ["node", "python3"],
            }
            env = {"PATH": str(root)}
            original = resolve_toolchain(check, env)
            self.assertEqual(
                str((root / "python3").resolve()),
                original["tools"]["python3"]["realpath"]
            )
            _script(root / "python3", "exit 1")
            changed = resolve_toolchain(check, env)
            self.assertEqual(original["executor"], changed["executor"])
            self.assertNotEqual(
                original["tools"]["python3"], changed["tools"]["python3"]
            )
            (root / "python3").unlink()
            with self.assertRaises(ToolchainUnavailable):
                resolve_toolchain(check, env)

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
                "required_environment": ["redis-test-endpoint"],
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
        with patch(
            "scripts.environment.toolchains.toolchain_identity.sys.platform", "linux"
        ):
            self.assertNotIn("apple_payload", self.resolve()["executor"])
        self.probe.assert_not_called()
        with patch(
            "scripts.environment.toolchains.toolchain_identity.shutil.which",
            return_value=str(self.payload),
        ):
            self.assertNotIn("apple_payload", self.resolve()["executor"])
        self.probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
