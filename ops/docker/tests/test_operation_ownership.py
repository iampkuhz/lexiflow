"""生命周期资源归属操作的真实 Shell/有状态 Docker CLI 替身测试。"""
import json
import os
import pathlib
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "release/lifecycle-docker.sh"
PROJECT = "lf_" + "a" * 32 + "_" + "b" * 16
INSTALL = "a" * 32
RELEASE = "b" * 64


class OperationOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state = self.root / "state.json"
        self.log = self.root / "commands.jsonl"
        self.installation = self.root / "installation"
        secret_dir = self.installation / "releases" / RELEASE
        secret_dir.mkdir(parents=True)
        for filename, value in (("postgres-password", "a" * 64), ("app-password", "b" * 64)):
            secret = secret_dir / filename
            secret.write_text(value)
            secret.chmod(0o600)
        binding = self.installation / "engine"
        binding.write_text("schema=lexiflow-engine-v1\nendpoint=unix:///fake.sock\ndaemon_id=synthetic-daemon-id\n")
        binding.chmod(0o600)
        self.release_root = self.root / "release"
        self.release_root.mkdir()
        self.env = {**os.environ, "PATH": str(self.bin) + ":" + os.environ["PATH"],
                    "LF_TEST_STATE": str(self.state), "LF_TEST_LOG": str(self.log),
                    "LF_PROJECT": PROJECT, "LF_INSTALL_ID": INSTALL,
                    "LF_CURRENT_KEY": RELEASE, "DOCKER_HOST": "unix:///fake.sock",
                    "DOCKER_CONTEXT": ""}
        self.write_state()
        self.mailbox = self.root / "mailbox"
        self.mailbox.mkdir(mode=0o700)
        self.server_log = self.root / "server.log"
        self.server_pid = None
        stub = self.bin / "docker"
        cli = (self.cli().replace("__STATE__", repr(str(self.state))).replace("__LOG__", repr(str(self.log)))
               .replace("__PROJECT__", repr(PROJECT)).replace("__INSTALL__", repr(INSTALL))
               .replace("__RELEASE__", repr(RELEASE)).replace("__LOCK__", repr(str(self.root / "lock-owned"))))
        self.model_source = self.root / "docker-model.py"
        self.model_source.write_text(cli)
        (self.mailbox / "server-config.json").write_text(json.dumps({"model": str(self.model_source)}))
        # CLI 业务模型仍只由 cli() 提供；server 启动时编译一次，逐请求执行。
        with self.server_log.open("wb") as server_log:
            self.server_process = subprocess.Popen(
                [sys.executable, "-u", str(pathlib.Path(__file__).resolve()), "--fake-server", str(self.root)],
                start_new_session=True, close_fds=True, stdin=subprocess.DEVNULL,
                stdout=server_log, stderr=subprocess.STDOUT)
        self.addCleanup(self._cleanup_server)
        self.server_pid = self.server_process.pid
        self._wait_for_server_ready()
        stub.write_text(self._shell_client())
        stub.chmod(0o755)
        self.script = self.root / "run.sh"
        self.script.write_text(SOURCE.with_name("lifecycle-state.sh").read_text() + '\nset -eu\n. "$1"\nlf_lock_check() { test -f "$LF_TEST_LOCK_FILE"; }\n'
                              'lf_docker_endpoint() { LF_DOCKER_ENDPOINT=unix:///fake.sock; }\n'
                              +
                              'LF_ROOT=/tmp/fake LF_PLATFORM=linux/amd64 LF_API_IMAGE=sha256:' + 'e'*64 +
                              ' LF_POSTGRES_IMAGE=sha256:' + 'f'*64 + ' LF_DATASET_SHA256=' + '1'*64 + '\n'
                              'LF_ROOT="$LF_TEST_ROOT" LF_RELEASE_ROOT="$LF_TEST_RELEASE" LF_COMPOSE_PATH=compose.yaml '
                              'LF_DATASET_PATH=dataset.zip\n'
                              'LF_API_ARCHIVE=api.tar LF_POSTGRES_ARCHIVE=postgres.tar\n'
                              'case "$2" in stop) lf_docker_stop;; cleanup) lf_operation_temp_cleanup;; '
                              'health) lf_wait_healthy "$3";; prep) lf_docker_prepare;; start) lf_docker_start;; '
                              'rmtemp) lf_operation_remove_temp "$3";; esac\n')
        self.env.update({"LF_TEST_ROOT": str(self.installation), "LF_TEST_RELEASE": str(self.release_root),
                         "LF_TEST_LOCK_FILE": str(self.root / "lock-owned")})
        (self.root / "lock-owned").touch()

    def _wait_for_server_ready(self):
        deadline = time.monotonic() + 2
        ready = self.mailbox / "server-ready"
        while time.monotonic() < deadline:
            if ready.is_file():
                return
            if self.server_process.poll() is not None:
                self.fail("fake Docker server exited before readiness")
            time.sleep(0.01)
        self.fail("fake Docker server readiness timed out")

    def _shell_client(self):
        mailbox = shlex.quote(str(self.mailbox))
        pid = shlex.quote(str(self.server_pid))
        return f'''#!/bin/sh
mailbox={mailbox}
server_pid={pid}
# This exact product identity query is intentionally outside the business transport.
if [ "$#" -eq 5 ] && [ "$1" = --host ] && [ "$2" = unix:///fake.sock ] &&
   [ "$3" = info ] && [ "$4" = --format ] && [ "$5" = "{{{{.ID}}}}" ]; then
  printf "%s\\n" synthetic-daemon-id
  exit $?
fi
req=
active="$mailbox/active.$$"
transport_error() {{
  : > "$mailbox/transport-failed" 2>/dev/null || :
  if [ -n "$req" ]; then
    rm -f "$req/argv.tmp" "$req/argv.ready" "$req/argv.claimed" "$req/stdout.tmp" "$req/stderr.tmp" "$req/status.tmp" "$req/done.tmp" "$req/stdout" "$req/stderr" "$req/status" "$req/done" 2>/dev/null || :
    rmdir "$req" 2>/dev/null || :
  fi
  rm -f "$active" 2>/dev/null || :
  exit 97
}}
trap 'transport_error' HUP INT TERM
: > "$active" || transport_error
umask 077
req=$(mktemp -d "$mailbox/req.XXXXXXXX") || transport_error
{{ printf '%s\\n' "$#" || transport_error; for arg do printf '%s\\000' "$arg" || transport_error; done; }} > "$req/argv.tmp" || transport_error
mv "$req/argv.tmp" "$req/argv.ready" || transport_error
count=0
while [ "$count" -lt 1000 ]; do
  [ ! -f "$req/done" ] || break
  count=$((count + 1))
done
if [ ! -f "$req/done" ]; then
  count=0
  while [ "$count" -lt 5 ]; do
    [ ! -f "$req/done" ] || break
    kill -0 "$server_pid" 2>/dev/null || transport_error
    sleep 1 || transport_error
    count=$((count + 1))
  done
fi
[ -f "$req/done" ] || transport_error
[ -f "$req/stdout" ] && [ -r "$req/stdout" ] || transport_error
[ -f "$req/stderr" ] && [ -r "$req/stderr" ] || transport_error
if [ -s "$req/stdout" ]; then cat "$req/stdout" || transport_error; fi
if [ -s "$req/stderr" ]; then cat "$req/stderr" >&2 || transport_error; fi
exec 3< "$req/status" || transport_error
IFS= read -r status <&3 || transport_error
extra=
if IFS= read -r extra <&3 || [ -n "$extra" ]; then exec 3<&-; transport_error; fi
exec 3<&- || transport_error
case "$status" in
  0|[1-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5]) ;;
  *) transport_error;;
esac
rm -f "$req/argv.claimed" "$req/stdout" "$req/stderr" "$req/status" "$req/done" || transport_error
rmdir "$req" || transport_error
rm -f "$active" || transport_error
trap - HUP INT TERM
exit "$status"
'''

    def _cleanup_server(self):
        process = getattr(self, "server_process", None)
        if process is None:
            return
        _terminate_owned_group(process)

    def write_state(self, mode="ok"):
        records = [
            {"kind": "container", "id": "c" * 64, "name": PROJECT + "-postgres-1", "running": True, "health": "healthy"},
            {"kind": "container", "id": "d" * 64, "name": PROJECT + "-api-1", "running": True, "health": "healthy"},
            {"kind": "container", "id": "e" * 64, "name": PROJECT + "-candidate", "running": True, "health": "healthy"},
            {"kind": "container", "id": "f" * 64, "name": PROJECT + "-initialize-1", "running": False, "health": "none"},
            {"kind": "network", "id": "1" * 64, "name": PROJECT + "_private"},
            {"kind": "volume", "id": PROJECT + "_pgdata", "name": PROJECT + "_pgdata"},
        ]
        if mode in ("fresh", "post-create-owner"):
            records = []
        self.state.write_text(json.dumps({"records": records, "mode": mode}))
        self.log.unlink(missing_ok=True)
        (self.root / "lock-owned").touch()

    @staticmethod
    def cli():
        return r'''import json, os, sys
from pathlib import Path
s=Path(__STATE__); log=Path(__LOG__)
PROJECT=__PROJECT__; INSTALL=__INSTALL__; RELEASE=__RELEASE__; LOCK=__LOCK__
data=json.loads(s.read_text()); mode=data['mode']; a=sys.argv[1:]
if a[:2] == ['context','inspect']:
 print('unix:///fake.sock'); sys.exit(0)
if a[:2] != ['--host','unix:///fake.sock']: sys.exit(80)
a=a[2:]
with log.open('a') as f: f.write(json.dumps(a)+'\n')
if a[0]=='info':
 print('linux' if a[-1]=='{{.OSType}}' else 'amd64'); sys.exit(0)
if a[0]=='load': sys.exit(0)
if a[:2]==['image','inspect']:
 print(a[-1]+'|linux|amd64'); sys.exit(0)
if a[0]=='compose':
 tail=a[a.index('-f')+2:] if '-f' in a else a[1:]
 records=data['records']
 def add(kind, ident, name):
  if not any(x['kind']==kind and x['name']==name for x in records):
   item={'kind':kind,'id':ident,'name':name}
   if kind=='container': item.update(running=True,health='healthy')
   records.append(item)
 if tail and tail[0]=='up':
  if mode=='post-create-owner': data['poisoned']=True
  service=tail[-1]
  if service=='postgres':
   add('container','2'*64,PROJECT+'-postgres-1')
   add('network','3'*64,PROJECT+'_private')
   add('volume',PROJECT+'_pgdata',PROJECT+'_pgdata')
  elif service=='api': add('container','4'*64,PROJECT+'-api-1')
 elif tail and tail[0]=='run' and tail[-1]=='api':
  add('container','7'*64,PROJECT+'-candidate')
 s.write_text(json.dumps(data)); sys.exit(0)
kind=a[0]
if kind=='inspect' or (kind in ('container','network','volume') and a[1]=='inspect'):
 ident=a[-1]; rec=next((x for x in data['records'] if x['id']==ident),None)
 if not rec: sys.exit(2)
 if mode=='inspect-fail': sys.exit(3)
 if mode=='lock-lost' and rec['kind']=='volume': Path(LOCK).unlink(missing_ok=True)
 seen=sum(1 for line in log.read_text().splitlines()
          if json.loads(line)[:2]==['container','inspect'] and json.loads(line)[-1]==rec['id'])
 owner='wrong' if (mode=='wrong-owner' and rec['kind']=='volume') or (mode=='late-owner' and seen>=2 and rec['name'].endswith('-candidate')) else INSTALL
 if data.get('poisoned'): owner='wrong'
 fmt=a[a.index('--format')+1]
 if fmt.startswith('{{.State.Health'):
  print(rec.get('health','none'))
 elif fmt.startswith('{{.State.Running'):
  print(str(rec.get('running',False)).lower())
  if mode=='stop-replaced' and rec['id']=='f'*64:
   rec['id']='9'*64; s.write_text(json.dumps(data))
 elif rec['kind']=='container': print('|'.join([rec['id'],'/'+rec['name'],PROJECT,owner,RELEASE]))
 else: print('|'.join([rec['id'],rec['name'],PROJECT,owner,RELEASE]))
 sys.exit(0)
if kind in ('container','network','volume'):
 action=a[1]
 if action=='ls':
  if mode=='list-fail' and kind=='volume': sys.exit(4)
  for r in data['records']:
   if r['kind']==kind: print(r['id']+'|'+r['name'])
  if mode=='unknown' and kind=='container': print('1'*64+'|'+PROJECT+'-foreign')
  sys.exit(0)
 if action=='stop':
  r=next(x for x in data['records'] if x['id']==a[-1])
  if mode=='stop-fail': sys.exit(5)
  if mode=='stop-noop': sys.exit(0)
  r['running']=False
  s.write_text(json.dumps(data)); sys.exit(0)
 if action=='rm':
  if mode=='remove-fail': sys.exit(6)
  data['records']=[r for r in data['records'] if r['id']!=a[-1]]
  if mode=='replace' and a[-1]=='e'*64: data['records'].append({'kind':'container','id':'1'*64,'name':PROJECT+'-candidate','running':False,'health':'healthy'})
  s.write_text(json.dumps(data)); sys.exit(0)
sys.exit(9)
'''

    def run_case(self, mode, op, name=None, timeout=12):
        self.write_state(mode)
        engine = self.installation / "engine"
        engine.write_text("schema=lexiflow-engine-v1\nendpoint=unix:///fake.sock\ndaemon_id=synthetic-daemon-id\n")
        engine.chmod(0o600)
        process = subprocess.Popen(["/bin/sh", str(self.script), str(SOURCE), op, name or ""],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._terminate_owned(process)
            stdout, stderr = process.communicate(timeout=2)
            raise subprocess.TimeoutExpired(process.args, timeout, output=stdout, stderr=stderr)
        except BaseException:
            if process.returncode is None:
                self._terminate_owned(process)
            raise
        residue = [p.name for p in self.mailbox.iterdir()
                   if p.name == "transport-failed" or p.name.startswith(("active.", "req."))]
        if residue or not self.mailbox.is_dir():
            self.fail(f"fake Docker transport left inconsistent mailbox state: {residue!r}")
        if self.server_process.poll() is not None:
            self.fail("fake Docker server exited during run_case")
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    @staticmethod
    def _terminate_owned(process):
        _terminate_owned_group(process)

    def commands(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_stop_uses_full_ids_and_never_deletes_resources(self):
        result = self.run_case("ok", "stop", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        cmds = self.commands()
        stops = [c for c in cmds if len(c) > 1 and c[0:2] == ["container", "stop"]]
        self.assertEqual({c[-1] for c in stops}, {"c"*64, "d"*64, "e"*64, "f"*64})
        self.assertTrue(all(c[2:4] == ["--time", "10"] for c in stops))
        self.assertFalse(any(c[0:2] in (["container", "rm"], ["volume", "rm"], ["network", "rm"])
                             for c in cmds))
        self.assertTrue(all(not r.get("running") for r in json.loads(self.state.read_text())["records"] if r["kind"] == "container"))

    def test_invalid_installation_and_release_ids_are_rejected_before_docker(self):
        for installation, release in (("a" * 31 + ":", RELEASE),
                                       (INSTALL, "b" * 63 + ":"), ("A" * 32, RELEASE),
                                       (INSTALL, "B" * 64), ("é" * 32, RELEASE)):
            self.write_state()
            env = {**self.env, "LF_INSTALL_ID": installation, "LF_CURRENT_KEY": release,
                   "LF_PROJECT": f"lf_{installation}_{release[:16]}"}
            result = subprocess.run(["/bin/sh", str(self.script), str(SOURCE), "stop", ""],
                                    env=env, capture_output=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout + result.stderr, b"")
            self.assertEqual(self.commands(), [])

    def test_prepare_and_start_use_compose_only_after_owned_preflight(self):
        result = self.run_case("ok", "prep", timeout=35)
        self.assertEqual(result.returncode, 0, (result.stderr, self.commands()[-8:]))
        logged = self.commands()
        compose = [c for c in logged if c and c[0] == "compose"]
        initialize = [c for c in compose if c[-1:] == ["initialize"]]
        self.assertEqual(len(initialize), 1)
        self.assertEqual(initialize[0][initialize[0].index("--name") + 1], PROJECT + "-initialize-1")
        self.assertFalse(any(r["name"] == PROJECT + "-candidate" for r in json.loads(self.state.read_text())["records"]))
        result = self.run_case("ok", "start", timeout=35)
        self.assertEqual(result.returncode, 0, result.stderr)
        compose = [c for c in self.commands() if c and c[0] == "compose"]
        self.assertEqual([c[c.index("-f") + 2:] for c in compose if "up" in c],
                         [["up", "-d", "--no-deps", "postgres"], ["up", "-d", "--no-deps", "api"]])
        self.assertFalse(any(c[-1:] == ["initialize"] for c in compose))
        self.assertFalse(any(r["name"] in (PROJECT + "-candidate", PROJECT + "-initialize-1")
                             for r in json.loads(self.state.read_text())["records"]))

    def test_wrong_owner_and_discovery_failure_have_zero_mutations(self):
        for mode in ("wrong-owner", "late-owner", "list-fail", "inspect-fail", "unknown"):
            with self.subTest(mode=mode):
                result = self.run_case(mode, "stop")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(c[0:2] == ["container", "stop"] for c in self.commands()))

    def test_replaced_candidate_after_remove_is_not_reported_clean(self):
        result = self.run_case("replace", "cleanup")
        self.assertNotEqual(result.returncode, 0)
        state = json.loads(self.state.read_text())
        self.assertTrue(any(r["name"] == PROJECT + "-candidate" and r["id"] == "1"*64
                            for r in state["records"]))

    def test_prepare_start_and_stop_refuse_collisions_and_lost_lock_before_mutation(self):
        for operation in ("prep", "start", "stop"):
            for mode in ("wrong-owner", "list-fail", "lock-lost"):
                with self.subTest(operation=operation, mode=mode):
                    result = self.run_case(mode, operation)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout + result.stderr, b"")
                    mutations = [c for c in self.commands() if c[0] in ("compose", "load")
                                 or c[:2] in (["container", "stop"], ["container", "rm"])]
                    self.assertEqual(mutations, [])

    def test_candidate_cleanup_revalidates_and_removes_only_exact_candidate(self):
        result = self.run_case("ok", "cleanup")
        self.assertEqual(result.returncode, 0, result.stderr)
        cmds = self.commands()
        stops = [c for c in cmds if c[0:2] == ["container", "stop"]]
        rms = [c for c in cmds if c[0:2] == ["container", "rm"]]
        self.assertEqual([c[-1] for c in stops], ["e"*64, "f"*64])
        self.assertEqual([c[-1] for c in rms], ["e"*64, "f"*64])

    def test_health_queries_full_id_and_rejects_unknown_or_bad_owner(self):
        result = self.run_case("ok", "health", PROJECT + "-api-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        health = [c for c in self.commands() if c[0] == "inspect" and c[-2].startswith("{{.State.Health")]
        self.assertTrue(health)
        self.assertTrue(all(c[-1] == "d"*64 for c in health))
        result = self.run_case("wrong-owner", "health", PROJECT + "-api-1")
        self.assertNotEqual(result.returncode, 0)

    def test_stop_failure_does_not_continue_or_delete(self):
        result = self.run_case("stop-fail", "stop")
        self.assertNotEqual(result.returncode, 0)
        stops = [c for c in self.commands() if c[0:2] == ["container", "stop"]]
        self.assertEqual(len(stops), 1)
        self.assertFalse(any(c[0:2] == ["container", "rm"] for c in self.commands()))

    def test_stop_success_without_stopped_state_does_not_remove(self):
        result = self.run_case("stop-noop", "cleanup")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0:2] == ["container", "rm"] for c in self.commands()))

    def test_remove_helper_rejects_non_temporary_resource_name(self):
        result = self.run_case("ok", "rmtemp", PROJECT + "-api-1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0:2] == ["container", "rm"] for c in self.commands()))

    def test_empty_installation_prepares_and_post_create_owner_failure_stops_sequence(self):
        result = self.run_case("fresh", "prep", timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, b"")
        names = {r["name"] for r in json.loads(self.state.read_text())["records"]}
        self.assertIn(PROJECT + "-postgres-1", names)
        self.assertNotIn(PROJECT + "-candidate", names)
        self.assertNotIn(PROJECT + "-api-1", names)
        result = self.run_case("post-create-owner", "prep")
        self.assertNotEqual(result.returncode, 0)
        compose = [c for c in self.commands() if c[0] == "compose" and "-f" in c]
        self.assertEqual(len(compose), 1)
        self.assertEqual(compose[0][-3:], ["up", "-d", "postgres"])

    def test_stop_does_not_accept_replaced_container_identity(self):
        result = self.run_case("stop-replaced", "stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:2] == ["container", "rm"] for c in self.commands()))


def _serve_fake_cli(root):
    mailbox = root / "mailbox"
    try:
        config = json.loads((mailbox / "server-config.json").read_text())
        source = pathlib.Path(config["model"]).read_text()
        compiled = compile(source, "<fake-docker-model>", "exec")
        (mailbox / "server-ready.tmp").write_text("ready\n")
        os.replace(mailbox / "server-ready.tmp", mailbox / "server-ready")
        while True:
            found = False
            for request in mailbox.glob("req.*"):
                ready = request / "argv.ready"
                if not ready.exists():
                    continue
                found = True
                claimed = request / "argv.claimed"
                try:
                    os.replace(ready, claimed)
                    with claimed.open("rb") as stream:
                        raw = stream.read(256 * 1024 + 1)
                except FileNotFoundError:
                    continue
                try:
                    header, payload = raw.split(b"\n", 1)
                    if not header or any(byte < 48 or byte > 57 for byte in header):
                        raise ValueError("invalid decimal argv count")
                    count = int(header.decode("ascii"))
                    if count < 0 or len(raw) > 256 * 1024:
                        raise ValueError("invalid request size/count")
                    fields = payload.split(b"\0")
                    if fields[-1] != b"" or len(fields) != count + 1:
                        raise ValueError("invalid NUL argv framing")
                    argv = [os.fsdecode(value) for value in fields[:-1]]
                    stdout, stderr, status = _execute_fake_cli(compiled, argv)
                except BaseException as exc:
                    (mailbox / "transport-failed").write_text(f"server request error: {type(exc).__name__}: {exc}\n")
                    stdout, stderr, status = b"", b"", 97
                if len(stdout) > 256 * 1024 or len(stderr) > 256 * 1024:
                    raise ValueError("response exceeds 256 KiB")
                for name, content in (("stdout", stdout), ("stderr", stderr), ("status", f"{status}\n".encode())):
                    temp = request / (name + ".tmp")
                    with temp.open("wb") as stream:
                        stream.write(content)
                    os.replace(temp, request / name)
                (request / "done.tmp").write_text("done\n")
                os.replace(request / "done.tmp", request / "done")
            if not found:
                time.sleep(0.001)
    except KeyboardInterrupt:
        return 0
    except BaseException as exc:
        try:
            (mailbox / "server-failed").write_text(f"{type(exc).__name__}: {exc}\n")
        except OSError:
            pass
        return 97


def _execute_fake_cli(compiled, argv):
    from contextlib import redirect_stderr, redirect_stdout
    import io

    out, err = io.StringIO(), io.StringIO()
    previous = sys.argv
    status = 0
    try:
        sys.argv = ["docker", *argv]
        with redirect_stdout(out), redirect_stderr(err):
            exec(compiled, {})
    except SystemExit as exc:
        status = int(exc.code or 0)
    finally:
        sys.argv = previous
    return out.getvalue().encode(), err.getvalue().encode(), status


def _owned_group_exists(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None


def _terminate_owned_group(process):
    """终止本次独立进程组，回收直接子进程并确认组消失后才返回。"""
    if process.returncode is not None:
        process.wait(timeout=2)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except (PermissionError, OSError):
            pass
        grace = time.monotonic() + 0.5
        group_state = _owned_group_exists(process.pid)
        while group_state is True and time.monotonic() < grace:
            time.sleep(0.01)
            group_state = _owned_group_exists(process.pid)
        if group_state is not False:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except (PermissionError, OSError):
                pass
        # Always reap the exact direct child, even if group signaling failed.
        reaped = False
        try:
            process.wait(timeout=2)
            reaped = True
        except subprocess.TimeoutExpired:
            pass
        except (PermissionError, OSError, subprocess.SubprocessError):
            pass
        if not reaped:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
                reaped = True
            except (PermissionError, OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
                pass
        if not reaped:
            try:
                process.wait(timeout=2)
                reaped = True
            except (PermissionError, OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
                pass
        if not reaped:
            raise subprocess.SubprocessError("failed to reap owned subprocess")
    # Do not signal by numeric PGID after wait/poll has reaped the direct child.
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        state = _owned_group_exists(process.pid)
        if state is False:
            return
        if state is None:
            break
        time.sleep(0.02)
    raise subprocess.SubprocessError("owned process group did not disappear")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--fake-server":
        raise SystemExit(_serve_fake_cli(pathlib.Path(sys.argv[2])))
    unittest.main()
