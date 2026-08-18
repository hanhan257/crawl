from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

from exporters.image_urls import collect_product_image_urls
from models.export_fields import ExportFields
from models.export_rules import ExportRules, apply_export_rules
from models.product import Product
from utils.price_format import format_export_price

if TYPE_CHECKING:
    from utils.export_progress import ExportProgress

WEB_FEED_COLUMNS = [
    "title",
    "description",
    "images",
    "category",
    "price",
    "color",
    "brand",
    "tag",
    "vendor",
]

COLOR_SWATCH_TYPE = "color_swatch"


def _color_swatches(product: Product) -> list[dict]:
    return [
        v
        for v in (product.variants or [])
        if isinstance(v, dict) and v.get("_type") == COLOR_SWATCH_TYPE
    ]


def web_feed_rows_for_product(
    product: Product,
    rules: ExportRules | None = None,
    max_images: int | None = None,
    *,
    include_url: bool = False,
) -> list[dict[str, str]]:
    """1 SP Trendsi nhiều màu → 1 dòng / màu (link + ảnh theo skuId)."""
    swatches = _color_swatches(product)
    if not swatches:
        row = product_to_web_feed_row(product, rules=rules, max_images=max_images)
        if product.gmc_color:
            row["color"] = product.gmc_color
        if not include_url:
            row.pop(WEB_FEED_URL_COLUMN, None)
        return [row]

    rows: list[dict[str, str]] = []
    base = product_to_web_feed_row(product, rules=rules, max_images=max_images)
    for swatch in swatches:
        row = dict(base)
        color = str(swatch.get("color") or "").strip()
        row["color"] = color
        if color and product.title:
            row["title"] = f"{product.title} - {color}"
        if swatch.get("url"):
            row[WEB_FEED_URL_COLUMN] = str(swatch["url"])
        if swatch.get("image_url"):
            row["images"] = str(swatch["image_url"])
        if swatch.get("price") not in (None, ""):
            row["price"] = format_export_price(swatch.get("price"))
        if not include_url:
            row.pop(WEB_FEED_URL_COLUMN, None)
        rows.append(row)
    return rows

WEB_FEED_URL_COLUMN = "product_url"


def web_feed_columns(*, include_url: bool = False) -> list[str]:
    columns = list(WEB_FEED_COLUMNS)
    if include_url and WEB_FEED_URL_COLUMN not in columns:
        columns.append(WEB_FEED_URL_COLUMN)
    return columns


def _join_category(categories: list[str]) -> str:
    cleaned = [str(c).strip() for c in categories if str(c).strip()]
    return cleaned[0] if len(cleaned) == 1 else ", ".join(cleaned)


def _join_tags(tags: list[str]) -> str:
    return ", ".join(str(t).strip() for t in tags if str(t).strip())


def product_to_web_feed_row(
    product: Product,
    rules: ExportRules | None = None,
    max_images: int | None = None,
) -> dict[str, str]:
    product = apply_export_rules(product, rules)
    images = collect_product_image_urls(product, max_images=max_images)
    vendor = (product.vendor or "").strip()
    brand = vendor or (product.product_type or "").strip()

    return {
        "title": product.title or "",
        "description": product.description or "",
        "images": ",".join(images),
        "category": _join_category(product.categories),
        "price": format_export_price(product.price),
        "color": (product.gmc_color or "").strip(),
        "brand": brand,
        "tag": _join_tags(product.tags),
        "vendor": vendor,
        WEB_FEED_URL_COLUMN: (product.url or "").strip(),
    }


def export_web_feed_import(
    products: list[Product],
    output_path: str,
    rules: ExportRules | None = None,
    fields: ExportFields | None = None,
    progress: ExportProgress | None = None,
    max_images_per_product: int | None = None,
) -> Path:
    fields = fields or ExportFields.from_preset("shop")
    final_csv = Path(output_path)
    final_csv.parent.mkdir(parents=True, exist_ok=True)

    max_images = max_images_per_product if max_images_per_product and max_images_per_product > 0 else None
    total = len(products)
    include_url = fields.includes("url")
    columns = web_feed_columns(include_url=include_url)

    if progress:
        progress.step("write", 0.0, "Ghi file CSV feed web thuần...")

    with final_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for index, product in enumerate(products):
            for row in web_feed_rows_for_product(
                product,
                rules=rules,
                max_images=max_images,
                include_url=include_url,
            ):
                if not fields.includes("images"):
                    row["images"] = ""
                if not fields.includes("description"):
                    row["description"] = ""
                if not fields.includes("categories"):
                    row["category"] = ""
                if not fields.includes("tags"):
                    row["tag"] = ""
                if not fields.includes("vendor"):
                    row["vendor"] = ""
                    row["brand"] = ""
                writer.writerow(row)
            if progress:
                progress.product(index, total)

    if progress:
        progress.step("write", 1.0, "Hoàn tất — tải file CSV feed web thuần")

    return final_csv
