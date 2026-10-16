"""为release runtime检查构建隔离的本机构件输入。"""

from __future__ import annotations

import hashlib
import json
import re
import os
import subprocess
from pathlib import Path
from typing import Mapping

from scripts.checks.release.source.check_synthetic_release_lifecycle import (
    ConsumerError,
)


def _run(
    argv: list[str], *, cwd: Path, env: Mapping[str, str], timeout: int = 1800
) -> subprocess.CompletedProcess[bytes]:
    """Run one fixed executable vector with bounded concurrent output collection."""
    from scripts.checks.release.source.check_synthetic_release_lifecycle import (
        _run_bounded,
    )

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
    _run(
        ["node", "scripts/packaging/package-extension-release.mjs"],
        cwd=repo / "extension",
        env=base,
    )
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
        "fixtures": fixtures,
        "artifact_root": artifact_root,
        "producer": report,
    }


def _source_identity(repo: Path, env: Mapping[str, str]) -> tuple[str, str]:
    """消费共享 Node 身份入口，不以基础版本文件推断制品路径。"""
    result = _run(
        ["node", "ops/release/integrity/source-build-identity.mjs", "--release"],
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
    repo: Path, scratch: Path, env: Mapping[str, str], binding: object
) -> dict[str, object]:
    """单次匿名 stdin 桥事务产生真实镜像与两代候选。"""
    from scripts.checks.release.source.check_synthetic_release_lifecycle import (
        _run_bounded,
        _pairs,
        _reject_constant,
    )
    from ops.release.host_resources import (
        release_docker_preflight as release_docker_preflight,
    )
    import tempfile

    inputs = build_local_inputs(repo, scratch, env)
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
    version, commit = _source_identity(repo, pipeline_env)
    sources = (
        (
            "api.jar",
            repo / "backend/product/api/build/internal/boot/api-internal.jar",
            1024**3,
        ),
        (
            "extension.zip",
            repo
            / "tmp/releases"
            / version
            / commit
            / f"lexiflow-extension-{version}.zip",
            256 * 1024**2,
        ),
    )
    for name, source, maximum in sources:
        data = _read_regular(source, maximum)
        with (Path(inputs["artifact_root"]) / name).open("xb") as output:
            output.write(data)
    check = release_docker_preflight.recheck(binding)
    if check.status != "PASS":
        raise ConsumerError("FAIL", "release-docker-binding-changed")
    rows = inputs["producer"]["datasets"]
    request = {
        "scratchDirectory": str(scratch),
        "endpoint": binding.endpoint,
        "datasets": rows,
    }
    with tempfile.TemporaryFile() as stream:
        stream.write(
            json.dumps(request, separators=(",", ":"), ensure_ascii=False).encode()
        )
        stream.seek(0)
        stdout, _ = _run_bounded(
            [
                "node",
                str(
                    repo
                    / "ops/release/test_tools/build-synthetic-release-candidates.mjs"
                ),
            ],
            repo,
            pipeline_env,
            3600,
            input_stream=stream,
        )
    try:
        result = json.loads(
            stdout, object_pairs_hook=_pairs, parse_constant=_reject_constant
        )
        if (
            not isinstance(result, dict)
            or set(result)
            != {"candidates", "datasets", "softwareVersion", "apiContract"}
            or result["softwareVersion"] != version
            or result["apiContract"] != "caption-hints.v2"
            or result["datasets"]
            != [
                {"version": row["datasetVersion"], "sha256": row["sha256"]}
                for row in rows
            ]
            or not isinstance(result["candidates"], list)
            or len(result["candidates"]) != 2
        ):
            raise ValueError
        directories = set()
        for candidate in result["candidates"]:
            if (
                not isinstance(candidate, dict)
                or set(candidate) != {"candidateDirectory", "candidateSha256"}
                or not isinstance(candidate["candidateDirectory"], str)
                or not isinstance(candidate["candidateSha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", candidate["candidateSha256"])
            ):
                raise ValueError
            directory = Path(candidate["candidateDirectory"])
            parent = scratch / "synthetic-releases"
            if (
                not directory.is_absolute()
                or directory.is_symlink()
                or not directory.is_dir()
                or parent.is_symlink()
                or directory.parent != parent
                or directory.resolve().parent != parent
                or directory in directories
            ):
                raise ValueError
            directories.add(directory)
            marker = _read_regular(directory / "candidate.json", 2_000_000)
            if hashlib.sha256(marker).hexdigest() != candidate["candidateSha256"]:
                raise ValueError
            value = json.loads(
                marker, object_pairs_hook=_pairs, parse_constant=_reject_constant
            )
            if (
                value.get("kind") != "lexiflow-release-candidate"
                or value.get("softwareVersion") != version
                or value.get("sourceCommit") != commit
                or not isinstance(value.get("buildIdentity"), dict)
                or value["buildIdentity"].get("softwareVersion") != version
                or value["buildIdentity"].get("sourceCommit") != commit
                or value["buildIdentity"].get("dirty") is not False
            ):
                raise ValueError
    except (ValueError, KeyError, TypeError):
        raise ConsumerError("FAIL", "synthetic-bridge-result-invalid") from None
    return {
        "status": "PASS",
        "releaseCandidates": result["candidates"],
        "synthetic": True,
        "formalReleaseEligible": False,
        "datasetRecords": result["datasets"],
        "softwareVersion": version,
        "apiContract": result["apiContract"],
    }
