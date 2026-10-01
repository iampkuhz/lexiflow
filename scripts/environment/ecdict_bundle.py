"""从固定公开 ECDICT 来源生成可复现的离线构建输入 ZIP。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPOSITORY_URL = "https://github.com/skywind3000/ECDICT.git"
COMMIT = "bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b"
ARCHIVE_MEMBER_SHA256 = (
    "88fce01e0a30524192a62e363d47eeb036fa17820d5826121b3b419fd67a3996"
)
ARCHIVE_MEMBER_BYTES = 232668349
LOCK_PATH = Path(__file__).resolve().parents[2] / "ops/dataset/ecdict-source.lock.json"
HEADER = (
    "word",
    "phonetic",
    "definition",
    "translation",
    "pos",
    "collins",
    "oxford",
    "tag",
    "bnc",
    "frq",
    "exchange",
    "detail",
    "audio",
)
COMPACT_HEADER = ("word", "translation", "oxford", "tag", "bnc", "frq", "exchange")
KEPT_FIELDS = frozenset(
    ("word", "translation", "oxford", "tag", "bnc", "frq", "exchange")
)
REMOVED_FIELDS = ("phonetic", "definition", "pos", "collins", "detail", "audio")
FILTER_VERSION = "ecdict-conservative-2"
MAX_CSV_BYTES = 32 * 1024 * 1024
MAX_ZIP_BYTES = 8 * 1024 * 1024
MAX_EXTRACT_BYTES = 256 * 1024 * 1024
WORD_RE = re.compile(r"[a-z]+(?:['-][a-z]+)*", re.ASCII)
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
CURATED = frozenset(("literally", "sustainability", "stream of data"))
DOMAIN_TAG_RE = re.compile(r"^\s*\[(医|化|矿|植|动)\]")
PROPER_TAG_RE = re.compile(r"^\s*\[(人名|地名)\]")
INSTITUTION_TERMS = ("协会", "学会", "联合会", "委员会", "研究会", "理事会", "基金会")
INFLECTION_RE = re.compile(
    r"(?:是|为|指)?\s*([A-Za-z][A-Za-z'-]*)\s*的?(?:变形|第三人称单数|过去式|过去分词|现在分词|复数)"
)


class BundleError(Exception):
    """表示可安全报告给调用者的构建错误。"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular(path: Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise BundleError(f"{label} unavailable") from exc
    if not stat.S_ISREG(mode):
        raise BundleError(f"{label} must be a regular non-link file")


def _no_symlink_ancestors(path: Path) -> None:
    absolute = path.absolute()
    for ancestor in (absolute, *absolute.parents):
        if ancestor.is_symlink():
            raise BundleError("path contains a symbolic-link component")


def _run_git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BundleError("source repository identity unavailable") from exc


def _source_lock(repo: Path) -> dict[str, Any]:
    _no_symlink_ancestors(repo)
    if (
        not repo.is_dir()
        or _run_git(repo, "remote", "get-url", "origin") != REPOSITORY_URL
        or _run_git(repo, "rev-parse", "HEAD") != COMMIT
    ):
        raise BundleError("source repository origin or commit differs from lock")
    try:
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError("source lock unavailable or invalid") from exc
    if lock.get("repository_url") != REPOSITORY_URL or lock.get("commit") != COMMIT:
        raise BundleError("source lock identity mismatch")
    hashes = lock.get("files")
    if not isinstance(hashes, dict) or set(hashes) != {
        "ecdict.csv",
        "stardict.7z",
        "LICENSE",
    }:
        raise BundleError("source lock file set invalid")
    actual = {}
    for name, expected in hashes.items():
        path = repo / name
        _regular(path, name)
        actual[name] = _sha256_file(path)
        if actual[name] != expected:
            raise BundleError(f"source hash mismatch: {name}")
    member = lock.get("archive_member")
    expected_member = {
        "archive": "stardict.7z",
        "member": "stardict.csv",
        "sha256": ARCHIVE_MEMBER_SHA256,
        "bytes": ARCHIVE_MEMBER_BYTES,
    }
    if member != expected_member:
        raise BundleError("source archive member lock mismatch")
    return {"files": actual, "archive_member": expected_member}


def _read_full_csv(repo: Path) -> tuple[bytes, str]:
    extracted = repo / "stardict.csv"
    if extracted.exists() or extracted.is_symlink():
        _regular(extracted, "stardict.csv")
        if extracted.stat().st_size > MAX_EXTRACT_BYTES:
            raise BundleError("source CSV exceeds extraction limit")
        data = extracted.read_bytes()
    else:
        proc = subprocess.Popen(
            ["tar", "-xOf", str(repo / "stardict.7z"), "stardict.csv"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        data = proc.stdout.read(MAX_EXTRACT_BYTES + 1)
        proc.stdout.close()
        if len(data) > MAX_EXTRACT_BYTES:
            proc.kill()
            proc.wait()
            raise BundleError("archive member exceeds extraction limit")
        if proc.wait() != 0:
            raise BundleError("failed to read locked archive member")
    digest = _sha256(data)
    if len(data) != ARCHIVE_MEMBER_BYTES or digest != ARCHIVE_MEMBER_SHA256:
        raise BundleError("full-source CSV does not match locked archive member")
    return data, digest


def _parse_rows(
    data: bytes, expected_header: tuple[str, ...] | None = None
) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(
            io.TextIOWrapper(io.BytesIO(data), encoding="utf-8-sig", newline="")
        )
        if expected_header and tuple(reader.fieldnames or ()) != expected_header:
            raise BundleError("source CSV header mismatch")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise BundleError("source CSV row shape invalid")
            rows.append(dict(row))
        return rows
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BundleError("source CSV parsing failed") from exc


def _iter_rows(data: bytes, expected_header: tuple[str, ...]):
    try:
        reader = csv.DictReader(
            io.TextIOWrapper(io.BytesIO(data), encoding="utf-8-sig", newline="")
        )
        if tuple(reader.fieldnames or ()) != expected_header:
            raise BundleError("source CSV header mismatch")
        for row_number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise BundleError("source CSV row shape invalid")
            yield row_number, dict(row)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BundleError("source CSV parsing failed") from exc


def _valid_word(word: str) -> bool:
    return all(WORD_RE.fullmatch(part) for part in word.strip().lower().split(" "))


def _is_ranked(row: dict[str, str]) -> bool:
    return any(value.isdigit() and int(value) > 0 for value in (row["bnc"], row["frq"]))


def _has_marker(row: dict[str, str]) -> bool:
    collins = row["collins"].strip()
    return (
        row["oxford"].strip() == "1"
        or bool(row["tag"].strip())
        or (collins.isdigit() and int(collins) > 0)
    )


def _translation_lines(text: str) -> list[str]:
    # 来源使用字面量 \n；先分开义项再判断，绝不改写最终保存的原文。
    decoded = text.replace(r"\r\n", "\n").replace(r"\n", "\n").replace(r"\r", "\n")
    return [part.strip() for part in re.split(r"[\n\r;；]+", decoded) if part.strip()]


def _pure_proper_noun(lines: list[str]) -> bool:
    return bool(lines) and all(
        PROPER_TAG_RE.match(line)
        or re.fullmatch(r".+[（(](?:人名|地名|公司名|作品名|品牌名)[）)]", line)
        for line in lines
    )


def _pure_institution_abbreviation(row: dict[str, str], lines: list[str]) -> bool:
    explicit = re.search(
        r"abbr\.|abbrev\.|缩写", row["pos"] + " " + row["translation"], re.IGNORECASE
    )
    if not explicit or not lines:
        return False
    # 只接受纯中文机构全称，以机构类型结尾；复合或带说明的未知义项保留。
    institution = re.compile(
        r"[\u3400-\u9fff]+(?:" + "|".join(INSTITUTION_TERMS) + r")"
    )
    cleaned = [re.sub(r"^abbr\.\s*", "", line, flags=re.IGNORECASE) for line in lines]
    return all(institution.fullmatch(line) for line in cleaned)


def _all_cold_domain(lines: list[str]) -> bool:
    return bool(lines) and all(DOMAIN_TAG_RE.match(line) for line in lines)


def _add_prototype_coverage(row: dict[str, str], covered: dict[str, set[str]]) -> None:
    prototype = row["word"].strip().lower()
    for token in row["exchange"].split("/"):
        match = re.fullmatch(
            r"([pd3rits]):([a-z][a-z'-]*)", token.strip(), re.IGNORECASE
        )
        if match:
            covered[match.group(2).lower()].add(prototype)


def _only_prototype_reference(
    lines: list[str], word: str, covered_by: set[str]
) -> bool:
    if not lines or not covered_by:
        return False
    # fullmatch 防止短独立义项被“中文字符数量”启发式吞掉。
    pattern = re.compile(
        r"(?:是|为|指)?\s*([A-Za-z][A-Za-z'-]*)\s*的(?:变形|第三人称单数|过去式|过去分词|现在分词|复数)[。.]?"
    )
    return all(
        (match := pattern.fullmatch(line))
        and match.group(1).lower() in covered_by
        and match.group(1).lower() != word
        for line in lines
    )


def _reason_candidates(
    row: dict[str, str], lines: list[str], covered_by: set[str]
) -> list[str]:
    reasons = []
    if _pure_proper_noun(lines):
        reasons.append("pure-proper-noun")
    if _pure_institution_abbreviation(row, lines):
        reasons.append("cold-institution-abbreviation")
    if _only_prototype_reference(lines, row["word"].strip().lower(), covered_by):
        reasons.append("covered-inflection-duplicate")
    if _all_cold_domain(lines):
        reasons.append("cold-domain-only")
    return reasons


def _build_rows(
    base_data: bytes, full_data: bytes
) -> tuple[list[tuple[str, int, dict[str, str]]], dict[str, Any], list[dict[str, Any]]]:
    base = _parse_rows(base_data, HEADER)
    full_index: dict[str, tuple[int, dict[str, str]]] = {}
    coverage: dict[str, set[str]] = defaultdict(set)
    for line_no, row in _iter_rows(full_data, HEADER):
        word = row["word"].strip().lower()
        if word in CURATED:
            full_index.setdefault(word, (line_no, row))
    # Baseline is single-word, source-order deduplicated; evidence is never quality-filtered.
    baseline: dict[str, tuple[int, dict[str, str]]] = {}
    prefilter = Counter()
    for line_no, row in enumerate(base, 2):
        key = row["word"].strip().lower()
        prefilter["base_physical_rows"] += 1
        if not _valid_word(row["word"]):
            prefilter["invalid_surface_rows"] += 1
        elif " " in row["word"].strip():
            prefilter["phrase_rows"] += 1
        elif not CHINESE_RE.search(row["translation"]):
            prefilter["without_chinese_rows"] += 1
        elif any(
            token.strip().startswith("0:") for token in row["exchange"].split("/")
        ):
            prefilter["derived_rows"] += 1
        elif key in baseline:
            prefilter["casefold_duplicate_rows"] += 1
        else:
            baseline.setdefault(key, (line_no, row))
    selected: list[tuple[str, int, dict[str, str]]] = []
    audits: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    selected_by_word: dict[str, tuple[str, int, dict[str, str]]] = {}
    # 词形引用自身不作为删词证据的原型，避免连锁删除或循环依赖。
    reference_words = {
        word
        for word, (_, row) in baseline.items()
        if any(
            INFLECTION_RE.search(line)
            for line in _translation_lines(row["translation"])
        )
    }
    safe_prototypes = set()
    for word, (_, row) in baseline.items():
        lines = _translation_lines(row["translation"])
        if (
            _is_ranked(row)
            or _has_marker(row)
            or (
                word not in reference_words
                and not _reason_candidates(row, lines, set())
            )
        ):
            safe_prototypes.add(word)
            _add_prototype_coverage(row, coverage)
    for word, (line_no, row) in baseline.items():
        counts["baseline"] += 1
        protected = _is_ranked(row) or _has_marker(row)
        if protected:
            counts["protected_evidence"] += 1
            reason_candidates = []
        else:
            lines = _translation_lines(row["translation"])
            reason_candidates = _reason_candidates(
                row, lines, coverage.get(word, set()) & safe_prototypes
            )
        reason = reason_candidates[0] if reason_candidates else None
        if reason:
            counts[f"removed:{reason}"] += 1
            audits.append(
                {
                    "source_file": "ecdict.csv",
                    "source_row": line_no,
                    "word": word,
                    "reason": reason,
                    "also_matched": reason_candidates[1:],
                }
            )
        else:
            counts["retained"] += 1
            if not protected:
                counts["unknown_or_unmatched_retained"] += 1
            selected_by_word[word] = ("ecdict.csv", line_no, row)
    # Keep every Oxford physical row, even if another physical row has the same lemma.
    oxford_rows = []
    for line_no, row in enumerate(base, 2):
        if row["oxford"].strip() == "1":
            counts["oxford_support_rows_seen"] += 1
            oxford_rows.append(("ecdict.csv", line_no, row))
    for word in CURATED:
        if word not in full_index:
            raise BundleError(f"curated source entry absent: {word}")
        full_row_no, full_row = full_index[word]
        selected_by_word[word] = ("stardict.csv", full_row_no, full_row)
        counts["curated_full_source_rows"] += 1
    selected = list(selected_by_word.values())
    existing_physical_rows = {
        (source_file, source_row) for source_file, source_row, _ in selected
    }
    for source_file, source_row, row in oxford_rows:
        if (source_file, source_row) not in existing_physical_rows:
            selected.append((source_file, source_row, row))
            existing_physical_rows.add((source_file, source_row))
            counts["oxford_support_rows_added"] += 1
    selected.sort(key=lambda item: (item[2]["word"].strip().lower(), item[0], item[1]))
    final_coverage: dict[str, set[str]] = defaultdict(set)
    for _, _, row in selected:
        _add_prototype_coverage(row, final_coverage)
    # 预筛中的 0: 不等于已证明无损：逐条报告其实际保留原型覆盖情况。
    for line_no, row in enumerate(base, 2):
        prototypes = {
            token[2:].strip().lower()
            for token in row["exchange"].split("/")
            if token.startswith("0:")
        }
        if (
            not prototypes
            or not _valid_word(row["word"])
            or not CHINESE_RE.search(row["translation"])
        ):
            continue
        word = row["word"].strip().lower()
        covered = bool(prototypes & final_coverage.get(word, set()))
        counts["derived_covered" if covered else "derived_uncovered"] += 1
        if not covered:
            audits.append(
                {
                    "source_file": "ecdict.csv",
                    "source_row": line_no,
                    "word": word,
                    "reason": "prefilter-derived-coverage-gap",
                    "also_matched": [],
                    "prototypes": sorted(prototypes),
                }
            )
    counts["final_unique_lemmas"] = len(
        {item[2]["word"].strip().lower() for item in selected}
    )
    counts["final_physical_rows"] = len(selected)
    counts["removed_total"] = sum(
        value for key, value in counts.items() if key.startswith("removed:")
    )
    counts["baseline_unique"] = len(baseline)
    counts.update(prefilter)
    return selected, dict(sorted(counts.items())), audits


def _serialize(rows: list[tuple[str, int, dict[str, str]]]) -> tuple[bytes, bytes]:
    data = io.StringIO(newline="")
    writer = csv.DictWriter(
        data, fieldnames=COMPACT_HEADER, lineterminator="\n", extrasaction="raise"
    )
    writer.writeheader()
    mapping = io.StringIO(newline="")
    map_writer = csv.writer(mapping, lineterminator="\n")
    map_writer.writerow(("source_row",))
    for subset_line, (source_file, source_line, row) in enumerate(rows, 2):
        writer.writerow({field: row[field] for field in COMPACT_HEADER})
        marker = str(source_line) if source_file == "ecdict.csv" else f"s:{source_line}"
        map_writer.writerow((marker,))
    data_bytes = data.getvalue().encode("utf-8")
    map_bytes = mapping.getvalue().encode("utf-8")
    if len(data_bytes) > MAX_CSV_BYTES:
        raise BundleError("expanded CSV exceeds 32 MiB limit")
    return data_bytes, map_bytes


def decode_source_row(marker: str, subset_row: int) -> tuple[str, int, int]:
    """严格解码来源行标记并还原来源文件、来源行与子集行。"""
    if (
        not isinstance(marker, str)
        or not isinstance(subset_row, int)
        or isinstance(subset_row, bool)
        or subset_row < 2
    ):
        raise BundleError("source row mapping invalid")
    match = re.fullmatch(r"(s:)?([1-9][0-9]*)", marker)
    if not match:
        raise BundleError("source row marker invalid")
    source_row = int(match.group(2))
    if source_row < 2:
        raise BundleError("source row must be at least 2")
    return ("stardict.csv" if match.group(1) else "ecdict.csv", source_row, subset_row)


def decode_source_rows(data: bytes) -> list[tuple[str, int, int]]:
    """校验紧凑映射的单列表头和行结构，并按记录顺序还原来源。"""
    rows = _parse_rows(data, ("source_row",))
    return [
        decode_source_row(row["source_row"], index) for index, row in enumerate(rows, 2)
    ]


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _verify_sources(
    repo: Path, initial: dict[str, Any], base_hash: str, full_hash: str
) -> None:
    current = _source_lock(repo)
    _, current_full_hash = _read_full_csv(repo)
    if (
        current != initial
        or _sha256_file(repo / "ecdict.csv") != base_hash
        or current_full_hash != full_hash
    ):
        raise BundleError("source inputs changed during build")


def build(source_repo: Path, output: Path) -> dict[str, Any]:
    """校验公开来源并生成不可覆盖、可复现的词库构建输入包。"""
    repo = source_repo.expanduser().absolute()
    target = output.expanduser().absolute()
    _no_symlink_ancestors(target.parent)
    if target.exists() or target.is_symlink():
        raise BundleError("output already exists; refusing overwrite")
    if not target.parent.is_dir():
        raise BundleError("output parent must be an existing directory")
    lock = _source_lock(repo)
    base_path = repo / "ecdict.csv"
    base_bytes = base_path.read_bytes()
    base_hash = _sha256(base_bytes)
    full_bytes, full_hash = _read_full_csv(repo)
    rows, counts, audit = _build_rows(base_bytes, full_bytes)
    data, row_map = _serialize(rows)
    license_bytes = (repo / "LICENSE").read_bytes()
    members = {
        "stardict.csv": data,
        "source-rows.csv": row_map,
        "LICENSE": license_bytes,
    }
    member_facts = {
        name: {"bytes": len(value), "sha256": _sha256(value)}
        for name, value in members.items()
    }
    manifest = {
        "schema_version": 2,
        "filter_version": FILTER_VERSION,
        "upstream": {
            "repository_url": REPOSITORY_URL,
            "commit": COMMIT,
            "files": lock["files"],
            "archive_member": {
                "name": "stardict.csv",
                "bytes": len(full_bytes),
                "sha256": full_hash,
            },
        },
        "members": member_facts,
        "selection": counts,
        "removed_fields": list(REMOVED_FIELDS),
        "csv_columns": list(COMPACT_HEADER),
        "source_row_mapping": {
            "column": "source_row",
            "subset_row": "data_record_ordinal_plus_2",
            "ecdict.csv": "decimal_line_number",
            "stardict.csv": "s:decimal_line_number",
        },
        "source_files": ["ecdict.csv", "stardict.csv"],
        "generator_sha256": _sha256_file(Path(__file__)),
    }
    members["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    temp_dir = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    )
    temp = temp_dir / target.name
    report_dir = temp_dir / "report"
    report_dir.mkdir()
    (report_dir / "excluded-rows.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in audit
        ),
        encoding="utf-8",
    )
    (report_dir / "summary.json").write_text(
        json.dumps(counts, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    published_output = False
    published_report = False
    report_target = target.parent / f"{target.stem}.report"
    try:
        if report_target.exists() or report_target.is_symlink():
            raise BundleError("report target already exists; refusing overwrite")
        with zipfile.ZipFile(
            temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for name in ("stardict.csv", "source-rows.csv", "LICENSE", "manifest.json"):
                archive.writestr(
                    _zip_info(name),
                    members[name],
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
        if temp.stat().st_size > MAX_ZIP_BYTES:
            raise BundleError("compressed source ZIP exceeds 8 MiB limit")
        _verify_sources(repo, lock, base_hash, full_hash)
        os.link(temp, target)
        published_output = True
        temp.unlink()
        report_target.mkdir()
        published_report = True
        shutil.move(
            str(report_dir / "summary.json"), str(report_target / "summary.json")
        )
        shutil.move(
            str(report_dir / "excluded-rows.jsonl"),
            str(report_target / "excluded-rows.jsonl"),
        )
    except Exception:
        if published_output:
            target.unlink(missing_ok=True)
        if published_report:
            shutil.rmtree(report_target, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return {
        "output_bytes": target.stat().st_size,
        "output_sha256": _sha256_file(target),
        "selection": counts,
    }


def main(argv: list[str] | None = None) -> int:
    """解析来源与输出路径，报告构建结果。"""
    parser = argparse.ArgumentParser(
        description="Build a deterministic ECDICT compact source ZIP"
    )
    parser.add_argument("--source-repo", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build(args.source_repo, args.output), sort_keys=True))
    except BundleError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
