from __future__ import annotations

import csv
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any

from models.export_fields import ExportFields
from models.export_rules import ExportRules, apply_export_rules
from models.product import Product
from utils.price_format import format_export_price

if TYPE_CHECKING:
    from utils.export_progress import ExportProgress

SHOPIFY_COLUMNS = [
    "Handle",
    "Title",
    "Body (HTML)",
    "Vendor",
    "Product Category",
    "Type",
    "Tags",
    "Published",
    "Option1 Name",
    "Option1 Value",
    "Option2 Name",
    "Option2 Value",
    "Option3 Name",
    "Option3 Value",
    "Variant SKU",
    "Variant Grams",
    "Variant Inventory Tracker",
    "Variant Inventory Qty",
    "Variant Inventory Policy",
    "Variant Fulfillment Service",
    "Variant Price",
    "Variant Compare At Price",
    "Variant Requires Shipping",
    "Variant Taxable",
    "Variant Barcode",
    "Image Src",
    "Image Position",
    "Image Alt Text",
    "Gift Card",
    "SEO Title",
    "SEO Description",
    "Variant Image",
    "Variant Weight Unit",
    "Status",
]


def _slugify_handle(title: str, product_id: str) -> str:
    slug = re.sub(r"[^\w\s\-]", "", (title or "").lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    if not slug:
        slug = f"product-{re.sub(r'[^a-z0-9]', '', product_id.lower())}"
    return slug[:255]


def _html_body(text: str | None) -> str:
    if not text:
        return ""
    if "<" in text and ">" in text:
        return text
    return f"<p>{text}</p>"


def _join_tags(tags: list[str]) -> str:
    return ", ".join(tags)


def _is_priced_variant(variant: dict[str, Any]) -> bool:
    return any(key in variant for key in ("price", "sku", "id", "title"))


def _variant_options(variants: list[dict[str, Any]]) -> tuple[str, str, str, str, str, str]:
    if not variants or _is_priced_variant(variants[0]):
        return ("", "", "", "", "", "")

    names = [str(v.get("name") or "") for v in variants[:3]]
    values = [str(v.get("value") or "") for v in variants[:3]]
    while len(names) < 3:
        names.append("")
    while len(values) < 3:
        values.append("")
    return (names[0], values[0], names[1], values[1], names[2], values[2])


def product_to_shopify_rows(product: Product, rules: ExportRules | None = None) -> list[dict[str, str]]:
    product = apply_export_rules(product, rules)
    handle = _slugify_handle(product.title, product.id)
    tags = _join_tags(product.tags)
    category = ", ".join(product.categories) if product.categories else ""
    priced_variants = [v for v in product.variants if _is_priced_variant(v)]
    option1_name, option1_value, option2_name, option2_value, option3_name, option3_value = (
        _variant_options(product.variants)
    )

    rows: list[dict[str, str]] = []

    if priced_variants:
        for index, variant in enumerate(priced_variants):
            row = _blank_shopify_row()
            if index == 0:
                row.update(
                    {
                        "Handle": handle,
                        "Title": product.title,
                        "Body (HTML)": _html_body(product.description),
                        "Vendor": product.vendor or "",
                        "Product Category": category,
                        "Type": product.product_type or "",
                        "Tags": tags,
                        "Published": "TRUE",
                        "Gift Card": "FALSE",
                        "SEO Title": product.title,
                        "Status": "active",
                    }
                )
                if product.image_urls:
                    row["Image Src"] = product.image_urls[0]
                    row["Image Position"] = "1"
                    row["Image Alt Text"] = product.title
            else:
                row["Handle"] = handle

            if variant.get("title"):
                row["Option1 Name"] = "Title"
                row["Option1 Value"] = str(variant.get("title"))
            else:
                row["Option1 Name"] = option1_name
                row["Option1 Value"] = option1_value or "Default Title"
            row["Variant SKU"] = str(variant.get("sku") or product.sku or "")
            row["Variant Inventory Tracker"] = ""
            row["Variant Inventory Qty"] = "100" if variant.get("available", True) else "0"
            row["Variant Inventory Policy"] = "deny"
            row["Variant Fulfillment Service"] = "manual"
            row["Variant Price"] = format_export_price(variant.get("price") or product.price)
            row["Variant Requires Shipping"] = "TRUE"
            row["Variant Taxable"] = "TRUE"
            row["Variant Weight Unit"] = "g"
            rows.append(row)
    else:
        row = _blank_shopify_row()
        row.update(
            {
                "Handle": handle,
                "Title": product.title,
                "Body (HTML)": _html_body(product.description),
                "Vendor": product.vendor or "",
                "Product Category": category,
                "Type": product.product_type or "",
                "Tags": tags,
                "Published": "TRUE",
                "Option1 Name": option1_name,
                "Option1 Value": option1_value or "Default Title",
                "Option2 Name": option2_name,
                "Option2 Value": option2_value,
                "Option3 Name": option3_name,
                "Option3 Value": option3_value,
                "Variant SKU": product.sku or "",
                "Variant Inventory Tracker": "",
                "Variant Inventory Qty": "100",
                "Variant Inventory Policy": "deny",
                "Variant Fulfillment Service": "manual",
                "Variant Price": format_export_price(product.price),
                "Variant Requires Shipping": "TRUE",
                "Variant Taxable": "TRUE",
                "Gift Card": "FALSE",
                "SEO Title": product.title,
                "Variant Weight Unit": "g",
                "Status": "active",
            }
        )
        if product.image_urls:
            row["Image Src"] = product.image_urls[0]
            row["Image Position"] = "1"
            row["Image Alt Text"] = product.title
        rows.append(row)

    for position, image_url in enumerate(product.image_urls[1:], start=2):
        image_row = _blank_shopify_row()
        image_row["Handle"] = handle
        image_row["Image Src"] = image_url
        image_row["Image Position"] = str(position)
        image_row["Image Alt Text"] = product.title
        rows.append(image_row)

    return rows


def _blank_shopify_row() -> dict[str, str]:
    return {column: "" for column in SHOPIFY_COLUMNS}


def export_shopify_import(
    products: list[Product],
    output_path: str,
    rules: ExportRules | None = None,
    fields: ExportFields | None = None,
    progress: ExportProgress | None = None,
    max_workers: int = 8,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    total = len(products)
    workers = max(1, min(max_workers, total, 32))
    product_rows: list[list[dict[str, str]] | None] = [None] * total

    if workers > 1 and total > 1:
        completed = 0
        lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(product_to_shopify_rows, product, rules): index
                for index, product in enumerate(products)
            }
            for future in as_completed(futures):
                index = futures[future]
                product_rows[index] = future.result()
                with lock:
                    completed += 1
                    if progress:
                        progress.product(completed - 1, total)
    else:
        for index, product in enumerate(products):
            product_rows[index] = product_to_shopify_rows(product, rules=rules)
            if progress:
                progress.product(index, total)

    rows: list[dict[str, str]] = []
    for block in product_rows:
        if block:
            rows.extend(block)

    if progress:
        progress.step("write", 0.0, "Ghi file Shopify CSV...")

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHOPIFY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    if progress:
        progress.step("write", 1.0)

    return path
