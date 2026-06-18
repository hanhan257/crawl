from __future__ import annotations

import html
import re

from exporters.export import CSV_COLUMNS
from exporters.image_urls import (
    max_exportable_images_in_products,
    resolve_image_column_count,
    woocommerce_image_column_names,
)
from exporters.gmc_feed_import import GMC_FEED_COLUMNS, expand_gmc_variants, product_to_gmc_row
from exporters.shopify_import import SHOPIFY_COLUMNS, product_to_shopify_rows
from exporters.web_feed_import import WEB_FEED_COLUMNS, product_to_web_feed_row
from exporters.woocommerce_import import WOOCOMMERCE_COLUMNS, product_to_woocommerce_row
from models.export_fields import ExportFields
from models.export_rules import ExportRules, apply_export_rules
from models.product import Product
from utils.product_editor import preview_rows

FORMAT_LABELS = {
    "woocommerce-csv": "CSV import WooCommerce",
    "shopify-csv": "CSV import Shopify",
    "web-feed-csv": "CSV feed web thuần",
    "gmc-feed-csv": "CSV feed GMC (Google Merchant)",
    "csv": "CSV thô",
    "json": "JSON",
}

GMC_PREVIEW_COLUMNS = [
    "id",
    "title",
    "link",
    "price",
    "availability",
    "image link",
    "gtin",
    "mpn",
    "brand",
    "google product category",
    "size",
    "gender",
    "age group",
    "color",
]

WOO_PREVIEW_BASE = ["Name", "SKU", "Regular price", "Categories", "Tags"]

SHOPIFY_PREVIEW_COLUMNS = [
    "Handle",
    "Title",
    "Variant Price",
    "Product Category",
    "Tags",
    "Image Src",
    "Body (HTML)",
]


def _truncate(value: object, limit: int = 100) -> str:
    text = re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _clean_title(title: str) -> str:
    return _truncate(html.unescape(title or ""), 80)


def build_product_preview_table(products: list[Product], limit: int = 10) -> list[dict]:
    rows = preview_rows(products, limit=limit)
    for row in rows:
        row["title"] = _clean_title(str(row.get("title", "")))
        image = str(row.get("image") or "")
        if image:
            row["image"] = _truncate(image, 60)
    return rows


def _woo_preview_columns(
    products: list[Product],
    image_mode: str,
    max_images_per_product: int | None = None,
) -> list[str]:
    if image_mode in {"comma", "pipe", "first_only"}:
        return WOO_PREVIEW_BASE + ["Images"]
    count = resolve_image_column_count(products, image_mode, max_images_per_product)
    return WOO_PREVIEW_BASE + woocommerce_image_column_names("split_columns", count)


def _woo_export_rows(
    products: list[Product],
    rules: ExportRules | None,
    fields: ExportFields,
    limit: int,
    image_mode: str = "comma",
    max_images_per_product: int | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    image_column_count = resolve_image_column_count(products, image_mode, max_images_per_product)
    preview_cols = _woo_preview_columns(products, image_mode, max_images_per_product)

    for product in fields.apply_to_products(products)[:limit]:
        row = product_to_woocommerce_row(
            apply_export_rules(product, rules),
            rules=rules,
            image_mode=image_mode,
            max_images=image_column_count,
        )
        rows.append({col: _truncate(row.get(col, ""), 120) for col in preview_cols})
    return rows


def _shopify_export_rows(
    products: list[Product],
    rules: ExportRules | None,
    fields: ExportFields,
    limit: int,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for product in fields.apply_to_products(products)[:limit]:
        shopify_rows = product_to_shopify_rows(apply_export_rules(product, rules), rules=rules)
        if not shopify_rows:
            continue
        main_row = shopify_rows[0]
        rows.append({col: _truncate(main_row.get(col, ""), 120) for col in SHOPIFY_PREVIEW_COLUMNS})
    return rows


def _raw_export_rows(
    products: list[Product],
    fields: ExportFields,
    limit: int,
) -> list[dict[str, str]]:
    columns = fields.raw_csv_columns()
    rows: list[dict[str, str]] = []
    for product in fields.apply_to_products(products)[:limit]:
        data = product.to_dict()
        row: dict[str, str] = {}
        for key in columns:
            value = data.get(key)
            if isinstance(value, list):
                if key == "image_urls":
                    row[key] = _truncate(" | ".join(str(v) for v in value), 120)
                else:
                    row[key] = _truncate(", ".join(str(v) for v in value), 120)
            else:
                row[key] = _truncate(value, 120)
        rows.append(row)
    return rows


def _web_feed_export_rows(
    products: list[Product],
    rules: ExportRules | None,
    fields: ExportFields,
    limit: int,
    max_images_per_product: int | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    max_images = max_images_per_product if max_images_per_product and max_images_per_product > 0 else None
    for product in fields.apply_to_products(products)[:limit]:
        row = product_to_web_feed_row(
            apply_export_rules(product, rules),
            rules=rules,
            max_images=max_images,
        )
        rows.append({col: _truncate(row.get(col, ""), 120) for col in WEB_FEED_COLUMNS})
    return rows


def _gmc_export_rows(
    products: list[Product],
    rules: ExportRules | None,
    fields: ExportFields,
    limit: int,
    *,
    gmc_currency: str = "USD",
    gmc_condition: str = "new",
    gmc_google_category: str = "",
    gmc_defaults: dict[str, str] | None = None,
    gmc_variant_mode: str = "all_variations",
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    gmc_defaults = gmc_defaults or {}
    count = 0
    for product in fields.apply_to_products(products):
        if count >= limit:
            break
        for variant in expand_gmc_variants(product, gmc_variant_mode):
            if count >= limit:
                break
            row = product_to_gmc_row(
                apply_export_rules(product, rules),
                rules=rules,
                variant=variant,
                currency=gmc_currency,
                condition=gmc_condition,
                google_category_default=gmc_google_category,
                defaults=gmc_defaults,
            )
            if not fields.includes("images"):
                row["image link"] = ""
            if not fields.includes("description"):
                row["description"] = ""
            rows.append({col: _truncate(row.get(col, ""), 120) for col in GMC_PREVIEW_COLUMNS})
            count += 1
    return rows


def build_export_file_preview(
    products: list[Product],
    export_format: str,
    rules: ExportRules | None,
    fields: ExportFields,
    limit: int = 3,
    image_mode: str = "comma",
    max_images_per_product: int | None = None,
    gmc_currency: str = "USD",
    gmc_condition: str = "new",
    gmc_google_category: str = "",
    gmc_defaults: dict[str, str] | None = None,
    gmc_variant_mode: str = "all_variations",
) -> tuple[list[dict], list[str], str]:
    label = FORMAT_LABELS.get(export_format, export_format)

    if export_format == "woocommerce-csv":
        cols = _woo_preview_columns(products, image_mode, max_images_per_product)
        rows = _woo_export_rows(
            products,
            rules,
            fields,
            limit,
            image_mode=image_mode,
            max_images_per_product=max_images_per_product,
        )
        return rows, cols, label
    if export_format == "shopify-csv":
        return _shopify_export_rows(products, rules, fields, limit), SHOPIFY_PREVIEW_COLUMNS, label
    if export_format == "web-feed-csv":
        return (
            _web_feed_export_rows(products, rules, fields, limit, max_images_per_product),
            WEB_FEED_COLUMNS,
            label,
        )
    if export_format == "gmc-feed-csv":
        return (
            _gmc_export_rows(
                products,
                rules,
                fields,
                limit,
                gmc_currency=gmc_currency,
                gmc_condition=gmc_condition,
                gmc_google_category=gmc_google_category,
                gmc_defaults=gmc_defaults,
                gmc_variant_mode=gmc_variant_mode,
            ),
            GMC_PREVIEW_COLUMNS,
            label,
        )
    if export_format == "json":
        columns = fields.raw_csv_columns()
        return _raw_export_rows(products, fields, limit), columns, label

    columns = fields.raw_csv_columns() or CSV_COLUMNS
    return _raw_export_rows(products, fields, limit), columns, label


def all_export_columns(
    export_format: str,
    products: list[Product] | None = None,
    image_mode: str = "comma",
    max_images_per_product: int | None = None,
) -> list[str]:
    if export_format == "woocommerce-csv":
        if products and image_mode == "split_columns":
            from exporters.woocommerce_import import woocommerce_columns_for_mode

            count = resolve_image_column_count(products, image_mode, max_images_per_product)
            return woocommerce_columns_for_mode(image_mode, count)
        return WOOCOMMERCE_COLUMNS
    if export_format == "shopify-csv":
        return SHOPIFY_COLUMNS
    if export_format == "web-feed-csv":
        return WEB_FEED_COLUMNS
    if export_format == "gmc-feed-csv":
        return GMC_FEED_COLUMNS
    return CSV_COLUMNS


def image_batch_summary(
    products: list[Product],
    max_images_per_product: int | None = None,
) -> str:
    if not products:
        return "Chưa có sản phẩm"
    max_count = max_exportable_images_in_products(products, max_images_per_product)
    return f"SP nhiều ảnh nhất: **{max_count}** ảnh → file tự thêm cột đến **Image {max_count}**"
