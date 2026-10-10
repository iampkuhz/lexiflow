"""Qoder 历史运行的生命周期消费与内部 worker/watchdog 支持。

公开入口只读取结果、校验结构、ack 或记录既有 fallback，不创建 start/resume。
旧 worker 仍等待真实 CLI 退出并先发布 completion，再通知父会话；退出零不是验收。
公开命令由 delegation/qoder_cli.py 调用；直接执行只供内部 worker/watchdog。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# 支持不设置 PYTHONPATH 直接运行本脚本；把仓库根加入搜索路径
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.agents.qoder import lifecycle as _lifecycle  # noqa: E402
from scripts.agents.qoder.cli import parse_command  # noqa: E402
from scripts.agents.qoder.read_model import (  # noqa: E402
    _status_snapshot,
)
from scripts.agents.qoder.handoff import (  # noqa: E402
    DEFAULT_PERMISSION_MODE,
    _is_valid_uuid,
    _validate_task,
)
from scripts.agents.qoder.failure_diagnostics import (  # noqa: E402
    summarize_cli_failure,
)
from scripts.agents.local_codex_runtime import (  # noqa: E402
    discover as discover_codex_runtime,
)  # noqa: E402
from scripts.agents import dispatch_fallback as _fallback  # noqa: E402

_validate_run_id = _lifecycle._validate_run_id
_check_no_symlink_ancestors = _lifecycle._check_no_symlink_ancestors
_atomic_write_json = _lifecycle._atomic_write_json
_safe_read_json = _lifecycle._safe_read_json

MAX_PROMPT_CHARACTERS = 8000


def _find_repo_root(start: Path) -> Path:
    """从 start 向上查找含 .git 的仓库根目录。"""
    current = start.absolute()
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    raise FileNotFoundError(f"repo root (with .git) not found from {start}")


def _find_qoder_cli() -> Path:
    """查找 qodercli 可执行文件，支持环境变量覆盖。"""
    override = os.environ.get("QODER_TASK_CLI")
    if override:
        p = Path(override)
        if not p.is_file() or not os.access(p, os.X_OK):
            raise FileNotFoundError(f"qodercli not found at {override}")
        return p
    found = shutil.which("qodercli")
    if not found:
        raise FileNotFoundError("qodercli not found on PATH")
    return Path(found)


def _task_dir(repo_root: Path) -> Path:
    """返回任务目录，支持环境变量覆盖（须在本仓库内且无符号链接）。"""
    override = os.environ.get("QODER_TASK_DIR")
    if override:
        td = Path(override)
        if not td.exists():
            raise FileNotFoundError(f"QODER_TASK_DIR does not exist: {override}")
        _check_no_symlink_ancestors(td)
        try:
            td.resolve().relative_to(repo_root.resolve())
        except ValueError:
            raise ValueError(
                f"QODER_TASK_DIR must be inside repo: {override}"
            ) from None
        return td
    default = repo_root / "tmp" / "qoder-tasks"
    _check_no_symlink_ancestors(default)
    return default


@contextmanager
def _host_lease():
    """同一用户所有 checkout 共用；只 close，不 LOCK_UN 解开子进程继承的锁。"""
    path = _host_lock_path()
    _check_no_symlink_ancestors(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(
        path,
        os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CREAT,
        0o600,
    )
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
        ):
            raise ValueError("unsafe host Qoder lease file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("BUSY: host-wide Qoder lease is held") from None
        yield fd
    finally:
        os.close(fd)


def _host_lock_path() -> Path:
    # 刻意独立于 cwd、CODEX_HOME 和 Task 包身份。
    return Path.home() / ".cache" / "lexiflow" / "qoder-cli.lock"


def _verify_inherited_lease(fd: int) -> None:
    path = _host_lock_path()
    _check_no_symlink_ancestors(path)
    actual, expected = os.fstat(fd), path.stat()
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError("worker did not inherit the host Qoder lease")
    if (
        not stat.S_ISREG(actual.st_mode)
        or actual.st_uid != os.getuid()
        or actual.st_mode & 0o022
    ):
        raise ValueError("unsafe inherited Qoder lease")
    # 未持锁的伪造描述符不能覆盖现有持有者。
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _build_prompt(task: dict[str, Any]) -> str:
    """构建完整 prompt，包含所有 handoff 字段和身份信息。"""
    lines = [
        f"Goal: {task['goal']}",
        f"Work package id: {task['work_package_id']}",
        f"Task ids: {json.dumps(task['task_ids'], ensure_ascii=False)}",
        f"Task versions: {json.dumps(task['task_versions'], sort_keys=True)}",
        f"Change versions: {json.dumps(task['change_versions'], sort_keys=True)}",
        f"Estimated minutes: {task['estimated_minutes']}",
        f"Primary owner: {task['primary_owner']}",
        f"Contract boundary: {task['contract_boundary']}",
        f"Agent profile: {task['agent_profile']}",
        f"Harness manifest: {task['harness_manifest']}",
        f"Harness manifest SHA-256: {task.get('harness_manifest_sha256', '<runner-bound>')}",
        "Frozen harness context: "
        + json.dumps(task.get("harness_context", []), ensure_ascii=False),
        "Read every task record named in task_ids from the hash-bound planning/workstreams.yaml; "
        "apply each deliverable, acceptance contract, validation command, and file claim before implementation.",
        f"Task id: {task['task_id']}",
        f"Task source: {task['task_source']}",
        f"Task version: {task['task_version']}",
        f"Change version: {task['change_version']}",
        f"Allowed files/directories: {task['allowed_files']}",
        f"Forbidden files/directories: {task['forbidden_files']}",
        f"Required context files: {task['required_context']}",
        f"Expected output: {task['expected_output']}",
        "Acceptance criteria:\n- " + "\n- ".join(task["acceptance_criteria"]),
        "Acceptance evidence:\n- " + "\n- ".join(task["acceptance_evidence"]),
        f"Validation command: {task['validation_command']}",
        f"Failure policy: {task['failure_policy']}",
        "Execute each unique manifest validation argv once after the complete package source is frozen; "
        "each Task outcome references its hash-bound execution evidence, sharing evidence only for identical argv/source. "
        "Do not repeat delivery commands in review/catalog. ",
        "Required result fields: schema_version, status, work_package_id, task_ids, "
        "task_versions, change_versions, run_id, outcomes, changed_files, validation, "
        "acceptance_evidence, effect_checks, risks",
        f"Copy the run-bound starting shape tmp/qoder-tasks/{task.get('run_id', '<runner-run-id>')}/result.template.json "
        f"to tmp/qoder-tasks/{task.get('run_id', '<runner-run-id>')}/result.json, then replace its BLOCKED placeholders with factual results.",
        f"Required result file: tmp/qoder-tasks/{task.get('run_id', '<runner-run-id>')}/result.json",
        "Result schema: lexiflow.qoder-work-package-result.v1 with exact work_package_id, "
        "task_ids, task_versions, change_versions, run_id and one outcome per task_id. "
        "outcomes MUST be an array in task_ids order, NOT an object keyed by task_id; "
        "each item requires task_id, status (PASS/BLOCKED/FAIL), acceptance_evidence (array). "
        "changed_files, validation, acceptance_evidence, effect_checks and risks MUST be arrays. "
        "Write it before the final response; prose and exit code are not acceptance evidence.",
        "Before final response, validate result structure only (not delivery tests): "
        f"python3 -m scripts.agents.delegation.qoder_cli validate-result {task.get('run_id', '<runner-run-id>')}",
    ]
    if task.get("title"):
        lines.insert(0, f"Task title: {task['title']}")
    if task.get("agent_id"):
        lines.append(f"Agent id: {task['agent_id']}")
    if task.get("client"):
        lines.append(f"Client: {task['client']}")
    if task.get("session_id"):
        lines.append(f"Session id: {task['session_id']}")
    if task.get("run_id"):
        lines.append(f"Run id: {task['run_id']}")
    if task.get("parent_client"):
        lines.append(f"Parent client: {task['parent_client']}")
    if task.get("parent_session_id"):
        lines.append(f"Parent session id: {task['parent_session_id']}")
    return "\n".join(lines)


def _validate_prompt_budget(task: dict[str, Any]) -> str:
    """阻断会重复粘贴大段设计的 handoff；详细上下文应通过文件定位。"""
    prompt = _build_prompt(task)
    if len(prompt) > MAX_PROMPT_CHARACTERS:
        raise ValueError(
            f"Qoder prompt exceeds {MAX_PROMPT_CHARACTERS} characters; "
            "replace repeated design text with bounded file locators"
        )
    return prompt


def _build_qodercli_args(task: dict[str, Any], cwd: Path) -> list[str]:
    """构建 qodercli 命令行参数数组。新任务传 --session-id，恢复任务传 --resume。"""
    cli = _find_qoder_cli()
    prompt = _validate_prompt_budget(task)
    args = [
        str(cli),
        "-p",
        prompt,
        "--cwd",
        str(cwd),
        "--agent",
        task["agent_profile"],
        # 保留 CLI provider/model 配置；仅传 project 会悄然丢失自定义模型。
        "--setting-sources",
        "user,project,local",
    ]
    args.extend(["--permission-mode", DEFAULT_PERMISSION_MODE])
    # 委派入口只执行当前目标，不暴露递归调度工具；Bash 仍不构成目录沙箱。
    args.extend(["--disallowed-tools", "Agent"])
    args.extend(["--output-format", "json"])
    # 不设置隐式的多次模型尝试循环；父任务负责显式、有界恢复。
    args.extend(["--max-model-request-retries", "0"])
    session_id = task.get("session_id", "")
    if session_id:
        if task.get("_resume_mode"):
            args.extend(["--resume", session_id])
        else:
            args.extend(["--session-id", session_id])
    return args


from scripts.agents.qoder.callback import (  # noqa: E402
    _build_callback_message as _build_callback_message,
    _run_codex_queue_cli as _run_codex_queue_cli,
    attempt_codex_callback as _attempt_codex_callback,
)


def _build_completion(
    task: dict[str, Any], status: str, exit_code: int, **extra: Any
) -> dict[str, Any]:
    """从任务与结果信息构建完成记录字典。"""
    completion: dict[str, Any] = {
        "status": status,
        "exit_code": exit_code,
        "session_id": task.get("session_id", ""),
        "task_id": task.get("task_id", ""),
        "task_version": task.get("task_version", ""),
        "change_version": task.get("change_version", ""),
        "title": task.get("title", task.get("task_id", "")),
        "agent_id": task.get("agent_id", ""),
        "client": task.get("client", "qoder"),
        "parent_client": task.get("parent_client", ""),
        "parent_session_id": task.get("parent_session_id", ""),
        "work_package_id": task.get("work_package_id", ""),
        "task_ids": task.get("task_ids", []),
        "task_versions": task.get("task_versions", {}),
        "change_versions": task.get("change_versions", {}),
    }
    completion.update(extra)
    return completion


def _validate_structured_result(run_dir: Path, task: dict[str, Any]) -> dict[str, Any]:
    """复用公开且单一 owner 的事实校验器处理结构化结果。"""
    from scripts.agents.qoder.facts import validate_structured_result

    result, _descriptor = validate_structured_result(run_dir, task)
    return result


def cmd_validate_result(args: argparse.Namespace) -> None:
    """只读检查本 run 的结果结构；不签发收据、结束进程或确认验收。"""
    run_id = _validate_run_id(args.run_id)
    run_dir = _task_dir(_find_repo_root(Path.cwd())) / run_id
    _check_no_symlink_ancestors(run_dir)
    task = _safe_read_json(run_dir / "task.json")
    if task.get("run_id") != run_id:
        raise ValueError("Qoder task identity does not match the run directory")
    result = _validate_structured_result(run_dir, task)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "status": "PASS",
                "result_status": result["status"],
                "scope": "result-structure-only-not-task-acceptance",
            }
        )
    )


def _worker_entry(
    task_dir: Path, run_id: str, cwd: Path, lease_fd: int | None = None
) -> None:
    if lease_fd is None:
        with _host_lease() as fd:
            return _worker_entry(task_dir, run_id, cwd, fd)
    _verify_inherited_lease(lease_fd)
    _run_worker_with_lease(task_dir, run_id, cwd, lease_fd)


def _run_worker_with_lease(
    task_dir: Path, run_id: str, cwd: Path, lease_fd: int
) -> None:
    """Worker 入口：从 task.json 读取任务，Popen 启动 CLI，记录启动证据后 wait 获取真实退出码。

    started.json 在 qodercli 成功 Popen 后写入真实子进程 PID，作为真正启动证据。
    spawn 失败不写 started.json；metadata 写失败不改变真实退出码。
    """
    run_dir = task_dir / run_id
    record_path = run_dir / "completion.json"
    stdout_log = run_dir / "stdout.log"
    stderr_log = run_dir / "stderr.log"
    task_file = run_dir / "task.json"
    task: dict[str, Any] = {}
    completion: dict[str, Any] = {}
    proc: subprocess.Popen | None = None
    started_at: float | None = None
    lifecycle_errors: list[str] = []
    try:
        task = _validate_task(_safe_read_json(task_file), runtime_bound=True)
        if stdout_log.is_symlink() or stderr_log.is_symlink():
            raise ValueError("log path is a symlink")
        qoder_args = _build_qodercli_args(task, cwd)
        with (
            stdout_log.open("w", encoding="utf-8") as out,
            stderr_log.open("w", encoding="utf-8") as err,
        ):
            # Popen 成功才说明真正启动了子进程
            proc = subprocess.Popen(
                qoder_args,
                pass_fds=(lease_fd,),
                stdout=out,
                stderr=err,
                cwd=str(cwd),
            )
        started_at = time.time()
        # 真实 Popen 只清除配对的派发连续失败计数，不构成验收。
        try:
            _fallback.mark_qoder_started(cwd, task, run_id)
        except _fallback.DispatchFallbackError as exc:
            lifecycle_errors.append(f"dispatch state: {exc.code}")
        # 元数据失败与 CLI 执行分离，始终等待已创建的子进程并保留真实退出码。
        try:
            _lifecycle.record_started(run_dir, proc.pid, task.get("session_id", ""))
            _complete_resume_start(run_dir, task)
        except (OSError, ValueError) as exc:
            lifecycle_errors.append(f"startup metadata: {exc}")
        # 等待真实退出码
        exit_code = proc.wait()
        finished_at = time.time()
        status = "finished" if exit_code == 0 else "failed"
        result_error = ""
        if exit_code == 0:
            try:
                _validate_structured_result(run_dir, task)
            except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
                status = "failed"
                result_error = str(exc)
        completion = _build_completion(
            task,
            status,
            exit_code,
            qoder_exit_code=exit_code,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=round(finished_at - started_at, 3),
            stdout_log=str(stdout_log),
            stderr_log=str(stderr_log),
            run_id=run_id,
        )
        if result_error:
            completion["result_error"] = result_error
    except FileNotFoundError as exc:
        # CLI 缺失 - 不写 started.json
        exit_code = 127
        completion = _build_completion(
            task, "failed", exit_code, error=str(exc), run_id=run_id
        )
    except Exception as exc:
        exit_code = proc.wait() if proc is not None else 1
        completion = _build_completion(
            task,
            "finished" if exit_code == 0 else "failed",
            exit_code,
            error=str(exc),
            run_id=run_id,
        )

    if proc is None:
        try:
            _release_resume_reservation(run_dir)
        except (OSError, ValueError) as exc:
            lifecycle_errors.append(f"reservation cleanup: {exc}")
        completion["qoder_dispatch_outcome"] = "not-started"
    if lifecycle_errors:
        completion["lifecycle_errors"] = lifecycle_errors
    if completion.get("status") == "failed":
        completion["failure"] = summarize_cli_failure(
            stdout_log,
            completion["exit_code"],
            pid=proc.pid if proc is not None else None,
            session_id=task.get("session_id", ""),
            cwd=cwd,
            started_at=started_at,
        )
    # 仅未发生 Popen 的失败属于派发失败；CLI 启动后的失败是执行证据。
    if (
        proc is None
        and completion.get("status") == "failed"
        and task.get("run_id") == run_id
    ):
        try:
            completion["fallback"] = _fallback.mark_qoder_unavailable(
                cwd, task, run_id, "qoder-cli-not-started"
            )
        except _fallback.DispatchFallbackError:
            # 保留原始终态事实；协调器故障不能虚构 fallback。
            pass
    # 无持久化完成记录时不发正常完成通知；watchdog 将诊断缺失结果。
    _atomic_write_json(record_path, completion)

    if task.get("parent_client") == "codex":
        parent_sid = task.get("parent_session_id", "")
        if parent_sid and _is_valid_uuid(parent_sid):
            _attempt_codex_callback(run_dir, completion, task, cwd)


def _complete_resume_start(run_dir: Path, task: dict[str, Any]) -> None:
    """只有真实 Qoder 已启动才建立替代链，慢启动也由 worker 最终确认。"""
    previous = _lifecycle.read_lifecycle(run_dir).get("previous_run_id")
    if not previous:
        return
    old_dir = run_dir.parent / _validate_run_id(previous)
    old_task = _safe_read_json(old_dir / "task.json")
    for key in ("client", "session_id", "parent_client", "parent_session_id"):
        if old_task.get(key) != task.get(key):
            raise ValueError(f"resume identity mismatch: {key}")
    reservation = _safe_read_json(old_dir / "resume.reservation")
    if reservation.get("new_run_id") != run_dir.name:
        raise ValueError("resume reservation mismatch")
    _lifecycle.record_supersession(old_dir, run_dir.name)


def _release_resume_reservation(run_dir: Path) -> None:
    """仅在确认未创建 Qoder 时释放属于本 run 的恢复占位。"""
    previous = _lifecycle.read_lifecycle(run_dir).get("previous_run_id")
    if not previous:
        return
    old_dir = run_dir.parent / _validate_run_id(previous)
    with _lifecycle._run_lock(old_dir):
        path = old_dir / "resume.reservation"
        if path.exists() and _safe_read_json(path).get("new_run_id") == run_dir.name:
            path.unlink()


def _reject_parent_polling(run_dir: Path) -> None:
    if (run_dir / "continuation.json").exists() and not (
        run_dir / "completion.json"
    ).exists():
        print(
            json.dumps(
                {
                    "run_id": run_dir.name,
                    "state": "AWAITING_CALLBACK",
                    "startup_handshake": "STARTING",
                    "next_action": "end-current-turn-await-callback",
                    "continuation": str(run_dir / "continuation.json"),
                    "started_confirmed": False,
                    "llm_polling": "forbidden",
                }
            )
        )
        raise SystemExit(3)


def cmd_status(args: argparse.Namespace) -> None:
    """只读一次协调后的记录快照；此命令不转为 watch。"""
    repo_root = _find_repo_root(Path.cwd())
    task_dir = _task_dir(repo_root)
    run_id = _validate_run_id(args.run_id)
    run_dir = task_dir / run_id
    _check_no_symlink_ancestors(run_dir)
    if not run_dir.exists():
        print(f"error: unknown run id: {run_id}", file=sys.stderr)
        sys.exit(1)
    status, detail = _status_snapshot(run_dir)
    extra: dict[str, Any] = {}
    try:
        watchdog = _lifecycle.read_watchdog_status(run_dir)
        if watchdog:
            extra["watchdog"] = watchdog
        if _lifecycle.ack_path(run_dir).exists():
            extra["ack"] = _safe_read_json(_lifecycle.ack_path(run_dir))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "status": "conflict",
                    "reason": f"status-side-record-invalid:{type(exc).__name__}",
                }
            )
        )
        return
    if status == "conflict":
        print(json.dumps({"run_id": run_id, "status": "conflict", **detail, **extra}))
        return
    data = {"run_id": run_id, "status": status, **extra}
    for field in ("startup", "startup_reason", "continuation"):
        if field in detail:
            data[field] = detail[field]
    if detail.get("lifecycle"):
        data["lifecycle"] = detail["lifecycle"].get("status", "")
    print(json.dumps(data))


def cmd_result(args: argparse.Namespace) -> None:
    """获取任务结果及报告、日志路径，不读取日志内容。"""
    repo_root = _find_repo_root(Path.cwd())
    task_dir = _task_dir(repo_root)
    run_id = _validate_run_id(args.run_id)
    run_dir = task_dir / run_id
    _check_no_symlink_ancestors(run_dir)

    if not run_dir.exists():
        print(f"error: unknown run id: {run_id}", file=sys.stderr)
        sys.exit(1)
    _reject_parent_polling(run_dir)

    record = run_dir / "completion.json"

    # 生命周期和异常信息（即使无 completion 也展示）
    lifecycle = _lifecycle.read_lifecycle(run_dir)
    watchdog_status = _lifecycle.read_watchdog_status(run_dir)

    # ack 信息
    ack_file = _lifecycle.ack_path(run_dir)
    ack_data = _safe_read_json(ack_file) if ack_file.exists() else None

    if not record.exists():
        result: dict[str, Any] = {"run_id": run_id, "status": "not_ready"}
        if lifecycle:
            result["lifecycle"] = lifecycle
        if watchdog_status:
            result["watchdog"] = watchdog_status
        if ack_data:
            result["ack"] = ack_data
        print(json.dumps(result, ensure_ascii=False))
        return

    data = _safe_read_json(record)
    data["run_id"] = run_id
    data["task_file"] = str(run_dir / "task.json")
    data["stdout_log"] = str(run_dir / "stdout.log")
    data["stderr_log"] = str(run_dir / "stderr.log")
    result_path = run_dir / "result.json"
    if result_path.exists():
        structured = _safe_read_json(result_path)
        data["result_file"] = str(result_path)
        data["result_status"] = structured.get("status", "invalid")

    callback_path = run_dir / "callback.json"
    if callback_path.exists():
        data["callback"] = _safe_read_json(callback_path)
    elif _lifecycle.callback_claim_path(run_dir).exists():
        data["callback"] = {"status": "unknown", "error": "已认领回调，但无可确认回执"}

    # 生命周期信息（只读）
    if lifecycle:
        data["lifecycle"] = lifecycle

    # watchdog 状态
    if watchdog_status:
        data["watchdog"] = watchdog_status

    # ack 信息
    if ack_data:
        data["ack"] = ack_data

    print(json.dumps(data, ensure_ascii=False))


def cmd_record_fallback(args: argparse.Namespace) -> None:
    """仅凭 call id 消费当前父任务的原生 Codex fallback spawn 结果。"""
    repo_root = _find_repo_root(Path.cwd())
    task_path = Path(args.task)
    _check_no_symlink_ancestors(task_path)
    task = _validate_task(json.loads(task_path.read_text(encoding="utf-8")))
    result = _fallback.record_terra_fallback(
        repo_root, task, args.attempt_id, args.call_id
    )
    print(json.dumps(result, ensure_ascii=False))
    if result.get("status") != "PASS":
        raise SystemExit(3)


def cmd_ack(args: argparse.Namespace) -> None:
    """显式消费确认 run 完成。幂等，需要精确父身份核对。"""
    repo_root = _find_repo_root(Path.cwd())
    task_dir = _task_dir(repo_root)

    try:
        parent_sid = discover_codex_runtime(repo_root).context["parent_session_id"]
        ack_data = _lifecycle.ack_run(
            task_dir,
            args.run_id,
            parent_sid,
            codex_thread_id=parent_sid,
        )
        print(json.dumps(ack_data, ensure_ascii=False))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def _dispatch_worker(argv: list[str]) -> int:
    """接收任务目录、运行标识、工作目录并执行后台子任务。"""
    if len(argv) != 5:
        print(
            "usage: _worker <task_dir> <run_id> <cwd> <host-lease-fd>", file=sys.stderr
        )
        return 1
    task_dir = Path(argv[1])
    run_id = argv[2]
    cwd = Path(argv[3])
    _validate_run_id(run_id)
    _check_no_symlink_ancestors(task_dir)
    run_dir = task_dir / run_id
    _check_no_symlink_ancestors(run_dir)
    try:
        task_dir.resolve().relative_to(cwd.resolve())
        run_dir.resolve().relative_to(cwd.resolve())
    except ValueError:
        print("error: task_dir/run_dir must be inside cwd", file=sys.stderr)
        return 1
    old_umask = os.umask(0o077)
    try:
        pid_file = run_dir / "worker.pid"
        if pid_file.is_symlink():
            raise ValueError("pid path is a symlink")
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
        # 注意：started.json 在 qodercli 成功 Popen 后由 _worker_entry 写入
        lease_fd = int(argv[4])
        _verify_inherited_lease(lease_fd)
        _worker_entry(task_dir, run_id, cwd, lease_fd)
    finally:
        os.umask(old_umask)
    return 0


def main(argv: list[str] | None = None) -> int:
    """主入口，解析子命令并执行。"""
    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] == "_worker":
        try:
            return _dispatch_worker(argv)
        except (ValueError, FileNotFoundError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if argv and argv[0] == "_watchdog":
        try:
            return _lifecycle.watchdog_entry(argv[1], argv[2], argv[3])
        except (ValueError, FileNotFoundError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    # 固定拒绝在 parser、仓库和 policy 读取前执行，不保留启动专用参数。
    if argv and argv[0] in {"start", "resume", "preflight"}:
        print(
            f"error: Qoder {argv[0]} is disabled; use native Codex collaboration",
            file=sys.stderr,
        )
        return 2

    args = parse_command(
        argv,
        {
            "status": cmd_status,
            "result": cmd_result,
            "validate-result": cmd_validate_result,
            "record-fallback": cmd_record_fallback,
            "ack": cmd_ack,
        },
    )
    try:
        args.func(args)
    except (ValueError, FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in {"_worker", "_watchdog"}:
        print("use python3 -m scripts.agents.delegation.qoder_cli", file=sys.stderr)
        sys.exit(2)
    sys.exit(main())
