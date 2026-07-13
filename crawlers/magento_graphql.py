"""Crawl Magento / Adobe Commerce qua GraphQL (vd. Jomashop)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse
import re

import requests

from crawlers.base import BaseCrawler
from models.product import Product
from utils.description_html import format_description_output
from utils.http_session import create_http_session

CrawlProgressCallback = Callable[[int, int, int], None]

DEFAULT_PAGE_SIZE = 100
KNOWN_MAGENTO_HOSTS = frozenset({"jomashop.com"})

PRODUCTS_FIELDS = """
  id
  sku
  name
  url_key
  stock_status
  price_range {
    minimum_price {
      regular_price { value currency }
      final_price { value currency }
    }
  }
  small_image { url }
  image { url }
  thumbnail { url }
  media_gallery { url label }
  description { html }
  short_description { html }
"""

_CACHE_PATH_RE = re.compile(r"/cache/[0-9a-f]{16,}/", re.I)
_BAD_IMAGE_MARKERS = (
    "placeholder",
    "no_selection",
    "/default/default",
    "data:image",
    ".svg",
)


class MagentoGraphqlCrawler(BaseCrawler):
    source = "magento"

    def __init__(
        self,
        site_url: str,
        *,
        proxy_url: str | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        description_format: str = "html",
    ):
        parsed = urlparse(site_url if "://" in site_url else f"https://{site_url}")
        self.base_url = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        self.graphql_url = f"{self.base_url}/graphql"
        self.page_size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), 100))
        self.description_format = (description_format or "html").strip().lower()
        self.session = create_http_session(proxy_url)
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/",
            }
        )

    @classmethod
    def is_known_host(cls, host: str) -> bool:
        cleaned = (host or "").lower().removeprefix("www.")
        return cleaned in KNOWN_MAGENTO_HOSTS or any(
            cleaned.endswith(f".{known}") for known in KNOWN_MAGENTO_HOSTS
        )

    @classmethod
    def site_supports(cls, site_url: str, session: requests.Session | None = None) -> bool:
        parsed = urlparse(site_url if "://" in site_url else f"https://{site_url}")
        base = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if cls.is_known_host(parsed.netloc):
            return True
        http = session or create_http_session()
        try:
            response = http.post(
                f"{base}/graphql",
                json={"query": "{ storeConfig { store_name base_url } }"},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=12,
            )
            if response.status_code != 200:
                return False
            payload = response.json()
            store = ((payload.get("data") or {}).get("storeConfig")) or {}
            return bool(store.get("store_name") or store.get("base_url"))
        except (requests.RequestException, ValueError, TypeError):
            return False

    @staticmethod
    def path_to_url_key(path: str) -> str:
        cleaned = (path or "").strip()
        if not cleaned:
            return ""
        cleaned = cleaned.split("?", 1)[0].split("#", 1)[0]
        cleaned = cleaned.strip("/")
        if cleaned.endswith(".html"):
            cleaned = cleaned[: -len(".html")]
        return cleaned.strip("/")

    def classify_path(self, path: str) -> tuple[str, str]:
        """Return (crawl_type, url_key) for a Magento path."""
        url_key = self.path_to_url_key(path)
        if not url_key:
            return "shop", ""
        category = self.fetch_category(url_key)
        if category:
            return "category", url_key
        product = self.fetch_product_by_url_key(url_key)
        if product:
            return "product", url_key
        return "unknown", url_key

    def crawl(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        del max_workers  # GraphQL pagination is sequential; keep signature consistent.
        categories = self.list_root_categories()
        if not categories:
            raise requests.RequestException(
                "Magento: không đọc được danh mục gốc — hãy dùng link category (vd. /jewelry.html)."
            )
        products: list[Product] = []
        seen: set[str] = set()
        for category in categories:
            path = str(category.get("url_path") or "").strip()
            if not path:
                continue
            batch = self.crawl_category(
                path,
                limit=None if limit is None else max(0, limit - len(products)),
                max_pages=max_pages,
                progress_callback=None,
            )
            for product in batch:
                if product.id in seen:
                    continue
                seen.add(product.id)
                products.append(product)
                if progress_callback:
                    progress_callback(len(products), len(products), len(products))
                if limit and len(products) >= limit:
                    return products[:limit]
        return products

    def crawl_category(
        self,
        category_path: str,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        del max_workers
        url_key = self.path_to_url_key(category_path)
        if not url_key:
            return []
        category = self.fetch_category(url_key)
        category_name = str((category or {}).get("name") or url_key)

        products: list[Product] = []
        page = 1
        total_pages = 1
        total_count = 0
        while True:
            if max_pages and page > max_pages:
                break
            payload = self._fetch_products_page(
                filter_type="category",
                value=url_key,
                page=page,
            )
            items = payload.get("items") or []
            if not items:
                break
            page_info = payload.get("page_info") or {}
            total_pages = int(page_info.get("total_pages") or total_pages or 1)
            total_count = int(payload.get("total_count") or total_count or 0)
            for item in items:
                product = self._map_product(item, category_name=category_name)
                if product:
                    products.append(product)
                if limit and len(products) >= limit:
                    if progress_callback:
                        progress_callback(len(products), limit, len(products))
                    return products[:limit]
            if progress_callback:
                expected = (
                    limit
                    or total_count
                    or (total_pages * self.page_size)
                    or len(products)
                )
                progress_callback(len(products), expected, len(products))
            if page >= total_pages:
                break
            page += 1
        return products

    def crawl_product(self, url_key: str) -> Product | None:
        item = self.fetch_product_by_url_key(self.path_to_url_key(url_key))
        if not item:
            return None
        return self._map_product(item)

    def fetch_category(self, url_path: str) -> dict[str, Any] | None:
        query = """
        query CategoryByPath($path: String!) {
          categoryList(filters: {url_path: {eq: $path}}) {
            id
            name
            url_path
          }
        }
        """
        data = self._graphql(query, {"path": url_path})
        items = (data.get("categoryList") or []) if isinstance(data, dict) else []
        if not items:
            return None
        first = items[0]
        return first if isinstance(first, dict) else None

    def fetch_product_by_url_key(self, url_key: str) -> dict[str, Any] | None:
        payload = self._fetch_products_page(filter_type="product", value=url_key, page=1)
        items = payload.get("items") or []
        first = items[0] if items else None
        return first if isinstance(first, dict) else None

    def count_category_products(self, category_path: str) -> int | None:
        url_key = self.path_to_url_key(category_path)
        if not url_key:
            return None
        payload = self._fetch_products_page(filter_type="category", value=url_key, page=1)
        total = payload.get("total_count")
        try:
            return int(total) if total is not None else None
        except (TypeError, ValueError):
            return None

    def list_root_categories(self) -> list[dict[str, Any]]:
        query = """
        query RootCategories {
          categoryList(filters: {ids: {eq: "2"}}) {
            children {
              id
              name
              url_path
            }
          }
        }
        """
        data = self._graphql(query)
        roots = (data.get("categoryList") or []) if isinstance(data, dict) else []
        if not roots or not isinstance(roots[0], dict):
            return []
        children = roots[0].get("children") or []
        return [child for child in children if isinstance(child, dict) and child.get("url_path")]

    def _fetch_products_page(
        self,
        *,
        filter_type: str,
        value: str,
        page: int,
    ) -> dict[str, Any]:
        if filter_type == "category":
            query = f"""
            query ProductsByCategory($path: String!, $pageSize: Int!, $currentPage: Int!) {{
              products(
                filter: {{ category_url_path: {{ eq: $path }} }}
                pageSize: $pageSize
                currentPage: $currentPage
              ) {{
                total_count
                page_info {{ current_page page_size total_pages }}
                items {{ {PRODUCTS_FIELDS} }}
              }}
            }}
            """
            variables = {
                "path": value,
                "pageSize": self.page_size,
                "currentPage": page,
            }
        else:
            query = f"""
            query ProductByUrlKey($key: String!, $pageSize: Int!, $currentPage: Int!) {{
              products(
                filter: {{ url_key: {{ eq: $key }} }}
                pageSize: $pageSize
                currentPage: $currentPage
              ) {{
                total_count
                page_info {{ current_page page_size total_pages }}
                items {{ {PRODUCTS_FIELDS} }}
              }}
            }}
            """
            variables = {
                "key": value,
                "pageSize": self.page_size,
                "currentPage": page,
            }
        data = self._graphql(query, variables)
        products = (data.get("products") or {}) if isinstance(data, dict) else {}
        return products if isinstance(products, dict) else {}

    def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables
        try:
            response = self.session.post(self.graphql_url, json=payload, timeout=45)
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise requests.RequestException(f"Magento GraphQL lỗi: {exc}") from exc
        if body.get("errors"):
            message = body["errors"][0].get("message") if body["errors"] else "GraphQL error"
            # Soft-fail empty results for classification lookups.
            if "Cannot query field" in str(message):
                raise requests.RequestException(f"Magento GraphQL schema: {message}")
        data = body.get("data")
        return data if isinstance(data, dict) else {}

    def _map_product(
        self,
        item: dict[str, Any],
        *,
        category_name: str = "",
    ) -> Product | None:
        product_id = str(item.get("id") or item.get("sku") or "").strip()
        title = str(item.get("name") or "").strip()
        if not product_id or not title:
            return None

        price_info = ((item.get("price_range") or {}).get("minimum_price") or {})
        final_price = (price_info.get("final_price") or {})
        regular_price = (price_info.get("regular_price") or {})
        price = self._safe_float(final_price.get("value"))
        if price is None:
            price = self._safe_float(regular_price.get("value"))
        currency = str(
            final_price.get("currency") or regular_price.get("currency") or "USD"
        ).strip() or "USD"

        images: list[str] = []
        for key in ("image", "small_image", "thumbnail"):
            url = self._normalize_image_url(((item.get(key) or {}).get("url")))
            if url and url not in images:
                images.append(url)
        for media in item.get("media_gallery") or []:
            if not isinstance(media, dict):
                continue
            url = self._normalize_image_url(media.get("url"))
            if url and url not in images:
                images.append(url)

        raw_description = str((item.get("description") or {}).get("html") or "").strip()
        if not raw_description:
            raw_description = str((item.get("short_description") or {}).get("html") or "").strip()
        description = format_description_output(raw_description, self.description_format)

        url_key = str(item.get("url_key") or "").strip()
        product_url = f"{self.base_url}/{url_key}.html" if url_key else None
        stock = str(item.get("stock_status") or "").upper()
        status = "instock" if stock == "IN_STOCK" else "outofstock"
        categories = [category_name] if category_name else []

        return Product(
            source=self.source,
            id=product_id,
            title=title,
            description=description,
            price=price,
            currency=currency,
            url=product_url,
            image_urls=images,
            vendor=self._vendor_from_base(),
            sku=str(item.get("sku") or product_id),
            tags=["Magento"],
            categories=categories,
            product_type=category_name or None,
            status=status,
            variants=[],
        )

    @classmethod
    def _normalize_image_url(cls, raw: object) -> str:
        url = str(raw or "").strip()
        if not url:
            return ""
        lower = url.lower()
        if any(marker in lower for marker in _BAD_IMAGE_MARKERS):
            return ""
        # Ảnh cache Magento thường nhỏ hơn — lấy bản gốc khi có thể.
        url = _CACHE_PATH_RE.sub("/", url)
        if url.startswith("//"):
            url = f"https:{url}"
        return url

    def _vendor_from_base(self) -> str:
        host = urlparse(self.base_url).netloc.lower().removeprefix("www.")
        label = host.split(".")[0] if host else "Magento"
        return label.capitalize()

    @staticmethod
    def _safe_float(value: object) -> float | None:
        if value in (None, ""):
            return None
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return None
