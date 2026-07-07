"""Crawl web thuần HTML — danh mục + trang SP (vd. silverbene.com)."""

from __future__ import annotations

import html
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests

from models.product import Product
from utils.description_html import extract_generic_description_from_html
from utils.http_session import create_http_session

CrawlProgressCallback = Callable[[int, int, int], None]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HTML_REQUEST_DELAY = 0.0
HTML_MAX_WORKERS = 32
HTML_LISTING_WORKERS = 32
HTML_PRODUCT_MAX_WORKERS = 32
HTML_FETCH_RETRIES = 3
HTML_TINY_IMAGE_CACHE = frozenset(
    {
        "a31b2d728506bab0303781ac871c0a85",
        "bc25b597ad82f2329aa45e556edea03c",
    }
)
KNOWN_MAGENTO_HOSTS = frozenset()  # legacy, không dùng host cố định
CATEGORY_SLUG_SEP = "::"
HTML_PRODUCT_LINK_RES = (
    re.compile(r'class="product-item-link"[^>]*href="([^"]+)"', re.I),
    re.compile(r'class="woocommerce-LoopProduct-link"[^>]*href="([^"]+)"', re.I),
    re.compile(r'class="[^"]*product-card[^"]*"[^>]*href="([^"]+)"', re.I),
)
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class GenericHtmlCatalog:
    """Crawl storefront HTML thuần: trang danh mục + trang sản phẩm."""

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

    @staticmethod
    def _normalize_site_url(site_url: str) -> str:
        url = site_url.strip().rstrip("/")
        if not url.startswith("http"):
            url = f"https://{url}"
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def category_slug(path: str) -> str:
        slug = (path or "").strip().strip("/").removesuffix(".html")
        slug = re.sub(r"[^\w\s/-]", "", slug, flags=re.UNICODE)
        return slug.replace("/", CATEGORY_SLUG_SEP) or "shop"

    @staticmethod
    def path_from_category_slug(slug: str) -> str:
        if not slug or slug in {"", "shop", "Tất cả"}:
            return ""
        return slug.replace(CATEGORY_SLUG_SEP, "/") + ".html"

    @staticmethod
    def is_product_page_html(html: str) -> bool:
        return "product-info-main" in html or 'page-layout-product' in html

    @staticmethod
    def is_listing_page_html(html: str) -> bool:
        return bool(GenericHtmlCatalog._extract_product_links(html))

    @classmethod
    def site_supports(cls, base_url: str, session: requests.Session | None = None) -> bool:
        session = session or requests.Session()
        session.headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
        base = cls._normalize_site_url(base_url)
        try:
            resp = session.get(base, timeout=15)
            if resp.status_code == 200:
                if cls.is_listing_page_html(resp.text) or cls.is_product_page_html(resp.text):
                    return True
                host = urlparse(base).netloc.lower()
                for href in re.findall(r'href="([^"]+)"', resp.text, re.I)[:80]:
                    href = href.strip()
                    if not href or href.startswith("#"):
                        continue
                    if href.startswith("/"):
                        href = urljoin(base + "/", href.lstrip("/"))
                    parsed = urlparse(href)
                    if parsed.netloc and parsed.netloc.lower().removeprefix("www.") != host.removeprefix("www."):
                        continue
                    if not (parsed.path or "").lower().endswith(".html"):
                        continue
                    try:
                        sample = session.get(href, timeout=12)
                        if sample.status_code == 200 and (
                            cls.is_listing_page_html(sample.text)
                            or cls.is_product_page_html(sample.text)
                        ):
                            return True
                    except requests.RequestException:
                        continue
        except requests.RequestException:
            pass

        try:
            resp = session.get(f"{base}/sitemap.xml", timeout=15)
            if resp.status_code != 200:
                return False
            root = ET.fromstring(resp.text)
            locs = [
                (el.text or "").strip()
                for el in root.findall(".//sm:loc", SITEMAP_NS)
                if el.text
            ]
            for loc in locs[:30]:
                if not loc.lower().endswith(".html"):
                    continue
                try:
                    page = session.get(loc, timeout=12)
                    if page.status_code == 200 and (
                        cls.is_listing_page_html(page.text)
                        or cls.is_product_page_html(page.text)
                    ):
                        return True
                except requests.RequestException:
                    continue
        except (requests.RequestException, ET.ParseError):
            return False
        return False

    def crawl(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        category_slug: str | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        if category_slug and category_slug not in {"", "Tất cả"}:
            category_path = self.path_from_category_slug(category_slug)
            if not category_path:
                raise ValueError(f"Không tìm thấy danh mục: {category_slug}")
            return self.crawl_category_path(
                category_path,
                limit=limit,
                max_pages=max_pages,
                progress_callback=progress_callback,
                max_workers=max_workers,
            )

        categories = self._discover_category_paths()
        if not categories:
            raise ValueError("Không tìm thấy danh mục HTML trên trang chủ.")

        products: list[Product] = []
        seen_urls: set[str] = set()
        estimated_total = 0
        for path in categories:
            estimated_total += self.count_listing_products(path)

        processed = 0
        for path in categories:
            if limit is not None and len(products) >= limit:
                break
            batch = self.crawl_category_path(
                path,
                limit=None if limit is None else max(0, limit - len(products)),
                max_pages=max_pages,
                max_workers=max_workers,
            )
            for product in batch:
                key = product.url or product.id
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                products.append(product)
                processed += 1
                if progress_callback and (
                    processed == estimated_total
                    or processed % max(1, min(10, max(estimated_total, 1) // 20)) == 0
                ):
                    progress_callback(
                        processed,
                        max(estimated_total, processed),
                        len(products),
                    )
                if limit is not None and len(products) >= limit:
                    break

        if progress_callback and estimated_total:
            progress_callback(estimated_total, estimated_total, len(products))
        return products

    def crawl_category_path(
        self,
        category_path: str,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        listing_url = urljoin(self.site_url + "/", category_path.lstrip("/"))
        product_urls = self._collect_listing_product_urls(
            listing_url,
            max_pages=max_pages,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )
        return self._crawl_product_urls(
            product_urls,
            limit=limit,
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
        search = query.strip()
        if not search:
            raise ValueError("Thiếu từ khóa tìm kiếm.")
        listing_url = f"{self.site_url}/catalogsearch/result/?{urlencode({'q': search})}"
        product_urls = self._collect_listing_product_urls(
            listing_url,
            max_pages=max_pages,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )
        return self._crawl_product_urls(
            product_urls,
            limit=limit,
            progress_callback=progress_callback,
            max_workers=max_workers,
        )

    def crawl_product_url(self, url: str) -> Product | None:
        return self._fetch_product(url)

    def crawl_product_path(self, handle: str) -> list[Product]:
        path = handle.strip().strip("/")
        if not path.endswith(".html"):
            path = f"{path}.html"
        url = urljoin(self.site_url + "/", path)
        product = self._fetch_product(url)
        return [product] if product else []

    def count_listing_products(self, category_path: str) -> int:
        listing_url = urljoin(self.site_url + "/", category_path.lstrip("/"))
        try:
            html = self._get_text(listing_url)
        except requests.RequestException:
            return 0
        if self.is_product_page_html(html) and not self.is_listing_page_html(html):
            return 1
        return self._listing_total(html) or len(self._extract_product_links(html))

    def probe_listing_page(
        self,
        listing_path: str,
        *,
        include_subcategories: bool = True,
        max_subcategories: int = 30,
        max_workers: int = 12,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Đếm SP trên link danh mục + các link con trong menu (nếu có)."""
        path = listing_path.strip().lstrip("/")
        total = self.count_listing_products(path)
        subcategories: list[dict[str, Any]] = []
        if not include_subcategories:
            return total, subcategories

        sub_paths = [
            sub_path
            for sub_path in self.discover_subcategory_paths(path)[:max_subcategories]
            if sub_path != path
        ]
        counts = self._count_paths_parallel(sub_paths, max_workers=max_workers)
        for sub_path in sub_paths:
            count = counts.get(sub_path, 0)
            name = sub_path.removesuffix(".html").split("/")[-1].replace("-", " ").title()
            subcategories.append(
                {
                    "slug": self.category_slug(sub_path.removesuffix(".html")),
                    "name": name,
                    "count": count,
                    "path": sub_path,
                }
            )
        subcategories.sort(key=lambda item: (-int(item.get("count") or 0), item["name"].lower()))
        return total, subcategories

    def discover_subcategory_paths(self, listing_path: str) -> list[str]:
        listing_path = listing_path.strip().lstrip("/")
        listing_url = urljoin(self.site_url + "/", listing_path)
        try:
            html = self._get_text(listing_url)
        except requests.RequestException:
            return []

        section = listing_path.split("/")[0].removesuffix(".html")
        paths: list[str] = []
        seen: set[str] = {listing_path}
        host = urlparse(self.site_url).netloc.lower()

        for href in re.findall(r'href="([^"]+\.html)"', html, re.I):
            href = href.strip()
            if href.startswith("/"):
                href = urljoin(self.site_url + "/", href.lstrip("/"))
            parsed = urlparse(href)
            if parsed.netloc.lower().removeprefix("www.") != host.removeprefix("www."):
                continue
            path = (parsed.path or "").lstrip("/")
            if not path.endswith(".html") or path in seen:
                continue
            if not path.startswith(section):
                continue
            if not self._looks_like_category_path(path):
                continue
            seen.add(path)
            paths.append(path)

        paths.sort(key=lambda p: (p.count("/"), p.lower()))
        return paths

    @staticmethod
    def _looks_like_category_path(path: str) -> bool:
        lower = path.lower()
        if any(skip in lower for skip in ("customer/", "checkout", "account", "wishlist")):
            return False
        if "/category/" in lower:
            name = path.rsplit("/", 1)[-1].removesuffix(".html")
            return name.count("-") <= 5 and len(name) <= 48
        if path.count("/") == 0:
            return path.removesuffix(".html").count("-") <= 3
        return False

    def count_search_results(self, query: str) -> int | None:
        listing_url = f"{self.site_url}/catalogsearch/result/?{urlencode({'q': query.strip()})}"
        try:
            html = self._get_text(listing_url)
        except requests.RequestException:
            return None
        total = self._listing_total(html)
        if total is not None:
            return total
        links = self._extract_product_links(html)
        return len(links) if links else None

    def probe_categories(
        self,
        *,
        max_workers: int = 12,
    ) -> tuple[list[dict[str, Any]], str]:
        paths = self._discover_category_paths()
        counts = self._count_paths_parallel(paths, max_workers=max_workers)
        categories: list[dict[str, Any]] = []
        total = 0
        for path in paths:
            count = counts.get(path, 0)
            name = path.removesuffix(".html").split("/")[-1].replace("-", " ").title()
            categories.append(
                {
                    "slug": self.category_slug(path.removesuffix(".html")),
                    "name": name,
                    "count": count,
                }
            )
            total += count
        categories.sort(key=lambda item: (-int(item.get("count") or 0), item["name"].lower()))
        note = f"Danh mục HTML ({len(categories)} link · **{total}** SP ước tính)."
        return categories, note

    def _count_paths_parallel(
        self,
        paths: list[str],
        *,
        max_workers: int = 12,
    ) -> dict[str, int]:
        if not paths:
            return {}
        workers = max(1, min(max_workers, 48))
        if workers <= 1 or len(paths) == 1:
            return {path: self.count_listing_products(path) for path in paths}

        def count_one(path: str) -> tuple[str, int]:
            worker = GenericHtmlCatalog(
                self.site_url,
                proxy_url=self._proxy_url,
                request_delay=0,
            )
            return path, worker.count_listing_products(path)

        results: dict[str, int] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for path, count in executor.map(count_one, paths):
                results[path] = count
        return results

    def _discover_category_paths(self) -> list[str]:
        try:
            html = self._get_text(self.site_url)
        except requests.RequestException:
            return []

        paths: list[str] = []
        seen: set[str] = set()
        host = urlparse(self.site_url).netloc.lower()

        for href in re.findall(r'href="([^"]+\.html)"', html, re.I):
            href = href.strip()
            if href.startswith("/"):
                href = urljoin(self.site_url + "/", href.lstrip("/"))
            parsed = urlparse(href)
            if parsed.netloc.lower().removeprefix("www.") != host.removeprefix("www."):
                continue
            path = (parsed.path or "").lstrip("/")
            if not path.endswith(".html"):
                continue
            if any(
                skip in path.lower()
                for skip in (
                    "customer/",
                    "checkout",
                    "account",
                    "wishlist",
                    "contact",
                    "privacy",
                    "terms",
                )
            ):
                continue
            if path not in seen:
                seen.add(path)
                paths.append(path)

        # Ưu tiên danh mục chính (earrings, necklaces, rings, ...)
        priority = ("earrings.html", "necklaces.html", "bracelets.html", "rings.html")
        paths.sort(
            key=lambda p: (
                0 if p in priority else 1,
                0 if p.count("/") == 0 else 1,
                p.lower(),
            )
        )
        return paths[:40]

    def _fetch_listing_page_links(self, listing_url: str, page: int) -> list[str]:
        worker = GenericHtmlCatalog(
            self.site_url,
            proxy_url=self._proxy_url,
            request_delay=0,
        )
        page_url = self._listing_page_url(listing_url, page)
        try:
            html = worker._get_text(page_url)
        except requests.RequestException:
            return []
        if self.is_product_page_html(html) and not self.is_listing_page_html(html):
            return [listing_url] if page == 1 else []
        return self._extract_product_links(html)

    def _collect_listing_product_urls(
        self,
        listing_url: str,
        *,
        max_pages: int | None = None,
        max_workers: int = 1,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[str]:
        try:
            first_html = self._get_text(self._listing_page_url(listing_url, 1))
        except requests.RequestException:
            return []

        if self.is_product_page_html(first_html) and not self.is_listing_page_html(first_html):
            return [listing_url]

        batch1 = self._extract_product_links(first_html)
        if not batch1:
            return []

        toolbar_total = self._listing_total(first_html)
        per_page = len(batch1) or 60
        if toolbar_total and per_page:
            total_pages = max(1, (toolbar_total + per_page - 1) // per_page)
            if max_pages:
                total_pages = min(total_pages, max_pages)
            if total_pages <= 1:
                if progress_callback:
                    progress_callback(1, 1, len(batch1))
                return batch1

            workers = max(1, min(max_workers, HTML_LISTING_WORKERS))
            page_links: dict[int, list[str]] = {1: batch1}
            if progress_callback:
                progress_callback(1, total_pages, len(batch1))
            pages_to_fetch = list(range(2, total_pages + 1))

            if workers <= 1:
                for page in pages_to_fetch:
                    links = self._fetch_listing_page_links(listing_url, page)
                    if not links:
                        break
                    page_links[page] = links
                    if progress_callback:
                        loaded = sum(len(chunk) for chunk in page_links.values())
                        progress_callback(page, total_pages, loaded)
                    if self.request_delay:
                        time.sleep(self.request_delay)
            else:
                def fetch_page(page: int) -> tuple[int, list[str]]:
                    return page, self._fetch_listing_page_links(listing_url, page)

                with ThreadPoolExecutor(
                    max_workers=min(workers, len(pages_to_fetch))
                ) as executor:
                    for page, links in executor.map(fetch_page, pages_to_fetch):
                        page_links[page] = links
                        if progress_callback:
                            loaded = sum(len(chunk) for chunk in page_links.values())
                            progress_callback(
                                len(page_links),
                                total_pages,
                                loaded,
                            )

            urls: list[str] = []
            seen: set[str] = set()
            for page in sorted(page_links.keys()):
                for link in page_links[page]:
                    if link not in seen:
                        seen.add(link)
                        urls.append(link)
            return urls

        urls: list[str] = []
        seen: set[str] = set()
        for link in batch1:
            if link not in seen:
                seen.add(link)
                urls.append(link)
        if progress_callback:
            progress_callback(1, 1, len(urls))

        page = 2
        total_pages = page
        while page <= total_pages:
            if max_pages and page > max_pages:
                break
            links = self._fetch_listing_page_links(listing_url, page)
            if not links:
                break
            for link in links:
                if link not in seen:
                    seen.add(link)
                    urls.append(link)
            if progress_callback:
                progress_callback(page, max(total_pages, page), len(urls))
            if len(links) < per_page:
                break
            total_pages = page + 1
            page += 1
            if self.request_delay:
                time.sleep(self.request_delay)
        return urls

    def _crawl_product_urls(
        self,
        urls: list[str],
        *,
        limit: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        if limit is not None:
            urls = urls[:limit]
        if not urls:
            return []

        workers = max(1, min(max_workers, HTML_PRODUCT_MAX_WORKERS))
        products = self._crawl_product_urls_pass(
            urls,
            workers=workers,
            progress_callback=progress_callback,
        )
        fetched_urls = {product.url for product in products if product.url}
        pending = [url for url in urls if url not in fetched_urls]

        for retry_workers in (8, 4, 1):
            if not pending:
                break
            retry_batch = self._crawl_product_urls_pass(
                pending,
                workers=retry_workers,
                progress_callback=None,
            )
            for product in retry_batch:
                if product.url and product.url not in fetched_urls:
                    fetched_urls.add(product.url)
                    products.append(product)
            pending = [url for url in pending if url not in fetched_urls]

        if progress_callback:
            progress_callback(len(urls), len(urls), len(products))
        if limit is not None:
            return products[:limit]
        return products

    def _crawl_product_urls_pass(
        self,
        urls: list[str],
        *,
        workers: int,
        progress_callback: CrawlProgressCallback | None,
    ) -> list[Product]:
        total = len(urls)
        step = max(1, min(10, total // 20))
        if not urls:
            return []

        if workers <= 1:
            products: list[Product] = []
            for index, url in enumerate(urls):
                product = self._fetch_product(url)
                if product:
                    products.append(product)
                if progress_callback and (
                    index + 1 == total or (index + 1) % step == 0
                ):
                    progress_callback(index + 1, total, len(products))
            return products

        products: list[Product] = []
        lock = threading.Lock()
        processed = 0
        matched = 0

        def fetch_one(url: str) -> Product | None:
            worker = GenericHtmlCatalog(self.site_url, proxy_url=self._proxy_url)
            worker.session = create_http_session(self._proxy_url)
            worker.session.headers.update(self.session.headers)
            return worker._fetch_product(url)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch_one, url): url for url in urls}
            for future in as_completed(futures):
                processed += 1
                product = future.result()
                if product:
                    with lock:
                        products.append(product)
                        matched = len(products)
                if progress_callback and (
                    processed == total or processed % step == 0
                ):
                    progress_callback(processed, total, matched)

        return products

    def _fetch_product(self, url: str) -> Product | None:
        backoff = 0.35
        for attempt in range(HTML_FETCH_RETRIES):
            try:
                html = self._get_text(url)
            except requests.RequestException:
                if attempt + 1 < HTML_FETCH_RETRIES:
                    time.sleep(backoff * (attempt + 1))
                    continue
                return None
            if not self.is_product_page_html(html):
                return None
            return self._parse_product_page(html, url)
        return None

    def _parse_product_page(self, html: str, url: str) -> Product | None:
        title = self._extract_title(html)
        if not title:
            return None

        path = urlparse(url).path.strip("/")
        product_id = path.removesuffix(".html").split("/")[-1]
        sku_match = re.search(r'itemprop="sku"[^>]*content="([^"]+)"', html, re.I)
        sku = sku_match.group(1).strip() if sku_match else product_id

        price = self._extract_price(html)
        images = self._extract_images(html)
        description = self._extract_description(html)
        categories = self._extract_categories(html)
        vendor = urlparse(self.site_url).netloc.removeprefix("www.")

        return Product(
            source=self.source,
            id=product_id,
            title=title,
            description=description,
            price=price,
            currency="USD",
            url=url,
            image_urls=images,
            vendor=vendor,
            sku=sku,
            categories=categories,
            status="active",
        )

    def _get_text(self, url: str) -> str:
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def _listing_page_url(listing_url: str, page: int) -> str:
        if page <= 1:
            return listing_url
        parsed = urlparse(listing_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["p"] = [str(page)]
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(query, doseq=True),
                parsed.fragment,
            )
        )

    @staticmethod
    def _extract_product_links(html: str) -> list[str]:
        links: list[str] = []
        seen: set[str] = set()
        for pattern in HTML_PRODUCT_LINK_RES:
            for href in pattern.findall(html):
                href = href.strip()
                if href and href not in seen:
                    seen.add(href)
                    links.append(href)
        return links

    @staticmethod
    def _listing_total(html: str) -> int | None:
        matches = re.findall(
            r'class="toolbar-number"[^>]*>\s*([\d,]+)\s*<',
            html,
            re.I,
        )
        numbers = []
        for match in matches:
            try:
                numbers.append(int(match.replace(",", "")))
            except ValueError:
                continue
        if numbers:
            return max(numbers)

        items_match = re.search(r"([\d,]+)\s+items\b", html, re.I)
        if items_match:
            try:
                return int(items_match.group(1).replace(",", ""))
            except ValueError:
                pass
        return None

    @staticmethod
    def _extract_title(html: str) -> str:
        patterns = [
            r'<h1[^>]*class="[^"]*page-title[^"]*"[^>]*>\s*<span[^>]*>([^<]+)</span>',
            r"<h1[^>]*class=\"[^\"]*page-title[^\"]*\"[^>]*>([^<]+)</h1>",
            r"<title>([^<|]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.I | re.S)
            if match:
                title = re.sub(r"\s+", " ", match.group(1)).strip()
                if title:
                    return title
        return ""

    @staticmethod
    def _extract_price(html: str) -> float | None:
        amounts = re.findall(r'data-price-amount="([\d.]+)"', html)
        for amount in amounts:
            try:
                return round(float(amount), 2)
            except ValueError:
                continue
        match = re.search(r'class="[^"]*price[^"]*"[^>]*>\s*\$?\s*([\d,.]+)', html, re.I)
        if match:
            try:
                return round(float(match.group(1).replace(",", "")), 2)
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_images(html: str) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        patterns = [
            r"https://cdn\.silverbene\.com/media/catalog/product/[^\"'\\>\s]+\.jpe?g",
            r'class="[^"]*product-image-photo[^"]*"[^>]*src="([^"]+)"',
            r'"full":"(https://[^"]+)"',
            r'"img":"(https://[^"]+)"',
            r'itemprop="image"[^>]*content="([^"]+)"',
            r'property="og:image"[^>]*content="([^"]+)"',
        ]
        for pattern in patterns:
            for match in re.findall(pattern, html, re.I):
                url = match.strip() if isinstance(match, str) else str(match).strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                candidates.append(url)

        by_stem: dict[str, str] = {}
        for url in candidates:
            stem = url.rsplit("/", 1)[-1].lower()
            current = by_stem.get(stem)
            if current is None or GenericHtmlCatalog._image_url_rank(url) > GenericHtmlCatalog._image_url_rank(current):
                by_stem[stem] = url

        def sort_key(url: str) -> tuple[int, str]:
            stem = url.rsplit("/", 1)[-1]
            order_match = re.search(r"_(\d+)\.", stem)
            order = int(order_match.group(1)) if order_match else 0
            return order, stem

        return sorted(by_stem.values(), key=sort_key)

    @staticmethod
    def _image_url_rank(url: str) -> int:
        if any(cache in url for cache in HTML_TINY_IMAGE_CACHE):
            return 0
        if "/cache/" in url:
            return 2
        return 3

    @classmethod
    def _extract_description(cls, html_text: str) -> str | None:
        return extract_generic_description_from_html(html_text)

    @staticmethod
    def _extract_categories(html: str) -> list[str]:
        crumbs: list[str] = []
        for match in re.findall(
            r'class="[^"]*breadcrumb[^"]*"[^>]*>\s*<a[^>]*>([^<]+)</a>',
            html,
            re.I,
        ):
            name = re.sub(r"\s+", " ", match).strip()
            if name and name.lower() not in {"home", "trang chủ"}:
                crumbs.append(name)
        return crumbs

    def count_sitemap_products(self) -> int:
        try:
            resp = self.session.get(f"{self.site_url}/sitemap.xml", timeout=30)
            if resp.status_code != 200:
                return 0
            root = ET.fromstring(resp.text)
            locs = [
                (el.text or "").strip()
                for el in root.findall(".//sm:loc", SITEMAP_NS)
                if el.text
            ]
            return sum(1 for loc in locs if loc.endswith(".html") and loc.count("-") >= 4)
        except (requests.RequestException, ET.ParseError):
            return 0
