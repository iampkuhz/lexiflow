"""封闭 Module Check 的执行内核，负责运行进程并产生报告与证据。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable

REPORT_SCHEMA = "lexiflow.verification-report.v1"
_ALLOWED_ENV_KEYS = frozenset(
    {
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LANGUAGE",
        "TERM",
        "TZ",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "PATH",
    }
)
_SECRET_PREFIXES = ("CODEX", "QODER", "CLAUDE", "ANTHROPIC", "OPENAI", "LEXIFLOW_GATE_")
_SECRET_SUBSTRINGS = (
    "API_KEY",
    "API_TOKEN",
    "API_SECRET",
    "SECRET_KEY",
    "CREDENTIAL",
    "PRIVATE_KEY",
    "ACCESS_TOKEN",
    "AUTH_TOKEN",
)
_TERM_GRACE = 0.5


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def fingerprint_json(value: Any) -> str:
    return sha256_bytes(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def build_child_environment(
    override: dict[str, str] | None = None,
    runtime_environment: dict[str, str] | None = None,
) -> dict[str, str]:
    """以安全基线和已预检的不透明运行值构建子进程环境；不把秘密写入报告。"""

    source = dict(os.environ)
    if override:
        source.update(override)
    env: dict[str, str] = {}
    for key, value in source.items():
        if (
            key not in _ALLOWED_ENV_KEYS
            or any(key.startswith(prefix) for prefix in _SECRET_PREFIXES)
            or any(part in key for part in _SECRET_SUBSTRINGS)
        ):
            continue
        env[key] = value
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("PYTHONHASHSEED", "0")
    # environment 模块校验并限制这些值；内核仅将其作为不透明值传入子进程，
    # 绝不把子进程环境序列化到报告。
    if runtime_environment:
        env.update(runtime_environment)
    return env


def verify_input_descriptors(
    root: Path, descriptors: list[dict[str, str]]
) -> list[dict[str, str]]:
    """仅在调用者显式提供时验证不可变输入描述符。"""
    results: list[dict[str, str]] = []
    for descriptor in descriptors:
        locator, expected = descriptor.get("locator", ""), descriptor.get("sha256", "")
        try:
            actual = sha256_bytes((root / locator).read_bytes())
            status = "verified" if expected and actual == expected else "drift"
        except FileNotFoundError:
            actual, status = "", "missing"
        except OSError as exc:
            actual, status = "", f"error:{exc}"
        results.append(
            {
                "locator": locator,
                "expected_sha256": expected,
                "actual_sha256": actual,
                "status": status,
            }
        )
    return results


def all_descriptors_verified(results: list[dict[str, str]]) -> bool:
    return all(item["status"] == "verified" for item in results)


def _safe_relative(locator: str) -> bool:
    return (
        bool(locator)
        and not locator.startswith("/")
        and ".." not in PurePosixPath(locator).parts
    )


def _source_input_file(path: Path) -> bool:
    """从声明的源码目录中排除可重建的本地产物。"""
    generated_parts = {"__pycache__", ".gradle", "build", "node_modules"}
    return path.suffix != ".pyc" and not generated_parts.intersection(path.parts)


def snapshot_check_inputs(root: Path, check: dict[str, Any]) -> dict[str, Any]:
    """对实际声明的输入文件和完整不可变 Check 配置取哈希。"""
    files: list[dict[str, str]] = []
    missing: list[str] = []
    for locator in sorted(set(check.get("input_paths", []))):
        if not _safe_relative(locator):
            missing.append(locator)
            continue
        target = root / locator
        if target.is_file():
            files.append(
                {"locator": locator, "sha256": sha256_bytes(target.read_bytes())}
            )
        elif target.is_dir():
            for path in sorted(
                p for p in target.rglob("*") if p.is_file() and _source_input_file(p)
            ):
                rel = path.relative_to(root).as_posix()
                files.append(
                    {"locator": rel, "sha256": sha256_bytes(path.read_bytes())}
                )
        else:
            missing.append(locator)
    value = {"check_config": check, "files": files, "missing": missing}
    return {"fingerprint": fingerprint_json(value), "files": files, "missing": missing}


def _terminate_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return
    deadline = time.monotonic() + _TERM_GRACE
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def run_check_process(
    argv: list[str],
    cwd: str,
    env: dict[str, str],
    timeout_seconds: int,
    executable: str | None = None,
) -> dict[str, Any]:
    """只运行声明的 argv；唯有声明为 python3 时可使用当前 venv 解释器。"""
    executed = list(argv)
    if executable == "python3" and argv[0] == "python3":
        executed[0] = sys.executable
    started_at, started = _utc_now(), time.monotonic()
    try:
        proc = subprocess.Popen(
            executed,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            shell=False,
        )
    except OSError as exc:
        return {
            "status": "FAIL",
            "exit_code": None,
            "exit_reason": "spawn-error",
            "stdout": "",
            "stderr": str(exc),
            "duration_seconds": 0.0,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "timed_out": False,
            "executable": executed[0],
            "executed_argv": executed,
        }
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
        timed_out, reason = False, "exited"
    except subprocess.TimeoutExpired:
        _terminate_group(proc.pid)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            proc.kill()
            stdout, stderr = b"", b""
        timed_out, reason = True, "timeout"
    return {
        "status": "FAIL" if timed_out or proc.returncode != 0 else "PASS",
        "exit_code": proc.returncode,
        "exit_reason": reason,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "duration_seconds": round(time.monotonic() - started, 6),
        "started_at": started_at,
        "finished_at": _utc_now(),
        "timed_out": timed_out,
        "executable": executed[0],
        "executed_argv": executed,
    }


def _write_output_artifacts(
    root: Path, run_id: str, check_id: str, process: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", check_id)
    directory = root / "tmp" / "quality" / "verification" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, dict[str, Any]] = {}
    for kind in ("stdout", "stderr"):
        path = directory / f"{safe_id}.{kind}.log"
        data = str(process.get(kind, "")).encode("utf-8")
        path.write_bytes(data)
        artifacts[kind] = {
            "locator": path.relative_to(root).as_posix(),
            "sha256": sha256_bytes(data),
            "bytes": len(data),
        }
    return artifacts


def _evaluate_result_contract(
    contract: dict[str, Any], process: dict[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    """把进程退出、结构化结果与声明的完整性条件合并为 Check 状态。缺必需证据时不能因退出码为零而返回 PASS；保留原始 BLOCKED 或 FAIL 诊断。"""
    if process.get("timed_out"):
        return "FAIL", "timeout", {}
    if process.get("exit_reason") == "spawn-error":
        return "FAIL", "spawn-error", {}
    if process.get("exit_code") != 0:
        return "FAIL", "non-zero-exit", {}
    if contract["type"] == "exit-code":
        return (
            "PASS",
            "",
            {
                "kind": "exit-code",
                "completeness_guarantee": contract["completeness_guarantee"],
            },
        )
    try:
        report = json.loads(str(process.get("stdout", "")).strip())
    except json.JSONDecodeError:
        return "FAIL", "result-report-invalid", {}
    if not isinstance(report, dict):
        return "FAIL", "result-report-invalid", {}
    missing = [field for field in contract["required_fields"] if field not in report]
    if missing:
        return "FAIL", "result-report-missing-fields", {"missing_fields": missing}
    reported_status = report.get("status")
    if reported_status not in {"PASS", "BLOCKED", "FAIL"}:
        return "FAIL", "result-report-invalid-status", {"report": report}
    if reported_status not in contract["allowed_statuses"]:
        return "FAIL", "result-status-unexpected", {"report": report}
    # 完整性阈值只约束声称 PASS 的结果。即使执行的 Check 数为零，模块仍可
    # 返回有类型的 BLOCKED/FAIL；不得提升或掩盖原始诊断。
    if reported_status != "PASS":
        return reported_status, report.get("reason", ""), {"report": report}
    for field, minimum in contract.get("minimum", {}).items():
        value = report.get(field)
        if not isinstance(value, int) or value < minimum:
            return (
                "FAIL",
                "result-report-incomplete",
                {"report": report, "field": field},
            )
    for field, expected in contract.get("equals", {}).items():
        if report.get(field) != expected:
            return (
                "FAIL",
                "result-report-incomplete",
                {"report": report, "field": field},
            )
    return reported_status, report.get("reason", ""), {"report": report}


def _not_run_result(
    check: dict[str, Any], reason: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "check_id": check["check_id"],
        "module": check.get("module", "unknown"),
        "status": "FAIL",
        "reason": reason,
        "process": {
            "executed_argv": [],
            "exit_code": None,
            "exit_reason": "not-run",
            "duration_seconds": 0,
            "timed_out": False,
            "started_at": "",
            "finished_at": "",
            "output_artifacts": {},
        },
        "input_snapshot": {"pre": {}, "post": {}},
        **(extra or {}),
    }


def execute_single_check(
    check: dict[str, Any],
    repo_root: Path,
    env: dict[str, str],
    runner: Callable[..., dict[str, Any]] | None = None,
    run_id: str | None = None,
    frozen_pre: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """为单个声明的 Check 建立输入快照与受控子进程环境，执行固定 argv 并收集有界证据。返回本次真实执行结果；异常和缺失证据不得被推断为 PASS。"""
    repo_root = repo_root.resolve()
    runner = run_check_process if runner is None else runner
    observed_before_execution = snapshot_check_inputs(repo_root, check)
    pre = frozen_pre or observed_before_execution
    pre_descriptors = verify_input_descriptors(
        repo_root, check.get("consumed_inputs", [])
    )
    if pre["missing"]:
        return _not_run_result(
            check,
            "input-missing",
            {
                "input_snapshot": {
                    "pre": pre,
                    "before_execution": observed_before_execution,
                    "post": {},
                },
                "consumed_inputs": {"pre": pre_descriptors, "post": []},
            },
        )
    if pre["fingerprint"] != observed_before_execution["fingerprint"]:
        return _not_run_result(
            check,
            "input-drift",
            {
                "input_snapshot": {
                    "pre": pre,
                    "before_execution": observed_before_execution,
                    "post": {},
                },
                "consumed_inputs": {"pre": pre_descriptors, "post": []},
            },
        )
    if pre_descriptors and not all_descriptors_verified(pre_descriptors):
        return _not_run_result(
            check,
            "input-drift",
            {
                "input_snapshot": {
                    "pre": pre,
                    "before_execution": observed_before_execution,
                    "post": {},
                },
                "consumed_inputs": {"pre": pre_descriptors, "post": []},
            },
        )
    cwd_rel = check["cwd"]
    cwd_path = (repo_root / cwd_rel).resolve()
    if repo_root not in (cwd_path, *cwd_path.parents) or not cwd_path.is_dir():
        return _not_run_result(
            check,
            "invalid-working-directory",
            {
                "input_snapshot": {
                    "pre": pre,
                    "before_execution": observed_before_execution,
                    "post": {},
                }
            },
        )
    process = runner(
        list(check["command"]),
        str(cwd_path),
        env,
        check["timeout_seconds"],
        check.get("executable"),
    )
    artifacts = _write_output_artifacts(
        repo_root, run_id or str(uuid.uuid4()), check["check_id"], process
    )
    post = snapshot_check_inputs(repo_root, check)
    post_descriptors = verify_input_descriptors(
        repo_root, check.get("consumed_inputs", [])
    )
    status, reason, completeness = _evaluate_result_contract(
        check["result_contract"], process
    )
    if pre["fingerprint"] != post["fingerprint"] or (
        post_descriptors and not all_descriptors_verified(post_descriptors)
    ):
        status, reason = "FAIL", "input-drift"
    return {
        "check_id": check["check_id"],
        "module": check["module"],
        "status": status,
        "reason": reason,
        "process": {
            "executed_argv": process.get("executed_argv")
            or [process.get("executable", ""), *check["command"][1:]],
            "exit_code": process.get("exit_code"),
            "exit_reason": process.get("exit_reason", ""),
            "duration_seconds": process.get("duration_seconds", 0),
            "timed_out": process.get("timed_out", False),
            "started_at": process.get("started_at", ""),
            "finished_at": process.get("finished_at", ""),
            "output_artifacts": artifacts,
        },
        "input_snapshot": {
            "pre": pre,
            "before_execution": observed_before_execution,
            "post": post,
        },
        "consumed_inputs": {"pre": pre_descriptors, "post": post_descriptors},
        "result_contract": completeness,
    }


def aggregate_results(results: list[dict[str, Any]]) -> tuple[str, str]:
    if not results:
        return "FAIL", "no-checks-executed"
    failed = next(
        (
            result.get("reason") or "check-failed"
            for result in results
            if result.get("status") not in {"PASS", "BLOCKED"}
        ),
        "",
    )
    if failed:
        return "FAIL", failed
    blocked = next(
        (
            result.get("reason") or "check-blocked"
            for result in results
            if result.get("status") == "BLOCKED"
        ),
        "",
    )
    return ("BLOCKED", blocked) if blocked else ("PASS", "")


def compute_coverage_gap(
    declared: list[dict[str, Any]], executed_ids: set[str]
) -> list[str]:
    return [
        check["check_id"] for check in declared if check["check_id"] not in executed_ids
    ]
