from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

import requests

from crawlers.base import BaseCrawler
from models.product import Product
from utils.crawl_parallel import fetch_pages_in_waves
from utils.http_retry import DEFAULT_REQUEST_TIMEOUT, get_with_retry

CrawlProgressCallback = Callable[[int, int, int], None]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class WooCommerceCrawler(BaseCrawler):
    """Crawl products from WooCommerce REST API or public Store API."""

    source = "woocommerce"

    def __init__(
        self,
        site_url: str,
        consumer_key: str = "",
        consumer_secret: str = "",
        request_delay: float = 0.5,
        verify_ssl: bool = True,
        use_public_api: bool = False,
    ):
        self.site_url = site_url.strip().rstrip("/")
        if not self.site_url.startswith("http"):
            self.site_url = f"https://{self.site_url}"
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.use_public_api = use_public_api or not (consumer_key and consumer_secret)
        self.request_delay = request_delay
        self.session = requests.Session()
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        self.session.verify = verify_ssl

    def crawl(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        status: str = "publish",
        per_page: int = 100,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        if self.use_public_api:
            return self._crawl_store_api(
                limit=limit,
                max_pages=max_pages,
                per_page=per_page,
                max_workers=max_workers,
                progress_callback=progress_callback,
            )
        return self._crawl_rest_api(
            limit=limit,
            max_pages=max_pages,
            status=status,
            per_page=per_page,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )

    def crawl_search(
        self,
        query: str,
        limit: int | None = None,
        max_pages: int | None = None,
        per_page: int = 100,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        search = query.strip()
        if not search:
            raise ValueError("Thiếu từ khóa tìm kiếm.")

        if self.use_public_api:
            return self._crawl_store_api(
                limit=limit,
                max_pages=max_pages,
                per_page=per_page,
                search=search,
                max_workers=max_workers,
                progress_callback=progress_callback,
            )

        return self._crawl_rest_api(
            limit=limit,
            max_pages=max_pages,
            per_page=per_page,
            search=search,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )

    def crawl_category(
        self,
        category_slug: str,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        category_id = self._resolve_category_id(category_slug)
        if category_id is None:
            raise ValueError(f"Không tìm thấy category: {category_slug}")

        if self.use_public_api:
            return self._crawl_store_api(
                limit=limit,
                max_pages=max_pages,
                category_id=category_id,
                category_slug=category_slug,
                max_workers=max_workers,
                progress_callback=progress_callback,
            )

        return self._crawl_rest_api(
            limit=limit,
            max_pages=max_pages,
            category_id=category_id,
            category_slug=category_slug,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )

    def crawl_product(self, slug: str) -> list[Product]:
        if self.use_public_api:
            response = self.session.get(
                f"{self.site_url}/wp-json/wc/store/v1/products",
                params={"search": slug, "per_page": 20},
                timeout=30,
            )
            if response.status_code == 200:
                for item in response.json():
                    if item.get("slug") == slug:
                        return [self._map_store_product(item, categories=[slug])]

        if self.consumer_key and self.consumer_secret:
            response = self.session.get(
                f"{self.site_url}/wp-json/wc/v3/products",
                params={"slug": slug},
                auth=(self.consumer_key, self.consumer_secret),
                timeout=30,
            )
            response.raise_for_status()
            batch = response.json()
            if isinstance(batch, list) and batch:
                return [self._map_product(batch[0], fetch_variations=True)]

        return []

    def _resolve_category_id(self, category_slug: str) -> int | None:
        if self.use_public_api:
            response = self.session.get(
                f"{self.site_url}/wp-json/wc/store/v1/products/categories",
                params={"slug": category_slug},
                timeout=30,
            )
            if response.status_code == 200:
                data = response.json()
                if data:
                    return int(data[0]["id"])

        if self.consumer_key and self.consumer_secret:
            response = self.session.get(
                f"{self.site_url}/wp-json/wc/v3/products/categories",
                params={"slug": category_slug},
                auth=(self.consumer_key, self.consumer_secret),
                timeout=30,
            )
            if response.status_code == 200:
                data = response.json()
                if data:
                    return int(data[0]["id"])

        return None

    def _worker_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(self.session.headers)
        session.verify = self.session.verify
        return session

    def _fetch_store_page(self, page: int, params_base: dict[str, Any]) -> list[dict[str, Any]]:
        session = self._worker_session()
        params = {**params_base, "page": page}
        response = get_with_retry(
            session,
            f"{self.site_url}/wp-json/wc/store/v1/products",
            params=params,
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        batch = response.json()
        if not isinstance(batch, list):
            return []
        if self.request_delay and page > 1:
            time.sleep(self.request_delay / max(1, min(32, 4)))
        return batch

    def _fetch_rest_page(self, page: int, params_base: dict[str, Any]) -> list[dict[str, Any]]:
        session = self._worker_session()
        params = {**params_base, "page": page}
        response = get_with_retry(
            session,
            f"{self.site_url}/wp-json/wc/v3/products",
            params=params,
            auth=(self.consumer_key, self.consumer_secret),
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        batch = response.json()
        if not isinstance(batch, list):
            return []
        if self.request_delay and page > 1:
            time.sleep(self.request_delay / max(1, min(32, 4)))
        return batch

    def _crawl_store_api(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        per_page: int = 100,
        category_id: int | None = None,
        category_slug: str | None = None,
        search: str | None = None,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        per_page = min(per_page, 100)
        params_base: dict[str, Any] = {"per_page": per_page}
        if category_id:
            params_base["category"] = category_id
        if search:
            params_base["search"] = search

        categories = [category_slug] if category_slug else []
        if search:
            categories = [search]

        first_batch = self._fetch_store_page(1, params_base)
        if progress_callback:
            est_pages = max_pages
            if est_pages is None and limit is not None:
                est_pages = max(1, (limit + per_page - 1) // per_page)
            elif est_pages is None:
                est_pages = 1 if len(first_batch) < per_page else 2
            matched = min(len(first_batch), limit) if limit else len(first_batch)
            progress_callback(1, est_pages or 1, matched)

        raw_items, _pages = fetch_pages_in_waves(
            lambda page: self._fetch_store_page(page, params_base),
            per_page=per_page,
            first_batch=first_batch,
            max_pages=max_pages,
            max_workers=max_workers,
            limit=limit,
            progress_callback=progress_callback,
        )

        products: list[Product] = []
        for item in raw_items:
            products.append(self._map_store_product(item, categories=categories))
        return products

    def _crawl_rest_api(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        status: str = "publish",
        per_page: int = 100,
        category_id: int | None = None,
        category_slug: str | None = None,
        search: str | None = None,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        per_page = min(per_page, 100)
        params_base: dict[str, Any] = {
            "per_page": per_page,
            "status": status,
        }
        if category_id:
            params_base["category"] = category_id
        if search:
            params_base["search"] = search

        first_batch = self._fetch_rest_page(1, params_base)
        if progress_callback:
            est_pages = max_pages
            if est_pages is None and limit is not None:
                est_pages = max(1, (limit + per_page - 1) // per_page)
            elif est_pages is None:
                est_pages = 1 if len(first_batch) < per_page else 2
            matched = min(len(first_batch), limit) if limit else len(first_batch)
            progress_callback(1, est_pages or 1, matched)

        raw_items, _pages = fetch_pages_in_waves(
            lambda page: self._fetch_rest_page(page, params_base),
            per_page=per_page,
            first_batch=first_batch,
            max_pages=max_pages,
            max_workers=max_workers,
            limit=limit,
            progress_callback=progress_callback,
        )

        products: list[Product] = []
        for item in raw_items:
            product = self._map_product(item, fetch_variations=True)
            if category_slug:
                product.categories = [category_slug]
            elif search:
                product.categories = [search]
            products.append(product)
        return products

    def _map_store_product(
        self,
        item: dict[str, Any],
        categories: list[str] | None = None,
    ) -> Product:
        images = [img.get("src") for img in item.get("images") or [] if img.get("src")]
        item_categories = categories or [
            cat.get("name") for cat in item.get("categories") or [] if cat.get("name")
        ]
        prices = item.get("prices") or {}

        return Product(
            source=self.source,
            id=str(item.get("id", "")),
            title=item.get("name") or "",
            description=self._strip_html(item.get("description") or item.get("short_description")),
            price=self._safe_float(prices.get("price") or prices.get("regular_price")),
            currency=prices.get("currency_code"),
            url=item.get("permalink"),
            image_urls=images,
            vendor=None,
            sku=item.get("sku") or None,
            tags=[tag.get("name") for tag in item.get("tags") or [] if tag.get("name")],
            categories=item_categories,
            product_type=", ".join(item_categories) if item_categories else None,
            status=item.get("stock_status"),
            variants=[],
        )

    def _fetch_variations(self, product_id: int) -> list[dict[str, Any]]:
        if not (self.consumer_key and self.consumer_secret):
            return []

        variations: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self.session.get(
                f"{self.site_url}/wp-json/wc/v3/products/{product_id}/variations",
                params={"per_page": 100, "page": page},
                auth=(self.consumer_key, self.consumer_secret),
                timeout=30,
            )
            if response.status_code != 200:
                break
            batch = response.json()
            if not isinstance(batch, list) or not batch:
                break

            for item in batch:
                attrs = item.get("attributes") or []
                option_parts = [
                    str(attr.get("option") or "").strip()
                    for attr in attrs
                    if str(attr.get("option") or "").strip()
                ]
                image = item.get("image") or {}
                regular = self._safe_float(item.get("regular_price") or item.get("price"))
                sale = self._safe_float(item.get("sale_price"))
                current = self._safe_float(item.get("price"))
                variations.append(
                    {
                        "id": item.get("id"),
                        "title": " / ".join(option_parts) or item.get("sku") or "",
                        "sku": item.get("sku"),
                        "price": current,
                        "regular_price": regular,
                        "sale_price": sale if sale and regular and sale < regular else None,
                        "available": str(item.get("stock_status") or "").lower() == "instock",
                        "stock_status": item.get("stock_status"),
                        "barcode": item.get("global_unique_id") or item.get("barcode"),
                        "image_url": image.get("src"),
                        "attributes": attrs,
                        "option1": option_parts[0] if len(option_parts) > 0 else "",
                        "option2": option_parts[1] if len(option_parts) > 1 else "",
                        "option3": option_parts[2] if len(option_parts) > 2 else "",
                    }
                )

            if len(batch) < 100:
                break
            page += 1
            time.sleep(self.request_delay)

        return variations

    def _map_product(self, item: dict[str, Any], fetch_variations: bool = False) -> Product:
        images = [img.get("src") for img in item.get("images") or [] if img.get("src")]
        tags = [tag.get("name") for tag in item.get("tags") or [] if tag.get("name")]
        categories = [
            cat.get("name") for cat in item.get("categories") or [] if cat.get("name")
        ]

        permalink = item.get("permalink")
        if not permalink and item.get("slug"):
            permalink = urljoin(f"{self.site_url}/", f"product/{item['slug']}/")

        product_type = item.get("type") or (", ".join(categories) if categories else None)
        variants: list[dict[str, Any]] = []
        if item.get("type") == "variable" and fetch_variations:
            variants = self._fetch_variations(int(item["id"]))
        else:
            for index, attr in enumerate(item.get("attributes") or []):
                variants.append(
                    {
                        "id": attr.get("id"),
                        "name": attr.get("name"),
                        "options": attr.get("options") or [],
                        "position": attr.get("position", index),
                    }
                )

        price = self._safe_float(item.get("price") or item.get("regular_price"))
        if variants and variants[0].get("price") not in (None, ""):
            price = self._safe_float(variants[0].get("price"))

        return Product(
            source=self.source,
            id=str(item.get("id", "")),
            title=item.get("name") or "",
            description=self._strip_html(item.get("description") or item.get("short_description")),
            price=price,
            currency=None,
            url=permalink,
            image_urls=images,
            vendor=None,
            sku=item.get("sku") or None,
            tags=tags,
            categories=categories,
            product_type=product_type,
            status=item.get("stock_status") or item.get("status"),
            variants=variants,
        )

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            raw = str(value)
            if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
                return float(raw) / 100
            return float(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _strip_html(value: str | None) -> str | None:
        if not value:
            return None
        import re

        text = re.sub(r"<[^>]+>", " ", value)
        return " ".join(text.split()) or None
