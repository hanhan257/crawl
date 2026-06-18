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
    "brand",
    "tag",
    "vendor",
]


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
        "brand": brand,
        "tag": _join_tags(product.tags),
        "vendor": vendor,
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

    if progress:
        progress.step("write", 0.0, "Ghi file CSV feed web thuần...")

    with final_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=WEB_FEED_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for index, product in enumerate(products):
            row = product_to_web_feed_row(
                product,
                rules=rules,
                max_images=max_images,
            )
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
