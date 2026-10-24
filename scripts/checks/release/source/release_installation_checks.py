"""单 runtime-image 合成候选生命周期消费者。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.checks.release.source.check_synthetic_release_lifecycle import (
    ConsumerError,
)


def execute_lifecycle(
    scratch: Path,
    evidence: Mapping[str, Any],
    binding: Any,
    environ: Mapping[str, str],
    *,
    fixture_root: Path,
) -> dict[str, Any]:
    """在真实冻结源码 fixture 中对两代单容器候选分别执行严格 lifecycle Check。"""
    from ops.release.host_resources import release_docker_preflight
    from ops.release.integrity.candidate_runtime_result_contract import (
        RuntimeContractError,
        validate_runtime_result,
    )
    from scripts.checks.release.source.check_synthetic_release_lifecycle import (
        _pairs,
        _reject_constant,
        _run_bounded,
    )
    from scripts.checks.release.source.release_test_artifact_builder import (
        _read_regular,
    )

    candidates = evidence.get("releaseCandidates")
    datasets = evidence.get("datasetRecords")
    if (
        not isinstance(candidates, list)
        or len(candidates) != 2
        or not isinstance(datasets, list)
        or len(datasets) != 2
    ):
        raise ConsumerError("FAIL", "release-candidate-not-assembled")
    if datasets[0].get("sha256") == datasets[1].get("sha256") or datasets[0].get(
        "version"
    ) == datasets[1].get("version"):
        raise ConsumerError("FAIL", "synthetic-generations-not-distinct")
    checker = (
        fixture_root / "ops/podman/candidates/check-podman-candidate-lifecycle.mjs"
    )
    if checker.is_symlink() or not checker.is_file():
        raise ConsumerError("FAIL", "candidate-lifecycle-check-missing")
    work = scratch / "single-runtime-candidates"
    work.mkdir(mode=0o700)
    home = work / "home"
    home.mkdir(mode=0o700)
    env = {
        "PATH": environ.get("PATH", os.defpath),
        "HOME": str(home),
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
        "DOCKER_HOST": binding.endpoint,
    }
    results = []
    lifecycle_deadline = time.monotonic() + 1100
    for candidate, dataset in zip(candidates, datasets, strict=True):
        directory = Path(candidate["candidateDirectory"])
        parent = scratch / "synthetic-releases"
        marker_path = directory / "candidate.json"
        if (
            not directory.is_absolute()
            or directory.is_symlink()
            or not directory.is_dir()
            or parent.is_symlink()
            or directory.parent != parent
            or directory.resolve().parent != parent.resolve()
            or marker_path.is_symlink()
            or not marker_path.is_file()
        ):
            raise ConsumerError("FAIL", "release-candidate-path-invalid")
        marker_bytes = _read_regular(marker_path, 2_000_000)
        digest = hashlib.sha256(marker_bytes).hexdigest()
        if digest != candidate.get("candidateSha256"):
            raise ConsumerError("FAIL", "release-candidate-hash-mismatch")
        marker = json.loads(marker_bytes.decode("utf-8"))
        if marker.get("sourceCommit") != evidence.get("sourceCommit"):
            raise ConsumerError("FAIL", "release-candidate-source-mismatch")
        check = release_docker_preflight.recheck(binding)
        if check.status != "PASS":
            raise ConsumerError("FAIL", "release-docker-binding-changed")
        request = {
            "target": {"candidateDirectory": str(directory), "candidateSha256": digest}
        }
        with tempfile.TemporaryFile() as stream:
            stream.write(json.dumps(request, separators=(",", ":")).encode("utf-8"))
            stream.seek(0)
            remaining = lifecycle_deadline - time.monotonic()
            if remaining <= 0:
                raise ConsumerError("BLOCKED", "candidate-runtime-lifecycle-timeout")
            stdout, _ = _run_bounded(
                ["node", str(checker)],
                fixture_root,
                env,
                remaining,
                input_stream=stream,
            )
        try:
            value = json.loads(
                stdout, object_pairs_hook=_pairs, parse_constant=_reject_constant
            )
            validate_runtime_result(value)
        except (UnicodeDecodeError, ValueError, TypeError, RuntimeContractError):
            raise ConsumerError("FAIL", "candidate-runtime-result-invalid") from None
        if (
            value.get("status") != "PASS"
            or value.get("target", {}).get("candidateSha256") != digest
        ):
            raise ConsumerError("FAIL", "candidate-runtime-result-not-pass")
        phase = next(
            item for item in value["phases"] if item["id"] == "target-clean-init"
        )
        observed = phase["facts"].get("publishedDatasetIdentity")
        expected_dataset = {
            "releaseId": "synthetic-" + dataset["sha256"],
            "preparationId": "synthetic-"
            + hashlib.sha256(b"lexiflow.deterministic-preparation.v1").hexdigest(),
            "ruleId": "synthetic-no-model",
            "sqlVersion": evidence.get("sqlVersion"),
            "sha256": dataset["sha256"],
        }
        api_identity = phase["facts"].get("apiIdentity")
        if (
            observed != expected_dataset
            or not isinstance(api_identity, dict)
            or api_identity.get("datasetVersion") != dataset.get("version")
        ):
            raise ConsumerError("FAIL", "candidate-published-dataset-mismatch")
        results.append(value)
    return {
        "platform": "linux/arm64",
        "endpoint": binding.endpoint,
        "generations": results,
        "lifecycleChecks": sum(len(item.get("phases", [])) for item in results),
        "hostPortVerified": False,
        "realChromeVerified": False,
    }
