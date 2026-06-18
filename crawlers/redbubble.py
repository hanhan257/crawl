from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import quote, urljoin

import requests

from crawlers.base import BaseCrawler
from models.product import Product
from utils.http_session import create_http_session, reset_session

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


class RedbubbleCrawler(BaseCrawler):
    """Crawl products from Redbubble search pages and product detail pages."""

    source = "redbubble"
    base_url = "https://www.redbubble.com"

    def __init__(self, request_delay: float = 1.0, proxy_url: str | None = None):
        self.request_delay = request_delay
        self.proxy_url = proxy_url
        self.session = create_http_session(proxy_url, for_redbubble=True)

    def refresh_session(self) -> None:
        """Xóa cookie và tạo session HTTP mới (thay IP/proxy mới nếu có)."""
        self.session = reset_session(self.session, self.proxy_url, for_redbubble=True)

    def crawl(
        self,
        query: str | None = None,
        url: str | None = None,
        urls: list[str] | None = None,
        limit: int | None = None,
        max_pages: int = 1,
    ) -> list[Product]:
        if urls:
            products = [self.crawl_product(u) for u in urls]
            return [p for p in products if p][: limit or None]

        if url:
            product = self.crawl_product(url)
            return [product] if product else []

        if not query:
            raise ValueError("Redbubble crawl requires --query, --url, --urls, or --category-url")

        return self.crawl_search(query=query, limit=limit, max_pages=max_pages)

    def crawl_category(
        self,
        category_url: str,
        limit: int | None = None,
        max_pages: int = 1,
    ) -> list[Product]:
        products: list[Product] = []
        category_name = self._extract_category_name(category_url)

        for page in range(1, max_pages + 1):
            params = {"page": page} if page > 1 else None
            page_props = self._fetch_page_props(category_url, params=params)
            page_category = self._category_from_page_props(page_props, category_name)

            for result in page_props.get("results") or []:
                product = self._map_search_result(result)
                if product:
                    product.categories = [page_category] if page_category else []
                    products.append(product)
                    if limit and len(products) >= limit:
                        return products

            pagination = page_props.get("pagination") or {}
            if not pagination.get("nextPage") or pagination.get("nextPage", {}).get("disabled"):
                break

            if page < max_pages:
                time.sleep(self.request_delay)

        return products

    def crawl_search(
        self,
        query: str,
        limit: int | None = None,
        max_pages: int = 1,
    ) -> list[Product]:
        products: list[Product] = []
        encoded_query = quote(query.strip())

        for page in range(1, max_pages + 1):
            params = {"page": page} if page > 1 else None
            search_url = f"{self.base_url}/shop/{encoded_query}"
            page_props = self._fetch_page_props(search_url, params=params)

            for result in page_props.get("results") or []:
                product = self._map_search_result(result)
                if product:
                    products.append(product)
                    if limit and len(products) >= limit:
                        return products

            pagination = page_props.get("pagination") or {}
            if not pagination.get("nextPage") or pagination.get("nextPage", {}).get("disabled"):
                break

            if page < max_pages:
                time.sleep(self.request_delay)

        return products

    def crawl_product(self, url: str) -> Product | None:
        page_props = self._fetch_page_props(url)
        inventory_item = page_props.get("inventoryItem") or page_props.get("inventory_item")
        if inventory_item:
            return self._map_inventory_item(inventory_item, fallback_url=url)

        work = page_props.get("work")
        if work:
            return self._map_work_only(work, url=url)

        for result in page_props.get("results") or []:
            product = self._map_search_result(result)
            if product:
                return product

        return None

    def _fetch_page_props(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        match = NEXT_DATA_PATTERN.search(response.text)
        if not match:
            raise ValueError(f"Could not find __NEXT_DATA__ on page: {url}")
        data = json.loads(match.group(1))
        return data.get("props", {}).get("pageProps", {})

    def _map_search_result(self, result: dict[str, Any]) -> Product | None:
        inventory_item = result.get("inventoryItem")
        if not inventory_item:
            return None
        return self._map_inventory_item(inventory_item)

    def _map_inventory_item(
        self,
        item: dict[str, Any],
        fallback_url: str | None = None,
    ) -> Product:
        work = item.get("work") or {}
        price_info = item.get("price") or {}
        preview_set = item.get("previewSet") or {}
        previews = preview_set.get("previews") or []
        image_urls = self._extract_image_urls(previews)

        product_url = (
            item.get("productPageUrl")
            or (item.get("productPageUrls") or {}).get("url")
            or fallback_url
        )

        stock_status = (item.get("stockStatus") or {}).get("status")

        return Product(
            source=self.source,
            id=str(item.get("id") or work.get("id") or ""),
            title=work.get("title") or item.get("description") or "",
            description=item.get("description"),
            price=self._safe_float(price_info.get("amount")),
            currency=price_info.get("currency"),
            url=product_url,
            image_urls=image_urls,
            vendor=work.get("artistShopDisplayName") or work.get("artistUsername"),
            sku=item.get("blankItemId"),
            tags=work.get("tags") or [],
            product_type=item.get("description") or item.get("productTypeId"),
            status=stock_status,
            variants=[
                {
                    "name": attr.get("name"),
                    "value": attr.get("value"),
                }
                for attr in item.get("attributes") or []
            ],
        )

    def _map_work_only(self, work: dict[str, Any], url: str) -> Product:
        return Product(
            source=self.source,
            id=str(work.get("id") or ""),
            title=work.get("title") or "",
            description=None,
            price=None,
            currency=None,
            url=url,
            image_urls=[],
            vendor=work.get("artistShopDisplayName") or work.get("artistUsername"),
            sku=None,
            tags=work.get("tags") or [],
            product_type=None,
            status=None,
            variants=[],
        )

    @staticmethod
    def _extract_image_urls(previews: list[dict[str, Any]]) -> list[str]:
        priority = {
            "artwork": 0,
            "product_close": 1,
            "alternate_product_close": 2,
            "primary": 3,
            "primary_square": 4,
        }

        def sort_key(preview: dict[str, Any]) -> tuple[int, str]:
            preview_type = str(preview.get("previewTypeId") or "")
            return (priority.get(preview_type, 99), preview_type)

        image_urls: list[str] = []
        for preview in sorted(previews, key=sort_key):
            preview_url = preview.get("url")
            if preview_url and preview_url not in image_urls:
                image_urls.append(preview_url)
        return image_urls

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_category_name(category_url: str) -> str | None:
        match = re.search(r"/shop/([^/?#]+)", category_url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _category_from_page_props(page_props: dict[str, Any], fallback: str | None) -> str | None:
        category = page_props.get("category") or page_props.get("title") or page_props.get("topic")
        if isinstance(category, dict):
            return category.get("name") or category.get("title") or fallback
        if isinstance(category, str):
            return category
        return fallback
