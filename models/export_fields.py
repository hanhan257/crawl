from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable

from models.product import Product

ProgressCallback = Callable[[float, str], None]

EXPORT_FIELD_LABELS: dict[str, str] = {
    "title": "Tiêu đề (Title)",
    "price": "Giá (Price)",
    "description": "Mô tả (Description)",
    "images": "Ảnh (Images)",
    "categories": "Danh mục (Categories)",
    "tags": "Tags",
    "sku": "SKU",
    "vendor": "Vendor / thương hiệu",
    "product_type": "Loại SP (Product type)",
    "url": "Link sản phẩm (URL)",
    "variants": "Biến thể (Variants)",
    "meta": "Meta (source, id, currency, status)",
}

EXPORT_FIELD_PRESETS: dict[str, list[str]] = {
    "all": list(EXPORT_FIELD_LABELS.keys()),
    "basic": ["title", "price", "description"],
    "listing": ["title", "price", "description", "images", "categories"],
    "minimal": ["title", "price"],
    "shop": ["title", "price", "description", "images", "categories", "tags", "sku", "vendor", "product_type"],
}

PRESET_LABELS: dict[str, str] = {
    "all": "Tất cả trường",
    "basic": "Cơ bản — Title + Giá + Mô tả",
    "listing": "Listing — Title + Giá + Mô tả + Ảnh + Category",
    "minimal": "Tối giản — Title + Giá",
    "shop": "Shop import — đủ cho Woo/Shopify",
    "custom": "Tùy chọn từng trường",
}


@dataclass
class ExportFields:
    enabled: set[str] = field(default_factory=lambda: set(EXPORT_FIELD_PRESETS["all"]))

    @classmethod
    def from_preset(cls, preset: str, custom: list[str] | None = None) -> ExportFields:
        if preset == "custom" and custom:
            return cls(enabled=set(custom))
        keys = EXPORT_FIELD_PRESETS.get(preset, EXPORT_FIELD_PRESETS["all"])
        return cls(enabled=set(keys))

    def includes(self, key: str) -> bool:
        return key in self.enabled

    def labels(self) -> list[str]:
        return [EXPORT_FIELD_LABELS[k] for k in EXPORT_FIELD_LABELS if k in self.enabled]

    def apply_to_product(self, product: Product) -> Product:
        updated = deepcopy(product)
        if not self.includes("title"):
            updated.title = ""
        if not self.includes("price"):
            updated.price = None
        if not self.includes("description"):
            updated.description = None
        if not self.includes("images"):
            updated.image_urls = []
        if not self.includes("categories"):
            updated.categories = []
        if not self.includes("tags"):
            updated.tags = []
        if not self.includes("sku"):
            updated.sku = None
        if not self.includes("vendor"):
            updated.vendor = None
        if not self.includes("product_type"):
            updated.product_type = None
        if not self.includes("url"):
            updated.url = None
        if not self.includes("variants"):
            updated.variants = []
        if not self.includes("meta"):
            updated.source = ""
            updated.id = ""
            updated.currency = None
            updated.status = None
        return updated

    def apply_to_products(self, products: list[Product]) -> list[Product]:
        return [self.apply_to_product(p) for p in products]

    def raw_csv_columns(self) -> list[str]:
        mapping = {
            "meta": ["source", "id", "currency", "status"],
            "title": ["title"],
            "description": ["description"],
            "price": ["price"],
            "url": ["url"],
            "images": ["image_urls"],
            "vendor": ["vendor"],
            "sku": ["sku"],
            "tags": ["tags"],
            "categories": ["categories"],
            "product_type": ["product_type"],
            "variants": ["variants"],
        }
        columns: list[str] = []
        for key, cols in mapping.items():
            if self.includes(key):
                columns.extend(cols)
        return columns or ["title", "price"]


def report_progress(callback: ProgressCallback | None, value: float, message: str) -> None:
    if callback:
        callback(min(max(value, 0.0), 1.0), message)
