"""候选执行事实的纯合同；两端分别认证各自请求与外部来源。"""

from __future__ import annotations
import re
from typing import Any

PHASE_IDS = (
    "preflight",
    "target-install",
    "target-repeat-install",
    "target-repeat-upgrade",
    "target-cleanup",
    "previous-install",
    "previous-baseline",
    "upgrade-failure",
    "upgrade-forward-retry",
    "retry-repeat-upgrade",
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
REQUIRED_FACTS = {
    "preflight": (
        "sourceClean",
        "hostDarwinArm64",
        "candidateBytesVerified",
        "sqlCompatible",
        "apiPortFree",
    ),
    "target-install": ("ready", "apiIdentity", "extensionIdentity", "installRecord"),
    "target-repeat-install": (
        "ready",
        "apiIdentity",
        "extensionIdentity",
        "successRecord",
    ),
    "target-repeat-upgrade": (
        "ready",
        "apiIdentity",
        "extensionIdentity",
        "successRecord",
    ),
    "target-cleanup": ("ownedResourcesRemoved",),
    "previous-install": ("ready", "apiIdentity", "extensionIdentity", "installRecord"),
    "previous-baseline": (
        "pgOwned",
        "volumeOwned",
        "markerPresent",
        "secretsAndDatasetHashed",
    ),
    "upgrade-failure": (
        "targetApiStoppedOnce",
        "upgradeNonzero",
        "targetNotReady",
        "failureRecord",
        "databaseCommitFactRetained",
    ),
    "upgrade-forward-retry": (
        "ready",
        "targetApiIdentity",
        "targetExtensionIdentity",
        "successRecord",
    ),
    "retry-repeat-upgrade": (
        "ready",
        "targetApiIdentity",
        "targetExtensionIdentity",
        "successRecord",
    ),
    "preservation": (
        "pgContainerOwned",
        "volumesSame",
        "markerSame",
        "secretsSame",
        "datasetSame",
        "portsSame",
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
    """完整核验固定候选事实，不认证请求、磁盘附件或 Formal 来源。"""

    def fail(reason: str) -> None:
        raise RuntimeContractError(reason)

    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != "lexiflow.candidate-runtime.v1"
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
    cleanup = value.get("cleanup")
    if (
        not isinstance(cleanup, dict)
        or cleanup.get("status") != "PASS"
        or cleanup.get("settled") is not True
        or cleanup.get("ownedResourcesRemoved") is not True
    ):
        fail("candidate-runtime-cleanup-invalid")
    candidates = value.get("candidates")
    if not isinstance(candidates, dict) or set(candidates) != {"previous", "target"}:
        fail("candidate-runtime-binding-invalid")
    for candidate in candidates.values():
        if (
            not isinstance(candidate, dict)
            or set(candidate) != {"candidateSha256", "manifestSha256", "buildIdentity"}
            or not _sha(candidate.get("candidateSha256"))
            or not _sha(candidate.get("manifestSha256"))
            or not _identity(candidate.get("buildIdentity"))
        ):
            fail("candidate-runtime-binding-invalid")
    if (
        candidates["previous"]["candidateSha256"]
        == candidates["target"]["candidateSha256"]
        or candidates["previous"]["buildIdentity"]["sourceCommit"]
        == candidates["target"]["buildIdentity"]["sourceCommit"]
    ):
        fail("candidate-runtime-candidates-not-distinct")
    identity = value.get("identity")
    if (
        not isinstance(identity, dict)
        or identity.get("source") != candidates["target"]["buildIdentity"]
    ):
        fail("candidate-runtime-binding-invalid")
    if value.get("assertions") != {
        "sourceAndCandidatesUnchanged": True,
        "realFaultProven": True,
        "forwardRetrySucceeded": True,
        "postgresVolumePreserved": True,
    }:
        fail("candidate-runtime-assertions-invalid")
