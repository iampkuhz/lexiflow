"""Immutable, run-scoped storage for Gate lifecycle artifacts.

The store deliberately has no notion of receipt kinds.  Callers provide
canonical bytes and the store provides fixed, repo-relative locators,
exclusive publication, durable flushes, and single-read status.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


RUN_ROOT = "tmp/quality/runs"
PLAN_NAME = "plan.json"
START_NAME = "start.json"
RECEIPT_NAME = "receipt.json"
START_SCHEMA = "lexiflow.gate-start-event.v1"
STATUS_SCHEMA = "lexiflow.gate-status.v1"

_UUID_V4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ReceiptStoreError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class PublishedArtifact:
    locator: str
    sha256: str


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ReceiptStoreError("invalid-json", str(exc)) from None


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_run_id(value: str) -> str:
    if not isinstance(value, str) or not _UUID_V4.fullmatch(value):
        raise ReceiptStoreError("invalid-run-id", "run_id must be a canonical UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise ReceiptStoreError("invalid-run-id", "run_id is not a UUID") from None
    if str(parsed) != value or parsed.version != 4:
        raise ReceiptStoreError("invalid-run-id", "run_id must be canonical UUIDv4")
    return value


def safe_locator(locator: str) -> str:
    if (
        not isinstance(locator, str)
        or not locator
        or "\\" in locator
        or any(ord(character) < 32 or ord(character) == 127 for character in locator)
    ):
        raise ReceiptStoreError("unsafe-locator", "locator must be a non-empty POSIX path")
    path = PurePosixPath(locator)
    if path.is_absolute() or locator != path.as_posix() or any(part in ("", ".", "..") for part in path.parts):
        raise ReceiptStoreError("unsafe-locator", f"unsafe locator: {locator!r}")
    if any(part.lower() == "latest" for part in path.parts):
        raise ReceiptStoreError("unsafe-locator", "alias locator is forbidden")
    return locator


def _open_repo_root(repo_root: str | os.PathLike[str]) -> tuple[Path, int]:
    root = Path(repo_root)
    try:
        fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(fd)
    except OSError as exc:
        raise ReceiptStoreError("invalid-repo-root", str(exc)) from None
    if not stat.S_ISDIR(info.st_mode):
        os.close(fd)
        raise ReceiptStoreError("invalid-repo-root", "repo_root is not a directory")
    return root, fd


def read_bound_bytes(repo_root: str | os.PathLike[str], locator: str) -> bytes:
    """Read one regular file without following any symlink component."""
    locator = safe_locator(locator)
    _, current_fd = _open_repo_root(repo_root)
    try:
        parts = PurePosixPath(locator).parts
        for part in parts[:-1]:
            try:
                child = os.open(
                    part,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current_fd,
                )
            except FileNotFoundError as exc:
                raise ReceiptStoreError("artifact-missing", f"{locator}: {exc}") from None
            except OSError as exc:
                raise ReceiptStoreError("artifact-unavailable", f"{locator}: {exc}") from None
            os.close(current_fd)
            current_fd = child
        try:
            file_fd = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd)
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode):
                raise ReceiptStoreError("artifact-unavailable", f"not a regular file: {locator}")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(file_fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(file_fd)
            identity = lambda value: (
                value.st_dev, value.st_ino, value.st_mode, value.st_size,
                getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000)),
            )
            if identity(after) != identity(info):
                raise ReceiptStoreError("artifact-drift", f"artifact changed while reading: {locator}")
            return b"".join(chunks)
        except FileNotFoundError as exc:
            raise ReceiptStoreError("artifact-missing", f"{locator}: {exc}") from None
        except OSError as exc:
            raise ReceiptStoreError("artifact-unavailable", f"{locator}: {exc}") from None
        finally:
            if "file_fd" in locals():
                os.close(file_fd)
    finally:
        os.close(current_fd)


class ImmutableReceiptStore:
    """Exclusive publisher for one freshly generated run identity."""

    def __init__(self, repo_root: str | os.PathLike[str], run_id: str) -> None:
        self.repo_root = Path(repo_root)
        self.run_id = validate_run_id(run_id)
        self.run_locator = f"{RUN_ROOT}/{self.run_id}"
        self.run_path = self.repo_root.joinpath(*PurePosixPath(self.run_locator).parts)
        self._created = False

    def create(self) -> None:
        if self._created:
            raise ReceiptStoreError("run-collision", "run store already initialized")
        parent = self._ensure_store_parent()
        parent_fd = -1
        try:
            parent_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
            os.mkdir(self.run_id, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            raise ReceiptStoreError("run-collision", f"run already exists: {self.run_id}") from None
        except OSError as exc:
            raise ReceiptStoreError("store-unavailable", str(exc)) from None
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)
        self._created = True

    def locator(self, name: str) -> str:
        if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name) or name in (".", ".."):
            raise ReceiptStoreError("unsafe-locator", f"unsafe run artifact name: {name!r}")
        return f"{self.run_locator}/{name}"

    def publish_bytes(self, name: str, content: bytes) -> PublishedArtifact:
        if not self._created:
            raise ReceiptStoreError("store-unavailable", "run store is not initialized")
        if not isinstance(content, bytes):
            raise ReceiptStoreError("invalid-artifact", "content must be bytes")
        locator = self.locator(name)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        temporary = f".{name}.{uuid.uuid4()}.tmp"
        linked = False
        committed = False
        temporary_identity: tuple[int, int] | None = None
        try:
            dir_fd = os.open(self.run_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
            fd = os.open(temporary, flags, 0o600, dir_fd=dir_fd)
            offset = 0
            while offset < len(content):
                written = os.write(fd, content[offset:])
                if written <= 0:
                    raise OSError("short write")
                offset += written
            os.fsync(fd)
            temporary_info = os.fstat(fd)
            temporary_identity = (temporary_info.st_dev, temporary_info.st_ino)
            os.close(fd)
            fd = -1
            os.link(
                temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd,
                follow_symlinks=False,
            )
            linked = True
            os.fsync(dir_fd)
            committed = True
            try:
                os.unlink(temporary, dir_fd=dir_fd)
            except OSError:
                # The immutable final name is already durable; a private
                # staging inode is harmless and is never considered evidence.
                pass
        except FileExistsError:
            raise ReceiptStoreError("artifact-collision", f"artifact already exists: {locator}") from None
        except OSError as exc:
            raise ReceiptStoreError("store-unavailable", f"cannot publish {locator}: {exc}") from None
        finally:
            if "fd" in locals() and fd >= 0:
                os.close(fd)
            if "dir_fd" in locals():
                if not committed and temporary_identity is not None:
                    try:
                        final_info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                        if (final_info.st_dev, final_info.st_ino) == temporary_identity:
                            os.unlink(name, dir_fd=dir_fd)
                            os.fsync(dir_fd)
                    except OSError:
                        pass
                if not committed:
                    try:
                        temp_info = os.stat(temporary, dir_fd=dir_fd, follow_symlinks=False)
                        if temporary_identity is None or (temp_info.st_dev, temp_info.st_ino) == temporary_identity:
                            os.unlink(temporary, dir_fd=dir_fd)
                    except OSError:
                        pass
                os.close(dir_fd)
        observed = read_bound_bytes(self.repo_root, locator)
        if observed != content:
            raise ReceiptStoreError("artifact-drift", f"published bytes changed: {locator}")
        return PublishedArtifact(locator, sha256_bytes(content))

    def publish_json(self, name: str, value: Any) -> PublishedArtifact:
        return self.publish_bytes(name, canonical_json_bytes(value))

    def publish_plan(self, canonical_plan: bytes) -> PublishedArtifact:
        return self.publish_bytes(PLAN_NAME, canonical_plan)

    def publish_start(self, start_event: dict[str, Any]) -> PublishedArtifact:
        return self.publish_json(START_NAME, start_event)

    def publish_receipt(self, receipt: dict[str, Any]) -> PublishedArtifact:
        return self.publish_json(RECEIPT_NAME, receipt)

    @staticmethod
    def _assert_directory_chain(path: Path) -> None:
        current = path
        while True:
            try:
                if stat.S_ISLNK(os.lstat(current).st_mode):
                    raise ReceiptStoreError("unsafe-locator", f"symlink directory forbidden: {current}")
            except FileNotFoundError:
                raise ReceiptStoreError("store-unavailable", f"missing store directory: {current}") from None
            if current.parent == current:
                break
            current = current.parent

    def _ensure_store_parent(self) -> Path:
        """Create RUN_ROOT beneath the bound repo root without following links."""
        _, current_fd = _open_repo_root(self.repo_root)
        current = self.repo_root
        try:
            for part in PurePosixPath(RUN_ROOT).parts:
                try:
                    os.mkdir(part, 0o700, dir_fd=current_fd)
                    os.fsync(current_fd)
                except FileExistsError:
                    pass
                try:
                    child = os.open(
                        part,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=current_fd,
                    )
                except OSError as exc:
                    raise ReceiptStoreError("unsafe-locator", f"unsafe run-store component {part!r}: {exc}") from None
                os.close(current_fd)
                current_fd = child
                current = current / part
            return current
        finally:
            os.close(current_fd)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
            os.fsync(fd)
            os.close(fd)
        except OSError as exc:
            raise ReceiptStoreError("store-unavailable", f"directory flush failed: {exc}") from None


# Short alias retained as the stable generic store surface.
ReceiptStore = ImmutableReceiptStore


def _parse_json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReceiptStoreError("status-invalid", f"invalid {label}: {exc}") from None
    if not isinstance(value, dict) or canonical_json_bytes(value) != content:
        raise ReceiptStoreError("status-invalid", f"{label} is not a canonical JSON object")
    return value


def read_status(repo_root: str | os.PathLike[str], run_id: str) -> dict[str, Any]:
    """Read exactly one fixed run path; never scan, wait, retry, or execute."""
    run_id = validate_run_id(run_id)
    base = f"{RUN_ROOT}/{run_id}"
    start_locator = f"{base}/{START_NAME}"

    def validated_start() -> tuple[dict[str, Any], bytes]:
        start_bytes = read_bound_bytes(repo_root, start_locator)
        start = _parse_json_object(start_bytes, "start event")
        if start.get("schema_version") != START_SCHEMA or start.get("run_id") != run_id:
            raise ReceiptStoreError("status-invalid", "start event identity mismatch")
        plan = start.get("plan")
        expected_plan = f"{base}/{PLAN_NAME}"
        if not isinstance(plan, dict) or plan.get("locator") != expected_plan:
            raise ReceiptStoreError("status-invalid", "start event plan locator mismatch")
        plan_bytes = read_bound_bytes(repo_root, expected_plan)
        if plan.get("sha256") != sha256_bytes(plan_bytes):
            raise ReceiptStoreError("status-invalid", "persisted plan hash mismatch")
        plan_value = _parse_json_object(plan_bytes, "plan")
        projection = {key: value for key, value in plan_value.items() if key != "content_fingerprint"}
        if (
            plan_value.get("content_fingerprint") != start.get("content_fingerprint")
            or sha256_bytes(canonical_json_bytes(projection)) != start.get("content_fingerprint")
            or plan_value.get("receipt_kind") != start.get("receipt_kind")
        ):
            raise ReceiptStoreError("status-invalid", "persisted plan fingerprint or kind mismatch")
        process = start.get("process_identity")
        if not isinstance(process, dict) or process.get("gate_run_id") != run_id:
            raise ReceiptStoreError("status-invalid", "process identity mismatch")
        issuer = start.get("trusted_issuer_packet")
        if (
            not isinstance(issuer, dict)
            or process.get("issuer_packet_sha256") != issuer.get("sha256")
        ):
            raise ReceiptStoreError("status-invalid", "issuer/process binding mismatch")
        return start, start_bytes

    receipt_locator = f"{base}/{RECEIPT_NAME}"
    try:
        receipt_bytes = read_bound_bytes(repo_root, receipt_locator)
    except ReceiptStoreError as exc:
        if exc.code != "artifact-missing":
            raise
    else:
        receipt = _parse_json_object(receipt_bytes, "receipt")
        if (
            receipt.get("schema_version") != "lexiflow.gate-receipt.v1"
            or receipt.get("run_id") != run_id
            or receipt.get("result") not in ("PASS", "BLOCKED", "FAIL")
        ):
            raise ReceiptStoreError("status-invalid", "final receipt identity or result mismatch")
        start, _ = validated_start()
        receipt_plan = receipt.get("plan")
        if (
            not isinstance(receipt_plan, dict)
            or receipt_plan.get("locator") != start["plan"]["locator"]
            or receipt_plan.get("sha256") != start["plan"]["sha256"]
            or receipt_plan.get("content_fingerprint") != start.get("content_fingerprint")
            or receipt.get("receipt_kind") != start.get("receipt_kind")
            or receipt.get("started_at") != start.get("started_at")
            or receipt.get("issuer", {}).get("process_identity") != start.get("process_identity")
            or receipt.get("issuer", {}).get("trusted_issuer_packet") != start.get("trusted_issuer_packet")
        ):
            raise ReceiptStoreError("status-invalid", "final receipt does not match the flushed start event")
        completeness = receipt.get("completeness", {}).get("status")
        manifest = receipt.get("artifact_manifest")
        if receipt["result"] in ("PASS", "BLOCKED") and completeness != "PASS":
            raise ReceiptStoreError("status-invalid", "non-FAIL receipt is incomplete")
        if completeness == "PASS":
            if not isinstance(manifest, dict):
                raise ReceiptStoreError("status-invalid", "complete receipt lacks artifact manifest")
            manifest_bytes = read_bound_bytes(repo_root, manifest.get("locator", ""))
            if manifest.get("sha256") != sha256_bytes(manifest_bytes):
                raise ReceiptStoreError("status-invalid", "artifact manifest hash mismatch")
            manifest_value = _parse_json_object(manifest_bytes, "artifact manifest")
            if manifest_value.get("run_id") != run_id:
                raise ReceiptStoreError("status-invalid", "artifact manifest identity mismatch")
        return {
            "schema_version": STATUS_SCHEMA,
            "run_id": run_id,
            "status": "FINALIZED",
            "receipt_kind": receipt.get("receipt_kind"),
            "result": receipt["result"],
            "receipt": {"locator": receipt_locator, "sha256": sha256_bytes(receipt_bytes)},
        }

    start, start_bytes = validated_start()
    return {
        "schema_version": STATUS_SCHEMA,
        "run_id": run_id,
        "status": "RUNNING",
        "receipt_kind": start.get("receipt_kind"),
        "start_event": {"locator": start_locator, "sha256": sha256_bytes(start_bytes)},
    }
