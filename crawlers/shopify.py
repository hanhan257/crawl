from __future__ import annotations

import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from crawlers.base import BaseCrawler
from models.product import Product

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class ShopifyCrawler(BaseCrawler):
    """Crawl products from Shopify Admin API or public storefront JSON."""

    source = "shopify"

    def __init__(
        self,
        shop_url: str,
        access_token: str | None = None,
        api_version: str = "2024-10",
        use_public_api: bool = False,
        request_delay: float = 0.5,
    ):
        self.shop_url = self._normalize_shop_url(shop_url)
        self.access_token = access_token
        self.api_version = api_version
        self.use_public_api = use_public_api
        self.request_delay = request_delay
        self.session = requests.Session()
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT

        if not use_public_api and not access_token:
            raise ValueError("Shopify Admin API requires --token or use --public for storefront JSON")

    @staticmethod
    def _normalize_shop_url(shop_url: str) -> str:
        url = shop_url.strip().rstrip("/")
        if not url.startswith("http"):
            url = f"https://{url}"
        parsed = urlparse(url)
        host = parsed.netloc or parsed.path.split("/")[0]
        if host.endswith(".myshopify.com"):
            return f"https://{host}"
        if host and "." in host:
            return f"https://{host}"
        return f"https://{host}.myshopify.com"

    def crawl(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        status: str = "active",
    ) -> list[Product]:
        if self.use_public_api:
            return self._crawl_public(limit=limit, max_pages=max_pages)
        return self._crawl_admin(limit=limit, max_pages=max_pages, status=status)

    def crawl_category(
        self,
        handle: str,
        limit: int | None = None,
        max_pages: int | None = None,
    ) -> list[Product]:
        if handle == "all":
            return self.crawl(limit=limit, max_pages=max_pages)

        products: list[Product] = []
        page = 1

        while True:
            if max_pages and page > max_pages:
                break

            response = self.session.get(
                f"{self.shop_url}/collections/{handle}/products.json",
                params={"limit": 250, "page": page},
                timeout=30,
            )
            response.raise_for_status()
            batch = response.json().get("products", [])
            if not batch:
                break

            for item in batch:
                product = self._map_public_product(item)
                product.categories = [handle]
                products.append(product)
                if limit and len(products) >= limit:
                    return products

            if len(batch) < 250:
                break

            page += 1
            time.sleep(self.request_delay)

        return products

    def crawl_product(self, handle: str) -> list[Product]:
        response = self.session.get(
            f"{self.shop_url}/products/{handle}.json",
            timeout=30,
        )
        response.raise_for_status()
        item = response.json().get("product")
        if not item:
            return []
        return [self._map_public_product(item)]

    def _crawl_public(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
    ) -> list[Product]:
        products: list[Product] = []
        page = 1

        while True:
            if max_pages and page > max_pages:
                break

            response = self.session.get(
                f"{self.shop_url}/products.json",
                params={"limit": 250, "page": page},
                timeout=30,
            )
            response.raise_for_status()
            batch = response.json().get("products", [])
            if not batch:
                break

            for item in batch:
                products.append(self._map_public_product(item))
                if limit and len(products) >= limit:
                    return products

            if len(batch) < 250:
                break

            page += 1
            time.sleep(self.request_delay)

        return products

    def _crawl_admin(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        status: str = "active",
    ) -> list[Product]:
        products: list[Product] = []
        url: str | None = (
            f"{self.shop_url}/admin/api/{self.api_version}/products.json"
            f"?limit=250&status={status}"
        )
        page_count = 0

        headers = {"X-Shopify-Access-Token": self.access_token or ""}

        while url:
            if max_pages and page_count >= max_pages:
                break

            response = self.session.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            batch = response.json().get("products", [])

            for item in batch:
                products.append(self._map_admin_product(item))
                if limit and len(products) >= limit:
                    return products

            url = self._next_link(response.headers.get("Link"))
            page_count += 1
            if url:
                time.sleep(self.request_delay)

        return products

    @staticmethod
    def _next_link(link_header: str | list[str] | None) -> str | None:
        if not link_header:
            return None
        if isinstance(link_header, list):
            link_header = ", ".join(str(part) for part in link_header)
        for part in str(link_header).split(","):
            if 'rel="next"' in part:
                return part.split(";")[0].strip().strip("<>")
        return None

    @staticmethod
    def _parse_tags(raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, list):
            return [str(tag).strip() for tag in raw if str(tag).strip()]
        if isinstance(raw, str):
            return [tag.strip() for tag in raw.split(",") if tag.strip()]
        return [str(raw).strip()] if str(raw).strip() else []

    def _map_public_product(self, item: dict[str, Any]) -> Product:
        variants = item.get("variants") or []
        first_variant = variants[0] if variants else {}
        images = item.get("images") or []
        image_urls = [img.get("src") for img in images if img.get("src")]
        image_by_id = {
            int(img["id"]): img.get("src")
            for img in images
            if img.get("id") and img.get("src")
        }

        return Product(
            source=self.source,
            id=str(item.get("id", "")),
            title=item.get("title") or "",
            description=self._strip_html(item.get("body_html")),
            price=self._safe_float(first_variant.get("price")),
            currency=None,
            url=urljoin(self.shop_url, f"/products/{item.get('handle')}"),
            image_urls=image_urls,
            vendor=item.get("vendor"),
            sku=first_variant.get("sku"),
            tags=self._parse_tags(item.get("tags")),
            product_type=item.get("product_type"),
            status="active",
            variants=[
                {
                    "id": v.get("id"),
                    "title": v.get("title"),
                    "sku": v.get("sku"),
                    "price": v.get("price"),
                    "regular_price": v.get("compare_at_price") or v.get("price"),
                    "compare_at_price": v.get("compare_at_price"),
                    "available": v.get("available"),
                    "option1": v.get("option1"),
                    "option2": v.get("option2"),
                    "option3": v.get("option3"),
                    "barcode": v.get("barcode"),
                    "image_url": image_by_id.get(int(v["image_id"])) if v.get("image_id") else None,
                }
                for v in variants
            ],
        )

    def _map_admin_product(self, item: dict[str, Any]) -> Product:
        product = self._map_public_product(item)
        product.status = item.get("status")
        return product

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _strip_html(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, list):
            value = " ".join(str(part) for part in value if part is not None)
        if not isinstance(value, str):
            value = str(value)
        if not value.strip():
            return None
        import re

        text = re.sub(r"<[^>]+>", " ", value)
        return " ".join(text.split()) or None
