from __future__ import annotations

import itertools
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from models.product import Product


@dataclass(frozen=True)
class VariantValue:
    label: str
    price_delta: float = 0.0


@dataclass
class VariantOption:
    name: str
    values: list[VariantValue] = field(default_factory=list)


@dataclass
class VariantExpansionConfig:
    options: list[VariantOption]
    base_price_override: float | None = None

    @property
    def option_names(self) -> list[str]:
        return [opt.name for opt in self.options]


def parse_option_names(text: str) -> list[str]:
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


def parse_attribute_line(line: str) -> list[VariantValue]:
    """Parse dòng attribute: S,M=1,L=2 hoặc Black."""
    values: list[VariantValue] = []
    for part in str(line or "").split(","):
        token = part.strip()
        if not token:
            continue
        if "=" in token:
            label, delta_text = token.split("=", 1)
            label = label.strip()
            try:
                delta = float(delta_text.strip())
            except ValueError:
                delta = 0.0
            values.append(VariantValue(label=label, price_delta=delta))
        else:
            values.append(VariantValue(label=token, price_delta=0.0))
    return values


def build_variant_config(
    option_names_text: str,
    attribute_lines_text: str,
    *,
    base_price_override: float | None = None,
) -> VariantExpansionConfig:
    names = parse_option_names(option_names_text)
    lines = [ln.strip() for ln in str(attribute_lines_text or "").splitlines() if ln.strip()]
    if not names:
        raise ValueError("Chưa khai báo Option (vd. Size, Color).")
    if len(lines) < len(names):
        raise ValueError(
            f"Cần **{len(names)}** dòng attribute (mỗi option một dòng), hiện có **{len(lines)}**."
        )

    options: list[VariantOption] = []
    for name, line in zip(names, lines):
        values = parse_attribute_line(line)
        if not values:
            raise ValueError(f"Option **{name}** chưa có giá trị.")
        options.append(VariantOption(name=name, values=values))

    return VariantExpansionConfig(
        options=options,
        base_price_override=base_price_override,
    )


def _slug_part(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", str(text or "").strip()).strip("-")
    return cleaned[:24] or "v"


def _resolve_base_price(product: Product, config: VariantExpansionConfig) -> float:
    if config.base_price_override is not None:
        return float(config.base_price_override)
    if product.price is not None:
        return float(product.price)
    return 0.0


def expand_product_variants(product: Product, config: VariantExpansionConfig) -> Product:
    """Gắn biến thể vào Product — giữ SP gốc, thêm attribute defs + priced variants."""
    updated = deepcopy(product)
    base_price = _resolve_base_price(updated, config)
    parent_sku = (updated.sku or str(updated.id or "item")).strip() or "item"

    attribute_defs: list[dict[str, Any]] = []
    for opt in config.options:
        attribute_defs.append(
            {
                "name": opt.name,
                "value": ", ".join(v.label for v in opt.values),
                "options": [v.label for v in opt.values],
            }
        )

    priced_variants: list[dict[str, Any]] = []
    value_groups = [opt.values for opt in config.options]
    for combo in itertools.product(*value_groups):
        labels = [v.label for v in combo]
        price_delta = sum(v.price_delta for v in combo)
        variant_price = round(max(base_price + price_delta, 0), 2)
        slug = "-".join(_slug_part(label) for label in labels)
        option_map = {
            f"option{index + 1}": labels[index]
            for index in range(min(3, len(labels)))
        }
        priced_variants.append(
            {
                "id": f"{parent_sku}-{slug}",
                "title": " / ".join(labels),
                "sku": f"{parent_sku}-{slug}",
                "price": variant_price,
                "regular_price": variant_price,
                **option_map,
            }
        )

    updated.variants = attribute_defs + priced_variants
    if updated.price is None and base_price:
        updated.price = base_price
    return updated


def expand_products_with_variants(
    products: list[Product],
    config: VariantExpansionConfig,
) -> list[Product]:
    return [expand_product_variants(product, config) for product in products]


def preview_variant_lines(
    config: VariantExpansionConfig,
    *,
    base_price: float = 36.99,
    limit: int = 12,
) -> list[str]:
    """Xem trước dòng biến thể: Size,Color,Giá."""
    names = config.option_names
    lines: list[str] = []
    value_groups = [opt.values for opt in config.options]
    for combo in itertools.product(*value_groups):
        labels = [v.label for v in combo]
        price_delta = sum(v.price_delta for v in combo)
        price = round(max(base_price + price_delta, 0), 2)
        row = [*labels, f"{price:.2f}"]
        lines.append(",".join(row))
        if len(lines) >= limit:
            break
    if names:
        header = ",".join([*names, "price"])
        return [header, *lines]
    return lines


def count_variants_per_product(config: VariantExpansionConfig) -> int:
    total = 1
    for opt in config.options:
        total *= max(len(opt.values), 1)
    return total
