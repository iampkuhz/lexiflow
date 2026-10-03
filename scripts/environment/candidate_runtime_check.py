"""将实际候选原生验收绑定到 Verify 冻结输入与输出附件。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from scripts.environment import release_runtime_check as process_runtime
from scripts.verification.declarations import load_declarations
from scripts.verification.kernel import (
    CANDIDATE_RUNTIME_CHECK_IDS,
    fingerprint_json,
    snapshot_check_inputs,
)
from scripts.verification.release_source_bridge import (
    BridgeError,
    _validate_selection_reasons,
)

REQUEST_ENV = "LEXIFLOW_CANDIDATE_RUNTIME_REQUEST"
MAX_REQUEST_BYTES = 16_384
PHASE_IDS = (
    "preflight",
    "target-install",
    "target-repeat",
    "target-noop",
    "target-cleanup",
    "previous-install",
    "previous-baseline",
    "upgrade-failure",
    "automatic-recovery",
    "upgrade-retry",
    "retry-noop",
    "preservation",
    "final-cleanup",
)
IDENTITY_FIELDS = {
    "schemaVersion",
    "baseVersion",
    "softwareVersion",
    "chromeVersion",
    "sourceCommit",
    "sourceSha256",
    "buildId",
    "dirty",
    "channel",
}
ConsumerError = process_runtime.ConsumerError


def _json(raw: bytes) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=process_runtime._pairs,
            parse_constant=process_runtime._reject_constant,
        )
    except (ValueError, UnicodeDecodeError):
        raise ConsumerError("FAIL", "candidate-runtime-json-invalid") from None


def _equal(left: Any, right: Any) -> bool:
    return fingerprint_json(left) == fingerprint_json(right)


def _sha(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{64}", value))


def _read_request(environ: Mapping[str, str]) -> tuple[dict[str, Any], str]:
    locator = environ.get(REQUEST_ENV, "")
    if not locator:
        raise ConsumerError("BLOCKED", "candidate-runtime-request-unavailable")
    file = Path(locator)
    if not file.is_absolute() or any(ord(char) < 32 for char in locator):
        raise ConsumerError("FAIL", "candidate-runtime-request-invalid")
    try:
        for item in [*reversed(file.parents), file]:
            if item.is_symlink():
                raise ValueError
        descriptor = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_REQUEST_BYTES:
                raise ValueError
            raw = stream.read(MAX_REQUEST_BYTES + 1)
            after = os.fstat(stream.fileno())
            named = file.stat(follow_symlinks=False)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(raw) > MAX_REQUEST_BYTES or any(
            getattr(before, key) != getattr(after, key)
            or getattr(after, key) != getattr(named, key)
            for key in fields
        ):
            raise ValueError
    except (OSError, ValueError):
        raise ConsumerError("FAIL", "candidate-runtime-request-invalid") from None
    request = _json(raw)
    if not isinstance(request, dict) or set(request) != {"previous", "target"}:
        raise ConsumerError("FAIL", "candidate-runtime-request-invalid")
    for value in request.values():
        if (
            not isinstance(value, dict)
            or set(value) != {"candidateDirectory", "candidateSha256"}
            or not isinstance(value["candidateDirectory"], str)
            or not Path(value["candidateDirectory"]).is_absolute()
            or any(ord(char) < 32 for char in value["candidateDirectory"])
            or not _sha(value["candidateSha256"])
        ):
            raise ConsumerError("FAIL", "candidate-runtime-request-invalid")
    if request["previous"]["candidateSha256"] == request["target"]["candidateSha256"]:
        raise ConsumerError("FAIL", "candidate-runtime-candidates-not-distinct")
    return request, hashlib.sha256(raw).hexdigest()


def _check_snapshot(root: Path, envelope: Mapping[str, Any]) -> None:
    try:
        declarations = load_declarations(root)["checks"]
        declared = next(
            item for item in declarations if item["check_id"] == envelope["check_id"]
        )
        expected = dict(declared)
        expected["input_paths"] = list(
            dict.fromkeys(
                [
                    *declared["input_paths"],
                    "harness/module-checks.yaml",
                ]
            )
        )
        supplied = envelope["effective_check"]
        if declared["scope"] == "change-targeted":
            reasons = supplied.get("selection_reasons")
            if not isinstance(reasons, list) or not reasons:
                raise ValueError
            _validate_selection_reasons(declared, reasons, declarations)
            expected["selection_reasons"] = reasons
        if (
            not _equal(expected, supplied)
            or fingerprint_json(supplied) != envelope["check_config_fingerprint"]
        ):
            raise ValueError
        if envelope["snapshot"].get("missing") or not _equal(
            snapshot_check_inputs(root, expected), envelope["snapshot"]
        ):
            raise ConsumerError("FAIL", "verify-snapshot-mismatch")
    except (KeyError, ValueError, StopIteration, BridgeError):
        raise ConsumerError("FAIL", "verify-context-invalid") from None


def _validate_result(value: Any, request: Mapping[str, Any]) -> None:
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != "lexiflow.candidate-runtime.v1"
        or value.get("status") not in {"PASS", "FAIL", "BLOCKED"}
    ):
        raise ConsumerError("FAIL", "candidate-runtime-result-invalid")
    if value["status"] != "PASS":
        return
    phases = value.get("phases")
    if (
        not isinstance(phases, list)
        or len(phases) != len(PHASE_IDS)
        or [item.get("id") for item in phases if isinstance(item, dict)]
        != list(PHASE_IDS)
        or any(item.get("status") != "PASS" for item in phases)
        or not isinstance(value.get("cleanup"), dict)
        or value["cleanup"].get("status") != "PASS"
        or value["cleanup"].get("settled") is not True
        or value.get("failure") is not None
    ):
        raise ConsumerError("FAIL", "candidate-runtime-incomplete")
    candidates = value.get("candidates")
    if not isinstance(candidates, dict) or set(candidates) != {"previous", "target"}:
        raise ConsumerError("FAIL", "candidate-runtime-binding-invalid")
    for name, candidate in candidates.items():
        if (
            not isinstance(candidate, dict)
            or set(candidate) != {"candidateSha256", "manifestSha256", "buildIdentity"}
            or candidate["candidateSha256"] != request[name]["candidateSha256"]
            or not _sha(candidate["manifestSha256"])
            or not isinstance(candidate["buildIdentity"], dict)
            or set(candidate["buildIdentity"]) != IDENTITY_FIELDS
            or candidate["buildIdentity"].get("schemaVersion") != 1
            or not _sha(candidate["buildIdentity"].get("buildId"))
            or not _sha(candidate["buildIdentity"].get("sourceSha256"))
            or candidate["buildIdentity"].get("dirty") is not False
        ):
            raise ConsumerError("FAIL", "candidate-runtime-binding-invalid")
    identity = value.get("identity")
    if not isinstance(identity, dict) or not _equal(
        identity.get("source"), candidates["target"]["buildIdentity"]
    ):
        raise ConsumerError("FAIL", "candidate-runtime-binding-invalid")


def run_consumer(
    root: Path, envelope: Mapping[str, Any], environ: Mapping[str, str]
) -> dict[str, Any]:
    """只运行固定 Node 候选入口，结束前再次核对冻结输入和请求字节。"""
    _check_snapshot(root, envelope)
    request, request_sha = _read_request(environ)
    # Node 自行读实际宿主、Podman machine 和候选；不传凭据、平台覆盖或任意环境。
    env = {
        key: environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "TMPDIR")
        if key in environ
    }
    env["HOME"] = str(Path.home())
    with tempfile.TemporaryFile() as stream:
        stream.write(json.dumps(request, separators=(",", ":")).encode())
        stream.seek(0)
        stdout, _stderr = process_runtime._run_bounded(
            ["node", "ops/podman/candidate-runtime.mjs"],
            root,
            env,
            2100,
            input_stream=stream,
        )
    result = _json(stdout)
    _validate_result(result, request)
    _check_snapshot(root, envelope)
    after, after_sha = _read_request(environ)
    if request_sha != after_sha or not _equal(request, after):
        raise ConsumerError("FAIL", "candidate-runtime-request-drift")
    passed = result["status"] == "PASS"
    return {
        "status": result["status"],
        "reason": "" if passed else "candidate-runtime-not-passed",
        "checks_run": len(PHASE_IDS) if passed else 0,
        "failures": int(result["status"] == "FAIL"),
        "errors": 0,
        "skipped": 0,
        "run_id": envelope["run_id"],
        "check_id": envelope["check_id"],
        "verify_input_fingerprint": envelope["snapshot"]["fingerprint"],
        "check_config_fingerprint": envelope["check_config_fingerprint"],
        "request_sha256": request_sha,
        "runtime": result,
    }


def main() -> int:
    """消费父进程 envelope，仅输出带关联字段的标准 Check JSON。"""
    envelope = None
    try:
        envelope = process_runtime._read_envelope(
            sys.stdin, check_ids=CANDIDATE_RUNTIME_CHECK_IDS
        )
        result = run_consumer(Path(__file__).resolve().parents[2], envelope, os.environ)
    except Exception as error:  # 错误消息可能含本机路径，不写入公开附件。
        known = isinstance(error, ConsumerError)
        status = error.status if known else "FAIL"
        result = {
            "status": status,
            "reason": error.reason if known else "candidate-runtime-consumer-failed",
            "checks_run": 0,
            "failures": int(status == "FAIL"),
            "errors": 0,
            "skipped": 0,
        }
        if envelope is not None:
            result.update(
                run_id=envelope["run_id"],
                check_id=envelope["check_id"],
                verify_input_fingerprint=envelope["snapshot"]["fingerprint"],
            )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
