from __future__ import annotations

import math
from typing import Any

import pandas as pd

EDIT_FIELD_LABELS: dict[str, str] = {
    "title": "Title",
    "price": "Price ($)",
    "categories": "Categories",
    "tags": "Tags",
    "product_type": "Type",
    "vendor": "Vendor / Brand",
    "sku": "SKU / MPN",
    "description": "Description",
    "gtin": "GTIN",
    "google_product_category": "Google product category",
    "gmc_size": "Size",
    "gmc_color": "Color",
    "gmc_condition": "Condition",
    "gmc_availability": "Availability",
    "gmc_gender": "Gender",
    "gmc_age_group": "Age group",
}

REQUIRED_COLUMNS = (
    "title",
    "price",
    "categories",
    "tags",
    "description",
)

FORMAT_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "woocommerce-csv": REQUIRED_COLUMNS,
    "shopify-csv": REQUIRED_COLUMNS,
    "web-feed-csv": ("title", "price", "description"),
    "gmc-feed-csv": ("title", "price", "description"),
}

GMC_OPTIONAL_EDIT_COLUMNS = (
    "vendor",
    "sku",
    "gtin",
    "google_product_category",
    "gmc_size",
    "gmc_color",
    "gmc_condition",
    "gmc_availability",
    "gmc_gender",
    "gmc_age_group",
)


def _is_missing_price(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    text = str(value).strip().lower()
    return text in {"", "none", "null", "nan", "-"}


def _is_missing_text(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    text = str(value).strip()
    if not text:
        return True
    return text.lower() in {"none", "null", "nan"}


def _cell_missing(column: str, value: object) -> bool:
    if column == "price":
        return _is_missing_price(value)
    return _is_missing_text(value)


def required_columns_for_format(export_format: str) -> tuple[str, ...]:
    return FORMAT_REQUIRED_COLUMNS.get(export_format, REQUIRED_COLUMNS)


def validate_edited_df(
    df: pd.DataFrame,
    export_format: str = "woocommerce-csv",
) -> dict[str, Any]:
    """Kiểm tra ô trống theo cột — trả về báo cáo cho UI."""
    columns_missing: dict[str, int] = {}
    rows_with_issues: set[int] = set()
    issues_by_column: dict[str, list[int]] = {}

    for column in required_columns_for_format(export_format):
        if column not in df.columns:
            continue
        bad_rows: list[int] = []
        for _, row in df.iterrows():
            row_id = int(row.get("row_id", 0))
            if _cell_missing(column, row.get(column)):
                bad_rows.append(row_id)
                rows_with_issues.add(row_id)
        if bad_rows:
            columns_missing[column] = len(bad_rows)
            issues_by_column[column] = bad_rows

    total = len(df)
    ok = len(columns_missing) == 0

    return {
        "ok": ok,
        "total_rows": total,
        "rows_with_issues": len(rows_with_issues),
        "columns_missing": columns_missing,
        "issues_by_column": issues_by_column,
        "export_format": export_format,
        "required_columns": required_columns_for_format(export_format),
    }


def first_issue_row(report: dict[str, Any], column: str | None = None) -> int | None:
    """row_id đầu tiên còn thiếu (theo cột hoặc bất kỳ cột bắt buộc)."""
    issues = report.get("issues_by_column") or {}
    if column:
        rows = issues.get(column) or []
        return int(rows[0]) if rows else None
    for col in report.get("required_columns") or ():
        rows = issues.get(col) or []
        if rows:
            return int(rows[0])
    return None


def column_header_label(column: str, report: dict[str, Any]) -> str:
    base = EDIT_FIELD_LABELS.get(column, column)
    export_format = report.get("export_format", "woocommerce-csv")
    if export_format == "gmc-feed-csv" and column in GMC_OPTIONAL_EDIT_COLUMNS:
        return f"{base} (tùy chọn)"
    count = report["columns_missing"].get(column, 0)
    if count:
        return f"🔴 {base} ({count} thiếu)"
    return base
