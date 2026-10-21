"""Crawl BigCommerce storefronts whose catalog is served by SearchSpring.

christmascentral.com is this shape: Stencil theme on BigCommerce, product
search/index on SearchSpring (site id in snapui.searchspring.io). The search
API caps each filter at 10,000 rows, so a full shop is split by category.
"""

from __future__ import annotations

import html
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse

import requests

from crawlers.base import BaseCrawler
from models.product import Product
from utils.http_session import create_http_session

CrawlProgressCallback = Callable[[int, int, int], None]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
SEARCH_API = "https://api.searchspring.net/api/search/search.json"
SITE_ID_RE = re.compile(r"snapui\.searchspring\.io/([a-z0-9]+)/", re.I)
PAGE_SIZE = 100
PAGE_CAP = 100
SKIP_TOPS = {"Clearance", "Sale", "Shop by Collection", "Shop by Species"}


class SearchSpringCrawler(BaseCrawler):
    source = "searchspring"

    def __init__(
        self,
        site_url: str,
        *,
        proxy_url: str | None = None,
        request_delay: float = 0.0,
    ):
        self.site_url = self._normalize_site_url(site_url)
        self.request_delay = request_delay
        self.session = create_http_session(proxy_url)
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        self._api = create_http_session(None)
        self._api.headers["User-Agent"] = DEFAULT_USER_AGENT
        self._site_id: str | None = None
        self.partial_callback: Callable[[list[Product]], None] | None = None
        self.last_products: list[Product] = []

    @staticmethod
    def _normalize_site_url(site_url: str) -> str:
        url = site_url.strip().rstrip("/")
        if not url.startswith("http"):
            url = f"https://{url}"
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @classmethod
    def site_id_from_html(cls, text: str) -> str | None:
        match = SITE_ID_RE.search(text or "")
        return match.group(1) if match else None

    @classmethod
    def site_supports(
        cls,
        site_url: str,
        *,
        session: requests.Session | None = None,
    ) -> bool:
        base = cls._normalize_site_url(site_url)
        http = session or requests.Session()
        if not http.headers.get("User-Agent"):
            http.headers["User-Agent"] = DEFAULT_USER_AGENT
        try:
            response = http.get(base + "/", timeout=12)
        except requests.RequestException:
            return False
        if response.status_code != 200:
            return False
        text = response.text or ""
        if cls.site_id_from_html(text) is None:
            return False
        lowered = text.lower()
        headers = {key.lower() for key in response.headers}
        return (
            "cdn11.bigcommerce.com" in lowered
            or "bigcommerce.com/s-" in lowered
            or "x-bc-store-id" in headers
        )

    def _resolve_site_id(self) -> str:
        if self._site_id:
            return self._site_id
        response = self.session.get(self.site_url + "/", timeout=20)
        response.raise_for_status()
        site_id = self.site_id_from_html(response.text or "")
        if not site_id:
            raise ValueError(
                f"Không thấy SearchSpring site id trên {self.site_url}."
            )
        self._site_id = site_id
        return site_id

    def _search(
        self,
        site_id: str,
        page: int,
        category: str | None = None,
        *,
        session: requests.Session | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "siteId": site_id,
            "resultsFormat": "native",
            "resultsPerPage": PAGE_SIZE,
            "page": max(1, page),
        }
        if category:
            params["filter.ss_categories_hierarchy"] = category
        http = session or self._api
        response = http.get(SEARCH_API, params=params, timeout=40)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("SearchSpring trả về không phải JSON object.")
        return payload

    @staticmethod
    def _category_values(payload: dict[str, Any]) -> list[dict[str, Any]]:
        for facet in payload.get("facets") or []:
            if isinstance(facet, dict) and facet.get("field") == "ss_categories_hierarchy":
                values = facet.get("values") or []
                return [item for item in values if isinstance(item, dict)]
        return []

    @staticmethod
    def _total(payload: dict[str, Any]) -> int:
        try:
            return int((payload.get("pagination") or {}).get("totalResults") or 0)
        except (TypeError, ValueError):
            return 0

    def _shards(self, site_id: str, only: str | None = None) -> list[tuple[str, int]]:
        chosen = (only or "").strip()
        if chosen and chosen not in {"Tất cả"}:
            payload = self._search(site_id, 1, chosen)
            total = self._total(payload)
            if total <= PAGE_SIZE * PAGE_CAP:
                return [(chosen, total)] if total else []
            children = self._child_shards(site_id, chosen, payload)
            return children or [(chosen, PAGE_SIZE * PAGE_CAP)]

        payload = self._search(site_id, 1)
        shards: list[tuple[str, int]] = []
        for item in self._category_values(payload):
            name = str(item.get("value") or "").strip()
            try:
                count = int(item.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
            if not name or count <= 0:
                continue
            if count <= PAGE_SIZE * PAGE_CAP:
                shards.append((name, count))
            else:
                children = self._child_shards(site_id, name)
                shards.extend(children or [(name, PAGE_SIZE * PAGE_CAP)])
        return shards

    def _child_shards(
        self,
        site_id: str,
        parent: str,
        payload: dict[str, Any] | None = None,
    ) -> list[tuple[str, int]]:
        if payload is None:
            payload = self._search(site_id, 1, parent)
        parent_depth = parent.count(">")
        shards: list[tuple[str, int]] = []
        for item in self._category_values(payload):
            name = str(item.get("value") or "").strip()
            if not name.startswith(parent + ">"):
                continue
            if name.count(">") != parent_depth + 1:
                continue
            try:
                count = int(item.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
            if count <= 0:
                continue
            if count <= PAGE_SIZE * PAGE_CAP:
                shards.append((name, count))
            else:
                children = self._child_shards(site_id, name)
                shards.extend(children or [(name, PAGE_SIZE * PAGE_CAP)])
        return shards

    def list_categories(self) -> list[dict[str, Any]]:
        site_id = self._resolve_site_id()
        payload = self._search(site_id, 1)
        rows: list[dict[str, Any]] = []
        for item in self._category_values(payload):
            name = str(item.get("value") or "").strip()
            if not name:
                continue
            try:
                count = int(item.get("count") or 0)
            except (TypeError, ValueError):
                count = None
            rows.append({"slug": name, "name": name, "count": count})
        return rows

    def count_products(self, category: str | None = None) -> int:
        site_id = self._resolve_site_id()
        chosen = (category or "").strip()
        payload = self._search(site_id, 1, chosen or None)
        return self._total(payload)

    def crawl(
        self,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        category_slug: str | None = None,
        max_workers: int = 8,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        site_id = self._resolve_site_id()
        shards = self._shards(site_id, category_slug)
        jobs: list[tuple[str, int]] = []
        for name, count in shards:
            pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
            pages = min(pages, PAGE_CAP)
            for page in range(1, pages + 1):
                jobs.append((name, page))
        if max_pages and max_pages > 0:
            jobs = jobs[:max_pages]
        if limit and limit > 0 and len(jobs) > limit:
            jobs = jobs[: max(1, (limit + PAGE_SIZE - 1) // PAGE_SIZE)]

        total_jobs = len(jobs)
        if total_jobs == 0:
            return []

        products: dict[str, Product] = {}
        lock = threading.Lock()
        done = 0
        local = threading.local()

        def session_for_thread() -> requests.Session:
            if not hasattr(local, "session"):
                local.session = create_http_session(None)
                local.session.headers["User-Agent"] = DEFAULT_USER_AGENT
            return local.session

        def fetch(job: tuple[str, int]) -> list[dict[str, Any]]:
            name, page = job
            for attempt in range(3):
                try:
                    payload = self._search(
                        site_id,
                        page,
                        name,
                        session=session_for_thread(),
                    )
                    results = payload.get("results") or []
                    return [item for item in results if isinstance(item, dict)]
                except (requests.RequestException, ValueError):
                    if attempt == 2:
                        return []
            return []

        workers = min(max(1, max_workers), 12, total_jobs)

        def absorb(items: list[dict[str, Any]]) -> None:
            nonlocal done
            mapped = [product for item in items if (product := self._map_item(item))]
            with lock:
                for product in mapped:
                    products.setdefault(product.id, product)
                done += 1
                current = list(products.values())
                self.last_products = current
                if progress_callback and (
                    done == total_jobs or done % 10 == 0
                ):
                    progress_callback(done, total_jobs, len(current))
                if (
                    self.partial_callback is not None
                    and current
                    and (done == total_jobs or done % 20 == 0)
                ):
                    self.partial_callback(current)

        if workers <= 1:
            for job in jobs:
                absorb(fetch(job))
                if limit and len(products) >= limit:
                    break
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(fetch, job) for job in jobs]
                for future in as_completed(futures):
                    absorb(future.result())

        ordered = list(products.values())
        if limit and limit > 0:
            ordered = ordered[:limit]
        self.last_products = ordered
        return ordered

    def _map_item(self, item: dict[str, Any]) -> Product | None:
        title = html.unescape(str(item.get("name") or "")).strip()
        url = str(item.get("url") or "").strip()
        product_id = str(item.get("uid") or item.get("id") or "").strip()
        if not title or not url or not product_id:
            return None
        price = self._price(item.get("price"))
        images: list[str] = []
        for key in ("imageUrl", "ss_image_hover", "custom_webimage2", "thumbnailImageUrl"):
            src = str(item.get(key) or "").strip()
            if src and src not in images:
                images.append(src)
        category = self._category(item)
        return Product(
            source=self.source,
            id=product_id,
            title=title,
            price=price,
            currency="USD",
            url=url,
            image_urls=images,
            vendor=str(item.get("brand") or "").strip() or None,
            sku=str(item.get("sku") or "").strip() or None,
            categories=[category] if category else [],
            status="instock" if item.get("ss_in_stock") in (True, "1", 1) else None,
        )

    @staticmethod
    def _price(value: object) -> float | None:
        if value in (None, ""):
            return None
        try:
            return round(float(str(value).replace(",", "")), 2)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _category(item: dict[str, Any]) -> str:
        raw_paths = item.get("categories_hierarchy") or item.get("ss_categories_hierarchy") or []
        if isinstance(raw_paths, str):
            raw_paths = [raw_paths]
        paths: list[str] = []
        for path in raw_paths:
            text = html.unescape(str(path or "")).replace("&gt;", ">").strip()
            if text:
                paths.append(text)
        preferred = [
            path
            for path in paths
            if path.split(">", 1)[0].strip() not in SKIP_TOPS
        ]
        chosen = preferred or paths
        if not chosen:
            return ""
        best = max(chosen, key=lambda path: path.count(">"))
        return " > ".join(part.strip() for part in best.split(">") if part.strip())
