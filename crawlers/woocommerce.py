from __future__ import annotations

import html as html_lib
import json
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests

from crawlers.base import BaseCrawler
from models.product import Product
from utils.crawl_parallel import API_LIST_WOO_MAX_WORKERS, fetch_pages_in_waves
from utils.description_html import (
    description_from_json_ld,
    extract_woo_description_from_html,
    woo_description_from_item,
)
from utils.http_retry import DEFAULT_REQUEST_TIMEOUT, get_with_retry
from utils.http_session import ProxyRotator, create_http_session

CrawlProgressCallback = Callable[[int, int, int], None]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
PRODUCT_LINK_RE = re.compile(
    r'href=["\'](https?://[^"\']+/product/[^"\'?#]+|/product/[^"\'?#]+)["\']',
    re.I,
)
PRICE_AMOUNT_RE = re.compile(
    r'woocommerce-Price-amount[^>]*>.*?<bdi>(?:<span[^>]*>[^<]*</span>)?([\d.,]+)',
    re.I | re.S,
)
SKU_RE = re.compile(r'class="sku"[^>]*>([^<]+)', re.I)
H1_TITLE_RE = re.compile(
    r'<h1[^>]*class="[^"]*product_title[^"]*"[^>]*>(.*?)</h1>',
    re.I | re.S,
)
BREADCRUMB_RE = re.compile(
    r'class="[^"]*woocommerce-breadcrumb[^"]*"[^>]*>(.*?)</nav>',
    re.I | re.S,
)
POSTED_IN_RE = re.compile(
    r'class="[^"]*posted_in[^"]*"[^>]*>(.*?)</span>',
    re.I | re.S,
)
CATEGORY_LINK_RE = re.compile(
    r'href=["\'][^"\']*/product-category/[^"\']+["\'][^>]*>([^<]+)</a>',
    re.I,
)
ANY_ANCHOR_TEXT_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.I | re.S)
SITEMAP_NS_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
HTML_DETAIL_MAX_WORKERS = 24
_SKIP_CATEGORY_LABELS = frozenset({"home", "shop", "products", "product"})



class WooCommerceCrawler(BaseCrawler):
    """Crawl products from WooCommerce REST API or public Store API."""

    source = "woocommerce"

    def __init__(
        self,
        site_url: str,
        consumer_key: str = "",
        consumer_secret: str = "",
        request_delay: float = 0.0,
        verify_ssl: bool = True,
        use_public_api: bool = False,
        proxy_url: str | None = None,
    ):
        self.site_url = site_url.strip().rstrip("/")
        if not self.site_url.startswith("http"):
            self.site_url = f"https://{self.site_url}"
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.use_public_api = use_public_api or not (consumer_key and consumer_secret)
        self.request_delay = request_delay
        self.proxy_rotator = ProxyRotator(proxy_url)
        self.session = create_http_session(self.proxy_rotator.current())
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        self.session.verify = verify_ssl
        self._api_total_pages: int | None = None
        self._parallel_fetch = False
        self._store_api_direct = False

    def _switch_to_direct_session(self) -> None:
        if self._store_api_direct or not self.proxy_rotator.current():
            return
        verify = self.session.verify
        self._store_api_direct = True
        self.proxy_rotator = ProxyRotator(None)
        self.session = create_http_session(None)
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        self.session.verify = verify

    def _store_api_probe_ok(self) -> bool:
        """Store API qua proxy hay bị chặn — thử direct trước khi crawl."""
        try:
            response = self.session.get(
                f"{self.site_url}/wp-json/wc/store/v1/products",
                params={"per_page": 1},
                timeout=DEFAULT_REQUEST_TIMEOUT,
            )
            if response.status_code == 200:
                data = response.json()
                return isinstance(data, list)
            if response.status_code in (401, 403, 429) and self.proxy_rotator.current():
                self._switch_to_direct_session()
                response = self.session.get(
                    f"{self.site_url}/wp-json/wc/store/v1/products",
                    params={"per_page": 1},
                    timeout=DEFAULT_REQUEST_TIMEOUT,
                )
                if response.status_code == 200:
                    data = response.json()
                    return isinstance(data, list)
        except requests.RequestException:
            if self.proxy_rotator.current():
                self._switch_to_direct_session()
                try:
                    response = self.session.get(
                        f"{self.site_url}/wp-json/wc/store/v1/products",
                        params={"per_page": 1},
                        timeout=DEFAULT_REQUEST_TIMEOUT,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        return isinstance(data, list)
                except requests.RequestException:
                    pass
        return False

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
            if not (self.consumer_key and self.consumer_secret):
                self._store_api_probe_ok()
            try:
                return self._crawl_store_api(
                    limit=limit,
                    max_pages=max_pages,
                    per_page=per_page,
                    max_workers=max_workers,
                    progress_callback=progress_callback,
                )
            except requests.HTTPError as exc:
                if self._is_auth_blocked(exc):
                    return self._crawl_html_catalog(
                        limit=limit,
                        max_pages=max_pages,
                        max_workers=max_workers,
                        progress_callback=progress_callback,
                    )
                raise
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
            try:
                return self._crawl_store_api(
                    limit=limit,
                    max_pages=max_pages,
                    per_page=per_page,
                    search=search,
                    max_workers=max_workers,
                    progress_callback=progress_callback,
                )
            except requests.HTTPError as exc:
                if self._is_auth_blocked(exc):
                    return self._crawl_html_listing(
                        f"{self.site_url}/?s={quote(search)}&post_type=product",
                        limit=limit,
                        max_pages=max_pages,
                        max_workers=max_workers,
                        progress_callback=progress_callback,
                        default_category=search,
                    )
                raise

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
        slug = (category_slug or "").strip().strip("/")
        if self.use_public_api:
            try:
                category_id = self._resolve_category_id(slug)
                if category_id is None:
                    # API category bị khóa / không có — thử HTML theo slug path.
                    return self._crawl_html_listing(
                        self._category_listing_url(slug),
                        limit=limit,
                        max_pages=max_pages,
                        max_workers=max_workers,
                        progress_callback=progress_callback,
                        default_category=slug.split("/")[-1].replace("-", " ").title(),
                    )
                return self._crawl_store_api(
                    limit=limit,
                    max_pages=max_pages,
                    category_id=category_id,
                    category_slug=slug,
                    max_workers=max_workers,
                    progress_callback=progress_callback,
                )
            except requests.HTTPError as exc:
                if self._is_auth_blocked(exc):
                    return self._crawl_html_listing(
                        self._category_listing_url(slug),
                        limit=limit,
                        max_pages=max_pages,
                        max_workers=max_workers,
                        progress_callback=progress_callback,
                        default_category=slug.split("/")[-1].replace("-", " ").title(),
                    )
                raise

        category_id = self._resolve_category_id(slug)
        if category_id is None:
            raise ValueError(f"Không tìm thấy category: {category_slug}")

        return self._crawl_rest_api(
            limit=limit,
            max_pages=max_pages,
            category_id=category_id,
            category_slug=slug,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )

    def crawl_product(self, slug: str) -> list[Product]:
        if self.use_public_api:
            try:
                response = self.session.get(
                    f"{self.site_url}/wp-json/wc/store/v1/products",
                    params={"search": slug, "per_page": 20},
                    timeout=30,
                )
                if response.status_code == 200:
                    for item in response.json():
                        if item.get("slug") == slug:
                            return [self._map_store_product(item, categories=[slug])]
                if response.status_code in (401, 403):
                    product = self._fetch_product_html(
                        f"{self.site_url}/product/{slug}/"
                    )
                    return [product] if product else []
            except requests.RequestException:
                product = self._fetch_product_html(f"{self.site_url}/product/{slug}/")
                return [product] if product else []

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

        product = self._fetch_product_html(f"{self.site_url}/product/{slug}/")
        return [product] if product else []

    @staticmethod
    def _is_auth_blocked(exc: requests.HTTPError) -> bool:
        response = exc.response
        return response is not None and response.status_code in (401, 403)

    def _category_listing_url(self, slug: str) -> str:
        cleaned = (slug or "").strip().strip("/")
        # GullPrint: /home-living/mug/ ; chuẩn Woo: /product-category/...
        direct = f"{self.site_url}/{cleaned}/"
        try:
            response = self.session.get(direct, timeout=20, allow_redirects=True)
            if response.status_code == 200 and (
                "/product/" in response.text.lower()
                or "woocommerce" in response.text.lower()
            ):
                # Tránh homepage giả (redirect về /).
                final_path = urlparse(response.url).path.rstrip("/")
                if final_path and final_path != "":
                    return response.url.split("?")[0].rstrip("/") + "/"
        except requests.RequestException:
            pass
        return f"{self.site_url}/product-category/{cleaned}/"

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
        proxy = (
            self.proxy_rotator.rotate()
            if self.proxy_rotator.count > 1
            else self.proxy_rotator.current()
        )
        session = create_http_session(proxy)
        session.headers.update(self.session.headers)
        session.headers["User-Agent"] = DEFAULT_USER_AGENT
        session.verify = self.session.verify
        return session

    @staticmethod
    def _total_pages_from_headers(response: requests.Response) -> int | None:
        for key in ("X-WP-TotalPages", "x-wp-totalpages"):
            raw = response.headers.get(key)
            if not raw:
                continue
            try:
                return max(1, int(raw))
            except (TypeError, ValueError):
                continue
        return None

    def _fetch_store_page(self, page: int, params_base: dict[str, Any]) -> list[dict[str, Any]]:
        batch = self._fetch_store_page_once(page, params_base)
        if (
            not batch
            and page > 1
            and self._api_total_pages
            and page < self._api_total_pages
        ):
            time.sleep(0.4)
            batch = self._fetch_store_page_once(page, params_base)
        return batch

    def _fetch_store_page_once(
        self, page: int, params_base: dict[str, Any]
    ) -> list[dict[str, Any]]:
        session = self._worker_session()
        params = {**params_base, "page": page}
        try:
            response = get_with_retry(
                session,
                f"{self.site_url}/wp-json/wc/store/v1/products",
                params=params,
                timeout=DEFAULT_REQUEST_TIMEOUT,
            )
        except requests.HTTPError as exc:
            if (
                self.proxy_rotator.current()
                and exc.response is not None
                and exc.response.status_code in (401, 403, 429)
            ):
                self._switch_to_direct_session()
                session = self._worker_session()
                response = get_with_retry(
                    session,
                    f"{self.site_url}/wp-json/wc/store/v1/products",
                    params=params,
                    timeout=DEFAULT_REQUEST_TIMEOUT,
                )
            else:
                raise
        except requests.RequestException:
            if self.proxy_rotator.current():
                self._switch_to_direct_session()
                session = self._worker_session()
                response = get_with_retry(
                    session,
                    f"{self.site_url}/wp-json/wc/store/v1/products",
                    params=params,
                    timeout=DEFAULT_REQUEST_TIMEOUT,
                )
            else:
                raise
        if page == 1:
            self._api_total_pages = self._total_pages_from_headers(response)
        batch = response.json()
        if not isinstance(batch, list):
            return []
        if self.request_delay and page > 1 and not self._parallel_fetch:
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
        if page == 1:
            self._api_total_pages = self._total_pages_from_headers(response)
        batch = response.json()
        if not isinstance(batch, list):
            return []
        if self.request_delay and page > 1 and not self._parallel_fetch:
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
        max_workers = max(1, min(int(max_workers or 1), API_LIST_WOO_MAX_WORKERS))
        self._parallel_fetch = max_workers > 1
        params_base: dict[str, Any] = {"per_page": per_page}
        if category_id:
            params_base["category"] = category_id
        if search:
            params_base["search"] = search

        categories = [category_slug] if category_slug else []
        if search:
            categories = [search]

        self._api_total_pages = None
        first_batch = self._fetch_store_page(1, params_base)
        estimated_pages = self._api_total_pages
        if estimated_pages is None:
            if max_pages:
                estimated_pages = max_pages
            elif limit is not None:
                estimated_pages = max(1, (limit + per_page - 1) // per_page)
            elif len(first_batch) >= per_page:
                estimated_pages = 2

        raw_items, _pages = fetch_pages_in_waves(
            lambda page: self._fetch_store_page(page, params_base),
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
        self._parallel_fetch = max_workers > 1
        params_base: dict[str, Any] = {
            "per_page": per_page,
            "status": status,
        }
        if category_id:
            params_base["category"] = category_id
        if search:
            params_base["search"] = search

        self._api_total_pages = None
        first_batch = self._fetch_rest_page(1, params_base)
        estimated_pages = self._api_total_pages
        if estimated_pages is None:
            if max_pages:
                estimated_pages = max_pages
            elif limit is not None:
                estimated_pages = max(1, (limit + per_page - 1) // per_page)
            elif len(first_batch) >= per_page:
                estimated_pages = 2

        raw_items, _pages = fetch_pages_in_waves(
            lambda page: self._fetch_rest_page(page, params_base),
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
            product = self._map_product(item, fetch_variations=False)
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
            description=self._resolve_description(item, item.get("permalink")),
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
            description=self._resolve_description(item, permalink),
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

    def _resolve_description(self, item: dict[str, Any], page_url: str | None) -> str | None:
        description = woo_description_from_item(item)
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
            return extract_woo_description_from_html(response.text)
        except requests.RequestException:
            return None

    def _crawl_html_catalog(
        self,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        urls = self._discover_product_urls_sitemap(max_sitemaps=max_pages)
        if not urls:
            urls = self._discover_product_urls_listing(
                f"{self.site_url}/shop/",
                limit=limit,
                max_pages=max_pages,
            )
        if limit:
            urls = urls[:limit]
        return self._products_from_html_urls(
            urls,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )

    def _crawl_html_listing(
        self,
        listing_url: str,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
        default_category: str = "",
    ) -> list[Product]:
        urls = self._discover_product_urls_listing(
            listing_url,
            limit=limit,
            max_pages=max_pages,
        )
        if limit:
            urls = urls[:limit]
        return self._products_from_html_urls(
            urls,
            max_workers=max_workers,
            progress_callback=progress_callback,
            default_category=default_category,
        )

    def _discover_product_urls_sitemap(
        self,
        *,
        max_sitemaps: int | None = None,
    ) -> list[str]:
        sitemap_urls = self._collect_product_sitemaps()
        if max_sitemaps and max_sitemaps > 0:
            sitemap_urls = sitemap_urls[:max_sitemaps]
        seen: set[str] = set()
        urls: list[str] = []
        for sitemap_url in sitemap_urls:
            for loc in self._parse_sitemap_locs(sitemap_url):
                if "/product/" not in loc.lower():
                    continue
                cleaned = loc.split("?")[0].rstrip("/") + "/"
                if cleaned in seen:
                    continue
                seen.add(cleaned)
                urls.append(cleaned)
        return urls

    def _collect_product_sitemaps(self) -> list[str]:
        candidates = [
            f"{self.site_url}/sitemap_index.xml",
            f"{self.site_url}/product-sitemap.xml",
            f"{self.site_url}/sitemap.xml",
        ]
        found: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            locs = self._parse_sitemap_locs(candidate)
            if not locs:
                continue
            product_sitemaps = [
                loc
                for loc in locs
                if "product-sitemap" in loc.lower() or "sitemap-product" in loc.lower()
            ]
            if product_sitemaps:
                for loc in product_sitemaps:
                    if loc not in seen:
                        seen.add(loc)
                        found.append(loc)
            elif any("/product/" in loc.lower() for loc in locs):
                if candidate not in seen:
                    seen.add(candidate)
                    found.append(candidate)
        if not found:
            # Yoast kiểu product-sitemap1.xml … khi index bị chặn.
            for index in range(1, 80):
                url = f"{self.site_url}/product-sitemap{index}.xml"
                locs = self._parse_sitemap_locs(url)
                if not locs:
                    if index > 3 and not found:
                        break
                    if index > 3:
                        break
                    continue
                if url not in seen:
                    seen.add(url)
                    found.append(url)
        return found

    def _parse_sitemap_locs(self, sitemap_url: str) -> list[str]:
        try:
            response = self.session.get(sitemap_url, timeout=30)
            if response.status_code != 200:
                return []
            ctype = (response.headers.get("content-type") or "").lower()
            text = response.text
            if "xml" not in ctype and not text.lstrip().startswith("<?xml"):
                return []
            return [html_lib.unescape(m.group(1).strip()) for m in SITEMAP_NS_LOC_RE.finditer(text)]
        except requests.RequestException:
            return []

    def _discover_product_urls_listing(
        self,
        listing_url: str,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
    ) -> list[str]:
        seen: set[str] = set()
        urls: list[str] = []
        page = 1
        while True:
            if max_pages and page > max_pages:
                break
            page_url = listing_url if page == 1 else self._listing_page_url(listing_url, page)
            try:
                response = self.session.get(page_url, timeout=30)
                if response.status_code != 200:
                    break
                page_html = response.text
            except requests.RequestException:
                break
            page_links = self._extract_product_links(page_html)
            if not page_links:
                break
            new_count = 0
            for link in page_links:
                if link in seen:
                    continue
                seen.add(link)
                urls.append(link)
                new_count += 1
                if limit and len(urls) >= limit:
                    return urls
            if new_count == 0:
                break
            page += 1
        return urls

    def _listing_page_url(self, listing_url: str, page: int) -> str:
        base = listing_url.split("?")[0].rstrip("/") + "/"
        if "post_type=product" in listing_url or "?s=" in listing_url:
            joiner = "&" if "?" in listing_url else "?"
            return f"{listing_url}{joiner}paged={page}"
        return f"{base}page/{page}/"

    def _extract_product_links(self, page_html: str) -> list[str]:
        links: list[str] = []
        seen: set[str] = set()
        for match in PRODUCT_LINK_RE.finditer(page_html):
            raw = html_lib.unescape(match.group(1).strip())
            absolute = urljoin(self.site_url + "/", raw)
            cleaned = absolute.split("?")[0].rstrip("/") + "/"
            if "/product/" not in cleaned.lower():
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            links.append(cleaned)
        return links

    def _products_from_html_urls(
        self,
        urls: list[str],
        *,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
        default_category: str = "",
    ) -> list[Product]:
        total = len(urls)
        if not total:
            return []
        workers = max(1, min(int(max_workers or 1), HTML_DETAIL_MAX_WORKERS, total))
        products: list[Product | None] = [None] * total

        def fetch_one(index: int, url: str) -> None:
            products[index] = self._fetch_product_html(
                url, default_category=default_category
            )

        if workers > 1 and total > 1:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(fetch_one, index, url)
                    for index, url in enumerate(urls)
                ]
                completed = 0
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        pass
                    completed += 1
                    if progress_callback and (
                        completed == total or completed % 2 == 0
                    ):
                        matched = sum(1 for item in products if item is not None)
                        progress_callback(completed, total, matched)
        else:
            for index, url in enumerate(urls):
                fetch_one(index, url)
                if progress_callback:
                    matched = sum(1 for item in products if item is not None)
                    progress_callback(index + 1, total, matched)

        if progress_callback and total:
            matched = sum(1 for item in products if item is not None)
            progress_callback(total, total, matched)

        return [item for item in products if item is not None]

    def _fetch_product_html(
        self,
        url: str,
        *,
        default_category: str = "",
    ) -> Product | None:
        try:
            response = self.session.get(url, timeout=30)
            if response.status_code != 200:
                return None
            page_html = response.text
        except requests.RequestException:
            return None

        if "single-product" not in page_html and "product_title" not in page_html:
            return None

        ld_product = self._parse_product_ld_json(page_html)
        title = ""
        if ld_product:
            title = str(ld_product.get("name") or "").strip()
        if not title:
            h1 = H1_TITLE_RE.search(page_html)
            if h1:
                title = re.sub(r"<[^>]+>", "", h1.group(1)).strip()
        title = html_lib.unescape(title)
        title = re.sub(r"\s*[-|].*$", "", title).strip() or title
        if not title:
            return None

        slug = urlparse(url).path.rstrip("/").split("/")[-1]
        sku = ""
        if ld_product:
            sku = str(ld_product.get("sku") or "").strip()
        if not sku:
            sku_match = SKU_RE.search(page_html)
            sku = sku_match.group(1).strip() if sku_match else slug

        price = None
        currency = "USD"
        if ld_product:
            offers = ld_product.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if isinstance(offers, dict):
                price = self._parse_html_price(offers.get("price"))
                currency = str(offers.get("priceCurrency") or currency).strip() or currency
        if price is None:
            amount = PRICE_AMOUNT_RE.search(page_html)
            if amount:
                price = self._parse_html_price(amount.group(1))

        images = self._collect_html_images(ld_product, page_html)
        description = None
        if ld_product:
            description = description_from_json_ld(ld_product.get("description"))
        if not description:
            description = extract_woo_description_from_html(page_html)

        categories: list[str] = []
        seen_cats: set[str] = set()

        def add_category(raw: object) -> None:
            text = html_lib.unescape(str(raw or "")).strip()
            text = re.sub(r"\s+", " ", text)
            if not text:
                return
            # JSON-LD đôi khi trả "HOLIDAYS > CHRISTMAS"
            parts = re.split(r"\s*[>|/]\s*", text)
            for part in parts:
                name = part.strip(" -|,")
                if not name:
                    continue
                key = name.lower()
                if key in _SKIP_CATEGORY_LABELS or key in seen_cats:
                    continue
                seen_cats.add(key)
                categories.append(name)

        if default_category:
            add_category(default_category)

        # Ưu tiên breadcrumb + meta Categories (đúng hơn JSON-LD trên GullPrint…).
        for name in self._extract_html_categories(page_html):
            add_category(name)

        if ld_product:
            add_category(ld_product.get("category"))
            brand = ld_product.get("brand")
            if isinstance(brand, dict):
                vendor = str(brand.get("name") or "").strip() or None
            else:
                vendor = str(brand or "").strip() or None
        else:
            vendor = None

        product_id = slug
        id_match = re.search(r"postid-(\d+)", page_html, re.I)
        if id_match:
            product_id = id_match.group(1)

        return Product(
            source=self.source,
            id=product_id,
            title=title,
            description=description,
            price=price,
            currency=currency,
            url=url,
            image_urls=images,
            vendor=vendor,
            sku=sku or None,
            categories=categories,
            product_type=categories[-1] if categories else None,
            status="instock",
        )

    @classmethod
    def _extract_html_categories(cls, page_html: str) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        def add(raw: str) -> None:
            text = html_lib.unescape(raw or "").strip()
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"\s+", " ", text).strip(" -|/")
            if not text:
                return
            key = text.lower()
            if key in _SKIP_CATEGORY_LABELS or key in seen:
                return
            if key.startswith("categories"):
                return
            seen.add(key)
            names.append(text)

        def add_from_block(block: str) -> None:
            # GullPrint dùng /home-living/mug/ thay vì /product-category/...
            for match in ANY_ANCHOR_TEXT_RE.finditer(block):
                add(match.group(1))
            for match in CATEGORY_LINK_RE.finditer(block):
                add(match.group(1))

        for block_re in (BREADCRUMB_RE, POSTED_IN_RE):
            block = block_re.search(page_html)
            if block:
                add_from_block(block.group(1))

        meta = re.search(
            r'class="[^"]*product_meta[^"]*"(.*?)</div>',
            page_html,
            re.I | re.S,
        )
        if meta:
            add_from_block(meta.group(1))

        return names

    @classmethod
    def _parse_product_ld_json(cls, page_html: str) -> dict[str, Any] | None:
        for match in LD_JSON_RE.finditer(page_html):
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            items: list[Any]
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict) and "@graph" in payload:
                graph = payload.get("@graph")
                items = graph if isinstance(graph, list) else [payload]
            else:
                items = [payload]
            for item in items:
                if not isinstance(item, dict):
                    continue
                raw_type = item.get("@type")
                types = raw_type if isinstance(raw_type, list) else [raw_type]
                if any(str(t).lower() == "product" for t in types):
                    return item
        return None

    @classmethod
    def _collect_html_images(
        cls,
        ld_product: dict[str, Any] | None,
        page_html: str,
    ) -> list[str]:
        images: list[str] = []
        seen: set[str] = set()

        def add(url: object) -> None:
            text = str(url or "").strip()
            if not text or text in seen:
                return
            if "logo" in text.lower() and "uploads" in text.lower() and len(images) == 0:
                # Cho phép logo nếu chưa có ảnh khác; bỏ nếu đã có gallery.
                pass
            if any(marker in text.lower() for marker in ("logo", "placeholder", "avatar")):
                if images:
                    return
            seen.add(text)
            images.append(text)

        if ld_product:
            raw_image = ld_product.get("image")
            if isinstance(raw_image, str):
                add(raw_image)
            elif isinstance(raw_image, list):
                for item in raw_image:
                    if isinstance(item, str):
                        add(item)
                    elif isinstance(item, dict):
                        add(item.get("url") or item.get("contentUrl"))

        for match in re.finditer(
            r'data-large_image=["\']([^"\']+)["\']', page_html, re.I
        ):
            add(match.group(1))
        for match in re.finditer(
            r'wp-post-image[^>]+src=["\']([^"\']+)["\']', page_html, re.I
        ):
            add(match.group(1))
        return images[:20]

    @staticmethod
    def _parse_html_price(value: object) -> float | None:
        if value in (None, ""):
            return None
        try:
            return round(float(str(value).replace(",", "").strip()), 2)
        except (TypeError, ValueError):
            return None

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
