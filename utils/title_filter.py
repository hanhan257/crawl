"""Lọc file sản phẩm theo danh sách title."""

from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass

import pandas as pd

from utils.csv_import import COLUMN_ALIASES


def _normalize_header(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def _build_header_map(columns: list[str]) -> dict[str, str]:
    normalized = {_normalize_header(col): col for col in columns}
    mapping: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[field] = normalized[alias]
                break
    return mapping


def normalize_title(value: object) -> str:
    text = html.unescape(str(value or "")).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _read_table_bytes(data: bytes, filename: str = "") -> pd.DataFrame:
    name = (filename or "").lower()
    buffer = io.BytesIO(data)
    if name.endswith((".xlsx", ".xls")):
        try:
            return pd.read_excel(buffer, dtype=str)
        except ImportError as exc:
            raise ValueError(
                "Đọc Excel cần cài openpyxl. Hãy lưu file title/SP dạng CSV hoặc TXT."
            ) from exc
    # CSV / TXT / TSV
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")

    lines = text.splitlines()
    first = lines[0] if lines else ""
    if name.endswith(".txt") and "," not in first and "\t" not in first:
        # Một title mỗi dòng
        titles = [line.strip() for line in lines if line.strip()]
        return pd.DataFrame({"title": titles})

    sep = "\t" if name.endswith(".tsv") else ","
    try:
        return pd.read_csv(io.StringIO(text), dtype=str, sep=sep)
    except Exception:
        return pd.read_csv(io.StringIO(text), dtype=str, sep=None, engine="python")


def detect_title_column(columns: list[str]) -> str | None:
    header_map = _build_header_map(columns)
    if "title" in header_map:
        return header_map["title"]
    for col in columns:
        norm = _normalize_header(col)
        if norm in {"title", "name", "product name", "product_name", "tên", "ten", "tiêu đề", "tieu de"}:
            return col
    if columns:
        return columns[0]
    return None


def load_title_set(
    data: bytes,
    filename: str = "",
    *,
    title_column: str | None = None,
) -> tuple[set[str], list[str], str]:
    """Trả về (set title chuẩn hóa, danh sách gốc giữ thứ tự, tên cột dùng)."""
    df = _read_table_bytes(data, filename)
    if df.empty:
        return set(), [], ""

    columns = [str(c) for c in df.columns]
    col = title_column or detect_title_column(columns)
    if not col or col not in df.columns:
        raise ValueError("Không tìm thấy cột title trong file title.")

    ordered: list[str] = []
    normalized: set[str] = set()
    for raw in df[col].tolist():
        text = html.unescape(str(raw or "")).strip()
        if not text or text.lower() in {"nan", "none", "null"}:
            continue
        key = normalize_title(text)
        if not key or key in normalized:
            continue
        normalized.add(key)
        ordered.append(text)
    return normalized, ordered, col


@dataclass
class TitleFilterResult:
    matched_df: pd.DataFrame
    title_count: int
    product_count: int
    matched_count: int
    missing_titles: list[str]
    product_title_column: str
    title_column: str


def filter_products_by_titles(
    product_bytes: bytes,
    title_bytes: bytes,
    *,
    product_filename: str = "",
    title_filename: str = "",
    match_mode: str = "exact",
    product_title_column: str | None = None,
    title_column: str | None = None,
) -> TitleFilterResult:
    """
    match_mode:
      - exact: title SP khớp đúng (không phân biệt hoa thường)
      - contains: title SP chứa một title trong file (hoặc ngược lại)
    """
    title_set, ordered_titles, used_title_col = load_title_set(
        title_bytes,
        title_filename,
        title_column=title_column,
    )
    if not title_set:
        raise ValueError("File title trống hoặc không đọc được title nào.")

    products_df = _read_table_bytes(product_bytes, product_filename)
    if products_df.empty:
        raise ValueError("File sản phẩm trống.")

    columns = [str(c) for c in products_df.columns]
    prod_col = product_title_column or detect_title_column(columns)
    if not prod_col or prod_col not in products_df.columns:
        raise ValueError("Không tìm thấy cột title/name trong file sản phẩm.")

    mode = (match_mode or "exact").strip().lower()
    mask = []
    for raw in products_df[prod_col].tolist():
        key = normalize_title(raw)
        if not key:
            mask.append(False)
            continue
        if mode == "contains":
            hit = any(t in key or key in t for t in title_set)
        else:
            hit = key in title_set
        mask.append(hit)

    matched = products_df.loc[mask].copy()
    matched_keys = {normalize_title(v) for v in matched[prod_col].tolist()}
    missing = [
        title
        for title in ordered_titles
        if normalize_title(title) not in matched_keys
        and (
            mode != "contains"
            or not any(
                normalize_title(title) in mk or mk in normalize_title(title)
                for mk in matched_keys
            )
        )
    ]

    return TitleFilterResult(
        matched_df=matched,
        title_count=len(title_set),
        product_count=len(products_df),
        matched_count=len(matched),
        missing_titles=missing,
        product_title_column=prod_col,
        title_column=used_title_col,
    )


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8-sig")
