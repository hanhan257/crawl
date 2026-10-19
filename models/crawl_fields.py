"""Trường dữ liệu user chọn khi crawl link."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from models.product import Product

CRAWL_FIELD_LABELS: dict[str, str] = {
    "title": "Title",
    "images": "Link ảnh",
    "url": "Link sản phẩm",
    "description": "Description",
    "categories": "Category",
}

ALL_CRAWL_FIELD_KEYS: tuple[str, ...] = tuple(CRAWL_FIELD_LABELS.keys())

# Giá luôn được crawl, không có nút tắt. Description chỉ khi khách chọn định dạng.
DEFAULT_CRAWL_FIELD_ON: frozenset[str] = frozenset(
    {"title", "images", "url", "categories"}
)


@dataclass
class CrawlFields:
    enabled: set[str] = field(default_factory=lambda: set(ALL_CRAWL_FIELD_KEYS))

    @classmethod
    def all_fields(cls) -> CrawlFields:
        return cls(enabled=set(ALL_CRAWL_FIELD_KEYS))

    @classmethod
    def from_keys(cls, keys: list[str] | set[str] | None) -> CrawlFields:
        picked = {k for k in (keys or []) if k in CRAWL_FIELD_LABELS}
        if not picked:
            picked = {"title"}
        return cls(enabled=picked)

    def includes(self, key: str) -> bool:
        return key in self.enabled

    def wants_description(self) -> bool:
        return self.includes("description")

    def labels(self) -> list[str]:
        return [CRAWL_FIELD_LABELS[k] for k in ALL_CRAWL_FIELD_KEYS if k in self.enabled]

    def apply_to_product(self, product: Product) -> Product:
        updated = deepcopy(product)
        if not self.includes("title"):
            updated.title = ""
        if not self.includes("description"):
            updated.description = None
        if not self.includes("images"):
            updated.image_urls = []
        if not self.includes("categories"):
            updated.categories = []
        if not self.includes("url"):
            updated.url = None
        return updated

    def apply_to_products(self, products: list[Product]) -> list[Product]:
        return [self.apply_to_product(p) for p in products]


def crawl_fields_from_session(session_state) -> CrawlFields:
    keys = [
        key
        for key in ALL_CRAWL_FIELD_KEYS
        if session_state.get(f"crawl_field_{key}", key in DEFAULT_CRAWL_FIELD_ON)
    ]
    if "description" in keys:
        fmt = str(session_state.get("crawl_description_format") or "").strip().lower()
        if fmt not in {"html", "text"}:
            keys = [key for key in keys if key != "description"]
    return CrawlFields.from_keys(keys)
