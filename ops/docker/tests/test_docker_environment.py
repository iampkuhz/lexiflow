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
        self.release_root = self.root / "release"
        self.release_root.mkdir()
        (self.release_root / "data.zip").write_bytes(b"dataset")
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
            "LF_PLATFORM=linux/amd64 LF_API_IMAGE=api@sha256:fixed\n"
            "LF_DATASET_SHA256="
            + "1" * 64
            + " LF_DATASET_PATH=data.zip LF_DATASET_BYTES=7 LF_INSTALL_ID="
            + "d" * 32
            + "\n"
            'LF_RELEASE_ROOT="$TEST_RELEASE" LF_COMPOSE_PATH=compose.yaml\n'
            "lf_check_file() { :; }\n"
            "lf_docker_endpoint || exit 1\n"
            'before="$LF_COMPOSE_PRIVATE|$private_value"\n'
            'case "$2" in\n'
            "  compose) lf_compose up -d api;;\n"
            '  initialize) lf_compose run --rm --no-deps --name "${LF_PROJECT}-initialize-1" initialize;;\n'
            '  private-stdin) printf "private-stdin-payload" | lf_docker_run 30 --stdin-fd3 env -i "PATH=${PATH}" "HOME=${HOME}" "DOCKER_CONFIG=${DOCKER_CONFIG}" LC_ALL=C docker --host "$LF_DOCKER_ENDPOINT" private-import;;\n'
            "  noncompose) lf_docker info --format fixed;;\n"
            "  version) lf_docker compose version;;\n"
            "  *) exit 2;;\n"
            "esac\n"
            "result=$?\n"
            'after="$LF_COMPOSE_PRIVATE|$private_value"\n'
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
            # 空值仍检测键泄漏；无效库会在进入被测 shell 前由加载器报错。
            "LD_PRELOAD": "",
            "DYLD_INSERT_LIBRARIES": "",
            "UNRELATED_POISON": "keep-out",
            "LF_PRIVATE_POISON": "keep-out",
            "LF_DOCKER_ENDPOINT": "tcp://inherited.invalid:2375",
            "LF_COMPOSE_PRIVATE": "parent-lf",
            "private_value": "parent-private-value",
            **{key: "poison-" + key for key in PRODUCT},
            "LEXIFLOW_PRIVATE_VALUE": "parent-private-value",
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

    def invoke(self, action="compose", *, context=None, host="unix:///host.sock"):
        env = dict(self.env)
        env["DOCKER_HOST"] = host
        if context is not None:
            env["DOCKER_CONTEXT"] = context
        result = subprocess.run(
            ["/bin/sh", str(self.entry), str(SOURCE), action],
            env=env,
            input=b"",
            capture_output=True,
            timeout=10,
            check=False,
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
        # bash 与 dash 自动生成的项不同，不要求每种 shell 都生成全部项。
        self.assertEqual(env["PATH"], self.env["PATH"])
        if "HOME" in env:
            self.assertEqual(env["HOME"], self.env["HOME"])
        if "DOCKER_CONFIG" in env:
            self.assertEqual(env["DOCKER_CONFIG"], self.env["DOCKER_CONFIG"])
        self.assertEqual(env["LC_ALL"], "C")

    def test_shell_bookkeeping_does_not_relax_environment_allowlist(self):
        clean = {key: self.env[key] for key in BASE - {"LC_ALL"}}
        clean["LC_ALL"] = "C"
        for generated in (
            {},
            {"PWD": "/synthetic"},
            {"PWD": "/synthetic", "SHLVL": "1", "_": "env"},
        ):
            with self.subTest(generated=generated):
                self.assert_clean({**clean, **generated}, BASE)
        for unexpected in (
            "LD_PRELOAD",
            "DYLD_INSERT_LIBRARIES",
            "UNRELATED_POISON",
            "LEXIFLOW_PRIVATE_VALUE",
        ):
            with self.subTest(unexpected=unexpected), self.assertRaises(AssertionError):
                self.assert_clean({**clean, unexpected: ""}, BASE)

    def assert_compose(self, endpoint):
        self.assertFalse(self.identity_env.exists())
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
        self.assertEqual(args[11:], ["up", "-d", "api"])
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

    def test_noncompose_and_version_exclude_product_and_private_values(self):
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
        self.assertIn('case "$1" in 1|30|600)', source)
        self.assertIn('lf_docker() { lf_docker_budget 30 "$@"; }', source)
        self.assertIn('lf_docker_run 600 env -i', source)
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
