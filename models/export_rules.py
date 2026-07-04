from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass

from models.product import Product

GMC_TITLE_MAX_LENGTH = 150


@dataclass
class ExportRules:
    price_multiplier: float = 1.0
    price_add: float = 0.0
    round_to_99: bool = False
    title_remove: str = ""
    title_remove_ignore_case: bool = True
    title_find: str = ""
    title_replace: str = ""
    title_replace_ignore_case: bool = True
    title_prefix: str = ""
    title_suffix: str = ""
    category_override: str = ""
    category_add: str = ""
    tags_add: str = ""
    vendor_override: str = ""
    product_type_override: str = ""
    description_action: str = "find_replace"
    description_format: str = "html"
    description_override: str = ""
    description_find: str = ""
    description_replace: str = ""
    description_replace_ignore_case: bool = True
    description_remove: str = ""
    description_remove_ignore_case: bool = True
    description_convert_h_to_p: bool = True


def _round_price_99(value: float) -> float:
    import math

    whole = math.floor(value)
    return whole + 0.99 if value > whole else value


def _parse_list(value: str) -> list[str]:
    if not value or not str(value).strip():
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _apply_text_removals(text: str, removals: list[str], ignore_case: bool) -> str:
    result = text or ""
    for needle in removals:
        if not needle:
            continue
        if ignore_case:
            result = re.sub(re.escape(needle), "", result, flags=re.IGNORECASE)
        else:
            result = result.replace(needle, "")
    if "<" in result and ">" in result:
        return result.strip()
    return " ".join(result.split()).strip()


def _format_description_snippet(content: str, fmt: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    if fmt == "html":
        return text
    if "<" in text and ">" in text:
        return text
    return f"<p>{text}</p>"


def _apply_find_replace(text: str, find: str, replace: str, ignore_case: bool) -> str:
    if not find:
        return text
    flags = re.IGNORECASE if ignore_case else 0
    return re.sub(re.escape(find), replace, text, flags=flags)


def convert_h_tags_to_p(html: str) -> str:
    """Đổi thẻ h1–h6 thành thẻ p (giữ attribute nếu có)."""
    if not html:
        return html
    result = re.sub(r"<h([1-6])\b", "<p", html, flags=re.IGNORECASE)
    return re.sub(r"</h([1-6])>", "</p>", result, flags=re.IGNORECASE)


def _apply_description_rules(updated: Product, rules: ExportRules) -> None:
    fmt = rules.description_format or "html"
    base = updated.description or ""
    changed = False

    if rules.description_action == "replace_all":
        if rules.description_override.strip():
            base = _format_description_snippet(rules.description_override, fmt)
            changed = True
    elif rules.description_action == "find_replace":
        if rules.description_find.strip():
            base = _apply_find_replace(
                base,
                rules.description_find.strip(),
                rules.description_replace,
                rules.description_replace_ignore_case,
            )
            changed = True

        removals = _parse_list(rules.description_remove)
        if removals:
            base = _apply_text_removals(base, removals, rules.description_remove_ignore_case)
            changed = True

    if rules.description_convert_h_to_p:
        converted = convert_h_tags_to_p(base)
        if converted != base:
            base = converted
            changed = True

    if changed:
        updated.description = base.strip() or None


def apply_export_rules(product: Product, rules: ExportRules | None) -> Product:
    if not rules:
        return product

    updated = deepcopy(product)

    title_removals = _parse_list(rules.title_remove)
    if title_removals:
        updated.title = _apply_text_removals(
            updated.title or "",
            title_removals,
            rules.title_remove_ignore_case,
        )

    if rules.title_find.strip():
        updated.title = _apply_find_replace(
            updated.title or "",
            rules.title_find.strip(),
            rules.title_replace,
            rules.title_replace_ignore_case,
        )

    if rules.title_prefix or rules.title_suffix:
        updated.title = f"{rules.title_prefix}{updated.title}{rules.title_suffix}".strip()

    if rules.vendor_override:
        updated.vendor = rules.vendor_override

    if rules.product_type_override:
        updated.product_type = rules.product_type_override

    _apply_description_rules(updated, rules)

    if rules.category_override:
        updated.categories = _parse_list(rules.category_override)
    elif rules.category_add:
        updated.categories = _parse_list(rules.category_add)

    if rules.tags_add:
        updated.tags = _parse_list(rules.tags_add)

    if updated.price is not None:
        price = float(updated.price) * rules.price_multiplier + rules.price_add
        if rules.round_to_99:
            price = _round_price_99(price)
        updated.price = round(max(price, 0), 2)

        for variant in updated.variants:
            if "price" in variant and variant["price"] not in (None, ""):
                try:
                    variant_price = float(variant["price"]) * rules.price_multiplier + rules.price_add
                    if rules.round_to_99:
                        variant_price = _round_price_99(variant_price)
                    variant["price"] = round(max(variant_price, 0), 2)
                except (TypeError, ValueError):
                    pass

    return updated


def apply_gmc_title_rules(
    product: Product,
    rules: ExportRules | None,
    *,
    shop_brand: str = "",
    prepend_brand: bool = False,
) -> Product:
    """Áp dụng quy tắc title cho feed GMC (tối đa 150 ký tự)."""
    updated = apply_export_rules(product, rules)
    if prepend_brand:
        brand = (shop_brand or updated.vendor or "").strip()
        title = (updated.title or "").strip()
        if brand and title and not title.lower().startswith(brand.lower()):
            updated.title = f"{brand} - {title}"
    title = (updated.title or "").strip()
    if len(title) > GMC_TITLE_MAX_LENGTH:
        updated.title = title[:GMC_TITLE_MAX_LENGTH].rstrip()
    return updated


def apply_rules_to_products(
    products: list[Product],
    rules: ExportRules | None,
) -> list[Product]:
    return [apply_export_rules(product, rules) for product in products]
