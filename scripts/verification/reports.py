"""以不可变方式发布不含 Task 语义的 Verification 报告。"""

from __future__ import annotations
import hashlib, json, os, stat, uuid
from pathlib import Path, PurePosixPath
from typing import Any


def canonical_bytes(v: Any) -> bytes:
    return json.dumps(
        v, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def sha256_bytes(v: bytes) -> str:
    return hashlib.sha256(v).hexdigest()


def _safe_uuid(value: Any) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise ValueError("report run_id must be UUID") from None
    if str(parsed) != str(value):
        raise ValueError("report run_id must be canonical UUID")
    return str(parsed)


_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _parts(relative: str) -> tuple[str, ...]:
    p = PurePosixPath(relative)
    if not relative or p.is_absolute() or any(x in ("", ".", "..") for x in p.parts):
        raise ValueError("unsafe report path")
    return p.parts


def _open_directory(root: Path, parts: tuple[str, ...], *, create=False) -> int:
    try:
        fd = os.open(root, _DIR_FLAGS)
    except OSError as exc:
        raise ValueError(f"unsafe report path: {exc}") from None
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ValueError(f"unsafe report path: {exc}") from None
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                raise ValueError("verification report missing") from None
            except OSError as exc:
                raise ValueError(f"unsafe report path: {exc}") from None
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                os.close(child)
                raise ValueError("unsafe report path")
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(root: Path, relative: str) -> bytes:
    parts = _parts(relative)
    dir_fd = _open_directory(root, parts[:-1])
    try:
        try:
            fd = os.open(
                parts[-1],
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=dir_fd,
            )
        except FileNotFoundError:
            raise ValueError("verification report missing") from None
        except OSError as exc:
            raise ValueError(f"unsafe verification report: {exc}") from None
    finally:
        os.close(dir_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("unsafe verification report")
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate verification report key: {key}")
        value[key] = item
    return value


def _descriptor_current(root: Path, descriptor: Any, run_id: str) -> bool:
    if not isinstance(descriptor, dict) or set(descriptor) != {
        "locator",
        "sha256",
        "bytes",
    }:
        return False
    locator, digest, size = (
        descriptor.get("locator"),
        descriptor.get("sha256"),
        descriptor.get("bytes"),
    )
    if (
        not isinstance(locator, str)
        or not locator.startswith(f"tmp/quality/verification/{run_id}/")
        or not isinstance(digest, str)
        or len(digest) != 64
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
    ):
        return False
    try:
        data = _read(root, locator)
    except ValueError:
        return False
    return len(data) == size and sha256_bytes(data) == digest


def _snapshot_shape(value: Any) -> bool:
    if (
        not isinstance(value, dict)
        or set(value) != {"fingerprint", "files", "missing"}
        or not isinstance(value.get("fingerprint"), str)
        or len(value["fingerprint"]) != 64
        or not isinstance(value.get("files"), list)
        or not isinstance(value.get("missing"), list)
    ):
        return False
    if any(
        not isinstance(x, dict)
        or set(x) != {"locator", "sha256"}
        or not isinstance(x["locator"], str)
        or not isinstance(x["sha256"], str)
        or len(x["sha256"]) != 64
        for x in value["files"]
    ):
        return False
    return all(isinstance(x, str) and x for x in value["missing"])


def _pass_check_current(
    root: Path, result: Any, expected: dict[str, Any], prior_ids: set[str], run_id: str
) -> bool:
    """复核已报告 PASS 的 Check 所绑定的声明和输入是否仍与当前快照一致；漂移不能沿用旧 PASS。"""
    if (
        not isinstance(result, dict)
        or set(result)
        != {
            "check_id",
            "module",
            "status",
            "reason",
            "process",
            "input_snapshot",
            "consumed_inputs",
            "result_contract",
            "environment",
        }
        or result.get("check_id") != expected["check_id"]
        or result.get("module") != expected["module"]
        or result.get("status") != "PASS"
        or result.get("reason") not in ("", "equivalent-check-deduplicated")
    ):
        return False
    process = result.get("process")
    snapshots = result.get("input_snapshot")
    environment = result.get("environment")
    if not isinstance(process, dict) or set(process) not in (
        {
            "executed_argv",
            "exit_code",
            "exit_reason",
            "duration_seconds",
            "timed_out",
            "started_at",
            "finished_at",
            "output_artifacts",
        },
        {
            "executed_argv",
            "exit_code",
            "exit_reason",
            "duration_seconds",
            "timed_out",
            "started_at",
            "finished_at",
            "output_artifacts",
            "deduplicated_from",
        },
    ):
        return False
    if (
        not isinstance(snapshots, dict)
        or set(snapshots) != {"pre", "before_execution", "post", "final"}
        or not isinstance(environment, dict)
        or set(environment) != {"status", "missing", "details"}
        or environment.get("status") != "PASS"
        or environment.get("missing") != []
        or not isinstance(environment.get("details"), dict)
    ):
        return False
    consumed = result.get("consumed_inputs")
    if (
        not isinstance(consumed, dict)
        or set(consumed) != {"pre", "post"}
        or any(not isinstance(consumed[x], list) for x in ("pre", "post"))
        or any(
            item.get("status") != "verified"
            for x in ("pre", "post")
            for item in consumed[x]
            if isinstance(item, dict)
        )
        or any(
            not isinstance(item, dict) for x in ("pre", "post") for item in consumed[x]
        )
    ):
        return False
    if (
        isinstance(process.get("duration_seconds"), bool)
        or not isinstance(process.get("duration_seconds"), (int, float))
        or process["duration_seconds"] < 0
        or not all(
            isinstance(process.get(x), str) and process[x]
            for x in ("started_at", "finished_at")
        )
    ):
        return False
    reason = process.get("exit_reason")
    if reason == "exited":
        if (
            type(process.get("exit_code")) is not int
            or process["exit_code"] != 0
            or process.get("timed_out") is not False
            or not isinstance(process.get("executed_argv"), list)
            or not process["executed_argv"]
        ):
            return False
    elif reason == "deduplicated":
        if (
            process.get("exit_code") != 0
            or process.get("timed_out") is not False
            or process.get("executed_argv") != []
            or process.get("deduplicated_from") not in prior_ids
        ):
            return False
    else:
        return False
    artifacts = process.get("output_artifacts")
    if (
        not isinstance(artifacts, dict)
        or set(artifacts) != {"stdout", "stderr"}
        or any(not _descriptor_current(root, x, run_id) for x in artifacts.values())
    ):
        return False
    try:
        pre, before, post, final = (
            snapshots[x] for x in ("pre", "before_execution", "post", "final")
        )
        if any(not _snapshot_shape(x) for x in (pre, before, post, final)):
            return False
        if pre["fingerprint"] != final["fingerprint"]:
            return False
        if (
            reason == "exited"
            and len(
                {
                    pre["fingerprint"],
                    before["fingerprint"],
                    post["fingerprint"],
                    final["fingerprint"],
                }
            )
            != 1
        ):
            return False
        from scripts.verification.kernel import snapshot_check_inputs

        if snapshot_check_inputs(root, expected)["fingerprint"] != final["fingerprint"]:
            return False
    except (KeyError, TypeError, ValueError, OSError):
        return False
    return isinstance(result.get("result_contract"), dict) and bool(
        result["result_contract"]
    )


def _validate_pass_report(root: Path, report: dict[str, Any]) -> None:
    """发布前核对 PASS 报告的范围、Check 完整性、输入指纹与证据；不为缺失检查补造成功结果。"""
    from scripts.verification.declarations import load_declarations_snapshot
    from scripts.verification.kernel import fingerprint_json
    from scripts.verification.scope import (
        resolve_module_dependencies,
        select_checks_for_changes,
    )
    from scripts.verification.scenarios import (
        _runtime_checks,
        _select_repository_checks,
    )

    try:
        declarations, declaration = load_declarations_snapshot(root)
    except Exception as exc:
        raise ValueError(
            f"verification report declaration unavailable: {exc}"
        ) from None
    if report.get("declaration_sha256") != declaration["sha256"]:
        raise ValueError("verification report declaration drift")
    scope = report.get("scope")
    try:
        if scope == "change-targeted":
            base = report.get("base")
            if not isinstance(base, str) or not base:
                raise ValueError("verification report base missing")
            review = report.get("scope_review")
            changed = review.get("changed_files") if isinstance(review, dict) else None
            if not isinstance(changed, list) or any(
                not isinstance(x, str) or not x for x in changed
            ):
                raise ValueError("verification report changed-file set invalid")
            selected = (
                select_checks_for_changes(declarations["checks"], changed)
                if changed
                else list(declarations["checks"])
            )
            selected = resolve_module_dependencies(selected, declarations["checks"])
        elif scope == "repository-baseline":
            required = report.get("required_check_ids")
            if not isinstance(required, list) or any(
                not isinstance(x, str) or not x for x in required
            ):
                raise ValueError("verification report required checks invalid")
            selected, _baseline, partial = _select_repository_checks(
                declarations["checks"], None, tuple(required)
            )
            if (
                partial
                or report.get("scope_review", {}).get("kind") != "full-repository"
            ):
                raise ValueError("verification report repository scope incomplete")
        else:
            raise ValueError("verification report scope invalid")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"verification report selection invalid: {exc}") from None
    runtime_checks = _runtime_checks(selected)
    checks = report.get("checks")
    if (
        not isinstance(checks, list)
        or not checks
        or [x.get("check_id") for x in checks if isinstance(x, dict)]
        != [x["check_id"] for x in runtime_checks]
    ):
        raise ValueError("verification report check coverage invalid")
    configuration = fingerprint_json(
        {"declaration_sha256": declaration["sha256"], "selected_checks": runtime_checks}
    )
    if report.get("configuration_fingerprint") != configuration:
        raise ValueError("verification report configuration fingerprint invalid")
    prior = set()
    for result, expected in zip(checks, runtime_checks, strict=True):
        if not _pass_check_current(root, result, expected, prior, report["run_id"]):
            raise ValueError(
                f"verification report check invalid: {expected['check_id']}"
            )
        prior.add(expected["check_id"])
    executed = sorted(
        x["check_id"]
        for x in checks
        if x.get("process", {}).get("exit_reason") != "not-run"
    )
    if (
        report.get("executed_check_ids") != executed
        or report.get("coverage_gaps") != []
        or report.get("reason") != ""
    ):
        raise ValueError("verification report PASS completeness invalid")
    snapshots = [
        {
            "check_id": x["check_id"],
            "pre": x["input_snapshot"]["pre"],
            "before_execution": x["input_snapshot"]["before_execution"],
            "post": x["input_snapshot"]["post"],
            "final": x["input_snapshot"]["final"],
        }
        for x in checks
    ]
    if report.get("input_fingerprint") != fingerprint_json(
        {"configuration_fingerprint": configuration, "snapshots": snapshots}
    ):
        raise ValueError("verification report input fingerprint invalid")
    if scope == "repository-baseline":
        frozen = fingerprint_json(
            {
                "declaration_sha256": declaration["sha256"],
                "checks": runtime_checks,
                "input_snapshots": {
                    x["check_id"]: result["input_snapshot"]["pre"]
                    for x, result in zip(runtime_checks, checks, strict=True)
                },
            }
        )
        if report.get("frozen_input_fingerprint") != frozen:
            raise ValueError("verification report frozen fingerprint invalid")


def validate_report(root: Path, report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ValueError("verification report must be an object")
    required = {
        "schema_version",
        "run_id",
        "result",
        "reason",
        "scope",
        "checks",
        "input_fingerprint",
        "configuration_fingerprint",
        "coverage_gaps",
        "scope_review",
    }
    if (
        not required.issubset(report)
        or report.get("schema_version") != "lexiflow.verification-report.v1"
    ):
        raise ValueError("verification report schema invalid")
    _safe_uuid(report.get("run_id"))
    if (
        report.get("result") not in {"PASS", "BLOCKED", "FAIL"}
        or report.get("scope") not in {"change-targeted", "repository-baseline"}
        or not isinstance(report.get("checks"), list)
        or not isinstance(report.get("coverage_gaps"), list)
        or not isinstance(report.get("scope_review"), dict)
    ):
        raise ValueError("verification report fields invalid")
    if report["result"] == "PASS":
        _validate_pass_report(root, report)
    return report


def _owned_inode(dir_fd: int, name: str, identity: tuple[int, int]) -> bool:
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity


def _rollback_owned(
    dir_fd: int, names: tuple[str, ...], identity: tuple[int, int]
) -> str:
    errors = []
    for name in names:
        try:
            if _owned_inode(dir_fd, name, identity):
                os.unlink(name, dir_fd=dir_fd)
        except OSError as exc:
            errors.append(f"{name}: {exc}")
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        errors.append(f"directory fsync: {exc}")
    return "; ".join(errors)


def persist_report(root: str | Path, report: dict[str, Any]) -> dict[str, str]:
    """将 Task 中立报告持久化为不可变内容并返回描述符。先验证形状及 PASS 条件，再一次性发布；已存在的记录不能被覆盖。"""
    repo = Path(root).resolve()
    validate_report(repo, report)
    run_id = _safe_uuid(report.get("run_id"))
    relative = f"tmp/quality/verification-reports/{run_id}.json"
    leaf = f"{run_id}.json"
    data = canonical_bytes(report)
    dir_fd = _open_directory(
        repo, ("tmp", "quality", "verification-reports"), create=True
    )
    temp = f".{run_id}.{uuid.uuid4()}.tmp"
    fd = None
    identity = None
    try:
        try:
            fd = os.open(
                temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o400,
                dir_fd=dir_fd,
            )
            info = os.fstat(fd)
            identity = (info.st_dev, info.st_ino)
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                if written <= 0:
                    raise OSError("short write made no progress")
                offset += written
            os.fsync(fd)
            os.close(fd)
            fd = None
            os.link(
                temp, leaf, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False
            )
            os.fsync(dir_fd)
            if _read(repo, relative) != data:
                raise ValueError("verification report publication drift")
            os.unlink(temp, dir_fd=dir_fd)
            os.fsync(dir_fd)
            identity = None
        except FileExistsError:
            raise ValueError("verification report already published") from None
        except OSError as exc:
            raise ValueError(f"verification report publication failed: {exc}") from None
        except ValueError:
            raise
    except ValueError as exc:
        rollback = (
            _rollback_owned(dir_fd, (leaf, temp), identity)
            if identity is not None
            else ""
        )
        if rollback:
            raise ValueError(
                f"verification report publication failed: {exc}; rollback: {rollback}"
            ) from None
        raise
    finally:
        if fd is not None:
            os.close(fd)
        if identity is not None:
            _rollback_owned(dir_fd, (temp,), identity)
        os.close(dir_fd)
    return {"locator": relative, "sha256": sha256_bytes(data)}


def read_report(
    root: str | Path, report_id: str
) -> tuple[dict[str, Any], dict[str, str]]:
    repo = Path(root).resolve()
    run_id = _safe_uuid(report_id)
    relative = f"tmp/quality/verification-reports/{run_id}.json"
    data = _read(repo, relative)
    try:
        value = json.loads(data, object_pairs_hook=_unique)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"verification report invalid: {exc}") from None
    try:
        validate_report(repo, value)
    except ValueError as exc:
        raise ValueError(f"verification report invalid: {exc}") from None
    if value.get("run_id") != run_id:
        raise ValueError("verification report invalid")
    return value, {"locator": relative, "sha256": sha256_bytes(data)}
