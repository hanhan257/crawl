"""Map Shopify Color option -> color_swatch variants (Trendsi-style export)."""
from __future__ import annotations

from typing import Any


COLOR_SWATCH_TYPE = "color_swatch"
_COLOR_NAMES = frozenset({"color", "colour", "màu", "mau"})


def _option_index(option_names: list[str]) -> int | None:
    for i, name in enumerate(option_names):
        if str(name or "").strip().casefold() in _COLOR_NAMES:
            return i
    return None


def _option_value(variant: dict[str, Any], index: int) -> str:
    key = f"option{index + 1}"
    return str(variant.get(key) or "").strip()


def attach_shopify_color_swatches(product) -> None:
    """Gắn color_swatch vào product.variants; set gmc_color nếu 1 màu."""
    variants = product.variants or []
    if not variants:
        return

    option_names: list[str] = []
    for v in variants:
        if isinstance(v, dict) and v.get("_shopify_option_names"):
            option_names = list(v["_shopify_option_names"])
            break

    color_idx = _option_index(option_names)
    if color_idx is None:
        return

    swatches: list[dict[str, Any]] = []
    seen: set[str] = set()
    colors: list[str] = []

    for variant in variants:
        if not isinstance(variant, dict):
            continue
        color = _option_value(variant, color_idx)
        if not color or color in seen:
            continue
        seen.add(color)
        colors.append(color)
        swatches.append(
            {
                "_type": COLOR_SWATCH_TYPE,
                "color": color,
                "price": variant.get("price"),
                "image_url": variant.get("image_url") or "",
                "url": product.url or "",
            }
        )

    if not swatches:
        return

    base = [v for v in variants if v.get("_type") != COLOR_SWATCH_TYPE]
    product.variants = base + swatches
    if len(colors) == 1:
        product.gmc_color = colors[0]
    elif len(colors) > 1:
        product.gmc_color = ", ".join(colors)
