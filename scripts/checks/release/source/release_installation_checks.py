"""在显式原生daemon上执行产品安装生命周期。"""

from __future__ import annotations

import json
import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping

from scripts.checks.release.source.check_synthetic_release_lifecycle import (
    ConsumerError,
)


def _run(
    argv: list[str], cwd: Path, env: Mapping[str, str], timeout: int = 180
) -> bytes:
    from scripts.checks.release.source.check_synthetic_release_lifecycle import (
        _run_bounded,
    )

    return _run_bounded(argv, cwd, env, timeout, limit=4096)[0]


def _docker(
    binding: Any, docker_config: Path, argv: list[str], env: Mapping[str, str]
) -> bytes:
    from scripts.checks.release.source.check_synthetic_release_lifecycle import (
        _run_bounded,
    )

    command_env = {
        "PATH": env.get("PATH", os.defpath),
        "HOME": str(docker_config),
        "LC_ALL": "C",
        "LANG": "C",
    }
    return _run_bounded(
        ["docker", "--config", str(docker_config), "--host", binding.endpoint, *argv],
        docker_config,
        command_env,
        90,
        limit=65_536,
    )[0]


def _compose_plugin(
    binding: Any, environ: Mapping[str, str]
) -> tuple[Path, str, tuple[int, int, int, int, str]]:
    """从当前 Docker CLI 的只读客户端元数据绑定唯一已安装 Compose 插件。"""
    metadata_env = {
        "PATH": environ.get("PATH", os.defpath),
        "HOME": environ.get("HOME", str(Path.home())),
        "LC_ALL": "C",
        "LANG": "C",
    }
    try:
        raw = _run(
            [
                "docker",
                "--host",
                binding.endpoint,
                "info",
                "--format",
                "{{json .ClientInfo.Plugins}}",
            ],
            Path.cwd(),
            metadata_env,
            timeout=30,
        )
    except ConsumerError:
        raise
    except Exception:
        raise ConsumerError("FAIL", "release-compose-metadata-failed") from None

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate-key")
            result[key] = value
        return result

    try:
        plugins = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
        if not isinstance(plugins, list):
            raise ValueError
        matches = [
            item
            for item in plugins
            if isinstance(item, dict) and item.get("Name") == "compose"
        ]
        if len(matches) != 1:
            raise ValueError
        item = matches[0]
        if (
            set(item)
            != {
                "SchemaVersion",
                "Vendor",
                "Version",
                "ShortDescription",
                "Name",
                "Path",
            }
            or item["SchemaVersion"] != "0.1.0"
            or not all(
                isinstance(item[key], str) and item[key]
                for key in ("Vendor", "Version", "ShortDescription")
            )
            or not isinstance(item["Path"], str)
            or not item["Path"]
            or not Path(item["Path"]).is_absolute()
            or any(ord(char) < 32 or ord(char) == 127 for char in item["Path"])
        ):
            raise ValueError
        path = Path(item["Path"]).resolve(strict=True)
        if path.name != "docker-compose":
            raise ValueError
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
            raise ValueError
        fingerprint = _compose_plugin_fingerprint(path)
        return path, item["Version"], fingerprint
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise ConsumerError("FAIL", "release-compose-plugin-invalid") from None


def _assert_compose_plugin_unchanged(
    path: Path, fingerprint: tuple[int, int, int, int, str]
) -> None:
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
            raise ValueError
        if _compose_plugin_fingerprint(path) != fingerprint:
            raise ValueError
    except (OSError, ValueError):
        raise ConsumerError("FAIL", "release-compose-plugin-changed") from None


def _compose_version_matches(output: bytes, expected: str) -> bool:
    match = re.search(rb"Docker Compose version v?([0-9]+\.[0-9]+\.[0-9]+)", output)
    return bool(match and match.group(1).decode("ascii") == expected)


def _compose_plugin_fingerprint(path: Path) -> tuple[int, int, int, int, str]:
    """无跟随、有界读取插件，并确认读取期间文件身份与元数据稳定。"""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 128 * 1024 * 1024:
            raise ValueError
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if total > 128 * 1024 * 1024:
                raise ValueError
            digest.update(chunk)
        after = os.fstat(descriptor)
        current = path.stat()

        def identity(value: os.stat_result) -> tuple[int, int, int, int]:
            return (
                value.st_dev,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
            )

        if (
            identity(before) != identity(after)
            or identity(after) != identity(current)
            or total != after.st_size
        ):
            raise ValueError
        return (*identity(after), digest.hexdigest())
    finally:
        os.close(descriptor)


def _release_key(entry: Path) -> str:
    text = entry.read_text(encoding="utf-8")
    matches = re.findall(r"^  LF_RELEASE_KEY='([0-9a-f]{64})'$", text, re.MULTILINE)
    if len(matches) != 1:
        raise ConsumerError("FAIL", "release-entry-identity-invalid")
    return matches[0]


def _remove_owned_installation_root(install_root: Path, binding: Any) -> None:
    """仅在产品删除后的私有安装树完全符合合同后清理测试根目录。"""
    if install_root.is_symlink() or not install_root.is_dir():
        raise ConsumerError("FAIL", "release-installation-cleanup-failed")
    owner_path = install_root / "owner"
    engine_path = install_root / "engine"
    state_path = install_root / "state"
    releases_path = install_root / "releases"
    expected_children = {"owner", "engine", "state", "releases"}
    try:
        if {item.name for item in install_root.iterdir()} != expected_children:
            raise ValueError
        for path in (owner_path, engine_path, state_path):
            if path.is_symlink() or not path.is_file():
                raise ValueError
        owner = owner_path.read_text(encoding="ascii")
        engine = engine_path.read_text(encoding="ascii")
        state = state_path.read_text(encoding="ascii")
        if not re.fullmatch(r"[0-9a-f]{32}\n", owner):
            raise ValueError
        if engine != (
            "schema=lexiflow-engine-v1\n"
            f"endpoint={binding.endpoint}\n"
            f"daemon_id={binding.daemon_id}\n"
        ):
            raise ValueError
        state_lines = state.splitlines()
        if state_lines != [
            "schema=lexiflow-installation-v1",
            f"installation_id={owner.strip()}",
            "phase=stopped",
            "active=none",
            "previous=none",
            "candidate=none",
            "deleting=none",
            "resume=none",
        ] or not state.endswith("\n"):
            raise ValueError
        if (
            releases_path.is_symlink()
            or not releases_path.is_dir()
            or any(releases_path.iterdir())
        ):
            raise ValueError
    except (OSError, UnicodeError, ValueError):
        raise ConsumerError("FAIL", "release-installation-cleanup-failed") from None
    import shutil

    shutil.rmtree(install_root)


def _status(
    entry: Path, install_root: Path, env: Mapping[str, str]
) -> tuple[str, str, str, str]:
    from scripts.checks.release.source.check_synthetic_release_lifecycle import (
        _run_bounded,
    )

    output, _ = _run_bounded(
        ["sh", str(entry), "status", str(install_root)],
        entry.parent,
        env,
        30,
        limit=4096,
    )
    try:
        fields = output.decode("ascii").strip().split(" ")
    except UnicodeDecodeError:
        fields = []
    if len(fields) != 4:
        raise ConsumerError("FAIL", "product-lifecycle-status-invalid")
    return tuple(fields)  # type: ignore[return-value]


def _assert_runtime(
    binding: Any,
    docker_config: Path,
    install_root: Path,
    release_key: str,
    software_version: str,
    dataset_version: int,
    expected_api_image: str,
    expected_postgres_image: str,
    env: Mapping[str, str],
) -> dict[str, Any]:
    owner = (install_root / "owner").read_text(encoding="ascii").strip()
    project = f"lf_{owner}_{release_key[:16]}"
    listing = _docker(
        binding,
        docker_config,
        [
            "ps",
            "-a",
            "--no-trunc",
            "--filter",
            f"label=lexiflow.installation={owner}",
            "--filter",
            f"label=lexiflow.release={release_key}",
            "--format",
            "{{.ID}}",
        ],
        env,
    )
    ids = [item for item in listing.decode("ascii").splitlines() if item]
    if not ids or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids):
        raise ConsumerError("FAIL", "release-container-identity-missing")
    if len(ids) != 2:
        raise ConsumerError("FAIL", "release-container-set-mismatch")
    records = []
    for container_id in ids:
        raw = _docker(binding, docker_config, ["inspect", container_id], env)
        try:
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, list) or len(parsed) != 1:
                raise ValueError
            row = parsed[0]
            labels = row["Config"]["Labels"]
            if (
                row["Id"] != container_id
                or labels.get("lexiflow.installation") != owner
                or labels.get("lexiflow.release") != release_key
                or labels.get("com.docker.compose.project") != project
            ):
                raise ValueError
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ):
            raise ConsumerError("FAIL", "release-container-identity-invalid") from None
        records.append(
            {
                "id": row["Id"],
                "name": row["Name"],
                "image": row["Config"]["Image"],
                "imageId": row["Image"],
                "state": row["State"].get("Status"),
                "health": row["State"].get("Health", {}).get("Status"),
            }
        )
    api = [row for row in records if row["name"].rstrip("/").endswith("-api-1")]
    postgres = [
        row for row in records if row["name"].rstrip("/").endswith("-postgres-1")
    ]
    if (
        len(api) != 1
        or len(postgres) != 1
        or api[0]["imageId"] != expected_api_image
        or postgres[0]["imageId"] != expected_postgres_image
    ):
        raise ConsumerError("FAIL", "release-container-image-mismatch")
    if len(api) != 1 or api[0]["state"] != "running" or api[0]["health"] != "healthy":
        raise ConsumerError("FAIL", "release-api-container-unhealthy")
    if (
        len(postgres) != 1
        or postgres[0]["state"] != "running"
        or postgres[0]["health"] != "healthy"
    ):
        raise ConsumerError("FAIL", "release-postgres-container-unhealthy")
    probe = _docker(
        binding,
        docker_config,
        ["exec", api[0]["id"], "/app/run-container-command.sh", "health"],
        env,
    )
    try:
        health = json.loads(probe.decode("utf-8"))
        if (
            set(health)
            != {
                "softwareVersion",
                "apiContract",
                "mode",
                "ready",
                "reason",
                "datasetVersion",
            }
            or health["softwareVersion"] != software_version
            or health["apiContract"] != "caption-hints.v2"
            or health["mode"] != "formal"
            or health["ready"] is not True
            or health["reason"] not in {"OK", "PREWARM_DEGRADED"}
            or type(health["datasetVersion"]) is not int
            or health["datasetVersion"] != dataset_version
        ):
            raise ValueError
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise ConsumerError("FAIL", "release-api-health-mismatch") from None
    return {"containers": records, "apiHealth": health}


def execute_lifecycle(
    scratch: Path,
    evidence: Mapping[str, Any],
    binding: Any,
    environ: Mapping[str, str],
) -> dict[str, Any]:
    """通过发行入口执行verify、prepare、activate、recover、stop与delete。"""
    candidates = evidence.get("releaseCandidates")
    datasets = evidence.get("datasetRecords")
    if (
        not isinstance(candidates, list)
        or len(candidates) != 2
        or not isinstance(datasets, list)
        or len(datasets) != 2
    ):
        raise ConsumerError("FAIL", "release-candidate-not-assembled")
    if (
        datasets[0]["sha256"] == datasets[1]["sha256"]
        or datasets[0]["version"] == datasets[1]["version"]
    ):
        raise ConsumerError("FAIL", "synthetic-generations-not-distinct")
    platform = "linux/arm64"
    work = scratch / "runtime-arm64"
    work.mkdir(mode=0o700)
    temp = str(work)
    home = Path(temp) / "home"
    home.mkdir(mode=0o700)
    install_root = Path(temp) / "installation"
    docker_config = Path(temp) / "docker"
    docker_config.mkdir(mode=0o700)
    plugin_path, plugin_version, plugin_fingerprint = _compose_plugin(binding, environ)
    plugin_dir = Path(temp) / "compose-plugin"
    plugin_dir.mkdir(mode=0o700)
    (plugin_dir / "docker-compose").symlink_to(plugin_path)
    (docker_config / "config.json").write_text(
        json.dumps(
            {
                "auths": {},
                "cliPluginsExtraDirs": [str(plugin_dir)],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="ascii",
    )
    env = {
        "PATH": environ.get("PATH", os.defpath),
        "HOME": str(home),
        "LC_ALL": "C",
        "LANG": "C",
        "DOCKER_HOST": binding.endpoint,
        "DOCKER_CONFIG": str(docker_config),
    }
    _assert_compose_plugin_unchanged(plugin_path, plugin_fingerprint)
    try:
        version = _docker(binding, docker_config, ["compose", "version"], env)
        if not _compose_version_matches(version, plugin_version):
            raise ValueError
    except ConsumerError:
        raise
    except Exception:
        raise ConsumerError(
            "FAIL", "release-compose-private-config-unavailable"
        ) from None
    _assert_compose_plugin_unchanged(plugin_path, plugin_fingerprint)
    lifecycle = []
    entries = []
    keys = []
    for release in candidates:
        directory = Path(release["candidateDirectory"])
        payload = directory / "payload"
        entry = payload / "lexiflow.sh"
        if not entry.is_file() or entry.is_symlink():
            raise ConsumerError("FAIL", "release-entry-missing")
        if (
            hashlib.sha256((directory / "candidate.json").read_bytes()).hexdigest()
            != release["candidateSha256"]
        ):
            raise ConsumerError("FAIL", "release-candidate-hash-mismatch")
        entries.append(entry)
        keys.append(_release_key(entry))
    for entry in entries:
        _run(["sh", str(entry), "verify"], cwd=entry.parent, env=env)
        lifecycle.append("verify")
    if keys[0] == keys[1]:
        raise ConsumerError("FAIL", "synthetic-release-identities-not-distinct")
    manifest = json.loads(
        (entries[0].parent / "manifest.json").read_text(encoding="utf-8")
    )
    image_records = manifest.get("artifacts", [])
    api_record = [
        item
        for item in image_records
        if item.get("role") == "api-image" and item.get("platform") == platform
    ]
    db_record = [
        item
        for item in image_records
        if item.get("role") == "postgres-image" and item.get("platform") == platform
    ]
    if len(api_record) != 1 or len(db_record) != 1:
        raise ConsumerError("FAIL", "release-image-record-missing")
    expected_images = (
        api_record[0].get("imageDigest"),
        db_record[0].get("imageDigest"),
    )
    if not all(
        isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value)
        for value in expected_images
    ):
        raise ConsumerError("FAIL", "release-image-record-invalid")
    from ops.release.host_resources import (
        release_docker_preflight as release_docker_preflight,
    )

    def run_product(entry: Path, *args: str) -> bytes:
        check = release_docker_preflight.recheck(binding)
        if check.status != "PASS":
            raise ConsumerError("FAIL", "release-docker-binding-changed")
        return _run(["sh", str(entry), *args], cwd=entry.parent, env=env)

    run_product(entries[0], "prepare", str(install_root))
    lifecycle.append("prepare-first")
    status = _status(entries[0], install_root, env)
    if status != ("prepared", "none", "none", keys[0]):
        raise ConsumerError("FAIL", "release-first-prepare-state-mismatch")
    run_product(entries[0], "activate", str(install_root))
    lifecycle.append("activate-first")
    status = _status(entries[0], install_root, env)
    if status != ("idle", keys[0], "none", "none"):
        raise ConsumerError("FAIL", "release-first-activate-state-mismatch")
    first_health = _assert_runtime(
        binding,
        docker_config,
        install_root,
        keys[0],
        evidence["softwareVersion"],
        datasets[0]["version"],
        expected_images[0],
        expected_images[1],
        env,
    )
    run_product(entries[1], "prepare", str(install_root))
    lifecycle.append("prepare-second")
    status = _status(entries[1], install_root, env)
    if status != ("prepared", keys[0], "none", keys[1]):
        raise ConsumerError("FAIL", "release-second-prepare-state-mismatch")
    run_product(entries[1], "activate", str(install_root))
    lifecycle.append("activate-second")
    status = _status(entries[1], install_root, env)
    if status != ("idle", keys[1], keys[0], "none"):
        raise ConsumerError("FAIL", "release-second-activate-state-mismatch")
    second_health = _assert_runtime(
        binding,
        docker_config,
        install_root,
        keys[1],
        evidence["softwareVersion"],
        datasets[1]["version"],
        expected_images[0],
        expected_images[1],
        env,
    )
    run_product(entries[1], "recover", str(install_root))
    lifecycle.append("recover-first")
    status = _status(entries[0], install_root, env)
    if status != ("idle", keys[0], keys[1], "none"):
        raise ConsumerError("FAIL", "release-recovery-state-mismatch")
    recovered_health = _assert_runtime(
        binding,
        docker_config,
        install_root,
        keys[0],
        evidence["softwareVersion"],
        datasets[0]["version"],
        expected_images[0],
        expected_images[1],
        env,
    )
    run_product(entries[0], "stop", str(install_root))
    lifecycle.append("stop")
    status = _status(entries[0], install_root, env)
    if status != ("stopped", keys[0], keys[1], "none"):
        raise ConsumerError("FAIL", "release-stop-state-mismatch")
    run_product(
        entries[0], "delete", str(install_root), keys[1], "--confirm-delete-data"
    )
    run_product(
        entries[0], "delete", str(install_root), keys[0], "--confirm-delete-data"
    )
    lifecycle.append("delete-both-generations")
    _remove_owned_installation_root(install_root, binding)
    result = {
        "platform": platform,
        "daemonId": binding.daemon_id,
        "states": lifecycle,
        "firstHealth": first_health,
        "secondHealth": second_health,
        "recoveredHealth": recovered_health,
    }
    return {
        "platforms": [result],
        "lifecycleChecks": len(result["states"]),
        "hostPortVerified": False,
        "realChromeVerified": False,
    }
