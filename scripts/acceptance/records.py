"""正式 Acceptance 记录的 fail-closed 存储原语。只信任受约束的本地根目录、不可变记录与哈希绑定。"""

from __future__ import annotations
import hashlib, json, os, stat, uuid
from pathlib import Path, PurePosixPath
from typing import Any


class RecordError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(record: dict[str, Any]) -> str:
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
    try:
        return json.loads(data, object_pairs_hook=_unique)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RecordError("record-invalid", f"{kind}: {exc}") from None


def verify_record(record: Any, kind: str) -> dict[str, Any]:
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
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise RecordError(f"unsafe-{label}-id", str(value)) from None
    if str(parsed) != value:
        raise RecordError(f"unsafe-{label}-id", str(value))
    return value


def safe_relative(locator: str) -> str:
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
    value = parse_json(
        _read_regular(root, locator, missing_code="record-not-found"), kind
    )
    if not isinstance(value, dict):
        raise RecordError("record-invalid", f"{kind} must be an object")
    return verify_record(value, kind) if require_hash else value


def read_bound_bytes(root: Path, locator: str, expected_sha256: str) -> bytes:
    data = _read_regular(root, locator, missing_code="artifact-missing")
    if not isinstance(expected_sha256, str) or sha256_bytes(data) != expected_sha256:
        raise RecordError("artifact-drift", locator)
    return data


def read_regular_bytes(root: Path, locator: str) -> bytes:
    return _read_regular(root, locator, missing_code="artifact-missing")


def read_optional_bytes(root: Path, locator: str) -> bytes | None:
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
    body = dict(record)
    body["content_hash"] = content_hash(body)
    return _publish_once(root, locator, canonical_bytes(body))


def publish_bytes(root: Path, locator: str, data: bytes) -> dict[str, str]:
    return _publish_once(root, locator, data)


def acceptance_locator(kind: str, record_id: str) -> str:
    require_uuid(record_id, kind.rstrip("s"))
    return (
        f"tmp/quality/acceptance/submissions/{record_id}.json"
        if kind == "submissions"
        else f"tmp/quality/acceptance/{kind}/{record_id}/record.json"
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
        "lexiflow.acceptance-submission.v4",
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
        "lexiflow.acceptance-validation.v3",
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
        "lexiflow.acceptance-review.v3",
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
        "lexiflow.acceptance-check.v3",
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
    require_uuid(submission_id, "submission")
    return _record_shape(
        read_json(root, acceptance_locator("submissions", submission_id), "submission"),
        "submission",
        submission_id,
    )


def load_layer(root: Path, kind: str, record_id: str) -> dict[str, Any]:
    singular = kind.rstrip("s")
    require_uuid(record_id, singular)
    return _record_shape(
        read_json(root, acceptance_locator(kind, record_id), singular),
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
    records = []
    for name in _list_directory(root, "tmp/quality/acceptance/submissions"):
        if not name.endswith(".json"):
            continue
        rid = require_uuid(name[:-5], "submission")
        records.append(load_submission(root, rid))
    return records


def list_layer(
    root: Path, kind: str, foreign_key: str, value: str
) -> list[dict[str, Any]]:
    matches = []
    for name in _list_directory(root, f"tmp/quality/acceptance/{kind}"):
        rid = require_uuid(name, kind.rstrip("s"))
        record = load_layer(root, kind, rid)
        if record.get(foreign_key) == value:
            matches.append(record)
    return matches
