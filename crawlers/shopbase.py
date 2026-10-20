"""Crawl ShopBase storefront — catalog API + /collections/ (vd. wc.luminaaura.us)."""

from __future__ import annotations

import html as html_lib
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse

import requests

from crawlers.base import BaseCrawler
from models.product import Product
from utils.description_html import (
    description_from_json_ld,
    extract_generic_description_from_html,
    shopbase_description_from_item,
)
from utils.http_session import create_http_session

CrawlProgressCallback = Callable[[int, int, int], None]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
CATALOG_LIST_API = "/api/catalog/next/products.json"
CATALOG_DETAIL_API = "/api/catalog/next/product.json"
CATALOG_COLLECTIONS_API = "/api/catalog/next/collections.json"
CATALOG_PAGE_SIZE = 50
# ShopBase catalog dễ rơi trang khi >8 request cùng lúc (số SP mỗi lần khác nhau).
CATALOG_LIST_MAX_WORKERS = 8
# Trang 200 trả "Max result reached" — mỗi bộ lọc chỉ đọc được 199 trang.
CATALOG_MAX_PAGES = 199
CATALOG_MAX_ITEMS = CATALOG_PAGE_SIZE * CATALOG_MAX_PAGES
# Count >= mức này nghĩa là danh sách dài hơn một cửa sổ, không phải số riêng của một shop.
CATALOG_COUNT_CAP = 10000
_SEARCH_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"
DETAIL_FETCH_MAX_WORKERS = 500

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
PRODUCT_PATH = re.compile(r"/products/([^/?#]+)", re.I)
COLLECTION_PATH = re.compile(r"/collections/([^/?#]+)", re.I)
LD_JSON_PATTERN = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.I,
)
PRODUCT_IMAGE_RE = re.compile(
    r"https://img\.(?:thesitebase|shopbase)\.net/[^\"'\s<>]+/products/[^\"'\s<>]+\.(?:jpg|jpeg|png|webp)",
    re.I,
)
SHOP_NAME_RE = re.compile(
    r'"shop"\s*:\s*\{\s*"id"\s*:\s*\d+\s*,\s*"name"\s*:\s*"([^"]+)"',
    re.I,
)


class ShopBaseCrawler(BaseCrawler):
    source = "shopbase"

    def __init__(
        self,
        site_url: str,
        *,
        proxy_url: str | None = None,
        request_delay: float = 0.0,
    ):
        self.site_url = self._normalize_site_url(site_url)
        self.request_delay = request_delay
        self._proxy_url = proxy_url
        self.session = create_http_session(proxy_url)
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        # Catalog API thường chặn/không ổn định qua proxy — luôn gọi trực tiếp.
        self._catalog_session = create_http_session(None)
        self._catalog_session.headers["User-Agent"] = DEFAULT_USER_AGENT
        self._shop_name_cache: str | None = None
        self._catalog_supported: bool | None = None
        self.crawl_fields = None
        self.partial_callback: Callable[[list[Product]], None] | None = None
        self.last_products: list[Product] = []
        self.progress_phase = "list"

    def _emit_partial(self, products: list[Product]) -> None:
        self.last_products = list(products)
        if self.partial_callback is not None and products:
            self.partial_callback(self.last_products)

    def _wants_description(self) -> bool:
        fields = self.crawl_fields
        if fields is None:
            return True
        return fields.wants_description()

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
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        return self._crawl_catalog(
            limit=limit,
            max_pages=max_pages,
            progress_callback=progress_callback,
            max_workers=max_workers,
        )

    def crawl_collection(
        self,
        handle: str,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        handle = handle.strip().strip("/")
        if handle.lower() in {"", "all", "all-products"}:
            return self.crawl(
                limit=limit,
                max_pages=max_pages,
                progress_callback=progress_callback,
                max_workers=max_workers,
            )
        meta = self._collection_meta(handle)
        default_category = (
            str(meta.get("title") or handle).replace("-", " ").title()
            if meta
            else handle.replace("-", " ").title()
        )
        return self._crawl_catalog(
            limit=limit,
            max_pages=max_pages,
            collection_handle=handle,
            progress_callback=progress_callback,
            max_workers=max_workers,
            default_category=default_category,
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
        search = query.strip()
        if not search:
            raise ValueError("Thiếu từ khóa tìm kiếm.")
        return self._crawl_catalog(
            limit=limit,
            max_pages=max_pages,
            query=search,
            progress_callback=progress_callback,
            max_workers=max_workers,
            default_category=search,
        )

    def count_search_results(self, query: str) -> int:
        search = query.strip()
        if not search or not self._catalog_api_available():
            return 0
        return self._catalog_reported_count(query=search)

    def crawl_product(self, handle: str) -> Product | None:
        handle = handle.strip().strip("/")
        product = self._product_from_detail_api(handle)
        if product:
            return product
        url = f"{self.site_url}/products/{handle}"
        return self._fetch_product_html(url, handle)

    def count_sitemap_products(
        self,
        max_sitemaps: int | None = None,
        *,
        collection_handle: str | None = None,
    ) -> int:
        if self._catalog_api_available():
            if max_sitemaps and max_sitemaps > 0:
                return min(
                    max_sitemaps * CATALOG_PAGE_SIZE,
                    self._catalog_reported_count(collection_handle),
                )
            reported = self._catalog_reported_count(collection_handle)
            if reported and reported < CATALOG_COUNT_CAP:
                return reported
            if reported >= CATALOG_COUNT_CAP and not collection_handle:
                # Cửa sổ catalog chỉ 9.950 SP. Tổng thật là collection lớn nhất của shop đó.
                return max(reported, self._max_collection_count())
            if reported:
                return reported
            if collection_handle:
                meta = self._collection_meta(collection_handle)
                if meta is not None:
                    try:
                        return int(meta.get("product_count") or 0)
                    except (TypeError, ValueError):
                        return 0
            return len(self._list_catalog_handles(collection_handle=collection_handle))
        return len(self._discover_product_urls(max_sitemaps=max_sitemaps))

    def _max_collection_count(self) -> int:
        categories = self._list_collections_from_api(include_counts=False)
        if not categories:
            return 0
        workers = min(CATALOG_LIST_MAX_WORKERS, len(categories))
        best = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(self._catalog_reported_count, str(item.get("slug") or ""))
                for item in categories
                if item.get("slug")
            ]
            for future in as_completed(futures):
                try:
                    best = max(best, int(future.result() or 0))
                except (TypeError, ValueError):
                    continue
        return best

    def _list_collections_from_api(
        self,
        *,
        include_counts: bool = False,
    ) -> list[dict[str, Any]]:
        if not self._catalog_api_available():
            return []

        categories: list[dict[str, Any]] = []
        page = 1
        while page <= 40:
            try:
                resp = self._catalog_session.get(
                    f"{self.site_url}{CATALOG_COLLECTIONS_API}",
                    params={"limit": 50, "page": page},
                    timeout=20,
                )
                if resp.status_code != 200:
                    break
                items = (resp.json().get("result") or {}).get("items") or []
                if not items:
                    break
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    handle = str(item.get("handle") or "").strip()
                    if not handle:
                        continue
                    title = str(item.get("title") or "").strip() or handle.replace("-", " ").title()
                    count: int | None = None
                    if include_counts:
                        try:
                            count = int(item.get("product_count") or 0)
                        except (TypeError, ValueError):
                            count = 0
                        if count <= 0:
                            count = self._catalog_reported_count(handle) or None
                    categories.append({"slug": handle, "name": title, "count": count})
                page += 1
            except (requests.RequestException, ValueError):
                break
        return categories

    def probe_categories(
        self,
        sample_size: int = 50,
        *,
        max_workers: int = 12,
        include_counts: bool = False,
    ) -> tuple[list[dict[str, Any]], str]:
        del sample_size, max_workers
        categories = self._list_collections_from_api(include_counts=include_counts)
        catalog_ok = self._catalog_api_available()

        if not categories:
            for loc in self._parse_sitemap_locs(f"{self.site_url}/sitemap_collections_1.xml"):
                match = COLLECTION_PATH.search(urlparse(loc).path or "")
                if not match:
                    continue
                handle = match.group(1)
                count = (
                    self._catalog_reported_count(handle)
                    if include_counts and catalog_ok
                    else None
                )
                categories.append(
                    {
                        "slug": handle,
                        "name": handle.replace("-", " ").title(),
                        "count": count,
                    }
                )

            if not categories:
                for loc in self._parse_sitemap_locs(f"{self.site_url}/sitemap.xml"):
                    if "sitemap_collections" not in loc.lower():
                        continue
                    for col_loc in self._parse_sitemap_locs(loc):
                        match = COLLECTION_PATH.search(urlparse(col_loc).path or "")
                        if match:
                            handle = match.group(1)
                            categories.append(
                                {
                                    "slug": handle,
                                    "name": handle.replace("-", " ").title(),
                                    "count": self._catalog_reported_count(handle)
                                    if include_counts and catalog_ok
                                    else None,
                                }
                            )

        categories.sort(key=lambda item: str(item.get("name") or "").lower())
        note = (
            f"Danh mục ShopBase ({len(categories)} collection) — "
            "crawl qua API catalog (~50 SP/trang)."
        )
        return categories, note

    @classmethod
    def site_supports(cls, base_url: str, session: requests.Session | None = None) -> bool:
        session = session or requests.Session()
        session.headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
        base = cls._normalize_site_url(base_url)
        try:
            products_json = session.get(
                f"{base}/products.json",
                params={"limit": 1},
                timeout=10,
            )
            if products_json.status_code == 200:
                return False
        except requests.RequestException:
            pass

        try:
            catalog = session.get(
                f"{base}{CATALOG_LIST_API}",
                params={"limit": 1, "page": 1},
                timeout=12,
            )
            if catalog.status_code == 200:
                payload = catalog.json()
                if isinstance(payload.get("result"), dict):
                    return True
        except (requests.RequestException, ValueError):
            pass

        try:
            # Shopify cũng dùng sitemap_products_*.xml — không dùng làm tín hiệu ShopBase.
            meta = session.get(f"{base}/meta.json", timeout=10)
            if meta.status_code == 200:
                payload = meta.json()
                if isinstance(payload, dict) and payload.get("myshopify_domain"):
                    return False
        except (requests.RequestException, ValueError):
            pass

        try:
            page = session.get(base, timeout=12)
            if page.status_code == 200 and "shopbase" in page.text.lower():
                return True
        except requests.RequestException:
            pass
        return False

    def _catalog_api_available(self) -> bool:
        if self._catalog_supported is not None:
            return self._catalog_supported
        try:
            resp = self._catalog_session.get(
                f"{self.site_url}{CATALOG_LIST_API}",
                params={"limit": 1, "page": 1},
                timeout=15,
            )
            if resp.status_code != 200:
                self._catalog_supported = False
                return False
            payload = resp.json()
            self._catalog_supported = isinstance(payload.get("result"), dict)
        except (requests.RequestException, ValueError):
            self._catalog_supported = False
        return bool(self._catalog_supported)

    def _fetch_catalog_page(
        self,
        page: int,
        *,
        collection_handle: str | None = None,
        query: str | None = None,
        session: requests.Session | None = None,
        sort_field: str | None = None,
        sort_direction: str | None = None,
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {
            "limit": CATALOG_PAGE_SIZE,
            "page": max(1, page),
        }
        if collection_handle:
            params["collection_handle"] = collection_handle
        search = (query or "").strip()
        if search:
            params["q"] = search
        if sort_field:
            params["sort_field"] = sort_field
            params["sort_direction"] = sort_direction or "asc"
        http = session or self._catalog_session
        try:
            resp = http.get(
                f"{self.site_url}{CATALOG_LIST_API}",
                params=params,
                timeout=25,
            )
        except requests.RequestException:
            return None
        if resp.status_code == 400:
            # Quá trang cuối của một bộ lọc → {"messages":["Max result reached"]}.
            return {"items": [], "_catalog_end": True}
        try:
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError):
            return None
        result = payload.get("result")
        if isinstance(result, dict):
            return result
        message = " ".join(str(item) for item in (payload.get("messages") or []))
        if "max result" in message.lower():
            return {"items": [], "_catalog_end": True}
        return None

    def _collection_meta(self, handle: str) -> dict[str, Any] | None:
        handle = handle.strip().strip("/")
        if not handle:
            return None
        page = 1
        while page <= 30:
            try:
                resp = self._catalog_session.get(
                    f"{self.site_url}{CATALOG_COLLECTIONS_API}",
                    params={"limit": 50, "page": page},
                    timeout=20,
                )
                if resp.status_code != 200:
                    break
                items = (resp.json().get("result") or {}).get("items") or []
                if not items:
                    break
                for item in items:
                    if isinstance(item, dict) and str(item.get("handle") or "") == handle:
                        return item
                page += 1
            except (requests.RequestException, ValueError):
                break
        return None

    def _catalog_reported_count(
        self,
        collection_handle: str | None = None,
        *,
        query: str | None = None,
    ) -> int:
        result = self._fetch_catalog_page(
            1,
            collection_handle=collection_handle,
            query=query,
        )
        if not result:
            return 0
        count = result.get("count")
        if count is None:
            return len(result.get("items") or [])
        try:
            return int(count)
        except (TypeError, ValueError):
            return 0

    def _catalog_total_pages(
        self,
        collection_handle: str | None = None,
        max_pages: int | None = None,
        *,
        query: str | None = None,
        sort_field: str | None = None,
        sort_direction: str | None = None,
    ) -> tuple[dict[str, Any] | None, int]:
        first = self._fetch_catalog_page(
            1,
            collection_handle=collection_handle,
            query=query,
            sort_field=sort_field,
            sort_direction=sort_direction,
        )
        if not first:
            return None, 0
        items = first.get("items") or []
        if not items:
            return first, 0
        count = first.get("count")
        try:
            total = int(count) if count is not None else len(items)
        except (TypeError, ValueError):
            total = len(items)
        pages = max(1, (total + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)
        if max_pages and max_pages > 0:
            pages = min(pages, max_pages)
        # Trang cuối đôi khi vượt trần API dù count vẫn báo đủ.
        if pages > 1:
            tail = self._fetch_catalog_page(
                pages,
                collection_handle=collection_handle,
                query=query,
                sort_field=sort_field,
                sort_direction=sort_direction,
            )
            if isinstance(tail, dict) and tail.get("_catalog_end"):
                pages -= 1
        return first, pages

    def _list_catalog_items(
        self,
        *,
        collection_handle: str | None = None,
        query: str | None = None,
        max_pages: int | None = None,
        limit: int | None = None,
        max_workers: int = 8,
        progress_callback: CrawlProgressCallback | None = None,
        sort_field: str | None = None,
        sort_direction: str | None = None,
    ) -> list[dict[str, Any]]:
        first, total_pages = self._catalog_total_pages(
            collection_handle,
            max_pages,
            query=query,
            sort_field=sort_field,
            sort_direction=sort_direction,
        )
        if not first:
            return []

        page_items: dict[int, list[dict[str, Any]]] = {
            1: [item for item in (first.get("items") or []) if isinstance(item, dict)],
        }
        self.progress_phase = "pages"
        if progress_callback:
            progress_callback(1, total_pages, len(page_items[1]))

        if total_pages <= 1:
            items = page_items[1]
            return items[:limit] if limit is not None else items

        local = threading.local()

        def session_for_thread() -> requests.Session:
            if not hasattr(local, "session"):
                local.session = create_http_session(None)
                local.session.headers.update(self._catalog_session.headers)
            return local.session

        def fetch_page(page_num: int) -> tuple[int, list[dict[str, Any]], bool]:
            ended = False
            for attempt in range(3):
                result = self._fetch_catalog_page(
                    page_num,
                    collection_handle=collection_handle,
                    query=query,
                    session=session_for_thread(),
                    sort_field=sort_field,
                    sort_direction=sort_direction,
                )
                if isinstance(result, dict) and result.get("_catalog_end"):
                    return page_num, [], True
                items = [
                    item
                    for item in ((result or {}).get("items") or [])
                    if isinstance(item, dict)
                ]
                if items:
                    return page_num, items, False
                if attempt < 2:
                    time.sleep(0.35 * (attempt + 1))
            return page_num, [], ended

        workers = min(
            max(1, max_workers),
            CATALOG_LIST_MAX_WORKERS,
            total_pages - 1,
        )
        pages_done = 1
        ended_pages: set[int] = set()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(fetch_page, page_num)
                for page_num in range(2, total_pages + 1)
            ]
            for future in as_completed(futures):
                page_num, items, ended = future.result()
                if ended:
                    ended_pages.add(page_num)
                    page_items[page_num] = []
                else:
                    page_items[page_num] = items
                pages_done += 1
                if progress_callback:
                    loaded = sum(len(chunk) for chunk in page_items.values())
                    progress_callback(pages_done, total_pages, loaded)

        # Trang rỗng do rate-limit: thử lại, bỏ trang API báo hết kết quả.
        missing = [
            page_num
            for page_num in range(2, total_pages + 1)
            if page_num not in ended_pages and not page_items.get(page_num)
        ]
        for offset in range(0, len(missing), 4):
            wave = missing[offset : offset + 4]
            for page_num in wave:
                result = self._fetch_catalog_page(
                    page_num,
                    collection_handle=collection_handle,
                    query=query,
                    sort_field=sort_field,
                    sort_direction=sort_direction,
                )
                if isinstance(result, dict) and result.get("_catalog_end"):
                    page_items[page_num] = []
                    continue
                page_items[page_num] = [
                    item
                    for item in ((result or {}).get("items") or [])
                    if isinstance(item, dict)
                ]
            if progress_callback:
                loaded = sum(len(chunk) for chunk in page_items.values())
                progress_callback(total_pages, total_pages, loaded)

        all_items: list[dict[str, Any]] = []
        seen_handles: set[str] = set()
        for page_num in range(1, total_pages + 1):
            for item in page_items.get(page_num, []):
                handle = str(item.get("handle") or "").strip()
                if not handle or handle in seen_handles:
                    continue
                seen_handles.add(handle)
                all_items.append(item)
                if limit is not None and len(all_items) >= limit:
                    return all_items
        return all_items

    def _list_catalog_handles(
        self,
        *,
        collection_handle: str | None = None,
        max_pages: int | None = None,
        limit: int | None = None,
        max_workers: int = 8,
    ) -> list[str]:
        return [
            str(item.get("handle") or "").strip()
            for item in self._list_catalog_items(
                collection_handle=collection_handle,
                max_pages=max_pages,
                limit=limit,
                max_workers=max_workers,
            )
            if str(item.get("handle") or "").strip()
        ]

    def _query_counts(self, queries: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        if not queries:
            return counts
        workers = min(CATALOG_LIST_MAX_WORKERS, len(queries))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._catalog_reported_count, query=query): query
                for query in queries
            }
            for future in as_completed(futures):
                query = futures[future]
                try:
                    counts[query] = int(future.result() or 0)
                except (TypeError, ValueError):
                    counts[query] = 0
        return counts

    def _search_leaf_queries(self) -> list[str]:
        """Tách catalog bằng từ khóa đến khi mỗi nhóm vừa một cửa sổ API."""
        pending = list(_SEARCH_CHARS)
        leaves: list[str] = []
        seen: set[str] = set()
        counted = 0
        while pending and counted < 2500 and len(leaves) < 800:
            wave: list[str] = []
            while pending and len(wave) < CATALOG_LIST_MAX_WORKERS:
                query = pending.pop(0)
                if query in seen or len(query) > 3:
                    continue
                seen.add(query)
                wave.append(query)
            if not wave:
                break
            counted += len(wave)
            for query, count in self._query_counts(wave).items():
                if count <= 0:
                    continue
                if count >= CATALOG_COUNT_CAP and len(query) < 3:
                    pending.extend(query + char for char in _SEARCH_CHARS)
                    continue
                leaves.append(query)
        return leaves

    def _merge_search_shards(
        self,
        bucket: dict[str, Product],
        *,
        progress_callback: CrawlProgressCallback | None,
        max_workers: int,
        job_offset: int,
    ) -> None:
        leaves = self._search_leaf_queries()
        total = max(job_offset + len(leaves), 1)
        for index, query in enumerate(leaves, start=1):
            items = self._list_catalog_items(
                query=query,
                max_workers=max_workers,
            )
            for raw in items:
                product = self._map_catalog_item(raw, skip_page_fallback=True)
                if product is None:
                    continue
                existing = bucket.get(product.id)
                if existing is None:
                    bucket[product.id] = product
                    continue
                for category in product.categories:
                    if category and category not in existing.categories:
                        existing.categories.append(category)
            done = job_offset + index
            if progress_callback and (index == len(leaves) or index % 2 == 0):
                progress_callback(done, total, len(bucket))
            if index == len(leaves) or index % 5 == 0:
                self._emit_partial(list(bucket.values()))

    def _crawl_shop_by_collections(
        self,
        *,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 8,
    ) -> list[Product]:
        """Mọi shop ShopBase vượt trần 9.950 SP/lần lọc.

        Đọc từng collection của chính shop đó rồi gộp theo id. Collection lớn hơn
        một cửa sổ được đọc thêm lượt sort id tăng dần. Collection tổng (lớn hơn
        hai cửa sổ) không đọc trực tiếp. Nếu các collection con không phủ nổi tổng,
        tách thêm bằng từ khóa tìm kiếm cho đến khi mỗi nhóm nằm trong cửa sổ API.
        """
        categories = self._list_collections_from_api(include_counts=False)
        counts: dict[str, int] = {}
        if categories:
            workers = min(CATALOG_LIST_MAX_WORKERS, len(categories))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._catalog_reported_count, str(item.get("slug") or "")): item
                    for item in categories
                    if item.get("slug")
                }
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        counts[str(item.get("slug") or "")] = int(future.result() or 0)
                    except (TypeError, ValueError):
                        counts[str(item.get("slug") or "")] = 0

        jobs: list[tuple[str, str, str | None, str | None]] = []
        for item in categories:
            slug = str(item.get("slug") or "").strip()
            name = str(item.get("name") or slug).strip()
            count = counts.get(slug) or 0
            if not slug or count <= 0 or count > CATALOG_MAX_ITEMS * 2:
                continue
            jobs.append((slug, name, None, None))
            if count > CATALOG_MAX_ITEMS:
                jobs.append((slug, name, "id", "asc"))

        bucket: dict[str, Product] = {}
        total_jobs = max(len(jobs), 1)
        self.progress_phase = "list"
        for index, (slug, name, sort_field, sort_direction) in enumerate(jobs, start=1):
            def _page_progress(page: int, pages: int, loaded: int, *, done: int = index) -> None:
                if progress_callback and (page == pages or page % 25 == 0):
                    progress_callback(done, total_jobs, len(bucket) + loaded)

            items = self._list_catalog_items(
                collection_handle=slug,
                max_workers=max_workers,
                progress_callback=_page_progress if progress_callback else None,
                sort_field=sort_field,
                sort_direction=sort_direction,
            )
            for raw in items:
                product = self._map_catalog_item(
                    raw,
                    default_category=name,
                    skip_page_fallback=True,
                )
                if product is None:
                    continue
                existing = bucket.get(product.id)
                if existing is None:
                    bucket[product.id] = product
                    continue
                for category in product.categories:
                    if category and category not in existing.categories:
                        existing.categories.append(category)
            if progress_callback:
                progress_callback(index, total_jobs, len(bucket))
            if index == len(jobs) or index % 2 == 0:
                self._emit_partial(list(bucket.values()))

        giant = max(counts.values(), default=0)
        reachable = sum(
            min(count, CATALOG_MAX_ITEMS * 2)
            for count in counts.values()
            if 0 < count <= CATALOG_MAX_ITEMS * 2
        )
        if not jobs or (giant > CATALOG_MAX_ITEMS * 2 and reachable < int(giant * 0.75)):
            self._merge_search_shards(
                bucket,
                progress_callback=progress_callback,
                max_workers=max_workers,
                job_offset=len(jobs),
            )

        products = list(bucket.values())
        self._emit_partial(products)
        if self._wants_description() and products:
            self.progress_phase = "description"
            self._enrich_descriptions_parallel(
                products,
                max_workers=max_workers,
                progress_callback=progress_callback,
            )
            self._emit_partial(products)
        if progress_callback and products:
            progress_callback(total_jobs, total_jobs, len(products))
        return products

    def _crawl_catalog(
        self,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        collection_handle: str | None = None,
        query: str | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
        default_category: str = "",
    ) -> list[Product]:
        if self._catalog_api_available():
            self._shop_name()
            if (
                not collection_handle
                and not (query or "").strip()
                and limit is None
                and not max_pages
                and self._catalog_reported_count() >= CATALOG_COUNT_CAP
            ):
                sharded = self._crawl_shop_by_collections(
                    progress_callback=progress_callback,
                    max_workers=max_workers,
                )
                if sharded:
                    return sharded
            items = self._list_catalog_items(
                collection_handle=collection_handle,
                query=query,
                max_pages=max_pages,
                limit=limit,
                max_workers=max_workers,
                progress_callback=progress_callback,
            )
            products: list[Product] = []
            total = len(items)
            map_total = max(total, 1)
            self.progress_phase = "list"
            for index, item in enumerate(items):
                product = self._map_catalog_item(
                    item,
                    default_category=default_category,
                    skip_page_fallback=True,
                )
                if product:
                    products.append(product)
                processed = index + 1
                if progress_callback and (
                    processed == total or processed % 50 == 0
                ):
                    progress_callback(processed, map_total, len(products))
                if processed == total or processed % 400 == 0:
                    self._emit_partial(products)
            self._emit_partial(products)
            if self._wants_description():
                self.progress_phase = "description"
                self._enrich_descriptions_parallel(
                    products,
                    max_workers=max_workers,
                    progress_callback=progress_callback,
                )
                self._emit_partial(products)
            if progress_callback and total:
                progress_callback(total, total, len(products))
            return products

        if (query or "").strip():
            return []

        urls = self._discover_product_urls(max_sitemaps=max_pages)
        if limit is not None:
            urls = urls[:limit]
        return self._crawl_product_urls_html(
            urls,
            progress_callback=progress_callback,
            max_workers=max_workers,
            default_category=default_category,
        )

    def _crawl_handles(
        self,
        handles: list[str],
        *,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
        default_category: str = "",
    ) -> list[Product]:
        if not handles:
            return []

        products: list[Product] = []
        lock = threading.Lock()
        total = len(handles)
        workers = max(1, min(max_workers, 500))
        processed = 0

        def fetch_one(handle: str) -> Product | None:
            return self._product_from_detail_api(handle, default_category=default_category)

        if workers <= 1:
            for index, handle in enumerate(handles):
                product = fetch_one(handle)
                if product:
                    products.append(product)
                processed = index + 1
                if progress_callback and (processed == total or processed % 5 == 0):
                    progress_callback(processed, total, len(products))
                if self.request_delay:
                    time.sleep(self.request_delay)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(fetch_one, handle): handle for handle in handles}
                for future in as_completed(futures):
                    processed += 1
                    product = future.result()
                    if product:
                        with lock:
                            products.append(product)
                    if progress_callback and (
                        processed == total or processed % 10 == 0
                    ):
                        progress_callback(processed, total, len(products))

        if progress_callback and total:
            progress_callback(total, total, len(products))
        return products

    def _fetch_product_detail(self, handle: str) -> dict[str, Any] | None:
        try:
            resp = self._catalog_session.get(
                f"{self.site_url}{CATALOG_DETAIL_API}",
                params={"handle": handle},
                timeout=30,
            )
            if resp.status_code != 200:
                return None
            payload = resp.json()
            result = payload.get("result")
            return result if isinstance(result, dict) else None
        except (requests.RequestException, ValueError):
            return None

    def _product_from_detail_api(
        self,
        handle: str,
        *,
        default_category: str = "",
    ) -> Product | None:
        data = self._fetch_product_detail(handle)
        if not data:
            url = f"{self.site_url}/products/{handle}"
            return self._fetch_product_html(url, handle, default_category=default_category)
        return self._map_detail_product(data, default_category=default_category)

    def _map_catalog_item(
        self,
        data: dict[str, Any],
        *,
        default_category: str = "",
        skip_page_fallback: bool = False,
    ) -> Product | None:
        return self._map_detail_product(
            data,
            default_category=default_category,
            skip_page_fallback=skip_page_fallback,
        )

    def _enrich_descriptions_parallel(
        self,
        products: list[Product],
        *,
        max_workers: int = 8,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> None:
        """Lấy mô tả qua detail API song song — tránh mở từng trang HTML (rất chậm)."""
        pending = [p for p in products if not (p.description or "").strip()]
        if not pending:
            return

        total = len(pending)
        workers = min(max(1, max_workers), DETAIL_FETCH_MAX_WORKERS, total)
        local = threading.local()
        done = 0
        lock = threading.Lock()

        def session_for_thread() -> requests.Session:
            if not hasattr(local, "session"):
                local.session = create_http_session(None)
                local.session.headers.update(self._catalog_session.headers)
            return local.session

        def fetch_description(product: Product) -> tuple[Product, str | None]:
            handle = str(product.url or "").rstrip("/").split("/")[-1]
            if not handle:
                handle = str(product.id or "")
            for attempt in range(3):
                try:
                    resp = session_for_thread().get(
                        f"{self.site_url}{CATALOG_DETAIL_API}",
                        params={"handle": handle},
                        timeout=25,
                    )
                    if resp.status_code != 200:
                        continue
                    data = resp.json().get("result")
                    if isinstance(data, dict):
                        desc = shopbase_description_from_item(data)
                        if desc:
                            return product, desc
                except (requests.RequestException, ValueError):
                    pass
                if attempt < 2:
                    time.sleep(0.25 * (attempt + 1))
            return product, None

        if workers <= 1:
            for index, product in enumerate(pending):
                _, desc = fetch_description(product)
                if desc:
                    product.description = desc
                if progress_callback and (
                    index + 1 == total or (index + 1) % 25 == 0
                ):
                    progress_callback(index + 1, total, len(products))
            return

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch_description, p) for p in pending]
            for future in as_completed(futures):
                product, desc = future.result()
                if desc:
                    product.description = desc
                with lock:
                    done += 1
                    if progress_callback and (
                        done == total or done % 25 == 0
                    ):
                        progress_callback(done, total, len(products))

    def _resolve_shopbase_description(
        self,
        data: dict[str, Any],
        handle: str,
        *,
        skip_page_fallback: bool = False,
    ) -> str | None:
        description = shopbase_description_from_item(data)
        if description or skip_page_fallback:
            return description
        url = f"{self.site_url}/products/{handle}"
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200:
                return extract_generic_description_from_html(resp.text)
        except requests.RequestException:
            pass
        return None

    def _map_detail_product(
        self,
        data: dict[str, Any],
        *,
        default_category: str = "",
        skip_page_fallback: bool = False,
    ) -> Product | None:
        handle = str(data.get("handle") or "").strip()
        title = html_lib.unescape(str(data.get("title") or "").strip())
        if not handle or not title:
            return None

        if self._wants_description():
            description = self._resolve_shopbase_description(
                data,
                handle,
                skip_page_fallback=skip_page_fallback,
            )
        else:
            description = None

        price = self._parse_price(data.get("price") or data.get("min_price"))
        if price is None and data.get("variants"):
            first = data["variants"][0]
            if isinstance(first, dict):
                price = self._parse_price(first.get("price"))

        images = self._images_from_api(data)
        tags = [str(tag).strip() for tag in (data.get("tags") or []) if str(tag).strip()]
        categories: list[str] = []
        if default_category:
            categories.append(default_category)
        for collection in data.get("collections") or []:
            if isinstance(collection, dict):
                name = str(collection.get("title") or collection.get("name") or "").strip()
                if name and name not in categories:
                    categories.append(name)

        status = "instock" if data.get("available", True) else "out_of_stock"
        variants = self._map_variants(data)

        product_id = str(data.get("id") or handle)
        sku = str(data.get("sku") or product_id).strip()
        if variants and variants[0].get("sku"):
            sku = str(variants[0]["sku"])

        return Product(
            source=self.source,
            id=product_id,
            title=title,
            description=description,
            price=price,
            currency="USD",
            url=f"{self.site_url}/products/{handle}",
            image_urls=images,
            vendor=self._shop_name(),
            sku=sku,
            tags=tags,
            categories=categories,
            product_type=str(data.get("product_type") or "").strip() or None,
            status=status,
            variants=variants,
        )

    def _map_variants(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        mapped: list[dict[str, Any]] = []
        product_available = data.get("available", True)
        for variant in data.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            available = (
                variant.get("available")
                if variant.get("available") is not None
                else product_available
            )
            image_id = variant.get("image_id")
            image_url = None
            for image in data.get("images") or []:
                if isinstance(image, dict) and image.get("id") == image_id:
                    image_url = str(image.get("src") or "").strip() or None
                    break
            mapped.append(
                {
                    "id": variant.get("id"),
                    "title": variant.get("title"),
                    "sku": variant.get("sku"),
                    "price": variant.get("price"),
                    "available": available,
                    "barcode": variant.get("barcode"),
                    "image_url": image_url,
                }
            )
        return mapped

    @staticmethod
    def _images_from_api(data: dict[str, Any]) -> list[str]:
        images: list[str] = []
        seen: set[str] = set()
        for key in ("images", "thumbnails"):
            for item in data.get(key) or []:
                if isinstance(item, str):
                    src = item.strip()
                elif isinstance(item, dict):
                    src = str(item.get("src") or item.get("url") or "").strip()
                else:
                    src = ""
                if src and src not in seen:
                    seen.add(src)
                    images.append(src)
        main = data.get("image")
        if isinstance(main, str) and main.strip() and main not in seen:
            images.insert(0, main.strip())
        return images[:12]

    def _shop_name(self) -> str | None:
        if self._shop_name_cache:
            return self._shop_name_cache
        try:
            resp = self.session.get(self.site_url, timeout=20)
            if resp.status_code == 200:
                match = SHOP_NAME_RE.search(resp.text)
                if match:
                    name = match.group(1).strip()
                    if name.lower() not in {"us dollar", "usd", "shop"}:
                        self._shop_name_cache = name
                        return self._shop_name_cache
        except requests.RequestException:
            pass
        return None

    def _crawl_product_urls_html(
        self,
        urls: list[str],
        *,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
        default_category: str = "",
    ) -> list[Product]:
        handles: list[str] = []
        for url in urls:
            match = PRODUCT_PATH.search(urlparse(url).path or "")
            if match:
                handles.append(match.group(1))
        return self._crawl_handles(
            handles,
            progress_callback=progress_callback,
            max_workers=max_workers,
            default_category=default_category,
        )

    def _fetch_product_html(
        self,
        url: str,
        handle: str,
        *,
        default_category: str = "",
    ) -> Product | None:
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code != 200:
                return None
            page_html = resp.text
        except requests.RequestException:
            return None

        ld_product = self._parse_product_ld_json(page_html)
        if not ld_product:
            return None

        title = str(ld_product.get("name") or "").strip()
        if not title:
            return None

        offers = ld_product.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price_raw = offers.get("price") if isinstance(offers, dict) else None
        price = self._parse_price(price_raw)
        currency = str(offers.get("priceCurrency") or "USD").strip() if isinstance(offers, dict) else "USD"

        availability = str(offers.get("availability") or "") if isinstance(offers, dict) else ""
        status = self._availability_from_schema(availability, page_html)

        images = self._collect_images_ld(ld_product, page_html)
        description = description_from_json_ld(ld_product.get("description"))
        if not description:
            description = extract_generic_description_from_html(page_html)

        sku = str(ld_product.get("sku") or handle).strip()
        categories = [default_category] if default_category else []

        return Product(
            source=self.source,
            id=sku or handle,
            title=html_lib.unescape(title),
            description=description,
            price=price,
            currency=currency,
            url=url,
            image_urls=images,
            vendor=self._shop_name(),
            sku=sku or handle,
            categories=categories,
            status=status,
        )

    @staticmethod
    def _parse_product_ld_json(page_html: str) -> dict[str, Any] | None:
        for match in LD_JSON_PATTERN.finditer(page_html):
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if isinstance(item, dict) and str(item.get("@type") or "").lower() == "product":
                    return item
        return None

    @staticmethod
    def _collect_images_ld(ld_product: dict[str, Any], page_html: str) -> list[str]:
        images: list[str] = []
        seen: set[str] = set()

        def add(url: object) -> None:
            text = str(url or "").strip()
            if not text or text in seen:
                return
            seen.add(text)
            images.append(text)

        raw_image = ld_product.get("image")
        if isinstance(raw_image, str):
            add(raw_image)
        elif isinstance(raw_image, list):
            for item in raw_image:
                if isinstance(item, str):
                    add(item)
                elif isinstance(item, dict):
                    add(item.get("url") or item.get("contentUrl"))

        for match in PRODUCT_IMAGE_RE.finditer(page_html):
            add(match.group(0))

        return images[:12]

    @staticmethod
    def _parse_price(value: object) -> float | None:
        if value in (None, ""):
            return None
        try:
            return round(float(str(value).replace(",", "")), 2)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _availability_from_schema(availability: str, page_html: str) -> str:
        text = (availability or "").lower()
        if "outofstock" in text.replace("_", "").replace("-", ""):
            return "out_of_stock"
        if "instock" in text.replace("_", "").replace("-", ""):
            return "instock"
        if re.search(r"\b(out of stock|sold out)\b", page_html, re.I):
            return "out_of_stock"
        return "instock"

    def _discover_product_urls(self, max_sitemaps: int | None = None) -> list[str]:
        sitemap_urls = self._collect_product_sitemaps()
        if max_sitemaps and max_sitemaps > 0:
            sitemap_urls = sitemap_urls[:max_sitemaps]

        urls: list[str] = []
        seen: set[str] = set()
        for sitemap_url in sitemap_urls:
            for loc in self._parse_sitemap_locs(sitemap_url):
                if not PRODUCT_PATH.search(urlparse(loc).path or ""):
                    continue
                if loc not in seen:
                    seen.add(loc)
                    urls.append(loc)
            if self.request_delay:
                time.sleep(self.request_delay)
        return urls

    def _collect_product_sitemaps(self) -> list[str]:
        root_candidates = [
            f"{self.site_url}/sitemap.xml",
            f"{self.site_url}/sitemap_products.xml",
        ]
        found: list[str] = []
        seen: set[str] = set()

        for candidate in root_candidates:
            locs = self._parse_sitemap_locs(candidate)
            if not locs:
                continue
            if any(PRODUCT_PATH.search(urlparse(loc).path or "") for loc in locs):
                return [candidate]
            for loc in locs:
                lower = loc.lower()
                if "sitemap_product" in lower and loc not in seen:
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
        for el in root.iter():
            if el.tag.endswith("loc") and el.text:
                locs.append(el.text.strip())
        return locs
