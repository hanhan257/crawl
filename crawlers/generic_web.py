from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from crawlers.base import BaseCrawler
from models.product import Product
from utils.http_session import create_http_session

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)
GENERIC_PRODUCT_PATH = re.compile(r"/product/([^/?#]+)", re.I)
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class GenericWebCrawler(BaseCrawler):
    """Crawl Next.js / web thuần qua sitemap + JSON productData (vd. keeptee.com)."""

    source = "generic-web"

    def __init__(
        self,
        site_url: str,
        *,
        proxy_url: str | None = None,
        request_delay: float = 0.35,
    ):
        self.site_url = self._normalize_site_url(site_url)
        self.request_delay = request_delay
        self.session = create_http_session(proxy_url)
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        self._build_id: str | None = None

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
    ) -> list[Product]:
        product_urls = self._discover_product_urls(max_sitemaps=max_pages)
        return self._crawl_urls(
            product_urls,
            limit=limit,
            category_slug=category_slug,
        )

    def crawl_product(self, product_id: str) -> Product | None:
        product_id = product_id.strip().strip("/")
        url = f"{self.site_url}/product/{product_id}"
        return self._fetch_product(url, product_id)

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
    ) -> tuple[list[dict[str, Any]], str]:
        """Lấy danh mục từ field category của SP mẫu (web thuần không có API category)."""
        product_urls = self._discover_product_urls(max_sitemaps=2)[:sample_size]
        counts: dict[str, int] = {}
        labels: dict[str, str] = {}

        for index, url in enumerate(product_urls):
            match = GENERIC_PRODUCT_PATH.search(urlparse(url).path or "")
            if not match:
                continue
            category = self._fetch_product_category(url, match.group(1))
            if not category:
                continue
            slug = self.category_slug(category)
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
    ) -> list[Product]:
        products: list[Product] = []
        for index, url in enumerate(urls):
            if limit is not None and len(products) >= limit:
                break
            match = GENERIC_PRODUCT_PATH.search(urlparse(url).path or "")
            if not match:
                continue
            product = self._fetch_product(url, match.group(1))
            if not product:
                continue
            if category_slug and category_slug not in {"", "Tất cả"}:
                if not any(self.category_slug(cat) == category_slug for cat in product.categories):
                    continue
            products.append(product)
            if self.request_delay and index + 1 < len(urls):
                time.sleep(self.request_delay)
        return products

    def _fetch_product(self, url: str, product_id: str) -> Product | None:
        build_id = self._get_build_id()
        if build_id:
            api_url = f"{self.site_url}/_next/data/{build_id}/product/{product_id}.json"
            try:
                resp = self.session.get(api_url, timeout=25)
                if resp.status_code == 200:
                    payload = resp.json()
                    product_data = (
                        payload.get("pageProps", {}).get("productData")
                        if isinstance(payload, dict)
                        else None
                    )
                    if isinstance(product_data, dict):
                        return self._product_from_payload(product_data, url=url)
            except (requests.RequestException, ValueError):
                pass

        try:
            resp = self.session.get(url, timeout=25)
            if resp.status_code != 200:
                return None
            match = NEXT_DATA_PATTERN.search(resp.text)
            if not match:
                return None
            payload = json.loads(match.group(1))
            product_data = payload.get("props", {}).get("pageProps", {}).get("productData")
            if isinstance(product_data, dict):
                return self._product_from_payload(product_data, url=url)
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            return None
        return None

    def _product_from_payload(self, data: dict[str, Any], url: str) -> Product | None:
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
        brand = str(data.get("brand") or "").strip() or None
        description = data.get("description")
        if description is not None:
            description = str(description).strip() or None

        return Product(
            source=self.source,
            id=product_id,
            title=title,
            description=description,
            price=price,
            url=url,
            image_urls=images,
            vendor=brand,
            sku=product_id,
            tags=tags,
            categories=[category] if category else [],
            product_type=str(data.get("template") or "").strip() or None,
            status="active",
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
                if resp.status_code == 200 and "sitemap-product" in resp.text.lower():
                    return True
            except requests.RequestException:
                continue

        try:
            resp = session.get(base, timeout=15)
            if resp.status_code != 200:
                return False
            if "__NEXT_DATA__" not in resp.text:
                return False
            sample = session.get(f"{base}/products", timeout=15)
            return sample.status_code == 200 and (
                "/product/" in sample.text or "productData" in sample.text
            )
        except requests.RequestException:
            return False
