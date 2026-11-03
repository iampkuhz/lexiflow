"""对应源码 tar.gz 的正向映射与恶意成员合同测试。"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from ops.release.integrity.source_archive_integrity import (
    InvalidArchive,
    verify_source_archive,
)


class SourceArchiveIntegrityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.notice = b"shared source notice\n"
        digest = hashlib.sha256(self.notice).hexdigest()
        self.licenses = [
            {
                "id": ident,
                "component": "runtime",
                "licenseId": "MIT",
                "licenseName": "MIT",
                "sourceUrl": "https://example.invalid/license",
                "noticePath": "licenses/shared.txt",
                "noticeBytes": len(self.notice),
                "noticeSha256": digest,
            }
            for ident in ("runtime-a", "runtime-b")
        ]
        self.items = [
            {
                **{
                    key: license[key]
                    for key in (
                        "id",
                        "component",
                        "licenseId",
                        "licenseName",
                        "sourceUrl",
                        "noticePath",
                    )
                },
                "archivePath": "materials/licenses/shared.txt",
                "bytes": len(self.notice),
                "sha256": digest,
            }
            for license in self.licenses
        ]
        self.items.sort(key=lambda item: item["id"])
        self.index = (
            json.dumps(
                {"schemaVersion": 1, "items": self.items}, ensure_ascii=False, indent=2
            )
            + "\n"
        ).encode()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def archive(
        self,
        entries: list[tuple[str, bytes, str]] | None = None,
        *,
        index: bytes | None = None,
    ) -> Path:
        rows = (
            entries
            if entries is not None
            else [
                ("index.json", self.index if index is None else index, "file"),
                ("materials/", b"", "dir"),
                ("materials/licenses/", b"", "dir"),
                ("materials/licenses/shared.txt", self.notice, "file"),
            ]
        )
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for name, data, kind in rows:
                member = tarfile.TarInfo(name)
                if kind == "dir":
                    member.type = tarfile.DIRTYPE
                    archive.addfile(member)
                elif kind == "symlink":
                    member.type = tarfile.SYMTYPE
                    member.linkname = "../../outside"
                    archive.addfile(member)
                elif kind == "hardlink":
                    member.type = tarfile.LNKTYPE
                    member.linkname = "index.json"
                    archive.addfile(member)
                elif kind == "special":
                    member.type = tarfile.FIFOTYPE
                    archive.addfile(member)
                else:
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
        path = self.root / "sources.tar.gz"
        path.write_bytes(stream.getvalue())
        return path

    def request(self, path: Path, *, index_sha: str | None = None) -> dict:
        data = path.read_bytes()
        return {
            "archivePath": str(path),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "licenses": self.licenses,
            "metadata": {
                "indexSha256": index_sha or hashlib.sha256(self.index).hexdigest(),
                "items": self.items,
            },
        }

    def assert_invalid(self, path: Path, *, index_sha: str | None = None) -> None:
        with self.assertRaises(InvalidArchive):
            verify_source_archive(self.request(path, index_sha=index_sha))

    def test_valid_shared_notice_maps_to_one_physical_file(self) -> None:
        result = verify_source_archive(self.request(self.archive()))
        self.assertEqual(result, {"valid": True})

    def test_rejects_extra_duplicate_case_collision_and_bad_paths(self) -> None:
        good = [
            ("index.json", self.index, "file"),
            ("materials/", b"", "dir"),
            ("materials/licenses/", b"", "dir"),
            ("materials/licenses/shared.txt", self.notice, "file"),
        ]
        for extra in (
            ("surprise.txt", b"x", "file"),
            ("materials/LICENSES/shared.txt", self.notice, "file"),
            ("../escape", b"x", "file"),
            ("/absolute", b"x", "file"),
            ("materials\\escape", b"x", "file"),
            ("materials/unexpected/", b"", "dir"),
        ):
            with self.subTest(name=extra[0]):
                self.assert_invalid(self.archive(good + [extra]))
        self.assert_invalid(self.archive(good + [good[-1]]))

    def test_rejects_links_special_truncated_and_digest_mismatch(self) -> None:
        good = [
            ("index.json", self.index, "file"),
            ("materials/", b"", "dir"),
            ("materials/licenses/", b"", "dir"),
            ("materials/licenses/shared.txt", self.notice, "file"),
        ]
        self.assert_invalid(self.archive(good + [("materials/link", b"", "symlink")]))
        self.assert_invalid(self.archive(good + [("materials/hard", b"", "hardlink")]))
        self.assert_invalid(self.archive(good + [("materials/fifo", b"", "special")]))
        self.assert_invalid(
            self.archive(
                good[:-1] + [("materials/licenses/shared.txt", b"changed", "file")]
            )
        )
        truncated = self.archive()
        truncated.write_bytes(truncated.read_bytes()[:-9])
        self.assert_invalid(truncated)

    def test_rejects_index_and_nonzero_hidden_tar_tail(self) -> None:
        self.assert_invalid(self.archive(), index_sha="0" * 64)
        wrong_index = (
            json.dumps({"schemaVersion": 2, "items": self.items}, indent=2).encode()
            + b"\n"
        )
        self.assert_invalid(self.archive(index=wrong_index))
        valid = self.archive().read_bytes()
        hidden = io.BytesIO()
        with tarfile.open(fileobj=hidden, mode="w") as archive:
            member = tarfile.TarInfo("hidden.txt")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        path = self.root / "hidden-tail.tar.gz"
        path.write_bytes(valid + gzip.compress(hidden.getvalue()))
        self.assert_invalid(path)

    def test_rejects_unbounded_pax_header_before_materialization(self) -> None:
        stream = io.BytesIO()
        with tarfile.open(
            fileobj=stream, mode="w:gz", format=tarfile.PAX_FORMAT
        ) as archive:
            for name, data, kind in (
                ("index.json", self.index, "file"),
                ("materials/", b"", "dir"),
                ("materials/licenses/", b"", "dir"),
                ("materials/licenses/shared.txt", self.notice, "file"),
            ):
                member = tarfile.TarInfo(name)
                if name == "index.json":
                    member.pax_headers = {"comment": "x" * 100_000}
                if kind == "dir":
                    member.type = tarfile.DIRTYPE
                    archive.addfile(member)
                else:
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
        path = self.root / "large-pax.tar.gz"
        path.write_bytes(stream.getvalue())
        self.assert_invalid(path)

    def test_rejects_sparse_formats_before_sparse_map_processing(self) -> None:
        pax = io.BytesIO()
        with tarfile.open(
            fileobj=pax, mode="w:gz", format=tarfile.PAX_FORMAT
        ) as archive:
            member = tarfile.TarInfo("index.json")
            member.pax_headers = {"GNU.sparse.map": "0,1", "GNU.sparse.size": "1"}
            member.size = len(self.index)
            archive.addfile(member, io.BytesIO(self.index))
        pax_path = self.root / "pax-sparse.tar.gz"
        pax_path.write_bytes(pax.getvalue())
        self.assert_invalid(pax_path)

        regular = bytearray(self.archive().read_bytes())
        uncompressed = bytearray(gzip.decompress(regular))
        header = uncompressed[:512]
        header[156:157] = b"S"
        header[148:156] = b"        "
        checksum = sum(header)
        header[148:156] = f"{checksum:06o}\0 ".encode()
        uncompressed[:512] = header
        sparse_path = self.root / "gnu-sparse.tar.gz"
        sparse_path.write_bytes(gzip.compress(uncompressed))
        self.assert_invalid(sparse_path)

    def test_rejects_declared_length_drift_and_shared_path_length_conflict(
        self,
    ) -> None:
        for license, item in zip(self.licenses, self.items, strict=True):
            license["noticeBytes"] += 1
            item["bytes"] += 1
        self.index = (
            json.dumps(
                {"schemaVersion": 1, "items": self.items},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode()
        archive = self.archive()
        request = self.request(
            archive, index_sha=hashlib.sha256(self.index).hexdigest()
        )
        with self.assertRaises(InvalidArchive):
            verify_source_archive(request)

        self.licenses[1]["noticeBytes"] += 1
        self.items[1]["bytes"] += 1
        conflict_index = (
            json.dumps(
                {"schemaVersion": 1, "items": self.items},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode()
        conflict_archive = self.archive(index=conflict_index)
        with self.assertRaises(InvalidArchive):
            verify_source_archive(
                self.request(
                    conflict_archive,
                    index_sha=hashlib.sha256(conflict_index).hexdigest(),
                )
            )


if __name__ == "__main__":
    unittest.main()
