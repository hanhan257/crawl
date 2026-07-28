"""Lọc trùng / lọc từ khóa trên file feed lớn (stream CSV, hỗ trợ ~3GB)."""

from __future__ import annotations

import csv
import hashlib
import html
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from utils.csv_import import COLUMN_ALIASES

DEDUP_FIELDS = ("title", "price", "categories", "tags", "description")

FIELD_LABELS = {
    "title": "Title",
    "price": "Giá",
    "categories": "Cate",
    "tags": "Tag",
    "description": "Des",
}

# Cột Shopify export thường gặp
_EXTRA_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "price": ("variant price", "variant price", "price / discounted price"),
    "categories": ("product category", "product type"),
    "tags": ("tags",),
    "description": ("body (html)", "body html", "description"),
}


def _normalize_header(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def _build_header_map(columns: list[str]) -> dict[str, str]:
    normalized = {_normalize_header(col): col for col in columns}
    mapping: dict[str, str] = {}
    for field_name in DEDUP_FIELDS:
        aliases = list(COLUMN_ALIASES.get(field_name, ())) + list(
            _EXTRA_ALIASES.get(field_name, ())
        )
        for alias in aliases:
            if alias in normalized:
                mapping[field_name] = normalized[alias]
                break
    return mapping


def normalize_cell(value: object) -> str:
    text = html.unescape(str(value or "")).strip().lower()
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    if text in {"nan", "none", "null"}:
        return ""
    return text


def normalize_price(value: object) -> str:
    text = normalize_cell(value)
    if not text:
        return ""
    text = text.replace(",", "").replace("$", "").replace("€", "").replace("£", "")
    try:
        return f"{float(text):.2f}"
    except ValueError:
        return text


def parse_keywords(raw: str) -> list[str]:
    parts = re.split(r"[,;\n]+", str(raw or ""))
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = normalize_cell(part)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _key_hash(parts: Iterable[str]) -> str:
    blob = "\x1f".join(parts).encode("utf-8", errors="replace")
    return hashlib.md5(blob).hexdigest()


def _sniff_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t|;")
    except csv.Error:
        class _Comma(csv.Dialect):
            delimiter = ","
            quotechar = '"'
            doublequote = True
            skipinitialspace = False
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL

        return _Comma()


def save_upload_streaming(uploaded, *, suffix: str = ".csv") -> Path:
    """Ghi upload ra ổ đĩa theo chunk — tránh load hết 3GB vào RAM."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        uploaded.seek(0)
    except Exception:
        pass
    while True:
        chunk = uploaded.read(8 * 1024 * 1024)
        if not chunk:
            break
        tmp.write(chunk)
    tmp.close()
    return Path(tmp.name)


@dataclass
class DedupeFilterResult:
    output_path: Path
    total_rows: int = 0
    kept_rows: int = 0
    removed_rows: int = 0
    keyword_matched: int = 0
    duplicate_groups: int = 0
    field_columns: dict[str, str] = field(default_factory=dict)
    mode: str = "unique"
    dedupe_fields: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


def filter_feed_file(
    input_path: Path,
    output_path: Path,
    *,
    dedupe_fields: list[str] | None = None,
    mode: str = "unique",
    title_keywords: str = "",
    keyword_mode: str = "any",
) -> DedupeFilterResult:
    """
    mode:
      - unique: giữ bản đầu, bỏ dòng trùng (theo tổ hợp cột đã chọn)
      - duplicates: chỉ giữ các dòng thuộc nhóm trùng (≥2 lần)
      - keyword_only: chỉ lọc theo từ khóa title (không dedupe)

    Nhiều cột dedupe được tích = AND (title giống VÀ giá giống …).
    """
    fields = [f for f in (dedupe_fields or []) if f in DEDUP_FIELDS]
    keywords = parse_keywords(title_keywords)
    if not fields and not keywords:
        raise ValueError(
            "Chọn ít nhất một ô lọc trùng (title/giá/cate/tag/des) "
            "hoặc nhập từ khóa trong title."
        )

    if mode not in {"unique", "duplicates", "keyword_only"}:
        mode = "unique"
    if not fields:
        mode = "keyword_only"

    # Đọc header
    with input_path.open("r", encoding="utf-8-sig", newline="", errors="replace") as fh:
        sample = fh.read(65536)
        fh.seek(0)
        dialect = _sniff_dialect(sample)
        reader = csv.DictReader(fh, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("File CSV không có header.")
        columns = [str(c) for c in reader.fieldnames if c is not None]
        header_map = _build_header_map(columns)

    missing = [f for f in fields if f not in header_map]
    if missing:
        labels = ", ".join(FIELD_LABELS.get(f, f) for f in missing)
        raise ValueError(f"Không tìm thấy cột: {labels}. Header hiện có: {', '.join(columns[:12])}")

    if keywords and "title" not in header_map:
        raise ValueError("Lọc từ khóa title cần cột title/name trong file.")

    title_col = header_map.get("title")
    field_cols = {f: header_map[f] for f in fields}

    def row_matches_keyword(row: dict[str, str]) -> bool:
        if not keywords:
            return True
        title = normalize_cell(row.get(title_col or "", ""))
        if not title:
            return False
        if keyword_mode == "all":
            return all(k in title for k in keywords)
        return any(k in title for k in keywords)

    def composite_key(row: dict[str, str]) -> str:
        parts: list[str] = []
        for field_name in fields:
            col = field_cols[field_name]
            raw = row.get(col, "")
            if field_name == "price":
                parts.append(normalize_price(raw))
            else:
                parts.append(normalize_cell(raw))
        return _key_hash(parts)

    result = DedupeFilterResult(
        output_path=output_path,
        mode=mode,
        dedupe_fields=list(fields),
        keywords=list(keywords),
        field_columns=dict(field_cols),
    )
    if title_col:
        result.field_columns["title"] = title_col

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # --- keyword_only: 1 pass ---
    if mode == "keyword_only":
        with input_path.open(
            "r", encoding="utf-8-sig", newline="", errors="replace"
        ) as src, output_path.open(
            "w", encoding="utf-8-sig", newline=""
        ) as dst:
            reader = csv.DictReader(src, dialect=dialect)
            writer = csv.DictWriter(dst, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in reader:
                result.total_rows += 1
                if row_matches_keyword(row):
                    writer.writerow(row)
                    result.kept_rows += 1
                    result.keyword_matched += 1
                else:
                    result.removed_rows += 1
        return result

    # --- Pass 1: đếm key (chỉ trên dòng khớp keyword nếu có) ---
    counts: dict[str, int] = {}
    with input_path.open("r", encoding="utf-8-sig", newline="", errors="replace") as src:
        reader = csv.DictReader(src, dialect=dialect)
        for row in reader:
            result.total_rows += 1
            if not row_matches_keyword(row):
                continue
            result.keyword_matched += 1
            key = composite_key(row)
            counts[key] = counts.get(key, 0) + 1

    result.duplicate_groups = sum(1 for n in counts.values() if n > 1)

    # --- Pass 2: ghi kết quả ---
    seen_first: set[str] = set()
    with input_path.open(
        "r", encoding="utf-8-sig", newline="", errors="replace"
    ) as src, output_path.open("w", encoding="utf-8-sig", newline="") as dst:
        reader = csv.DictReader(src, dialect=dialect)
        writer = csv.DictWriter(dst, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in reader:
            if not row_matches_keyword(row):
                result.removed_rows += 1
                continue
            key = composite_key(row)
            count = counts.get(key, 0)
            if mode == "unique":
                if key in seen_first:
                    result.removed_rows += 1
                    continue
                seen_first.add(key)
                writer.writerow(row)
                result.kept_rows += 1
            else:  # duplicates
                if count < 2:
                    result.removed_rows += 1
                    continue
                writer.writerow(row)
                result.kept_rows += 1

    return result


def preview_csv_head(path: Path, limit: int = 20) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="", errors="replace") as fh:
        sample = fh.read(65536)
        fh.seek(0)
        dialect = _sniff_dialect(sample)
        reader = csv.DictReader(fh, dialect=dialect)
        columns = [str(c) for c in (reader.fieldnames or []) if c]
        rows: list[dict[str, str]] = []
        for i, row in enumerate(reader):
            if i >= limit:
                break
            rows.append({k: str(row.get(k, "") or "") for k in columns})
    return columns, rows


@dataclass
class TwoFileCompareResult:
    output_path: Path
    dedupe_fields: list[str] = field(default_factory=list)
    file1_rows: int = 0
    file2_rows: int = 0
    match_rows: int = 0
    only_file1_rows: int = 0
    only_file2_rows: int = 0
    field_columns_file1: dict[str, str] = field(default_factory=dict)
    field_columns_file2: dict[str, str] = field(default_factory=dict)


def _read_csv_meta(path: Path) -> tuple[csv.Dialect, list[str], dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="", errors="replace") as fh:
        sample = fh.read(65536)
        fh.seek(0)
        dialect = _sniff_dialect(sample)
        reader = csv.DictReader(fh, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError(f"File không có header: {path.name}")
        columns = [str(c) for c in reader.fieldnames if c is not None]
        header_map = _build_header_map(columns)
    return dialect, columns, header_map


def _row_key(
    row: dict[str, str],
    *,
    fields: list[str],
    field_cols: dict[str, str],
) -> str:
    parts: list[str] = []
    for field_name in fields:
        col = field_cols[field_name]
        raw = row.get(col, "")
        if field_name == "price":
            parts.append(normalize_price(raw))
        else:
            parts.append(normalize_cell(raw))
    return _key_hash(parts)


def _stringify_row(row: dict[str, str], columns: list[str]) -> dict[str, str]:
    return {c: str(row.get(c, "") or "") for c in columns}


def compare_two_feed_files(
    file1_path: Path,
    file2_path: Path,
    output_path: Path,
    *,
    dedupe_fields: list[str] | None = None,
    file1_label: str = "file1",
    file2_label: str = "file2",
) -> TwoFileCompareResult:
    """
    So sánh 2 file feed theo các cột đã chọn (AND).
    Xuất Excel (.xlsx) 2 sheet:
      - trung: dòng khớp giữa 2 file
      - khong_trung: dòng chỉ có ở file 1 hoặc chỉ file 2
    """
    fields = [f for f in (dedupe_fields or []) if f in DEDUP_FIELDS]
    if not fields:
        raise ValueError(
            "Chọn ít nhất một cột để so (title / giá / des…) hoặc «Trùng hết»."
        )

    dialect1, columns1, map1 = _read_csv_meta(file1_path)
    dialect2, columns2, map2 = _read_csv_meta(file2_path)

    missing1 = [f for f in fields if f not in map1]
    missing2 = [f for f in fields if f not in map2]
    if missing1 or missing2:
        parts: list[str] = []
        if missing1:
            parts.append(
                f"File 1 thiếu: {', '.join(FIELD_LABELS.get(f, f) for f in missing1)}"
            )
        if missing2:
            parts.append(
                f"File 2 thiếu: {', '.join(FIELD_LABELS.get(f, f) for f in missing2)}"
            )
        raise ValueError(". ".join(parts))

    field_cols1 = {f: map1[f] for f in fields}
    field_cols2 = {f: map2[f] for f in fields}

    # Index file 2: key -> list rows
    index2: dict[str, list[dict[str, str]]] = {}
    file2_rows = 0
    with file2_path.open(
        "r", encoding="utf-8-sig", newline="", errors="replace"
    ) as src:
        reader = csv.DictReader(src, dialect=dialect2)
        for row in reader:
            file2_rows += 1
            key = _row_key(row, fields=fields, field_cols=field_cols2)
            index2.setdefault(key, []).append(_stringify_row(row, columns2))

    matched_keys: set[str] = set()
    match_rows: list[dict[str, str]] = []
    only1: list[dict[str, str]] = []
    file1_rows = 0

    # Union columns for match sheet readability
    union_cols = list(dict.fromkeys([*columns1, *columns2]))

    with file1_path.open(
        "r", encoding="utf-8-sig", newline="", errors="replace"
    ) as src:
        reader = csv.DictReader(src, dialect=dialect1)
        for row in reader:
            file1_rows += 1
            key = _row_key(row, fields=fields, field_cols=field_cols1)
            row1 = _stringify_row(row, columns1)
            if key in index2 and index2[key]:
                matched_keys.add(key)
                out = {c: "" for c in union_cols}
                out.update(row1)
                out["_nguon"] = file1_label
                out["_khoa_trung"] = key[:12]
                match_rows.append(out)
            else:
                out = dict(row1)
                out["_nguon"] = file1_label
                only1.append(out)

    only2: list[dict[str, str]] = []
    for key, rows in index2.items():
        if key in matched_keys:
            for row2 in rows:
                out = {c: "" for c in union_cols}
                out.update(row2)
                out["_nguon"] = file2_label
                out["_khoa_trung"] = key[:12]
                match_rows.append(out)
        else:
            for row2 in rows:
                out = dict(row2)
                out["_nguon"] = file2_label
                only2.append(out)

    khong_trung = only1 + only2

    output_path = Path(output_path)
    if output_path.suffix.lower() != ".xlsx":
        output_path = output_path.with_suffix(".xlsx")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import pandas as pd
    except ImportError as exc:
        raise ValueError("Cần pandas để xuất Excel.") from exc

    match_cols = ["_nguon", "_khoa_trung", *union_cols]
    # Sheet không trùng: union cột + _nguon
    only_union = list(dict.fromkeys(["_nguon", *columns1, *columns2]))

    df_match = pd.DataFrame(match_rows)
    if not df_match.empty:
        for c in match_cols:
            if c not in df_match.columns:
                df_match[c] = ""
        df_match = df_match[match_cols]
    else:
        df_match = pd.DataFrame(columns=match_cols)

    df_only = pd.DataFrame(khong_trung)
    if not df_only.empty:
        for c in only_union:
            if c not in df_only.columns:
                df_only[c] = ""
        df_only = df_only[only_union]
    else:
        df_only = pd.DataFrame(columns=only_union)

    try:
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            df_match.to_excel(writer, sheet_name="trung", index=False)
            df_only.to_excel(writer, sheet_name="khong_trung", index=False)
    except ImportError as exc:
        raise ValueError(
            "Cần gói openpyxl để xuất .xlsx — chạy: pip install openpyxl"
        ) from exc

    return TwoFileCompareResult(
        output_path=output_path,
        dedupe_fields=list(fields),
        file1_rows=file1_rows,
        file2_rows=file2_rows,
        match_rows=len(match_rows),
        only_file1_rows=len(only1),
        only_file2_rows=len(only2),
        field_columns_file1=dict(field_cols1),
        field_columns_file2=dict(field_cols2),
    )
