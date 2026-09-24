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

COLOR_SWATCH_TYPE = "color_swatch"
SHOPIFY_URL_COLUMN = "Product URL"
SHOPIFY_TEMPLATE_FILE = (
    Path(__file__).resolve().parents[1] / "output" / "fileShopify.txt"
)

METAFIELD_COLOR = "Color (product.metafields.shopify.color-pattern)"
METAFIELD_MATERIAL = "Material (product.metafields.shopify.material)"

# Fallback nếu chưa có fileShopify.txt
SHOPIFY_COLUMNS_FALLBACK = [
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
    "Option1 Linked To",
    "Option2 Name",
    "Option2 Value",
    "Option2 Linked To",
    "Option3 Name",
    "Option3 Value",
    "Option3 Linked To",
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
    "Unit Price Total Measure",
    "Unit Price Total Measure Unit",
    "Unit Price Base Measure",
    "Unit Price Base Measure Unit",
    "Variant Barcode",
    "Image Src",
    "Image Position",
    "Image Alt Text",
    "Gift Card",
    "SEO Title",
    "SEO Description",
    METAFIELD_COLOR,
    "Drinkware material (product.metafields.shopify.drinkware-material)",
    "Food storage material (product.metafields.shopify.food-storage-material)",
    METAFIELD_MATERIAL,
    "Tableware material (product.metafields.shopify.tableware-material)",
    "Variant Image",
    "Variant Weight Unit",
    "Variant Tax Code",
    "Cost per item",
    "Status",
]

_cached_columns: list[str] | None = None


def _load_template_columns() -> list[str] | None:
    if not SHOPIFY_TEMPLATE_FILE.is_file():
        return None
    try:
        with SHOPIFY_TEMPLATE_FILE.open(encoding="utf-8-sig", newline="") as fh:
            row = next(csv.reader(fh, delimiter="\t"), None)
        if row and row[0] == "Handle":
            return row
    except OSError:
        pass
    return None


def shopify_columns(*, include_url: bool = False) -> list[str]:
    global _cached_columns
    if _cached_columns is None:
        _cached_columns = _load_template_columns() or list(SHOPIFY_COLUMNS_FALLBACK)
    columns = list(_cached_columns)
    if include_url and SHOPIFY_URL_COLUMN not in columns:
        columns.append(SHOPIFY_URL_COLUMN)
    return columns


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


def _color_swatches(variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        v
        for v in variants
        if isinstance(v, dict) and v.get("_type") == COLOR_SWATCH_TYPE
    ]


def _color_metafield_value(swatches: list[dict[str, Any]], product: Product) -> str:
    colors: list[str] = []
    seen: set[str] = set()
    for swatch in swatches:
        color = str(swatch.get("color") or "").strip().lower()
        if color and color not in seen:
            seen.add(color)
            colors.append(color)
    if colors:
        return "; ".join(colors)
    if product.gmc_color:
        parts = re.split(r"[,;/]", product.gmc_color)
        return "; ".join(p.strip().lower() for p in parts if p.strip())
    return ""


def _is_priced_variant(variant: dict[str, Any]) -> bool:
    if variant.get("_type") == COLOR_SWATCH_TYPE:
        return False
    if variant.get("name") and not variant.get("price"):
        return False
    return any(key in variant for key in ("price", "sku", "id", "title"))


def _has_sku_matrix(variants: list[dict[str, Any]]) -> bool:
    """Trendsi / Shopify: skuList có Size×Color — ưu tiên hơn color_swatch."""
    for variant in variants:
        if not _is_priced_variant(variant):
            continue
        if any(str(variant.get(f"option{i}") or "").strip() for i in (1, 2, 3)):
            return True
    return False


_SIZE_OPTION_NAMES = frozenset({"size", "kích cỡ", "kich co"})
_COLOR_OPTION_NAMES = frozenset({"color", "colour", "màu", "mau"})
_SIZE_VALUE_RE = re.compile(
    r"^(XXS|XS|S|M|L|XL|XXL|XXXL|2XL|3XL|4XL|5XL|"
    r"ONE SIZE|ONE-SIZE|OS|FREE SIZE|"
    r"\d+(?:\.\d+)?(?:cm|mm|in|\"|'')?)$",
    re.I,
)
_SIZE_RANK = {
    "XXS": 10,
    "XS": 20,
    "S": 30,
    "M": 40,
    "L": 50,
    "XL": 60,
    "XXL": 70,
    "2XL": 75,
    "XXXL": 80,
    "3XL": 85,
    "4XL": 90,
    "5XL": 95,
    "ONE SIZE": 100,
    "OS": 100,
    "FREE SIZE": 100,
}


def _looks_like_size(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and bool(_SIZE_VALUE_RE.match(text))


def _size_sort_key(value: str) -> tuple[int, str]:
    text = str(value or "").strip()
    upper = text.upper()
    if upper in _SIZE_RANK:
        return (_SIZE_RANK[upper], text.lower())
    num_match = re.match(r"^(\d+(?:\.\d+)?)", text)
    if num_match:
        return (200 + int(float(num_match.group(1))), text.lower())
    return (999, text.lower())


def _option_axis_indices(
    attr_defs: list[dict[str, Any]],
    variant: dict[str, Any] | None,
) -> tuple[int | None, int | None]:
    names = _shopify_option_names(attr_defs, variant)
    color_idx: int | None = None
    size_idx: int | None = None
    for index, name in enumerate(names[:3]):
        normalized = str(name or "").strip().casefold()
        if normalized in _COLOR_OPTION_NAMES:
            color_idx = index
        elif normalized in _SIZE_OPTION_NAMES:
            size_idx = index
    if size_idx is None and variant:
        for index in range(3):
            if _looks_like_size(str(variant.get(f"option{index + 1}") or "")):
                size_idx = index
                break
    return color_idx, size_idx


def _size_attr_value(attr_defs: list[dict[str, Any]]) -> str:
    """Lấy Size từ attr_defs — jewelry thường chỉ có One Size."""
    for attr in attr_defs:
        if str(attr.get("name") or "").strip().casefold() not in _SIZE_OPTION_NAMES:
            continue
        options = [str(option).strip() for option in (attr.get("options") or []) if str(option).strip()]
        if len(options) == 1:
            return options[0]
        parts = [part.strip() for part in str(attr.get("value") or "").split(",") if part.strip()]
        if len(parts) == 1:
            return parts[0]
    return ""


def _size_option_slot(
    attr_defs: list[dict[str, Any]],
    variant: dict[str, Any] | None,
) -> int:
    names = _shopify_option_names(attr_defs, variant)
    for index, name in enumerate(names[:3]):
        if str(name or "").strip().casefold() in _SIZE_OPTION_NAMES:
            return index + 1
    return 2


def _apply_size_option(
    row: dict[str, str],
    attr_defs: list[dict[str, Any]],
    variant: dict[str, Any] | None,
    *,
    first_variant: bool,
) -> None:
    """Gắn Size (vd. One Size) nếu swatch/SKU thiếu option Size."""
    slot = _size_option_slot(attr_defs, variant)
    if str(row.get(f"Option{slot} Value") or "").strip():
        return

    size_val = ""
    names = _shopify_option_names(attr_defs, variant)
    for index, name in enumerate(names[:3]):
        if str(name or "").strip().casefold() in _SIZE_OPTION_NAMES:
            size_val = str(variant.get(f"option{index + 1}") or "").strip() if variant else ""
            slot = index + 1
            break
    if not size_val:
        size_val = _size_attr_value(attr_defs)
    if not size_val:
        return

    if first_variant:
        row[f"Option{slot} Name"] = "Size"
    row[f"Option{slot} Value"] = size_val


def _color_options_order(attr_defs: list[dict[str, Any]]) -> list[str]:
    for attr in attr_defs:
        if str(attr.get("name") or "").strip().casefold() in _COLOR_OPTION_NAMES:
            options = attr.get("options") or []
            return [str(option).strip() for option in options if str(option).strip()]
    return []


def _sort_priced_variants(
    variants: list[dict[str, Any]],
    attr_defs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(variants) <= 1:
        return variants

    sample = variants[0]
    color_idx, size_idx = _option_axis_indices(attr_defs, sample)
    color_order = _color_options_order(attr_defs)

    def sort_key(variant: dict[str, Any]) -> tuple[Any, ...]:
        parts: list[Any] = []
        if color_idx is not None:
            color = str(variant.get(f"option{color_idx + 1}") or "").strip()
            if color in color_order:
                parts.append(color_order.index(color))
            else:
                parts.append(999)
            parts.append(color.casefold())
        if size_idx is not None:
            size = str(variant.get(f"option{size_idx + 1}") or "").strip()
            parts.extend(_size_sort_key(size))
        else:
            for index in range(3):
                parts.append(str(variant.get(f"option{index + 1}") or "").strip().casefold())
        return tuple(parts)

    return sorted(variants, key=sort_key)


def _attribute_defs(variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        v
        for v in variants
        if v.get("name") and not v.get("price") and v.get("_type") != COLOR_SWATCH_TYPE
    ]


def _variant_options(variants: list[dict[str, Any]]) -> tuple[str, str, str, str, str, str]:
    attrs = _attribute_defs(variants)
    if not attrs:
        return ("", "", "", "", "", "")

    names = [str(v.get("name") or "") for v in attrs[:3]]
    values = [str(v.get("value") or "") for v in attrs[:3]]
    while len(names) < 3:
        names.append("")
    while len(values) < 3:
        values.append("")
    return (names[0], values[0], names[1], values[1], names[2], values[2])


def _shopify_option_names(
    attr_defs: list[dict[str, Any]],
    variant: dict[str, Any] | None,
) -> list[str]:
    if attr_defs:
        return [str(item.get("name") or "") for item in attr_defs[:3]]
    if variant:
        names = variant.get("_shopify_option_names") or []
        if names:
            return [str(name) for name in names[:3]]
    return []


def _shopify_option_fields(
    variant: dict[str, Any],
    attr_defs: list[dict[str, Any]],
    fallback: tuple[str, str, str, str, str, str],
    *,
    first_variant: bool,
) -> dict[str, str]:
    names = _shopify_option_names(attr_defs, variant)
    while len(names) < 3:
        names.append("")

    values = [
        str(variant.get("option1") or "").strip(),
        str(variant.get("option2") or "").strip(),
        str(variant.get("option3") or "").strip(),
    ]
    if any(values):
        return {
            "Option1 Name": names[0] if first_variant else "",
            "Option1 Value": values[0],
            "Option2 Name": names[1] if first_variant else "",
            "Option2 Value": values[1],
            "Option3 Name": names[2] if first_variant else "",
            "Option3 Value": values[2],
        }

    title = str(variant.get("title") or "").strip()
    if title and title.lower() not in {"default title", "default"}:
        return {
            "Option1 Name": "Title" if first_variant else "",
            "Option1 Value": title,
            "Option2 Name": "",
            "Option2 Value": "",
            "Option3 Name": "",
            "Option3 Value": "",
        }

    return {
        "Option1 Name": fallback[0] if first_variant else "",
        "Option1 Value": fallback[1],
        "Option2 Name": fallback[2] if first_variant else "",
        "Option2 Value": fallback[3],
        "Option3 Name": fallback[4] if first_variant else "",
        "Option3 Value": fallback[5],
    }


def _product_base_fields(
    product: Product,
    handle: str,
    *,
    include_url: bool,
    color_metafield: str = "",
) -> dict[str, str]:
    tags = _join_tags(product.tags)
    category = ", ".join(product.categories) if product.categories else ""
    row: dict[str, str] = {
        "Handle": handle,
        "Title": product.title or "",
        "Body (HTML)": _html_body(product.description),
        "Vendor": product.vendor or "",
        "Product Category": category,
        "Type": product.product_type or "",
        "Tags": tags,
        "Published": "TRUE",
        "Gift Card": "FALSE",
        "SEO Title": product.title or "",
        "Status": "active",
        "Variant Weight Unit": "kg",
    }
    if include_url:
        row[SHOPIFY_URL_COLUMN] = (product.url or "").strip()
    if color_metafield and METAFIELD_COLOR in shopify_columns():
        row[METAFIELD_COLOR] = color_metafield
    return row


def _variant_common_fields(
    variant: dict[str, Any],
    product: Product,
) -> dict[str, str]:
    return {
        "Variant SKU": str(variant.get("sku") or product.sku or ""),
        "Variant Inventory Tracker": "",
        "Variant Inventory Qty": "100" if variant.get("available", True) else "0",
        "Variant Inventory Policy": "deny",
        "Variant Fulfillment Service": "manual",
        "Variant Price": format_export_price(variant.get("price") or product.price),
        "Variant Requires Shipping": "TRUE",
        "Variant Taxable": "TRUE",
        "Variant Weight Unit": "kg",
    }


def _shopify_rows_from_color_swatches(
    product: Product,
    handle: str,
    swatches: list[dict[str, Any]],
    *,
    include_url: bool,
    attr_defs: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    color_meta = _color_metafield_value(swatches, product)
    attr_defs = attr_defs or _attribute_defs(product.variants or [])
    rows: list[dict[str, str]] = []
    used_images: set[str] = set()

    for index, swatch in enumerate(swatches):
        row = _blank_shopify_row(include_url=include_url)
        if index == 0:
            row.update(_product_base_fields(product, handle, include_url=include_url, color_metafield=color_meta))
            if product.image_urls:
                row["Image Src"] = product.image_urls[0]
                row["Image Position"] = "1"
                row["Image Alt Text"] = product.title or ""
                used_images.add(product.image_urls[0])
        else:
            row["Handle"] = handle

        color = str(swatch.get("color") or "").strip()
        if index == 0:
            row["Option1 Name"] = "Color"
        row["Option1 Value"] = color
        _apply_size_option(row, attr_defs, swatch, first_variant=index == 0)
        row.update(_variant_common_fields(swatch, product))

        image_url = str(swatch.get("image_url") or "").strip()
        if image_url:
            row["Variant Image"] = image_url
            if index == 0 and not row.get("Image Src"):
                row["Image Src"] = image_url
                row["Image Position"] = "1"
                row["Image Alt Text"] = product.title or ""
            elif index > 0 and image_url not in used_images:
                row["Image Src"] = image_url
                row["Image Position"] = str(index + 1)
                row["Image Alt Text"] = product.title or ""
                used_images.add(image_url)

        rows.append(row)

    start_pos = len(used_images) + 1
    for position, image_url in enumerate(product.image_urls, start=1):
        if image_url in used_images:
            continue
        image_row = _blank_shopify_row(include_url=include_url)
        image_row["Handle"] = handle
        image_row["Image Src"] = image_url
        image_row["Image Position"] = str(start_pos)
        image_row["Image Alt Text"] = product.title or ""
        rows.append(image_row)
        used_images.add(image_url)
        start_pos += 1

    return rows


def product_to_shopify_rows(
    product: Product,
    rules: ExportRules | None = None,
    *,
    include_url: bool = False,
) -> list[dict[str, str]]:
    product = apply_export_rules(product, rules)
    handle = _slugify_handle(product.title, product.id)
    variants = product.variants or []
    swatches = _color_swatches(variants)
    attr_defs = _attribute_defs(variants)
    if swatches and not _has_sku_matrix(variants):
        return _shopify_rows_from_color_swatches(
            product,
            handle,
            swatches,
            include_url=include_url,
            attr_defs=attr_defs,
        )

    tags = _join_tags(product.tags)
    category = ", ".join(product.categories) if product.categories else ""
    priced_variants = _sort_priced_variants(
        [v for v in product.variants if _is_priced_variant(v)],
        attr_defs,
    )
    fallback_options = _variant_options(product.variants)
    product_url = (product.url or "").strip() if include_url else ""

    rows: list[dict[str, str]] = []

    if priced_variants:
        for index, variant in enumerate(priced_variants):
            row = _blank_shopify_row(include_url=include_url)
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
                        "Variant Weight Unit": "kg",
                    }
                )
                if include_url:
                    row[SHOPIFY_URL_COLUMN] = product_url
                if product.gmc_color and METAFIELD_COLOR in shopify_columns():
                    row[METAFIELD_COLOR] = _color_metafield_value([], product)
                if product.image_urls:
                    row["Image Src"] = product.image_urls[0]
                    row["Image Position"] = "1"
                    row["Image Alt Text"] = product.title
            else:
                row["Handle"] = handle

            row.update(
                _shopify_option_fields(
                    variant,
                    attr_defs,
                    fallback_options,
                    first_variant=index == 0,
                )
            )
            _apply_size_option(row, attr_defs, variant, first_variant=index == 0)
            row.update(_variant_common_fields(variant, product))
            if variant.get("image_url"):
                row["Variant Image"] = str(variant["image_url"])
            rows.append(row)
    else:
        row = _blank_shopify_row(include_url=include_url)
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
                "Variant Weight Unit": "kg",
            }
        )
        if include_url:
            row[SHOPIFY_URL_COLUMN] = product_url
        if product.gmc_color and METAFIELD_COLOR in shopify_columns():
            row[METAFIELD_COLOR] = _color_metafield_value([], product)
        row.update(
            {
                "Option1 Name": fallback_options[0],
                "Option1 Value": fallback_options[1] or "Default Title",
                "Option2 Name": fallback_options[2],
                "Option2 Value": fallback_options[3],
                "Option3 Name": fallback_options[4],
                "Option3 Value": fallback_options[5],
                "Variant SKU": product.sku or "",
                "Variant Inventory Tracker": "",
                "Variant Inventory Qty": "100",
                "Variant Inventory Policy": "deny",
                "Variant Fulfillment Service": "manual",
                "Variant Price": format_export_price(product.price),
                "Variant Requires Shipping": "TRUE",
                "Variant Taxable": "TRUE",
            }
        )
        if product.image_urls:
            row["Image Src"] = product.image_urls[0]
            row["Image Position"] = "1"
            row["Image Alt Text"] = product.title
        rows.append(row)

    for position, image_url in enumerate(product.image_urls[1:], start=2):
        image_row = _blank_shopify_row(include_url=include_url)
        image_row["Handle"] = handle
        image_row["Image Src"] = image_url
        image_row["Image Position"] = str(position)
        image_row["Image Alt Text"] = product.title
        rows.append(image_row)

    return rows


def _blank_shopify_row(*, include_url: bool = False) -> dict[str, str]:
    return {column: "" for column in shopify_columns(include_url=include_url)}


def count_shopify_export_rows(products: list[Product]) -> int:
    total = 0
    for product in products:
        total += len(
            [row for row in product_to_shopify_rows(product) if row.get("Variant Price")]
        )
    return total


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
    fields = fields or ExportFields.from_preset("shop")
    include_url = fields.includes("url")
    columns = shopify_columns(include_url=include_url)
    total = len(products)
    workers = max(1, min(max_workers, total, 32))
    product_rows: list[list[dict[str, str]] | None] = [None] * total

    if workers > 1 and total > 1:
        completed = 0
        lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    product_to_shopify_rows, product, rules, include_url=include_url
                ): index
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
            product_rows[index] = product_to_shopify_rows(
                product, rules=rules, include_url=include_url
            )
            if progress:
                progress.product(index, total)

    rows: list[dict[str, str]] = []
    for block in product_rows:
        if block:
            rows.extend(block)

    if progress:
        progress.step("write", 0.0, "Ghi file Shopify CSV...")

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    if progress:
        progress.step("write", 1.0)

    return path
