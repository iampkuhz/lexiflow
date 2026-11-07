"""把已绑定摘要的告知原件直接写入对应源码归档。"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile


MAX_REQUEST_BYTES = 2_000_000
MAX_FILE_BYTES = 2**40
CHUNK_BYTES = 65536
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _fail() -> None:
    raise ValueError("SOURCE_PACKAGE_INVALID")


def _notice_parts(value: object) -> list[str]:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 512
        or value.startswith("/")
        or "\\" in value
    ):
        _fail()
    parts = value.split("/")
    if any(
        not p
        or p in (".", "..")
        or len(p) > 128
        or any(ord(c) < 32 or ord(c) == 127 for c in p)
        for p in parts
    ):
        _fail()
    return parts


def _directory_at(root_fd: int, parts: list[str]) -> int:
    current = os.dup(root_fd)
    try:
        for part in parts:
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


class _DigestStream:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.bytes = 0

    def read(self, size: int) -> bytes:
        data = self.stream.read(size)
        self.bytes += len(data)
        self.digest.update(data)
        return data

    def write(self, data: bytes) -> int:
        written = self.stream.write(data)
        if written != len(data) or self.bytes + written > MAX_FILE_BYTES:
            _fail()
        self.bytes += written
        self.digest.update(data)
        return written


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _add_notice(tar, root_fd: int, name: str, parts: list[str], size: int, digest: str):
    parent = _directory_at(root_fd, parts[:-1])
    try:
        fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        with os.fdopen(fd, "rb") as raw:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size != size:
                _fail()
            member = tarfile.TarInfo(name)
            member.size, member.mode = size, 0o600
            metered = _DigestStream(raw)
            tar.addfile(member, metered)
            named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if (
                metered.bytes != size
                or metered.digest.hexdigest() != digest
                or raw.read(1)
                or _stamp(before) != _stamp(os.fstat(fd))
                or _stamp(before) != _stamp(named)
            ):
                _fail()
    finally:
        os.close(parent)


def _write(request: dict) -> dict:
    if set(request) != {"artifactRoot", "archivePath", "index", "files"}:
        _fail()
    root, archive, records = (
        request[k] for k in ("artifactRoot", "archivePath", "files")
    )
    if (
        not all(isinstance(p, str) and os.path.isabs(p) for p in (root, archive))
        or not isinstance(records, list)
        or not 1 <= len(records) <= 128
    ):
        _fail()
    root_parts = root.split("/")[1:]
    if any(not p or p in (".", "..") for p in root_parts):
        _fail()
    index = base64.b64decode(request["index"], validate=True)
    if not 1 <= len(index) <= 2_000_000:
        _fail()

    seen = set()
    dirs = {"materials"}
    files = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            _fail()
        parts = _notice_parts(record["path"])
        size, digest = record["bytes"], record["sha256"]
        if (
            record["path"] in seen
            or type(size) is not int
            or not 1 <= size <= MAX_FILE_BYTES
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
        ):
            _fail()
        seen.add(record["path"])
        archive_name = "materials/" + record["path"]
        dirs.update("materials/" + "/".join(parts[:i]) for i in range(1, len(parts)))
        files.append((archive_name, parts, size, digest))

    system_root = os.open("/", DIRECTORY_FLAGS)
    try:
        root_fd = _directory_at(system_root, root_parts)
    finally:
        os.close(system_root)
    try:
        archive_fd = os.open(
            archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            with os.fdopen(archive_fd, "wb") as raw:
                metered = _DigestStream(raw)
                with (
                    gzip.GzipFile(
                        filename="",
                        fileobj=metered,
                        mode="wb",
                        compresslevel=6,
                        mtime=0,
                    ) as gz,
                    tarfile.open(
                        fileobj=gz,
                        mode="w|",
                        format=tarfile.PAX_FORMAT,
                        copybufsize=CHUNK_BYTES,
                    ) as tar,
                ):
                    info = tarfile.TarInfo("index.json")
                    info.size, info.mode = len(index), 0o600
                    tar.addfile(info, io.BytesIO(index))
                    for name in sorted(
                        dirs, key=lambda value: (value.count("/"), value)
                    ):
                        info = tarfile.TarInfo(name)
                        info.type, info.mode = tarfile.DIRTYPE, 0o700
                        tar.addfile(info)
                    for name, parts, size, digest in files:
                        _add_notice(tar, root_fd, name, parts, size, digest)
                raw.flush()
                if os.fstat(raw.fileno()).st_size != metered.bytes:
                    _fail()
                return {"bytes": metered.bytes, "sha256": metered.digest.hexdigest()}
        except BaseException:
            os.unlink(archive)
            raise
    finally:
        os.close(root_fd)


def main() -> int:
    """读取私有的有界构建请求，只返回归档摘要。"""
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            _fail()
        result = _write(json.loads(raw))
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (OSError, ValueError, TypeError, EOFError, tarfile.TarError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
