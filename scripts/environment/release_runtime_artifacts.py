"""为release runtime检查构建隔离的本机构件输入。"""

from __future__ import annotations

import hashlib
import json
import re
import os
import subprocess
from pathlib import Path
from typing import Mapping

from scripts.environment.release_runtime_check import ConsumerError


def _run(
    argv: list[str], *, cwd: Path, env: Mapping[str, str], timeout: int = 1800
) -> subprocess.CompletedProcess[bytes]:
    """Run one fixed executable vector with bounded concurrent output collection."""
    from scripts.environment.release_runtime_check import _run_bounded

    stdout, stderr = _run_bounded(argv, cwd, env, timeout, limit=4_194_304)
    return subprocess.CompletedProcess(argv, 0, stdout, stderr)


def _read_regular(path: Path, maximum: int) -> bytes:
    """以固定上限和no-follow方式读取自有构件。"""
    import os
    import stat

    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size < 1
            or before.st_size > maximum
        ):
            raise ConsumerError("FAIL", "release-artifact-invalid")
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
            ) != (before.st_dev, before.st_ino, before.st_size):
                raise ConsumerError("FAIL", "release-artifact-invalid")
            chunks = []
            remaining = maximum + 1
            while remaining:
                chunk = os.read(fd, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) != before.st_size or len(data) > maximum:
                raise ConsumerError("FAIL", "release-artifact-invalid")
            return data
        finally:
            os.close(fd)
    except ConsumerError:
        raise
    except OSError:
        raise ConsumerError("FAIL", "release-artifact-invalid") from None


def build_local_inputs(
    repo: Path, scratch: Path, environ: Mapping[str, str]
) -> dict[str, object]:
    """构建 API 与扩展，并运行真实合成资料发布和导出生产者。"""
    home = scratch / "home"
    gradle_home = scratch / "gradle-home"
    npm_cache = Path(environ.get("LEXIFLOW_RELEASE_NPM_CACHE", ""))
    gradle_cache = Path(environ.get("LEXIFLOW_RELEASE_GRADLE_CACHE", ""))
    fixture_parent = scratch / "producer-output"
    fixtures = fixture_parent / "synthetic-datasets"
    artifact_root = scratch / "artifacts"
    for directory in (home, gradle_home, fixture_parent, artifact_root):
        directory.mkdir(mode=0o700)
    base = {
        key: environ[key] for key in ("PATH", "LANG", "LC_ALL", "TZ") if key in environ
    }
    base.update(
        {
            "HOME": str(home),
            "GRADLE_USER_HOME": str(gradle_home),
            "npm_config_cache": str(npm_cache),
            "npm_config_offline": "true",
            "npm_config_userconfig": str(home / "npmrc"),
            "npm_config_globalconfig": os.devnull,
            "GRADLE_OPTS": "-Dorg.gradle.daemon=false -Dorg.gradle.caching=false",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    if environ.get("JAVA_HOME"):
        base["JAVA_HOME"] = environ["JAVA_HOME"]
        base["PATH"] = os.pathsep.join(
            [str(Path(environ["JAVA_HOME"]) / "bin"), base.get("PATH", os.defpath)]
        )
    (home / "npmrc").write_text(
        "offline=true\nignore-scripts=false\n", encoding="ascii"
    )
    # Expose only the Gradle dependency and wrapper caches in a fresh user home; user init scripts are excluded.
    source_modules = gradle_cache / "caches/modules-2"
    source_wrapper = gradle_cache / "wrapper/dists"
    if not source_modules.is_dir() or not source_wrapper.is_dir():
        raise ConsumerError("BLOCKED", "release-gradle-cache-incomplete")
    (gradle_home / "caches").mkdir(mode=0o700)
    (gradle_home / "wrapper").mkdir(mode=0o700)
    os.symlink(source_modules, gradle_home / "caches/modules-2")
    os.symlink(source_wrapper, gradle_home / "wrapper/dists")
    _run(
        [str(repo / "backend/gradlew"), "--offline", "--no-daemon", ":api:bootJar"],
        cwd=repo / "backend",
        env=base,
    )
    _run(
        ["npm", "ci", "--offline", "--cache", str(npm_cache)],
        cwd=repo / "extension",
        env=base,
    )
    _run(["npm", "run", "build", "--offline"], cwd=repo / "extension", env=base)
    _run(["node", "scripts/release.mjs"], cwd=repo / "extension", env=base)
    jdbc = environ.get("LEXIFLOW_POSTGRES_TEST_JDBC_URL", "").strip()
    if not jdbc:
        raise ConsumerError("BLOCKED", "missing-isolated-postgres-test-jdbc-url")
    producer_env = dict(base)
    producer_env["LEXIFLOW_POSTGRES_TEST_JDBC_URL"] = jdbc
    _run(
        [
            str(repo / "backend/gradlew"),
            "--offline",
            "--no-daemon",
            "-Prelease=true",
            "-Dlexiflow.release.fixture.output=" + str(fixtures),
            ":integration-tests:produceSyntheticReleaseDatasets",
        ],
        cwd=repo / "backend",
        env=producer_env,
    )
    report_path = fixtures / "result.json"
    report = json.loads(_read_regular(report_path, 64 * 1024).decode("utf-8"))
    if (
        set(report)
        != {"schemaVersion", "synthetic", "formalReleaseEligible", "datasets"}
        or type(report["schemaVersion"]) is not int
        or report["schemaVersion"] != 1
        or report["synthetic"] is not True
        or report["formalReleaseEligible"] is not False
        or not isinstance(report["datasets"], list)
        or len(report["datasets"]) != 2
    ):
        raise ConsumerError("FAIL", "synthetic-producer-result-invalid")
    expected_names = {"dataset-a.zip", "dataset-b.zip"}
    if {
        row.get("file") for row in report["datasets"] if isinstance(row, dict)
    } != expected_names:
        raise ConsumerError("FAIL", "synthetic-producer-result-invalid")
    for row in report["datasets"]:
        if (
            not isinstance(row, dict)
            or set(row)
            != {"file", "bytes", "sha256", "datasetVersion", "preparationPolicy"}
            or type(row["bytes"]) is not int
            or type(row["datasetVersion"]) is not int
            or row["file"] not in expected_names
            or not isinstance(row["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
        ):
            raise ConsumerError("FAIL", "synthetic-producer-result-invalid")
        archive = fixtures / row["file"]
        content = _read_regular(archive, 64 * 1024 * 1024)
        if (
            len(content) != row["bytes"]
            or hashlib.sha256(content).hexdigest() != row["sha256"]
            or row["preparationPolicy"] != "lexiflow.deterministic-preparation.v1"
        ):
            raise ConsumerError("FAIL", "synthetic-producer-content-invalid")
    if (
        report["datasets"][0]["datasetVersion"]
        == report["datasets"][1]["datasetVersion"]
        or report["datasets"][0]["sha256"] == report["datasets"][1]["sha256"]
    ):
        raise ConsumerError("FAIL", "synthetic-generations-not-distinct")
    return {
        "home": home,
        "gradle_home": gradle_home,
        "fixtures": fixtures,
        "fixture_root": repo,
        "artifact_root": artifact_root,
        "producer": report,
    }


def _source_identity(repo: Path, env: Mapping[str, str]) -> tuple[str, str]:
    """消费共享 Node 身份入口，不以基础版本文件推断制品路径。"""
    result = _run(
        ["node", "ops/release/version.mjs", "--release"],
        cwd=repo,
        env=env,
        timeout=15,
    )
    try:
        identity = json.loads(result.stdout)
        version = identity["softwareVersion"]
        commit = identity["sourceCommit"]
        if (
            set(identity) != {"softwareVersion", "sourceCommit"}
            or not isinstance(version, str)
            or not re.fullmatch(r"[0-9][A-Za-z0-9.-]{0,199}", version)
            or not isinstance(commit, str)
            or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", commit)
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise ConsumerError("FAIL", "release-source-identity-invalid") from None
    return version, commit


def produce_candidates(
    repo: Path, scratch: Path, env: Mapping[str, str], bindings: tuple[object, ...]
) -> dict[str, object]:
    """按冻结平台集合构建原生镜像候选并组装两代合成发布包。"""
    from scripts.environment.release_runtime_check import _run_json_command

    inputs = build_local_inputs(repo, scratch, env)
    fixture = Path(inputs["fixture_root"])
    pipeline_env = {
        "PATH": env.get("PATH", os.defpath),
        "HOME": str(inputs["home"]),
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    artifact_root = Path(inputs["artifact_root"])
    output_parent = scratch / "pipeline-output"
    output_parent.mkdir(mode=0o700)
    version, commit = _source_identity(fixture, pipeline_env)
    jar = fixture / "backend/product/api/build/libs" / f"api-{version}.jar"
    if not jar.is_file() or jar.is_symlink():
        raise ConsumerError("FAIL", "api-bootjar-output-missing")
    extension_dir = fixture / "tmp/releases" / version / commit
    extension_zip = extension_dir / f"lexiflow-extension-{version}.zip"
    if not extension_zip.is_file() or extension_zip.is_symlink():
        raise ConsumerError("FAIL", "extension-release-output-missing")
    import hashlib

    for name, source, maximum in (
        ("api.jar", jar, 1024 * 1024 * 1024),
        ("extension.zip", extension_zip, 256 * 1024 * 1024),
        ("compose.yaml", fixture / "ops/docker/compose.yaml", 1_000_000),
        ("schema.sql", fixture / "infra/postgres/schema.sql", 16 * 1024 * 1024),
    ):
        data = _read_regular(source, maximum)
        with (artifact_root / name).open("xb") as output:
            output.write(data)
    dataset_records = inputs["producer"]["datasets"]
    candidates = []
    lock = json.loads(
        (fixture / "ops/docker/base-images.json").read_text(encoding="utf-8")
    )
    platforms = tuple(binding.target_platform for binding in bindings)
    for platform, binding in zip(platforms, bindings, strict=True):
        request = {
            "repoRoot": str(fixture),
            "artifactRoot": str(artifact_root),
            "descriptor": {
                "schemaVersion": 1,
                "softwareVersion": version,
                "sourceCommit": commit,
                "jar": {
                    "path": "api.jar",
                    "bytes": (artifact_root / "api.jar").stat().st_size,
                    "sha256": hashlib.sha256(
                        _read_regular(artifact_root / "api.jar", 1024 * 1024 * 1024)
                    ).hexdigest(),
                },
                "baseImages": [
                    item
                    for item in lock["baseImages"]
                    if item.get("platform") == platform
                ],
            },
            "outputParent": str(output_parent),
            "endpoint": binding.endpoint,
            "platform": platform,
        }
        request_path = scratch / f"images-{platform.split('/')[1]}.json"
        request_path.write_text(
            json.dumps(request, separators=(",", ":")), encoding="utf-8"
        )
        candidates.append(
            _run_json_command(
                [
                    "node",
                    str(fixture / "ops/release/pipeline.mjs"),
                    "images",
                    str(request_path),
                ],
                fixture,
                pipeline_env,
                1200,
                output_parent,
            )
        )
    # Build a test-only descriptor from actual generated bytes; license labels make no redistribution claim.
    ext_bytes = _read_regular(artifact_root / "extension.zip", 256 * 1024 * 1024)
    compose_bytes = _read_regular(artifact_root / "compose.yaml", 1_000_000)
    sql_bytes = _read_regular(artifact_root / "schema.sql", 16 * 1024 * 1024)
    artifact_records: list[dict[str, object]] = []
    licenses: list[dict[str, object]] = []
    components = [
        ("lexiflow", "LexiFlow"),
        ("ext-third-party", "extension-third-party"),
        ("api-runtime", "API-runtime"),
        ("postgresql", "PostgreSQL"),
        ("dataset-license", "dataset"),
    ]
    for license_id, component in components:
        notice_path = f"licenses/{license_id}.txt"
        notice = f"Internal synthetic runtime acceptance only; no redistribution authorization. Component: {component}.\n".encode()
        target = artifact_root / notice_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(notice)
        licenses.append(
            {
                "id": license_id,
                "component": component,
                "licenseId": "UNVERIFIED-SYNTHETIC-ONLY",
                "licenseName": "Internal synthetic test only; no redistribution authorization",
                "sourceUrl": "https://example.invalid/synthetic/runtime-check",
                "noticePath": notice_path,
                "noticeBytes": len(notice),
                "noticeSha256": hashlib.sha256(notice).hexdigest(),
            }
        )
        artifact_records.append(
            {
                "role": "license",
                "path": notice_path,
                "bytes": len(notice),
                "sha256": hashlib.sha256(notice).hexdigest(),
                "licenseIds": [license_id],
            }
        )

    def add(
        role: str,
        path: str,
        data: bytes,
        licenses_for: list[str],
        metadata: dict[str, object] | None = None,
    ) -> None:
        target = artifact_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        record: dict[str, object] = {
            "role": role,
            "path": path,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "licenseIds": licenses_for,
        }
        if metadata is not None:
            record["metadata"] = metadata
        artifact_records.append(record)

    build_identity = None
    for expected_platform, candidate in zip(platforms, candidates, strict=True):
        candidate_dir = Path(candidate["candidateDirectory"])
        marker = _read_regular(candidate_dir / "candidate.json", 2_000_000)
        if hashlib.sha256(marker).hexdigest() != candidate["candidateSha256"]:
            raise ConsumerError("FAIL", "image-candidate-hash-mismatch")
        record = json.loads(marker.decode("utf-8"))
        if (
            record.get("schemaVersion") != 1
            or record.get("kind") != "lexiflow-image-candidate"
            or record.get("platform") != expected_platform
            or not isinstance(record.get("artifacts"), list)
            or len(record["artifacts"]) != 2
        ):
            raise ConsumerError("FAIL", "image-candidate-invalid")
        # 完整 schema 与派生一致性仍由 Node assembler 复核，此处只传递已绑定 marker 的身份。
        identity = record.get("buildIdentity")
        if (
            not isinstance(identity, dict)
            or identity.get("softwareVersion") != version
            or identity.get("sourceCommit") != commit
            or identity.get("dirty") is not False
            or (build_identity is not None and identity != build_identity)
        ):
            raise ConsumerError("FAIL", "image-candidate-identity-mismatch")
        build_identity = identity
        expected_roles = {
            "api-image": f"images/{expected_platform.replace('/', '-')}/api-image.tar",
            "postgres-image": f"images/{expected_platform.replace('/', '-')}/postgres-image.tar",
        }
        seen_roles = set()
        for item in record["artifacts"]:
            if (
                not isinstance(item, dict)
                or set(item)
                != {"role", "platform", "path", "bytes", "sha256", "imageDigest"}
                or item.get("platform") != expected_platform
                or item.get("path") != expected_roles.get(item.get("role"))
                or item.get("role") in seen_roles
                or type(item.get("bytes")) is not int
                or not isinstance(item.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or not isinstance(item.get("imageDigest"), str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["imageDigest"])
            ):
                raise ConsumerError("FAIL", "image-candidate-invalid")
            seen_roles.add(item["role"])
            source = candidate_dir / expected_roles[item["role"]]
            data = _read_regular(source, 1024 * 1024 * 1024)
            if (
                len(data) != item["bytes"]
                or hashlib.sha256(data).hexdigest() != item["sha256"]
            ):
                raise ConsumerError("FAIL", "image-candidate-artifact-mismatch")
            target = artifact_root / expected_roles[item["role"]]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            artifact_records.append(
                {
                    **item,
                    "licenseIds": ["lexiflow", "api-runtime"]
                    if item["role"] == "api-image"
                    else ["postgresql"],
                }
            )
    add("compose", "compose.yaml", compose_bytes, ["lexiflow"])
    add("sql", "schema.sql", sql_bytes, ["lexiflow"])
    add(
        "extension",
        "extension.zip",
        ext_bytes,
        ["lexiflow", "ext-third-party"],
        {"softwareVersion": version, "sourceCommit": commit},
    )
    release_candidates = []
    for index, row in enumerate(dataset_records):
        data = _read_regular(Path(inputs["fixtures"]) / row["file"], 64 * 1024 * 1024)
        release_id = "synthetic-" + row["sha256"]
        preparation_id = (
            "synthetic-" + hashlib.sha256(row["preparationPolicy"].encode()).hexdigest()
        )
        synthetic_dataset = {
            "releaseId": release_id,
            "preparationId": preparation_id,
            "ruleId": "synthetic-no-model",
        }
        data_path = f"dataset/{row['file']}"
        data_artifact = {
            "role": "dataset",
            "path": data_path,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "licenseIds": ["dataset-license"],
            "metadata": {
                **synthetic_dataset,
                "sqlVersion": hashlib.sha256(sql_bytes).hexdigest(),
            },
        }
        (artifact_root / data_path).parent.mkdir(parents=True, exist_ok=True)
        (artifact_root / data_path).write_bytes(data)
        descriptor = {
            "schemaVersion": 1,
            "buildIdentity": build_identity,
            "softwareVersion": version,
            "sourceCommit": commit,
            "apiContract": "caption-hints.v1",
            "sqlVersion": hashlib.sha256(sql_bytes).hexdigest(),
            "dataset": synthetic_dataset,
            "platforms": list(platforms),
            "artifacts": [*artifact_records, data_artifact],
            "licenses": licenses,
        }
        request = {
            "repoRoot": str(fixture),
            "imageCandidates": [
                {
                    "directory": str(Path(candidate["candidateDirectory"])),
                    "sha256": candidate["candidateSha256"],
                }
                for candidate in candidates
            ],
            "artifactRoot": str(artifact_root),
            "descriptor": descriptor,
            "outputParent": str(output_parent),
        }
        req_path = scratch / f"assemble-{index}.json"
        req_path.write_text(
            json.dumps(request, separators=(",", ":")), encoding="utf-8"
        )
        release_candidates.append(
            _run_json_command(
                [
                    "node",
                    str(fixture / "ops/release/pipeline.mjs"),
                    "assemble",
                    str(req_path),
                ],
                fixture,
                pipeline_env,
                1200,
                output_parent,
            )
        )
    return {
        "status": "PASS",
        "releaseCandidates": release_candidates,
        "synthetic": True,
        "formalReleaseEligible": False,
        "datasetRecords": [
            {"version": row["datasetVersion"], "sha256": row["sha256"]}
            for row in dataset_records
        ],
        "softwareVersion": version,
        "apiContract": "caption-hints.v1",
        "imageCandidates": candidates,
        "artifacts": str(artifact_root),
    }
