from __future__ import annotations

from copy import deepcopy
from typing import Any

from exporters.gmc_feed_import import expand_gmc_variants, product_to_gmc_row
from models.product import Product
from utils.gmc_taxonomy import (
    GMC_AGE_GROUP_DEFAULT,
    normalize_age_group_for_export,
)

GMC_FEED_EDIT_COLUMNS = [
    "feed_key",
    "id",
    "title",
    "brand",
    "size",
    "color",
    "gender",
    "age group",
]


def build_gmc_feed_edit_rows(
    products: list[Product],
    row_ids: list[int],
    *,
    variant_mode: str = "all_variations",
    currency: str = "USD",
    condition: str = "new",
    google_category_default: str = "",
    gmc_defaults: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    gmc_defaults = gmc_defaults or {}
    age_default = normalize_age_group_for_export(gmc_defaults.get("age group"))
    gender_default = gmc_defaults.get("gender") or ""

    rows: list[dict[str, str]] = []
    for row_id, product in zip(row_ids, products):
        for variant in expand_gmc_variants(product, variant_mode):
            gmc_row = product_to_gmc_row(
                product,
                variant=variant,
                currency=currency,
                condition=condition,
                google_category_default=google_category_default,
                defaults=gmc_defaults,
            )
            variant_id = str(variant.get("id") if variant else product.id or row_id)
            rows.append(
                {
                    "feed_key": f"{row_id}:{variant_id}",
                    "id": gmc_row["id"],
                    "title": (gmc_row["title"] or "")[:80],
                    "brand": gmc_row["brand"],
                    "size": gmc_row["size"],
                    "color": gmc_row["color"],
                    "gender": gmc_row["gender"] or gender_default,
                    "age group": normalize_age_group_for_export(
                        gmc_row["age group"] or age_default
                    ),
                }
            )
    return rows


def merge_gmc_feed_edits(
    products_raw: list[Product],
    feed_edits: list[dict[str, Any]],
) -> None:
    """Ghi brand / size / color / gender / age group từ bảng feed vào products_raw."""
    if not feed_edits:
        return

    for edit in feed_edits:
        feed_key = str(edit.get("feed_key") or "")
        if ":" not in feed_key:
            continue
        row_id_text, variant_id = feed_key.split(":", 1)
        try:
            row_id = int(row_id_text)
        except ValueError:
            continue
        if row_id < 0 or row_id >= len(products_raw):
            continue

        product = products_raw[row_id]
        brand = str(edit.get("brand") or "").strip()
        gender = str(edit.get("gender") or "").strip()
        age_group = str(edit.get("age group") or "").strip()
        size = str(edit.get("size") or "").strip()
        color = str(edit.get("color") or "").strip()

        if brand:
            product.vendor = brand
        if gender:
            product.gmc_gender = gender
        product.gmc_age_group = normalize_age_group_for_export(
            age_group or product.gmc_age_group or GMC_AGE_GROUP_DEFAULT
        )

        variant_match = None
        for variant in product.variants:
            if str(variant.get("id") or "") == variant_id:
                variant_match = variant
                break

        if variant_match is not None:
            if size:
                variant_match["option1"] = size
            if color:
                variant_match["option2"] = color
        else:
            if size:
                product.gmc_size = size
            if color:
                product.gmc_color = color


def apply_gmc_feed_edits_to_products(
    products_raw: list[Product],
    edited_df_rows: list[dict],
    feed_edits: list[dict[str, Any]],
    export_format: str = "gmc-feed-csv",
) -> list[Product]:
    from utils.product_editor import products_from_edit_table

    working = deepcopy(products_raw)
    merge_gmc_feed_edits(working, feed_edits)
    return products_from_edit_table(working, edited_df_rows, export_format=export_format)
