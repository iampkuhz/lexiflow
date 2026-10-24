"""消费Verify冻结来源并执行合成发布runtime生命周期验收。"""

from __future__ import annotations

import contextvars
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ops.release.host_resources import (
    release_docker_preflight as release_docker_preflight,
)
from ops.release.host_resources import release_process_control as release_process
from scripts.checks.release.source.frozen_source_fixture import (
    BridgeError,
    consume_verify_snapshot,
)
from scripts.verification.execution.check_runner import (
    RELEASE_RUNTIME_CHECK_IDS,
    VERIFY_CHILD_INPUT_SCHEMA,
    decode_child_request,
    snapshot_check_inputs,
)

MAX_ENVELOPE = 1_048_576
CHECK_IDS = RELEASE_RUNTIME_CHECK_IDS
ENVELOPE_FIELDS = frozenset({"schema_version", "run_id", "check_id", "snapshot"})
SNAPSHOT_FIELDS = frozenset({"fingerprint", "files", "missing"})
_PROGRESS: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar(
    "release_runtime_progress", default=None
)
RELEASE_PLATFORM = "linux/arm64"


def _require_supported_host() -> None:
    """只允许当前合同支持的 Apple Silicon macOS 宿主。"""
    if sys.platform != "darwin" or os.uname().machine not in {"arm64", "aarch64"}:
        raise ConsumerError("BLOCKED", "release-host-platform-unsupported")


def mark_completed() -> None:
    """记录一个真实成功完成的runtime命令阶段。"""
    progress = _PROGRESS.get()
    if progress is not None:
        progress[0] += 1


class ConsumerError(RuntimeError):
    """固定状态和安全错误码。"""

    def __init__(self, status: str, reason: str) -> None:
        self.status, self.reason = status, reason
        progress = _PROGRESS.get()
        self.checks_run = progress[0] if progress is not None else 0
        super().__init__(f"{status}/{reason}")


def _reject_constant(value: str) -> None:
    """拒绝JSON标准之外的NaN与Infinity。"""
    raise ValueError(value)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-key")
        result[key] = value
    return result


def _read_envelope(
    stream: Any, *, check_ids: frozenset[str] = CHECK_IDS
) -> dict[str, Any]:
    raw = stream.buffer.read(MAX_ENVELOPE + 1)
    if not raw:
        raise ConsumerError("BLOCKED", "verify-context-unavailable")
    if len(raw) > MAX_ENVELOPE:
        raise ConsumerError("FAIL", "verify-context-invalid")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ConsumerError("FAIL", "verify-context-invalid") from None
    if (
        not isinstance(value, dict)
        or set(value) != ENVELOPE_FIELDS
        or value.get("schema_version") != VERIFY_CHILD_INPUT_SCHEMA
        or type(value.get("run_id")) is not str
        or not value["run_id"].strip()
        or value.get("check_id") not in check_ids
        or not isinstance(value.get("snapshot"), dict)
        or set(value["snapshot"]) != SNAPSHOT_FIELDS
    ):
        raise ConsumerError("FAIL", "verify-context-invalid")
    if value["snapshot"].get("missing"):
        raise ConsumerError("FAIL", "verify-snapshot-mismatch")
    return value


def _check_environment(repo: Path, environ: Mapping[str, str]) -> list[str]:
    missing = []
    for executable in ("git", "node", "npm", "docker"):
        if not shutil.which(executable, path=environ.get("PATH")):
            missing.append(executable)
    java_home = environ.get("JAVA_HOME", "")
    java = (
        Path(java_home) / "bin/java"
        if java_home
        else Path(shutil.which("java", path=environ.get("PATH")) or "/nonexistent")
    )
    if not java.is_file():
        missing.append("java-25")
    else:
        try:
            _stdout, version_bytes = _run_bounded(
                [str(java), "-version"],
                repo,
                {"PATH": environ.get("PATH", "")},
                5,
                limit=4096,
            )
            version = version_bytes.decode("utf-8", "replace")
            if 'version "25' not in version:
                missing.append("java-25")
        except (OSError, subprocess.SubprocessError):
            missing.append("java-25")
    if not (repo / "backend/gradlew").is_file():
        missing.append("gradle-wrapper")
    for name in ("LEXIFLOW_RELEASE_GRADLE_CACHE", "LEXIFLOW_RELEASE_NPM_CACHE"):
        value = environ.get(name, "")
        if not value or not Path(value).is_absolute() or not Path(value).is_dir():
            missing.append(name.lower().replace("_", "-"))
    if not environ.get("LEXIFLOW_POSTGRES_TEST_JDBC_URL", "").strip():
        missing.append("isolated-postgres-test-jdbc-url")
    return missing


def _binding(env: Mapping[str, str]) -> Any:
    """绑定唯一 ARM64 daemon，不接受平台选择。"""
    _require_supported_host()
    key = "LEXIFLOW_RELEASE_ARM64_DOCKER_HOST"
    endpoint = env.get(key, "").strip()
    if not endpoint:
        raise ConsumerError("BLOCKED", "missing-lexiflow-release-arm64-docker-host")
    if not endpoint.startswith("unix://"):
        raise ConsumerError("FAIL", "release-docker-endpoint-invalid")
    info = release_docker_preflight.preflight(
        {"DOCKER_HOST": endpoint, "PATH": env.get("PATH", "")}
    )
    if info.status != "PASS" or info.binding is None:
        raise ConsumerError(info.status, info.reason)
    return info.binding


def run_consumer(
    repo_root: str | Path,
    envelope: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """消费原始Verify快照并串联构件、候选与安装生命周期。"""
    release_process._install_termination_handler()
    from scripts.checks.release.source.release_installation_checks import (
        execute_lifecycle,
    )
    from scripts.checks.release.source.release_test_artifact_builder import (
        produce_candidates,
    )

    repo, env = Path(repo_root).resolve(), (os.environ if environ is None else environ)
    try:
        effective, snapshot, run_id = decode_child_request(repo, envelope)
    except (ValueError, TypeError, KeyError) as exc:
        reason = (
            "verify-snapshot-mismatch"
            if str(exc) == "verify-snapshot-mismatch"
            else "verify-context-invalid"
        )
        raise ConsumerError("FAIL", reason) from None
    check_id = envelope["check_id"]
    context = {"run_id": run_id, "check_id": check_id}
    progress = [0]
    progress_token = _PROGRESS.set(progress)
    scratch_path = Path(tempfile.mkdtemp(prefix="lexiflow-release-runtime-")).resolve()
    source_parent = scratch_path / "source-parent"
    source_parent.mkdir(mode=0o700)
    try:
        fixture = consume_verify_snapshot(
            repo,
            context,
            envelope["snapshot"],
            source_parent,
        )
    except BridgeError as exc:
        try:
            shutil.rmtree(scratch_path)
        except OSError:
            _PROGRESS.reset(progress_token)
            raise ConsumerError("FAIL", "release-scratch-cleanup-failed") from None
        error = ConsumerError(exc.status, exc.code)
        _PROGRESS.reset(progress_token)
        raise error from None
    try:
        fixture_root = Path(fixture["fixture"])
        current = snapshot_check_inputs(repo, effective)
        if json.dumps(current, sort_keys=True, separators=(",", ":")) != json.dumps(
            envelope["snapshot"], sort_keys=True, separators=(",", ":")
        ):
            raise ConsumerError("FAIL", "verify-snapshot-mismatch")
        missing = _check_environment(fixture_root, env)
        if missing:
            raise ConsumerError("BLOCKED", "missing-environment")
        # Native endpoints are checked before any build or Docker side effect.
        binding = _binding(env)
        evidence = produce_candidates(fixture_root, scratch_path, env, binding)
        if not isinstance(evidence, dict) or evidence.get("status", "PASS") != "PASS":
            raise ConsumerError("FAIL", "release-runtime-evidence-invalid")
        lifecycle = execute_lifecycle(
            scratch_path, evidence, binding, env, fixture_root=fixture_root
        )
        result = {
            "status": "PASS",
            "run_id": context["run_id"],
            "check_id": check_id,
            "verify_input_fingerprint": envelope["snapshot"]["fingerprint"],
            "evidence": evidence,
            "lifecycle": lifecycle,
        }
        if progress[0] < 1:
            raise ConsumerError("FAIL", "release-runtime-no-checks-executed")
        result["checks_run"] = progress[0]
        result["failures"] = 0
        result["errors"] = 0
        result["skipped"] = 0
        result["reason"] = ""
        shutil.rmtree(scratch_path)
        _PROGRESS.reset(progress_token)
        return result
    except ConsumerError:
        # Leave the task-owned scratch intact for diagnosis; never recursively clean daemon resources here.
        _PROGRESS.reset(progress_token)
        raise
    except Exception:
        error = ConsumerError("FAIL", "release-runtime-consumer-failed")
        _PROGRESS.reset(progress_token)
        raise error from None


def _run_bounded(
    argv: list[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    limit: int = 1_048_576,
    *,
    input_stream: Any = None,
) -> tuple[bytes, bytes]:
    """发行消费者只映射统一进程事实，并记录真实成功命令。"""
    try:
        output = release_process.run_bounded(
            argv, cwd, env, timeout, limit, input_stream=input_stream
        )
    except release_process.ProcessError as exc:
        raise ConsumerError(exc.status, exc.reason) from None
    mark_completed()
    return output


def main() -> int:
    """读取匿名pipe envelope并输出带correlation的结果。"""
    envelope: dict[str, Any] | None = None
    try:
        release_process._install_termination_handler()
        root = Path(__file__).resolve().parents[4]
        envelope = _read_envelope(sys.stdin)
        result = run_consumer(root, envelope)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except ConsumerError as exc:
        report: dict[str, Any] = {"status": exc.status, "reason": exc.reason}
        if envelope is not None:
            report.update(
                {
                    "run_id": envelope["run_id"],
                    "check_id": envelope["check_id"],
                    "verify_input_fingerprint": envelope["snapshot"]["fingerprint"],
                }
            )
        report.update(
            {
                "checks_run": exc.checks_run,
                "failures": 1 if exc.status == "FAIL" else 0,
                "errors": 0,
                "skipped": 0,
            }
        )
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception:
        report = {
            "status": "FAIL",
            "reason": "release-runtime-consumer-failed",
            "checks_run": 0,
            "failures": 1,
            "errors": 0,
            "skipped": 0,
        }
        if envelope is not None:
            report.update(
                {
                    "run_id": envelope["run_id"],
                    "check_id": envelope["check_id"],
                    "verify_input_fingerprint": envelope["snapshot"]["fingerprint"],
                }
            )
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0


if __name__ == "__main__":
    # helpers 由规范模块导入异常与进度；CLI 同样使用该实例，避免 -m 的双实例。
    from scripts.checks.release.source.check_synthetic_release_lifecycle import (
        main as canonical_main,
    )

    raise SystemExit(canonical_main())
