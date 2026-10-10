"""以不可变方式发布不含 Task 语义的 Verification 报告。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from typing import Any


_READ_STACK: ContextVar[tuple[str, ...]] = ContextVar(
    "verification-report-read-stack", default=()
)


def canonical_bytes(v: Any) -> bytes:
    """用确定性 JSON 编码生成可哈希报告字节。"""
    return json.dumps(
        v, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def sha256_bytes(v: bytes) -> str:
    """计算报告或附件原始字节的 SHA-256。"""
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
        or set(x) != {"locator", "sha256", "size_bytes", "executable"}
        or not isinstance(x["locator"], str)
        or not isinstance(x["sha256"], str)
        or len(x["sha256"]) != 64
        or isinstance(x["size_bytes"], bool)
        or not isinstance(x["size_bytes"], int)
        or x["size_bytes"] < 0
        or not isinstance(x["executable"], bool)
        for x in value["files"]
    ):
        return False
    return all(isinstance(x, str) and x for x in value["missing"])


def _pass_check_current(
    root: Path,
    result: Any,
    expected: dict[str, Any],
    prior_ids: set[str],
    run_id: str,
    *,
    cross_source_valid: bool = False,
    artifact_run_id: str | None = None,
) -> bool:
    """复核已报告 PASS 的 Check 所绑定的声明和输入是否仍与当前快照一致；漂移不能沿用旧 PASS。"""
    cross_view = isinstance(result, dict) and "cross_view_source" in result
    if (
        not isinstance(result, dict)
        or (
            set(result)
            - ({"toolchain"} if expected.get("transaction_reuse") is True else set())
        )
        not in (
            {
                "check_id",
                "status",
                "reason",
                "process",
                "input_snapshot",
                "result_contract",
                "environment",
                "environment_fingerprint",
            },
            {
                "check_id",
                "status",
                "reason",
                "process",
                "input_snapshot",
                "result_contract",
                "environment",
                "environment_fingerprint",
                "cross_view_source",
            },
        )
        or result.get("check_id") != expected["check_id"]
        or result.get("status") != "PASS"
        or result.get("reason") not in ("", "equivalent-check-deduplicated")
    ):
        return False
    try:
        from scripts.environment import resolve_check_environment
        from scripts.environment.toolchains.toolchain_identity import resolve_toolchain
        from scripts.verification.execution.check_runner import (
            build_child_environment,
            fingerprint_json,
        )

        resolved = resolve_check_environment(root, expected)
        if resolved.diagnostic["status"] != "PASS":
            return False
        child_environment = build_child_environment(runtime_environment=resolved.values)
        if result.get("environment_fingerprint") != fingerprint_json(
            {key: child_environment[key] for key in sorted(child_environment)}
        ):
            return False
        if expected.get("transaction_reuse") is True:
            if "toolchain" not in result or result["toolchain"] != resolve_toolchain(
                expected, child_environment
            ):
                return False
        elif "toolchain" in result:
            return False
    except (OSError, ValueError):
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
            "source_run_id",
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
            or (
                process.get("deduplicated_from") not in prior_ids
                and not cross_source_valid
            )
            or (cross_view and not cross_source_valid)
            or (not cross_view and "source_run_id" in process)
        ):
            return False
    else:
        return False
    artifacts = process.get("output_artifacts")
    evidence_run_id = artifact_run_id or run_id
    if (
        not isinstance(artifacts, dict)
        or set(artifacts) != {"stdout", "stderr"}
        or any(
            not _descriptor_current(root, x, evidence_run_id)
            for x in artifacts.values()
        )
    ):
        return False
    try:
        pre, before, post, final = (
            snapshots[x] for x in ("pre", "before_execution", "post", "final")
        )
        if any(not _snapshot_shape(x) for x in (pre, before, post, final)):
            return False
        if pre != final or pre["missing"]:
            return False
        if reason == "exited" and (before != pre or post != pre):
            return False
        if reason == "deduplicated" and any(
            item["files"] != pre["files"] or item["missing"] != pre["missing"]
            for item in (before, post)
        ):
            return False
        from scripts.verification.execution.check_runner import snapshot_check_inputs

        if snapshot_check_inputs(root, expected) != final:
            return False
    except (KeyError, TypeError, ValueError, OSError):
        return False
    return isinstance(result.get("result_contract"), dict) and bool(
        result["result_contract"]
    )


def _operation_fingerprint(check: dict[str, Any]) -> str:
    """计算跨视图允许共享的完整操作配置，不抹除命令或输入声明。"""
    from scripts.verification.execution.check_runner import fingerprint_json

    from scripts.verification.execution.check_runner import operation_config

    return fingerprint_json(operation_config(check))


def _environment_fingerprint(root: Path, check: dict[str, Any]) -> str:
    """对本次可执行的精确子进程环境求指纹，不将环境值写入报告。"""
    from scripts.environment import resolve_check_environment
    from scripts.verification.execution.check_runner import (
        build_child_environment,
        fingerprint_json,
    )

    resolved = resolve_check_environment(root, check)
    if resolved.diagnostic["status"] != "PASS":
        raise ValueError("missing-environment")
    environment = build_child_environment(runtime_environment=resolved.values)
    return fingerprint_json({key: environment[key] for key in sorted(environment)})


def _cross_view_source_current(
    root: Path,
    result: dict[str, Any],
    expected: dict[str, Any],
    declarations: dict[str, Any],
    consumer_report: dict[str, Any],
) -> tuple[bool, str | None]:
    """消费不可变来源索引；重核完整来源报告、新鲜度及同事务严格有向顺序。"""
    from scripts.verification.execution.check_runner import (
        is_release_runtime_child_check,
    )
    from scripts.verification.execution.check_plan_runner import freeze_inputs

    witness = result.get("cross_view_source")
    if not isinstance(witness, dict) or set(witness) != {
        "source_report",
        "check_id",
        "transaction_nonce",
    }:
        return False, None
    descriptor = witness["source_report"]
    if not isinstance(descriptor, dict) or set(descriptor) != {"locator", "sha256"}:
        return False, None
    try:
        source_id = _safe_uuid(Path(descriptor["locator"]).stem)
        nonce = _safe_uuid(witness["transaction_nonce"])
        target_transaction = consumer_report["verification_transaction"]
        if (
            set(target_transaction) != {"nonce", "profile_index"}
            or target_transaction["nonce"] != nonce
            or type(target_transaction["profile_index"]) is not int
            or not 0 <= target_transaction["profile_index"] < 16
            or source_id == consumer_report["run_id"]
        ):
            return False, None
        source_report, actual = read_report(root, source_id)
        source_transaction = source_report["verification_transaction"]
        if (
            actual != descriptor
            or source_report["result"] != "PASS"
            or set(source_transaction) != {"nonce", "profile_index"}
            or source_transaction["nonce"] != nonce
            or type(source_transaction["profile_index"]) is not int
            or not 0
            <= source_transaction["profile_index"]
            < target_transaction["profile_index"]
        ):
            return False, None
        source_freeze = freeze_inputs(
            root,
            verification_scope=source_report["scope"],
            base=source_report.get("base"),
            required_check_ids=source_report["required_check_ids"],
            _subject_changed_files=source_report["scope_review"].get("changed_files")
            if source_report["scope"] == "development-change"
            else None,
        )
        check_id = witness["check_id"]
        source_check = next(
            c for c in source_freeze["checks"] if c["check_id"] == check_id
        )
        source_result = next(
            c for c in source_report["checks"] if c["check_id"] == check_id
        )
        if (
            source_freeze["result"] != "PASS"
            or source_check.get("transaction_reuse") is not True
            or expected.get("transaction_reuse") is not True
            or is_release_runtime_child_check(expected)
            or source_result["process"]["exit_reason"] != "exited"
            or "cross_view_source" in source_result
        ):
            return False, None
        if (
            _operation_fingerprint(source_check) != _operation_fingerprint(expected)
            or _environment_fingerprint(root, source_check)
            != _environment_fingerprint(root, expected)
            or source_result["environment_fingerprint"]
            != result["environment_fingerprint"]
            or source_result.get("toolchain") != result.get("toolchain")
        ):
            return False, None
        if (
            source_result["input_snapshot"]["pre"]["files"]
            != result["input_snapshot"]["pre"]["files"]
            or result["process"].get("source_run_id") != source_id
            or result["process"].get("deduplicated_from") != check_id
        ):
            return False, None
        return True, source_id
    except (OSError, ValueError, TypeError, KeyError, StopIteration, RuntimeError):
        return False, None


def _validate_pass_report(root: Path, report: dict[str, Any]) -> None:
    """发布前核对 PASS 报告的范围、Check 完整性、输入指纹与证据；不为缺失检查补造成功结果。"""
    from scripts.verification.planning.check_definitions import (
        DeclarationError,
        load_declarations_snapshot,
    )
    from scripts.verification.execution.check_runner import fingerprint_json
    from scripts.verification.execution.check_plan_runner import (
        _runtime_checks,
        _select_repository_checks,
    )

    try:
        declarations, declaration = load_declarations_snapshot(root)
    except (DeclarationError, OSError) as exc:
        raise ValueError(
            f"verification report declaration unavailable: {exc}"
        ) from None
    if report.get("declaration_sha256") != declaration["sha256"]:
        raise ValueError("verification report declaration drift")
    scope = report.get("scope")
    try:
        if scope in {"development-baseline", "development-change"}:
            from scripts.verification.execution.check_plan_runner import freeze_inputs

            required_ids = report.get("required_check_ids")
            if not isinstance(required_ids, list) or any(
                not isinstance(x, str) or not x for x in required_ids
            ):
                raise ValueError("verification profile required checks invalid")
            if scope == "development-baseline" and report.get("base") is not None:
                raise ValueError("verification profile baseline base invalid")
            changed_subject = report.get("scope_review", {}).get("changed_files")
            frozen = freeze_inputs(
                root,
                required_check_ids=required_ids,
                verification_scope=scope,
                base=report.get("base"),
                _subject_changed_files=changed_subject
                if scope == "development-change"
                else None,
            )
            if frozen.get("result") != "PASS":
                raise ValueError("verification profile selection unavailable")
            if scope == "development-change" and report.get("base") != frozen.get(
                "base"
            ):
                raise ValueError("verification profile base invalid")
            selected = frozen["checks"]
            if report.get("frozen_input_fingerprint") != frozen.get(
                "input_fingerprint"
            ) or not isinstance(report.get("frozen_input_fingerprint"), str):
                raise ValueError("verification profile freeze fingerprint invalid")
            if report.get("scope_review", {}).get("changed_files", []) != frozen.get(
                "changed_files", []
            ):
                raise ValueError("verification profile changed files invalid")
        elif scope == "repository-baseline":
            required = report.get("required_check_ids")
            if not isinstance(required, list) or any(
                not isinstance(x, str) or not x for x in required
            ):
                raise ValueError("verification report required checks invalid")
            selected, _baseline = _select_repository_checks(
                declarations["checks"], tuple(required)
            )
            if report.get("scope_review", {}).get("kind") != "full-repository":
                raise ValueError("verification report repository scope incomplete")
        else:
            raise ValueError("verification report scope invalid")
    except ValueError:
        raise
    except (KeyError, TypeError) as exc:
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
        cross_source_valid, source_run_id = False, None
        if isinstance(result, dict) and "cross_view_source" in result:
            cross_source_valid, source_run_id = _cross_view_source_current(
                root, result, expected, declarations, report
            )
        if not _pass_check_current(
            root,
            result,
            expected,
            prior,
            report["run_id"],
            cross_source_valid=cross_source_valid,
            artifact_run_id=source_run_id,
        ):
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
                "verification_scope": "repository-baseline",
                "base": None,
                "changed_files": [],
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
    """验证报告结构、输入指纹与检查完整性，拒绝伪造或缺项。"""
    if not isinstance(report, dict):
        raise TypeError("verification report must be an object")
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
        or report.get("scope")
        not in {
            "repository-baseline",
            "development-baseline",
            "development-change",
        }
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
    return _publish_document(repo, report, run_id, "verification-reports")


def _publish_document(repo, report, run_id, namespace):
    """复用 descriptor-relative 不可变写入，不把诊断目录当正式报告来源。"""
    relative = f"tmp/quality/{namespace}/{run_id}.json"
    leaf = f"{run_id}.json"
    data = canonical_bytes(report)
    dir_fd = _open_directory(repo, ("tmp", "quality", namespace), create=True)
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
    """按精确报告 ID 安全读取并核对发布记录，不搜索最新报告。"""
    repo = Path(root).resolve()
    run_id = _safe_uuid(report_id)
    relative = f"tmp/quality/verification-reports/{run_id}.json"
    data = _read(repo, relative)
    try:
        value = json.loads(data, object_pairs_hook=_unique)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"verification report invalid: {exc}") from None
    stack = _READ_STACK.get()
    if run_id in stack or len(stack) >= 16:
        raise ValueError("verification report reuse cycle or depth exceeded")
    token = _READ_STACK.set((*stack, run_id))
    try:
        validate_report(repo, value)
    except ValueError as exc:
        raise ValueError(f"verification report invalid: {exc}") from None
    finally:
        _READ_STACK.reset(token)
    if value.get("run_id") != run_id:
        raise ValueError("verification report invalid")
    return value, {"locator": relative, "sha256": sha256_bytes(data)}
