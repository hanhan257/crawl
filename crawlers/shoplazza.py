"""Crawl Shoplazza storefronts (soulmaty.com / static.staticdj.com).

Catalog: ``GET /api/collections/{id}/products?page=0&limit=N``.
Search: ``GET /api/search?keyword=&page=0&limit=N``.

``page`` on these shops does not walk the catalog (later pages repeat the
first rows and then stop). One request with ``limit`` equal to the reported
count returns the full set. The API rejects limits above 250, so a collection
larger than that is only partially available from this public endpoint.
"""

from __future__ import annotations

import gzip
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
from utils.http_session import create_http_session

CrawlProgressCallback = Callable[[int, int, int], None]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MAX_LIMIT = 250
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
COLLECTION_ID_RE = re.compile(
    r"/api/collections/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/",
    re.I,
)
MENU_JSON_RE = re.compile(
    r'<script id="menu-json" type="application/json">(.*?)</script>',
    re.S,
)
CURRENCY_RE = re.compile(r"[?&]currency=([A-Z]{3})")


class ShoplazzaCrawler(BaseCrawler):
    source = "shoplazza"

    def __init__(
        self,
        site_url: str,
        *,
        proxy_url: str | None = None,
        request_delay: float = 0.0,
    ):
        self.site_url = self._normalize_site_url(site_url)
        self.proxy_url = proxy_url
        self.request_delay = request_delay
        self.session = create_http_session(proxy_url)
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        self.partial_callback: Callable[[list[Product]], None] | None = None
        self.last_products: list[Product] = []
        self._collections: list[dict[str, str]] | None = None
        self._sitemap_locs_cache: list[str] | None = None
        self._currency: str | None = None

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
        try:
            response = http.get(base + "/", timeout=12)
        except requests.RequestException:
            return False
        if response.status_code != 200:
            return False
        text = (response.text or "").lower()
        return "shoplazza.com" in text or "static.staticdj.com" in text

    def api_reachable(self) -> bool:
        try:
            response = self.session.get(
                f"{self.site_url}/api/search",
                params={"keyword": "", "page": 0, "limit": 1},
                headers={"Accept": "application/json"},
                timeout=12,
            )
        except requests.RequestException:
            return False
        if response.status_code != 200:
            return False
        try:
            data = response.json().get("data")
        except ValueError:
            return False
        return isinstance(data, dict) and isinstance(data.get("products"), list)

    def crawl(
        self,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        category_slug: str | None = None,
        max_workers: int = 8,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        chosen = (category_slug or "").strip()
        if chosen and chosen not in {"Tất cả"}:
            collection = self._collection_by_handle(chosen)
            products = self._crawl_collections(
                [collection],
                limit=limit,
                progress_callback=progress_callback,
                max_workers=1,
            )
        else:
            collections = self.list_collection_rows()
            if max_pages and max_pages > 0:
                collections = collections[:max_pages]
            products = self._crawl_collections(
                collections,
                limit=limit,
                progress_callback=progress_callback,
                max_workers=max_workers,
            )
            products = self._fill_from_catalog_search(products, limit=limit)
        if limit and limit > 0:
            products = products[:limit]
        self.last_products = products
        return products

    def crawl_search(
        self,
        query: str,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        del max_pages, max_workers
        keyword = query.strip()
        if not keyword:
            raise ValueError("Thiếu từ khóa tìm kiếm.")
        if progress_callback:
            progress_callback(0, 1, 0)
        items = self._list_products("/api/search", {"keyword": keyword})
        products = [
            product
            for item in items
            if (product := self._map_item(item, categories=self._search_categories(item)))
        ]
        if limit and limit > 0:
            products = products[:limit]
        self.last_products = products
        if progress_callback:
            progress_callback(1, 1, len(products))
        if self.partial_callback and products:
            self.partial_callback(products)
        return products

    def crawl_product(self, handle: str) -> list[Product]:
        slug = (handle or "").strip().strip("/")
        if not slug:
            return []
        keyword = slug.replace("-", " ")
        items = self._list_products("/api/search", {"keyword": keyword})
        for item in items:
            if str(item.get("handle") or "").strip() == slug:
                product = self._map_item(item, categories=self._search_categories(item))
                return [product] if product else []
        for collection in self.list_collection_rows():
            items = self._list_products(
                f"/api/collections/{collection['id']}/products",
                {},
            )
            for item in items:
                if str(item.get("handle") or "").strip() == slug:
                    product = self._map_item(
                        item,
                        categories=[collection["title"]] if collection.get("title") else [],
                    )
                    return [product] if product else []
        return []

    def count_search_results(self, query: str) -> int | None:
        keyword = (query or "").strip()
        if not keyword:
            return None
        try:
            data = self._get_json(
                "/api/search",
                {"keyword": keyword, "page": 0, "limit": 1},
            )
        except (requests.RequestException, ValueError):
            return None
        return self._as_int(data.get("total"))

    def count_collection(self, handle: str) -> int | None:
        try:
            collection = self._collection_by_handle(handle)
            data = self._get_json(
                f"/api/collections/{collection['id']}/products",
                {"page": 0, "limit": 1},
            )
        except (requests.RequestException, ValueError, KeyError):
            return None
        count = self._as_int(data.get("count"))
        if count is None:
            count = self._as_int(data.get("total"))
        return count

    def count_shop(self) -> int | None:
        sitemap_count = self._sitemap_product_count()
        if sitemap_count is not None:
            return sitemap_count
        rows = self.list_categories()
        counts = [row["count"] for row in rows if isinstance(row.get("count"), int)]
        if not counts:
            return None
        if len(counts) == 1:
            return counts[0]
        return sum(counts)

    def list_categories(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for collection in self.list_collection_rows():
            count = self.count_collection(collection["handle"])
            rows.append(
                {
                    "slug": collection["handle"],
                    "name": collection.get("title") or collection["handle"],
                    "count": count,
                }
            )
        return rows

    def list_collection_rows(self) -> list[dict[str, str]]:
        if self._collections is not None:
            return list(self._collections)
        found: dict[str, dict[str, str]] = {}
        for row in self._menu_collections():
            found[row["handle"]] = row
        for handle in self._sitemap_collection_handles():
            if handle in found:
                continue
            try:
                found[handle] = self._collection_from_page(handle)
            except (requests.RequestException, ValueError):
                continue
        self._collections = list(found.values())
        return list(self._collections)

    def _crawl_collections(
        self,
        collections: list[dict[str, str]],
        *,
        limit: int | None,
        progress_callback: CrawlProgressCallback | None,
        max_workers: int,
    ) -> list[Product]:
        if not collections:
            return []
        total_jobs = len(collections)
        products: dict[str, Product] = {}
        lock = threading.Lock()
        done = 0
        if progress_callback:
            progress_callback(0, total_jobs, 0)

        def fetch(collection: dict[str, str]) -> list[Product]:
            http = create_http_session(self.proxy_url)
            http.headers["User-Agent"] = DEFAULT_USER_AGENT
            items = self._list_products(
                f"/api/collections/{collection['id']}/products",
                {},
                session=http,
            )
            title = collection.get("title") or ""
            mapped: list[Product] = []
            for item in items:
                product = self._map_item(item, categories=[title] if title else [])
                if product:
                    mapped.append(product)
            return mapped

        def absorb(batch: list[Product]) -> None:
            nonlocal done
            with lock:
                for product in batch:
                    current = products.get(product.id)
                    if current is None:
                        products[product.id] = product
                    else:
                        for name in product.categories:
                            if name and name not in current.categories:
                                current.categories.append(name)
                done += 1
                current_list = list(products.values())
                self.last_products = current_list
                if progress_callback and (done == total_jobs or done % 1 == 0):
                    progress_callback(done, total_jobs, len(current_list))
                if self.partial_callback and current_list:
                    self.partial_callback(current_list)

        workers = min(max(1, max_workers), 8, total_jobs)
        if workers <= 1:
            for collection in collections:
                absorb(fetch(collection))
                if limit and len(products) >= limit:
                    break
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(fetch, collection) for collection in collections]
                for future in as_completed(futures):
                    absorb(future.result())
                    if limit and len(products) >= limit:
                        break
        return list(products.values())

    def _fill_from_catalog_search(
        self,
        products: list[Product],
        *,
        limit: int | None,
    ) -> list[Product]:
        """Empty-keyword search is the public catalog when collections miss rows."""
        if limit and len(products) >= limit:
            return products
        try:
            total = self._as_int(
                self._get_json(
                    "/api/search",
                    {"keyword": "", "page": 0, "limit": 1},
                ).get("total")
            )
        except (requests.RequestException, ValueError):
            return products
        if total is None or len(products) >= total:
            return products
        indexed = {product.id: product for product in products}
        for item in self._list_products("/api/search", {"keyword": ""}):
            product = self._map_item(item, categories=self._search_categories(item))
            if product and product.id not in indexed:
                indexed[product.id] = product
        return list(indexed.values())

    def _collection_by_handle(self, handle: str) -> dict[str, str]:
        slug = handle.strip().strip("/")
        for collection in self.list_collection_rows():
            if collection["handle"] == slug:
                return collection
        return self._collection_from_page(slug)

    def _collection_from_page(self, handle: str) -> dict[str, str]:
        response = self.session.get(
            f"{self.site_url}/collections/{handle}",
            timeout=20,
        )
        response.raise_for_status()
        match = COLLECTION_ID_RE.search(response.text or "")
        if not match:
            raise ValueError(f"Không thấy collection id của /collections/{handle}.")
        title = handle.replace("-", " ").strip().title()
        return {"id": match.group(1), "handle": handle, "title": title}

    def _menu_collections(self) -> list[dict[str, str]]:
        try:
            response = self.session.get(self.site_url + "/", timeout=20)
            response.raise_for_status()
        except requests.RequestException:
            return []
        text = response.text or ""
        currency = CURRENCY_RE.search(text)
        if currency:
            self._currency = currency.group(1)
        match = MENU_JSON_RE.search(text)
        if not match:
            return []
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []
        rows: list[dict[str, str]] = []
        seen: set[str] = set()

        def walk(nodes: object) -> None:
            if not isinstance(nodes, list):
                return
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if str(node.get("type") or "") == "collection":
                    obj = node.get("object") if isinstance(node.get("object"), dict) else {}
                    source_id = str(obj.get("source_id") or "").strip()
                    raw_url = str(obj.get("url") or node.get("url") or "")
                    handle = raw_url.rstrip("/").split("/")[-1].strip()
                    title = str(obj.get("title") or node.get("title") or handle).strip()
                    if source_id and handle and handle not in seen:
                        seen.add(handle)
                        rows.append({"id": source_id, "handle": handle, "title": title})
                walk(node.get("links"))

        walk(payload.get("links") if isinstance(payload, dict) else None)
        return rows

    def _sitemap_collection_handles(self) -> list[str]:
        handles: list[str] = []
        for loc in self._sitemap_locs():
            if "/collections/" not in loc:
                continue
            handle = loc.split("/collections/", 1)[1].strip("/").split("/")[0]
            if handle and handle not in handles:
                handles.append(handle)
        return handles

    def _sitemap_product_count(self) -> int | None:
        locs = [loc for loc in self._sitemap_locs() if "/products/" in loc]
        if not locs:
            return None
        return len(locs)

    def _sitemap_locs(self) -> list[str]:
        if self._sitemap_locs_cache is not None:
            return list(self._sitemap_locs_cache)
        try:
            index = self._fetch_bytes(self.site_url + "/sitemap.xml")
        except requests.RequestException:
            self._sitemap_locs_cache = []
            return []
        locs = self._xml_locs(index)
        nested = [loc for loc in locs if "sitemap" in loc.lower() and loc != self.site_url + "/sitemap.xml"]
        if not nested:
            self._sitemap_locs_cache = locs
            return list(locs)
        collected: list[str] = []
        for loc in nested:
            if "product" not in loc.lower() and "collection" not in loc.lower():
                continue
            try:
                collected.extend(self._xml_locs(self._fetch_bytes(loc)))
            except requests.RequestException:
                continue
        self._sitemap_locs_cache = collected or locs
        return list(self._sitemap_locs_cache)

    def _fetch_bytes(self, url: str) -> bytes:
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        raw = response.content or b""
        if raw[:2] == b"\x1f\x8b":
            return gzip.decompress(raw)
        return raw

    @staticmethod
    def _xml_locs(raw: bytes) -> list[str]:
        if not raw:
            return []
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return []
        locs = [
            (el.text or "").strip()
            for el in root.findall(".//sm:loc", SITEMAP_NS)
            if (el.text or "").strip()
        ]
        if locs:
            return locs
        return [
            (el.text or "").strip()
            for el in root.iter()
            if el.tag.endswith("loc") and (el.text or "").strip()
        ]

    def _list_products(
        self,
        path: str,
        params: dict[str, Any],
        *,
        session: requests.Session | None = None,
    ) -> list[dict[str, Any]]:
        probe = self._get_json(path, {**params, "page": 0, "limit": 1}, session=session)
        total = self._as_int(probe.get("count"))
        if total is None:
            total = self._as_int(probe.get("total")) or 0
        limits: list[int] = []
        if total > 0:
            limits.append(min(total, MAX_LIMIT))
        for fallback in (100, 50):
            if fallback not in limits:
                limits.append(fallback)
        best: list[dict[str, Any]] = []
        for limit in limits:
            data = self._get_json(
                path,
                {**params, "page": 0, "limit": limit},
                session=session,
            )
            items = [item for item in data.get("products") or [] if isinstance(item, dict)]
            if len(items) > len(best):
                best = items
            reported = self._as_int(data.get("count"))
            if reported is None:
                reported = self._as_int(data.get("total")) or total
            if reported and len(items) >= reported:
                return items
            if not data.get("has_more"):
                return items
        return best

    def _get_json(
        self,
        path: str,
        params: dict[str, Any],
        *,
        session: requests.Session | None = None,
    ) -> dict[str, Any]:
        http = session or self.session
        url = f"{self.site_url}{path}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = http.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json"},
                    timeout=30,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    time.sleep(0.6 + attempt)
                    last_error = requests.HTTPError(f"HTTP {response.status_code}")
                    continue
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, dict):
                    raise ValueError("Shoplazza trả về không phải JSON catalog.")
                if self.request_delay:
                    time.sleep(self.request_delay)
                return data
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                time.sleep(0.4 + attempt)
        if last_error:
            raise last_error
        raise ValueError("Shoplazza không trả dữ liệu.")

    def _map_item(
        self,
        item: dict[str, Any],
        *,
        categories: list[str],
    ) -> Product | None:
        title = str(item.get("title") or "").strip()
        handle = str(item.get("handle") or "").strip()
        product_id = str(item.get("id") or handle).strip()
        if not title or not handle or not product_id:
            return None
        images: list[str] = []
        for image in item.get("images") or []:
            src = ""
            if isinstance(image, dict):
                src = str(image.get("src") or "").strip()
            elif isinstance(image, str):
                src = image.strip()
            normalized = self._absolute_url(src)
            if normalized and normalized not in images:
                images.append(normalized)
        if not images and isinstance(item.get("image"), dict):
            normalized = self._absolute_url(str(item["image"].get("src") or ""))
            if normalized:
                images.append(normalized)
        sku = str(item.get("spu") or "").strip() or None
        variants = item.get("variants") if isinstance(item.get("variants"), list) else []
        if not sku and variants and isinstance(variants[0], dict):
            sku = str(variants[0].get("sku") or "").strip() or None
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            description = None
        product_type = str(item.get("product_type") or "").strip()
        if product_type.lower() == "default":
            product_type = ""
        return Product(
            source=self.source,
            id=product_id,
            title=title,
            description=description,
            price=self._price(item.get("price")),
            currency=self._currency or "USD",
            url=f"{self.site_url}/products/{handle}",
            image_urls=images,
            vendor=str(item.get("vendor") or "").strip() or None,
            sku=sku,
            tags=self._tags(item),
            categories=[name for name in categories if name],
            product_type=product_type or None,
            status="active" if item.get("published") else None,
        )

    def _absolute_url(self, src: str) -> str:
        text = (src or "").strip()
        if not text:
            return ""
        if text.startswith("//"):
            return "https:" + text
        if text.startswith("/"):
            return self.site_url + text
        return text

    @classmethod
    def _search_categories(cls, item: dict[str, Any]) -> list[str]:
        tags = cls._tags(item)
        if tags:
            return tags
        product_type = str(item.get("product_type") or "").strip()
        if product_type and product_type.lower() != "default":
            return [product_type]
        return []

    @staticmethod
    def _tags(item: dict[str, Any]) -> list[str]:
        raw = item.get("tags")
        if isinstance(raw, str):
            parts = raw.split(",")
        elif isinstance(raw, list):
            parts = [str(part) for part in raw]
        else:
            parts = []
        return [part.strip() for part in parts if part and str(part).strip()]

    @staticmethod
    def _price(value: object) -> float | None:
        if value in (None, ""):
            return None
        try:
            return round(float(str(value).replace(",", "")), 2)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_int(value: object) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
