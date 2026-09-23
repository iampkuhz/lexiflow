"""Small, fail-closed Qoder-to-Codex dispatch fallback coordinator.

It never invokes a Codex collaboration tool.  The parent invokes that native tool
and later supplies only its call id; this module verifies that id against the
current trusted parent's local rollout before changing the dispatch state.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

import yaml

from scripts.agents.local_codex_runtime import CodexRuntimeError, discover

_SCHEMA = "lexiflow.agent-dispatch.v1"
_TOOL = "spawn_agent"
_LIST_AGENTS_TOOL = "list_agents"


class DispatchFallbackError(ValueError):
    def __init__(self, code: str, detail: str, *, decision: dict[str, Any] | None = None):
        self.code, self.decision = code, decision
        super().__init__(f"{code}: {detail}")


def _policy(root: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load((root / "harness/agent-policy.manifest.yaml").read_text(encoding="utf-8"))["agent_dispatch"]
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise DispatchFallbackError("dispatch-policy-unavailable", str(exc)) from None
    if (not isinstance(value, dict) or value.get("primary") != "qoder"
            or not isinstance(value.get("fallback_model"), str) or not value["fallback_model"]
            or not isinstance(value.get("fallback_reasoning_effort"), str) or not value["fallback_reasoning_effort"]
            or isinstance(value.get("max_consecutive_failures"), bool) or not isinstance(value.get("max_consecutive_failures"), int)
            or value["max_consecutive_failures"] < 1
            or value.get("host_wait_policy") != "reject-active-or-unknown-goal-without-supported-wait-adapter"):
        raise DispatchFallbackError("dispatch-policy-invalid", "agent_dispatch has an invalid type or supported route")
    return value


def _safe_owned_file(path: Path) -> None:
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    try: relative = path.relative_to(home)
    except ValueError: raise DispatchFallbackError("dispatch-runtime-unsafe", "runtime source escapes CODEX_HOME") from None
    try:
        if stat.S_ISLNK(home.lstat().st_mode):
            raise DispatchFallbackError("dispatch-runtime-unsafe", "CODEX_HOME is a symlink")
    except OSError as exc:
        raise DispatchFallbackError("dispatch-runtime-unsafe", str(exc)) from None
    current = home
    for part in relative.parts:
        current = current / part
        if stat.S_ISLNK(current.lstat().st_mode):
            raise DispatchFallbackError("dispatch-runtime-unsafe", "symlink in runtime source")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise DispatchFallbackError("dispatch-runtime-unsafe", "runtime source is not a private regular file")


def host_wait_snapshot(root: str | Path) -> dict[str, str]:
    """Read only this current trusted session's goal row; no caller capability bit."""
    repo = Path(root).resolve()
    _policy(repo)
    try:
        runtime = discover(repo)
    except CodexRuntimeError as exc:
        return {"state": "unknown", "reason": exc.code}
    db = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "goals_1.sqlite"
    try:
        _safe_owned_file(db)
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0)
        try:
            row = con.execute("SELECT status FROM thread_goals WHERE thread_id = ?", (runtime.context["parent_session_id"],)).fetchone()
        finally:
            con.close()
    except (OSError, sqlite3.Error, DispatchFallbackError):
        return {"state": "unknown", "reason": "goal-state-unavailable"}
    if row is None:
        return {"state": "inactive", "reason": "no-goal"}
    status = row[0]
    if status == "active":
        return {"state": "active", "reason": "active-goal"}
    if isinstance(status, str) and status in {"paused", "blocked", "usage_limited", "budget_limited", "complete"}:
        return {"state": "inactive", "reason": "nonactive-goal"}
    return {"state": "unknown", "reason": "goal-state-invalid"}


def host_wait_decision(root: str | Path) -> dict[str, Any] | None:
    repo = Path(root).resolve(); policy = _policy(repo); snapshot = host_wait_snapshot(repo)
    if snapshot["state"] == "inactive":
        return None
    return {"status": "BLOCKED", "code": "HOST_WAIT_UNSUPPORTED", "qoder": "not-started",
            "next_action": "fallback-to-codex-terra", "host_goal": snapshot,
            "fallback": {"model": policy["fallback_model"], "reasoning_effort": policy["fallback_reasoning_effort"]}}


def _identity(root: Path, task: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    try:
        runtime = discover(root)
    except CodexRuntimeError as exc:
        raise DispatchFallbackError(exc.code, str(exc)) from None
    if task.get("parent_session_id") != runtime.context["parent_session_id"]:
        raise DispatchFallbackError("dispatch-identity-conflict", "task parent_session_id differs from the current trusted parent")
    fields = ("work_package_id", "task_ids", "task_versions", "change_versions")
    if any(field not in task for field in fields):
        raise DispatchFallbackError("dispatch-identity-invalid", "work package identity is incomplete")
    identity = {"parent_session_id": runtime.context["parent_session_id"], "repo": str(root),
                **{field: task[field] for field in fields}}
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return identity, hashlib.sha256(encoded).hexdigest()


def _root(root: Path, key: str) -> Path:
    path = root
    for part in ("tmp", "quality", "agent-dispatch", key):
        path = path / part
        if path.exists():
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise DispatchFallbackError("dispatch-state-unsafe", "dispatch state directory is unsafe")
        else:
            try: path.mkdir(mode=0o700)
            except FileExistsError:
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise DispatchFallbackError("dispatch-state-unsafe", "dispatch state directory is unsafe") from None
    return path


def _existing_root(root: Path, key: str) -> Path | None:
    """Find a ledger without allocating its directory or lock (preflight only)."""
    path = root
    for part in ("tmp", "quality", "agent-dispatch", key):
        path = path / part
        if not path.exists():
            if path.is_symlink():
                raise DispatchFallbackError("dispatch-state-unsafe", "dispatch state directory is unsafe")
            return None
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise DispatchFallbackError("dispatch-state-unsafe", "dispatch state directory is unsafe")
    return path


@contextmanager
def _locked(directory: Path):
    lock = directory / ".lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise DispatchFallbackError("dispatch-state-unsafe", "dispatch lock is not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def _read(path: Path, identity: dict[str, Any]) -> dict[str, Any]:
    if path.is_symlink() or (path.exists() and not stat.S_ISREG(path.lstat().st_mode)):
        raise DispatchFallbackError("dispatch-state-unsafe", "state file is not a regular file")
    if not path.exists():
        return {"schema_version": _SCHEMA, "identity": identity, "consecutive_failures": 0, "history": [], "attempts": {}}
    try:
        value = _json_value(path.read_text(encoding="utf-8"), "dispatch state")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise DispatchFallbackError("dispatch-state-invalid", str(exc)) from None
    if (not isinstance(value, dict) or value.get("schema_version") != _SCHEMA or value.get("identity") != identity
            or isinstance(value.get("consecutive_failures"), bool) or not isinstance(value.get("consecutive_failures"), int)
            or value["consecutive_failures"] < 0 or not isinstance(value.get("history"), list)
            or not isinstance(value.get("attempts"), dict)):
        raise DispatchFallbackError("dispatch-state-invalid", "state identity or shape mismatch")
    for attempt in value["attempts"].values():
        if not isinstance(attempt, dict) or not isinstance(attempt.get("qoder"), dict):
            raise DispatchFallbackError("dispatch-state-invalid", "attempt shape is invalid")
        if attempt["qoder"].get("status") not in {"pending", "dispatched", "unavailable"}:
            raise DispatchFallbackError("dispatch-state-invalid", "Qoder attempt state is invalid")
        terra = attempt.get("terra")
        if terra is not None and (not isinstance(terra, dict) or terra.get("status") not in {"required", "pending", "dispatched", "unavailable", "completed"}):
            raise DispatchFallbackError("dispatch-state-invalid", "Terra attempt state is invalid")
    return value


def _write(path: Path, state: dict[str, Any]) -> None:
    if path.is_symlink() or (path.exists() and not stat.S_ISREG(path.lstat().st_mode)):
        raise DispatchFallbackError("dispatch-state-unsafe", "state file is not a regular file")
    fd, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.chmod(temp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":")); stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def ledger_decision(root: str | Path, task: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read pending/stop coordination state without creating any ledger files."""
    repo = Path(root).resolve(); policy = _policy(repo); identity, key = _identity(repo, task)
    directory = _existing_root(repo, key)
    if directory is None:
        return None
    state = _read(directory / "state.json", identity)
    if state["consecutive_failures"] >= policy["max_consecutive_failures"]:
        return {"status": "BLOCKED", "code": "DISPATCH_STOPPED", "next_action": "stop-package-dispatch",
                "consecutive_failures": state["consecutive_failures"]}
    for attempt_id, attempt in state["attempts"].items():
        if (attempt["qoder"].get("status") == "pending"
                or attempt.get("terra", {}).get("status") in {"required", "pending", "dispatched"}):
            return {"status": "BLOCKED", "code": "DISPATCH_PENDING", "next_action": "do-not-redispatch",
                    "attempt_id": attempt_id}
    return None


def begin_qoder_attempt(root: str | Path, task: Mapping[str, Any], attempt_id: str) -> None:
    """Persist one formal Qoder start/resume, refusing stopped or pending pairs."""
    repo = Path(root).resolve(); policy = _policy(repo); identity, key = _identity(repo, task)
    if not isinstance(attempt_id, str) or not attempt_id:
        raise DispatchFallbackError("dispatch-attempt-invalid", "attempt_id is required")
    directory = _root(repo, key); path = directory / "state.json"
    with _locked(directory):
        state = _read(path, identity)
        if state["consecutive_failures"] >= policy["max_consecutive_failures"]:
            raise DispatchFallbackError("dispatch-stopped", "three consecutive paired failures already recorded")
        old = state["attempts"].get(attempt_id)
        if old is not None:
            if old.get("qoder", {}).get("status") == "pending": return
            raise DispatchFallbackError("dispatch-replay-conflict", "attempt already transitioned")
        for previous in state["attempts"].values():
            if previous.get("qoder", {}).get("status") == "pending" or previous.get("terra", {}).get("status") in {"required", "pending", "dispatched"}:
                raise DispatchFallbackError("dispatch-pending", "an earlier pair is awaiting a verified outcome")
        state["attempts"][attempt_id] = {"qoder": {"status": "pending"}, "created_at": time.time()}
        _write(path, state)


def mark_qoder_started(root: str | Path, task: Mapping[str, Any], attempt_id: str) -> None:
    """A real qodercli Popen was observed; this is a dispatch success, not acceptance."""
    repo = Path(root).resolve(); identity, key = _task_identity(repo, task); directory = _root(repo, key); path = directory / "state.json"
    with _locked(directory):
        state = _read(path, identity); attempt = state["attempts"].get(attempt_id)
        if not isinstance(attempt, dict): raise DispatchFallbackError("dispatch-attempt-invalid", "unknown Qoder attempt")
        status = attempt.get("qoder", {}).get("status")
        if status == "dispatched": return
        if status != "pending": raise DispatchFallbackError("dispatch-replay-conflict", "Qoder attempt already terminal")
        attempt["qoder"] = {"status": "dispatched"}; state["consecutive_failures"] = 0
        state["history"].append({"attempt_id": attempt_id, "event": "qoder-started"}); _write(path, state)


def _task_identity(root: Path, task: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    fields = ("work_package_id", "task_ids", "task_versions", "change_versions")
    identity = {"parent_session_id": task.get("parent_session_id"), "repo": str(root),
                **{field: task.get(field) for field in fields}}
    if not isinstance(identity["parent_session_id"], str) or any(identity[field] is None for field in fields):
        raise DispatchFallbackError("dispatch-identity-invalid", "persisted Qoder task identity is incomplete")
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return identity, hashlib.sha256(encoded).hexdigest()


def mark_qoder_unavailable(root: str | Path, task: Mapping[str, Any], attempt_id: str, reason: str) -> dict[str, Any]:
    """Trusted runner transition for a no-start dispatch failure, never execution failure."""
    repo = Path(root).resolve(); policy = _policy(repo); identity, key = _task_identity(repo, task)
    directory = _root(repo, key); path = directory / "state.json"
    with _locked(directory):
        state = _read(path, identity); attempt = state["attempts"].get(attempt_id)
        if not isinstance(attempt, dict) or attempt.get("qoder", {}).get("status") != "pending":
            raise DispatchFallbackError("dispatch-replay-conflict", "attempt is not pending Qoder dispatch")
        attempt["qoder"] = {"status": "unavailable", "reason": reason}
        attempt["terra"] = {"status": "required"}
        state["history"].append({"attempt_id": attempt_id, "event": "qoder-unavailable", "reason": reason})
        _write(path, state)
    return {"status": "BLOCKED", "code": "TERRA_REQUIRED", "attempt_id": attempt_id,
            "next_action": "spawn-codex-subagent", "fallback": {"model": policy["fallback_model"], "reasoning_effort": policy["fallback_reasoning_effort"]},
            "work_package_id": task["work_package_id"], "task_ids": task["task_ids"]}


def begin_terra_fallback(root: str | Path, task: Mapping[str, Any], attempt_id: str, reason: str) -> dict[str, Any]:
    """Persist one Qoder-unavailable attempt and return the native Terra request."""
    repo = Path(root).resolve(); policy = _policy(repo); identity, key = _identity(repo, task)
    if not isinstance(attempt_id, str) or not attempt_id or not isinstance(reason, str) or not reason:
        raise DispatchFallbackError("dispatch-attempt-invalid", "attempt_id and reason are required")
    directory = _root(repo, key); path = directory / "state.json"
    with _locked(directory):
        state = _read(path, identity)
        if state["consecutive_failures"] >= policy["max_consecutive_failures"]:
            return {"status": "BLOCKED", "code": "DISPATCH_STOPPED", "attempt_id": attempt_id,
                    "next_action": "stop-package-dispatch", "consecutive_failures": state["consecutive_failures"]}
        attempt = state["attempts"].get(attempt_id)
        if attempt is None:
            for previous in state["attempts"].values():
                if previous.get("qoder", {}).get("status") == "pending" or previous.get("terra", {}).get("status") in {"required", "pending", "dispatched"}:
                    raise DispatchFallbackError("dispatch-pending", "an earlier pair is awaiting a verified outcome")
        if attempt is None:
            attempt = {"qoder": {"status": "unavailable", "reason": reason}, "terra": {"status": "required"}, "created_at": time.time()}
            state["attempts"][attempt_id] = attempt
            state["history"].append({"attempt_id": attempt_id, "event": "qoder-unavailable", "reason": reason})
            _write(path, state)
        elif attempt.get("qoder", {}).get("status") == "pending":
            attempt["qoder"] = {"status": "unavailable", "reason": reason}; attempt["terra"] = {"status": "required"}
            state["history"].append({"attempt_id": attempt_id, "event": "qoder-unavailable", "reason": reason}); _write(path, state)
        elif attempt.get("qoder", {}).get("status") != "unavailable":
            raise DispatchFallbackError("dispatch-replay-conflict", "attempt already has a different Qoder state")
        terra = attempt.get("terra", {})
        if terra.get("status") == "dispatched":
            return {"status": "PASS", "code": "TERRA_ALREADY_DISPATCHED", "attempt_id": attempt_id,
                    "native_call_id": terra.get("call_id"), "task_name": terra.get("task_name")}
        if terra.get("status") == "unavailable":
            return {"status": "BLOCKED", "code": "BOTH_DISPATCH_UNAVAILABLE", "attempt_id": attempt_id,
                    "consecutive_failures": state["consecutive_failures"]}
        if terra.get("status") == "pending":
            return {"status": "BLOCKED", "code": "TERRA_RESULT_UNKNOWN", "attempt_id": attempt_id, "next_action": "do-not-redispatch"}
        if terra.get("status") == "completed":
            raise DispatchFallbackError("dispatch-replay-conflict", "completed Terra attempt needs a new attempt id")
    return {"status": "BLOCKED", "code": "TERRA_REQUIRED", "attempt_id": attempt_id,
            "next_action": "spawn-codex-subagent", "fallback": {"model": policy["fallback_model"], "reasoning_effort": policy["fallback_reasoning_effort"]},
            "work_package_id": task["work_package_id"], "task_ids": task["task_ids"]}


def _current_rollout(root: Path, session_id: str) -> Path:
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    candidates = list((home / "sessions").glob(f"**/*-{session_id}.jsonl"))
    if len(candidates) != 1:
        raise DispatchFallbackError("native-tool-record-unavailable", "expected one current parent rollout")
    _safe_owned_file(candidates[0]); return candidates[0]


def _json_value(value: str, description: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = item
        return result
    try:
        return json.loads(value, object_pairs_hook=unique)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DispatchFallbackError("native-tool-call-invalid", f"{description} is not strict JSON") from None


def _tool_record(root: Path, session_id: str, call_id: str, tool_name: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(call_id, str) or not call_id:
        raise DispatchFallbackError("native-tool-call-invalid", "call_id is required")
    call = output = None
    try:
        with _current_rollout(root, session_id).open(encoding="utf-8") as stream:
            for line in stream:
                # Do not retain parent prose/tool payloads from unrelated records.
                if call_id not in line:
                    continue
                event = _json_value(line, "native rollout event")
                if not isinstance(event, dict) or event.get("type") != "response_item": continue
                payload = event.get("payload")
                if not isinstance(payload, dict) or payload.get("call_id") != call_id: continue
                if payload.get("type") == "function_call" and payload.get("name") == tool_name:
                    if call is not None: raise DispatchFallbackError("native-tool-call-invalid", "duplicate native call id")
                    call = payload
                elif payload.get("type") == "function_call_output":
                    if output is not None: raise DispatchFallbackError("native-tool-call-invalid", "duplicate spawn output id")
                    output = payload
    except (OSError, UnicodeError) as exc:
        raise DispatchFallbackError("native-tool-record-unavailable", str(exc)) from None
    return call, output


def _call_args(call: Mapping[str, Any]) -> dict[str, Any]:
    raw = call.get("arguments")
    value = _json_value(raw, "native arguments") if isinstance(raw, str) else raw
    if not isinstance(value, dict):
        raise DispatchFallbackError("native-tool-call-invalid", "native arguments are not an object")
    return value


def _terminal_terra_from_list(
    state: dict[str, Any], attempt_id: str, call_id: str, call: Mapping[str, Any], output: Mapping[str, Any] | None,
) -> dict[str, Any]:
    attempt = state["attempts"].get(attempt_id)
    if not isinstance(attempt, dict) or attempt.get("terra", {}).get("status") not in {"dispatched", "completed"}:
        raise DispatchFallbackError("dispatch-replay-conflict", "attempt is not a dispatched Terra task")
    if _call_args(call) != {} or output is None or not isinstance(output.get("output"), str):
        raise DispatchFallbackError("native-tool-call-invalid", "list_agents record is incomplete")
    result = _json_value(output["output"], "list_agents output")
    if not isinstance(result, dict) or not isinstance(result.get("agents"), list):
        raise DispatchFallbackError("native-tool-call-invalid", "list_agents output is unsupported")
    handle = attempt["terra"].get("task_name")
    matches = [item for item in result["agents"] if isinstance(item, dict) and item.get("agent_name") == handle]
    native_status = matches[0].get("agent_status") if len(matches) == 1 else None
    # This is the actual native collaboration envelope, not an invented status DTO.
    if not isinstance(native_status, dict) or set(native_status) != {"completed"} or not isinstance(native_status["completed"], str):
        raise DispatchFallbackError("native-tool-call-invalid", "list_agents does not prove this Terra task terminal")
    if attempt["terra"]["status"] == "completed":
        if attempt["terra"].get("completion_call_id") != call_id:
            raise DispatchFallbackError("dispatch-replay-conflict", "completion already recorded from another call")
        return {"status": "PASS", "code": "TERRA_TERMINAL", "attempt_id": attempt_id,
                "task_name": handle, "terminal_status": "completed"}
    attempt["terra"] = {**attempt["terra"], "status": "completed", "completion_call_id": call_id,
                         "completion_output_sha256": hashlib.sha256(output["output"].encode()).hexdigest(),
                         "terminal_status": "completed"}
    state["history"].append({"attempt_id": attempt_id, "event": "terra-terminal", "call_id": call_id,
                             "terminal_status": "completed"})
    return {"status": "PASS", "code": "TERRA_TERMINAL", "attempt_id": attempt_id,
            "task_name": handle, "terminal_status": "completed"}



def record_terra_fallback(root: str | Path, task: Mapping[str, Any], attempt_id: str, call_id: str) -> dict[str, Any]:
    """Consume a current-parent native spawn record; unknown results remain pending."""
    repo = Path(root).resolve(); policy = _policy(repo); identity, key = _identity(repo, task)
    call, output = _tool_record(repo, identity["parent_session_id"], call_id, _TOOL)
    if call is None:
        list_call, list_output = _tool_record(repo, identity["parent_session_id"], call_id, _LIST_AGENTS_TOOL)
        if list_call is None:
            raise DispatchFallbackError("native-tool-call-unavailable", "native spawn/list call is not in this parent rollout")
        directory = _root(repo, key); path = directory / "state.json"
        with _locked(directory):
            state = _read(path, identity)
            result = _terminal_terra_from_list(state, attempt_id, call_id, list_call, list_output)
            _write(path, state)
            return result
    args = _call_args(call)
    if not isinstance(args, dict) or args.get("model") != policy["fallback_model"] or args.get("reasoning_effort") != policy["fallback_reasoning_effort"]:
        raise DispatchFallbackError("native-tool-call-invalid", "call did not explicitly request the configured fallback model and reasoning effort")
    # Bind to the intended package without retaining the complete prompt in state.
    prompt = args.get("message") or args.get("prompt")
    if not isinstance(prompt, str) or task["work_package_id"] not in prompt or attempt_id not in prompt:
        raise DispatchFallbackError("native-tool-call-invalid", "native call does not bind this package and attempt")
    directory = _root(repo, key); path = directory / "state.json"
    with _locked(directory):
        state = _read(path, identity); attempt = state["attempts"].get(attempt_id)
        if not isinstance(attempt, dict) or attempt.get("terra", {}).get("status") not in {"required", "pending", "dispatched", "unavailable", "completed"}:
            raise DispatchFallbackError("dispatch-replay-conflict", "attempt is not awaiting Terra")
        prior = attempt.get("terra", {})
        if prior.get("status") == "pending" and prior.get("call_id") != call_id:
            raise DispatchFallbackError("dispatch-replay-conflict", "pending Terra attempt cannot change call id")
        if prior.get("status") in {"dispatched", "completed"}:
            if prior.get("call_id") != call_id: raise DispatchFallbackError("dispatch-replay-conflict", "attempt already dispatched")
            return {"status": "PASS", "attempt_id": attempt_id, "consecutive_failures": state["consecutive_failures"], "native_call_id": call_id}
        if prior.get("status") == "unavailable":
            if prior.get("call_id") != call_id: raise DispatchFallbackError("dispatch-replay-conflict", "attempt already terminal")
            stopped = state["consecutive_failures"] >= policy["max_consecutive_failures"]
            return {"status": "BLOCKED", "code": "DISPATCH_STOPPED" if stopped else "BOTH_DISPATCH_UNAVAILABLE", "attempt_id": attempt_id, "consecutive_failures": state["consecutive_failures"]}
        if output is None:
            attempt["terra"] = {"status": "pending", "call_id": call_id}; _write(path, state)
            return {"status": "BLOCKED", "code": "TERRA_RESULT_UNKNOWN", "attempt_id": attempt_id, "next_action": "do-not-redispatch"}
        raw = output.get("output")
        try:
            result = _json_value(raw, "native spawn output") if isinstance(raw, str) else raw
        except DispatchFallbackError:
            if isinstance(raw, str) and raw.lstrip().startswith(("{", "[")):
                raise
            result = None
        if isinstance(result, dict) and set(result) == {"task_name"} and isinstance(result["task_name"], str) and result["task_name"]:
            handle = result["task_name"]
            attempt["terra"] = {"status": "dispatched", "call_id": call_id, "task_name": handle,
                                "handle_sha256": hashlib.sha256(handle.encode()).hexdigest(),
                                "call_args_sha256": hashlib.sha256(json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                                "output_sha256": hashlib.sha256(raw.encode()).hexdigest() if isinstance(raw, str) else None}
            state["consecutive_failures"] = 0; state["history"].append({"attempt_id": attempt_id, "event": "terra-dispatched", "call_id": call_id, "task_name": handle})
            _write(path, state)
            return {"status": "PASS", "attempt_id": attempt_id, "consecutive_failures": 0, "native_call_id": call_id, "task_name": handle}
        known_failure = isinstance(raw, str) and raw.startswith("collab spawn failed:")
        known_failure = known_failure or (isinstance(result, dict) and result.get("isError") is True)
        if known_failure:
            attempt["terra"] = {"status": "unavailable", "call_id": call_id,
                                "call_args_sha256": hashlib.sha256(json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                                "output_sha256": hashlib.sha256(raw.encode()).hexdigest() if isinstance(raw, str) else None}; state["consecutive_failures"] += 1
            state["history"].append({"attempt_id": attempt_id, "event": "both-unavailable", "call_id": call_id}); _write(path, state)
            stopped = state["consecutive_failures"] >= policy["max_consecutive_failures"]
            return {"status": "BLOCKED", "code": "DISPATCH_STOPPED" if stopped else "BOTH_DISPATCH_UNAVAILABLE", "attempt_id": attempt_id, "consecutive_failures": state["consecutive_failures"], "next_action": "stop-package-dispatch" if stopped else "retry-qoder"}
        attempt["terra"] = {"status": "pending", "call_id": call_id,
                            "call_args_sha256": hashlib.sha256(json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                            "output_sha256": hashlib.sha256(raw.encode()).hexdigest() if isinstance(raw, str) else None}; _write(path, state)
        return {"status": "BLOCKED", "code": "TERRA_RESULT_UNKNOWN", "attempt_id": attempt_id, "next_action": "do-not-redispatch"}
