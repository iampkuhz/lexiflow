"""每例私有、仅测试使用的有限 Podman fake CLI 传输。"""
from __future__ import annotations

import io
import linecache
import os
from pathlib import Path
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
import traceback
from typing import Mapping, Sequence

MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_ENV_BYTES = 256 * 1024
MAX_CWD_BYTES = 64 * 1024
START_TIMEOUT = 3.0
REQUEST_TIMEOUT = 12.0
STOP_TIMEOUT = 2.0

# 唯一有限路由声明：只选择不会观察命令 PID、信号、TTY 或流式输出的例。
# 未列出的用例不启动 server；任何未列出的命令均执行原 Python 程序。
_COMMANDS = {
    "build": None, "compose": {"config", "up", "stop"},
    "container": {"inspect"}, "image": {"exists", "inspect"},
    "info": None, "inspect": None, "network": {"ls"}, "ps": None,
    "rm": None, "rmi": None, "stop": None, "tag": None,
    "volume": {"ls"},
}
# The long Node candidate fixture has its own fixed, non-signalling route set.
# Keep this separate from _COMMANDS so existing Python cases cannot inherit it.
_CANDIDATE_COMMANDS = {
    "info": None,
    "image": {"exists", "inspect"},
    "tag": None,
    "rmi": None,
    "ps": None,
    "container": {"inspect"},
    "inspect": None,
    "stop": None,
    "rm": None,
    "network": {"ls"},
    "volume": {"ls"},
    "compose": {"config", "up", "stop"},
}
_CANDIDATE_ROUTE = "test_candidate_local_lifecycle"
FINITE_ROUTES = {name: _COMMANDS for name in (
    "test_first_install_plan_uses_current_checkout_and_not_foreign_private_state",
    "test_install_stop_up_does_not_reimport",
    "test_external_runlocal_uses_only_app_secret_and_recovers_read_only",
    "test_repeat_install_uses_same_executor_and_preserves_data",
    "test_upgrade_cli_uses_api_only_and_never_initializes_again",
    "test_install_and_upgrade_share_explicit_java_home",
    "test_upgrade_rejects_successful_compose_that_reuses_old_api_container",
    "test_upgrade_build_and_start_failures_restore_then_retry",
    "test_same_version_postgres_build_failure_requires_confirmed_forward_retry",
    "test_same_version_api_build_failure_retries_after_precise_removal",
    "test_verify_plan_failure_keeps_config_bound_for_retry",
    "test_fetch_failure_can_retry_before_database",
    "test_incomplete_install_rejects_changed_identity_resolver",
    "test_up_does_not_repair_tampered_network_or_reimport",
    "test_readiness_deadline_keeps_initialized_data",
    "test_uncertain_initialization_never_retries",
    "test_tampered_configuration_refuses_stop",
    "test_foreign_project_label_refuses_stop",
    "test_owned_postgres_port_does_not_block_preinit_retry",
    "test_schema_and_csv_tampering_block_initialization",
)}
# `load`, `run`, `pull`, `images`, `volume inspect`, compose `ps`/`run`/`logs`,
# and every unlisted command intentionally fall through to the original model.
FINITE_ROUTES[_CANDIDATE_ROUTE] = _CANDIDATE_COMMANDS
# 专属小测试用路由，用以验证 argv/cwd/env/退出与故障通道。
FINITE_ROUTES["test_transport_equivalence_and_context"] = {"": None, "probe": None}
FINITE_TEST_CASES = frozenset(FINITE_ROUTES)


class TransportError(RuntimeError):
    """协议/资源错误，绝不能映射为模型的正常返回。"""


class _LimitedBytesIO(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_RESPONSE_BYTES:
            raise TransportError("model response over limit")
        return super().write(data)


class PodmanFakeTransport:
    """拥有一个测试例的 server、wrapper 与私有 mailbox。"""

    def __init__(self, model: Path, bin_dir: Path, owner_dir: Path, test_case: str):
        if test_case not in FINITE_TEST_CASES:
            raise ValueError(f"ineligible test case: {test_case}")
        self.model = Path(model)
        self.bin_dir = Path(bin_dir)
        self.owner_dir = Path(owner_dir)
        self.test_case = test_case
        self.mailbox = self.owner_dir / "podman-transport"
        self.server: subprocess.Popen | None = None
        self.server_log = None
        self.started = False

    def start(self):
        if self.started:
            raise RuntimeError("transport already started")
        self.mailbox.mkdir(mode=0o700)
        original = self.bin_dir / "podman"
        self.model = self.bin_dir / "podman-model.py"
        os.replace(original, self.model)
        self.model.chmod(0o700)
        self.server_log = (self.owner_dir / "podman-transport-server.log").open("xb")
        self.server = subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__).resolve()), "--serve", str(self.mailbox), str(self.model)],
            cwd=self.owner_dir, stdin=subprocess.DEVNULL, stdout=self.server_log,
            stderr=subprocess.STDOUT, close_fds=True)
        self.started = True
        try:
            deadline = time.monotonic() + START_TIMEOUT
            while time.monotonic() < deadline:
                ready = self.mailbox / "server.ready"
                if ready.is_file() and not ready.is_symlink():
                    self._write_wrapper()
                    return
                if self.server.poll() is not None:
                    raise TransportError("server exited before readiness")
                time.sleep(.002)
            raise TransportError("server readiness timed out")
        except BaseException:
            self.close()
            raise

    def _write_wrapper(self):
        assert self.server is not None
        utilities = {name: shutil.which(name, path=os.defpath) for name in ("env", "wc", "cat")}
        if any(value is None for value in utilities.values()):
            raise TransportError("required POSIX utility is unavailable")
        commands = FINITE_ROUTES[self.test_case]
        allowed = " ".join(sorted(commands))
        cases = []
        for command, subcommands in sorted(commands.items()):
            if subcommands is None:
                cases.append(f"  {shlex.quote(command)}) ;;" if command else "  '') ;;")
            elif command == "compose":
                # Only known value-taking options may precede the actual verb.
                # Shift function-local positional args, preserving the original argv.
                cases.extend(("  compose)", "    route_subcommand=",
                              "    compose_verb() {",
                              "      shift",
                              "      while [ \"$#\" -gt 0 ]; do",
                              "        case \"$1\" in",
                              "          --env-file|--project-name|--project-directory|--file|-p|-f)",
                              "            [ \"$#\" -ge 2 ] || return 1;",
                              "            case \"$2\" in ''|-*) return 1;; esac; shift 2;;",
                              "          -*) return 1;;",
                              "          *) route_subcommand=$1; return 0;;",
                              "        esac",
                              "      done",
                              "      return 1",
                              "    }",
                              "    compose_verb \"$@\" || native \"$@\"",
                              "    [ -n \"$route_subcommand\" ] || native \"$@\"",
                              "    case \" $allowed_compose \" in *\" $route_subcommand \"*) ;; *) native \"$@\";; esac",
                              "    ;;"))
            else:
                cases.append(f"  {shlex.quote(command)}) case \" $allowed_{command} \" in *\" ${{2-}} \"*) ;; *) native \"$@\";; esac;;")
        cases.append('  *) native "$@";;')
        script = r'''#!/bin/bash
box=__BOX__
model=__MODEL__
server=__PID__
envbin=__ENV__
wcb=__WC__
catb=__CAT__
allowed_commands=' __COMMANDS__ '
__SUBALLOW__
native() { exec "$model" "$@"; }
case " $allowed_commands " in *" ${1-} "*) ;; *) native "$@";; esac
case "${1-}" in pull|run) native "$@";; esac
case "${1-}" in
__CASES__
esac
req="$box/q.$$"
fail() { : > "$box/server.error" 2>/dev/null || :; exit 97; }
trap 'fail' HUP INT TERM
umask 077
set -C
exec 3> "$req.args" || fail
{ printf '%s\n' "$#"; for arg do printf '%s\000' "$arg"; done; } >&3 || fail
exec 3>&-
"$envbin" -0 > "$req.env" || fail
pwd -P > "$req.cwd" || fail
: > "$req.ready" || fail
SECONDS=0
n=0
while [ ! -f "$req.done" ]; do
  [ ! -e "$box/server.error" ] && [ ! -e "$box/server.failed" ] || exit 97
  kill -0 "$server" 2>/dev/null || fail
  n=$((n+1)); [ "$n" -le 30000 ] && [ "$SECONDS" -le 12 ] || fail
done
for suffix in out err status lengths; do [ -f "$req.$suffix" ] && [ ! -L "$req.$suffix" ] || fail; done
extra=
exec 4< "$req.lengths" || fail
IFS=' ' read -r outlen errlen extra <&4 || fail
[ -z "$extra" ] || fail
extra=
if IFS= read -r extra <&4 || [ -n "$extra" ]; then fail; fi
exec 4<&-
case "$outlen:$errlen" in *[!0-9:]*|:*) fail;; esac
[ "${#outlen}" -le 6 ] && [ "${#errlen}" -le 6 ] || fail
[ "$outlen" -le 262144 ] && [ "$errlen" -le 262144 ] || fail
counts=$(cd "$box" && "$wcb" -c "q.$$.out" "q.$$.err") || fail
set -- $counts
[ "$#" -eq 6 ] && [ "$2" = "q.$$.out" ] && [ "$4" = "q.$$.err" ] && [ "$6" = total ] || fail
[ "$1" -eq "$outlen" ] && [ "$3" -eq "$errlen" ] || fail
exec 3< "$req.status" || fail
IFS= read -r status <&3 || fail
extra=
if IFS= read -r extra <&3 || [ -n "$extra" ]; then fail; fi
exec 3<&-
case "$status" in 0|[1-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5]) ;; *) fail;; esac
LC_ALL=C
export LC_ALL
for stream in out err; do
  stream_file="$req.$stream"
  stream_len=$outlen
  [ "$stream" = out ] || stream_len=$errlen
  if [ "$stream_len" -eq 0 ]; then
    :
  elif IFS= read -r line < "$stream_file" && [ "$(( ${#line} + 1 ))" -eq "$stream_len" ]; then
    if [ "$stream" = out ]; then printf '%s\n' "$line" || fail
    else printf '%s\n' "$line" >&2 || fail
    fi
  else
    if [ "$stream" = out ]; then "$catb" "$stream_file" || fail
    else "$catb" "$stream_file" >&2 || fail
    fi
  fi
done
: > "$req.ack" || fail
trap - HUP INT TERM
exit "$status"
'''
        suballow = "\n".join(f"allowed_{command}=' {' '.join(sorted(subcommands))} '"
                             for command, subcommands in commands.items() if subcommands is not None)
        replacements = {"BOX": shlex.quote(str(self.mailbox)), "MODEL": shlex.quote(str(self.model)),
                        "PID": str(self.server.pid), "ENV": shlex.quote(utilities["env"]),
                        "WC": shlex.quote(utilities["wc"]), "CAT": shlex.quote(utilities["cat"]),
                        "COMMANDS": allowed, "SUBALLOW": suballow, "CASES": "\n".join(cases)}
        for key, value in replacements.items():
            script = script.replace("__" + key + "__", value)
        wrapper = self.bin_dir / "podman"
        wrapper.write_text(script)
        wrapper.chmod(0o700)

    def close(self):
        if not self.started:
            return
        error = None
        try:
            assert self.server is not None
            (self.mailbox / "server.stop").touch(mode=0o600)
            try:
                self.server.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                self.server.send_signal(signal.SIGTERM)
                try:
                    self.server.wait(timeout=STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    self.server.kill()
                    self.server.wait(timeout=STOP_TIMEOUT)
            if self.server.returncode != 0:
                raise TransportError(f"server exited {self.server.returncode}")
            if list(self.mailbox.glob("q.*")) or any((self.mailbox / n).exists() for n in ("server.error", "server.failed")):
                raise TransportError("server cleanup left failure or request residue")
        except BaseException as exc:
            error = exc
        finally:
            if self.server is not None and self.server.poll() is None:
                self.server.kill()
                self.server.wait(timeout=STOP_TIMEOUT)
            if self.server_log is not None:
                self.server_log.close()
            self.started = False
        if error is not None:
            raise error


def _side(base: Path, suffix: str) -> Path:
    return Path(str(base) + suffix)


def _regular(path: Path, limit: int) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise TransportError(f"invalid transport file {path.name}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise TransportError(f"transport file over limit {path.name}")
    return data


def _decode_args(raw: bytes) -> list[str]:
    if len(raw) > MAX_REQUEST_BYTES:
        raise TransportError("argv over limit")
    header, sep, payload = raw.partition(b"\n")
    if not sep or not header or not header.isdigit() or len(header) > 8:
        raise TransportError("bad argv header")
    count = int(header)
    if not payload:
        if count:
            raise TransportError("argv count mismatch")
        return []
    fields = payload.split(b"\0")
    if fields[-1] != b"" or len(fields) != count + 1:
        raise TransportError("argv framing mismatch")
    return [os.fsdecode(value) for value in fields[:-1]]


def _decode_env(raw: bytes) -> dict[str, str]:
    if len(raw) > MAX_ENV_BYTES:
        raise TransportError("environment over limit")
    if raw and not raw.endswith(b"\0"):
        raise TransportError("environment terminal NUL missing")
    result = {}
    for entry in raw.split(b"\0")[:-1] if raw else ():
        key, sep, value = entry.partition(b"=")
        name = os.fsdecode(key)
        if not sep or not name or name in result:
            raise TransportError("invalid environment frame")
        result[name] = os.fsdecode(value)
    return result


def _execute(code, argv: Sequence[str], cwd: str, env: Mapping[str, str], argv0: Path):
    old_argv, old_cwd, old_env = sys.argv, os.getcwd(), dict(os.environ)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    out_bytes, err_bytes = _LimitedBytesIO(), _LimitedBytesIO()
    out = io.TextIOWrapper(out_bytes, encoding=old_stdout.encoding or "utf-8", errors=old_stdout.errors or "surrogateescape", write_through=True)
    err = io.TextIOWrapper(err_bytes, encoding=old_stderr.encoding or "utf-8", errors=old_stderr.errors or "backslashreplace", write_through=True)
    status = 0
    namespace = {"__name__": "__main__", "__file__": str(argv0), "__package__": None}
    try:
        os.chdir(cwd)
        os.environ.clear()
        os.environ.update(env)
        sys.argv = [str(argv0), *argv]
        sys.stdout, sys.stderr = out, err
        try:
            exec(code, namespace, namespace)
        except SystemExit as exc:
            if exc.code is None:
                status = 0
            elif isinstance(exc.code, int):
                status = exc.code & 255
            else:
                print(exc.code, file=sys.stderr)
                status = 1
        except TransportError:
            raise
        except Exception as exc:
            frames = [frame for frame in traceback.extract_tb(exc.__traceback__) if frame.filename == str(argv0)]
            summary = traceback.TracebackException(type(exc), exc, None)
            summary.stack = traceback.StackSummary.from_list(frames)
            summary.print(file=sys.stderr)
            status = 1
        out.flush()
        err.flush()
        return out_bytes.getvalue(), err_bytes.getvalue(), status
    finally:
        sys.argv, sys.stdout, sys.stderr = old_argv, old_stdout, old_stderr
        os.environ.clear()
        os.environ.update(old_env)
        os.chdir(old_cwd)


def serve(mailbox: Path, model: Path):
    if mailbox.is_symlink():
        raise TransportError("mailbox must not be a symlink")
    mailbox = mailbox.resolve(strict=True)
    if not stat.S_ISDIR(mailbox.stat().st_mode) or mailbox.stat().st_mode & 0o077:
        raise TransportError("mailbox is not private")
    argv0 = model.with_name("podman")
    source = model.read_bytes()
    code = compile(source, str(argv0), "exec")
    # Traceback source must be the original Python model, not the new shell wrapper.
    linecache.cache[str(argv0)] = (len(source), None, source.decode("utf-8").splitlines(True), str(argv0))
    marker = mailbox / "server.ready"
    marker.write_text(f"{os.getpid()}\n")
    marker.chmod(0o600)
    completed = 0
    while True:
        if (mailbox / "server.error").exists():
            raise TransportError("client protocol error")
        ready_paths = sorted(mailbox.glob("q.*.ready"))
        if not ready_paths:
            # ready may appear between the preceding glob and this snapshot.
            residues = [p for p in mailbox.glob("q.*") if not p.name.endswith((".args", ".env", ".cwd", ".ready"))]
            if residues:
                raise TransportError("unknown request residue")
            if (mailbox / "server.stop").exists():
                if list(mailbox.glob("q.*")):
                    raise TransportError("stop with unfinished request")
                return 0
            time.sleep(.001)
            continue
        ready = ready_paths[0]
        base = ready.with_suffix("")
        prefix = base.name
        expected_input = {prefix + "." + x for x in ("args", "env", "cwd", "ready")}
        if {p.name for p in mailbox.glob(prefix + ".*")} != expected_input:
            raise TransportError("unexpected request files")
        _regular(ready, 0)
        args = _decode_args(_regular(_side(base, ".args"), MAX_REQUEST_BYTES))
        env = _decode_env(_regular(_side(base, ".env"), MAX_ENV_BYTES))
        cwd_raw = _regular(_side(base, ".cwd"), MAX_CWD_BYTES)
        if not cwd_raw.endswith(b"\n"):
            raise TransportError("bad cwd frame")
        cwd = os.fsdecode(cwd_raw[:-1])
        if not cwd or not os.path.isabs(cwd):
            raise TransportError("cwd not absolute")
        out, err, status = _execute(code, args, cwd, env, argv0)
        if not 0 <= status <= 255:
            raise TransportError("bad model status")
        for suffix, data in (("out", out), ("err", err), ("status", f"{status}\n".encode()),
                             ("lengths", f"{len(out)} {len(err)}\n".encode())):
            _side(base, "." + suffix).write_bytes(data)
        _side(base, ".done").touch(mode=0o600)
        deadline = time.monotonic() + REQUEST_TIMEOUT
        ack = _side(base, ".ack")
        while time.monotonic() < deadline and not ack.exists():
            if (mailbox / "server.error").exists():
                raise TransportError("client protocol error")
            time.sleep(.001)
        if not ack.is_file() or ack.is_symlink():
            raise TransportError("ack timeout")
        expected = {prefix + "." + x for x in ("args", "env", "cwd", "ready", "out", "err", "status", "lengths", "done", "ack")}
        if {p.name for p in mailbox.glob(prefix + ".*")} != expected:
            raise TransportError("unknown residue at ack")
        for suffix in ("args", "env", "cwd", "ready", "out", "err", "status", "lengths", "done", "ack"):
            path = _side(base, "." + suffix)
            if path.is_symlink() or not path.is_file():
                raise TransportError("request ownership/type changed")
            path.unlink()
        completed += 1
        (mailbox / "server.count").write_text(str(completed))


def _main(argv):
    try:
        return serve(Path(argv[2]), Path(argv[3]))
    except BaseException as exc:
        try:
            marker = Path(argv[2]) / "server.failed"
            marker.write_text(f"{type(exc).__name__}: {exc}\n")
            marker.chmod(0o600)
        except BaseException:
            pass
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--serve":
        raise SystemExit("usage: podman_fake_transport --serve BOX MODEL")
    raise SystemExit(_main(sys.argv))
