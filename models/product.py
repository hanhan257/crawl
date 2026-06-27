from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Product:
    source: str
    id: str
    title: str
    description: str | None = None
    price: float | None = None
    currency: str | None = None
    url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    vendor: str | None = None
    sku: str | None = None
    tags: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    product_type: str | None = None
    status: str | None = None
    variants: list[dict[str, Any]] = field(default_factory=list)
    gtin: str | None = None
    google_product_category: str | None = None
    gmc_size: str | None = None
    gmc_color: str | None = None
    gmc_gender: str | None = None
    gmc_age_group: str | None = None
    gmc_condition: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
