"""Docker installation contract tests."""

import hashlib
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

import yaml

from ops.docker.tests.health_probe_fixture import HealthProbeFixture

ROOT = pathlib.Path(__file__).resolve().parents[3]
DOCKER = ROOT / "ops/docker"


def _fast_fixture_identity(stub):
    """仅短路本例固定daemon身份；业务模型和其他参数保持原生执行。"""
    model = stub.with_name("docker-model.py")
    stub.rename(model)
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$#" -eq 5 ] && [ "$1" = --host ] && '
        '[ "$2" = unix:///synthetic.sock ] && [ "$3" = info ] && '
        '[ "$4" = --format ] && [ "$5" = \'{{.ID}}\' ]; then\n'
        "  printf '%s\\n' synthetic-daemon-id; exit $?\n"
        "fi\n" + "exec " + shlex.quote(str(model)) + ' "$@"\n'
    )
    stub.chmod(0o755)


class DockerContractTest(unittest.TestCase):
    def test_fixture_identity_route_is_exact_and_other_commands_remain_native(self):
        with tempfile.TemporaryDirectory(prefix="lf identity '雪 ") as td:
            root = pathlib.Path(td).resolve()
            stub, log = root / "docker", root / "calls.json"
            stub.write_text(
                f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n"
                f"Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n"
                "sys.stdout.buffer.write(b'native\\x00out\\n')\n"
                "sys.stderr.buffer.write(b'native-err\\n')\nsys.exit(7)\n"
            )
            stub.chmod(0o755)
            _fast_fixture_identity(stub)
            exact = ["--host", "unix:///synthetic.sock", "info", "--format", "{{.ID}}"]
            direct = subprocess.run([str(stub), *exact], capture_output=True, timeout=5)
            self.assertEqual(
                (direct.returncode, direct.stdout, direct.stderr),
                (0, b"synthetic-daemon-id\n", b""),
            )
            self.assertFalse(log.exists())
            for argv in (
                [],
                exact[:-1],
                [*exact, "extra"],
                ["--host", "unix:///other.sock", *exact[2:]],
                [*exact[:2], "version", *exact[3:]],
                [*exact[:-1], "{{.Other}}"],
                ["inspect", "space value", "", "line\nquote'雪"],
            ):
                with self.subTest(argv=argv):
                    result = subprocess.run(
                        [str(stub), *argv], capture_output=True, timeout=5
                    )
                    self.assertEqual(
                        (result.returncode, result.stdout, result.stderr),
                        (7, b"native\x00out\n", b"native-err\n"),
                    )
                    self.assertEqual(json.loads(log.read_text()), argv)

    def test_private_transfer_replays_authenticated_journal_before_fixed_order(self):
        source = ROOT / "ops/release/runtime/release-installation-docker.sh"
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            key = "a" * 64
            project = "lf-test"
            install = "0123456789abcdef0123456789abcdef"
            release = base / "releases" / key
            release.mkdir(parents=True)
            dataset = base / "dataset.zip"
            dataset.write_bytes(b"synthetic dataset")
            secrets = {
                "pg64hex": release / "postgres-password",
                "app64hex": release / "app-password",
                "zip": dataset,
            }
            secrets["pg64hex"].write_bytes(b"a" * 64)
            secrets["app64hex"].write_bytes(b"b" * 64)
            for file in secrets.values():
                file.chmod(0o600)
            hashes = {
                mode: hashlib.sha256(file.read_bytes()).hexdigest()
                for mode, file in secrets.items()
            }
            volumes = {
                "pg64hex": f"{project}_postgres-secret",
                "app64hex": f"{project}_app-secret",
                "zip": f"{project}_dataset",
            }
            script = base / "driver.sh"
            script.write_text(
                'set -eu\n. "$1"\n'
                "lf_private_sources_check() { :; }\n"
                "lf_sha256() { shasum -a 256 \"$1\" | cut -d ' ' -f1; }\n"
                "lf_operation_preflight() { :; }\n"
                "lf_operation_volume_labels() { :; }\n"
                "lf_random_hex() { printf '%032d\\n' 0; }\n"
                'lf_private_import() { lf_transfer_journal_begin "$1" "$2" "$4" "$5" || return 1; '
                'printf "%s\\n" "$1" >> "$LF_TEST_LOG"; '
                '[ "$LF_TEST_CANCEL" != "$1" ] || kill -TERM "$$"; lf_transfer_journal_remove; }\n'
                "lf_lock_check() { :; }\n"
                "lf_private_files_import\n"
            )
            journal = release / ".private-file-transfer"
            log = base / "transfers.log"
            env = {
                **os.environ,
                "LF_ROOT": str(base),
                "LF_RELEASE_ROOT": str(base),
                "LF_CURRENT_KEY": key,
                "LF_PROJECT": project,
                "LF_INSTALL_ID": install,
                "LF_DATASET_PATH": "dataset.zip",
                "LF_DATASET_BYTES": str(dataset.stat().st_size),
                "LF_DATASET_SHA256": hashes["zip"],
                "LF_TEST_LOG": str(log),
                "LF_LOCK_TOKEN": "f" * 32,
                "LF_TEST_CANCEL": "none",
            }

            def write_journal(
                mode, digest=None, uuid="01234567-89ab-cdef-0123-456789abcdef"
            ):
                journal.write_text(
                    "schema=lexiflow-private-transfer-v1\n"
                    f"installation={install}\nrelease={key}\n"
                    f"volume={volumes[mode]}\nmode={mode}\nuuid={uuid}\n"
                    f"bytes={secrets[mode].stat().st_size}\nsha256={digest or hashes[mode]}\n"
                )
                journal.chmod(0o600)

            for mode in ("app64hex", "zip"):
                with self.subTest(mode=mode):
                    log.unlink(missing_ok=True)
                    write_journal(mode)
                    result = subprocess.run(
                        ["/bin/sh", str(script), str(source)],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        log.read_text().splitlines(),
                        [mode, "pg64hex", "app64hex", "zip"],
                    )
                    self.assertFalse(journal.exists())
            for mode in ("app64hex", "zip"):
                with self.subTest(cancelled_mode=mode):
                    log.unlink(missing_ok=True)
                    result = subprocess.run(
                        ["/bin/sh", str(script), str(source)],
                        env={**env, "LF_TEST_CANCEL": mode},
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    self.assertEqual(
                        result.returncode, -15, "real TERM interrupts journal removal"
                    )
                    self.assertTrue(
                        journal.exists(), "cancel retains authenticated journal"
                    )
                    self.assertIn(f"mode={mode}\n", journal.read_text())
                    self.assertEqual(
                        log.read_text().splitlines(),
                        ["pg64hex", "app64hex"]
                        if mode == "app64hex"
                        else ["pg64hex", "app64hex", "zip"],
                    )
                    log.unlink()
                    result = subprocess.run(
                        ["/bin/sh", str(script), str(source)],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        log.read_text().splitlines(),
                        [mode, "pg64hex", "app64hex", "zip"],
                    )
                    self.assertFalse(journal.exists())
            for digest, uuid in (
                ("0" * 64, "01234567-89ab-cdef-0123-456789abcdef"),
                (None, "0123456--89ab-cdef-0123-456789abcdef"),
            ):
                log.unlink(missing_ok=True)
                write_journal("app64hex", digest=digest, uuid=uuid)
                result = subprocess.run(
                    ["/bin/sh", str(script), str(source)],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(journal.exists(), "unknown journal is preserved")
                self.assertFalse(
                    log.exists(), "unknown journal does not begin transfer"
                )

    def test_image_import_checks_actual_image_os_and_architecture(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            script = root / "import.sh"
            log = root / "actions"
            script.write_text(
                '. "$1"\n'
                "lf_docker_preflight() { LF_HOST_PLATFORM=$TEST_HOST; }\n"
                'lf_docker() { printf "%s\\n" "$*" >> "$TEST_LOG"; '
                'if [ "$1" = load ]; then [ "$TEST_MODE" != load-fail ]; return $?; fi\n'
                "for value do last=$value; done\n"
                '[ "$TEST_MODE" != inspect-fail ] || return 1\n'
                'if [ "$last" = "$LF_API_IMAGE" ]; then printf "%s\\n" "$TEST_API"; '
                'else printf "%s\\n" "$TEST_DATABASE"; fi; }\n'
                "lf_docker_import\n"
            )
            api, database = "sha256:" + "a" * 64, "sha256:" + "b" * 64
            base = {
                **os.environ,
                "TEST_LOG": str(log),
                "TEST_MODE": "ok",
                "TEST_HOST": "linux/amd64",
                "LF_PLATFORM": "linux/amd64",
                "LF_RELEASE_ROOT": str(root),
                "LF_API_ARCHIVE": "api.tar",
                "LF_POSTGRES_ARCHIVE": "postgres.tar",
                "LF_API_IMAGE": api,
                "LF_POSTGRES_IMAGE": database,
                "TEST_API": api + "|linux|amd64",
                "TEST_DATABASE": database + "|linux|amd64",
            }
            cases = [
                ({}, True),
                ({"TEST_HOST": "linux/arm64"}, False),
                ({"TEST_API": api + "|linux|arm64"}, False),
                ({"TEST_DATABASE": database + "|windows|amd64"}, False),
                ({"TEST_API": database + "|linux|amd64"}, False),
                ({"TEST_MODE": "load-fail"}, False),
                ({"TEST_MODE": "inspect-fail"}, False),
                (
                    {
                        "TEST_HOST": "linux/arm64",
                        "LF_PLATFORM": "linux/arm64",
                        "TEST_API": api + "|linux|arm64",
                        "TEST_DATABASE": database + "|linux|arm64",
                    },
                    True,
                ),
            ]
            for overrides, passed in cases:
                log.unlink(missing_ok=True)
                result = subprocess.run(
                    [
                        "/bin/sh",
                        str(script),
                        str(
                            ROOT / "ops/release/runtime/release-installation-docker.sh"
                        ),
                    ],
                    env={**base, **overrides},
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(result.returncode == 0, passed, overrides)
                self.assertEqual(result.stdout + result.stderr, "")
                if overrides == {"TEST_HOST": "linux/arm64"}:
                    self.assertFalse(log.exists())
                if overrides == {"TEST_MODE": "load-fail"}:
                    self.assertEqual(len(log.read_text().splitlines()), 1)

    def test_restart_waits_for_database_and_does_not_rerun_initialization(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            script = root / "restart.sh"
            log = root / "actions"
            script.write_text(
                '. "$1"\nLF_PROJECT=synthetic\n'
                'step() { printf "%s\\n" "$*" >> "$TEST_LOG"; [ "$*" != "$TEST_FAIL" ]; }\n'
                "lf_operation_preflight() { :; }\n"
                "lf_operation_temp_cleanup() { :; }\n"
                'lf_operation_compose() { lf_compose "$@"; }\n'
                "lf_docker_import() { step import; }\n"
                "lf_private_sources_check() { :; }\n"
                "lf_private_files_import() { step private-files; }\n"
                "lf_operation_volume_labels() { :; }\n"
                'lf_compose() { step compose "$@"; }\n'
                'lf_wait_healthy() { step healthy "$@"; }\n'
                "lf_docker_start\n"
            )
            steps = [
                "import",
                "private-files",
                "compose up -d --no-deps postgres",
                "healthy synthetic-postgres-1",
                "compose up -d --no-deps api",
                "healthy synthetic-api-1",
            ]
            for failure in ("none", *steps):
                log.unlink(missing_ok=True)
                result = subprocess.run(
                    [
                        "/bin/sh",
                        str(script),
                        str(
                            ROOT / "ops/release/runtime/release-installation-docker.sh"
                        ),
                    ],
                    env={**os.environ, "TEST_LOG": str(log), "TEST_FAIL": failure},
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(result.returncode == 0, failure == "none")
                expected = (
                    steps if failure == "none" else steps[: steps.index(failure) + 1]
                )
                self.assertEqual(log.read_text().splitlines(), expected)
                self.assertEqual(result.stdout + result.stderr, "")

    def test_installation_signal_retains_lock_and_normal_exit_releases_only_owned_lock(
        self,
    ):
        source = ROOT / "ops/release/runtime/release-installation-state.sh"
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            script = root / "signals.sh"
            script.write_text(
                '. "$1"\nLF_ROOT=$2\n'
                'if [ "$3" = init ]; then lf_owner_init || exit 20; '
                "else lf_owner_init || exit 20; lf_lock_release; lf_lock_acquire || exit 21; fi\n"
                'if [ "$5" = replaced ]; then printf "%s\\n" replacement > "$LF_ROOT/.lock/token"; fi\n'
                'cat "$LF_ROOT/.lock/token" > "$LF_ROOT/token.before"\n'
                'if [ "$4" != normal ]; then kill -s "$4" "$$"; '
                'printf "%s\\n" forbidden > "$LF_ROOT/continued"; fi\n'
            )
            for mode in ("init", "acquire"):
                for signal, code in (
                    ("normal", 0),
                    ("HUP", 129),
                    ("INT", 130),
                    ("TERM", 143),
                ):
                    for ownership in ("owned", "replaced"):
                        installation = root / (mode + signal + ownership)
                        result = subprocess.run(
                            [
                                "/bin/sh",
                                str(script),
                                str(source),
                                str(installation),
                                mode,
                                signal,
                                ownership,
                            ],
                            capture_output=True,
                            text=True,
                            timeout=3,
                        )
                        self.assertEqual(result.returncode, code, result.stderr)
                        self.assertFalse((installation / "continued").exists())
                        if ownership == "owned":
                            if signal == "normal":
                                self.assertFalse((installation / ".lock").exists())
                            else:
                                self.assertTrue(
                                    (installation / ".lock/token").is_file()
                                )
                                self.assertEqual(
                                    (installation / ".lock/token").read_bytes(),
                                    (installation / "token.before").read_bytes(),
                                )
                        else:
                            self.assertEqual(
                                (installation / ".lock/token").read_text(),
                                "replacement\n",
                            )

    def test_installation_hash_rejects_tool_failure_and_invalid_digest(self):
        source = ROOT / "ops/release/runtime/release-artifact-verification.sh"
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            script = root / "digest.sh"
            script.write_text(
                source.read_text().split("# 在 cd -P", 1)[0] + '\nlf_sha256 "$2"\n'
            )
            actual = root / "file with spaces"
            actual.write_bytes(b"abc")
            result = subprocess.run(
                ["/bin/sh", str(script), str(source), str(actual)],
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout,
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad\n",
            )
            actual.unlink()
            result = subprocess.run(
                ["/bin/sh", str(script), str(source), str(actual)],
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout + result.stderr, "")
            (root / "synthetic").write_bytes(b"abc")
            for tool in ("sha256sum", "shasum"):
                bindir = root / tool
                bindir.mkdir()
                stub = bindir / tool
                stub.write_text(
                    '#!/bin/sh\nprintf "%s  %s\\n" "$TEST_DIGEST" "$TEST_NAME"\n'
                    'printf "%s\\n" private-error >&2\nexit "$TEST_EXIT"\n'
                )
                stub.chmod(0o755)
                for digest, status, passed in (
                    ("a" * 64, "0", True),
                    ("a" * 64, "7", False),
                    ("a" * 63, "0", False),
                    ("g" * 64, "0", False),
                    ("", "0", False),
                ):
                    env = {
                        "PATH": str(bindir),
                        "TEST_DIGEST": digest,
                        "TEST_EXIT": status,
                        "TEST_NAME": "-",
                    }
                    result = subprocess.run(
                        ["/bin/sh", str(script), str(source), str(root / "synthetic")],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    self.assertEqual(result.returncode == 0, passed)
                    self.assertEqual(result.stdout, digest + "\n" if passed else "")
                    self.assertEqual(result.stderr, "")
                env = {
                    "PATH": str(bindir),
                    "TEST_DIGEST": "a" * 64,
                    "TEST_EXIT": "0",
                    "TEST_NAME": "synthetic",
                }
                result = subprocess.run(
                    ["/bin/sh", str(script), str(source), str(root / "synthetic")],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout + result.stderr, "")

    def test_context_and_image_are_minimal_nonroot(self):
        ignore = (DOCKER / ".dockerignore").read_text()
        self.assertEqual(
            ignore.splitlines(),
            [
                "*",
                "!Dockerfile",
                "!run-container-command.sh",
                "!lexiflow-api.jar",
                "!Dockerfile.postgres",
                "!initialize-postgres-app-role.sh",
            ],
        )
        dockerfile = (DOCKER / "Dockerfile").read_text()
        self.assertIn("ARG JAVA_RUNTIME_IMAGE", dockerfile)
        self.assertIn("COPY --chown=10001:10001 lexiflow-api.jar", dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("--chmod=0555 run-container-command.sh", dockerfile)
        postgres = (DOCKER / "Dockerfile.postgres").read_text()
        self.assertIn("ARG POSTGRES_RUNTIME_IMAGE", postgres)
        self.assertIn(
            "COPY --chmod=0555 initialize-postgres-app-role.sh /docker-entrypoint-initdb.d/10-lexiflow-initialize-postgres-app-role.sh",
            postgres,
        )
        self.assertNotIn("RUN ", postgres)
        self.assertEqual(dockerfile.count("RUN "), 1)
        self.assertIn("mkdir /work && chown 10001:10001 /work", dockerfile)
        for tool in ("/bin/bash", "/usr/bin/timeout", "/usr/bin/env"):
            self.assertIn("test -x " + tool, dockerfile)

    def test_compose_is_private_bounded_and_health_ordered(self):
        cfg = yaml.safe_load((DOCKER / "compose.yaml").read_text())
        services = cfg["services"]
        self.assertEqual(
            set(services), {"postgres", "initialize", "api", "private-file-importer"}
        )
        self.assertEqual(services["api"]["ports"], ["127.0.0.1:18080:8080"])
        self.assertNotIn("ports", services["postgres"])
        self.assertIn(
            "pgdata:/var/lib/postgresql/data", services["postgres"]["volumes"]
        )
        self.assertIn(
            "postgres-secret:/run/postgres-secret:ro", services["postgres"]["volumes"]
        )
        self.assertIn("app-secret:/run/secrets:ro", services["postgres"]["volumes"])
        self.assertEqual(
            services["postgres"]["environment"]["POSTGRES_PASSWORD_FILE"],
            "/run/postgres-secret/postgres-password",
        )
        self.assertEqual(
            services["api"]["depends_on"]["initialize"]["condition"],
            "service_completed_successfully",
        )
        self.assertEqual(
            services["initialize"]["depends_on"]["postgres"]["condition"],
            "service_healthy",
        )
        self.assertEqual(services["api"]["mem_limit"], "1280m")
        self.assertEqual(services["postgres"]["mem_limit"], "768m")
        self.assertTrue(services["api"]["read_only"])
        self.assertEqual(set(services["api"]["cap_drop"]), {"ALL"})
        self.assertTrue(cfg["networks"]["private"]["internal"])
        self.assertIn("initialization-work:/work", services["initialize"]["volumes"])
        self.assertNotIn(
            "initialization-work:/work", services["api"].get("volumes", [])
        )
        self.assertIn("app-secret:/run/secrets:ro", services["api"]["volumes"])
        self.assertIn("dataset:/dataset:ro", services["initialize"]["volumes"])
        self.assertIn("app-secret:/run/secrets:ro", services["initialize"]["volumes"])
        importer = services["private-file-importer"]
        self.assertTrue(importer["read_only"])
        self.assertEqual(importer["network_mode"], "none")
        self.assertEqual(set(importer["cap_drop"]), {"ALL"})
        self.assertIn("no-new-privileges:true", importer["security_opt"])
        self.assertEqual(
            set(cfg["volumes"]),
            {
                "pgdata",
                "initialization-work",
                "postgres-secret",
                "app-secret",
                "dataset",
            },
        )
        self.assertIn(
            "pg_isready -h 127.0.0.1",
            " ".join(services["postgres"]["healthcheck"]["test"]),
        )
        self.assertNotIn("external", cfg["volumes"].get("pgdata", {}))
        for service in services.values():
            self.assertEqual(service["pull_policy"], "never")
            self.assertNotIn("build", service)

    def test_compose_secret_sources_and_reserved_labels(self):
        cfg = yaml.safe_load((DOCKER / "compose.yaml").read_text())
        self.assertNotIn("secrets", cfg)
        for section in ("services", "volumes", "networks"):
            for resource in cfg[section].values():
                self.assertFalse(
                    any(
                        key.startswith("com.docker.compose.")
                        for key in resource.get("labels", {})
                    )
                )
                self.assertEqual(
                    set(resource["labels"]),
                    {"lexiflow.installation", "lexiflow.release"},
                )
        for service in cfg["services"].values():
            self.assertFalse(
                any("COMPOSE_" in key for key in service.get("environment", {}))
            )
            self.assertNotIn("secrets", service)

    def test_compose_child_secret_isolation_and_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            installation = root / "installation with spaces"
            release_key = "c" * 64
            secret_dir = installation / "releases" / release_key
            secret_dir.mkdir(parents=True)
            for name, value in (
                ("postgres-password", "a" * 64),
                ("app-password", "b" * 64),
            ):
                secret = secret_dir / name
                secret.write_text(value)
                secret.chmod(0o600)
            binding = installation / "engine"
            binding.write_text(
                "schema=lexiflow-engine-v1\nendpoint=unix:///synthetic.sock\ndaemon_id=synthetic-daemon-id\n"
            )
            binding.chmod(0o600)
            bindir = root / "bin"
            bindir.mkdir()
            env_log, args_log = root / "env", root / "argv"
            stub = bindir / "docker"
            stub.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = --host ] && [ "$2" = unix:///synthetic.sock ] && [ "$3" = version ]; then printf "Engine\\n"; exit 0; fi\n'
                'if [ "$1" = --host ] && [ "$2" = unix:///synthetic.sock ] && [ "$3" = info ] && [ "$5" = "{{.ID}}" ]; then printf "synthetic-daemon-id\\n"; exit 0; fi\n'
                f"/usr/bin/env -0 > {shlex.quote(str(env_log))}\n"
                f"printf '%s\\0' \"$@\" > {shlex.quote(str(args_log))}\nexit 0\n"
            )
            stub.chmod(0o755)
            source = ROOT / "ops/release/runtime/release-installation-docker.sh"
            script = root / "test.sh"
            script.write_text(
                (ROOT / "ops/release/runtime/release-installation-state.sh").read_text()
                + '\nset -eu\n. "$1"\n'
                "lf_lock_check() { :; }\nlf_compose up -d api\n"
            )
            env = {
                **os.environ,
                "PATH": str(bindir) + ":" + os.environ["PATH"],
                "DOCKER_HOST": "unix:///synthetic.sock",
                "DOCKER_CONTEXT": "",
                "LF_ROOT": str(installation),
                "LF_RELEASE_ROOT": str(secret_dir),
                "LF_CURRENT_KEY": release_key,
                "LF_INSTALL_ID": "d" * 32,
                "LF_PLATFORM": "linux/amd64",
                "LF_API_IMAGE": "sha256:" + "e" * 64,
                "LF_POSTGRES_IMAGE": "sha256:" + "f" * 64,
                "LF_DATASET_SHA256": "1" * 64,
                "LF_DATASET_PATH": "dataset.zip",
                "LF_COMPOSE_PATH": "compose.yaml",
                "LF_PROJECT": "lf_synthetic",
                "COMPOSE_FILE": "poison",
                "COMPOSE_PROJECT_NAME": "poison",
                "HTTP_PROXY": "http://proxy.invalid",
                "LEXIFLOW_COMPOSE_POSTGRES_PASSWORD": "poisoned",
                "LEXIFLOW_COMPOSE_APP_PASSWORD": "poisoned",
            }
            result = subprocess.run(
                ["sh", str(script), str(source)],
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            child_env = env_log.read_bytes()
            args = args_log.read_bytes()
            for value in (
                "a" * 64,
                "b" * 64,
                "LEXIFLOW_COMPOSE_POSTGRES_PASSWORD",
                "LEXIFLOW_COMPOSE_APP_PASSWORD",
                "COMPOSE_FILE",
                "COMPOSE_PROJECT_NAME",
                "HTTP_PROXY",
            ):
                self.assertNotIn(value.encode(), child_env + args)
            self.assertIn(b"LEXIFLOW_INSTALLATION_ID=d", child_env)
            self.assertIn(b"LEXIFLOW_RELEASE_KEY=" + release_key.encode(), child_env)
            self.assertIn(b"compose\0--project-name\0lf_synthetic", args)

    def test_removal_preflights_every_owner_and_deletes_exact_resources(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            project = "lf_" + "a" * 32 + "_" + "b" * 16
            resources = {
                "container": [
                    ["c" * 64, project + "-api-1"],
                    ["d" * 64, project + "-postgres-1"],
                    ["1" * 64, project + "-private-file-importer"],
                ],
                "network": [["e" * 64, project + "_private"]],
                "volume": [
                    [project + "_pgdata", project + "_pgdata"],
                    [
                        project + "_initialization-work",
                        project + "_initialization-work",
                    ],
                    [project + "_postgres-secret", project + "_postgres-secret"],
                    [project + "_app-secret", project + "_app-secret"],
                    [project + "_dataset", project + "_dataset"],
                ],
            }
            binding = root / "engine"
            binding.write_text(
                "schema=lexiflow-engine-v1\nendpoint=unix:///synthetic.sock\ndaemon_id=synthetic-daemon-id\n"
            )
            binding.chmod(0o600)
            state = root / "resources.json"
            log = root / "commands.jsonl"
            bindir = root / "bin"
            bindir.mkdir()
            stub = bindir / "docker"
            stub.write_text(
                "#!"
                + sys.executable
                + "\n"
                + f"STATE = {str(state)!r}\nLOG = {str(log)!r}\nPROJECT = {project!r}\n"
                f"INSTALL_ID = {'a' * 32!r}\nCURRENT_KEY = {'b' * 64!r}\n"
                + r"""
import json, os, sys
from pathlib import Path
state = Path(STATE)
log = Path(LOG)
fixture = json.loads(state.read_text())
mode = fixture["mode"]
values = fixture["resources"]
if sys.argv[1:3] != ["--host", "unix:///synthetic.sock"]: sys.exit(80)
if sys.argv[3:5] == ["version", "--format"]:
    print("Engine")
    sys.exit(0)
if sys.argv[3:] == ["info", "--format", "{{.ID}}"]:
    print("synthetic-daemon-id")
    sys.exit(0)
kind, action, *args = sys.argv[3:]
with log.open("a") as out: out.write(json.dumps([kind, action, *args]) + "\n")
if kind not in values: sys.exit(81)
if action == "ls":
    if mode == "list-fail" and kind == "volume": sys.exit(82)
    records = values[kind]
    if mode == "unknown" and kind == "container": records += [["f" * 64, PROJECT + "-other-1"]]
    if mode == "bad-id" and kind == "container": records = [["short", records[0][1]]]
    for identity, name in records: print(identity + "|" + name)
elif action == "inspect":
    if mode == "inspect-fail" and kind == "volume": sys.exit(83)
    identities = args[args.index('--format') + 2:]
    if not identities: sys.exit(84)
    for identity in identities:
        record = next((item for item in values[kind] if item[0] == identity), None)
        if not record: sys.exit(84)
        identity, name = record
        owner = "wrong" if mode == "wrong-owner" and kind == "volume" else INSTALL_ID
        seen = [json.loads(line) for line in log.read_text().splitlines()]
        if mode == "late-owner" and kind == "container" and sum(item[0:2] == [kind, "inspect"] and identity in item[4:] for item in seen) > 1: owner = "wrong"
        prefix = "/" if kind == "container" else ""
        print("|".join([identity, prefix + name, PROJECT, owner, CURRENT_KEY]))
elif action == "rm":
    identity = args[-1]
    if identity not in [item[0] for item in values[kind]]: sys.exit(85)
    if mode == "remove-fail" and kind == "network": sys.exit(87)
    values[kind] = [item for item in values[kind] if item[0] != identity]
    if mode == "appeared" and not any(values.values()): values["container"] = [["f" * 64, PROJECT + "-other-1"]]
    fixture["resources"] = values
    state.write_text(json.dumps(fixture))
else: sys.exit(86)
"""
            )
            stub.chmod(0o755)
            _fast_fixture_identity(stub)
            source = ROOT / "ops/release/runtime/release-installation-docker.sh"
            script = root / "remove.sh"
            script.write_text(
                (ROOT / "ops/release/runtime/release-installation-state.sh").read_text()
                + '\nset -eu\n. "$1"\nlf_lock_check() { test "${LF_TEST_LOCK:-yes}" = yes; }\nlf_docker_remove\n'
            )
            env = {
                **os.environ,
                "PATH": str(bindir) + ":" + os.environ["PATH"],
                "DOCKER_HOST": "unix:///synthetic.sock",
                "DOCKER_CONTEXT": "",
                "LF_TEST_LOCK": "yes",
                "LF_PROJECT": project,
                "LF_ROOT": str(root),
                "LF_INSTALL_ID": "a" * 32,
                "LF_CURRENT_KEY": "b" * 64,
            }

            def run(mode):
                state.write_text(json.dumps({"mode": mode, "resources": resources}))
                log.unlink(missing_ok=True)
                return subprocess.run(
                    ["sh", str(script), str(source)],
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=30,
                )

            for mode in (
                "wrong-owner",
                "list-fail",
                "inspect-fail",
                "unknown",
                "bad-id",
                "late-owner",
            ):
                result = run(mode)
                self.assertNotEqual(result.returncode, 0, mode)
                commands = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertFalse(any(command[1] == "rm" for command in commands), mode)
                self.assertEqual(json.loads(state.read_text())["resources"], resources)
            result = run("ok")
            self.assertEqual(result.returncode, 0, result.stderr)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            first_delete = next(
                i for i, command in enumerate(commands) if command[1] == "rm"
            )
            inspected = {
                (command[0], identity)
                for command in commands[:first_delete]
                if command[1] == "inspect"
                for identity in command[4:]
            }
            self.assertEqual(
                inspected,
                {
                    (kind, item[0])
                    for kind, items in resources.items()
                    for item in items
                },
            )
            self.assertEqual(
                [command for command in commands if command[1] == "rm"],
                [
                    ["container", "rm", "--force", item[0]]
                    for item in sorted(resources["container"], key=lambda item: item[0])
                ]
                + [["network", "rm", resources["network"][0][0]]]
                + [["volume", "rm", item[0]] for item in sorted(resources["volume"])],
            )
            self.assertEqual(
                json.loads(state.read_text())["resources"],
                {"container": [], "network": [], "volume": []},
            )
            self.assertEqual(result.stdout + result.stderr, "")
            empty = subprocess.run(
                ["sh", str(script), str(source)],
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(empty.returncode, 0, empty.stderr)
            self.assertNotEqual(run("appeared").returncode, 0)
            self.assertEqual(
                json.loads(state.read_text())["resources"]["container"][0][1],
                project + "-other-1",
            )
            self.assertNotEqual(run("remove-fail").returncode, 0)
            remaining = json.loads(state.read_text())["resources"]
            self.assertEqual(remaining["container"], [])
            self.assertEqual(remaining["network"], resources["network"])
            self.assertEqual(remaining["volume"], resources["volume"])
            env["LF_TEST_LOCK"] = "no"
            self.assertNotEqual(run("ok").returncode, 0)
            self.assertFalse(log.exists())

    def test_docker_endpoint_is_local_pinned_and_cannot_inherit_context_override(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            bindir = root / "bin"
            bindir.mkdir()
            log = root / "commands.jsonl"
            state = root / "endpoint-fixture.json"
            stub = bindir / "docker"
            stub.write_text(
                "#!"
                + sys.executable
                + "\n"
                + f"LOG = {str(log)!r}\nSTATE = {str(state)!r}\n"
                + r"""
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
fixture = json.loads(Path(STATE).read_text())
leaked = {key: os.environ[key] for key in ["DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"] if key in os.environ}
with Path(LOG).open("a") as out: out.write(json.dumps({"args": args, "leaked": leaked}) + "\n")
if args[:2] == ["context", "inspect"]:
    if fixture.get("context_fail"): sys.exit(5)
    print(fixture["endpoint"])
elif args[:1] == ["--host"]:
    if args[2:4] == ["compose", "version"]: print("Compose synthetic")
    elif args[2:3] == ["info"]: print("linux" if "OSType" in args[-1] else "amd64")
    else: sys.exit(6)
else: sys.exit(7)
"""
            )
            stub.chmod(0o755)
            script = root / "test.sh"
            script.write_text(
                'set -eu\n. "$1"\nlf_docker_preflight || exit 19\n'
                "DOCKER_CONTEXT=malicious DOCKER_HOST=tcp://remote.invalid:2375\n"
                'lf_docker info --format "{{.OSType}}" >/dev/null\n'
            )
            base = {
                **os.environ,
                "PATH": str(bindir) + ":" + os.environ["PATH"],
                "DOCKER_CONTEXT": "",
                "DOCKER_HOST": "",
                "DOCKER_TLS": "1",
                "DOCKER_TLS_VERIFY": "1",
                "DOCKER_CERT_PATH": "/must-not-use",
                "LF_DOCKER_ENDPOINT": "unix:///forged.sock",
            }
            source = ROOT / "ops/release/runtime/release-installation-docker.sh"
            cases = [
                (
                    {"DOCKER_HOST": "unix:///explicit.sock"},
                    "unix:///ok.sock",
                    False,
                    "unix:///explicit.sock",
                ),
                (
                    {
                        "DOCKER_CONTEXT": "desktop-local",
                        "DOCKER_HOST": "tcp://ignored.invalid:2375",
                    },
                    "unix:///context.sock",
                    False,
                    "unix:///context.sock",
                ),
                ({}, "unix:///default.sock", False, "unix:///default.sock"),
                (
                    {"DOCKER_HOST": "tcp://remote.invalid:2375"},
                    "unix:///ok.sock",
                    False,
                    None,
                ),
                (
                    {"DOCKER_HOST": "ssh://remote.invalid"},
                    "unix:///ok.sock",
                    False,
                    None,
                ),
                ({"DOCKER_HOST": "unix://relative"}, "unix:///ok.sock", False, None),
                ({"DOCKER_HOST": "unix:///"}, "unix:///ok.sock", False, None),
                (
                    {"DOCKER_CONTEXT": "remote"},
                    "tcp://remote.invalid:2375",
                    False,
                    None,
                ),
                ({"DOCKER_CONTEXT": "--bad"}, "unix:///ok.sock", False, None),
                ({"DOCKER_CONTEXT": "safe"}, "unix:///ok.sock", True, None),
            ]
            for overrides, endpoint, context_fail, expected in cases:
                state.write_text(
                    json.dumps({"endpoint": endpoint, "context_fail": context_fail})
                )
                log.unlink(missing_ok=True)
                result = subprocess.run(
                    ["sh", str(script), str(source)],
                    env={**base, **overrides},
                    text=True,
                    capture_output=True,
                    timeout=3,
                )
                records = (
                    [json.loads(line) for line in log.read_text().splitlines()]
                    if log.exists()
                    else []
                )
                daemon = [record for record in records if record["args"][0] == "--host"]
                self.assertTrue(
                    all(not record["leaked"] for record in records), records
                )
                if expected:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(len(daemon), 4)
                    self.assertTrue(
                        all(
                            record["args"][:2] == ["--host", expected]
                            for record in daemon
                        )
                    )
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(daemon, [])

    def _run(self, mode, args=(), env=None, files=None):
        with tempfile.TemporaryDirectory() as td:
            bindir = pathlib.Path(td) / "bin"
            bindir.mkdir()
            log = pathlib.Path(td) / "log"
            stub = bindir / "java"
            stub.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" > "$LOG"\nprintf \'JVM_ENV=%s|%s|%s\\n\' "${JAVA_TOOL_OPTIONS:-}" "${JDK_JAVA_OPTIONS:-}" "${_JAVA_OPTIONS:-}" >> "$LOG"\nprintf \'DB_ENV=%s|%s\\n\' "${SPRING_DATASOURCE_URL:-}" "${SPRING_DATASOURCE_USERNAME:-}" >> "$LOG"\nif [ -z "${SPRING_DATASOURCE_PASSWORD:-}" ]; then printf \'DB_PASSWORD_EMPTY=true\\n\' >> "$LOG"; else printf \'DB_PASSWORD_EMPTY=false\\n\' >> "$LOG"; fi\nexit "${JAVA_EXIT:-0}"\n'
            )
            stub.chmod(0o755)
            timer = bindir / "timeout"
            timer.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" > ' + shlex.quote(str(log))
                + '\nprintf "JVM_ENV=%s|%s|%s\\n" "${JAVA_TOOL_OPTIONS:-}" "${JDK_JAVA_OPTIONS:-}" "${_JAVA_OPTIONS:-}" >> '
                + shlex.quote(str(log)) + '\nexit 7\n'
            )
            timer.chmod(0o755)
            secrets = pathlib.Path(td) / "secrets"
            secrets.mkdir()
            if files:
                for name, val in files.items():
                    (secrets / name).write_text(val)
            env0 = os.environ.copy()
            env0.update(
                {
                    "PATH": str(bindir) + ":" + env0["PATH"],
                    "LOG": str(log),
                    **(env or {}),
                }
            )
            # 仅在合成测试副本重定向固定路径，产品脚本不接受路径覆盖。
            script_copy = pathlib.Path(td) / "run-container-command.sh"
            script_copy.write_text(
                (DOCKER / "run-container-command.sh")
                .read_text()
                .replace("/run/secrets/app-password", str(secrets / "app-password"))
                .replace("/dataset/dataset.zip", str(pathlib.Path(td) / "dataset.zip"))
                .replace("/usr/bin/timeout", str(timer))
            )
            script_copy.chmod(0o755)
            (pathlib.Path(td) / "dataset.zip").write_text("synthetic")
            script = script_copy
            proc = subprocess.run(
                ["sh", str(script), mode, *args],
                env=env0,
                text=True,
                capture_output=True,
                cwd=DOCKER,
                timeout=3,
            )
            return proc, log.read_text() if log.exists() else ""

    def test_entrypoint_fixed_arguments_env_and_privacy(self):
        password = "a" * 64
        proc, logged = self._run(
            "api",
            files={"app-password": password},
            env={"JAVA_TOOL_OPTIONS": "-Dsecret=LEAK"},
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("-Xms64m -Xmx768m", logged)
        self.assertIn("-Dlexiflow.runtime.mode=formal", logged)
        self.assertIn("JVM_ENV=||", logged)
        self.assertNotIn("LEAK", logged)
        self.assertNotIn(password, logged)
        proc, logged = self._run(
            "initialize",
            env={"LEXIFLOW_DATASET_SHA256": "x" * 64},
            files={"app-password": password},
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn(password, proc.stderr + logged)
        proc, _ = self._run("api", files={"app-password": password + "x"})
        self.assertNotEqual(proc.returncode, 0)
        proc, _ = self._run("api", ("arbitrary",), files={"app-password": password})
        self.assertNotEqual(proc.returncode, 0)

    def test_caption_logs_are_default_without_jsonl_and_removed_alias_is_rejected(self):
        password = "a" * 64
        normal, normal_log = self._run("api", files={"app-password": password})
        rejected, rejected_log = self._run(
            "api-debug", files={"app-password": password}
        )
        self.assertEqual(normal.returncode, 0)
        self.assertEqual(rejected.returncode, 64)
        self.assertEqual(rejected_log, "")
        self.assertIn("-Dlexiflow.caption-debug.enabled=true", normal_log)
        self.assertIn("-Dlexiflow.segment-analysis.enabled=false", normal_log)
        self.assertNotIn(password, normal_log + rejected.stderr)
        enabled, enabled_log = self._run(
            "api",
            files={"app-password": password},
            env={"LEXIFLOW_SEGMENT_ANALYSIS_ENABLED": "true"},
        )
        self.assertEqual(enabled.returncode, 0)
        self.assertIn("-Dlexiflow.segment-analysis.enabled=true", enabled_log)
        invalid, invalid_log = self._run(
            "api",
            files={"app-password": password},
            env={"LEXIFLOW_SEGMENT_ANALYSIS_ENABLED": "unknown"},
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(invalid_log, "")

    def test_api_development_trust_requires_explicit_external_postgres_without_secret(
        self,
    ):
        target = {
            "LEXIFLOW_DATABASE_HOST": "host.containers.internal",
            "LEXIFLOW_DATABASE_PORT": "15432",
            "LEXIFLOW_DATABASE_USER": "postgres",
            "LEXIFLOW_DATABASE_AUTH_MODE": "development-trust",
        }
        proc, logged = self._run("api", env=target)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(
            "DB_ENV=jdbc:postgresql://host.containers.internal:15432/lexiflow?currentSchema=lexiflow_release|postgres",
            logged,
        )
        self.assertIn("DB_PASSWORD_EMPTY=true", logged)
        for broken_env, files in [
            ({**target, "LEXIFLOW_DATABASE_USER": "lexiflow"}, None),
            ({**target, "LEXIFLOW_DATABASE_HOST": "postgres"}, None),
            ({**target, "LEXIFLOW_DATABASE_AUTH_MODE": "unknown"}, None),
            ({**target, "SPRING_DATASOURCE_PASSWORD": "synthetic-secret"}, None),
            (target, {"app-password": "a" * 64}),
        ]:
            rejected, leaked = self._run("api", env=broken_env, files=files)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertNotIn("synthetic-secret", rejected.stderr + leaked)

    def test_initialize_exact_cli_and_health_no_forwarding(self):
        password = "b" * 64
        with tempfile.TemporaryDirectory():
            digest = "c" * 64
            proc, logged = self._run(
                "initialize",
                env={"LEXIFLOW_DATASET_SHA256": digest},
                files={"app-password": password},
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("-Xms256m -Xmx768m", logged)
            self.assertIn("--release-dataset initialize --package", logged)
            self.assertIn("--expected-sha256 " + digest, logged)
            self.assertIn("-Djava.io.tmpdir=/work", logged)
            self.assertIn(
                "--expected-database lexiflow --expected-schema lexiflow_release",
                logged,
            )
            self.assertNotIn(password, logged)
            proc, _ = self._run("wat", ("secret",))
            self.assertNotEqual(proc.returncode, 0)
            self.assertNotIn("secret", proc.stderr)

    def test_health_is_fixed_and_propagates_failure(self):
        proc, logged = self._run(
            "health", env={"JAVA_EXIT": "7", "JAVA_TOOL_OPTIONS": "-Dsurprise=1"}
        )
        self.assertEqual(proc.returncode, 7)
        self.assertIn("--kill-after=1s 2s /bin/bash --noprofile --norc -s", logged)
        self.assertNotIn("java", logged)
        self.assertNotIn("PropertiesLauncher", logged)
        self.assertIn("JVM_ENV=||", logged)
        proc, _ = self._run("health", ("http://evil",))
        self.assertNotEqual(proc.returncode, 0)

    def test_bootstrap_shell_stub_path_and_secret_validation(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td)
            secret = path / "app-password"
            secret.write_text("d" * 64 + "\n")
            bindir = path / "bin"
            bindir.mkdir()
            capture = path / "captured"
            stub = bindir / "psql"
            stub.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" > "$CAPTURE"\ncat >> "$CAPTURE"\n'
            )
            stub.chmod(0o755)
            script = path / "initialize-postgres-app-role.sh"
            script.write_text(
                (DOCKER / "initialize-postgres-app-role.sh")
                .read_text()
                .replace("/run/secrets/app-password", str(secret))
            )
            env = {
                **os.environ,
                "PATH": str(bindir) + ":" + os.environ["PATH"],
                "CAPTURE": str(capture),
                "POSTGRES_USER": "postgres",
                "POSTGRES_DB": "lexiflow",
            }
            result = subprocess.run(
                ["sh", str(script)], text=True, capture_output=True, env=env, timeout=3
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("CREATE SCHEMA lexiflow_release", capture.read_text())
            self.assertIn(
                "GRANT CONNECT, CREATE ON DATABASE lexiflow TO lexiflow;",
                capture.read_text(),
            )
            self.assertIn("NOSUPERUSER NOCREATEDB NOCREATEROLE", capture.read_text())
            self.assertNotIn("d" * 64, result.stdout + result.stderr)
            secret.write_text("d" * 64 + "extra")
            result = subprocess.run(
                ["sh", str(script)], text=True, capture_output=True, env=env, timeout=3
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("extra", result.stderr)


class ContainerHealthTransportTest(unittest.TestCase):
    SUMMARY = (
        b'{"softwareVersion":"1.2.3","apiContract":"caption-hints.v2",'
        b'"mode":"formal","ready":true,"reason":"OK","datasetVersion":42,'
        b'"publishedDataset":null}'
    )

    @classmethod
    def response(cls, body=None, status=b"200 OK", extra=b"", length=None):
        body = cls.SUMMARY if body is None else body
        size = len(body) if length is None else length
        return (
            b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\n"
            + b"Content-Length: " + str(size).encode() + b"\r\n" + extra
            + b"Connection: close\r\n\r\n" + body
        )

    def probe(self, response):
        with HealthProbeFixture(DOCKER / "run-container-command.sh", [(0, response)]) as fixture:
            return fixture.run()

    def assertRejected(self, response):
        result = self.probe(response)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_fixed_request_safe_summary_and_degraded_state(self):
        for body in (self.SUMMARY, self.SUMMARY.replace(b'"OK"', b'"PREWARM_DEGRADED"')):
            with self.subTest(body=body), HealthProbeFixture(
                DOCKER / "run-container-command.sh", [(0, self.response(body))]
            ) as fixture:
                result = fixture.run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.encode(), body + b"\n")
                self.assertEqual(fixture.requests, [
                    b"GET /internal/container-health HTTP/1.1\r\nHost: 127.0.0.1:8080\r\nConnection: close\r\n\r\n"
                ])

    def test_published_dataset_seven_field_summary_is_accepted(self):
        body = self.SUMMARY.replace(
            b'"publishedDataset":null',
            b'"publishedDataset":{"releaseId":"rel","preparationId":"prep",'
            b'"ruleId":"rule","sqlVersion":"sql","sha256":"' + b'a' * 64 + b'"}',
        )
        result = self.probe(self.response(body))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.encode(), body + b"\n")

    def test_non_success_redirect_unknown_framing_and_duplicate_headers_rejected(self):
        for response in (
            self.response(status=b"503 Unavailable"),
            self.response(status=b"302 Found", extra=b"Location: http://example.invalid/\r\n"),
            self.response(extra=b"Transfer-Encoding: chunked\r\n"),
            self.response(extra=b"Content-Length: 137\r\n"),
            self.response(extra=b"Content-Type: application/json\r\n"),
            self.response().replace(b"application/json", b"text/html"),
            self.response().replace(b"Content-Length:", b"Content-Length: +"),
            self.response().replace(b"Connection: close", b" malformed header"),
            self.response().replace(b"HTTP/1.1 200 OK", b"HTTP/1.1 2000 OK"),
        ):
            with self.subTest(response=response[:100]):
                self.assertRejected(response)

    def test_bounds_wire_body_and_rejects_truncated_extra_and_nul_content(self):
        for response in (
            b"x" * 8193,
            self.response(b"x" * 4097),
            self.response(extra=b"X-Large: " + b"x" * 4096 + b"\r\n"),
            self.response(length=len(self.SUMMARY) + 1),
            self.response(length=len(self.SUMMARY) - 1),
            self.response() + b"extra",
            self.response() + b"\0",
            self.response().replace(b"formal", b"for\0mal"),
        ):
            with self.subTest(size=len(response)):
                self.assertRejected(response)

    def test_only_fixed_validated_summary_shape_can_reach_stdout(self):
        for body in (
            self.SUMMARY.replace(b'"ready":true', b'"ready":false'),
            self.SUMMARY.replace(b'"formal"', b'"demo"'),
            self.SUMMARY.replace(b'"OK"', b'"DEPENDENCY_UNAVAILABLE"'),
            self.SUMMARY.replace(b'caption-hints.v2', b'other'),
            self.SUMMARY.replace(b'"datasetVersion":42', b'"datasetVersion":0'),
            self.SUMMARY.replace(b'"datasetVersion":42', b'"datasetVersion":-1'),
            self.SUMMARY.replace(b'"datasetVersion":42', b'"datasetVersion":9223372036854775808'),
            self.SUMMARY.replace(b'"datasetVersion":42', b'"datasetVersion":42.0'),
            self.SUMMARY.replace(b'"datasetVersion":42', b'"datasetVersion":"42"'),
            self.SUMMARY.replace(b'"ready":true', b'"ready":true,"ready":true'),
            self.SUMMARY[:-1] + b',"secret":"never-print"}',
            self.SUMMARY + b" {}",
        ):
            with self.subTest(body=body):
                self.assertRejected(self.response(body))

    def test_shared_deadline_covers_headers_body_and_missing_close(self):
        for chunks in (
            [(3, self.response())],
            [(0, self.response()[:40]), (3, self.response()[40:])],
            [(1.2, self.response()[:40]), (1.2, self.response()[40:])],
            [(0, self.response()), (3, b"")],
        ):
            with self.subTest(chunks=len(chunks)), HealthProbeFixture(
                DOCKER / "run-container-command.sh", chunks
            ) as fixture:
                started = time.monotonic()
                result = fixture.run()
                self.assertEqual(result.returncode, 124)
                self.assertLess(time.monotonic() - started, 2.8)
                self.assertEqual(result.stdout, "")

    def test_unreachable_loopback_fails_without_summary(self):
        with HealthProbeFixture(
            DOCKER / "run-container-command.sh", [(0, self.response())]
        ) as fixture:
            fixture.server.shutdown()
            fixture.server.server_close()
            result = fixture.run()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")

    def test_environment_injection_and_arguments_cannot_change_health_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            sentinel = pathlib.Path(directory) / "executed"
            poison = pathlib.Path(directory) / "poison"
            poison.write_text("touch " + shlex.quote(str(sentinel)))
            env = {
                "BASH_ENV": str(poison), "ENV": str(poison),
                "JAVA_TOOL_OPTIONS": "-Dsecret=never-print",
                "HTTP_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1",
            }
            with HealthProbeFixture(
                DOCKER / "run-container-command.sh", [(0, self.response())], env
            ) as fixture:
                result = fixture.run()
                self.assertEqual(result.returncode, 0)
                self.assertFalse(sentinel.exists())
                rejected = fixture.run(("http://example.invalid/",))
                self.assertEqual(rejected.returncode, 64)
                self.assertEqual(len(fixture.requests), 1)
                self.assertNotIn("never-print", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
