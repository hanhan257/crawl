from __future__ import annotations

import csv
import html
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from exporters.image_urls import collect_product_image_urls
from models.export_fields import ExportFields
from models.export_rules import ExportRules, GMC_TITLE_MAX_LENGTH, apply_export_rules
from models.product import Product
from utils.gmc_taxonomy import normalize_age_group_for_export as _normalize_age_group
from utils.price_format import format_export_price

if TYPE_CHECKING:
    from utils.export_progress import ExportProgress

GMC_VARIANT_MODES = {
    "all_variations": "All Variations",
    "parent_only": "Variable Products (Parent)",
}

GMC_FEED_COLUMNS = [
    "id",
    "title",
    "description",
    "link",
    "condition",
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

_ATTR_ALIASES = {
    "size": ("size", "kích cỡ", "kich co"),
    "color": ("color", "colour", "màu", "mau"),
    "gender": ("gender", "giới tính", "gioi tinh"),
    "age group": ("age group", "age", "độ tuổi", "do tuoi"),
}

_SIZE_VALUE_RE = re.compile(
    r"^(XXS|XS|S|M|L|XL|XXL|XXXL|2XL|3XL|4XL|5XL|\d+(?:\.\d+)?(?:cm|mm|in|\"|'')?)$",
    re.I,
)


def _plain_gmc_text(value: str | None, *, limit: int | None = None) -> str:
    """Chuyển HTML/entity (&#8211;, &amp;…) sang plain text cho feed GMC."""
    if not value:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    if limit is not None and limit > 0:
        return text[:limit]
    return text


def _plain_description(value: str | None, limit: int = 5000) -> str:
    return _plain_gmc_text(value, limit=limit)


def _format_gmc_price(price: object, currency: str = "USD") -> str:
    base = format_export_price(price)
    if not base:
        return ""
    code = (currency or "USD").strip().upper() or "USD"
    return f"{base} {code}"


def _safe_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _looks_like_size(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(_SIZE_VALUE_RE.match(text))


def _parse_slash_parts(variant: dict[str, Any]) -> tuple[str, str]:
    """Suy ra (color, size) từ title variant hoặc option1/2/3."""
    part = str(variant.get("title") or "").strip()
    if not part or part.lower() in {"default title", "default"}:
        options = [
            str(variant.get(key) or "").strip()
            for key in ("option1", "option2", "option3")
        ]
        part = " / ".join(option for option in options if option)
    if not part:
        return "", ""
    if " / " not in part:
        if _looks_like_size(part):
            return "", part
        return part, ""
    parts = [piece.strip() for piece in part.split(" / ") if piece.strip()]
    if len(parts) >= 2:
        if _looks_like_size(parts[-1]):
            return " / ".join(parts[:-1]), parts[-1]
        if _looks_like_size(parts[0]):
            return parts[1] if len(parts) > 1 else "", parts[0]
        return parts[0], parts[-1]
    return parts[0], ""


def _shopify_variant_available(variant: dict[str, Any]) -> bool | None:
    if "available" in variant and variant.get("available") is not None:
        return bool(variant.get("available"))
    stock = str(variant.get("stock_status") or "").lower()
    if stock in {"outofstock", "out_of_stock"}:
        return False
    if stock in {"instock", "in_stock", "onbackorder"}:
        return True
    mgmt = variant.get("inventory_management")
    if mgmt in (None, ""):
        return True
    qty = variant.get("inventory_quantity")
    if qty is not None:
        try:
            return int(qty) > 0
        except (TypeError, ValueError):
            return True
    return None


def _normalize_availability_status(status: str | None) -> str | None:
    if not status:
        return None
    text = str(status).strip().lower()
    if text.startswith("http"):
        text = text.rsplit("/", 1)[-1]
    normalized = text.replace("-", "").replace("_", "").replace(" ", "")
    if normalized in {"outofstock", "soldout", "discontinued", "unavailable"}:
        return "out_of_stock"
    if normalized in {
        "instock",
        "active",
        "publish",
        "published",
        "onlineonly",
        "limitedavailability",
        "onbackorder",
        "preorder",
        "presale",
    }:
        return "in_stock"
    if normalized in {"draft", "private", "disabled"}:
        return "out_of_stock"
    return None


def _availability(status: str | None, variant: dict[str, Any] | None = None) -> str:
    if variant is not None:
        shopify_avail = _shopify_variant_available(variant)
        if shopify_avail is False:
            return "out_of_stock"
        if shopify_avail is True:
            return "in_stock"
        if variant.get("stock_status"):
            mapped = _normalize_availability_status(str(variant.get("stock_status")))
            if mapped:
                return mapped
    mapped = _normalize_availability_status(status)
    if mapped:
        return mapped
    if not status:
        return "in_stock"
    return "in_stock"


def _match_option_name(name: str, aliases: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(alias in lowered for alias in aliases)


def _attr_from_woo_variants(product: Product, field: str) -> str:
    aliases = _ATTR_ALIASES.get(field, (field,))
    for variant in product.variants:
        name = str(variant.get("name") or "")
        if not _match_option_name(name, aliases):
            continue
        options = variant.get("options") or []
        if options:
            return str(options[0]).strip()
    return ""


def _attr_from_shopify_variant(variant: dict[str, Any], field: str) -> str:
    if not variant:
        return ""

    option_names = variant.get("_shopify_option_names") or []
    aliases = _ATTR_ALIASES.get(field, (field,))
    for index, name in enumerate(option_names[:3]):
        if not _match_option_name(str(name), aliases):
            continue
        key = f"option{index + 1}"
        value = str(variant.get(key) or "").strip()
        if value and value.lower() not in {"default title", "default"}:
            return value

    color, size = _parse_slash_parts(variant)
    if field == "color" and color:
        return color
    if field == "size" and size:
        return size

    opt1 = str(variant.get("option1") or "").strip()
    opt2 = str(variant.get("option2") or "").strip()
    opt3 = str(variant.get("option3") or "").strip()
    for value in (opt1, opt2, opt3):
        if value.lower() in {"default title", "default"}:
            continue
    if field == "size":
        if _looks_like_size(opt1):
            return opt1
        if _looks_like_size(opt2):
            return opt2
        if _looks_like_size(opt3):
            return opt3
        return opt2 or opt1
    if field == "color":
        for value in (opt1, opt2, opt3):
            if value and not _looks_like_size(value):
                return value
    if field == "gender":
        return opt3
    return ""


def _attr_from_variation_attributes(variant: dict[str, Any], field: str) -> str:
    aliases = _ATTR_ALIASES.get(field, (field,))
    for attr in variant.get("attributes") or []:
        name = str(attr.get("name") or "")
        if not _match_option_name(name, aliases):
            continue
        value = str(attr.get("option") or attr.get("value") or "").strip()
        if value:
            return value
    return ""


def _product_gmc_attr(product: Product, field: str) -> str:
    mapping = {
        "size": product.gmc_size,
        "color": product.gmc_color,
        "gender": product.gmc_gender,
        "age group": product.gmc_age_group,
    }
    value = mapping.get(field)
    return str(value or "").strip()


def _resolve_attr(
    product: Product,
    field: str,
    variant: dict[str, Any] | None,
    defaults: dict[str, str],
) -> str:
    manual = _product_gmc_attr(product, field)
    if manual:
        return manual
    if variant:
        value = _attr_from_shopify_variant(variant, field)
        if value:
            return value
        value = _attr_from_variation_attributes(variant, field)
        if value:
            return value
    value = _attr_from_woo_variants(product, field)
    if value:
        return value
    return defaults.get(field, "")


def _google_category(product: Product, default: str) -> str:
    if product.google_product_category and str(product.google_product_category).strip():
        return str(product.google_product_category).strip()
    if default.strip():
        return default.strip()
    for cat in product.categories:
        text = str(cat).strip()
        if text.isdigit():
            return text
    return ""


def _is_priced_variant(variant: dict[str, Any]) -> bool:
    if variant.get("price") not in (None, ""):
        return True
    if variant.get("regular_price") not in (None, ""):
        return True
    return variant.get("id") not in (None, "")


def expand_gmc_variants(
    product: Product,
    variant_mode: str = "all_variations",
) -> list[dict[str, Any] | None]:
    if variant_mode == "parent_only":
        return [None]

    priced = [v for v in product.variants if _is_priced_variant(v)]
    if priced:
        return priced
    return [None]


def _variant_link(product: Product, variant: dict[str, Any] | None) -> str:
    base = (product.url or "").strip()
    if not base or not variant:
        return base
    variant_id = variant.get("id")
    if not variant_id:
        return base
    parsed = urlparse(base)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["variant"] = str(variant_id)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _variant_title(product: Product, variant: dict[str, Any] | None) -> str:
    base = _plain_gmc_text(product.title)
    if not variant:
        return _plain_gmc_text(base, limit=GMC_TITLE_MAX_LENGTH)
    part = _plain_gmc_text(variant.get("title"))
    if not part or part.lower() in {"default title", "default"}:
        options = [
            _plain_gmc_text(variant.get(key))
            for key in ("option1", "option2", "option3")
        ]
        part = " / ".join(option for option in options if option)
    if not part:
        return _plain_gmc_text(base, limit=GMC_TITLE_MAX_LENGTH)
    if part.lower() in base.lower():
        return _plain_gmc_text(base, limit=GMC_TITLE_MAX_LENGTH)
    return _plain_gmc_text(f"{base} - {part}", limit=GMC_TITLE_MAX_LENGTH)


def _variant_price(product: Product, variant: dict[str, Any] | None) -> float | None:
    if variant:
        return (
            _safe_float(variant.get("price"))
            or _safe_float(variant.get("regular_price"))
            or _safe_float(product.price)
        )
    return _safe_float(product.price)


def _variant_image(product: Product, variant: dict[str, Any] | None) -> str:
    if variant:
        image = str(variant.get("image_url") or variant.get("image") or "").strip()
        if image:
            return image
    images = collect_product_image_urls(product)
    return images[0] if images else ""


def _resolve_gtin(product: Product, variant: dict[str, Any] | None, defaults: dict[str, str] | None = None) -> str:
    if product.gtin and str(product.gtin).strip():
        return str(product.gtin).strip()
    if variant:
        value = str(variant.get("barcode") or variant.get("gtin") or "").strip()
        if value:
            return value
    defaults = defaults or {}
    return str(defaults.get("gtin") or "").strip()


def _resolve_mpn(product: Product, variant: dict[str, Any] | None) -> str:
    if variant:
        sku = str(variant.get("sku") or "").strip()
        if sku:
            return sku
    return (product.sku or "").strip()


_SUPPLIER_VENDOR_NAMES = frozenset(
    {
        "trendsi",
        "cjdropshipping",
        "cj dropshipping",
        "oberlo",
        "spocket",
        "syncee",
        "modalyst",
        "printful",
        "printify",
    }
)


def is_supplier_vendor(name: str) -> bool:
    return str(name or "").strip().lower() in _SUPPLIER_VENDOR_NAMES


def _resolve_gmc_brand(
    product: Product,
    defaults: dict[str, str] | None = None,
) -> str:
    """Brand GMC: ưu tiên tên shop; bỏ vendor nhà cung cấp dropship (Trendsi…)."""
    defaults = defaults or {}
    shop_brand = str(defaults.get("brand") or "").strip()
    vendor = (product.vendor or "").strip()
    vendor_key = vendor.lower()

    if shop_brand and vendor_key in _SUPPLIER_VENDOR_NAMES:
        return shop_brand
    if shop_brand and (not vendor or vendor_key == shop_brand.lower()):
        return shop_brand
    if vendor:
        return vendor
    return shop_brand or (product.product_type or "").strip()


def finalize_gmc_feed_row(row: dict[str, str], fields: ExportFields) -> dict[str, str]:
    """Áp dụng cùng quy tắc ẩn trường cho preview và file xuất GMC."""
    updated = dict(row)
    if not fields.includes("images"):
        updated["image link"] = ""
    if not fields.includes("description"):
        updated["description"] = ""
    if not fields.includes("vendor"):
        updated["brand"] = ""
    return updated


def product_to_gmc_row(
    product: Product,
    rules: ExportRules | None = None,
    variant: dict[str, Any] | None = None,
    *,
    currency: str = "USD",
    condition: str = "new",
    google_category_default: str = "",
    defaults: dict[str, str] | None = None,
) -> dict[str, str]:
    product = apply_export_rules(product, rules)
    defaults = defaults or {}
    code = currency or product.currency or "USD"

    row_id = str(product.id or "")
    if variant and variant.get("id"):
        row_id = str(variant.get("id"))

    gtin = _resolve_gtin(product, variant, defaults)
    mpn = _resolve_mpn(product, variant)
    brand = _resolve_gmc_brand(product, defaults)

    return {
        "id": row_id,
        "title": _variant_title(product, variant),
        "description": _plain_description(product.description),
        "link": _variant_link(product, variant),
        "condition": (str(product.gmc_condition or "").strip() or condition or "new"),
        "price": _format_gmc_price(_variant_price(product, variant), code),
        "availability": _availability(product.status, variant),
        "image link": _variant_image(product, variant),
        "gtin": gtin,
        "mpn": mpn,
        "brand": brand,
        "google product category": _google_category(product, google_category_default),
        "size": _resolve_attr(product, "size", variant, defaults),
        "gender": _resolve_attr(product, "gender", variant, defaults)
        or defaults.get("gender", ""),
        "age group": _normalize_age_group(
            _resolve_attr(product, "age group", variant, defaults)
            or defaults.get("age group", "")
        ),
        "color": _resolve_attr(product, "color", variant, defaults),
    }


def count_gmc_export_rows(
    products: list[Product],
    variant_mode: str = "all_variations",
) -> int:
    total = 0
    for product in products:
        total += len(expand_gmc_variants(product, variant_mode))
    return total


def export_gmc_feed_import(
    products: list[Product],
    output_path: str,
    rules: ExportRules | None = None,
    fields: ExportFields | None = None,
    progress: ExportProgress | None = None,
    currency: str = "USD",
    condition: str = "new",
    google_category_default: str = "",
    gmc_defaults: dict[str, str] | None = None,
    variant_mode: str = "all_variations",
) -> Path:
    fields = fields or ExportFields.from_preset("all")
    final_csv = Path(output_path)
    final_csv.parent.mkdir(parents=True, exist_ok=True)
    gmc_defaults = gmc_defaults or {}

    rows_to_write: list[tuple[Product, dict[str, Any] | None]] = []
    for product in products:
        for variant in expand_gmc_variants(product, variant_mode):
            rows_to_write.append((product, variant))

    total = len(rows_to_write)
    if progress:
        progress.step("write", 0.0, "Ghi file CSV feed GMC...")

    with final_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=GMC_FEED_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for index, (product, variant) in enumerate(rows_to_write):
            row = product_to_gmc_row(
                product,
                rules=rules,
                variant=variant,
                currency=currency,
                condition=condition,
                google_category_default=google_category_default,
                defaults=gmc_defaults,
            )
            writer.writerow(finalize_gmc_feed_row(row, fields))
            if progress:
                progress.product(index, total)

    if progress:
        progress.step("write", 1.0, "Hoàn tất — tải file CSV feed GMC")

    return final_csv


_expand_variants = expand_gmc_variants
