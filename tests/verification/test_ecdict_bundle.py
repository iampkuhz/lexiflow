"""ECDICT 来源包的标准库 unittest 合同用例。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from ops.dataset import prepare_ecdict_bundle as bundle


def _row(word: str, translation: str = "中文释义", **changes: str) -> dict[str, str]:
    row = {
        "word": word,
        "phonetic": "",
        "definition": "meaning",
        "translation": translation,
        "pos": "n.",
        "collins": "0",
        "oxford": "",
        "tag": "",
        "bnc": "0",
        "frq": "0",
        "exchange": "",
        "detail": "",
        "audio": "",
    }
    row.update(changes)
    return row


def _csv(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=bundle.HEADER, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _full(rows: list[dict[str, str]]) -> bytes:
    present = {row["word"].strip().lower() for row in rows}
    return _csv(
        rows
        + [
            _row(word, "受信curated释义")
            for word in bundle.CURATED
            if word not in present
        ]
    )


class EcdictBundleTests(unittest.TestCase):
    def test_evidence_rows_are_protected_and_no_cap_or_alphabetic_selection(
        self,
    ) -> None:
        rows = [
            _row("ranked", bnc="9"),
            _row("tagged", tag="cet4"),
            _row("oxford", oxford="1"),
            _row("collins", collins="1"),
            _row("unknown-z"),
            _row("unknown-a"),
        ]
        selected, counts, audit = bundle._build_rows(_csv(rows), _full(rows))
        self.assertEqual(counts["baseline_unique"], 6)
        self.assertEqual(counts["protected_evidence"], 4)
        self.assertEqual(counts["final_unique_lemmas"], 9)
        self.assertFalse(audit)
        reversed_selected, _, _ = bundle._build_rows(
            _csv(list(reversed(rows))), _full(rows)
        )
        self.assertEqual(
            {item[2]["word"] for item in selected} - bundle.CURATED,
            {item[2]["word"] for item in reversed_selected} - bundle.CURATED,
        )

    def test_pure_proper_name_removed_but_mixed_sense_retained(self) -> None:
        rows = [_row("proper", "[人名] 约翰"), _row("mixed", "[人名] 约翰\n普通词义")]
        selected, counts, audit = bundle._build_rows(_csv(rows), _full(rows))
        self.assertEqual(
            {item[2]["word"] for item in selected} - bundle.CURATED, {"mixed"}
        )
        self.assertEqual(counts["removed:pure-proper-noun"], 1)
        self.assertEqual(audit[0]["source_file"], "ecdict.csv")
        self.assertEqual(audit[0]["reason"], "pure-proper-noun")

    def test_institution_only_abbreviation_and_general_abbreviations(self) -> None:
        rows = [
            _row("nsa", "美国国家安全局协会", pos="abbr."),
            _row("pce", "个人计算机电子产品", pos="abbr."),
            _row("usb", "通用串行总线", pos="abbr."),
        ]
        selected, counts, _ = bundle._build_rows(_csv(rows), _full(rows))
        self.assertEqual(
            {item[2]["word"] for item in selected} - bundle.CURATED, {"pce", "usb"}
        )
        self.assertEqual(counts["removed:cold-institution-abbreviation"], 1)

    def test_domain_requires_every_sense_to_have_approved_tag(self) -> None:
        rows = [
            _row("cold", "[医] 甲症\n[化] 乙物"),
            _row("mixed", "[医] 甲症\n普通义项"),
            _row("computer", "[计] 计算机义项"),
            _row("hyphenated", "普通义项"),
        ]
        selected, counts, _ = bundle._build_rows(_csv(rows), _full(rows))
        self.assertEqual(
            {item[2]["word"] for item in selected} - bundle.CURATED,
            {"mixed", "computer", "hyphenated"},
        )
        self.assertEqual(counts["removed:cold-domain-only"], 1)

    def test_inflection_duplicate_only_when_exchange_covers_and_no_independent_sense(
        self,
    ) -> None:
        rows = [
            _row("walk", "行走", exchange="3:walks/p:walked"),
            _row("walks", "walk的第三人称单数"),
            _row("walked", "walk的过去式\n步行的过去分词以外独立含义"),
            _row("guilefulness", "guileful的变形"),
        ]
        selected, counts, audit = bundle._build_rows(_csv(rows), _full(rows))
        words = {item[2]["word"] for item in selected}
        self.assertNotIn("walks", words)
        self.assertIn("walked", words)
        self.assertIn("guilefulness", words)
        self.assertEqual(counts["removed:covered-inflection-duplicate"], 1)
        self.assertEqual(audit[0]["word"], "walks")

    def test_network_and_hyphenated_unknowns_are_retained(self) -> None:
        rows = [
            _row("unknown-word", "[网络] 网络释义"),
            _row("hyphen-word", "中文义项"),
        ]
        selected, _, _ = bundle._build_rows(_csv(rows), _full(rows))
        self.assertEqual(
            {item[2]["word"] for item in selected} - bundle.CURATED,
            {"unknown-word", "hyphen-word"},
        )

    def test_oxford_rows_and_curated_replacements_are_source_bound(self) -> None:
        base = [
            _row("oxford-only", "", oxford="1"),
            _row("literally", "base meaning"),
            _row("sustainability", "base"),
        ]
        full = [
            _row("literally", "full literal"),
            _row("sustainability", "full sustainability"),
            _row("stream of data", "数据流"),
        ]
        selected, counts, _ = bundle._build_rows(_csv(base), _csv(full))
        by_word = {
            row["word"]: (source, row_number, row)
            for source, row_number, row in selected
        }
        self.assertEqual(by_word["literally"][0], "stardict.csv")
        self.assertEqual(by_word["stream of data"][0], "stardict.csv")
        self.assertEqual(by_word["oxford-only"][0], "ecdict.csv")
        self.assertEqual(counts["curated_full_source_rows"], 3)

    def test_literal_separators_and_same_line_mixed_senses_are_retained(self) -> None:
        rows = [
            _row("walk", "行走", exchange="p:walked/3:walks"),
            _row("walked", r"walk的过去式\n普通义项"),
            _row("walks", "walk的第三人称单数，散步"),
            _row("medic", "[医] 某病；普通词义"),
            _row("assoc", "某协会；普通词义", pos="abbr."),
            _row("person", r"[人名] 约翰\n普通词义"),
        ]
        selected, _, audit = bundle._build_rows(_csv(rows), _full(rows))
        self.assertEqual(
            {r["word"] for _, _, r in selected} - bundle.CURATED,
            {r["word"] for r in rows},
        )
        self.assertFalse(audit)

    def test_reference_chain_and_derived_coverage_gap(self) -> None:
        rows = [
            _row("run", "跑", exchange="3:runs"),
            _row("runs", "run的第三人称单数", exchange="p:runned"),
            _row("runned", "runs的过去式"),
            _row("unknowns", "未知的复数", exchange="0:unknown"),
        ]
        selected, counts, audit = bundle._build_rows(_csv(rows), _full(rows))
        words = {r["word"] for _, _, r in selected}
        self.assertNotIn("runs", words)
        self.assertIn("runned", words)
        self.assertEqual(counts["derived_uncovered"], 1)
        self.assertTrue(
            any(r["reason"] == "prefilter-derived-coverage-gap" for r in audit)
        )

    def test_serialized_rows_use_compact_schema_and_strict_source_mapping(self) -> None:
        rows = [("ecdict.csv", 2, _row("word")), ("stardict.csv", 8, _row("phrase"))]
        data, mapping = bundle._serialize(rows)
        parsed = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
        self.assertEqual(tuple(parsed[0]), bundle.COMPACT_HEADER)
        self.assertEqual(parsed[0]["translation"], "中文释义")
        self.assertEqual(
            mapping,
            b"source_row\n2\ns:8\n",
        )

    def test_compact_manifest_declares_schema_and_reversible_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "ecdict.csv").write_bytes(_csv([_row("word")]))
            (root / "LICENSE").write_bytes(b"public license")
            with (
                patch.object(bundle, "_source_lock", return_value={"files": {}}),
                patch.object(
                    bundle, "_read_full_csv", return_value=(_full([]), "hash")
                ),
                patch.object(bundle, "_verify_sources"),
            ):
                bundle.build(
                    root, root / "bundle.zip", generation_code_version="a" * 64
                )
            with zipfile.ZipFile(root / "bundle.zip") as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["schema_version"], 2)
                self.assertEqual(manifest["csv_columns"], list(bundle.COMPACT_HEADER))
                self.assertEqual(
                    manifest["source_row_mapping"],
                    {
                        "column": "source_row",
                        "subset_row": "data_record_ordinal_plus_2",
                        "ecdict.csv": "decimal_line_number",
                        "stardict.csv": "s:decimal_line_number",
                    },
                )
                mapping = archive.read("source-rows.csv")
                published = list(csv.DictReader(io.StringIO(mapping.decode("utf-8"))))
                self.assertEqual(len(published), 4)
                self.assertTrue(all(set(row) == {"source_row"} for row in published))
                self.assertEqual(
                    sum(row["source_row"].startswith("s:") for row in published), 3
                )
                compact = list(
                    csv.DictReader(
                        io.StringIO(archive.read("stardict.csv").decode("utf-8"))
                    )
                )
                self.assertEqual(len(compact), len(published))
                for name, data in (
                    ("source-rows.csv", mapping),
                    ("stardict.csv", archive.read("stardict.csv")),
                ):
                    self.assertEqual(
                        manifest["members"][name],
                        {
                            "bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        },
                    )

    def test_multiline_source_records_preserve_published_record_ordinals(self) -> None:
        base = [
            _row("before", "多行\n中文释义"),
            _row("oxford", "证据\n释义", oxford="1"),
            _row("after"),
        ]
        full = [
            _row("other", "全文\n释义"),
            _row("literally"),
            _row("sustainability"),
            _row("stream of data"),
        ]
        selected, _, _ = bundle._build_rows(_csv(base), _csv(full))
        data, mapping = bundle._serialize(selected)
        compact = list(csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")))
        markers = list(csv.DictReader(io.StringIO(mapping.decode("utf-8"), newline="")))
        expected = {
            "before": "2",
            "oxford": "3",
            "after": "4",
            "literally": "s:3",
            "sustainability": "s:4",
            "stream of data": "s:5",
        }
        self.assertEqual(len(compact), len(markers))
        for row, marker in zip(compact, markers, strict=True):
            self.assertEqual(marker, {"source_row": expected[row["word"]]})
        self.assertEqual(
            next(row for row in compact if row["word"] == "oxford")["translation"],
            "证据\n释义",
        )

    def test_duplicate_oxford_records_keep_distinct_source_ordinals(self) -> None:
        base = [
            _row("before", "多行\n释义"),
            _row("evidence", "第一条\n释义", oxford="1"),
            _row("evidence", "第二条", oxford="1"),
        ]
        selected, counts, _ = bundle._build_rows(_csv(base), _full([]))
        self.assertEqual(counts["oxford_support_rows_seen"], 2)
        data, mapping = bundle._serialize(selected)
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")))
        markers = list(csv.DictReader(io.StringIO(mapping.decode("utf-8"), newline="")))
        evidence = [
            (row["translation"], marker["source_row"])
            for row, marker in zip(rows, markers, strict=True)
            if row["word"] == "evidence"
        ]
        self.assertEqual(evidence, [("第一条\n释义", "3"), ("第二条", "4")])

    def test_source_reader_requires_exact_header_and_complete_record_shape(
        self,
    ) -> None:
        for invalid in (
            b"wrong,header\na,b\n",
            ",".join(bundle.HEADER).encode() + b"\nword\n",
            _csv([_row("word")]) + b"a,b,c,d,e,f,g,h,i,j,k,l,m,n\n",
            _csv([_row("word")]) + b"\xff\n",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(bundle.BundleError):
                list(bundle._iter_rows(invalid))

    def test_invalid_header_and_source_hash_fail_closed(self) -> None:
        with self.assertRaises(bundle.BundleError):
            list(bundle._iter_rows(b"wrong,header\na,b\n"))
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "stardict.csv").write_bytes(b"untrusted")
            with self.assertRaisesRegex(bundle.BundleError, "does not match"):
                bundle._read_full_csv(repo)

    def test_input_symlink_and_output_overwrite_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(bundle.BundleError):
                bundle._no_symlink_ancestors(link / "out.zip")
            output = root / "existing.zip"
            output.write_bytes(b"keep")
            with self.assertRaises(bundle.BundleError):
                bundle.build(root, output, generation_code_version="a" * 64)
            self.assertEqual(output.read_bytes(), b"keep")

    def test_build_rejects_limits_and_cleans_atomic_failures(self) -> None:
        for failure in (
            "csv-limit",
            "zip-limit",
            "source-changed",
            "publish-link",
            "report-move",
        ):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                (root / "ecdict.csv").write_bytes(_csv([_row("word")]))
                (root / "LICENSE").write_bytes(b"public license")
                output = root / "result.zip"
                with ExitStack() as stack:
                    stack.enter_context(
                        patch.object(bundle, "_source_lock", return_value={"files": {}})
                    )
                    stack.enter_context(
                        patch.object(
                            bundle, "_read_full_csv", return_value=(_full([]), "hash")
                        )
                    )
                    final_check = stack.enter_context(
                        patch.object(bundle, "_verify_sources")
                    )
                    if failure == "csv-limit":
                        stack.enter_context(patch.object(bundle, "MAX_CSV_BYTES", 1))
                    elif failure == "zip-limit":
                        stack.enter_context(patch.object(bundle, "MAX_ZIP_BYTES", 1))
                    elif failure == "source-changed":
                        final_check.side_effect = bundle.BundleError(
                            "source inputs changed during build"
                        )
                    elif failure == "publish-link":
                        stack.enter_context(
                            patch.object(
                                bundle.os,
                                "link",
                                side_effect=OSError("publish failure"),
                            )
                        )
                    else:
                        stack.enter_context(
                            patch.object(
                                bundle.shutil,
                                "move",
                                side_effect=OSError("report failure"),
                            )
                        )
                    with self.assertRaises((bundle.BundleError, OSError)):
                        bundle.build(root, output, generation_code_version="a" * 64)
                    if failure not in ("csv-limit", "zip-limit"):
                        final_check.assert_called_once()
                self.assertFalse(output.exists())
                self.assertFalse((root / "result.report").exists())
                self.assertEqual(
                    {p.name for p in root.iterdir()}, {"ecdict.csv", "LICENSE"}
                )

    def test_final_source_check_rejects_changed_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "ecdict.csv").write_bytes(b"modified")
            with (
                patch.object(bundle, "_source_lock", return_value={"files": {}}),
                patch.object(
                    bundle, "_read_full_csv", return_value=(b"full", "full-hash")
                ),
                self.assertRaisesRegex(bundle.BundleError, "changed"),
            ):
                bundle._verify_sources(root, {"files": {}}, "initial-hash", "full-hash")

    def test_zip_member_order_timestamp_mode_and_hashes(self) -> None:
        members = {
            "stardict.csv": b"csv",
            "source-rows.csv": b"map",
            "LICENSE": b"license",
            "manifest.json": b"{}\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.zip"
            with zipfile.ZipFile(
                path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive:
                for name in (
                    "stardict.csv",
                    "source-rows.csv",
                    "LICENSE",
                    "manifest.json",
                ):
                    archive.writestr(
                        bundle._zip_info(name),
                        members[name],
                        compress_type=zipfile.ZIP_DEFLATED,
                        compresslevel=9,
                    )
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist(), list(members))
                for info in archive.infolist():
                    self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                    self.assertEqual(info.external_attr >> 16, 0o100644)
                    self.assertEqual(
                        hashlib.sha256(archive.read(info.filename)).hexdigest(),
                        hashlib.sha256(members[info.filename]).hexdigest(),
                    )

    def test_extracted_csv_is_required_without_archive_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(bundle.subprocess, "Popen") as process,
                self.assertRaises(bundle.BundleError),
            ):
                bundle._read_full_csv(Path(directory))
            process.assert_not_called()

    def test_cli_rejects_generator_source_override(self):
        self.assertEqual(
            bundle.main(["--source-repo", "repo", "--output", "bundle.zip"]), 2
        )


if __name__ == "__main__":
    unittest.main()
