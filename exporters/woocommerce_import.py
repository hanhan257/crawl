from __future__ import annotations

import csv
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any

from exporters.image_downloader import download_product_images
from exporters.image_urls import (
    IMAGE_EXPORT_MODES,
    format_woocommerce_images_fields,
    max_exportable_images_in_products,
    resolve_image_column_count,
    woocommerce_image_column_names,
)
from models.export_fields import ExportFields
from models.export_rules import ExportRules, apply_export_rules
from models.product import Product
from utils.http_session import create_http_session
from utils.price_format import format_export_price

if TYPE_CHECKING:
    from utils.export_progress import ExportProgress

WOOCOMMERCE_COLUMNS = [
    "ID",
    "Type",
    "SKU",
    "Name",
    "Published",
    "Is featured?",
    "Visibility in catalog",
    "Short description",
    "Description",
    "Date sale price starts",
    "Date sale price ends",
    "Tax status",
    "Tax class",
    "In stock?",
    "Stock",
    "Backorders allowed?",
    "Sold individually?",
    "Weight (kg)",
    "Length (cm)",
    "Width (cm)",
    "Height (cm)",
    "Allow customer reviews?",
    "Purchase note",
    "Sale price",
    "Regular price",
    "Categories",
    "Tags",
    "Shipping class",
    "Images",
    "Download limit",
    "Download expiry days",
    "Parent",
    "Grouped products",
    "Upsells",
    "Cross-sells",
    "External URL",
    "Button text",
    "Position",
    "Attribute 1 name",
    "Attribute 1 value(s)",
    "Attribute 1 visible",
    "Attribute 1 global",
    "Attribute 2 name",
    "Attribute 2 value(s)",
    "Attribute 2 visible",
    "Attribute 2 global",
]

IMPORT_GUIDE = """HUONG DAN IMPORT LEN WOOCOMMERCE (veexovar.com / shop khac)
=====================================================

TAI SAO ANH BI LOI?
1. URL anh Redbubble co dau phay (,) -> WooCommerce cat sai link khi import
2. WordPress bi Redbubble chan (403) khi tai anh ve server
3. Import that bai -> Woo dung placeholder -> URL bi ghép sai:
   veexovar.com/http:/veexovar.com/wp-content/.../woocommerce-placeholder.webp

CACH FIX (LAM 1 LAN):

BUOC 1 - Cap nhat code WordPress (QUAN TRONG)
  Copy file wordpress-image-fix.php MOI NHAT vao functions.php theme child
  (file trong goi ZIP export — phien ban moi fix loi double URL + http:/)

BUOC 2 - Xoa san pham import loi (co anh placeholder)
  WooCommerce -> Products -> chon SP loi -> Delete

BUOC 3 - Import CSV MOI
  WooCommerce -> Products -> Import -> products.csv
  Map cot "Images" -> Images

BUOC 4 - Neu anh van trong (Redbubble chan server)
  a) Trong app: bat Proxy -> xuat lai ZIP (thu muc images/ co anh tai san)
  b) Upload anh trong images/ vao Media Library
  c) Sua CSV cot Images = URL tu Media (Copy URL) — KHONG de URL veexovar.com/...
  d) Import lai

COT Images TRONG CSV:
- Chuẩn WooCommerce: MỘT cột "Images", nhiều URL cách nhau dấu phẩy (,)
  Ví dụ: https://a.jpg,https://b.jpg,https://c.jpg
- KHÔNG dùng cột Image 2, Image 3… — Woo chỉ đọc cột Images, bỏ qua các cột còn lại
- Giới hạn ảnh/SP = 0 trong app → tự động lấy hết ảnh crawl được
"""


def woocommerce_columns_for_mode(
    image_mode: str = "comma",
    max_images: int = 5,
) -> list[str]:
    columns = list(WOOCOMMERCE_COLUMNS)
    if image_mode != "split_columns":
        return columns
    insert_at = columns.index("Images") + 1
    for offset, name in enumerate(woocommerce_image_column_names("split_columns", max_images)[1:]):
        columns.insert(insert_at + offset, name)
    return columns


def _slug_sku(title: str, product_id: str, sku: str | None) -> str:
    if sku:
        return str(sku)
    base = re.sub(r"[^A-Za-z0-9\-]", "-", (title or "product")[:40].upper())
    suffix = re.sub(r"[^A-Za-z0-9]", "", product_id)[-8:]
    return f"{base}-{suffix}" if suffix else base


def _html_or_text(text: str | None) -> str:
    if not text:
        return ""
    if "<" in text and ">" in text:
        return text
    return f"<p>{text}</p>"


def _short_description(text: str | None, limit: int = 200) -> str:
    if not text:
        return ""
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = " ".join(plain.split())
    return plain[:limit]


def _woo_categories(categories: list[str]) -> str:
    return ", ".join(categories)


def _woo_tags(tags: list[str]) -> str:
    cleaned = [str(tag).strip() for tag in tags if str(tag).strip()]
    return ", ".join(cleaned[:20])


def _attribute_columns(variants: list[dict[str, Any]]) -> dict[str, str]:
    attrs = [v for v in variants if v.get("name") and not v.get("price")]
    result: dict[str, str] = {}
    for index, attr in enumerate(attrs[:2], start=1):
        result[f"Attribute {index} name"] = str(attr.get("name") or "")
        result[f"Attribute {index} value(s)"] = str(attr.get("value") or "")
        result[f"Attribute {index} visible"] = "1"
        result[f"Attribute {index} global"] = "0"
    return result


def _in_stock(status: str | None) -> str:
    if not status:
        return "1"
    normalized = status.lower()
    if normalized in {"out-of-stock", "outofstock", "out_of_stock", "draft", "private"}:
        return "0"
    return "1"


def product_to_woocommerce_row(
    product: Product,
    local_images: list[str] | None = None,
    rules: ExportRules | None = None,
    image_mode: str = "comma",
    max_images: int = 5,
) -> dict[str, str]:
    product = apply_export_rules(product, rules)
    row = {column: "" for column in woocommerce_columns_for_mode(image_mode, max_images)}
    image_fields = format_woocommerce_images_fields(
        product,
        mode=image_mode,
        max_images=max_images if image_mode == "split_columns" else None,
    )

    row.update(
        {
            "Type": "simple",
            "SKU": _slug_sku(product.title, product.id, product.sku),
            "Name": product.title,
            "Published": "1",
            "Is featured?": "0",
            "Visibility in catalog": "visible",
            "Short description": _short_description(product.description),
            "Description": _html_or_text(product.description),
            "Tax status": "taxable",
            "In stock?": _in_stock(product.status),
            "Stock": "100" if _in_stock(product.status) == "1" else "0",
            "Backorders allowed?": "0",
            "Sold individually?": "0",
            "Allow customer reviews?": "1",
            "Regular price": format_export_price(product.price),
            "Categories": _woo_categories(product.categories),
            "Tags": _woo_tags(product.tags),
        }
    )
    row.update(image_fields)
    row.update(_attribute_columns(product.variants))
    return row


def _prepare_woocommerce_row(
    index: int,
    product: Product,
    *,
    download_images: bool,
    fields: ExportFields,
    images_dir: Path,
    proxy_url: str | None,
    rules: ExportRules | None,
    image_mode: str,
    image_column_count: int,
) -> tuple[int, dict[str, str], str]:
    session = create_http_session(proxy_url, for_redbubble=True)
    local_images: list[str] = []
    stat = "none"

    if download_images and fields.includes("images") and product.image_urls:
        local_images = download_product_images(product, images_dir, session=session)
        stat = "downloaded" if local_images else "url_only"
    elif fields.includes("images") and product.image_urls:
        stat = "url_only"

    row = product_to_woocommerce_row(
        product,
        local_images=local_images,
        rules=rules,
        image_mode=image_mode,
        max_images=image_column_count,
    )
    return index, row, stat


def export_woocommerce_import(
    products: list[Product],
    output_path: str,
    download_images: bool = True,
    rules: ExportRules | None = None,
    fields: ExportFields | None = None,
    progress: ExportProgress | None = None,
    proxy_url: str | None = None,
    image_mode: str = "comma",
    max_images_per_product: int | None = None,
    max_workers: int = 8,
) -> Path:
    fields = fields or ExportFields.from_preset("shop")
    image_column_count = resolve_image_column_count(
        products,
        image_mode,
        max_images_per_product,
    )
    columns = woocommerce_columns_for_mode(image_mode, image_column_count)
    final_csv = Path(output_path)
    final_csv.parent.mkdir(parents=True, exist_ok=True)
    extras_dir = final_csv.with_suffix("")
    images_dir = extras_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    php_fix = Path(__file__).resolve().parent / "wordpress_image_fix.php"

    total = len(products)
    rows: list[dict[str, str] | None] = [None] * total
    stats = {"downloaded": 0, "url_only": 0}
    workers = max(1, min(max_workers, total, 32))
    use_parallel = workers > 1 and total > 1

    if use_parallel:
        completed = 0
        lock = threading.Lock()

        def _submit(executor: ThreadPoolExecutor, index: int, product: Product):
            return executor.submit(
                _prepare_woocommerce_row,
                index,
                product,
                download_images=download_images,
                fields=fields,
                images_dir=images_dir,
                proxy_url=proxy_url,
                rules=rules,
                image_mode=image_mode,
                image_column_count=image_column_count,
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [_submit(executor, index, product) for index, product in enumerate(products)]
            for future in as_completed(futures):
                index, row, stat = future.result()
                rows[index] = row
                if stat == "downloaded":
                    stats["downloaded"] += 1
                elif stat == "url_only":
                    stats["url_only"] += 1
                with lock:
                    completed += 1
                    if progress:
                        progress.product(completed - 1, total)
    else:
        for index, product in enumerate(products):
            _, row, stat = _prepare_woocommerce_row(
                index,
                product,
                download_images=download_images,
                fields=fields,
                images_dir=images_dir,
                proxy_url=proxy_url,
                rules=rules,
                image_mode=image_mode,
                image_column_count=image_column_count,
            )
            rows[index] = row
            if stat == "downloaded":
                stats["downloaded"] += 1
            elif stat == "url_only":
                stats["url_only"] += 1
            if progress:
                progress.product(index, total)

    assert all(row is not None for row in rows)
    final_rows: list[dict[str, str]] = rows  # type: ignore[assignment]

    if progress:
        progress.step("write", 0.0, "Ghi file CSV import WooCommerce...")

    with final_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(final_rows)

    if progress:
        progress.step("write", 1.0)

    guide_path = extras_dir / "HUONG_DAN.txt"
    field_note = ", ".join(fields.labels()) if fields else "Tat ca"
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text(
        IMPORT_GUIDE
        + (
            f"\n\nThong ke:\n"
            f"- Tong san pham: {len(products)}\n"
            f"- Truong xuat: {field_note}\n"
            f"- Tai anh local thanh cong: {stats['downloaded']}\n"
            f"- Che do anh: {IMAGE_EXPORT_MODES.get(image_mode, image_mode)}\n"
            + (
                f"- Cot anh (tach cot): {image_column_count} (Image 2…Image {image_column_count})\n"
                if image_mode == "split_columns"
                else "- Cot anh: Images (mot cot, URL cach nhau dau phay)\n"
            )
            + f"- SP nhieu anh nhat: {max_exportable_images_in_products(products, max_images_per_product)}\n"
            f"- File import: {final_csv.name}\n"
            f"- Anh backup (neu co): {extras_dir.name}/images/\n"
        ),
        encoding="utf-8",
    )

    if php_fix.exists():
        shutil.copy2(php_fix, extras_dir / "wordpress-image-fix.php")

    if progress:
        progress.step("images", 1.0, "Hoàn tất — tải file CSV để import WooCommerce")

    return final_csv

