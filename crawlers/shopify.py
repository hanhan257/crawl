from __future__ import annotations

import gzip
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
SHOPIFY_SEARCH_PAGE_SIZE = 250
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
GRAPHQL_PATH = "/api/2024-10/graphql.json"
GRAPHQL_PAGE_SIZE = 50
GRAPHQL_HANDLE_BATCH = 5
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _xml_locs(root: ET.Element) -> list[str]:
    """Lấy mọi thẻ loc, kể cả sitemap không khai báo namespace chuẩn."""
    found: list[str] = []
    for el in root.iter():
        if not str(el.tag).lower().endswith("loc"):
            continue
        text = (el.text or "").strip()
        if text:
            found.append(text)
    return found


def _usable_products(products: list[Product]) -> list[Product]:
    return [product for product in products if (product.title or "").strip() and (product.url or "").strip()]
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
priceRange { minVariantPrice { amount } }
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
query ProductsPage($cursor: String, $pageSize: Int!) @inContext(country: US) {{
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
query CollectionProducts($handle: String!, $cursor: String, $pageSize: Int!) @inContext(country: US) {{
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
query ProductByHandle($handle: String!) @inContext(country: US) {{
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
        self._proxy_lock = threading.Lock()
        self._api_session_locked = False
        self._api_proxy: str | None = None
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
        sess = create_http_session(None)
        sess.headers["User-Agent"] = DEFAULT_USER_AGENT
        self._lock_api_route(sess, None)
        self._public_json_ok = None
        self._graphql_ok = None

    def _lock_api_route(self, sess: requests.Session, proxy: str | None) -> None:
        """Giữ đúng phiên đã đọc được API — luồng sau không được đổi sang proxy khác."""
        self._api_session_locked = True
        self._api_proxy = proxy
        self.session = sess
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        self._page_local.session = sess

    def _pause_for_throttle(self, response: requests.Response | None, attempt: int) -> None:
        """429 ngắn thì chờ rồi thử lại. Không ngủ quá lâu kẻo crawl đứng."""
        retry_after = ""
        if response is not None:
            retry_after = response.headers.get("Retry-After", "")
        try:
            sleep_s = float(retry_after)
        except (TypeError, ValueError):
            sleep_s = 1.2 * (attempt + 1)
        time.sleep(min(max(sleep_s, 0.8), 8.0))

    @staticmethod
    def _products_json_probe(session: requests.Session, shop_url: str) -> bool:
        for attempt in range(3):
            try:
                response = session.get(
                    f"{shop_url}/products.json",
                    params={"limit": 1, "page": 1},
                    timeout=12,
                    headers={"Accept": "application/json"},
                )
            except (requests.RequestException, ValueError):
                return False
            if response.status_code == 429 and attempt < 2:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    sleep_s = float(retry_after)
                except (TypeError, ValueError):
                    sleep_s = 1.2 * (attempt + 1)
                time.sleep(min(max(sleep_s, 0.8), 8.0))
                continue
            if response.status_code != 200:
                return False
            try:
                data = response.json()
            except ValueError:
                return False
            products = data.get("products") if isinstance(data, dict) else None
            # 200 + danh sách rỗng thường là throttle, không phải shop hết hàng.
            return isinstance(products, list) and len(products) > 0
        return False

    def _page_session(self, *, rotate: bool = False) -> requests.Session:
        if not rotate and hasattr(self._page_local, "session"):
            return self._page_local.session
        if self._api_session_locked and not rotate:
            proxy = self._api_proxy
        elif rotate and self.proxy_rotator.count > 1:
            proxy = self.proxy_rotator.rotate()
        else:
            proxy = self.proxy_rotator.current()
        sess = create_http_session(proxy)
        sess.headers.update(self.session.headers)
        sess.headers["User-Agent"] = DEFAULT_USER_AGENT
        if not rotate:
            self._page_local.session = sess
        return sess

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
        self._skip_html_description = True
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
        products = self._continue_truncated_catalog(
            products,
            limit=limit,
            max_pages=max_pages,
            max_workers=max_workers,
            progress_callback=progress_callback,
            collection_handle=handle,
        )
        for product in products:
            if handle not in product.categories:
                product.categories = [handle]
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
            return self._crawl_search_from_catalog(
                search,
                limit=limit,
                max_pages=max_pages,
                max_workers=max_workers,
                progress_callback=progress_callback,
            )

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
        html = self._fetch_search_html({"q": query.strip(), "type": "product"})
        if html:
            match = SHOPIFY_SEARCH_COUNT_PATTERN.search(html)
            if match:
                return int(match.group(1).replace(",", ""))
        return self._count_search_in_catalog(query)

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

            html = self._fetch_search_html(params)
            if html is None:
                break

            page_handles = self._extract_search_handles_from_html(html)
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

    def _fetch_search_html(self, params: dict[str, str | int]) -> str | None:
        """Một request. Trang /search hay 429 — không retry dài, để crawl chuyển sang catalog."""
        try:
            response = self.session.get(
                f"{self.shop_url}/search",
                params=params,
                timeout=(8, 20),
                headers={"Accept": "text/html"},
            )
        except requests.RequestException:
            return None
        if response.status_code != 200 or not response.text:
            return None
        return response.text

    def _crawl_search_from_catalog(
        self,
        query: str,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int = 8,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        """Lọc /products.json theo từ khóa khi trang /search không trả HTML."""
        tokens = self._search_tokens(query)
        if not tokens:
            return []
        if not self._products_json_probe(self.session, self.shop_url):
            direct = create_http_session(None)
            direct.headers["User-Agent"] = DEFAULT_USER_AGENT
            if self._products_json_probe(direct, self.shop_url):
                self._switch_to_direct_session()
        self._skip_html_description = True
        total_pages = self._search_catalog_pages(max_pages)
        if total_pages <= 0:
            return []
        if progress_callback:
            progress_callback(0, total_pages, 0)

        products: dict[str, Product] = {}
        lock = threading.Lock()
        done = 0
        workers = min(max(1, max_workers), 12, total_pages)

        def fetch_page(page_num: int) -> list[dict[str, Any]]:
            for attempt in range(3):
                try:
                    return self._fetch_public_page(page_num)
                except requests.RequestException:
                    if attempt == 2:
                        return []
                    time.sleep(0.4 * (attempt + 1))
            return []

        def absorb(batch: list[dict[str, Any]]) -> None:
            nonlocal done
            matched: list[Product] = []
            for item in batch:
                if not self._item_matches_search(item, tokens):
                    continue
                product = self._map_public_product(item)
                if product.id:
                    matched.append(product)
            with lock:
                for product in matched:
                    products.setdefault(product.id, product)
                done += 1
                if progress_callback and (done == total_pages or done % 2 == 0):
                    progress_callback(done, total_pages, len(products))

        if workers <= 1:
            for page_num in range(1, total_pages + 1):
                absorb(fetch_page(page_num))
                if limit and len(products) >= limit:
                    break
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(fetch_page, page_num)
                    for page_num in range(1, total_pages + 1)
                ]
                for future in as_completed(futures):
                    absorb(future.result())

        ordered = list(products.values())
        if limit and limit > 0:
            ordered = ordered[:limit]
        if progress_callback and total_pages:
            progress_callback(total_pages, total_pages, len(ordered))
        return ordered

    def _count_search_in_catalog(self, query: str) -> int | None:
        """Đếm SP khớp từ khóa trên /products.json khi trang /search không mở được."""
        tokens = self._search_tokens(query)
        if not tokens:
            return None
        if not self._products_json_probe(self.session, self.shop_url):
            direct = create_http_session(None)
            direct.headers["User-Agent"] = DEFAULT_USER_AGENT
            if not self._products_json_probe(direct, self.shop_url):
                return None
            self._switch_to_direct_session()
        total_pages = self._search_catalog_pages(None)
        if total_pages <= 0:
            return None

        matched: set[str] = set()
        saw_page = False
        lock = threading.Lock()
        workers = min(8, total_pages)

        def fetch_page(page_num: int) -> list[dict[str, Any]]:
            for attempt in range(3):
                try:
                    return self._fetch_public_page(page_num)
                except requests.RequestException:
                    if attempt == 2:
                        return []
                    time.sleep(0.4 * (attempt + 1))
            return []

        def absorb(batch: list[dict[str, Any]]) -> None:
            nonlocal saw_page
            ids: list[str] = []
            if batch:
                with lock:
                    saw_page = True
            for item in batch:
                if not self._item_matches_search(item, tokens):
                    continue
                product_id = str(item.get("id") or item.get("handle") or "").strip()
                if product_id:
                    ids.append(product_id)
            if not ids:
                return
            with lock:
                matched.update(ids)

        if workers <= 1:
            for page_num in range(1, total_pages + 1):
                absorb(fetch_page(page_num))
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(fetch_page, page_num)
                    for page_num in range(1, total_pages + 1)
                ]
                for future in as_completed(futures):
                    absorb(future.result())
        if not saw_page:
            return None
        return len(matched)

    def _search_catalog_pages(self, max_pages: int | None) -> int:
        published = 0
        try:
            response = self.session.get(f"{self.shop_url}/meta.json", timeout=15)
            if response.status_code == 200:
                published = int(response.json().get("published_products_count") or 0)
        except (requests.RequestException, TypeError, ValueError):
            published = 0
        if published > 0:
            pages = max(1, (published + SHOPIFY_SEARCH_PAGE_SIZE - 1) // SHOPIFY_SEARCH_PAGE_SIZE)
        else:
            pages = 40
        if max_pages and max_pages > 0:
            pages = min(pages, max_pages)
        return pages

    @staticmethod
    def _search_tokens(query: str) -> list[str]:
        tokens: list[str] = []
        for part in re.split(r"\s+", query.strip().lower()):
            if len(part) >= 2:
                tokens.append(part)
        return tokens

    @staticmethod
    def _item_matches_search(item: dict[str, Any], tokens: list[str]) -> bool:
        tags = item.get("tags")
        if isinstance(tags, list):
            tag_text = " ".join(str(tag) for tag in tags)
        else:
            tag_text = str(tags or "")
        blob = " ".join(
            (
                str(item.get("title") or ""),
                str(item.get("product_type") or ""),
                str(item.get("vendor") or ""),
                tag_text,
            )
        ).lower()
        return all(token in blob for token in tokens)

    @staticmethod
    def _extract_search_handles_from_html(html: str) -> list[str]:
        handles: list[str] = []
        seen: set[str] = set()

        def add(handle: str) -> None:
            handle = unquote(handle).strip().strip("/")
            if not handle or handle in seen or handle.lower() in {"json", "js"}:
                return
            if handle.endswith(".json") or handle.endswith(".js"):
                return
            seen.add(handle)
            handles.append(handle)

        for match in SHOPIFY_SEARCH_HANDLE_PATTERN.finditer(html):
            add(match.group(1))
        for match in PRODUCT_PATH.finditer(html):
            add(match.group(1))
        return handles

    def _fetch_collection_page(self, handle: str, page: int) -> list[dict[str, Any]]:
        response = self._page_session().get(
            f"{self.shop_url}/collections/{handle}/products.json",
            params={"limit": 250, "page": page},
            timeout=(8, 20),
            headers={"Accept": "application/json"},
        )
        if response.status_code == 429:
            raise requests.HTTPError(
                "Shopify collection products.json 429",
                response=response,
            )
        response.raise_for_status()
        batch = response.json().get("products", [])
        return batch if isinstance(batch, list) else []

    def _public_products_json_ok(self) -> bool:
        """True nếu /products.json còn dùng được (một số shop Shopify chặn 429)."""
        if getattr(self, "_public_json_ok", None) is True and self._api_session_locked:
            return True
        if self._products_json_probe(self.session, self.shop_url):
            self._lock_api_route(self.session, self.proxy_rotator.current())
            self._public_json_ok = True
            return True
        if self.proxy_rotator.has_proxy:
            direct = create_http_session(None)
            direct.headers["User-Agent"] = DEFAULT_USER_AGENT
            if self._products_json_probe(direct, self.shop_url):
                self.proxy_rotator = ProxyRotator(None)
                self._lock_api_route(direct, None)
                self._public_json_ok = True
                return True
        proxies: list[str] = []
        seen: set[str] = set()
        for _ in range(min(12, self.proxy_rotator.count)):
            proxy = self.proxy_rotator.rotate()
            if not proxy or proxy in seen:
                break
            seen.add(proxy)
            proxies.append(proxy)

        def _probe_proxy(proxy: str) -> tuple[str, requests.Session] | None:
            sess = create_http_session(proxy)
            sess.headers["User-Agent"] = DEFAULT_USER_AGENT
            if self._products_json_probe(sess, self.shop_url):
                return proxy, sess
            return None

        if proxies:
            pool = ThreadPoolExecutor(max_workers=min(6, len(proxies)))
            futures = [pool.submit(_probe_proxy, proxy) for proxy in proxies]
            try:
                for future in as_completed(futures):
                    found = future.result()
                    if not found:
                        continue
                    proxy, sess = found
                    self._lock_api_route(sess, proxy)
                    self._public_json_ok = True
                    return True
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        return False

    def _graphql_available(self) -> bool:
        if getattr(self, "_graphql_ok", None) is True:
            return True
        try:
            payload = self._graphql_request(
                "query { products(first: 1) { edges { node { handle } } } }",
                max_retries=1,
            )
            ok = bool(payload.get("data", {}).get("products"))
        except (requests.RequestException, ValueError, KeyError):
            ok = False
        if ok:
            self._graphql_ok = True
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
        """Một trang catalog API — 250 SP. 429 thì chờ ngắn rồi thử lại.

        Trang vượt cửa sổ ~100 (HTTP 400) là hết lượt products.json, không phải lỗi shop.
        """
        response: requests.Response | None = None
        for attempt in range(4):
            response = self._page_session().get(
                f"{self.shop_url}/products.json",
                params={"limit": 250, "page": page},
                timeout=(8, 25),
                headers={"Accept": "application/json"},
            )
            if response.status_code == 429 and attempt < 3:
                self._pause_for_throttle(response, attempt)
                continue
            break
        if response is None:
            return []
        if response.status_code == 429:
            raise requests.HTTPError(
                "Shopify products.json 429",
                response=response,
            )
        if response.status_code == 400:
            return []
        response.raise_for_status()
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
        """Shopify có API: lấy thẳng /products.json (250 SP/trang), không đi GraphQL trước."""
        self._skip_html_description = True
        if getattr(self, "_public_json_ok", None) is not True:
            self._public_json_ok = None

        if progress_callback:
            progress_callback(0, 1, 0)

        api_products: list[Product] = []
        if self._public_products_json_ok():
            try:
                api_products = self._crawl_products_json_api(
                    limit=limit,
                    max_pages=max_pages,
                    max_workers=max_workers,
                    progress_callback=progress_callback,
                )
            except requests.RequestException:
                api_products = []
                self._public_json_ok = None
                self._api_session_locked = False
        api_products = _usable_products(api_products)
        # API ra sản phẩm đủ title/link thì dùng. Không định dạng được thì chuyển sitemap.
        if api_products:
            return api_products

        products = self._crawl_via_product_js(
            limit=limit,
            max_sitemaps=None,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )
        products = _usable_products(products)
        if products:
            return products

        if self._graphql_available():
            try:
                return self._crawl_graphql(
                    limit=limit,
                    max_pages=max_pages,
                    progress_callback=progress_callback,
                )
            except requests.RequestException:
                pass

        raise requests.RequestException(
            "Không định dạng được sản phẩm từ API Shopify. Đã kiểm tra sitemap "
            "sản phẩm (sitemap_products_*.xml) nhưng không crawl được. "
            "Bật **Proxy**, giảm luồng crawl (8–32), đợi vài phút rồi thử lại."
        )

    def _crawl_products_json_api(
        self,
        *,
        limit: int | None,
        max_pages: int | None,
        max_workers: int,
        progress_callback: CrawlProgressCallback | None,
    ) -> list[Product]:
        per_page = 250
        first_batch = self._fetch_public_page(1)
        if limit is None and len(first_batch) < per_page:
            completed = self._complete_short_catalog(
                first_batch,
                max_pages=max_pages,
            )
            if completed is not None:
                return completed
        estimated_pages = max_pages
        if estimated_pages is None and limit is not None:
            estimated_pages = max(1, (limit + per_page - 1) // per_page)
        elif estimated_pages is None and len(first_batch) >= per_page:
            estimated_pages = None

        # Quá 4 trang products.json cùng lúc dễ 429 cả shop (thesportsmerch ~25k SP).
        list_workers = max(1, min(max_workers or 1, 4, API_LIST_WAVE_SIZE))
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
        self._publish_partial(products)
        # products.json hay dừng sau trang đầu (vd. 20 SP) dù shop còn hàng.
        # Shop lớn (đủ trang 250) vẫn đối chiếu sitemap trước khi kết thúc.
        return self._continue_truncated_catalog(
            products,
            limit=limit,
            max_pages=max_pages,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )

    def _complete_short_catalog(
        self,
        first_batch: list[dict[str, Any]],
        *,
        max_pages: int | None,
    ) -> list[Product] | None:
        """Trang products.json ngắn hơn sitemap thì đó là response bị cắt, không phải hết shop."""
        handles = self._discover_handles_from_sitemaps(max_sitemaps=max_pages)
        if handles and len(first_batch) >= len(handles):
            return None
        if not handles and len(first_batch) >= 30:
            return None
        target = len(handles) or 250
        fuller = self._longest_products_json(stop_at=target)
        raw_items = fuller if len(fuller) > len(first_batch) else first_batch
        by_handle = self._products_by_handle(raw_items)
        if not handles:
            if len(raw_items) > len(first_batch):
                products = list(by_handle.values())
                self._publish_partial(products)
                return products
            return None
        # Còn thiếu so với sitemap. Không chốt 20 SP trang đầu rồi xuất file.
        if len(by_handle) < len(handles):
            return None
        ordered = [by_handle[h] for h in handles if h in by_handle]
        self._publish_partial(ordered)
        return ordered

    def _published_product_count(self) -> int | None:
        try:
            response = self._page_session().get(
                f"{self.shop_url}/meta.json",
                timeout=(8, 15),
                headers={"Accept": "application/json"},
            )
            if response.status_code != 200:
                return None
            count = int(response.json().get("published_products_count") or 0)
        except (requests.RequestException, ValueError, TypeError):
            return None
        return count or None

    def _collection_product_count(self, handle: str) -> int | None:
        try:
            response = self._page_session().get(
                f"{self.shop_url}/collections/{handle}.json",
                timeout=(8, 15),
                headers={"Accept": "application/json"},
            )
            if response.status_code != 200:
                return None
            raw = (response.json().get("collection") or {}).get("products_count")
            if raw is None:
                return None
            return int(raw)
        except (requests.RequestException, ValueError, TypeError):
            return None

    def _continue_truncated_catalog(
        self,
        products: list[Product],
        *,
        limit: int | None,
        max_pages: int | None,
        max_workers: int,
        progress_callback: CrawlProgressCallback | None,
        collection_handle: str | None = None,
    ) -> list[Product]:
        """products.json trả 1 trang ngắn (thường 20) rồi hết. Shop còn SP thì crawl tiếp."""
        if limit is not None:
            return products
        handles: list[str] = []
        if collection_handle:
            expected = self._collection_product_count(collection_handle) or 0
        else:
            handles = self._discover_handles_from_sitemaps(max_sitemaps=max_pages)
            expected = len(handles)
            published = self._published_product_count() or 0
            if published > expected:
                expected = published
        gap = expected - len(products)
        if gap <= 0:
            return products
        # Lệch vài SP ở trang cuối — catalog đã hết, không crawl lại cả shop.
        if len(products) >= 250 and len(products) % 250 != 0 and gap < 15:
            return products
        if handles and len(handles) > len(products):
            # Chỉ lấy handle còn thiếu. Crawl lại cả sitemap bằng GraphQL
            # dễ 429 và làm rơi catalog vừa lấy được.
            by_handle = {}
            for product in products:
                handle = ""
                if product.url:
                    handle = urlparse(product.url).path.rstrip("/").split("/")[-1]
                if handle:
                    by_handle[handle] = product
            self._fill_missing_handles(
                by_handle,
                handles,
                max_workers=min(max_workers or 1, 6),
                progress_callback=progress_callback,
            )
            ordered = [by_handle[h] for h in handles if h in by_handle]
            if len(ordered) > len(products):
                products = ordered
            if len(products) >= expected:
                self._publish_partial(products)
                return products
        try:
            fuller = self._crawl_graphql(
                limit=None,
                max_pages=max_pages,
                progress_callback=progress_callback,
                collection_handle=collection_handle,
            )
        except requests.RequestException:
            return products
        if len(fuller) > len(products):
            return fuller
        return products

    def _read_products_page(self, session: requests.Session) -> list[dict[str, Any]] | None:
        try:
            response = session.get(
                f"{self.shop_url}/products.json",
                params={"limit": 250, "page": 1},
                timeout=(8, 20),
                headers={"Accept": "application/json"},
            )
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        batch = data.get("products") if isinstance(data, dict) else None
        if not isinstance(batch, list):
            return None
        return batch

    def _longest_products_json(self, *, stop_at: int) -> list[dict[str, Any]]:
        """Đổi route cho tới khi products.json dài bằng sitemap, không giữ trang 2 SP."""
        sessions: list[tuple[requests.Session, str | None]] = []
        direct = create_http_session(None)
        direct.headers["User-Agent"] = DEFAULT_USER_AGENT
        sessions.append((direct, None))
        seen: set[str] = set()
        if self._api_proxy:
            seen.add(self._api_proxy)
        for _ in range(min(8, self.proxy_rotator.count)):
            with self._proxy_lock:
                proxy = self.proxy_rotator.rotate()
            if not proxy or proxy in seen:
                break
            seen.add(proxy)
            sess = create_http_session(proxy)
            sess.headers["User-Agent"] = DEFAULT_USER_AGENT
            sessions.append((sess, proxy))

        best: list[dict[str, Any]] = []
        best_pair: tuple[requests.Session, str | None] | None = None
        pool = ThreadPoolExecutor(max_workers=min(4, len(sessions)))
        futures = {
            pool.submit(self._read_products_page, sess): (sess, proxy)
            for sess, proxy in sessions
        }
        try:
            for future in as_completed(futures):
                batch = future.result()
                if not batch or len(batch) <= len(best):
                    continue
                best = batch
                best_pair = futures[future]
                if len(best) >= stop_at:
                    break
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if best_pair is not None:
            sess, proxy = best_pair
            self._lock_api_route(sess, proxy)
        return best

    def _products_by_handle(self, raw_items: list[dict[str, Any]]) -> dict[str, Product]:
        by_handle: dict[str, Product] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            handle = str(item.get("handle") or "").strip()
            product = self._map_public_product(item)
            if not handle and product.url:
                handle = urlparse(product.url).path.rstrip("/").split("/")[-1]
            if handle:
                by_handle[handle] = product
        return by_handle

    def _next_spread_session(self) -> requests.Session:
        with self._proxy_lock:
            if self.proxy_rotator.count > 1:
                proxy = self.proxy_rotator.rotate()
            else:
                proxy = self._api_proxy if self._api_session_locked else self.proxy_rotator.current()
        sess = create_http_session(proxy)
        sess.headers["User-Agent"] = DEFAULT_USER_AGENT
        return sess

    def _fetch_product_by_handle(self, handle: str) -> Product | None:
        """Một SP: /products/{handle}.json cùng dạng products.json, rồi .js nếu bị chặn."""
        for attempt in range(3):
            sess = self._page_session() if attempt == 0 else self._next_spread_session()
            for suffix in (".json", ".js"):
                try:
                    response = sess.get(
                        f"{self.shop_url}/products/{handle}{suffix}",
                        timeout=(8, 15),
                        headers={"Accept": "application/json"},
                    )
                except requests.RequestException:
                    continue
                if response.status_code == 429:
                    self._pause_for_throttle(response, attempt)
                    break
                if response.status_code != 200:
                    continue
                try:
                    payload = response.json()
                except ValueError:
                    continue
                if suffix == ".json" and isinstance(payload, dict):
                    item = payload.get("product")
                    if isinstance(item, dict) and item.get("title"):
                        return self._map_public_product(item)
                    continue
                product = self._product_from_js_payload(payload)
                if product is not None:
                    return product
        return None

    def _fill_missing_handles(
        self,
        by_handle: dict[str, Product],
        handles: list[str],
        *,
        max_workers: int,
        progress_callback: CrawlProgressCallback | None,
    ) -> None:
        missing = [handle for handle in handles if handle not in by_handle]
        if not missing:
            return
        workers = max(1, min(8, max_workers or 1, len(missing)))
        total = len(handles)

        def report() -> None:
            if progress_callback:
                progress_callback(len(by_handle), total, len(by_handle))

        def load(handle: str) -> tuple[str, Product | None]:
            return handle, self._fetch_product_by_handle(handle)

        for _round in range(2):
            if not missing:
                break
            if workers <= 1:
                for handle in missing:
                    _, product = load(handle)
                    if product:
                        by_handle[handle] = product
                    report()
            else:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = [executor.submit(load, handle) for handle in missing]
                    for future in as_completed(futures):
                        handle, product = future.result()
                        if product:
                            by_handle[handle] = product
                        report()
            missing = [handle for handle in handles if handle not in by_handle]
            self._api_session_locked = False

    @staticmethod
    def _xml_root(raw: bytes) -> ET.Element | None:
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except OSError:
                return None
        try:
            return ET.fromstring(raw)
        except ET.ParseError:
            return None

    def _http_get(self, url: str, *, timeout: int = 20) -> requests.Response | None:
        """GET tới khi có 200. IP server bị 429 thì thử direct rồi vài proxy."""
        sessions: list[tuple[requests.Session, str | None]] = [
            (self.session, self._api_proxy if self._api_session_locked else self.proxy_rotator.current())
        ]
        if self.proxy_rotator.has_proxy or self._api_proxy:
            direct = create_http_session(None)
            direct.headers["User-Agent"] = DEFAULT_USER_AGENT
            sessions.append((direct, None))
        seen: set[str] = set()
        for _ in range(min(4, self.proxy_rotator.count)):
            proxy = self.proxy_rotator.rotate()
            if not proxy or proxy in seen:
                break
            seen.add(proxy)
            sess = create_http_session(proxy)
            sess.headers["User-Agent"] = DEFAULT_USER_AGENT
            sessions.append((sess, proxy))
        for sess, proxy in sessions:
            try:
                response = sess.get(url, timeout=timeout)
            except requests.RequestException:
                continue
            if response.status_code == 429:
                self._pause_for_throttle(response, 0)
                try:
                    response = sess.get(url, timeout=timeout)
                except requests.RequestException:
                    continue
            if response.status_code == 200 and response.content:
                if not self._api_session_locked:
                    self._lock_api_route(sess, proxy)
                return response
        return None

    def _collect_product_sitemaps(self) -> list[str]:
        index_url = f"{self.shop_url}/sitemap.xml"
        response = self._http_get(index_url, timeout=20)
        if response is None:
            return []
        root = self._xml_root(response.content)
        if root is None:
            return []
        locs = [
            loc
            for loc in _xml_locs(root)
            if "sitemap_products" in loc.lower() or "product-sitemap" in loc.lower()
        ]
        return locs

    def _parse_sitemap_product_handles(self, sitemap_url: str) -> list[str]:
        response = self._http_get(sitemap_url, timeout=25)
        if response is None:
            return []
        root = self._xml_root(response.content)
        if root is None:
            return []
        handles: list[str] = []
        seen: set[str] = set()
        for loc in _xml_locs(root):
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
        # ?from=&to= nằm ở query, không được làm tool bỏ file này để lấy cả shop.
        from utils.sitemap_mode import product_sitemap_filename

        if sitemap_url and product_sitemap_filename(sitemap_url):
            return self._parse_sitemap_product_handles(sitemap_url)
        sitemap_path = (urlparse(sitemap_url).path or "").lower().rstrip("/") if sitemap_url else ""
        if sitemap_url and sitemap_path.endswith("sitemap.xml"):
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

    def _fetch_product_js(self, handle: str) -> Product | None:
        """products.json bị 429 thì /products/{handle}.js vẫn trả đủ title, giá, ảnh."""
        return self._fetch_product_by_handle(handle)

    def _product_from_js_payload(self, item: Any) -> Product | None:
        if not isinstance(item, dict) or not item.get("title"):
            return None
        images = item.get("images") or []
        if images and isinstance(images[0], str):
            item["images"] = [
                {"src": src if str(src).startswith("http") else f"https:{src}"}
                for src in images
            ]
        variants: list[dict[str, Any]] = []
        for variant in item.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            variant = dict(variant)
            price = variant.get("price")
            if isinstance(price, int):
                variant["price"] = f"{price / 100:.2f}"
            compare = variant.get("compare_at_price")
            if isinstance(compare, int) and compare:
                variant["compare_at_price"] = f"{compare / 100:.2f}"
            variants.append(variant)
        item["variants"] = variants
        if not item.get("body_html") and item.get("description"):
            item["body_html"] = item.get("description")
        if not item.get("product_type"):
            item["product_type"] = item.get("type") or ""
        return self._map_public_product(item)

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
        product = self._map_public_product(self._graphql_node_to_public_item(node))
        if self._has_selling_price(product):
            return product
        return self._fetch_product_by_handle(handle) or product

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
            if not isinstance(node, dict):
                continue
            product = self._map_public_product(self._graphql_node_to_public_item(node))
            if not self._has_selling_price(product):
                product = self._fetch_product_by_handle(handles[i]) or product
            products.append(product)
        return products

    def _crawl_via_product_js(
        self,
        *,
        limit: int | None = None,
        max_sitemaps: int | None = None,
        max_workers: int = 8,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        """API danh sách bị cắt/429: đọc từng handle, ít luồng để khỏi chỉ sót vài SP."""
        self._skip_html_description = True
        handles = self._discover_handles_from_sitemaps(max_sitemaps=max_sitemaps)
        if not handles:
            time.sleep(2.0)
            handles = self._discover_handles_from_sitemaps(max_sitemaps=max_sitemaps)
        if not handles:
            return []
        if limit is not None:
            handles = handles[:limit]
        if progress_callback:
            progress_callback(0, len(handles), 0)
        by_handle: dict[str, Product] = {}
        self._fill_missing_handles(
            by_handle,
            handles,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )
        ordered = [by_handle[h] for h in handles if h in by_handle]
        self._publish_partial(ordered)
        return ordered

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
                if product is None:
                    product = self._fetch_product_js(handle)
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
        fallback_price = (
            ((node.get("priceRange") or {}).get("minVariantPrice") or {}).get("amount")
        )
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
            price_node = vnode.get("price") if isinstance(vnode.get("price"), dict) else {}
            compare_node = (
                vnode.get("compareAtPrice")
                if isinstance(vnode.get("compareAtPrice"), dict)
                else {}
            )
            price = price_node.get("amount") or fallback_price
            compare = compare_node.get("amount")
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

    def _has_selling_price(self, product: Product) -> bool:
        if (self._safe_float(product.price) or 0) > 0:
            return True
        for variant in product.variants or []:
            if (self._safe_float(variant.get("price")) or 0) > 0:
                return True
        return False
