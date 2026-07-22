from __future__ import annotations

import math
from copy import deepcopy

from models.product import Product
from exporters.gmc_feed_import import expand_gmc_variants, product_to_gmc_row
from utils.export_selection import apply_export_selection_indexed
from utils.gmc_taxonomy import (
    category_id_from_option,
    resolve_gmc_age_group_display,
    resolve_gmc_category_display,
    resolve_gmc_gender_display,
)

EDIT_COLUMNS = [
    "row_id",
    "title",
    "price",
    "categories",
    "tags",
    "description",
    "product_type",
    "vendor",
    "sku",
]

GMC_EDIT_COLUMNS = [
    "row_id",
    "title",
    "price",
    "description",
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
]


def edit_columns_for_format(
    export_format: str,
    *,
    include_url: bool = False,
) -> list[str]:
    if export_format == "gmc-feed-csv":
        return list(GMC_EDIT_COLUMNS)
    columns = list(EDIT_COLUMNS)
    if include_url and "url" not in columns:
        columns.append("url")
    return columns


def _is_missing_price(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    text = str(value).strip().lower()
    return text in {"", "nan", "none", "null"}


def _primary_gmc_variant(product: Product) -> dict | None:
    expanded = expand_gmc_variants(product, "all_variations")
    return expanded[0] if expanded else None


def _product_to_gmc_row(
    product: Product,
    index: int,
    *,
    google_category_default: str = "",
    gmc_defaults: dict[str, str] | None = None,
    gmc_condition: str = "new",
) -> dict:
    gmc_defaults = gmc_defaults or {}
    variant = _primary_gmc_variant(product)
    gmc_row = product_to_gmc_row(
        product,
        variant=variant,
        condition=gmc_condition,
        google_category_default=google_category_default,
        defaults=gmc_defaults,
    )
    return {
        "row_id": index,
        "title": product.title,
        "price": product.price,
        "description": product.description or "",
        "vendor": product.vendor or "",
        "sku": product.sku or "",
        "gtin": product.gtin or "",
        "google_product_category": resolve_gmc_category_display(
            product, google_category_default
        ),
        "gmc_size": gmc_row["size"],
        "gmc_color": gmc_row["color"],
        "gmc_condition": gmc_row["condition"],
        "gmc_availability": gmc_row["availability"],
        "gmc_gender": resolve_gmc_gender_display(product, gmc_defaults.get("gender", "")),
        "gmc_age_group": resolve_gmc_age_group_display(
            product, gmc_defaults.get("age group", "")
        ),
    }


def products_to_rows(
    products: list[Product],
    export_format: str = "woocommerce-csv",
    *,
    google_category_default: str = "",
    gmc_defaults: dict[str, str] | None = None,
    gmc_condition: str = "new",
    include_url: bool = False,
) -> list[dict]:
    rows = []
    for index, product in enumerate(products):
        if export_format == "gmc-feed-csv":
            rows.append(
                _product_to_gmc_row(
                    product,
                    index,
                    google_category_default=google_category_default,
                    gmc_defaults=gmc_defaults,
                    gmc_condition=gmc_condition,
                )
            )
            continue
        row = {
            "row_id": index,
            "title": product.title,
            "price": product.price,
            "categories": ", ".join(product.categories)
            or (product.product_type or "").strip(),
            "tags": ", ".join(product.tags),
            "description": product.description or "",
            "product_type": product.product_type or "",
            "vendor": product.vendor or "",
            "sku": product.sku or "",
        }
        if include_url:
            row["url"] = product.url or ""
        rows.append(row)
    return rows


def preview_rows(products: list[Product], limit: int = 10) -> list[dict]:
    rows = []
    for index, product in enumerate(products[:limit]):
        rows.append(
            {
                "#": index + 1,
                "title": product.title,
                "price": product.price,
                "categories": ", ".join(product.categories) or (product.product_type or ""),
                "tags": ", ".join(product.tags[:5]),
                "vendor": product.vendor or "",
                "sku": product.sku or "",
                "image": product.image_urls[0] if product.image_urls else "",
                "url": product.url or "",
            }
        )
    return rows


def _split_csv(value: str) -> list[str]:
    if not value or not str(value).strip():
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _row_index(row: dict) -> int | None:
    if "row_id" in row:
        return int(row["row_id"])
    if "_index" in row:
        return int(row["_index"])
    return None


def _merge_gmc_fields(product: Product, row: dict) -> None:
    product.title = str(row.get("title") or product.title).strip()
    product.description = str(row.get("description") or "").strip() or None
    product.vendor = str(row.get("vendor") or "").strip() or None
    product.sku = str(row.get("sku") or "").strip() or None
    product.gtin = str(row.get("gtin") or "").strip() or None
    category_raw = category_id_from_option(row.get("google_product_category"))
    product.google_product_category = category_raw.strip() or None
    product.gmc_size = str(row.get("gmc_size") or "").strip() or None
    product.gmc_color = str(row.get("gmc_color") or "").strip() or None
    product.gmc_condition = str(row.get("gmc_condition") or "").strip() or None
    product.gmc_gender = str(row.get("gmc_gender") or "").strip() or None
    product.gmc_age_group = str(row.get("gmc_age_group") or "").strip() or None

    price_raw = row.get("price")
    if price_raw is not None and not _is_missing_price(price_raw):
        try:
            product.price = round(float(price_raw), 2)
        except (TypeError, ValueError):
            pass


def merge_edited_rows(
    products: list[Product],
    edited_rows: list[dict],
    export_format: str = "woocommerce-csv",
) -> list[Product]:
    if not edited_rows:
        return products

    index_map = {}
    for row in edited_rows:
        idx = _row_index(row)
        if idx is not None:
            index_map[idx] = row

    merged: list[Product] = []

    for index, product in enumerate(products):
        row = index_map.get(index)
        if not row:
            merged.append(product)
            continue

        if export_format == "gmc-feed-csv":
            _merge_gmc_fields(product, row)
            merged.append(product)
            continue

        product.title = str(row.get("title") or product.title).strip()
        product.categories = _split_csv(str(row.get("categories") or ""))
        product.tags = _split_csv(str(row.get("tags") or ""))
        product.description = str(row.get("description") or "").strip() or None
        product.product_type = str(row.get("product_type") or "").strip() or None
        product.vendor = str(row.get("vendor") or "").strip() or None
        product.sku = str(row.get("sku") or "").strip() or None
        if "url" in row:
            product.url = str(row.get("url") or "").strip() or None

        price_raw = row.get("price")
        if price_raw is not None and not _is_missing_price(price_raw):
            try:
                product.price = round(float(price_raw), 2)
            except (TypeError, ValueError):
                pass

        merged.append(product)
    return merged


def products_from_edit_table(
    products: list[Product],
    edited_rows: list[dict],
    export_format: str = "woocommerce-csv",
) -> list[Product]:
    """Lấy đúng các SP trong bảng chỉnh sửa (theo thứ tự hiển thị)."""
    if not edited_rows:
        return []

    merged = merge_edited_rows(deepcopy(products), edited_rows, export_format=export_format)
    ordered: list[Product] = []
    for row in edited_rows:
        idx = _row_index(row)
        if idx is not None and 0 <= idx < len(merged):
            ordered.append(merged[idx])
    return ordered


def build_edit_subset_rows(
    products: list[Product],
    quantity: int = 0,
    order: str = "original",
    export_format: str = "woocommerce-csv",
    *,
    google_category_default: str = "",
    gmc_defaults: dict[str, str] | None = None,
    gmc_condition: str = "new",
    include_url: bool = False,
) -> list[dict]:
    indexed = list(enumerate(products))
    selected = apply_export_selection_indexed(indexed, quantity=quantity, order=order)
    rows: list[dict] = []
    for row_id, product in selected:
        row = products_to_rows(
            [product],
            export_format=export_format,
            google_category_default=google_category_default,
            gmc_defaults=gmc_defaults,
            gmc_condition=gmc_condition,
            include_url=include_url,
        )[0]
        row["row_id"] = row_id
        rows.append(row)
    return rows
