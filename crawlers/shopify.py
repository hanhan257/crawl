from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from crawlers.base import BaseCrawler
from models.product import Product
from utils.crawl_parallel import fetch_pages_in_waves
from utils.description_html import (
    extract_shopify_description_from_html,
    shopify_description_from_item,
)
from utils.http_retry import DEFAULT_REQUEST_TIMEOUT, get_with_retry

CrawlProgressCallback = Callable[[int, int, int], None]
SHOPIFY_SEARCH_HANDLE_PATTERN = re.compile(r'"handle"\s*:\s*"([^"]+)"')
SHOPIFY_SEARCH_COUNT_PATTERN = re.compile(r"([\d][\d,]*)\s+items", re.I)
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
        request_delay: float = 0.0,
    ):
        self.shop_url = self._normalize_shop_url(shop_url)
        self.access_token = access_token
        self.api_version = api_version
        self.use_public_api = use_public_api
        self.request_delay = request_delay
        self.session = requests.Session()
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        self._page_local = threading.local()

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
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        if self.use_public_api:
            return self._crawl_public(
                limit=limit,
                max_pages=max_pages,
                max_workers=max_workers,
                progress_callback=progress_callback,
            )
        return self._crawl_admin(
            limit=limit,
            max_pages=max_pages,
            status=status,
            progress_callback=progress_callback,
        )

    def crawl_category(
        self,
        handle: str,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        if handle == "all":
            return self.crawl(
                limit=limit,
                max_pages=max_pages,
                max_workers=max_workers,
                progress_callback=progress_callback,
            )

        per_page = 250

        def fetch_page(page: int) -> list[dict[str, Any]]:
            return self._fetch_collection_page(handle, page)

        first_batch = fetch_page(1)
        estimated_pages = max_pages
        if estimated_pages is None:
            if limit is not None:
                estimated_pages = max(1, (limit + per_page - 1) // per_page)
            elif len(first_batch) >= per_page:
                estimated_pages = 2

        raw_items, _pages = fetch_pages_in_waves(
            fetch_page,
            per_page=per_page,
            first_batch=first_batch,
            max_pages=max_pages,
            max_workers=max_workers,
            limit=limit,
            progress_callback=progress_callback,
            estimated_pages=estimated_pages,
        )

        products: list[Product] = []
        for item in raw_items:
            product = self._map_public_product(item)
            product.categories = [handle]
            products.append(product)
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

    def crawl_search(
        self,
        query: str,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        search = query.strip()
        if not search:
            raise ValueError("Thiếu từ khóa tìm kiếm.")

        handles = self._discover_search_handles(search, max_pages=max_pages)
        if not handles:
            return []

        if limit is not None:
            handles = handles[:limit]

        total = len(handles)
        if progress_callback:
            progress_callback(0, total, 0)

        if max_workers <= 1:
            products: list[Product] = []
            for index, handle in enumerate(handles, start=1):
                batch = self.crawl_product(handle)
                if batch:
                    product = batch[0]
                    product.categories = [search]
                    products.append(product)
                if progress_callback and (
                    index == total or index % max(1, min(10, total // 20)) == 0
                ):
                    progress_callback(index, total, len(products))
            if progress_callback and total:
                progress_callback(total, total, len(products))
            return products

        products: list[Product] = []
        lock = threading.Lock()
        processed = 0

        def fetch_one(handle: str) -> Product | None:
            batch = self.crawl_product(handle)
            if not batch:
                return None
            product = batch[0]
            product.categories = [search]
            return product

        workers = max(1, min(max_workers, 32))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch_one, handle): handle for handle in handles}
            for future in as_completed(futures):
                processed += 1
                product = future.result()
                if product:
                    with lock:
                        products.append(product)
                if progress_callback and (
                    processed == total or processed % max(1, min(10, total // 20)) == 0
                ):
                    progress_callback(processed, total, len(products))

        if progress_callback and total:
            progress_callback(total, total, len(products))
        return products

    def count_search_results(self, query: str) -> int | None:
        try:
            response = get_with_retry(
                self.session,
                f"{self.shop_url}/search",
                params={"q": query.strip(), "type": "product"},
                timeout=DEFAULT_REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            return None

        match = SHOPIFY_SEARCH_COUNT_PATTERN.search(response.text)
        if match:
            return int(match.group(1).replace(",", ""))

        handles = self._extract_search_handles_from_html(response.text)
        return len(handles) if handles else None

    def _discover_search_handles(
        self,
        query: str,
        max_pages: int | None = None,
    ) -> list[str]:
        handles: list[str] = []
        seen: set[str] = set()
        page = 1

        while True:
            if max_pages and page > max_pages:
                break

            params: dict[str, str | int] = {"q": query, "type": "product"}
            if page > 1:
                params["page"] = page

            try:
                response = get_with_retry(
                    self.session,
                    f"{self.shop_url}/search",
                    params=params,
                    timeout=DEFAULT_REQUEST_TIMEOUT,
                )
            except requests.RequestException:
                break

            page_handles = self._extract_search_handles_from_html(response.text)
            if not page_handles:
                break

            added = 0
            for handle in page_handles:
                if handle in seen:
                    continue
                seen.add(handle)
                handles.append(handle)
                added += 1

            if added == 0:
                break
            if len(page_handles) < 8:
                break

            page += 1
            if self.request_delay:
                time.sleep(self.request_delay)

        return handles

    @staticmethod
    def _extract_search_handles_from_html(html: str) -> list[str]:
        handles: list[str] = []
        seen: set[str] = set()
        for match in SHOPIFY_SEARCH_HANDLE_PATTERN.finditer(html):
            handle = match.group(1).strip()
            if not handle or handle in seen:
                continue
            seen.add(handle)
            handles.append(handle)
        return handles

    def _fetch_collection_page(self, handle: str, page: int) -> list[dict[str, Any]]:
        response = get_with_retry(
            self._page_session(),
            f"{self.shop_url}/collections/{handle}/products.json",
            params={"limit": 250, "page": page},
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        batch = response.json().get("products", [])
        return batch if isinstance(batch, list) else []

    def _page_session(self) -> requests.Session:
        if not hasattr(self._page_local, "session"):
            self._page_local.session = requests.Session()
            self._page_local.session.headers.update(self.session.headers)
        return self._page_local.session

    def _fetch_public_page(self, page: int) -> list[dict[str, Any]]:
        response = get_with_retry(
            self._page_session(),
            f"{self.shop_url}/products.json",
            params={"limit": 250, "page": page},
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        batch = response.json().get("products", [])
        return batch if isinstance(batch, list) else []

    def _crawl_public(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        per_page = 250
        first_batch = self._fetch_public_page(1)
        estimated_pages = max_pages
        if estimated_pages is None:
            if limit is not None:
                estimated_pages = max(1, (limit + per_page - 1) // per_page)
            elif len(first_batch) >= per_page:
                estimated_pages = 2

        raw_items, _pages = fetch_pages_in_waves(
            self._fetch_public_page,
            per_page=per_page,
            first_batch=first_batch,
            max_pages=max_pages,
            max_workers=max_workers,
            limit=limit,
            progress_callback=progress_callback,
            estimated_pages=estimated_pages,
        )
        return [self._map_public_product(item) for item in raw_items]

    def _crawl_admin(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        status: str = "active",
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        products: list[Product] = []
        url: str | None = (
            f"{self.shop_url}/admin/api/{self.api_version}/products.json"
            f"?limit=250&status={status}"
        )
        page_count = 0
        per_page = 250

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
                    if progress_callback:
                        progress_callback(page_count + 1, page_count + 1, len(products))
                    return products

            page_count += 1
            if progress_callback:
                estimated = max_pages or (page_count + 1 if len(batch) >= per_page else page_count)
                progress_callback(page_count, estimated, len(products))

            url = self._next_link(response.headers.get("Link"))
            if url:
                time.sleep(self.request_delay)

        if progress_callback and products:
            progress_callback(page_count, page_count, len(products))

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
        option_names = [str(option.get("name") or "") for option in item.get("options") or []]

        mapped_variants: list[dict[str, Any]] = []
        for variant in variants:
            available = self._variant_available(variant)
            mapped_variants.append(
                {
                    "id": variant.get("id"),
                    "title": variant.get("title"),
                    "sku": variant.get("sku"),
                    "price": variant.get("price"),
                    "regular_price": variant.get("compare_at_price") or variant.get("price"),
                    "compare_at_price": variant.get("compare_at_price"),
                    "available": available,
                    "inventory_quantity": variant.get("inventory_quantity"),
                    "inventory_management": variant.get("inventory_management"),
                    "option1": variant.get("option1"),
                    "option2": variant.get("option2"),
                    "option3": variant.get("option3"),
                    "barcode": variant.get("barcode"),
                    "image_url": image_by_id.get(int(variant["image_id"]))
                    if variant.get("image_id")
                    else None,
                    "_shopify_option_names": option_names,
                }
            )

        if mapped_variants:
            any_in_stock = any(v.get("available") for v in mapped_variants)
            product_status = "instock" if any_in_stock else "out_of_stock"
        else:
            product_status = "active"

        return Product(
            source=self.source,
            id=str(item.get("id", "")),
            title=item.get("title") or "",
            description=self._resolve_description(item, urljoin(self.shop_url, f"/products/{item.get('handle')}")),
            price=self._safe_float(first_variant.get("price")),
            currency=None,
            url=urljoin(self.shop_url, f"/products/{item.get('handle')}"),
            image_urls=image_urls,
            vendor=item.get("vendor"),
            sku=first_variant.get("sku"),
            tags=self._parse_tags(item.get("tags")),
            product_type=item.get("product_type"),
            status=product_status,
            variants=mapped_variants,
        )

    @staticmethod
    def _variant_available(variant: dict[str, Any]) -> bool:
        if variant.get("available") is not None:
            return bool(variant.get("available"))
        mgmt = variant.get("inventory_management")
        if mgmt in (None, ""):
            return True
        qty = variant.get("inventory_quantity")
        if qty is not None:
            try:
                return int(qty) > 0
            except (TypeError, ValueError):
                return True
        return True

    def _map_admin_product(self, item: dict[str, Any]) -> Product:
        product = self._map_public_product(item)
        product.status = item.get("status")
        return product

    def _resolve_description(self, item: dict[str, Any], page_url: str | None) -> str | None:
        description = shopify_description_from_item(item)
        if description or not page_url:
            return description
        try:
            response = get_with_retry(
                self.session,
                page_url,
                timeout=DEFAULT_REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                return None
            return extract_shopify_description_from_html(response.text)
        except requests.RequestException:
            return None

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
