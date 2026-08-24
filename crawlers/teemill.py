"""Crawl Teemill storefront via Omnis API (vd. thewildlifetrustsshop.com)."""

from __future__ import annotations

import re
from collections.abc import Callable
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
DIVISION_RE = re.compile(r"/omnis/v3/(?:frontend|division)/(\d+)/")
TEEMILL_MARKERS = (
    "images.teemill.com",
    "/omnis/v3/frontend/",
    "/omnis/v3/division/",
)
TEEMILL_COLLECTION_PATH = re.compile(r"/collection/([^/?#]+)", re.I)
TEEMILL_PRODUCT_PATH = re.compile(r"/product/([^/?#]+)", re.I)


class TeemillCrawler(BaseCrawler):
    source = "teemill"

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
        # Omnis API + HTML preload ổn định hơn khi direct.
        self._api_session = create_http_session(None)
        self._api_session.headers["User-Agent"] = DEFAULT_USER_AGENT
        self._division_id: str | None = None

    @staticmethod
    def _normalize_site_url(site_url: str) -> str:
        url = site_url.strip().rstrip("/")
        if not url.startswith("http"):
            url = f"https://{url}"
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

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
        for path in ("/", "/collection/all-products/"):
            try:
                response = http.get(f"{base}{path}", timeout=20)
            except requests.RequestException:
                continue
            if response.status_code != 200:
                continue
            text = response.text or ""
            if any(marker in text for marker in TEEMILL_MARKERS):
                return True
        return False

    def _resolve_division_id(self, *, page_url: str | None = None) -> str:
        if self._division_id:
            return self._division_id

        candidates = [page_url, f"{self.site_url}/", f"{self.site_url}/collection/all-products/"]
        for candidate in candidates:
            if not candidate:
                continue
            for http in (self._api_session, self.session):
                try:
                    response = http.get(candidate, timeout=25)
                except requests.RequestException:
                    continue
                if response.status_code != 200:
                    continue
                match = DIVISION_RE.search(response.text or "")
                if match:
                    self._division_id = match.group(1)
                    return self._division_id

        raise ValueError(
            f"Không tìm thấy Teemill division id trên {self.site_url}."
        )

    def _api_get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = self._api_session.get(
            f"{self.site_url}{path}",
            params=params or {},
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def list_collections(self) -> list[dict[str, Any]]:
        division_id = self._resolve_division_id()
        payload = self._api_get(f"/omnis/v3/division/{division_id}/collections/")
        if not isinstance(payload, list):
            return []
        collections: list[dict[str, Any]] = []
        for item in payload:
            slug = str(item.get("urlName") or "").strip()
            name = str(item.get("name") or item.get("title") or slug).strip()
            if not slug or slug == "all-products":
                continue
            collections.append(
                {
                    "slug": slug,
                    "name": name,
                    "count": None,
                    "id": item.get("id"),
                }
            )
        return collections

    def count_products(self, collection_slug: str | None = None) -> int:
        division_id = self._resolve_division_id()
        params: dict[str, Any] = {}
        slug = (collection_slug or "").strip().strip("/")
        if slug and slug not in {"", "all-products", "Tất cả"}:
            params["collection"] = slug
        payload = self._api_get(
            f"/omnis/v3/division/{division_id}/products/",
            params=params,
        )
        if isinstance(payload, list):
            return len(payload)
        return 0

    def crawl(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        category_slug: str | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        slug = (category_slug or "").strip().strip("/")
        if slug and slug not in {"", "all-products", "Tất cả"}:
            return self.crawl_collection(
                slug,
                limit=limit,
                progress_callback=progress_callback,
            )
        return self._crawl_products(
            collection_slug=None,
            limit=limit,
            progress_callback=progress_callback,
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
        slug = (handle or "").strip().strip("/")
        if not slug or slug in {"all-products", "all"}:
            return self.crawl(limit=limit, progress_callback=progress_callback)
        return self._crawl_products(
            collection_slug=slug,
            limit=limit,
            progress_callback=progress_callback,
        )

    def crawl_product(self, slug: str) -> Product | None:
        slug = (slug or "").strip().strip("/")
        if not slug:
            return None
        products = self._crawl_products(
            collection_slug=None,
            product_slug=slug,
            limit=1,
        )
        return products[0] if products else None

    def _collection_name(self, collection_slug: str | None) -> str | None:
        slug = (collection_slug or "").strip().strip("/")
        if not slug:
            return None
        division_id = self._resolve_division_id(
            page_url=f"{self.site_url}/collection/{slug}/"
        )
        payload = self._api_get(
            f"/omnis/v3/division/{division_id}/collections/{slug}/"
        )
        if isinstance(payload, dict):
            return str(payload.get("name") or payload.get("title") or slug)
        return slug.replace("-", " ").title()

    def _crawl_products(
        self,
        *,
        collection_slug: str | None = None,
        product_slug: str | None = None,
        limit: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        page_url = (
            f"{self.site_url}/collection/{collection_slug}/"
            if collection_slug
            else f"{self.site_url}/"
        )
        division_id = self._resolve_division_id(page_url=page_url)
        params: dict[str, Any] = {}
        slug = (collection_slug or "").strip().strip("/")
        if slug:
            params["collection"] = slug

        payload = self._api_get(
            f"/omnis/v3/division/{division_id}/products/",
            params=params,
        )
        if not isinstance(payload, list):
            return []

        if product_slug:
            payload = [
                item
                for item in payload
                if str(item.get("urlName") or "").strip() == product_slug
            ]

        category_name = self._collection_name(collection_slug) if slug else None
        products = [self._map_product(item, category_name=category_name) for item in payload]
        if limit is not None:
            products = products[: max(0, int(limit))]

        if progress_callback:
            total = len(products)
            progress_callback(1, 1, total)
        return products

    def _map_product(
        self,
        item: dict[str, Any],
        *,
        category_name: str | None = None,
    ) -> Product:
        slug = str(item.get("urlName") or "").strip()
        images = [
            str(item.get("image") or "").strip(),
            str(item.get("lifestyleImage") or "").strip(),
            str(item.get("collectionImage") or "").strip(),
        ]
        images = [url for url in images if url]

        price_raw = item.get("salePrice") or item.get("price")
        price: float | None = None
        if price_raw not in (None, "", 0):
            try:
                price = float(price_raw)
            except (TypeError, ValueError):
                price = None

        categories = [category_name] if category_name else []
        return Product(
            source=self.source,
            id=str(item.get("id") or item.get("uuid") or slug),
            title=str(item.get("name") or ""),
            description=str(item.get("description") or "") or None,
            price=price,
            currency=None,
            url=f"{self.site_url}/product/{slug}/" if slug else None,
            image_urls=images,
            categories=categories,
            product_type=category_name,
        )
