"""Docker 子进程环境白名单与私有 FD 的合成回归。"""

import pathlib
import shlex
import subprocess
import tempfile
import unittest

SOURCE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "release/runtime/release-installation-docker.sh"
)
STATE = SOURCE.with_name("release-installation-state.sh")
PRODUCT = {
    "LEXIFLOW_PLATFORM": "linux/amd64",
    "LEXIFLOW_API_IMAGE": "api@sha256:fixed",
    "LEXIFLOW_POSTGRES_IMAGE": "pg@sha256:fixed",
    "LEXIFLOW_DATASET_SHA256": "1" * 64,
    "LEXIFLOW_INSTALLATION_ID": "d" * 32,
}
BASE = {"PATH", "HOME", "DOCKER_CONFIG", "LC_ALL"}


def nul_map(path):
    return dict(
        part.decode().split("=", 1) for part in path.read_bytes().split(b"\0") if part
    )


def nul_args(path):
    return [part.decode() for part in path.read_bytes().split(b"\0") if part]


class DockerEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.install = self.root / "install"
        self.release_key = "b" * 64
        self.secret_dir = self.install / "releases" / self.release_key
        self.secret_dir.mkdir(parents=True)
        self.release_root = self.root / "release"
        self.release_root.mkdir()
        (self.release_root / "data.zip").write_bytes(b"dataset")
        self.postgres = self.secret_dir / "postgres-password"
        self.app = self.secret_dir / "app-password"
        self.set_secret(self.postgres, "a" * 64)
        self.set_secret(self.app, "c" * 64)
        self.identity_env = self.root / "identity.env"
        self.docker_env = self.root / "docker.env"
        self.docker_args = self.root / "docker.args"
        self.inspect_env = self.root / "inspect.env"
        self.inspect_args = self.root / "inspect.args"
        self.fd_state = self.root / "fd.state"
        self.stdin_state = self.root / "stdin.state"
        self.tool_env = self.root / "file-tools.env"

        def q(path):
            return shlex.quote(str(path))

        stub = self.bin / "docker"
        stub.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = context ] && [ "$2" = inspect ]; then\n'
            f"  /usr/bin/env -0 > {q(self.inspect_env)}\n"
            f"  printf '%s\\0' \"$@\" > {q(self.inspect_args)}\n"
            "  printf 'unix:///context.sock\\n'\n  exit 0\nfi\n"
            'if [ "$1" = --host ] && [ "$3" = version ]; then printf "Engine\\n"; exit 0; fi\n'
            'if [ "$1" = --host ] && [ "$4" = --format ] && [ "$5" = "{{.ID}}" ]; then\n'
            f"  /usr/bin/env -0 > {q(self.identity_env)}\n"
            '  printf "synthetic-daemon-id\\n"; exit 0; fi\n'
            f"/usr/bin/env -0 > {q(self.docker_env)}\n"
            f"printf '%s\\0' \"$@\" > {q(self.docker_args)}\n"
            f"if (: <&3) 2>/dev/null; then echo open > {q(self.fd_state)}; else echo closed > {q(self.fd_state)}; fi\n"
            f'if IFS= read -r line || [ -n "$line" ]; then echo data > {q(self.stdin_state)}; else echo eof > {q(self.stdin_state)}; fi\n'
        )
        stub.chmod(0o755)
        for name in ("stat", "wc"):
            tool = self.bin / name
            tool.write_text(
                "#!/bin/sh\n"
                f"/usr/bin/env -0 >> {q(self.tool_env)}\n"
                f"printf '\\n--END--\\n' >> {q(self.tool_env)}\n"
                f'exec /usr/bin/{name} "$@"\n'
            )
            tool.chmod(0o755)
        self.entry = self.root / "entry.sh"
        self.entry.write_text(
            STATE.read_text() + '\n. "$1"\n'
            "lf_lock_check() { :; }\n"
            'LF_ROOT="$TEST_INSTALL" LF_CURRENT_KEY="$TEST_KEY" LF_INSTALL_ID='
            + "d"
            * 32
            + "\n"
            'LF_PROJECT="lf_${LF_INSTALL_ID}_$(printf "%s" "$LF_CURRENT_KEY" | cut -c1-16)"\n'
            "LF_PLATFORM=linux/amd64 LF_API_IMAGE=api@sha256:fixed LF_POSTGRES_IMAGE=pg@sha256:fixed\n"
            "LF_DATASET_SHA256="
            + "1" * 64
            + " LF_DATASET_PATH=data.zip LF_DATASET_BYTES=7 LF_INSTALL_ID="
            + "d" * 32
            + "\n"
            'LF_RELEASE_ROOT="$TEST_RELEASE" LF_COMPOSE_PATH=compose.yaml\n'
            "lf_check_file() { :; }\n"
            'before="$LF_COMPOSE_SECRET|$postgres_secret|$app_secret"\n'
            'case "$2" in\n'
            "  compose) lf_compose up -d postgres;;\n"
            '  initialize) lf_compose run --rm --no-deps --name "${LF_PROJECT}-initialize-1" initialize;;\n'
            '  private-stdin) printf "private-stdin-payload" | lf_docker_run 30 --stdin-fd3 env -i "PATH=${PATH}" "HOME=${HOME}" "DOCKER_CONFIG=${DOCKER_CONFIG}" LC_ALL=C docker --host "$LF_DOCKER_ENDPOINT" private-import;;\n'
            "  private-check) lf_private_sources_check;;\n"
            "  noncompose) lf_docker info --format fixed;;\n"
            "  version) lf_docker compose version;;\n"
            "  *) exit 2;;\n"
            "esac\n"
            "result=$?\n"
            'after="$LF_COMPOSE_SECRET|$postgres_secret|$app_secret"\n'
            '[ "$before" = "$after" ] || exit 88\n'
            'exit "$result"\n'
        )
        self.poison = {
            "DOCKER_TLS": "1",
            "DOCKER_TLS_VERIFY": "1",
            "DOCKER_API_VERSION": "poison-api",
            "DOCKER_CUSTOM_HEADERS": "poison-header",
            "DOCKER_DEFAULT_PLATFORM": "poison-platform",
            "COMPOSE_FILE": "poison-compose",
            "COMPOSE_PROJECT_NAME": "poison-project",
            "COMPOSE_PROFILES": "poison-profile",
            "HTTP_PROXY": "http://proxy.invalid",
            "HTTPS_PROXY": "http://proxy.invalid",
            "ALL_PROXY": "http://proxy.invalid",
            "NO_PROXY": "poison",
            "http_proxy": "http://proxy.invalid",
            "https_proxy": "http://proxy.invalid",
            "all_proxy": "http://proxy.invalid",
            "no_proxy": "poison",
            "JAVA_TOOL_OPTIONS": "poison-java",
            "PYTHONPATH": "poison-python",
            "NODE_OPTIONS": "poison-node",
            "LD_PRELOAD": "poison-loader",
            "DYLD_INSERT_LIBRARIES": "poison-dyld",
            "UNRELATED_POISON": "keep-out",
            "LF_PRIVATE_POISON": "keep-out",
            "LF_DOCKER_ENDPOINT": "tcp://inherited.invalid:2375",
            "LF_COMPOSE_SECRET": "parent-lf",
            "postgres_secret": "parent-postgres-private",
            "app_secret": "parent-app-private",
            **{key: "poison-" + key for key in PRODUCT},
            "LEXIFLOW_COMPOSE_POSTGRES_PASSWORD": "parent-postgres-secret",
            "LEXIFLOW_COMPOSE_APP_PASSWORD": "parent-app-secret",
            "LEXIFLOW_DATASET_FILE": "poison-file",
            "LEXIFLOW_RELEASE_KEY": "poison-key",
        }
        self.env = {
            **self.poison,
            "PATH": str(self.bin) + ":/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.root / "home"),
            "DOCKER_CONFIG": str(self.root / "docker-config"),
            "TEST_INSTALL": str(self.install),
            "TEST_RELEASE": str(self.root / "release"),
            "TEST_KEY": self.release_key,
            "DOCKER_HOST": "unix:///host.sock",
        }

    @staticmethod
    def set_secret(path, value, mode=0o600):
        path.unlink(missing_ok=True)
        path.write_text(value)
        path.chmod(mode)

    def invoke(self, action="compose", *, context=None, host="unix:///host.sock"):
        env = dict(self.env)
        env["DOCKER_HOST"] = host
        if context is not None:
            env["DOCKER_CONTEXT"] = context
        endpoint = "unix:///context.sock" if context is not None else host
        self.set_secret(
            self.install / "engine",
            f"schema=lexiflow-engine-v1\nendpoint={endpoint}\ndaemon_id=synthetic-daemon-id\n",
        )
        result = subprocess.run(
            ["/bin/sh", str(self.entry), str(SOURCE), action],
            env=env,
            input=b"",
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(
            env,
            {
                **self.env,
                "DOCKER_HOST": host,
                **({"DOCKER_CONTEXT": context} if context is not None else {}),
            },
        )
        self.assertNotEqual(result.returncode, 88, "父 shell 的合成值被更改")
        return result

    def assert_clean(self, env, allowed):
        # /bin/sh 的 CLI 替身自身生成这些 bookkeeping 项；其余键必须精确白名单。
        shell_generated = {"PWD", "SHLVL", "_"}
        self.assertEqual(set(env) - shell_generated, allowed)
        self.assertEqual(set(env) & shell_generated, shell_generated)
        self.assertEqual(env["PATH"], self.env["PATH"])
        if "HOME" in env:
            self.assertEqual(env["HOME"], self.env["HOME"])
        if "DOCKER_CONFIG" in env:
            self.assertEqual(env["DOCKER_CONFIG"], self.env["DOCKER_CONFIG"])
        self.assertEqual(env["LC_ALL"], "C")

    def assert_compose(self, endpoint):
        self.assert_clean(nul_map(self.identity_env), {"PATH", "LC_ALL"})
        env = nul_map(self.docker_env)
        self.assert_clean(env, BASE | set(PRODUCT) | {"LEXIFLOW_RELEASE_KEY"})
        for key, value in {**PRODUCT, "LEXIFLOW_RELEASE_KEY": self.release_key}.items():
            self.assertEqual(env[key], value)
        args = nul_args(self.docker_args)
        self.assertEqual(args[:3], ["--host", endpoint, "compose"])
        self.assertEqual(
            args[3:9],
            [
                "--project-name",
                "lf_" + "d" * 32 + "_" + "b" * 16,
                "--project-directory",
                str(self.root / "release"),
                "--env-file",
                "/dev/null",
            ],
        )
        self.assertEqual(args[9:11], ["-f", str(self.root / "release/compose.yaml")])
        self.assertEqual(args[11:], ["up", "-d", "postgres"])
        self.assertFalse(
            any(value in arg for arg in args for value in ("a" * 64, "c" * 64))
        )
        self.assertEqual(self.fd_state.read_text().strip(), "closed")
        self.assertEqual(self.stdin_state.read_text().strip(), "eof")

    def test_host_and_context_compose_protocol(self):
        result = self.invoke()
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr), (0, b"", b"")
        )
        self.assertFalse(self.inspect_env.exists())
        self.assert_compose("unix:///host.sock")
        result = self.invoke(context="synthetic", host="tcp://poison.invalid:2375")
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr), (0, b"", b"")
        )
        self.assert_clean(nul_map(self.inspect_env), BASE)
        self.assertEqual(
            nul_args(self.inspect_args),
            [
                "context",
                "inspect",
                "--format",
                '{{(index .Endpoints "docker").Host}}',
                "synthetic",
            ],
        )
        self.assert_compose("unix:///context.sock")

    def test_noncompose_and_version_exclude_product_and_secret(self):
        for action, trailing in (
            ("noncompose", ["info", "--format", "fixed"]),
            ("version", ["compose", "version"]),
        ):
            with self.subTest(action=action):
                result = self.invoke(action)
                self.assertEqual(
                    (result.returncode, result.stdout, result.stderr), (0, b"", b"")
                )
                self.assert_clean(nul_map(self.docker_env), BASE)
                self.assertEqual(
                    nul_args(self.docker_args),
                    ["--host", "unix:///host.sock", *trailing],
                )
                self.assertEqual(self.stdin_state.read_text().strip(), "eof")
                self.assertFalse(self.inspect_env.exists())

    def test_secret_not_exported_to_later_file_tools(self):
        self.env["TEST_SECRET"] = str(self.postgres)
        result = self.invoke("private-check")
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr), (0, b"", b"")
        )
        snapshots = self.tool_env.read_bytes().split(b"\n--END--\n")
        self.assertGreaterEqual(len(snapshots), 2)
        for snapshot in snapshots:
            if snapshot:
                self.assertNotIn(b"a" * 64, snapshot)
                self.assertNotIn(b"c" * 64, snapshot)
                self.assertNotIn(b"a" * 64, snapshot)
                self.assertNotIn(b"c" * 64, snapshot)

    def test_each_bad_secret_fails_closed_independently(self):
        cases = (
            "65-lf",
            "65-no-lf",
            "invalid-character",
            "mode",
            "file-link",
            "ancestor-link",
        )
        for case in cases:
            with self.subTest(case=case):
                self.set_secret(self.postgres, "a" * 64)
                self.set_secret(self.app, "c" * 64)
                releases = self.install / "releases"
                if releases.is_symlink():
                    releases.unlink()
                    (self.install / "real-releases").rename(releases)
                if case == "65-lf":
                    self.set_secret(self.postgres, "a" * 64 + "\n")
                elif case == "65-no-lf":
                    self.set_secret(self.postgres, "a" * 65)
                elif case == "invalid-character":
                    self.set_secret(self.postgres, "g" * 64)
                elif case == "mode":
                    self.set_secret(self.postgres, "a" * 64, 0o640)
                elif case == "file-link":
                    target = self.root / "target-secret"
                    self.set_secret(target, "a" * 64)
                    self.postgres.unlink()
                    self.postgres.symlink_to(target)
                elif case == "ancestor-link":
                    releases.rename(self.install / "real-releases")
                    releases.symlink_to(self.install / "real-releases")
                self.docker_env.unlink(missing_ok=True)
                self.env["TEST_SECRET"] = str(self.postgres)
                result = self.invoke("private-check")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout + result.stderr, b"")
                self.assertFalse(self.docker_env.exists())

    def test_private_input_uses_opt_in_stdin_without_argv_or_environment_copy(self):
        result = self.invoke("private-stdin")
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr), (0, b"", b"")
        )
        self.assertEqual(self.fd_state.read_text().strip(), "closed")
        self.assertEqual(self.stdin_state.read_text().strip(), "data")
        self.assertNotIn(b"private-stdin-payload", self.docker_args.read_bytes())
        self.assertNotIn(b"private-stdin-payload", self.docker_env.read_bytes())
        self.assert_clean(nul_map(self.docker_env), BASE)

    def test_initialize_budget_remains_600_ordinary_30(self):
        source = SOURCE.read_text()
        self.assertIn("budget=600", source)
        self.assertIn("else budget=30; fi", source)
        self.assertIn('lf_docker_run "$budget" env -i', source)
        result = self.invoke("initialize")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            nul_args(self.docker_args)[11:],
            [
                "run",
                "--rm",
                "--no-deps",
                "--name",
                "lf_" + "d" * 32 + "_" + "b" * 16 + "-initialize-1",
                "initialize",
            ],
        )


if __name__ == "__main__":
    unittest.main()
