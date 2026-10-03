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

from scripts.delivery_gate.consume import consume_existing_pass
from scripts.verification.reports import validate_report
from scripts.verification.declarations import load_declarations_snapshot
from scripts.verification.kernel import fingerprint_json
from scripts.environment.candidate_runtime_check import PHASE_IDS, _validate_result
from scripts.environment.release_runtime_check import _run_bounded, ConsumerError


REQUIRED_FACTS = {
    "preflight": (
        "sourceClean",
        "hostDarwinArm64",
        "candidateBytesVerified",
        "sqlCompatible",
        "apiPortFree",
    ),
    "target-install": ("ready", "apiIdentity", "extensionIdentity", "installRecord"),
    "target-repeat": ("noExtraRecord", "sameExtension"),
    "target-noop": ("noExtraRecord", "sameExtension"),
    "target-cleanup": ("ownedResourcesRemoved",),
    "previous-install": ("ready", "apiIdentity", "extensionIdentity", "installRecord"),
    "previous-baseline": (
        "pgOwned",
        "volumeOwned",
        "markerPresent",
        "secretsAndDatasetHashed",
    ),
    "upgrade-failure": ("targetApiStoppedOnce", "upgradeNonzero"),
    "automatic-recovery": (
        "previousApiRestored",
        "previousExtensionRestored",
        "pgAndMarkerPreserved",
        "failureRecord",
    ),
    "upgrade-retry": (
        "ready",
        "targetApiIdentity",
        "targetExtensionIdentity",
        "successRecord",
    ),
    "retry-noop": ("noExtraRecord", "sameExtension"),
    "preservation": (
        "pgContainerSame",
        "volumesSame",
        "markerSame",
        "secretsSame",
        "datasetSame",
        "portsSame",
    ),
    "final-cleanup": ("ownedResourcesRemoved",),
}


class CandidateProofError(ValueError):
    """候选或链证据缺失、漂移或不满足只读消费合同。"""


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
    report_locator = (
        f"tmp/quality/delivery-gate/validations/{proof['validation_id']}/report.json"
    )
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
        != ["-m", "scripts.environment.candidate_runtime_check"]
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
            [*expected_checks[0].get("input_paths", []), "harness/module-checks.yaml"]
        )
    )
    if expected_checks[0].get("command") != [
        "python3",
        "-m",
        "scripts.environment.candidate_runtime_check",
    ]:
        raise CandidateProofError("candidate-runtime-command-invalid")
    if (
        check.get("module") != expected_checks[0]["module"]
        or check.get("result_contract", {}).get("report", {}).get("status") != "PASS"
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
        or envelope.get("status") != "PASS"
        or envelope.get("checks_run") != len(PHASE_IDS)
        or envelope.get("failures") != 0
        or envelope.get("errors") != 0
        or envelope.get("skipped") != 0
        or envelope.get("run_id") != report.get("run_id")
        or envelope.get("check_id") != "eng.release.candidate-runtime"
        or envelope.get("verify_input_fingerprint")
        != snapshots["pre"].get("fingerprint")
        or envelope.get("check_config_fingerprint")
        != fingerprint_json(expected_checks[0])
        or not isinstance(envelope.get("request_sha256"), str)
        or re.fullmatch(r"[a-f0-9]{64}", envelope["request_sha256"]) is None
        or re.fullmatch(r"[a-f0-9]{64}", envelope["check_config_fingerprint"]) is None
    ):
        raise CandidateProofError("candidate-runtime-output-invalid")
    value = envelope.get("runtime")
    if not isinstance(value, dict) or value.get("status") != "PASS":
        raise CandidateProofError("candidate-runtime-output-invalid")
    candidates = value.get("candidates")
    if not isinstance(candidates, dict) or set(candidates) != {"previous", "target"}:
        raise CandidateProofError("candidate-runtime-request-invalid")
    # Runtime artifact itself records both actual candidate identities. Feed those
    # records to the producer validator, then separately bind target to the Gate.
    request = {
        name: {"candidateSha256": item.get("candidateSha256")}
        for name, item in candidates.items()
        if isinstance(item, dict)
    }
    if set(request) != {"previous", "target"}:
        raise CandidateProofError("candidate-runtime-request-invalid")
    try:
        _validate_result(value, request)
    except ConsumerError:
        raise CandidateProofError("candidate-runtime-result-invalid") from None
    if value.get("cleanup", {}).get("ownedResourcesRemoved") is not True:
        raise CandidateProofError("candidate-runtime-cleanup-invalid")
    if any(
        phase.get("facts", {}).get("ownedResourcesRemoved") is not True
        for phase in value["phases"]
        if phase["id"] in ("target-cleanup", "final-cleanup")
    ):
        raise CandidateProofError("candidate-runtime-cleanup-invalid")
    for phase in value["phases"]:
        facts = phase.get("facts")
        if not isinstance(facts, dict) or any(
            facts.get(key) is not True for key in REQUIRED_FACTS[phase["id"]]
        ):
            raise CandidateProofError("candidate-runtime-facts-invalid")
    for name in ("previous", "target"):
        if (
            not isinstance(candidates[name]["candidateSha256"], str)
            or re.fullmatch(r"[a-f0-9]{64}", candidates[name]["candidateSha256"])
            is None
        ):
            raise CandidateProofError("candidate-runtime-binding-invalid")
    if (
        candidates["previous"]["candidateSha256"]
        == candidates["target"]["candidateSha256"]
        or candidates["previous"]["buildIdentity"]["buildId"]
        == candidates["target"]["buildIdentity"]["buildId"]
    ):
        raise CandidateProofError("candidate-runtime-candidates-not-distinct")
    assertions = value.get("assertions")
    if not isinstance(assertions, dict) or assertions != {
        "sourceAndCandidatesUnchanged": True,
        "realFaultAndRecovery": True,
        "samePgAndDataset": True,
    }:
        raise CandidateProofError("candidate-runtime-assertions-invalid")
    return value, stdout, desc


def _node_proof(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    script = Path(__file__).resolve().parents[2] / "ops/release/candidate-proof.mjs"
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
        if key in os.environ
    }
    try:
        with tempfile.TemporaryFile() as stream:
            stream.write(json.dumps(request, separators=(",", ":")).encode())
            stream.seek(0)
            stdout, _stderr = _run_bounded(
                ["node", str(script)], root, env, 60, limit=65536, input_stream=stream
            )
    except ConsumerError:
        raise CandidateProofError("candidate-integrity-check-failed") from None
    try:
        value = _json(stdout)
    except (ValueError, UnicodeError):
        raise CandidateProofError("candidate-integrity-output-invalid") from None
    return value


def consume_candidate_pass(
    root: str | Path, *, submission_id: str, candidate_directory: str | Path
) -> dict[str, Any]:
    """重核既有 PASS 链与唯一候选运行 stdout，再只读验证相同候选字节。"""
    repo = Path(root).resolve()
    candidate = Path(candidate_directory)
    if not candidate.is_absolute():
        return {"result": "BLOCKED", "reason": "candidate-directory-must-be-absolute"}
    first = consume_existing_pass(repo, submission_id=submission_id)
    if first.get("result") != "PASS" or not isinstance(first.get("proof"), dict):
        return first
    try:
        value, stdout, stdout_desc = _extract(repo, first["proof"])
        target = value["candidates"]["target"]
        expected = {
            "candidateDirectory": str(candidate),
            "candidateSha256": target["candidateSha256"],
            "manifestSha256": target["manifestSha256"],
            "buildIdentity": target["buildIdentity"],
        }
        node = _node_proof(repo, expected)
        if node != {
            k: expected["buildIdentity"] if k == "buildIdentity" else expected[k]
            for k in ("candidateSha256", "manifestSha256", "buildIdentity")
        }:
            raise CandidateProofError("candidate-integrity-binding-invalid")
        # 候选读取可能耗时；最终链与附件读取必须位于它之后。
        node2 = _node_proof(repo, expected)
        second = consume_existing_pass(repo, submission_id=submission_id)
        if second.get("result") != "PASS" or second.get("proof") != first["proof"]:
            raise CandidateProofError("evidence-changed-during-consumption")
        value2, stdout2, desc2 = _extract(repo, second["proof"])
        if (
            second.get("result") != "PASS"
            or second.get("proof") != first["proof"]
            or stdout2 != stdout
            or desc2 != stdout_desc
            or value2 != value
            or node2 != node
        ):
            raise CandidateProofError("evidence-changed-during-consumption")
        return {
            "result": "PASS",
            "published": False,
            "delivery_rerun": False,
            "proof": {
                **first["proof"],
                "candidate_runtime_stdout": stdout_desc,
                "candidate": {
                    "candidateSha256": target["candidateSha256"],
                    "manifestSha256": target["manifestSha256"],
                    "buildIdentity": target["buildIdentity"],
                },
            },
        }
    except (
        CandidateProofError,
        KeyError,
        TypeError,
        ValueError,
        AttributeError,
        OSError,
    ) as exc:
        reason = (
            str(exc)
            if isinstance(exc, CandidateProofError)
            else "candidate-proof-invalid"
        )
        return {
            "submission_id": submission_id,
            "result": "BLOCKED",
            "reason": reason,
            "published": False,
            "delivery_rerun": False,
        }
