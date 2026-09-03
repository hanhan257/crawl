from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urljoin, urlparse, unquote

import requests

from crawlers.base import BaseCrawler
from models.product import Product
from utils.crawl_parallel import API_LIST_WAVE_SIZE, fetch_pages_in_waves
from utils.description_html import (
    extract_shopify_description_from_html,
    shopify_description_from_item,
)
from utils.http_retry import DEFAULT_REQUEST_TIMEOUT, get_with_retry
from utils.http_session import ProxyRotator, create_http_session, reset_session

CrawlProgressCallback = Callable[[int, int, int], None]
SHOPIFY_SEARCH_HANDLE_PATTERN = re.compile(r'"handle"\s*:\s*"([^"]+)"')
SHOPIFY_SEARCH_COUNT_PATTERN = re.compile(r"([\d][\d,]*)\s+items", re.I)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
GRAPHQL_PATH = "/api/2024-10/graphql.json"
GRAPHQL_PAGE_SIZE = 50
GRAPHQL_HANDLE_BATCH = 5
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
GID_NUMERIC = re.compile(r"/(\d+)\s*$")
PRODUCT_PATH = re.compile(r"/products/([^/?#]+)", re.I)

PRODUCT_NODE_SELECTION = """
id
title
handle
descriptionHtml
productType
vendor
tags
featuredImage { url }
images(first: 10) { edges { node { url } } }
variants(first: 50) {
  edges {
    node {
      id
      title
      sku
      availableForSale
      price { amount }
      compareAtPrice { amount }
      selectedOptions { name value }
      image { url }
    }
  }
}
"""

PRODUCTS_GRAPHQL = f"""
query ProductsPage($cursor: String, $pageSize: Int!) {{
  products(first: $pageSize, after: $cursor) {{
    pageInfo {{ hasNextPage endCursor }}
    edges {{
      node {{
{PRODUCT_NODE_SELECTION}
      }}
    }}
  }}
}}
"""

COLLECTION_PRODUCTS_GRAPHQL = f"""
query CollectionProducts($handle: String!, $cursor: String, $pageSize: Int!) {{
  collection(handle: $handle) {{
    products(first: $pageSize, after: $cursor) {{
      pageInfo {{ hasNextPage endCursor }}
      edges {{
        node {{
{PRODUCT_NODE_SELECTION}
        }}
      }}
    }}
  }}
}}
"""

PRODUCT_BY_HANDLE_GRAPHQL = f"""
query ProductByHandle($handle: String!) {{
  product(handle: $handle) {{
{PRODUCT_NODE_SELECTION}
  }}
}}
"""


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
        proxy_url: str | None = None,
    ):
        self.shop_url = self._normalize_shop_url(shop_url)
        self.access_token = access_token
        self.api_version = api_version
        self.use_public_api = use_public_api
        self.request_delay = request_delay
        self.proxy_rotator = ProxyRotator(proxy_url)
        self.session = create_http_session(self.proxy_rotator.current())
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        self._page_local = threading.local()
        self.last_products: list[Product] = []
        self.partial_callback: Callable[[list[Product]], None] | None = None

        if not use_public_api and not access_token:
            raise ValueError("Shopify Admin API requires --token or use --public for storefront JSON")

    def refresh_session(self, *, rotate: bool = True) -> None:
        proxy = self.proxy_rotator.rotate() if rotate else self.proxy_rotator.current()
        self.session = reset_session(self.session, proxy)
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        # Xóa cache probe — proxy mới có thể mở được products.json/GraphQL.
        self._public_json_ok = None
        self._graphql_ok = None

    def _switch_to_direct_session(self) -> None:
        """Một số shop chặn proxy list — chuyển sang direct cho cả crawl."""
        self.proxy_rotator = ProxyRotator(None)
        self.session = create_http_session(None)
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        if hasattr(self._page_local, "session"):
            delattr(self._page_local, "session")
        self._public_json_ok = None
        self._graphql_ok = None

    @staticmethod
    def _products_json_probe(session: requests.Session, shop_url: str) -> bool:
        try:
            response = session.get(
                f"{shop_url}/products.json",
                params={"limit": 1, "page": 1},
                timeout=12,
            )
            if response.status_code != 200:
                return False
            data = response.json()
            return isinstance(data, dict) and isinstance(data.get("products"), list)
        except (requests.RequestException, ValueError):
            return False

    def _page_session(self, *, rotate: bool = False) -> requests.Session:
        if rotate or not hasattr(self._page_local, "session"):
            proxy = (
                self.proxy_rotator.rotate()
                if rotate and self.proxy_rotator.count > 1
                else self.proxy_rotator.current()
            )
            if (
                not rotate
                and self.proxy_rotator.count > 1
                and not hasattr(self._page_local, "session")
            ):
                proxy = self.proxy_rotator.rotate()
            sess = create_http_session(proxy)
            sess.headers.update(self.session.headers)
            self._page_local.session = sess
        return self._page_local.session

    def _publish_partial(self, products: list[Product]) -> None:
        self.last_products = list(products)
        if self.partial_callback is not None:
            try:
                self.partial_callback(self.last_products)
            except Exception:
                pass

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

        if self.use_public_api and not self._public_products_json_ok():
            products = self._crawl_graphql(
                limit=limit,
                max_pages=max_pages,
                progress_callback=progress_callback,
                collection_handle=handle,
            )
            for product in products:
                product.categories = [handle]
            return products

        per_page = 250

        def fetch_page(page: int) -> list[dict[str, Any]]:
            return self._fetch_collection_page(handle, page)

        try:
            first_batch = fetch_page(1)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 429:
                products = self._crawl_graphql(
                    limit=limit,
                    max_pages=max_pages,
                    progress_callback=progress_callback,
                    collection_handle=handle,
                )
                for product in products:
                    product.categories = [handle]
                return products
            raise

        estimated_pages = max_pages
        if estimated_pages is None and limit is not None:
            estimated_pages = max(1, (limit + per_page - 1) // per_page)

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

        workers = max(1, min(max_workers, 500))
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

    def _public_products_json_ok(self) -> bool:
        """True nếu /products.json còn dùng được (một số shop Shopify chặn 429)."""
        cached = getattr(self, "_public_json_ok", None)
        if cached is not None:
            return cached
        ok = self._products_json_probe(self.session, self.shop_url)
        if not ok and self.proxy_rotator.has_proxy:
            direct = create_http_session(None)
            direct.headers["User-Agent"] = DEFAULT_USER_AGENT
            if self._products_json_probe(direct, self.shop_url):
                self._switch_to_direct_session()
                ok = True
        elif not ok and self.proxy_rotator.count > 1:
            self.refresh_session(rotate=True)
            ok = self._products_json_probe(self.session, self.shop_url)
        self._public_json_ok = ok
        return ok

    def _graphql_available(self) -> bool:
        cached = getattr(self, "_graphql_ok", None)
        if cached is not None:
            return cached
        try:
            payload = self._graphql_request(
                "query { products(first: 1) { edges { node { handle } } } }"
            )
            ok = bool(payload.get("data", {}).get("products"))
        except (requests.RequestException, ValueError, KeyError):
            ok = False
        self._graphql_ok = ok
        return ok

    def _graphql_request(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        max_retries: int = 5,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                response = self._page_session().post(
                    f"{self.shop_url}{GRAPHQL_PATH}",
                    json={"query": query, "variables": variables or {}},
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    timeout=DEFAULT_REQUEST_TIMEOUT,
                )
                if response.status_code == 429 and attempt + 1 < max_retries:
                    retry_after = response.headers.get("Retry-After", "5")
                    try:
                        sleep_s = min(max(float(retry_after), 1.0), 30.0)
                    except (TypeError, ValueError):
                        sleep_s = 5.0
                    time.sleep(sleep_s)
                    continue
                response.raise_for_status()
                payload = response.json()
                errors = payload.get("errors") or []
                throttled = any(
                    "throttl" in str(err.get("message") or err).lower()
                    for err in errors
                )
                if throttled and attempt + 1 < max_retries:
                    time.sleep(min(2.0 * (attempt + 1), 20.0))
                    continue
                if errors:
                    messages = "; ".join(
                        str(err.get("message") or err) for err in errors[:3]
                    )
                    raise requests.RequestException(
                        f"Shopify GraphQL error: {messages}"
                    )
                return payload
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                if attempt + 1 < max_retries:
                    time.sleep(min(2.0 * (attempt + 1), 12.0))
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise requests.RequestException("Shopify GraphQL request failed")

    def _fetch_public_page(self, page: int) -> list[dict[str, Any]]:
        response = get_with_retry(
            self._page_session(),
            f"{self.shop_url}/products.json",
            params={"limit": 250, "page": page},
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        batch = response.json().get("products", [])
        return batch if isinstance(batch, list) else []

    def crawl_sitemap(
        self,
        sitemap_url: str | None = None,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int = 8,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        """Crawl SP từ 1 file sitemap_products_*.xml hoặc toàn bộ sitemap shop."""
        return self._crawl_via_sitemap(
            sitemap_url=sitemap_url,
            limit=limit,
            max_sitemaps=max_pages,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )

    def _crawl_public(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        # GraphQL catalog đầy đủ hơn products.json (hết page ~21) và hỗ trợ tới ~25k SP.
        if self._graphql_available():
            try:
                products = self._crawl_graphql(
                    limit=limit,
                    max_pages=max_pages,
                    progress_callback=progress_callback,
                )
                return self._crawl_via_sitemap_if_larger(
                    products,
                    limit=limit,
                    max_pages=max_pages,
                    max_workers=max_workers,
                    progress_callback=progress_callback,
                )
            except requests.RequestException:
                # GraphQL lỗi/throttle → sitemap + batch handle (tránh products.json đứng ở page 2).
                return self._crawl_via_sitemap(
                    limit=limit,
                    max_sitemaps=max_pages,
                    max_workers=max_workers,
                    progress_callback=progress_callback,
                )

        # PopFanPrints: /products.json 429 — crawl sitemap + GraphQL theo handle
        # (không paginate products GraphQL: dễ throttle/đứng ~7–8k SP).
        if not self._public_products_json_ok():
            if self._graphql_available():
                return self._crawl_via_sitemap(
                    limit=limit,
                    max_sitemaps=max_pages,
                    max_workers=max_workers,
                    progress_callback=progress_callback,
                )
            raise requests.RequestException(
                "Shopify /products.json bị chặn (429) và GraphQL cũng không dùng được."
            )

        per_page = 250
        try:
            first_batch = self._fetch_public_page(1)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 429 and self._graphql_available():
                return self._crawl_via_sitemap(
                    limit=limit,
                    max_sitemaps=max_pages,
                    max_workers=max_workers,
                    progress_callback=progress_callback,
                )
            raise

        estimated_pages = max_pages
        if estimated_pages is None and limit is not None:
            estimated_pages = max(1, (limit + per_page - 1) // per_page)

        list_workers = max_workers
        if list_workers > API_LIST_WAVE_SIZE:
            list_workers = API_LIST_WAVE_SIZE

        raw_items, _pages = fetch_pages_in_waves(
            self._fetch_public_page,
            per_page=per_page,
            first_batch=first_batch,
            max_pages=max_pages,
            max_workers=list_workers,
            limit=limit,
            progress_callback=progress_callback,
            estimated_pages=estimated_pages,
        )
        products = [self._map_public_product(item) for item in raw_items]
        return self._crawl_via_sitemap_if_larger(
            products,
            limit=limit,
            max_pages=max_pages,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )

    def _collect_product_sitemaps(self) -> list[str]:
        index_url = f"{self.shop_url}/sitemap.xml"
        try:
            response = self.session.get(index_url, timeout=30)
            if response.status_code != 200:
                return []
            root = ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError):
            return []
        locs = [
            (el.text or "").strip()
            for el in root.findall(".//sm:loc", SITEMAP_NS)
            if el.text and "sitemap_products" in el.text.lower()
        ]
        return locs

    def _parse_sitemap_product_handles(self, sitemap_url: str) -> list[str]:
        try:
            response = self.session.get(sitemap_url, timeout=40)
            if response.status_code != 200:
                return []
            root = ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError):
            return []
        handles: list[str] = []
        seen: set[str] = set()
        for el in root.findall(".//sm:loc", SITEMAP_NS):
            loc = (el.text or "").strip()
            match = PRODUCT_PATH.search(loc)
            if not match:
                continue
            handle = unquote(match.group(1)).strip()
            if not handle or handle in seen:
                continue
            seen.add(handle)
            handles.append(handle)
        return handles

    def _discover_handles_from_sitemaps(
        self,
        *,
        sitemap_url: str | None = None,
        max_sitemaps: int | None = None,
    ) -> list[str]:
        if sitemap_url and "sitemap_products" in sitemap_url.lower():
            return self._parse_sitemap_product_handles(sitemap_url)
        if sitemap_url and sitemap_url.rstrip("/").endswith("sitemap.xml"):
            # index — lấy tất cả product sitemaps bên trong
            pass

        smaps = self._collect_product_sitemaps()
        if max_sitemaps and max_sitemaps > 0:
            smaps = smaps[:max_sitemaps]
        handles: list[str] = []
        seen: set[str] = set()
        for smap in smaps:
            for handle in self._parse_sitemap_product_handles(smap):
                if handle in seen:
                    continue
                seen.add(handle)
                handles.append(handle)
        return handles

    def _sitemap_handle_count(self, *, max_sitemaps: int | None = None) -> int:
        return len(self._discover_handles_from_sitemaps(max_sitemaps=max_sitemaps))

    def _crawl_via_sitemap_if_larger(
        self,
        products: list[Product],
        *,
        limit: int | None,
        max_pages: int | None,
        max_workers: int,
        progress_callback: CrawlProgressCallback | None,
    ) -> list[Product]:
        """Nếu sitemap có nhiều handle hơn batch vừa lấy → crawl hết qua sitemap."""
        if limit is not None:
            return products
        sitemap_total = self._sitemap_handle_count(max_sitemaps=max_pages)
        if sitemap_total <= len(products):
            return products
        workers = max_workers
        if sitemap_total > 5000:
            workers = max(workers, 32)
        if sitemap_total > 20000:
            workers = max(workers, 64)
        return self._crawl_via_sitemap(
            limit=limit,
            max_sitemaps=max_pages,
            max_workers=workers,
            progress_callback=progress_callback,
        )

    def _fetch_product_by_handle_graphql(self, handle: str) -> Product | None:
        try:
            payload = self._graphql_request(
                PRODUCT_BY_HANDLE_GRAPHQL,
                {"handle": handle},
            )
        except requests.RequestException:
            return None
        node = (payload.get("data") or {}).get("product")
        if not isinstance(node, dict):
            return None
        return self._map_public_product(self._graphql_node_to_public_item(node))

    def _fetch_handles_batch_graphql(self, handles: list[str]) -> list[Product]:
        if not handles:
            return []
        if len(handles) == 1:
            product = self._fetch_product_by_handle_graphql(handles[0])
            return [product] if product else []

        var_defs = ", ".join(f"$h{i}: String!" for i in range(len(handles)))
        selections = "\n".join(
            f"p{i}: product(handle: $h{i}) {{\n{PRODUCT_NODE_SELECTION}\n}}"
            for i in range(len(handles))
        )
        query = f"query BatchProducts({var_defs}) {{\n{selections}\n}}"
        variables = {f"h{i}": handle for i, handle in enumerate(handles)}
        try:
            payload = self._graphql_request(query, variables)
        except requests.RequestException:
            # fallback từng handle
            out: list[Product] = []
            for handle in handles:
                product = self._fetch_product_by_handle_graphql(handle)
                if product:
                    out.append(product)
            return out

        data = payload.get("data") or {}
        products: list[Product] = []
        for i in range(len(handles)):
            node = data.get(f"p{i}")
            if isinstance(node, dict):
                products.append(
                    self._map_public_product(self._graphql_node_to_public_item(node))
                )
        return products

    def _crawl_via_sitemap(
        self,
        *,
        sitemap_url: str | None = None,
        limit: int | None = None,
        max_sitemaps: int | None = None,
        max_workers: int = 8,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        self._skip_html_description = True
        handles = self._discover_handles_from_sitemaps(
            sitemap_url=sitemap_url,
            max_sitemaps=max_sitemaps,
        )
        if not handles:
            return []
        if limit is not None:
            handles = handles[:limit]

        total = len(handles)
        if progress_callback:
            progress_callback(0, total, 0)

        batches = [
            handles[i : i + GRAPHQL_HANDLE_BATCH]
            for i in range(0, len(handles), GRAPHQL_HANDLE_BATCH)
        ]
        # UI cho tới 500 luồng; không proxy thì hạ nhẹ để tránh 429 hàng loạt.
        worker_cap = 500 if self.proxy_rotator.count > 0 else 64
        workers = max(1, min(max_workers or 1, worker_cap))
        by_handle: dict[str, Product] = {}
        done_handles = 0
        lock = threading.Lock()
        batch_timeout = 90.0

        def run_batch(batch: list[str]) -> tuple[list[str], list[Product]]:
            return batch, self._fetch_handles_batch_graphql(batch)

        def ingest(batch: list[str], batch_products: list[Product]) -> None:
            nonlocal done_handles
            for product in batch_products:
                handle = ""
                if product.url:
                    handle = urlparse(product.url).path.rstrip("/").split("/")[-1]
                if not handle and product.title:
                    continue
                if handle:
                    by_handle[handle] = product
            done_handles += len(batch)
            snapshot = [by_handle[h] for h in handles if h in by_handle]
            self._publish_partial(snapshot)
            if progress_callback:
                progress_callback(done_handles, total, len(by_handle))

        if workers <= 1:
            for batch in batches:
                _, batch_products = run_batch(batch)
                ingest(batch, batch_products)
                if self.request_delay:
                    time.sleep(self.request_delay)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(run_batch, batch): batch for batch in batches
                }
                for future in as_completed(futures):
                    batch = futures[future]
                    try:
                        _, batch_products = future.result(timeout=batch_timeout)
                    except Exception:
                        batch_products = []
                    with lock:
                        ingest(batch, batch_products)

        # Retry handle thiếu (GraphQL null / timeout / throttle).
        missing = [h for h in handles if h not in by_handle]
        if missing:
            for handle in missing:
                product = self._fetch_product_by_handle_graphql(handle)
                if product:
                    by_handle[handle] = product
                snapshot = [by_handle[h] for h in handles if h in by_handle]
                self._publish_partial(snapshot)
                if progress_callback:
                    progress_callback(total, total, len(by_handle))
                if self.request_delay:
                    time.sleep(self.request_delay)

        ordered = [by_handle[h] for h in handles if h in by_handle]
        self._publish_partial(ordered)
        return ordered

    def _crawl_graphql(
        self,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        collection_handle: str | None = None,
    ) -> list[Product]:
        self._skip_html_description = True
        products: list[Product] = []
        cursor: str | None = None
        page_count = 0
        per_page = GRAPHQL_PAGE_SIZE

        while True:
            if max_pages and page_count >= max_pages:
                break
            if limit is not None and len(products) >= limit:
                break

            if collection_handle:
                payload = self._graphql_request(
                    COLLECTION_PRODUCTS_GRAPHQL,
                    {
                        "handle": collection_handle,
                        "cursor": cursor,
                        "pageSize": per_page,
                    },
                )
                connection = (payload.get("data") or {}).get("collection")
                if not connection:
                    break
                connection = connection.get("products") or {}
            else:
                payload = self._graphql_request(
                    PRODUCTS_GRAPHQL,
                    {"cursor": cursor, "pageSize": per_page},
                )
                connection = (payload.get("data") or {}).get("products") or {}

            edges = connection.get("edges") or []
            page_info = connection.get("pageInfo") or {}
            page_count += 1

            for edge in edges:
                node = edge.get("node") if isinstance(edge, dict) else None
                if not isinstance(node, dict):
                    continue
                item = self._graphql_node_to_public_item(node)
                products.append(self._map_public_product(item))
                if limit is not None and len(products) >= limit:
                    break

            if progress_callback:
                estimated = max_pages or (
                    page_count + 1 if page_info.get("hasNextPage") else page_count
                )
                progress_callback(page_count, estimated, len(products))

            if limit is not None and len(products) >= limit:
                break
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                break
            if self.request_delay:
                time.sleep(self.request_delay)

        return products[:limit] if limit is not None else products

    @classmethod
    def _gid_to_id(cls, gid: Any) -> str:
        text = str(gid or "")
        match = GID_NUMERIC.search(text)
        return match.group(1) if match else text

    def _graphql_node_to_public_item(self, node: dict[str, Any]) -> dict[str, Any]:
        variant_edges = ((node.get("variants") or {}).get("edges")) or []
        image_edges = ((node.get("images") or {}).get("edges")) or []
        images: list[dict[str, Any]] = []
        for index, edge in enumerate(image_edges):
            url = ((edge.get("node") or {}).get("url")) if isinstance(edge, dict) else None
            if url:
                images.append({"id": index + 1, "src": url})
        featured = (node.get("featuredImage") or {}).get("url")
        if featured and not any(img.get("src") == featured for img in images):
            images.insert(0, {"id": 0, "src": featured})

        option_names: list[str] = []
        variants: list[dict[str, Any]] = []
        for edge in variant_edges:
            vnode = edge.get("node") if isinstance(edge, dict) else None
            if not isinstance(vnode, dict):
                continue
            selected = vnode.get("selectedOptions") or []
            option_values: list[str] = []
            for opt in selected:
                if not isinstance(opt, dict):
                    continue
                name = str(opt.get("name") or "").strip()
                value = str(opt.get("value") or "").strip()
                if name and name not in option_names:
                    option_names.append(name)
                option_values.append(value)
            while len(option_values) < 3:
                option_values.append(None)  # type: ignore[arg-type]
            price = (vnode.get("price") or {}).get("amount")
            compare = (vnode.get("compareAtPrice") or {}).get("amount")
            variants.append(
                {
                    "id": self._gid_to_id(vnode.get("id")),
                    "title": vnode.get("title"),
                    "sku": vnode.get("sku"),
                    "price": price,
                    "compare_at_price": compare,
                    "available": bool(vnode.get("availableForSale")),
                    "option1": option_values[0],
                    "option2": option_values[1],
                    "option3": option_values[2],
                    "image_id": None,
                }
            )

        return {
            "id": self._gid_to_id(node.get("id")),
            "title": node.get("title") or "",
            "handle": node.get("handle") or "",
            "body_html": node.get("descriptionHtml") or "",
            "vendor": node.get("vendor"),
            "product_type": node.get("productType"),
            "tags": node.get("tags") or [],
            "variants": variants,
            "images": images,
            "options": [{"name": name} for name in option_names],
        }

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

        product_type = item.get("product_type") or ""
        categories = [str(product_type).strip()] if str(product_type).strip() else []
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
            categories=categories,
            product_type=product_type or None,
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
        # PopFanPrints: HTML storefront cũng 429 — không fallback trang SP
        # (mỗi SP thiếu mô tả sẽ treo retry rất lâu → crawl “đứng” gần cuối).
        if getattr(self, "_skip_html_description", False):
            return description
        if self.use_public_api and not self._public_products_json_ok():
            return description
        try:
            response = get_with_retry(
                self.session,
                page_url,
                timeout=DEFAULT_REQUEST_TIMEOUT,
                max_retries=2,
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
