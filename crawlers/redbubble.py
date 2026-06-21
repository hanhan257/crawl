from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import quote, urljoin

import requests

from crawlers.base import BaseCrawler
from models.product import Product
from utils.http_session import ProxyRotator, create_http_session, reset_session

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
        self.proxy_rotator = ProxyRotator(proxy_url)
        self.session = create_http_session(
            self.proxy_rotator.current(),
            for_redbubble=True,
        )

    def refresh_session(self, *, rotate: bool = True) -> None:
        """Tạo session mới — đổi sang proxy kế trong pool nếu rotate=True."""
        proxy = self.proxy_rotator.rotate() if rotate else self.proxy_rotator.current()
        self.session = reset_session(self.session, proxy, for_redbubble=True)

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
                if self.proxy_rotator.count > 1:
                    self.refresh_session(rotate=True)
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
                if self.proxy_rotator.count > 1:
                    self.refresh_session(rotate=True)
                time.sleep(self.request_delay)

        return products

    def crawl_product(self, url: str) -> Product | None:
        page_props = self._fetch_page_props(url)
        inventory_item = (
            page_props.get("initialInventoryItem")
            or page_props.get("inventoryItem")
            or page_props.get("inventory_item")
        )
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

    def probe_category(self, category_url: str) -> dict[str, Any]:
        """Đếm SP từ pagination.total / resultCount trên trang /shop/..."""
        page_props = self._fetch_page_props(category_url)
        pagination = page_props.get("pagination") or {}
        total_raw = pagination.get("total")
        if total_raw is None:
            total_raw = page_props.get("resultCount")
        total = int(total_raw) if total_raw is not None else None
        category_name = (
            self._category_from_page_props(
                page_props,
                self._extract_category_name(category_url),
            )
            or page_props.get("title")
            or self._extract_category_name(category_url)
        )
        return {
            "total": total,
            "category_name": category_name,
            "per_page": pagination.get("perPage"),
            "results_on_page": len(page_props.get("results") or []),
        }

    def _fetch_page_props(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempts = max(1, self.proxy_rotator.count)
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()
                match = NEXT_DATA_PATTERN.search(response.text)
                if not match:
                    raise ValueError(f"Could not find __NEXT_DATA__ on page: {url}")
                data = json.loads(match.group(1))
                return data.get("props", {}).get("pageProps", {})
            except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < attempts and self.proxy_rotator.count > 1:
                    self.refresh_session(rotate=True)
                    if self.request_delay:
                        time.sleep(self.request_delay)
                    continue
                break

        if last_error:
            raise last_error
        raise ValueError(f"Could not fetch page: {url}")

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
    def _upgrade_product_mockup_url(url: str) -> str:
        """Thumbnail search 600×600 → mockup sản phẩm 750×1000 (giống web Redbubble)."""
        if not url or "/flat," in url or "ssrco," not in url:
            return url
        upgraded = re.sub(
            r"srp,square,600x600-bg",
            "primary,wide_portrait,750x1000-bg",
            url,
        )
        if "primary,wide_portrait,750x1000-bg" not in upgraded:
            upgraded = re.sub(
                r"primary,square,1000x1000-bg",
                "primary,wide_portrait,750x1000-bg",
                upgraded,
            )
        return upgraded

    @staticmethod
    def _preview_url_candidates(preview: dict[str, Any]) -> list[str]:
        urls: list[str] = []
        main = preview.get("url")
        if main:
            urls.append(str(main))
        for fmt in preview.get("formats") or []:
            fmt_url = fmt.get("url")
            if fmt_url:
                urls.append(str(fmt_url))
        return urls

    @staticmethod
    def _mockup_url_rank(url: str, preview_type: str) -> tuple[int, int]:
        """Rank thấp = ưu tiên cao. Ưu tiên ssrco mockup 750×1000, bỏ flat artwork."""
        if "/flat," in url or preview_type == "artwork":
            return (999, 0)
        if "ssrco," not in url:
            return (998, 0)

        type_rank = {
            "primary": 0,
            "primary_square": 1,
            "product_close": 2,
            "alternate_product_close": 3,
            "supplementary": 4,
            "supplementary2": 5,
            "supplementary3": 6,
            "supplementary4": 7,
        }.get(preview_type, 50)

        size_rank = 0
        if "primary,wide_portrait,750x1000-bg" in url:
            size_rank = 0
        elif "750x1000" in url:
            size_rank = 1
        elif "1000x1000" in url:
            size_rank = 2
        elif "600x600" in url:
            size_rank = 3
        else:
            size_rank = 4
        return (type_rank, size_rank)

    @classmethod
    def _extract_image_urls(cls, previews: list[dict[str, Any]]) -> list[str]:
        ranked: list[tuple[tuple[int, int], int, str]] = []

        for order, preview in enumerate(previews):
            preview_type = str(preview.get("previewTypeId") or "")
            for raw_url in cls._preview_url_candidates(preview):
                url = cls._upgrade_product_mockup_url(raw_url)
                rank = cls._mockup_url_rank(url, preview_type)
                if rank[0] >= 999:
                    continue
                ranked.append((rank, order, url))

        ranked.sort(key=lambda item: (item[0], item[1]))

        image_urls: list[str] = []
        for _, _, url in ranked:
            if url not in image_urls:
                image_urls.append(url)
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
