"""对应源码归档的私有流式完整性校验器。"""

from __future__ import annotations

import hashlib
import gzip
import json
import os
import stat
import sys
import tarfile


class InvalidArchive(ValueError):
    """对应源码归档违反固定完整性合同。"""

    pass


MAX_REQUEST_BYTES = 2_000_000
MAX_INDEX_BYTES = 2_000_000


class _BoundedTarInfo(tarfile.TarInfo):
    """Bound extension metadata before stdlib tarfile allocates its payload."""

    max_extension_bytes = 0
    max_extensions = 0
    extension_bytes = 0
    extensions = 0

    @classmethod
    def _count_extension(cls, size: int, limit: int) -> None:
        cls.extensions += 1
        cls.extension_bytes += size
        if (
            size < 0
            or size > limit
            or cls.extensions > cls.max_extensions
            or cls.extension_bytes > cls.max_extension_bytes
        ):
            _fail()

    def _proc_pax(self, archive):
        self._count_extension(self.size, 65536)
        return super()._proc_pax(archive)

    def _proc_gnulong(self, archive):
        self._count_extension(self.size, 4096)
        return super()._proc_gnulong(archive)

    def _proc_sparse(self, archive):
        _fail()

    def _proc_gnusparse_00(self, next_member, raw_headers):
        _fail()

    def _proc_gnusparse_01(self, next_member, pax_headers):
        _fail()

    def _proc_gnusparse_10(self, next_member, pax_headers, archive):
        _fail()


def _fail() -> None:
    raise InvalidArchive("SOURCE_ARCHIVE_INVALID")


def _path(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or "\\" in value
        or value.startswith("/")
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
    return value


def _read_member(
    stream, size: int, limit: int, expected_sha: str | None = None
) -> tuple[bytes | None, str]:
    if size < 0 or size > limit:
        _fail()
    digest = hashlib.sha256()
    kept = bytearray() if size <= 2_000_000 else None
    remaining = size
    while remaining:
        chunk = stream.read(min(65536, remaining))
        if not chunk:
            _fail()
        remaining -= len(chunk)
        digest.update(chunk)
        if kept is not None:
            kept.extend(chunk)
    value = digest.hexdigest()
    if expected_sha is not None and value != expected_sha:
        _fail()
    return (bytes(kept) if kept is not None else None), value


def _verify_gzip_tail(fd: int, tar_end: int, limit: int) -> None:
    """Validate gzip CRC/trailer to EOF and allow only zero tar padding after EOF markers."""
    offset = 0
    total = 0
    with os.fdopen(os.dup(fd), "rb") as raw:
        raw.seek(0)
        with gzip.GzipFile(fileobj=raw, mode="rb") as stream:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    _fail()
                end = offset + len(chunk)
                if end > tar_end:
                    tail = chunk[max(0, tar_end - offset) :]
                    if any(tail):
                        _fail()
                offset = end
    if offset < tar_end:
        _fail()


def _open_snapshot(path: object) -> tuple[int, int, str]:
    if (
        not isinstance(path, str)
        or not path.startswith(os.sep)
        or path == os.sep
        or path.endswith(os.sep)
    ):
        _fail()
    raw_parts = path.split(os.sep)[1:]
    if any(not part or part in (".", "..") or "\x00" in part for part in raw_parts):
        _fail()
    parent_path, leaf = os.path.split(path)
    canonical = os.path.join(os.path.realpath(parent_path), leaf)
    parts = canonical.split(os.sep)[1:]
    if any(not part or part in (".", "..") or "\x00" in part for part in parts):
        _fail()
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(os.sep, os.O_RDONLY | directory_flag | nofollow)
    try:
        for part in parts[:-1]:
            next_fd = os.open(
                part, os.O_RDONLY | directory_flag | nofollow, dir_fd=parent_fd
            )
            os.close(parent_fd)
            parent_fd = next_fd
        fd = os.open(
            parts[-1],
            os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
        return fd, parent_fd, parts[-1]
    except Exception:
        os.close(parent_fd)
        raise


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def verify_source_archive(request: dict) -> dict:
    """流式核验归档原始摘要、索引、成员类型、路径及每项许可原件。"""
    try:
        if set(request) != {"archivePath", "licenses", "metadata", "bytes", "sha256"}:
            _fail()
        licenses = request["licenses"]
        metadata = request["metadata"]
        if (
            not isinstance(licenses, list)
            or not 1 <= len(licenses) <= 128
            or not isinstance(metadata, dict)
            or set(metadata) != {"indexSha256", "items"}
            or not isinstance(metadata.get("indexSha256"), str)
            or len(metadata["indexSha256"]) != 64
            or any(c not in "0123456789abcdef" for c in metadata["indexSha256"])
        ):
            _fail()
        items = metadata.get("items")
        if not isinstance(items, list) or len(items) != len(licenses):
            _fail()
        license_fields = {
            "id",
            "component",
            "licenseId",
            "licenseName",
            "sourceUrl",
            "noticePath",
            "noticeBytes",
            "noticeSha256",
        }
        license_by_id = {
            x.get("id"): x
            for x in licenses
            if isinstance(x, dict) and set(x) == license_fields
        }
        if len(license_by_id) != len(licenses):
            _fail()
        item_by_id = {x.get("id"): x for x in items if isinstance(x, dict)}
        if len(item_by_id) != len(items) or set(item_by_id) != set(license_by_id):
            _fail()
        expected_files = {"index.json": (None, None)}
        total_notice_bytes = 0
        expected_index_items = []
        for ident, license in license_by_id.items():
            item = item_by_id[ident]
            if (
                not isinstance(ident, str)
                or not ident
                or len(ident) > 128
                or not isinstance(license.get("noticeBytes"), int)
                or isinstance(license.get("noticeBytes"), bool)
                or not 1 <= license["noticeBytes"] <= 2**40
                or not isinstance(license.get("noticeSha256"), str)
                or len(license["noticeSha256"]) != 64
                or any(c not in "0123456789abcdef" for c in license["noticeSha256"])
            ):
                _fail()
            item_fields = {
                "id",
                "component",
                "licenseId",
                "licenseName",
                "sourceUrl",
                "noticePath",
                "archivePath",
                "bytes",
                "sha256",
            }
            if set(item) != item_fields:
                _fail()
            notice = _path(license.get("noticePath"))
            archive_path = _path(item.get("archivePath"))
            if archive_path != "materials/" + notice:
                _fail()
            if (
                not isinstance(item.get("bytes"), int)
                or isinstance(item.get("bytes"), bool)
                or not isinstance(item.get("sha256"), str)
                or len(item["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in item["sha256"])
                or item.get("component") != license.get("component")
                or item.get("licenseId") != license.get("licenseId")
                or item.get("licenseName") != license.get("licenseName")
                or item.get("sourceUrl") != license.get("sourceUrl")
                or item.get("noticePath") != notice
                or item.get("bytes") != license.get("noticeBytes")
                or item.get("sha256") != license.get("noticeSha256")
            ):
                _fail()
            expected_file = (item["bytes"], item["sha256"])
            prior = expected_files.get(archive_path)
            if prior is not None and prior != expected_file:
                _fail()
            expected_files[archive_path] = expected_file
            total_notice_bytes += (
                item["bytes"]
                if archive_path not in {x["archivePath"] for x in expected_index_items}
                else 0
            )
            expected_index_items.append(
                {
                    k: item[k]
                    for k in (
                        "id",
                        "component",
                        "licenseId",
                        "licenseName",
                        "sourceUrl",
                        "noticePath",
                        "archivePath",
                        "bytes",
                        "sha256",
                    )
                }
            )
        expected_index_items.sort(key=lambda x: x["id"])
        total_limit = total_notice_bytes + 4_000_000 + len(licenses) * 4096
        dirs = {"materials"}
        for name in expected_files:
            parts = name.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                dirs.add("/".join(parts[:i]))
        _BoundedTarInfo.max_extensions = 3 * (len(expected_files) + len(dirs) + 2)
        _BoundedTarInfo.max_extension_bytes = 65536 + 4096 * (
            len(expected_files) + len(dirs) + 2
        )
        _BoundedTarInfo.extension_bytes = 0
        _BoundedTarInfo.extensions = 0
        seen_files: set[str] = set()
        seen_dirs: set[str] = set()
        folded: set[str] = set()
        index_bytes = None
        tar_end = None
        fd, parent_fd, leaf = _open_snapshot(request["archivePath"])
        try:
            before = os.fstat(fd)
            named_before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size < 1
                or before.st_size > total_limit
                or before.st_size != request["bytes"]
                or not isinstance(request["sha256"], str)
                or not stat.S_ISREG(named_before.st_mode)
                or _stamp(named_before) != _stamp(before)
            ):
                _fail()
            digest = hashlib.sha256()
            offset = 0
            while offset < before.st_size:
                chunk = os.pread(fd, min(65536, before.st_size - offset), offset)
                if not chunk:
                    _fail()
                digest.update(chunk)
                offset += len(chunk)
            if digest.hexdigest() != request["sha256"]:
                _fail()
            with os.fdopen(os.dup(fd), "rb") as raw:
                with tarfile.open(
                    fileobj=raw, mode="r|gz", tarinfo=_BoundedTarInfo
                ) as archive:
                    for member in archive:
                        raw_name = (
                            member.name[:-1]
                            if member.isdir() and member.name.endswith("/")
                            else member.name
                        )
                        name = _path(raw_name)
                        folded_name = name.casefold()
                        if folded_name in folded:
                            _fail()
                        folded.add(folded_name)
                        if member.isdir():
                            if name not in dirs or name in seen_dirs:
                                _fail()
                            seen_dirs.add(name)
                            continue
                        if (
                            not member.isfile()
                            or getattr(member, "sparse", None)
                            or name not in expected_files
                            or name in seen_files
                        ):
                            _fail()
                        expected_size, expected_sha = expected_files[name]
                        size_limit = (
                            MAX_INDEX_BYTES if name == "index.json" else expected_size
                        )
                        if name != "index.json" and member.size != expected_size:
                            _fail()
                        stream = archive.extractfile(member)
                        if stream is None:
                            _fail()
                        data, digest = _read_member(
                            stream, member.size, size_limit, expected_sha
                        )
                        if name == "index.json":
                            index_bytes = data
                        seen_files.add(name)
                    tar_end = archive.offset
            if tar_end is None:
                _fail()
            _verify_gzip_tail(fd, tar_end, total_limit)
            after = os.fstat(fd)
            named_after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if _stamp(before) != _stamp(after) or _stamp(before) != _stamp(named_after):
                _fail()
        finally:
            os.close(fd)
            os.close(parent_fd)
        if (
            seen_files != set(expected_files)
            or seen_dirs != dirs
            or index_bytes is None
        ):
            _fail()
        if hashlib.sha256(index_bytes).hexdigest() != metadata.get("indexSha256"):
            _fail()
        expected_index = {"schemaVersion": 1, "items": expected_index_items}
        if index_bytes != (
            json.dumps(expected_index, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"):
            _fail()
        return {"valid": True}
    except Exception as exc:
        if isinstance(exc, InvalidArchive):
            raise
        _fail()


def main() -> int:
    """读取有界 JSON 请求并输出不含本机路径的固定结果。"""
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            _fail()
        request = json.loads(raw)
        result = verify_source_archive(request)
        sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
        return 0
    except Exception:
        sys.stdout.write('{"error":"SOURCE_ARCHIVE_INVALID"}\n')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
