import json
import os
import sys
import pathlib
import shlex
import subprocess
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
DOCKER = ROOT / "ops/docker"


class DockerContractTest(unittest.TestCase):
    def test_image_import_checks_actual_image_os_and_architecture(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            script = root / "import.sh"
            log = root / "actions"
            script.write_text('. "$1"\n'
                              'lf_docker_preflight() { LF_HOST_PLATFORM=$TEST_HOST; }\n'
                              'lf_docker() { printf "%s\\n" "$*" >> "$TEST_LOG"; '
                              'if [ "$1" = load ]; then [ "$TEST_MODE" != load-fail ]; return $?; fi\n'
                              'for value do last=$value; done\n'
                              '[ "$TEST_MODE" != inspect-fail ] || return 1\n'
                              'if [ "$last" = "$LF_API_IMAGE" ]; then printf "%s\\n" "$TEST_API"; '
                              'else printf "%s\\n" "$TEST_DATABASE"; fi; }\n'
                              'lf_docker_import\n')
            api, database = "sha256:" + "a" * 64, "sha256:" + "b" * 64
            base = {**os.environ, "TEST_LOG": str(log), "TEST_MODE": "ok", "TEST_HOST": "linux/amd64",
                    "LF_PLATFORM": "linux/amd64", "LF_RELEASE_ROOT": str(root), "LF_API_ARCHIVE": "api.tar",
                    "LF_POSTGRES_ARCHIVE": "postgres.tar", "LF_API_IMAGE": api, "LF_POSTGRES_IMAGE": database,
                    "TEST_API": api + "|linux|amd64", "TEST_DATABASE": database + "|linux|amd64"}
            cases = [({}, True), ({"TEST_HOST": "linux/arm64"}, False),
                     ({"TEST_API": api + "|linux|arm64"}, False),
                     ({"TEST_DATABASE": database + "|windows|amd64"}, False),
                     ({"TEST_API": database + "|linux|amd64"}, False),
                     ({"TEST_MODE": "load-fail"}, False), ({"TEST_MODE": "inspect-fail"}, False),
                     ({"TEST_HOST": "linux/arm64", "LF_PLATFORM": "linux/arm64",
                       "TEST_API": api + "|linux|arm64", "TEST_DATABASE": database + "|linux|arm64"}, True)]
            for overrides, passed in cases:
                log.unlink(missing_ok=True)
                result = subprocess.run(["/bin/sh", str(script), str(ROOT / "ops/release/lifecycle-docker.sh")],
                                        env={**base, **overrides}, capture_output=True, text=True, timeout=3)
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
            script.write_text('. "$1"\nLF_PROJECT=synthetic\n'
                              'step() { printf "%s\\n" "$*" >> "$TEST_LOG"; [ "$*" != "$TEST_FAIL" ]; }\n'
                              'lf_operation_preflight() { :; }\n'
                              'lf_operation_temp_cleanup() { :; }\n'
                              'lf_operation_compose() { lf_compose "$@"; }\n'
                              'lf_docker_import() { step import; }\n'
                              'lf_compose() { step compose "$@"; }\n'
                              'lf_wait_healthy() { step healthy "$@"; }\n'
                              'lf_docker_start\n')
            steps = ["import", "compose up -d --no-deps postgres", "healthy synthetic-postgres-1",
                     "compose up -d --no-deps api", "healthy synthetic-api-1"]
            for failure in ("none", *steps):
                log.unlink(missing_ok=True)
                result = subprocess.run(["/bin/sh", str(script), str(ROOT / "ops/release/lifecycle-docker.sh")],
                                        env={**os.environ, "TEST_LOG": str(log), "TEST_FAIL": failure},
                                        capture_output=True, text=True, timeout=3)
                self.assertEqual(result.returncode == 0, failure == "none")
                expected = steps if failure == "none" else steps[:steps.index(failure) + 1]
                self.assertEqual(log.read_text().splitlines(), expected)
                self.assertEqual(result.stdout + result.stderr, "")

    def test_installation_signal_retains_lock_and_normal_exit_releases_only_owned_lock(self):
        source = ROOT / "ops/release/lifecycle-state.sh"
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            script = root / "signals.sh"
            script.write_text('. "$1"\nLF_ROOT=$2\n'
                              'if [ "$3" = init ]; then lf_owner_init || exit 20; '
                              'else lf_owner_init || exit 20; lf_lock_release; lf_lock_acquire || exit 21; fi\n'
                              'if [ "$5" = replaced ]; then printf "%s\\n" replacement > "$LF_ROOT/.lock/token"; fi\n'
                              'cat "$LF_ROOT/.lock/token" > "$LF_ROOT/token.before"\n'
                              'if [ "$4" != normal ]; then kill -s "$4" "$$"; '
                              'printf "%s\\n" forbidden > "$LF_ROOT/continued"; fi\n')
            for mode in ("init", "acquire"):
                for signal, code in (("normal", 0), ("HUP", 129), ("INT", 130), ("TERM", 143)):
                    for ownership in ("owned", "replaced"):
                        installation = root / (mode + signal + ownership)
                        result = subprocess.run(["/bin/sh", str(script), str(source), str(installation),
                                                 mode, signal, ownership], capture_output=True, text=True, timeout=3)
                        self.assertEqual(result.returncode, code, result.stderr)
                        self.assertFalse((installation / "continued").exists())
                        if ownership == "owned":
                            if signal == "normal":
                                self.assertFalse((installation / ".lock").exists())
                            else:
                                self.assertTrue((installation / ".lock/token").is_file())
                                self.assertEqual((installation / ".lock/token").read_bytes(),
                                                 (installation / "token.before").read_bytes())
                        else:
                            self.assertEqual((installation / ".lock/token").read_text(), "replacement\n")

    def test_installation_hash_rejects_tool_failure_and_invalid_digest(self):
        source = ROOT / "ops/release/lifecycle-state.sh"
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            script = root / "digest.sh"
            script.write_text('. "$1"\nlf_sha "$2"\n')
            actual = root / "file with spaces"
            actual.write_bytes(b"abc")
            result = subprocess.run(["/bin/sh", str(script), str(source), str(actual)],
                                    capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad\n")
            actual.unlink()
            result = subprocess.run(["/bin/sh", str(script), str(source), str(actual)],
                                    capture_output=True, text=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout + result.stderr, "")
            for tool in ("sha256sum", "shasum"):
                bindir = root / tool
                bindir.mkdir()
                stub = bindir / tool
                stub.write_text('#!/bin/sh\nprintf "%s  synthetic\\n" "$TEST_DIGEST"\n'
                                'printf "%s\\n" private-error >&2\nexit "$TEST_EXIT"\n')
                stub.chmod(0o755)
                for digest, status, passed in (("a" * 64, "0", True), ("a" * 64, "7", False),
                                               ("a" * 63, "0", False), ("g" * 64, "0", False),
                                               ("", "0", False)):
                    env = {"PATH": str(bindir), "TEST_DIGEST": digest, "TEST_EXIT": status}
                    result = subprocess.run(["/bin/sh", str(script), str(source), str(root / "synthetic")],
                                            env=env, capture_output=True, text=True, timeout=3)
                    self.assertEqual(result.returncode == 0, passed)
                    self.assertEqual(result.stdout, digest + "\n" if passed else "")
                    self.assertEqual(result.stderr, "")

    def test_context_and_image_are_minimal_nonroot(self):
        ignore = (DOCKER / ".dockerignore").read_text()
        self.assertEqual(ignore.splitlines(), ["*", "!Dockerfile", "!entrypoint.sh", "!lexiflow-api.jar", "!Dockerfile.postgres", "!bootstrap.sh"])
        dockerfile = (DOCKER / "Dockerfile").read_text()
        self.assertIn("ARG JAVA_RUNTIME_IMAGE", dockerfile)
        self.assertIn("COPY --chown=10001:10001 lexiflow-api.jar", dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("--chmod=0555 entrypoint.sh", dockerfile)
        postgres = (DOCKER / "Dockerfile.postgres").read_text()
        self.assertIn("ARG POSTGRES_RUNTIME_IMAGE", postgres)
        self.assertIn("COPY --chmod=0555 bootstrap.sh /docker-entrypoint-initdb.d/10-lexiflow-bootstrap.sh", postgres)
        self.assertNotIn("RUN ", postgres)
        self.assertEqual(dockerfile.count("RUN "), 1)
        self.assertIn("RUN mkdir /work && chown 10001:10001 /work", dockerfile)

    def test_compose_is_private_bounded_and_health_ordered(self):
        cfg = yaml.safe_load((DOCKER / "compose.yaml").read_text())
        services = cfg["services"]
        self.assertEqual(set(services), {"postgres", "initialize", "api"})
        self.assertEqual(services["api"]["ports"], ["127.0.0.1:18080:8080"])
        self.assertNotIn("ports", services["postgres"])
        self.assertEqual(services["postgres"]["volumes"], ["pgdata:/var/lib/postgresql/data"])
        self.assertEqual(services["api"]["depends_on"]["initialize"]["condition"], "service_completed_successfully")
        self.assertEqual(services["initialize"]["depends_on"]["postgres"]["condition"], "service_healthy")
        self.assertEqual(services["api"]["mem_limit"], "1280m")
        self.assertEqual(services["postgres"]["mem_limit"], "768m")
        self.assertTrue(services["api"]["read_only"])
        self.assertEqual(set(services["api"]["cap_drop"]), {"ALL"})
        self.assertTrue(cfg["networks"]["private"]["internal"])
        self.assertIn("initialization-work:/work", services["initialize"]["volumes"])
        self.assertNotIn("initialization-work:/work", services["api"].get("volumes", []))
        self.assertIn("pg_isready -h 127.0.0.1", " ".join(services["postgres"]["healthcheck"]["test"]))
        self.assertNotIn("external", cfg["volumes"].get("pgdata", {}))
        for service in services.values():
            self.assertEqual(service["pull_policy"], "never")
            self.assertNotIn("build", service)

    def test_compose_secret_sources_and_reserved_labels(self):
        cfg = yaml.safe_load((DOCKER / "compose.yaml").read_text())
        self.assertEqual(cfg["secrets"], {
            "postgres-password": {"environment": "LEXIFLOW_COMPOSE_POSTGRES_PASSWORD"},
            "app-password": {"environment": "LEXIFLOW_COMPOSE_APP_PASSWORD"},
        })
        for section in ("services", "volumes", "networks"):
            for resource in cfg[section].values():
                self.assertFalse(any(key.startswith("com.docker.compose.") for key in resource.get("labels", {})))
                self.assertEqual(set(resource["labels"]), {"lexiflow.installation", "lexiflow.release"})
        for service in cfg["services"].values():
            self.assertFalse(any("COMPOSE_" in key for key in service.get("environment", {})))
            self.assertIn("app-password", service["secrets"])

    def test_compose_child_secret_isolation_and_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            installation = root / "installation with spaces"
            release_key = "c" * 64
            secret_dir = installation / "releases" / release_key
            secret_dir.mkdir(parents=True)
            binding = installation / "engine"
            binding.write_text("schema=lexiflow-engine-v1\nendpoint=unix:///synthetic.sock\ndaemon_id=synthetic-daemon-id\n")
            binding.chmod(0o600)
            postgres = secret_dir / "postgres-password"
            application = secret_dir / "app-password"
            postgres.write_text("a" * 64)
            application.write_text("b" * 64)
            postgres.chmod(0o600)
            application.chmod(0o600)
            bindir = root / "bin"
            bindir.mkdir()
            log = root / "argv"
            stub = bindir / "docker"
            stub.write_text("#!/bin/sh\n"
                            f'LOG={shlex.quote(str(log))}\n'
                            'test "$1" = --host && test "$2" = unix:///synthetic.sock || exit 10\nshift 2\n'
                            'if [ "$1" = info ] && [ "$2" = --format ] && [ "$3" = "{{.ID}}" ]; then printf "synthetic-daemon-id\\n"; exit 0; fi\n'
                            'test "$LEXIFLOW_COMPOSE_POSTGRES_PASSWORD" = "' + "a" * 64 + '" || exit 11\n'
                            'test "$LEXIFLOW_COMPOSE_APP_PASSWORD" = "' + "b" * 64 + '" || exit 12\n'
                            'test "$LEXIFLOW_INSTALLATION_ID" = "' + "d" * 32 + '" || exit 13\n'
                            'test "$LEXIFLOW_RELEASE_KEY" = "' + release_key + '" || exit 14\n'
                            'test -z "${COMPOSE_FILE:-}${COMPOSE_PROJECT_NAME:-}${COMPOSE_ENV_FILES:-}${LEXIFLOW_APP_PASSWORD_FILE:-}" || exit 15\n'
                            'printf "%s\\n" "$@" > "$LOG"\n')
            stub.chmod(0o755)
            source = ROOT / "ops/release/lifecycle-docker.sh"
            script = root / "test.sh"
            script.write_text((ROOT / "ops/release/lifecycle-state.sh").read_text() + '\nset -eu\n. "$1"\nlf_lock_check() { test "${LF_TEST_LOCK:-yes}" = yes; }\n'
                              'lf_compose up -d api || exit 23\n'
                              'test "$LEXIFLOW_COMPOSE_POSTGRES_PASSWORD" = poisoned\n'
                              'test "$LEXIFLOW_COMPOSE_APP_PASSWORD" = poisoned\n')
            env = {**os.environ, "PATH": str(bindir) + ":" + os.environ["PATH"],
                   "DOCKER_HOST": "unix:///synthetic.sock", "DOCKER_CONTEXT": "",
                   "LF_ROOT": str(installation), "LF_CURRENT_KEY": release_key,
                   "LF_INSTALL_ID": "d" * 32, "LF_PLATFORM": "linux/amd64", "LF_API_IMAGE": "sha256:" + "e" * 64,
                   "LF_POSTGRES_IMAGE": "sha256:" + "f" * 64, "LF_DATASET_SHA256": "1" * 64,
                   "LF_RELEASE_ROOT": str(root / "package with spaces"), "LF_DATASET_PATH": "dataset.zip",
                   "LF_COMPOSE_PATH": "compose.yaml", "LF_PROJECT": "lf_synthetic",
                   "COMPOSE_FILE": "malicious", "COMPOSE_PROJECT_NAME": "malicious", "COMPOSE_ENV_FILES": "malicious",
                   "LEXIFLOW_APP_PASSWORD_FILE": "/not-read", "LEXIFLOW_INSTALLATION_ID": "poisoned",
                   "LEXIFLOW_RELEASE_KEY": "poisoned", "LEXIFLOW_COMPOSE_POSTGRES_PASSWORD": "poisoned",
                   "LEXIFLOW_COMPOSE_APP_PASSWORD": "poisoned"}
            def run():
                return subprocess.run(["sh", str(script), str(source)], env=env, text=True,
                                      capture_output=True, timeout=3)
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text().splitlines(), ["compose", "--project-name", "lf_synthetic",
                "--project-directory", str(root / "package with spaces"), "--env-file", "/dev/null",
                "-f", str(root / "package with spaces/compose.yaml"), "up", "-d", "api"])
            self.assertNotIn("a" * 64, log.read_text() + result.stdout + result.stderr)
            self.assertNotIn("b" * 64, log.read_text() + result.stdout + result.stderr)
            self.assertEqual(application.stat().st_mode & 0o777, 0o600)
            for invalid in ["b" * 63, "b" * 64 + "\n", "b" * 64 + "\n\n", "g" * 64, "x" * 100000]:
                log.unlink(missing_ok=True)
                application.write_text(invalid)
                self.assertNotEqual(run().returncode, 0)
                self.assertFalse(log.exists())
            application.unlink()
            application.symlink_to(postgres)
            self.assertNotEqual(run().returncode, 0)
            self.assertFalse(log.exists())
            application.unlink()
            application.write_text("b" * 64)
            env["LF_TEST_LOCK"] = "no"
            self.assertNotEqual(run().returncode, 0)
            self.assertFalse(log.exists())

    def test_removal_preflights_every_owner_and_deletes_exact_resources(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            project = "lf_" + "a" * 32 + "_" + "b" * 16
            resources = {
                "container": [["c" * 64, project + "-api-1"], ["d" * 64, project + "-postgres-1"]],
                "network": [["e" * 64, project + "_private"]],
                "volume": [[project + "_pgdata", project + "_pgdata"],
                           [project + "_initialization-work", project + "_initialization-work"]],
            }
            binding = root / "engine"
            binding.write_text("schema=lexiflow-engine-v1\nendpoint=unix:///synthetic.sock\ndaemon_id=synthetic-daemon-id\n")
            binding.chmod(0o600)
            state = root / "resources.json"
            log = root / "commands.jsonl"
            bindir = root / "bin"
            bindir.mkdir()
            stub = bindir / "docker"
            stub.write_text("#!" + sys.executable + "\n" +
                f"STATE = {str(state)!r}\nLOG = {str(log)!r}\nPROJECT = {project!r}\n"
                f"INSTALL_ID = {'a' * 32!r}\nCURRENT_KEY = {'b' * 64!r}\n" + r'''
import json, os, sys
from pathlib import Path
state = Path(STATE)
log = Path(LOG)
fixture = json.loads(state.read_text())
mode = fixture["mode"]
values = fixture["resources"]
if sys.argv[1:3] != ["--host", "unix:///synthetic.sock"]: sys.exit(80)
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
    record = next((item for item in values[kind] if item[0] == args[-1]), None)
    if not record: sys.exit(84)
    identity, name = record
    owner = "wrong" if mode == "wrong-owner" and kind == "volume" else INSTALL_ID
    seen = [json.loads(line) for line in log.read_text().splitlines()]
    if mode == "late-owner" and kind == "container" and sum(item[0:2] == [kind, "inspect"] and item[-1] == identity for item in seen) > 1: owner = "wrong"
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
''')
            stub.chmod(0o755)
            source = ROOT / "ops/release/lifecycle-docker.sh"
            script = root / "remove.sh"
            script.write_text((ROOT / "ops/release/lifecycle-state.sh").read_text() + '\nset -eu\n. "$1"\nlf_lock_check() { test "${LF_TEST_LOCK:-yes}" = yes; }\nlf_docker_remove\n')
            env = {**os.environ, "PATH": str(bindir) + ":" + os.environ["PATH"],
                   "DOCKER_HOST": "unix:///synthetic.sock", "DOCKER_CONTEXT": "",
                   "LF_TEST_LOCK": "yes", "LF_PROJECT": project, "LF_ROOT": str(root),
                   "LF_INSTALL_ID": "a" * 32, "LF_CURRENT_KEY": "b" * 64}
            def run(mode):
                state.write_text(json.dumps({"mode": mode, "resources": resources}))
                log.unlink(missing_ok=True)
                return subprocess.run(["sh", str(script), str(source)], env=env,
                                      text=True, capture_output=True, timeout=10)
            for mode in ("wrong-owner", "list-fail", "inspect-fail", "unknown", "bad-id", "late-owner"):
                result = run(mode)
                self.assertNotEqual(result.returncode, 0, mode)
                commands = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertFalse(any(command[1] == "rm" for command in commands), mode)
                self.assertEqual(json.loads(state.read_text())["resources"], resources)
            result = run("ok")
            self.assertEqual(result.returncode, 0, result.stderr)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            first_delete = next(i for i, command in enumerate(commands) if command[1] == "rm")
            inspected = {(command[0], command[-1]) for command in commands[:first_delete] if command[1] == "inspect"}
            self.assertEqual(inspected, {(kind, item[0]) for kind, items in resources.items() for item in items})
            self.assertEqual([command for command in commands if command[1] == "rm"],
                [["container", "rm", "--force", item[0]] for item in resources["container"]]
                + [["network", "rm", resources["network"][0][0]]]
                + [["volume", "rm", item[0]] for item in reversed(resources["volume"])])
            self.assertEqual(json.loads(state.read_text())["resources"], {"container": [], "network": [], "volume": []})
            self.assertEqual(result.stdout + result.stderr, "")
            empty = subprocess.run(["sh", str(script), str(source)], env=env, text=True,
                                   capture_output=True, timeout=10)
            self.assertEqual(empty.returncode, 0, empty.stderr)
            self.assertNotEqual(run("appeared").returncode, 0)
            self.assertEqual(json.loads(state.read_text())["resources"]["container"][0][1], project + "-other-1")
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
            stub.write_text("#!" + sys.executable + "\n" +
                f"LOG = {str(log)!r}\nSTATE = {str(state)!r}\n" + r'''
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
''')
            stub.chmod(0o755)
            script = root / "test.sh"
            script.write_text('set -eu\n. "$1"\nlf_docker_preflight || exit 19\n'
                              'DOCKER_CONTEXT=malicious DOCKER_HOST=tcp://remote.invalid:2375\n'
                              'lf_docker info --format "{{.OSType}}" >/dev/null\n')
            base = {**os.environ, "PATH": str(bindir) + ":" + os.environ["PATH"],
                    "DOCKER_CONTEXT": "", "DOCKER_HOST": "", "DOCKER_TLS": "1", "DOCKER_TLS_VERIFY": "1",
                    "DOCKER_CERT_PATH": "/must-not-use", "LF_DOCKER_ENDPOINT": "unix:///forged.sock"}
            source = ROOT / "ops/release/lifecycle-docker.sh"
            cases = [
                ({"DOCKER_HOST": "unix:///explicit.sock"}, "unix:///ok.sock", False, "unix:///explicit.sock"),
                ({"DOCKER_CONTEXT": "desktop-local", "DOCKER_HOST": "tcp://ignored.invalid:2375"}, "unix:///context.sock", False, "unix:///context.sock"),
                ({}, "unix:///default.sock", False, "unix:///default.sock"),
                ({"DOCKER_HOST": "tcp://remote.invalid:2375"}, "unix:///ok.sock", False, None),
                ({"DOCKER_HOST": "ssh://remote.invalid"}, "unix:///ok.sock", False, None),
                ({"DOCKER_HOST": "unix://relative"}, "unix:///ok.sock", False, None),
                ({"DOCKER_HOST": "unix:///"}, "unix:///ok.sock", False, None),
                ({"DOCKER_CONTEXT": "remote"}, "tcp://remote.invalid:2375", False, None),
                ({"DOCKER_CONTEXT": "--bad"}, "unix:///ok.sock", False, None),
                ({"DOCKER_CONTEXT": "safe"}, "unix:///ok.sock", True, None),
            ]
            for overrides, endpoint, context_fail, expected in cases:
                state.write_text(json.dumps({"endpoint": endpoint,
                                             "context_fail": context_fail}))
                log.unlink(missing_ok=True)
                result = subprocess.run(["sh", str(script), str(source)], env={**base, **overrides},
                                        text=True, capture_output=True, timeout=3)
                records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
                daemon = [record for record in records if record["args"][0] == "--host"]
                self.assertTrue(all(not record["leaked"] for record in records), records)
                if expected:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(len(daemon), 4)
                    self.assertTrue(all(record["args"][:2] == ["--host", expected] for record in daemon))
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(daemon, [])

    def _run(self, mode, args=(), env=None, files=None):
        with tempfile.TemporaryDirectory() as td:
            bindir = pathlib.Path(td) / "bin"
            bindir.mkdir()
            log = pathlib.Path(td) / "log"
            stub = bindir / "java"
            stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$LOG\"\nprintf 'JVM_ENV=%s|%s|%s\\n' \"${JAVA_TOOL_OPTIONS:-}\" \"${JDK_JAVA_OPTIONS:-}\" \"${_JAVA_OPTIONS:-}\" >> \"$LOG\"\nexit \"${JAVA_EXIT:-0}\"\n")
            stub.chmod(0o755)
            secrets = pathlib.Path(td) / "secrets"
            secrets.mkdir()
            if files:
                for name, val in files.items():
                    (secrets / name).write_text(val)
            env0 = os.environ.copy()
            env0.update({"PATH": str(bindir) + ":" + env0["PATH"], "LOG": str(log), **(env or {})})
            # 仅在合成测试副本重定向固定路径，产品脚本不接受路径覆盖。
            script_copy = pathlib.Path(td) / "entrypoint.sh"
            script_copy.write_text((DOCKER / "entrypoint.sh").read_text()
                                   .replace("/run/secrets/app-password", str(secrets / "app-password"))
                                   .replace("/dataset/dataset.zip", str(pathlib.Path(td) / "dataset.zip")))
            script_copy.chmod(0o755)
            (pathlib.Path(td) / "dataset.zip").write_text("synthetic")
            script = script_copy
            proc = subprocess.run(["sh", str(script), mode, *args], env=env0, text=True, capture_output=True,
                                  cwd=DOCKER, timeout=3)
            return proc, log.read_text() if log.exists() else ""

    def test_entrypoint_fixed_arguments_env_and_privacy(self):
        password = "a" * 64
        proc, logged = self._run("api", files={"app-password": password}, env={"JAVA_TOOL_OPTIONS": "-Dsecret=LEAK"})
        self.assertEqual(proc.returncode, 0)
        self.assertIn("-Xms256m -Xmx768m", logged)
        self.assertIn("-Dlexiflow.runtime.mode=formal", logged)
        self.assertIn("JVM_ENV=||", logged)
        self.assertNotIn("LEAK", logged)
        self.assertNotIn(password, logged)
        proc, logged = self._run("initialize", env={"LEXIFLOW_DATASET_SHA256": "x" * 64}, files={"app-password": password})
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn(password, proc.stderr + logged)
        proc, _ = self._run("api", files={"app-password": password + "x"})
        self.assertNotEqual(proc.returncode, 0)
        proc, _ = self._run("api", ("arbitrary",), files={"app-password": password})
        self.assertNotEqual(proc.returncode, 0)

    def test_caption_debug_requires_explicit_command(self):
        password = "a" * 64
        normal, normal_log = self._run("api", files={"app-password": password})
        debug, debug_log = self._run("api-debug", files={"app-password": password})
        self.assertEqual(normal.returncode, 0)
        self.assertEqual(debug.returncode, 0)
        self.assertIn("-Dlexiflow.segment-analysis.console=false", normal_log)
        self.assertIn("-Dlexiflow.segment-analysis.console=true", debug_log)
        self.assertIn("-Dlexiflow.segment-analysis.enabled=true", debug_log)
        self.assertNotIn(password, debug_log)

    def test_initialize_exact_cli_and_health_no_forwarding(self):
        password = "b" * 64
        with tempfile.TemporaryDirectory() as td:
            digest = "c" * 64
            proc, logged = self._run("initialize", env={"LEXIFLOW_DATASET_SHA256": digest},
                                     files={"app-password": password})
            self.assertEqual(proc.returncode, 0)
            self.assertIn("--release-dataset initialize --package", logged)
            self.assertIn("--expected-sha256 " + digest, logged)
            self.assertIn("-Djava.io.tmpdir=/work", logged)
            self.assertIn("--expected-database lexiflow --expected-schema lexiflow_release", logged)
            self.assertNotIn(password, logged)
            proc, _ = self._run("wat", ("secret",))
            self.assertNotEqual(proc.returncode, 0)
            self.assertNotIn("secret", proc.stderr)

    def test_health_is_fixed_and_propagates_failure(self):
        proc, logged = self._run("health", env={"JAVA_EXIT": "7", "JAVA_TOOL_OPTIONS": "-Dsurprise=1"})
        self.assertEqual(proc.returncode, 7)
        self.assertIn("-Dloader.main=io.lexiflow.api.release.RuntimeHealthCommand", logged)
        self.assertIn("org.springframework.boot.loader.launch.PropertiesLauncher", logged)
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
            stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$CAPTURE\"\ncat >> \"$CAPTURE\"\n")
            stub.chmod(0o755)
            script = path / "bootstrap.sh"
            script.write_text((DOCKER / "bootstrap.sh").read_text().replace("/run/secrets/app-password", str(secret)))
            env = {**os.environ, "PATH": str(bindir) + ":" + os.environ["PATH"], "CAPTURE": str(capture),
                   "POSTGRES_USER": "postgres", "POSTGRES_DB": "lexiflow"}
            result = subprocess.run(["sh", str(script)], text=True, capture_output=True, env=env, timeout=3)
            self.assertEqual(result.returncode, 0)
            self.assertIn("CREATE SCHEMA lexiflow_release", capture.read_text())
            self.assertNotIn("d" * 64, result.stdout + result.stderr)
            secret.write_text("d" * 64 + "extra")
            result = subprocess.run(["sh", str(script)], text=True, capture_output=True, env=env, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("extra", result.stderr)


if __name__ == "__main__":
    unittest.main()
