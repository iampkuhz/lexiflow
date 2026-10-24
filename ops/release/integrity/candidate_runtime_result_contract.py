"""单 runtime-image 候选运行事实合同；不授予 Formal 验收资格。"""

from __future__ import annotations

import re
from typing import Any

PHASE_IDS = (
    "preflight",
    "target-clean-init",
    "target-repeat-run",
    "target-restart",
    "interrupted-init-retry",
    "incompatible-volume-refusal",
    "failure-exit",
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

REQUIRED_FACTS = {
    "preflight": (
        "sourceClean",
        "hostDarwinArm64",
        "candidateBytesVerified",
        "apiPortFree",
        "containerNameFree",
        "imageLoadVerified",
    ),
    "target-clean-init": ("ready", "runtimeImageIdentity", "cleanVolume"),
    "target-repeat-run": ("ready", "publishedDataset", "sameVolume", "sameMarker"),
    "target-restart": ("ready", "publishedDataset", "sameVolume", "markerPresent"),
    "interrupted-init-retry": (
        "initInterrupted",
        "firstRunNonzero",
        "volumeRetained",
        "retryReady",
        "retryDatasetIdentity",
    ),
    "incompatible-volume-refusal": (
        "refused",
        "nonzero",
        "volumeRetained",
        "noUnknownResourcesRemoved",
    ),
    "failure-exit": (
        "apiExitObserved",
        "postgresExitObserved",
        "unhealthyObserved",
        "nonzero",
    ),
    "final-cleanup": ("ownedResourcesRemoved",),
}


class RuntimeContractError(ValueError):
    """候选执行事实缺失或不满足固定合同。"""


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _identity(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != IDENTITY_FIELDS:
        return False
    numeric = r"(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})"
    version = value.get("softwareVersion")
    base = value.get("baseVersion")
    if not isinstance(version, str) or not isinstance(base, str):
        return False
    if value.get("channel") == "release":
        valid_version = bool(re.fullmatch(numeric, version)) and base == version
    elif value.get("channel") == "snapshot":
        valid_version = (
            bool(
                re.fullmatch(
                    numeric + r"-SNAPSHOT\.[1-9][0-9]{7}\.g[a-f0-9]{7,64}", version
                )
            )
            and base == version.split("-SNAPSHOT.")[0] + "-SNAPSHOT"
        )
    else:
        return False
    return (
        type(value.get("schemaVersion")) is int
        and value["schemaVersion"] == 1
        and value.get("dirty") is False
        and valid_version
        and value.get("chromeVersion")
        == version.split("-")[0] + (".1" if value["channel"] == "release" else ".0")
        and isinstance(value.get("sourceCommit"), str)
        and re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value["sourceCommit"])
        is not None
        and _sha(value.get("buildId"))
        and _sha(value.get("sourceSha256"))
    )


def validate_runtime_result(value: Any) -> None:
    """核验完整真实运行事实；请求和外部来源由各自消费者认证。"""

    def fail(reason: str) -> None:
        raise RuntimeContractError(reason)

    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != "lexiflow.candidate-runtime.v2"
        or value.get("status") not in {"PASS", "FAIL", "BLOCKED"}
    ):
        fail("candidate-runtime-result-invalid")
    if value["status"] != "PASS":
        return
    phases = value.get("phases")
    if (
        not isinstance(phases, list)
        or len(phases) != len(PHASE_IDS)
        or not all(isinstance(phase, dict) for phase in phases)
        or [phase.get("id") for phase in phases] != list(PHASE_IDS)
        or any(phase.get("status") != "PASS" for phase in phases)
        or value.get("failure") is not None
    ):
        fail("candidate-runtime-incomplete")
    for phase in phases:
        facts = phase.get("facts")
        if not isinstance(facts, dict) or any(
            facts.get(key) is not True for key in REQUIRED_FACTS[phase["id"]]
        ):
            fail("candidate-runtime-facts-invalid")
    target = value.get("target")
    if (
        not isinstance(target, dict)
        or set(target) != {"candidateSha256", "manifestSha256", "buildIdentity"}
        or not _sha(target.get("candidateSha256"))
        or not _sha(target.get("manifestSha256"))
        or not _identity(target.get("buildIdentity"))
    ):
        fail("candidate-runtime-binding-invalid")
    if value.get("identity") != {"source": target["buildIdentity"]}:
        fail("candidate-runtime-binding-invalid")
    dataset_fields = {"releaseId", "preparationId", "ruleId", "sqlVersion", "sha256"}
    api_fields = {
        "softwareVersion",
        "apiContract",
        "mode",
        "datasetVersion",
        "publishedDataset",
    }
    for phase_id in (
        "target-clean-init",
        "target-repeat-run",
        "target-restart",
        "interrupted-init-retry",
    ):
        facts = next(item["facts"] for item in phases if item["id"] == phase_id)
        identity = facts.get("publishedDatasetIdentity")
        if (
            not isinstance(identity, dict)
            or set(identity) != dataset_fields
            or any(
                not isinstance(identity[key], str) or not identity[key]
                for key in dataset_fields
            )
            or not _sha(identity["sha256"])
        ):
            fail("candidate-runtime-dataset-identity-invalid")
    first = next(item["facts"] for item in phases if item["id"] == "target-clean-init")
    for phase_id in (
        "target-clean-init",
        "target-repeat-run",
        "target-restart",
        "interrupted-init-retry",
    ):
        facts = next(item["facts"] for item in phases if item["id"] == phase_id)
        api, extension = facts.get("apiIdentity"), facts.get("extensionIdentity")
        if (
            not isinstance(api, dict)
            or set(api) != api_fields
            or api.get("softwareVersion") != target["buildIdentity"]["softwareVersion"]
            or api.get("apiContract") != "caption-hints.v2"
            or api.get("mode") != "formal"
            or type(api.get("datasetVersion")) is not int
            or api["datasetVersion"] <= 0
            or api.get("publishedDataset") != facts["publishedDatasetIdentity"]
            or extension != facts["publishedDatasetIdentity"]
        ):
            fail("candidate-runtime-api-identity-invalid")
    for phase_id in ("target-repeat-run", "target-restart", "interrupted-init-retry"):
        facts = next(item["facts"] for item in phases if item["id"] == phase_id)
        if (
            facts["publishedDatasetIdentity"] != first["publishedDatasetIdentity"]
            or facts["apiIdentity"] != first["apiIdentity"]
        ):
            fail("candidate-runtime-dataset-identity-drift")
    cleanup = value.get("cleanup")
    if (
        not isinstance(cleanup, dict)
        or cleanup.get("status") != "PASS"
        or cleanup.get("settled") is not True
        or cleanup.get("ownedResourcesRemoved") is not True
    ):
        fail("candidate-runtime-cleanup-invalid")
    if value.get("assertions") != {
        "targetOnly": True,
        "realInitializationInterruption": True,
        "sameVolumeRestart": True,
        "realFailureObserved": True,
        "unknownResourcesUntouched": True,
    }:
        fail("candidate-runtime-assertions-invalid")
