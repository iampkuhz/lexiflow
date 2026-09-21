"""Qoder callback transport, independent of the lifecycle command entry."""
from __future__ import annotations

import errno
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from scripts.agents.qoder import lifecycle
from scripts.agents.qoder.failure_diagnostics import access_blocked, compact_failure_signal

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_RECEIPT_RE = re.compile(r"^Queued message ([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}) for thread ([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.$")

def _is_valid_uuid(value: str) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.fullmatch(value))

def _run_codex_queue_cli(
    thread_id: str, message: str, timeout: int = 10, cwd: str = "."
) -> subprocess.CompletedProcess[str]:
    """调用 codex queue CLI，参数数组传递，显式 cwd 与有限超时。"""
    return subprocess.run(
        ["codex", "queue", "--thread", thread_id, "--message", message],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=cwd,
    )


def _build_callback_message(run_id: str, completion: dict[str, Any], run_dir: Path) -> str:
    """构建回调消息，仅含受校验字段与固定指令，不含可注入文本或原始日志。"""
    task_id = completion.get("task_id", "")
    work_package_id = completion.get("work_package_id", "")
    task_ids = completion.get("task_ids", [])
    status = completion.get("status", "unknown")
    exit_code = completion.get("exit_code", "")
    lines = [
        f"[qoder-callback] run_id={run_id} work_package_id={work_package_id} task_id={task_id}",
        f"task_ids: {json.dumps(task_ids)}",
        f"status: {status} exit_code: {exit_code}",
        f"结果目录: {run_dir}",
        "请核对 completion 身份和版本、必要 diff 与已有验证证据；不要无条件重跑交付命令。",
        "已 ack 或 superseded 时直接结束。日志不进入回调。queued 不算任务通过。",
    ]
    signal = compact_failure_signal(completion.get("failure"))
    if signal:
        lines.insert(4, signal)
    if access_blocked(completion.get("failure")):
        lines.insert(-1, "账号/访问阻断不得原样补开；先确认外部状态已修复，再显式恢复。无新产物不得计交付通过。")
    fallback = completion.get("fallback")
    if isinstance(fallback, dict) and fallback.get("code") == "TERRA_REQUIRED":
        lines.insert(-1, "Qoder 未形成可验证启动；按 attempt_id 使用原生 Codex Terra/high 派发，随后用 record-fallback 只提交该原生 call_id。")
    return "\n".join(lines)


def _callback_claim_path(run_dir: Path) -> Path:
    """返回回调认领文件路径。"""
    return run_dir / "callback.claim"


def is_run_consumed_or_superseded(run_dir: Path) -> bool:
    """检查 run 是否已被消费或替代。"""
    # 检查 ack.json（权威 marker）
    ack_file = lifecycle.ack_path(run_dir)
    if ack_file.exists():
        return True
    # 检查 lifecycle 状态
    state = lifecycle.read_lifecycle(run_dir)
    status = state.get("status", "")
    return status in ("acknowledged", "superseded")


def attempt_codex_callback(
    run_dir: Path,
    run_id: str,
    completion: dict[str, Any],
    task: dict[str, Any],
    cwd: Path,
) -> None:
    """尝试向 Codex 父会话发送回调通知。

    写入 callback.json，状态为 queued/failed/unknown。
    不重试，不覆盖已确认 queued。claim 文件保证同一 run 至多一次尝试。
    超时或回执不可验证时状态为 unknown，不伪造 queued。
    已 ack 或 superseded 的 run 不发送。
    """
    if task.get("parent_client") != "codex":
        return
    parent_session_id = task.get("parent_session_id", "")
    if not parent_session_id or not _is_valid_uuid(parent_session_id):
        return

    # 检查是否已被消费或替代 - 在 claim 前检查
    if is_run_consumed_or_superseded(run_dir):
        return

    callback_path = run_dir / "callback.json"
    if callback_path.exists():
        return

    claim_path = lifecycle.callback_claim_path(run_dir)
    try:
        fd = os.open(str(claim_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return
        lifecycle._atomic_write_json(
            callback_path,
            {
                "status": "unknown",
                "error": f"claim failed: {exc}",
                "parent_session_id": parent_session_id,
            },
            mode=0o600,
        )
        return

    # claim 后 queue 前再次检查 - 防止竞态
    if is_run_consumed_or_superseded(run_dir):
        return

    message = _build_callback_message(run_id, completion, run_dir)
    returned = False
    try:
        result = _run_codex_queue_cli(parent_session_id, message, cwd=str(cwd))
        returned = True
        if result.returncode == 0:
            msg_id = ""
            thread_verified = False
            for line in result.stdout.splitlines():
                m = _RECEIPT_RE.fullmatch(line.strip())
                if m:
                    msg_id = m.group(1)
                    thread_verified = m.group(2).lower() == parent_session_id.lower()
                    break
            if msg_id and thread_verified:
                lifecycle._atomic_write_json(
                    callback_path,
                    {
                        "status": "queued",
                        "message_id": msg_id,
                        "parent_session_id": parent_session_id,
                    },
                    mode=0o600,
                )
            else:
                lifecycle._atomic_write_json(
                    callback_path,
                    {
                        "status": "unknown",
                        "error": "exit 0 but no verifiable receipt",
                        "parent_session_id": parent_session_id,
                    },
                    mode=0o600,
                )
        else:
            lifecycle._atomic_write_json(
                callback_path,
                {
                    "status": "failed",
                    "exit_code": result.returncode,
                    "parent_session_id": parent_session_id,
                },
                mode=0o600,
            )
    except subprocess.TimeoutExpired:
        lifecycle._atomic_write_json(
            callback_path,
            {
                "status": "unknown",
                "error": "回调超时，状态不确定",
                "parent_session_id": parent_session_id,
            },
            mode=0o600,
        )
    except Exception as exc:
        lifecycle._atomic_write_json(
            callback_path,
            {
                "status": "failed"
                if isinstance(exc, FileNotFoundError) and not returned
                else "unknown",
                "error": type(exc).__name__,
                "parent_session_id": parent_session_id,
            },
            mode=0o600,
        )
