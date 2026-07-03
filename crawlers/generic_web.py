from __future__ import annotations

import json
import re
import time
import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse

import requests

from crawlers.base import BaseCrawler
from crawlers.generic_html import HTML_MAX_WORKERS, HTML_REQUEST_DELAY
from models.product import Product
from utils.http_session import create_http_session

CrawlProgressCallback = Callable[[int, int, int], None]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DATASHIELD_BACKEND_DEFAULT = "https://api.kingdomshirt.com"
SHOP_CATALOG_PAGE_SIZE = 12

NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)
GENERIC_PRODUCT_PATH = re.compile(r"/product/([^/?#]+)", re.I)
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
LD_JSON_PATTERN = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.I,
)
OUT_OF_STOCK_HTML = re.compile(
    r"\b(out of stock|out-of-stock|sold out|h[eê]t h[aà]ng)\b",
    re.I,
)


class GenericWebCrawler(BaseCrawler):
    """Crawl Next.js / web thuần qua sitemap + JSON productData (vd. keeptee.com)."""

    source = "generic-web"

    def __init__(
        self,
        site_url: str,
        *,
        proxy_url: str | None = None,
        request_delay: float = HTML_REQUEST_DELAY,
    ):
        self.site_url = self._normalize_site_url(site_url)
        self.request_delay = request_delay
        self._proxy_url = proxy_url
        self.session = create_http_session(proxy_url)
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        self._build_id: str | None = None
        self._init_data: dict[str, Any] | None = None
        self._shop_catalog_mode: bool | None = None
        self._html_catalog_mode: bool | None = None

    def _html_catalog(self):
        from crawlers.generic_html import GenericHtmlCatalog

        return GenericHtmlCatalog(
            self.site_url,
            proxy_url=self._proxy_url,
            request_delay=self.request_delay,
        )

    @staticmethod
    def _normalize_site_url(site_url: str) -> str:
        url = site_url.strip().rstrip("/")
        if not url.startswith("http"):
            url = f"https://{url}"
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def crawl(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        category_slug: str | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        if self._uses_shop_catalog():
            return self._crawl_shop_catalog(
                limit=limit,
                max_pages=max_pages,
                category_slug=category_slug,
                progress_callback=progress_callback,
                max_workers=max_workers,
            )
        if self._uses_html_catalog():
            return self._html_catalog().crawl(
                limit=limit,
                max_pages=max_pages,
                category_slug=category_slug,
                progress_callback=progress_callback,
                max_workers=max_workers,
            )
        product_urls = self._discover_product_urls(max_sitemaps=max_pages)
        return self._crawl_urls(
            product_urls,
            limit=limit,
            category_slug=category_slug,
            progress_callback=progress_callback,
            max_workers=max_workers,
        )

    def crawl_product(self, product_id: str) -> Product | None:
        product_id = product_id.strip().strip("/")
        if self._uses_html_catalog():
            items = self._html_catalog().crawl_product_path(product_id)
            return items[0] if items else None
        url = f"{self.site_url}/product/{product_id}"
        return self._fetch_product(url, product_id)

    def crawl_category_path(
        self,
        category_path: str,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        return self._html_catalog().crawl_category_path(
            category_path,
            limit=limit,
            max_pages=max_pages,
            progress_callback=progress_callback,
            max_workers=max_workers,
        )

    def crawl_search(
        self,
        query: str,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        return self._html_catalog().crawl_search(
            query,
            limit=limit,
            max_pages=max_pages,
            progress_callback=progress_callback,
            max_workers=max_workers,
        )

    def crawl_product_url(self, url: str) -> Product | None:
        match = GENERIC_PRODUCT_PATH.search(urlparse(url).path or "")
        if not match:
            return None
        return self._fetch_product(url, match.group(1))

    @staticmethod
    def category_slug(name: str) -> str:
        slug = re.sub(r"[^\w\s-]", "", str(name or "").lower(), flags=re.UNICODE)
        slug = re.sub(r"[-\s]+", "-", slug).strip("-")
        return slug or "uncategorized"

    def count_sitemap_products(self, max_sitemaps: int | None = None) -> int:
        if self._uses_shop_catalog():
            data = self._fetch_shop_catalog_page(category=None, page=1, limit=1)
            return int(data.get("total") or 0)
        if self._uses_html_catalog() and max_sitemaps is None:
            return 0

        sitemap_urls = self._collect_product_sitemaps()
        if max_sitemaps and max_sitemaps > 0:
            sitemap_urls = sitemap_urls[:max_sitemaps]

        total = 0
        for sitemap_url in sitemap_urls:
            for loc in self._parse_sitemap_locs(sitemap_url):
                if GENERIC_PRODUCT_PATH.search(urlparse(loc).path or ""):
                    total += 1
        return total

    def probe_categories(
        self,
        sample_size: int = 50,
        *,
        max_workers: int = 12,
    ) -> tuple[list[dict[str, Any]], str]:
        if self._uses_html_catalog():
            return self._html_catalog().probe_categories(max_workers=max_workers)

        if self._uses_shop_catalog():
            return self._probe_shop_categories(max_workers=max_workers)

        product_urls = self._discover_product_urls(max_sitemaps=2)[:sample_size]
        counts: dict[str, int] = {}
        labels: dict[str, str] = {}
        workers = max(1, min(max_workers, 48))

        def sample_one(url: str) -> tuple[str, str] | None:
            match = GENERIC_PRODUCT_PATH.search(urlparse(url).path or "")
            if not match:
                return None
            worker = GenericWebCrawler(self.site_url, proxy_url=self._proxy_url, request_delay=0)
            category = worker._fetch_product_category(url, match.group(1))
            if not category:
                return None
            return self.category_slug(category), category

        if workers <= 1 or len(product_urls) <= 1:
            samples = [sample_one(url) for url in product_urls]
        else:
            samples = []
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for result in executor.map(sample_one, product_urls):
                    samples.append(result)

        for item in samples:
            if not item:
                continue
            slug, category = item
            counts[slug] = counts.get(slug, 0) + 1
            labels.setdefault(slug, category)

        categories = [
            {"slug": slug, "name": labels[slug], "count": count}
            for slug, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]
        if not categories:
            note = "Không đọc được danh mục từ SP mẫu — category nằm trong từng SP khi crawl."
        elif len(product_urls) < sample_size:
            note = f"Danh mục từ **{len(product_urls)}** SP mẫu."
        else:
            note = f"Danh mục từ **{len(product_urls)}** SP mẫu (web thuần không có API collection)."
        return categories, note

    def _fetch_product_category(self, url: str, product_id: str) -> str:
        build_id = self._get_build_id()
        payloads: list[dict[str, Any]] = []

        if build_id:
            api_url = f"{self.site_url}/_next/data/{build_id}/product/{product_id}.json"
            try:
                resp = self.session.get(api_url, timeout=20)
                if resp.status_code == 200:
                    payload = resp.json()
                    if isinstance(payload, dict):
                        payloads.append(payload)
            except (requests.RequestException, ValueError):
                pass

        if not payloads:
            try:
                resp = self.session.get(url, timeout=20)
                if resp.status_code == 200:
                    match = NEXT_DATA_PATTERN.search(resp.text)
                    if match:
                        payloads.append(json.loads(match.group(1)))
            except (requests.RequestException, ValueError, json.JSONDecodeError):
                return ""

        for payload in payloads:
            product_data = payload.get("pageProps", {}).get("productData")
            if not isinstance(product_data, dict):
                product_data = payload.get("props", {}).get("pageProps", {}).get("productData")
            if isinstance(product_data, dict):
                return str(product_data.get("category") or "").strip()
        return ""

    def _crawl_urls(
        self,
        urls: list[str],
        limit: int | None = None,
        category_slug: str | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        total = len(urls)
        if total == 0:
            return []

        workers = max(1, min(max_workers, HTML_MAX_WORKERS))
        if workers <= 1:
            return self._crawl_urls_sequential(
                urls,
                limit=limit,
                category_slug=category_slug,
                progress_callback=progress_callback,
            )

        products: list[Product] = []
        lock = threading.Lock()
        processed = 0

        def matches(product: Product) -> bool:
            return not category_slug or category_slug in {"", "Tất cả"} or any(
                self.category_slug(cat) == category_slug for cat in product.categories
            )

        def fetch_one(url: str) -> Product | None:
            match = GENERIC_PRODUCT_PATH.search(urlparse(url).path or "")
            if not match:
                return None
            worker = GenericWebCrawler(self.site_url, proxy_url=self._proxy_url)
            worker._build_id = self._build_id
            worker._init_data = self._init_data
            worker._shop_catalog_mode = self._shop_catalog_mode
            worker.session = create_http_session(None)
            worker.session.headers.update(self.session.headers)
            return worker._fetch_product(url, match.group(1))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch_one, url): url for url in urls}
            for future in as_completed(futures):
                processed += 1
                product = future.result()
                if product and matches(product):
                    with lock:
                        if limit is None or len(products) < limit:
                            products.append(product)
                if progress_callback and (
                    processed == total or processed % 5 == 0
                ):
                    progress_callback(processed, total, len(products))
                if limit is not None and len(products) >= limit:
                    for fut in futures:
                        fut.cancel()
                    break

        if progress_callback and total:
            progress_callback(total, total, len(products))
        return products

    def _crawl_urls_sequential(
        self,
        urls: list[str],
        limit: int | None = None,
        category_slug: str | None = None,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        products: list[Product] = []
        total = len(urls)
        for index, url in enumerate(urls):
            if limit is not None and len(products) >= limit:
                break
            match = GENERIC_PRODUCT_PATH.search(urlparse(url).path or "")
            if match:
                product = self._fetch_product(url, match.group(1))
                if product and (
                    not category_slug
                    or category_slug in {"", "Tất cả"}
                    or any(
                        self.category_slug(cat) == category_slug
                        for cat in product.categories
                    )
                ):
                    products.append(product)
                if self.request_delay and index + 1 < total:
                    time.sleep(self.request_delay)
            if progress_callback and (index + 1 == total or (index + 1) % 5 == 0):
                progress_callback(index + 1, total, len(products))
        if progress_callback and total:
            progress_callback(total, total, len(products))
        return products

    def _fetch_product(self, url: str, product_id: str) -> Product | None:
        product_data: dict[str, Any] | None = None
        html = ""

        build_id = self._get_build_id()
        if build_id:
            api_url = f"{self.site_url}/_next/data/{build_id}/product/{product_id}.json"
            try:
                resp = self.session.get(api_url, timeout=25)
                if resp.status_code == 200:
                    payload = resp.json()
                    candidate = (
                        payload.get("pageProps", {}).get("productData")
                        if isinstance(payload, dict)
                        else None
                    )
                    if isinstance(candidate, dict):
                        product_data = candidate
            except (requests.RequestException, ValueError):
                pass

        if product_data is None:
            try:
                resp = self.session.get(url, timeout=25)
                if resp.status_code != 200:
                    return None
                html = resp.text
                match = NEXT_DATA_PATTERN.search(html)
                if not match:
                    return None
                payload = json.loads(match.group(1))
                candidate = payload.get("props", {}).get("pageProps", {}).get("productData")
                if isinstance(candidate, dict):
                    product_data = candidate
            except (requests.RequestException, ValueError, json.JSONDecodeError):
                return None

        if not isinstance(product_data, dict):
            return None

        if not html:
            try:
                resp = self.session.get(url, timeout=25)
                if resp.status_code == 200:
                    html = resp.text
            except requests.RequestException:
                pass

        return self._product_from_payload(product_data, url=url, html=html)

    @staticmethod
    def _normalize_stock_status(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return "instock" if value else "out_of_stock"
        if isinstance(value, (int, float)):
            return "instock" if float(value) > 0 else "out_of_stock"
        text = str(value).strip().lower()
        if not text:
            return None
        if text.startswith("http"):
            text = text.rsplit("/", 1)[-1]
        normalized = text.replace("-", "").replace("_", "").replace(" ", "")
        if normalized in {"outofstock", "soldout", "discontinued", "unavailable"}:
            return "out_of_stock"
        if normalized in {
            "instock",
            "available",
            "active",
            "publish",
            "published",
            "onlineonly",
            "limitedavailability",
            "onbackorder",
            "preorder",
            "presale",
        }:
            return "instock"
        return None

    def _stock_status_from_html(self, html: str) -> str | None:
        if not html:
            return None
        for match in LD_JSON_PATTERN.finditer(html):
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if not isinstance(item, dict):
                    continue
                offers = item.get("offers")
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    status = self._normalize_stock_status(offers.get("availability"))
                    if status:
                        return status
        if OUT_OF_STOCK_HTML.search(html):
            return "out_of_stock"
        return None

    def _resolve_stock_status(self, data: dict[str, Any], html: str = "") -> str | None:
        for key in (
            "stock_status",
            "stockStatus",
            "availability",
            "available",
            "inStock",
            "in_stock",
            "isAvailable",
            "is_available",
        ):
            if key in data and data.get(key) is not None:
                status = self._normalize_stock_status(data.get(key))
                if status:
                    return status
        if data.get("isSoldOut") or data.get("soldOut"):
            return "out_of_stock"

        for key in ("stock", "inventory", "inventory_quantity", "quantity", "qty"):
            if key in data and data.get(key) is not None:
                status = self._normalize_stock_status(data.get(key))
                if status:
                    return status

        variants = data.get("variants")
        if isinstance(variants, list) and variants:
            any_available = False
            has_signal = False
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                if variant.get("available") is not None:
                    has_signal = True
                    any_available = any_available or bool(variant.get("available"))
                    continue
                status = self._normalize_stock_status(
                    variant.get("stock_status")
                    or variant.get("stockStatus")
                    or variant.get("in_stock")
                    or variant.get("inStock")
                )
                if status:
                    has_signal = True
                    any_available = any_available or status == "instock"
            if has_signal:
                return "instock" if any_available else "out_of_stock"

        return self._stock_status_from_html(html)

    def _map_variants_from_payload(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        variants = data.get("variants")
        if not isinstance(variants, list):
            return []
        mapped: list[dict[str, Any]] = []
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            available = variant.get("available")
            if available is None:
                stock = self._normalize_stock_status(
                    variant.get("stock_status")
                    or variant.get("stockStatus")
                    or variant.get("in_stock")
                    or variant.get("inStock")
                )
                if stock == "instock":
                    available = True
                elif stock == "out_of_stock":
                    available = False
            mapped.append(
                {
                    "id": variant.get("id"),
                    "title": variant.get("title") or variant.get("name"),
                    "sku": variant.get("sku"),
                    "price": variant.get("price"),
                    "available": available,
                    "stock_status": variant.get("stock_status") or variant.get("stockStatus"),
                    "option1": variant.get("option1"),
                    "option2": variant.get("option2"),
                    "option3": variant.get("option3"),
                }
            )
        return mapped

    def _product_from_payload(
        self,
        data: dict[str, Any],
        url: str,
        *,
        html: str = "",
    ) -> Product | None:
        title = str(data.get("name") or data.get("title") or "").strip()
        if not title:
            return None

        product_id = str(data.get("id") or data.get("_id") or "").strip()
        if not product_id:
            product_id = urlparse(url).path.rstrip("/").split("/")[-1]

        images = self._normalize_images(data.get("image") or data.get("images"))
        price_raw = data.get("price") or data.get("salePrice") or data.get("regularPrice")
        price = self._parse_price(price_raw)

        category = str(data.get("category") or "").strip()
        tags = self._normalize_tags(data.get("tag") or data.get("tags"))
        description = data.get("description")
        if description is not None:
            description = str(description).strip() or None

        variants = self._map_variants_from_payload(data)
        status = self._resolve_stock_status(data, html)

        return Product(
            source=self.source,
            id=product_id,
            title=title,
            description=description,
            price=price,
            url=url,
            image_urls=images,
            vendor=None,
            sku=product_id,
            tags=tags,
            categories=[category] if category else [],
            product_type=str(data.get("template") or "").strip() or None,
            status=status,
            variants=variants,
        )

    @staticmethod
    def _parse_price(value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return round(float(value), 2)
        text = str(value).strip().replace(",", "")
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if not match:
            return None
        try:
            return round(float(match.group(1)), 2)
        except ValueError:
            return None

    @staticmethod
    def _normalize_images(value: object) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        urls: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                urls.append(item.strip())
            elif isinstance(item, dict):
                for key in ("url", "src", "image", "href"):
                    text = str(item.get(key) or "").strip()
                    if text:
                        urls.append(text)
                        break
        return urls

    @staticmethod
    def _normalize_tags(value: object) -> list[str]:
        if not value:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        text = str(value).strip()
        if not text:
            return []
        if "," in text:
            return [part.strip() for part in text.split(",") if part.strip()]
        return [text]

    def _uses_shop_catalog(self) -> bool:
        if self._shop_catalog_mode is not None:
            return self._shop_catalog_mode
        if self._count_sitemap_product_urls() > 0:
            self._shop_catalog_mode = False
            return False
        init = self._get_init_data()
        if str(init.get("template") or "").lower() == "datashield":
            self._shop_catalog_mode = True
            return True
        try:
            resp = self.session.get(f"{self.site_url}/shop", timeout=20)
            if resp.status_code == 200 and "initialShopData" in resp.text:
                self._shop_catalog_mode = True
                return True
        except requests.RequestException:
            pass
        self._shop_catalog_mode = False
        return False

    def _uses_html_catalog(self) -> bool:
        if self._html_catalog_mode is not None:
            return self._html_catalog_mode
        if self._shop_catalog_mode is True:
            self._html_catalog_mode = False
            return False
        if self._count_sitemap_product_urls() > 0:
            self._html_catalog_mode = False
            return False
        from crawlers.generic_html import GenericHtmlCatalog

        self._html_catalog_mode = GenericHtmlCatalog.site_supports(
            self.site_url,
            session=self.session,
        )
        return self._html_catalog_mode

    def _count_sitemap_product_urls(self) -> int:
        total = 0
        for sitemap_url in self._collect_product_sitemaps():
            for loc in self._parse_sitemap_locs(sitemap_url):
                if GENERIC_PRODUCT_PATH.search(urlparse(loc).path or ""):
                    total += 1
        return total

    def _get_page_props(self, url: str | None = None) -> dict[str, Any]:
        target = url or self.site_url
        try:
            resp = self.session.get(target, timeout=25)
            if resp.status_code != 200:
                return {}
            match = NEXT_DATA_PATTERN.search(resp.text)
            if not match:
                return {}
            payload = json.loads(match.group(1))
            page_props = payload.get("props", {}).get("pageProps")
            return page_props if isinstance(page_props, dict) else {}
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            return {}

    def _get_init_data(self) -> dict[str, Any]:
        if self._init_data is None:
            self._init_data = self._get_page_props().get("initData") or {}
            if not isinstance(self._init_data, dict):
                self._init_data = {}
        return self._init_data

    def _get_domain(self) -> str:
        host = urlparse(self.site_url).netloc.lower()
        return host.removeprefix("www.")

    def _get_template(self) -> str:
        return str(self._get_init_data().get("template") or "datashield").strip() or "datashield"

    def _get_backend_url(self) -> str:
        endpoint = str(self._get_init_data().get("endpoint") or "").strip().rstrip("/")
        return endpoint or DATASHIELD_BACKEND_DEFAULT

    def _fetch_shop_catalog_page(
        self,
        *,
        category: str | None,
        page: int,
        limit: int = SHOP_CATALOG_PAGE_SIZE,
        include_categories: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "page": page,
            "limit": limit,
            "template": self._get_template(),
            "domain": self._get_domain(),
        }
        if category:
            params["category"] = category
        if include_categories:
            params["include_categories"] = "true"
        resp = self.session.get(
            f"{self._get_backend_url()}/shop",
            params=params,
            timeout=45,
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            return payload["data"]
        return payload if isinstance(payload, dict) else {}

    def _resolve_category_name(self, category_slug: str | None) -> str | None:
        if not category_slug or category_slug in {"", "Tất cả"}:
            return None
        overview = self._fetch_shop_catalog_page(
            category=None,
            page=1,
            limit=SHOP_CATALOG_PAGE_SIZE,
            include_categories=True,
        )
        for name in overview.get("categories") or []:
            if self.category_slug(str(name)) == category_slug:
                return str(name)
        return None

    def _probe_shop_categories(
        self,
        *,
        max_workers: int = 12,
    ) -> tuple[list[dict[str, Any]], str]:
        overview = self._fetch_shop_catalog_page(
            category=None,
            page=1,
            limit=1,
            include_categories=True,
        )
        names = [str(name) for name in (overview.get("categories") or []) if str(name).strip()]
        workers = max(1, min(max_workers, 48))

        def count_category(name: str) -> tuple[str, int]:
            worker = GenericWebCrawler(self.site_url, proxy_url=self._proxy_url, request_delay=0)
            cat_data = worker._fetch_shop_catalog_page(category=name, page=1, limit=1)
            return name, int(cat_data.get("total") or 0)

        if workers <= 1 or len(names) <= 1:
            counted = [count_category(name) for name in names]
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                counted = list(executor.map(count_category, names))

        categories: list[dict[str, Any]] = []
        total = 0
        for name, count in counted:
            total += count
            categories.append(
                {"slug": self.category_slug(name), "name": name, "count": count}
            )
        categories.sort(key=lambda item: (-int(item.get("count") or 0), item["name"].lower()))
        note = f"Danh mục từ **/shop/** API ({len(categories)} loại · **{total or overview.get('total') or 0}** SP)."
        return categories, note

    def _product_from_shop_item(self, data: dict[str, Any]) -> Product | None:
        product_id = str(data.get("id") or "").strip()
        title = str(data.get("name") or data.get("title") or "").strip()
        if not product_id or not title:
            return None
        category = str(data.get("category") or "").strip()
        return Product(
            source=self.source,
            id=product_id,
            title=title,
            price=self._parse_price(data.get("price")),
            url=f"{self.site_url}/product/{product_id}",
            image_urls=self._normalize_images(data.get("image") or data.get("images")),
            vendor=None,
            sku=product_id,
            categories=[category] if category else [],
            status=self._resolve_stock_status(data),
        )

    def _enrich_product_detail(self, product: Product) -> Product:
        detailed = self._fetch_product(product.url, product.id)
        if not detailed:
            return product
        if detailed.description:
            product.description = detailed.description
        if detailed.tags:
            product.tags = detailed.tags
        if detailed.image_urls:
            product.image_urls = detailed.image_urls
        if detailed.price is not None:
            product.price = detailed.price
        if detailed.categories:
            product.categories = detailed.categories
        if detailed.status:
            product.status = detailed.status
        if detailed.variants:
            product.variants = detailed.variants
        return product

    def _crawl_shop_catalog(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        category_slug: str | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        category_name = self._resolve_category_name(category_slug)
        if category_slug and category_slug not in {"", "Tất cả"} and not category_name:
            raise ValueError(f"Không tìm thấy danh mục: {category_slug}")

        if category_name:
            categories = [category_name]
        else:
            overview = self._fetch_shop_catalog_page(
                category=None,
                page=1,
                limit=SHOP_CATALOG_PAGE_SIZE,
                include_categories=True,
            )
            categories = [str(name) for name in (overview.get("categories") or []) if str(name).strip()]

        products: list[Product] = []
        estimated_total = 0
        for name in categories:
            first_page = self._fetch_shop_catalog_page(category=name, page=1, limit=1)
            estimated_total += int(first_page.get("total") or 0)
        processed = 0

        for name in categories:
            page = 1
            total_pages = 1
            while page <= total_pages:
                if max_pages and page > max_pages:
                    break
                data = self._fetch_shop_catalog_page(
                    category=name,
                    page=page,
                    limit=SHOP_CATALOG_PAGE_SIZE,
                )
                total_pages = int(data.get("totalPages") or 1)
                batch = data.get("products") or []
                if not batch:
                    break
                for item in batch:
                    if limit is not None and len(products) >= limit:
                        break
                    if not isinstance(item, dict):
                        continue
                    product = self._product_from_shop_item(item)
                    if not product:
                        continue
                    product = self._enrich_product_detail(product)
                    products.append(product)
                    processed += 1
                    if progress_callback and (
                        processed == estimated_total
                        or processed % 5 == 0
                    ):
                        progress_callback(processed, max(estimated_total, processed), len(products))
                    if self.request_delay:
                        time.sleep(self.request_delay)
                if limit is not None and len(products) >= limit:
                    break
                page += 1
            if limit is not None and len(products) >= limit:
                break

        if progress_callback and estimated_total:
            progress_callback(estimated_total, estimated_total, len(products))
        return products

    def _get_build_id(self) -> str | None:
        if self._build_id:
            return self._build_id
        try:
            resp = self.session.get(self.site_url, timeout=20)
            if resp.status_code != 200:
                return None
            match = NEXT_DATA_PATTERN.search(resp.text)
            if not match:
                return None
            payload = json.loads(match.group(1))
            build_id = str(payload.get("buildId") or "").strip()
            if build_id:
                self._build_id = build_id
            return build_id or None
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            return None

    def _discover_product_urls(self, max_sitemaps: int | None = None) -> list[str]:
        sitemap_urls = self._collect_product_sitemaps()
        if max_sitemaps and max_sitemaps > 0:
            sitemap_urls = sitemap_urls[:max_sitemaps]

        product_urls: list[str] = []
        seen: set[str] = set()
        for sitemap_url in sitemap_urls:
            for loc in self._parse_sitemap_locs(sitemap_url):
                if GENERIC_PRODUCT_PATH.search(urlparse(loc).path or ""):
                    if loc not in seen:
                        seen.add(loc)
                        product_urls.append(loc)
            if self.request_delay:
                time.sleep(self.request_delay)
        return product_urls

    def _collect_product_sitemaps(self) -> list[str]:
        candidates = [
            f"{self.site_url}/sitemap-products.xml",
            f"{self.site_url}/sitemap.xml",
        ]
        found: list[str] = []
        seen: set[str] = set()

        for candidate in candidates:
            locs = self._parse_sitemap_locs(candidate)
            if not locs:
                continue

            if any(
                GENERIC_PRODUCT_PATH.search(urlparse(loc).path or "")
                for loc in locs
            ):
                return [candidate]

            for loc in locs:
                lower = loc.lower()
                if "sitemap-product" in lower and loc not in seen:
                    seen.add(loc)
                    found.append(loc)
            if found:
                return found
        return found

    def _parse_sitemap_locs(self, sitemap_url: str) -> list[str]:
        try:
            resp = self.session.get(sitemap_url, timeout=25)
            if resp.status_code != 200 or not resp.text.strip():
                return []
            root = ET.fromstring(resp.text)
        except (requests.RequestException, ET.ParseError):
            return []

        locs: list[str] = []
        for el in root.findall(".//sm:loc", SITEMAP_NS):
            text = (el.text or "").strip()
            if text:
                locs.append(text)
        if locs:
            return locs

        # fallback without namespace
        for el in root.iter():
            if el.tag.endswith("loc") and el.text:
                locs.append(el.text.strip())
        return locs

    @classmethod
    def site_supports(cls, base_url: str, session: requests.Session | None = None) -> bool:
        session = session or requests.Session()
        session.headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
        base = cls._normalize_site_url(base_url)

        for path in ("/sitemap-products.xml", "/sitemap.xml"):
            try:
                resp = session.get(base + path, timeout=15)
                if resp.status_code != 200:
                    continue
                text = resp.text.lower()
                if "sitemap-product" in text or "/product/" in text:
                    return True
            except requests.RequestException:
                continue

        try:
            resp = session.get(base, timeout=15)
            if resp.status_code != 200:
                return False
            if "__NEXT_DATA__" not in resp.text:
                return False
            match = NEXT_DATA_PATTERN.search(resp.text)
            if match:
                payload = json.loads(match.group(1))
                init = payload.get("props", {}).get("pageProps", {}).get("initData", {})
                if str(init.get("template") or "").lower() == "datashield":
                    return True
            sample = session.get(f"{base}/products", timeout=15)
            if sample.status_code == 200 and (
                "/product/" in sample.text or "productData" in sample.text
            ):
                return True
            shop = session.get(f"{base}/shop", timeout=15)
            if shop.status_code == 200 and "initialShopData" in shop.text:
                return True
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            pass

        try:
            from crawlers.generic_html import GenericHtmlCatalog

            return GenericHtmlCatalog.site_supports(base, session=session)
        except Exception:
            return False
