"""只读联合消费候选运行附件与既有 Formal PASS 链。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.acceptance.evidence.verify_existing_acceptance_chain import _consume_pass
from scripts.verification.reports.verification_report_store import validate_report
from scripts.verification.planning.check_definitions import load_declarations_snapshot
from ops.release.integrity.candidate_runtime_result_contract import (
    PHASE_IDS,
    RuntimeContractError,
    validate_runtime_result,
)
from ops.release.host_resources.release_process_control import run_bounded, ProcessError


class CandidateProofError(ValueError):
    """候选或链证据缺失、漂移或不满足只读消费合同。"""

    def __init__(self, reason: str):
        self.code = reason
        super().__init__(reason)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json(data: bytes) -> Any:
    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_unique,
        parse_constant=lambda _value: (_ for _ in ()).throw(
            ValueError("non-finite number")
        ),
        parse_float=lambda value: _finite_float(value),
    )


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return number


def _read_bounded(root: Path, locator: str, limit: int) -> bytes:
    if (
        not isinstance(locator, str)
        or not locator
        or "\\" in locator
        or any(ord(char) < 32 for char in locator)
    ):
        raise CandidateProofError("evidence-locator-invalid")
    pure = PurePosixPath(locator)
    if pure.as_posix() != locator:
        raise CandidateProofError("evidence-locator-invalid")
    if pure.is_absolute() or any(x in ("", ".", "..") for x in pure.parts):
        raise CandidateProofError("evidence-locator-invalid")
    base = root.resolve()
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fd = os.open(base, directory_flags)
        for part in pure.parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(
            pure.parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=fd,
        )
        try:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise CandidateProofError("evidence-size-invalid")
            chunks = []
            total = 0
            while True:
                chunk = os.read(file_fd, min(1024 * 1024, limit + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise CandidateProofError("evidence-size-invalid")
                chunks.append(chunk)
            after = os.fstat(file_fd)
            if total != info.st_size or any(
                getattr(info, key) != getattr(after, key)
                for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            ):
                raise CandidateProofError("evidence-changed-during-read")
            return b"".join(chunks)
        finally:
            os.close(file_fd)
    except CandidateProofError:
        raise
    except OSError:
        raise CandidateProofError("evidence-path-unsafe") from None
    finally:
        if "fd" in locals():
            os.close(fd)


def _bound(
    root: Path, descriptor: Any, *, prefix: str, limit: int
) -> tuple[bytes, dict[str, Any]]:
    if not isinstance(descriptor, dict) or set(descriptor) != {
        "locator",
        "sha256",
        "bytes",
    }:
        raise CandidateProofError("evidence-descriptor-invalid")
    locator = descriptor["locator"]
    if (
        not isinstance(locator, str)
        or not locator.startswith(prefix)
        or type(descriptor["bytes"]) is not int
        or descriptor["bytes"] < 0
        or descriptor["bytes"] > limit
        or not isinstance(descriptor["sha256"], str)
        or re.fullmatch(r"[a-f0-9]{64}", descriptor["sha256"]) is None
    ):
        raise CandidateProofError("evidence-descriptor-invalid")
    data = _read_bounded(root, locator, limit)
    if (
        len(data) != descriptor["bytes"]
        or hashlib.sha256(data).hexdigest() != descriptor["sha256"]
    ):
        raise CandidateProofError("evidence-hash-mismatch")
    return data, descriptor


def _extract(
    root: Path, proof: dict[str, Any]
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    report_desc = proof["verification_report"]
    report_locator = f"tmp/quality/delivery-gate/current/validations/{proof['validation_id']}/report.json"
    if (
        not isinstance(report_desc, dict)
        or set(report_desc) != {"locator", "sha256"}
        or report_desc["locator"] != report_locator
        or not isinstance(report_desc["sha256"], str)
        or re.fullmatch(r"[a-f0-9]{64}", report_desc["sha256"]) is None
    ):
        raise CandidateProofError("verification-report-descriptor-invalid")
    report_bytes = _read_bounded(root, report_locator, 32 * 1024 * 1024)
    if hashlib.sha256(report_bytes).hexdigest() != report_desc["sha256"]:
        raise CandidateProofError("verification-report-hash-mismatch")
    report = _json(report_bytes)
    if not isinstance(report, dict):
        raise CandidateProofError("verification-report-invalid")
    validate_report(root, report)
    if (
        report.get("result") != "PASS"
        or report.get("scope") != "repository-baseline"
        or report.get("coverage_gaps") != []
        or report.get("frozen_input_fingerprint") != proof["frozen_input_fingerprint"]
    ):
        raise CandidateProofError("verification-report-not-full-pass")
    matches = [
        item
        for item in report["checks"]
        if isinstance(item, dict)
        and item.get("check_id") == "eng.release.candidate-runtime"
    ]
    if len(matches) != 1:
        raise CandidateProofError("candidate-runtime-check-not-unique")
    check = matches[0]
    if check.get("status") != "PASS" or check.get("reason") not in (
        "",
        "equivalent-check-deduplicated",
    ):
        raise CandidateProofError("candidate-runtime-check-not-pass")
    process = check.get("process", {})
    if (
        process.get("exit_reason") != "exited"
        or process.get("exit_code") != 0
        or process.get("timed_out") is not False
        or not isinstance(process.get("executed_argv"), list)
        or len(process["executed_argv"]) != 3
        or not isinstance(process["executed_argv"][0], str)
        or not process["executed_argv"][0]
        or process["executed_argv"][1:]
        != [
            "-m",
            "scripts.checks.release.candidate.check_candidate_installation_lifecycle",
        ]
    ):
        raise CandidateProofError("candidate-runtime-process-invalid")
    declarations, _ = load_declarations_snapshot(root)
    expected_checks = [
        dict(item)
        for item in declarations["checks"]
        if item.get("check_id") == "eng.release.candidate-runtime"
    ]
    if len(expected_checks) != 1:
        raise CandidateProofError("candidate-runtime-declaration-invalid")
    expected_checks[0]["input_paths"] = list(
        dict.fromkeys(
            [
                *expected_checks[0].get("input_paths", []),
                "config/engineering/module-checks.yaml",
            ]
        )
    )
    if expected_checks[0].get("command") != [
        "python3",
        "-m",
        "scripts.checks.release.candidate.check_candidate_installation_lifecycle",
    ]:
        raise CandidateProofError("candidate-runtime-command-invalid")
    if (
        check.get("result_contract", {}).get("report", {}).get("status") != "PASS"
        or check["result_contract"]["report"].get("check_id") != check["check_id"]
    ):
        raise CandidateProofError("candidate-runtime-result-contract-invalid")
    snapshots = check.get("input_snapshot")
    if (
        not isinstance(snapshots, dict)
        or set(snapshots) != {"pre", "before_execution", "post", "final"}
        or not (
            snapshots["pre"]
            == snapshots["before_execution"]
            == snapshots["post"]
            == snapshots["final"]
        )
    ):
        raise CandidateProofError("candidate-runtime-snapshot-invalid")
    artifacts = process.get("output_artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"stdout", "stderr"}:
        raise CandidateProofError("candidate-runtime-artifacts-invalid")
    stdout_locator = f"tmp/quality/verification/{report['run_id']}/eng.release.candidate-runtime.stdout.log"
    if artifacts["stdout"].get("locator") != stdout_locator:
        raise CandidateProofError("candidate-runtime-stdout-locator-invalid")
    stdout, desc = _bound(
        root, artifacts["stdout"], prefix=stdout_locator, limit=4 * 1024 * 1024
    )
    envelope = _json(stdout)
    if envelope != check["result_contract"]["report"]:
        raise CandidateProofError("candidate-runtime-result-contract-invalid")
    if (
        not isinstance(envelope, dict)
        or set(envelope)
        != {
            "status",
            "reason",
            "checks_run",
            "failures",
            "errors",
            "skipped",
            "run_id",
            "check_id",
            "verify_input_fingerprint",
            "request_sha256",
            "runtime",
        }
        or envelope.get("status") != "PASS"
        or envelope.get("checks_run") != len(PHASE_IDS)
        or envelope.get("failures") != 0
        or envelope.get("errors") != 0
        or envelope.get("skipped") != 0
        or envelope.get("run_id") != report.get("run_id")
        or envelope.get("check_id") != "eng.release.candidate-runtime"
        or envelope.get("verify_input_fingerprint")
        != snapshots["pre"].get("fingerprint")
        or not isinstance(envelope.get("request_sha256"), str)
        or re.fullmatch(r"[a-f0-9]{64}", envelope["request_sha256"]) is None
    ):
        raise CandidateProofError("candidate-runtime-output-invalid")
    value = envelope.get("runtime")
    if not isinstance(value, dict) or value.get("status") != "PASS":
        raise CandidateProofError("candidate-runtime-output-invalid")
    try:
        validate_runtime_result(value)
    except RuntimeContractError as exc:
        raise CandidateProofError(str(exc)) from None
    return value, stdout, desc


def _node_proof(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    script = (
        Path(__file__).resolve().parents[3]
        / "ops/release/integrity/candidate-source-proof.mjs"
    )
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
        if key in os.environ
    }
    try:
        with tempfile.TemporaryFile() as stream:
            stream.write(json.dumps(request, separators=(",", ":")).encode())
            stream.seek(0)
            stdout, _stderr = run_bounded(
                ["node", str(script)], root, env, 60, limit=65536, input_stream=stream
            )
    except ProcessError:
        raise CandidateProofError("candidate-integrity-check-failed") from None
    try:
        value = _json(stdout)
    except (ValueError, UnicodeError):
        raise CandidateProofError("candidate-integrity-output-invalid") from None
    return value


def _prove_candidate(
    repo: Path, proof: dict[str, Any], candidate: Path
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """同一 Gate 读取窗口内验证一次带完整前后稳定性证明的候选。"""
    value, stdout, descriptor = _extract(repo, proof)
    target = value["target"]
    expected = {
        "candidateDirectory": str(candidate),
        "candidateSha256": target["candidateSha256"],
    }
    node = _node_proof(repo, expected)
    if node != target:
        raise CandidateProofError("candidate-integrity-binding-invalid")
    return value, stdout, descriptor


def _recheck_candidate(
    repo: Path,
    proof: dict[str, Any],
    initial: tuple[dict[str, Any], bytes, dict[str, Any]],
) -> dict[str, Any]:
    """Gate 末尾重核完整附件来源，不把 Node 返回当作 receipt 新鲜度。"""
    final = _extract(repo, proof)
    if final != initial:
        raise CandidateProofError("evidence-changed-during-consumption")
    value, _stdout, descriptor = final
    return {
        "candidate_runtime_stdout": descriptor,
        "candidate": value["target"],
    }


def consume_candidate_pass(
    root: str | Path, *, submission_id: str, candidate_directory: str | Path
) -> dict[str, Any]:
    """一次 DAG 求值中消费候选，终边界仍重读所有 Gate、附件与 actor 来源。"""
    candidate = Path(candidate_directory)
    if not candidate.is_absolute():
        return {"result": "BLOCKED", "reason": "candidate-directory-must-be-absolute"}
    return _consume_pass(
        root, submission_id=submission_id, candidate_directory=candidate
    )
