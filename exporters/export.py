from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from exporters.shopify_import import export_shopify_import
from exporters.web_feed_import import export_web_feed_import
from exporters.gmc_feed_import import export_gmc_feed_import
from exporters.woocommerce_import import export_woocommerce_import
from models.export_fields import ExportFields, ProgressCallback, report_progress
from models.export_rules import ExportRules
from models.product import Product
from utils.export_progress import ExportProgress

CSV_COLUMNS = [
    "source",
    "id",
    "title",
    "description",
    "price",
    "currency",
    "url",
    "image_urls",
    "vendor",
    "sku",
    "tags",
    "categories",
    "product_type",
    "status",
    "variants",
]

EXPORT_FORMATS = {
    "json": "json",
    "csv": "csv",
    "shopify-csv": "csv",
    "woocommerce-csv": "csv",
    "web-feed-csv": "csv",
    "gmc-feed-csv": "csv",
}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", text.strip().lower())
    return slug.strip("_") or "products"


def default_output_path(platform: str, name: str, fmt: str = "csv") -> str:
    ext = EXPORT_FORMATS.get(fmt, "csv")
    if fmt == "shopify-csv":
        return f"output/shopify_import_{_slugify(name)}.{ext}"
    if fmt == "woocommerce-csv":
        return f"output/woocommerce_import_{_slugify(name)}.csv"
    if fmt == "web-feed-csv":
        return f"output/web_feed_{_slugify(name)}.csv"
    if fmt == "gmc-feed-csv":
        return f"output/gmc_feed_{_slugify(name)}.csv"
    return f"output/{platform}_{_slugify(name)}.{ext}"


def _row_for_csv(product: Product, columns: list[str] | None = None) -> dict[str, str | float | None]:
    data = product.to_dict()
    keys = columns or CSV_COLUMNS
    row: dict[str, str | float | None] = {}
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            if key == "image_urls":
                row[key] = " | ".join(str(v) for v in value)
            else:
                row[key] = ", ".join(str(v) for v in value)
        elif isinstance(value, dict):
            row[key] = json.dumps(value, ensure_ascii=False)
        else:
            row[key] = value
    return row


def export_products(
    products: list[Product],
    output_path: str,
    fmt: str = "csv",
    rules: ExportRules | None = None,
    fields: ExportFields | None = None,
    progress: ProgressCallback | None = None,
    proxy_url: str | None = None,
    image_mode: str = "comma",
    max_images_per_product: int | None = None,
    max_workers: int = 8,
    gmc_currency: str = "USD",
    gmc_condition: str = "new",
    gmc_google_category: str = "",
    gmc_defaults: dict[str, str] | None = None,
    gmc_variant_mode: str = "all_variations",
) -> Path:
    fields = fields or ExportFields.from_preset("all")
    prog = ExportProgress(progress, len(products))

    prog.step("prepare", 0.0)
    filtered = fields.apply_to_products(products)
    prog.step("prepare", 1.0)

    if fmt == "shopify-csv":
        result = export_shopify_import(
            filtered,
            output_path,
            rules=rules,
            fields=fields,
            progress=prog,
            max_workers=max_workers,
        )
        report_progress(progress, 1.0, "Hoàn tất xuất file Shopify CSV")
        return result
    if fmt == "woocommerce-csv":
        result = export_woocommerce_import(
            filtered,
            output_path,
            rules=rules,
            fields=fields,
            progress=prog,
            download_images=fields.includes("images"),
            proxy_url=proxy_url,
            image_mode=image_mode,
            max_images_per_product=max_images_per_product,
            max_workers=max_workers,
        )
        report_progress(progress, 1.0, "Hoàn tất xuất file WooCommerce")
        return result
    if fmt == "web-feed-csv":
        result = export_web_feed_import(
            filtered,
            output_path,
            rules=rules,
            fields=fields,
            progress=prog,
            max_images_per_product=max_images_per_product,
        )
        report_progress(progress, 1.0, "Hoàn tất xuất file feed web thuần")
        return result
    if fmt == "gmc-feed-csv":
        result = export_gmc_feed_import(
            filtered,
            output_path,
            rules=rules,
            fields=fields,
            progress=prog,
            currency=gmc_currency or "USD",
            condition=gmc_condition or "new",
            google_category_default=gmc_google_category or "",
            gmc_defaults=gmc_defaults or {},
            variant_mode=gmc_variant_mode or "all_variations",
        )
        report_progress(progress, 1.0, "Hoàn tất xuất file feed GMC")
        return result

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = fields.raw_csv_columns()

    if fmt == "json":
        prog.step("write", 0.0, "Ghi file JSON...")
        payload = []
        for index, product in enumerate(filtered):
            data = product.to_dict()
            payload.append({key: data.get(key) for key in columns})
            prog.product(index, len(filtered))
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        prog.step("write", 1.0)
    elif fmt == "csv":
        prog.step("write", 0.0, "Ghi file CSV...")
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for index, product in enumerate(filtered):
                writer.writerow(_row_for_csv(product, columns))
                prog.product(index, len(filtered))
        prog.step("write", 1.0)
    else:
        raise ValueError(f"Unsupported format: {fmt}")

    report_progress(progress, 1.0, "Hoàn tất xuất file")
    return path


def get_woocommerce_extras_dir(output_path: str | Path) -> Path | None:
    """Thư mục phụ (ảnh backup, HUONG_DAN, PHP fix) cùng tên với file CSV."""
    extras = Path(output_path).with_suffix("")
    return extras if extras.is_dir() else None
