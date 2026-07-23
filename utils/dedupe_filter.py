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
