"""正式 Delivery Gate 记录的 fail-closed 存储原语。只信任受约束的本地根目录、不可变记录与哈希绑定。"""

from __future__ import annotations
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path, PurePosixPath
from typing import Any


class RecordError(ValueError):
    """正式证据存储、哈希或路径边界失败时携带稳定错误代码。"""

    def __init__(self, code: str, detail: str) -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def canonical_bytes(value: Any) -> bytes:
    """按确定性 JSON 编码记录，供内容哈希和不可变发布使用。"""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def sha256_bytes(data: bytes) -> str:
    """计算正式记录字节的 SHA-256 摘要。"""
    return hashlib.sha256(data).hexdigest()


def content_hash(record: dict[str, Any]) -> str:
    """对不含自身哈希字段的记录计算规范内容哈希。"""
    return sha256_bytes(
        canonical_bytes({k: v for k, v in record.items() if k != "content_hash"})
    )


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def parse_json(data: bytes, kind: str) -> Any:
    """拒绝重复键和损坏 JSON，不把不可信记录当作普通字典。"""
    try:
        return json.loads(data, object_pairs_hook=_unique)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RecordError("record-invalid", f"{kind}: {exc}") from None


def verify_record(record: Any, kind: str) -> dict[str, Any]:
    """核对记录内容哈希，拒绝缺失或被修改的持久化证据。"""
    if not isinstance(record, dict):
        raise RecordError("record-invalid", f"{kind} must be an object")
    stored = record.get("content_hash")
    if (
        not isinstance(stored, str)
        or len(stored) != 64
        or any(c not in "0123456789abcdef" for c in stored)
    ):
        raise RecordError("record-hash-missing", f"{kind} lacks canonical content_hash")
    if stored != content_hash(record):
        raise RecordError("record-tampered", f"{kind} content hash mismatch")
    return record


def require_uuid(value: str, label: str) -> str:
    """要求精确规范 UUID，阻止路径拼接和模糊 ID。"""
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise RecordError(f"unsafe-{label}-id", str(value)) from None
    if str(parsed) != value:
        raise RecordError(f"unsafe-{label}-id", str(value))
    return value


def safe_relative(locator: str) -> str:
    """拒绝绝对、穿越与异常分隔符路径，返回规范相对 locator。"""
    if (
        not isinstance(locator, str)
        or not locator
        or "\\" in locator
        or "\0" in locator
    ):
        raise RecordError("unsafe-path", str(locator))
    path = PurePosixPath(locator)
    if path.is_absolute() or any(x in ("", ".", "..") for x in path.parts):
        raise RecordError("unsafe-path", locator)
    return path.as_posix()


_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _open_directory(
    root: Path,
    parts: tuple[str, ...],
    *,
    create: bool = False,
    missing_code: str = "record-not-found",
    locator: str = "",
) -> int:
    try:
        fd = os.open(root, _DIR_FLAGS)
    except OSError as exc:
        raise RecordError("unsafe-path", f"{locator}: {exc}") from None
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise RecordError("unsafe-path", f"{locator}: {exc}") from None
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                raise RecordError(missing_code, locator) from None
            except OSError as exc:
                raise RecordError("unsafe-path", f"{locator}: {exc}") from None
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                os.close(child)
                raise RecordError("unsafe-path", locator)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_regular(root: Path, locator: str, *, missing_code: str) -> bytes:
    safe = safe_relative(locator)
    parts = PurePosixPath(safe).parts
    dir_fd = _open_directory(root, parts[:-1], missing_code=missing_code, locator=safe)
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
            raise RecordError(missing_code, safe) from None
        except OSError as exc:
            raise RecordError("unsafe-path", f"{safe}: {exc}") from None
    finally:
        os.close(dir_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RecordError("unsafe-path", safe)
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def read_json(
    root: Path, locator: str, kind: str, *, require_hash: bool = True
) -> dict[str, Any]:
    """安全读取精确 locator 的 JSON 正式记录。"""
    value = parse_json(
        _read_regular(root, locator, missing_code="record-not-found"), kind
    )
    if not isinstance(value, dict):
        raise RecordError("record-invalid", f"{kind} must be an object")
    return verify_record(value, kind) if require_hash else value


def read_bound_bytes(root: Path, locator: str, expected_sha256: str) -> bytes:
    """读取与预期哈希绑定的附件字节，拒绝来源漂移。"""
    data = _read_regular(root, locator, missing_code="artifact-missing")
    if not isinstance(expected_sha256, str) or sha256_bytes(data) != expected_sha256:
        raise RecordError("artifact-drift", locator)
    return data


def read_regular_bytes(root: Path, locator: str) -> bytes:
    """只读取仓库内普通文件字节，不追随越界或符号链接。"""
    return _read_regular(root, locator, missing_code="artifact-missing")


def read_optional_bytes(root: Path, locator: str) -> bytes | None:
    """仅在目标存在时读取安全普通文件，缺失与非法路径区分。"""
    try:
        return _read_regular(root, locator, missing_code="artifact-missing")
    except RecordError as exc:
        if exc.code == "artifact-missing":
            return None
        raise


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


def _publish_once(root: Path, locator: str, data: bytes) -> dict[str, str]:
    """将给定字节只发布一次到受约束的记录路径，返回定位符和哈希。拒绝已存在或不安全目标，避免覆盖正式证据。"""
    safe = safe_relative(locator)
    parts = PurePosixPath(safe).parts
    leaf = parts[-1]
    dir_fd = _open_directory(root, parts[:-1], create=True, locator=safe)
    temp = f".{leaf}.{uuid.uuid4()}.tmp"
    file_fd = None
    identity = None
    try:
        try:
            file_fd = os.open(
                temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o400,
                dir_fd=dir_fd,
            )
            info = os.fstat(file_fd)
            identity = (info.st_dev, info.st_ino)
            offset = 0
            while offset < len(data):
                written = os.write(file_fd, data[offset:])
                if written <= 0:
                    raise OSError("short write made no progress")
                offset += written
            os.fsync(file_fd)
            os.close(file_fd)
            file_fd = None
            os.link(
                temp, leaf, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False
            )
            os.fsync(dir_fd)
            if _read_regular(root, safe, missing_code="record-not-found") != data:
                raise RecordError("record-publication-drift", safe)
            os.unlink(temp, dir_fd=dir_fd)
            os.fsync(dir_fd)
            identity = None
        except FileExistsError:
            raise RecordError("immutable-record-exists", safe) from None
        except OSError as exc:
            raise RecordError("record-publication-failed", f"{safe}: {exc}") from None
        except RecordError:
            raise
    except RecordError as exc:
        rollback = (
            _rollback_owned(dir_fd, (leaf, temp), identity)
            if identity is not None
            else ""
        )
        if rollback:
            raise RecordError(
                "record-publication-failed", f"{safe}: {exc}; rollback: {rollback}"
            ) from None
        raise
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if identity is not None:
            _rollback_owned(dir_fd, (temp,), identity)
        os.close(dir_fd)
    return {"locator": safe, "sha256": sha256_bytes(data)}


def publish_json(root: Path, locator: str, record: dict[str, Any]) -> dict[str, str]:
    """以不可变语义发布规范 JSON 记录，拒绝碰撞覆盖。"""
    body = dict(record)
    body["content_hash"] = content_hash(body)
    return _publish_once(root, locator, canonical_bytes(body))


def publish_bytes(root: Path, locator: str, data: bytes) -> dict[str, str]:
    """以不可变语义发布附件字节并返回内容描述符。"""
    return _publish_once(root, locator, data)


def delivery_gate_locator(kind: str, record_id: str) -> str:
    """从层级与精确记录 ID 构造受控 Delivery Gate locator。"""
    require_uuid(record_id, kind.rstrip("s"))
    return (
        f"tmp/quality/delivery-gate/submissions/{record_id}.json"
        if kind == "submissions"
        else f"tmp/quality/delivery-gate/{kind}/{record_id}/record.json"
    )


_RECORD_FIELDS = {
    "submission": (
        {
            "schema_version",
            "submission_id",
            "task_requirements",
            "change_report",
            "scope_confirmation",
            "scope_base",
            "diff",
            "submitter_identity",
            "runtime_proof",
            "authority",
            "producer",
            "verification_freeze",
            "changed_file_snapshots",
            "created_at",
            "content_hash",
        },
        "lexiflow.delivery-gate-submission.v4",
        "submission_id",
    ),
    "validation": (
        {
            "schema_version",
            "validation_id",
            "submission_id",
            "submission_content_hash",
            "validator_identity",
            "runtime_proof",
            "authority",
            "verification_report",
            "frozen_input_fingerprint",
            "gaps",
            "result",
            "created_at",
            "content_hash",
        },
        "lexiflow.delivery-gate-validation.v3",
        "validation_id",
    ),
    "review": (
        {
            "schema_version",
            "review_id",
            "submission_id",
            "validation_id",
            "submission_content_hash",
            "validation_content_hash",
            "reviewer_identity",
            "runtime_proof",
            "authority",
            "findings",
            "result",
            "created_at",
            "delivery_rerun",
            "content_hash",
        },
        "lexiflow.delivery-gate-review.v3",
        "review_id",
    ),
    "check": (
        {
            "schema_version",
            "check_id",
            "submission_id",
            "validation_id",
            "review_id",
            "submission_content_hash",
            "validation_content_hash",
            "review_content_hash",
            "result",
            "reason",
            "conditions",
            "delivery_rerun",
            "created_at",
            "content_hash",
        },
        "lexiflow.delivery-gate-check.v3",
        "check_id",
    ),
}


def _record_shape(record: dict[str, Any], kind: str, record_id: str) -> dict[str, Any]:
    fields, schema, id_field = _RECORD_FIELDS[kind]
    if (
        set(record) != fields
        or record.get("schema_version") != schema
        or record.get(id_field) != record_id
    ):
        raise RecordError("record-schema-invalid", kind)
    return record


def load_submission(root: Path, submission_id: str) -> dict[str, Any]:
    """按 submission ID 读取并校验送验记录与内容哈希。"""
    require_uuid(submission_id, "submission")
    return _record_shape(
        read_json(
            root, delivery_gate_locator("submissions", submission_id), "submission"
        ),
        "submission",
        submission_id,
    )


def load_layer(root: Path, kind: str, record_id: str) -> dict[str, Any]:
    """读取指定层的正式记录并核对其上游绑定。"""
    singular = kind.rstrip("s")
    require_uuid(record_id, singular)
    return _record_shape(
        read_json(root, delivery_gate_locator(kind, record_id), singular),
        singular,
        record_id,
    )


def _list_directory(root: Path, locator: str) -> list[str]:
    safe = safe_relative(locator)
    parts = PurePosixPath(safe).parts
    try:
        fd = _open_directory(root, parts, locator=safe)
    except RecordError as exc:
        if exc.code == "record-not-found":
            return []
        raise
    try:
        return sorted(os.listdir(fd))
    finally:
        os.close(fd)


def list_submissions(root: Path) -> list[dict[str, Any]]:
    """只列出安全且可校验的送验记录，不推断 latest。"""
    records = []
    for name in _list_directory(root, "tmp/quality/delivery-gate/submissions"):
        if not name.endswith(".json"):
            continue
        rid = require_uuid(name[:-5], "submission")
        records.append(load_submission(root, rid))
    return records


def list_layer(
    root: Path, kind: str, foreign_key: str, value: str
) -> list[dict[str, Any]]:
    """列出指定层的安全记录供状态查询，不改变证据。"""
    matches = []
    for name in _list_directory(root, f"tmp/quality/delivery-gate/{kind}"):
        rid = require_uuid(name, kind.rstrip("s"))
        record = load_layer(root, kind, rid)
        if record.get(foreign_key) == value:
            matches.append(record)
    return matches
