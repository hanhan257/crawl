"""Crawl Fine Art America / Pixels — sitemap + HTML /featured/*.html (đa luồng)."""

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
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse, unquote

import requests

from crawlers.base import BaseCrawler
from models.product import Product
from utils.description_html import (
    format_description_output,
    normalize_description_html,
)
from utils.http_session import ProxyRotator, create_http_session, reset_session

CrawlProgressCallback = Callable[[int, int, int], None]
PartialProductsCallback = Callable[[list[Product]], None]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
FEATURED_PATH = re.compile(r"/featured/([^/?#]+\.html)", re.I)
PRICE_LINE = re.compile(
    r"(Canvas Print|Framed Print|Art Print|Metal Print|Acrylic Print|"
    r"Wood Print|Poster|Tapestry|Throw Pillow|Coffee Mug|T[- ]?Shirt)"
    r"[^\n$]{0,40}\$\s*([\d,.]+)",
    re.I,
)
META_PRODUCT_PRICE = re.compile(
    r'<meta[^>]+property=["\']product:price:amount["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
META_PRODUCT_PRICE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']product:price:amount["\']',
    re.I,
)
NAMED_OFFER_PRICE = re.compile(
    r'"name"\s*:\s*"([^"]+)"\s*,\s*"price"\s*:\s*([\d.]+)',
    re.I,
)
BUTTON_PRICE = re.compile(
    r"buttonProductPrice[^>]*>\s*\$?\s*([\d,.]+)",
    re.I,
)
LOW_PRICE = re.compile(r'"lowPrice"\s*:\s*"?([\d.]+)"?', re.I)
ANY_JSON_PRICE = re.compile(r'"price"\s*:\s*"?([\d.]+)"?', re.I)

PRODUCT_SLUG_LABELS: dict[str, tuple[str, ...]] = {
    "art-print": ("art print",),
    "canvas-print": ("canvas print",),
    "framed-print": ("framed print",),
    "metal-print": ("metal print",),
    "acrylic-print": ("acrylic print",),
    "wood-print": ("wood print",),
    "poster": ("poster",),
    "tapestry": ("tapestry",),
    "throw-pillow": ("throw pillow",),
    "coffee-mug": ("coffee mug",),
    "iphone-case": ("iphone case",),
    "galaxy-case": ("galaxy case",),
    "tote-bag": ("tote bag",),
    "shower-curtain": ("shower curtain",),
    "jigsaw-puzzle": ("jigsaw puzzle",),
    "greeting-card": ("greeting card",),
    "spiral-notebook": ("spiral notebook",),
    "beach-towel": ("beach towel", "beach sheet"),
    "tshirt": ("t-shirt", "tshirt", "men's t-shirt", "mens t-shirt"),
    "kids-tshirt": ("kids t-shirt", "kid's t-shirt"),
    "youth-tshirt": ("youth t-shirt",),
    "baby-onesie": ("baby one-piece", "baby onesie"),
}
LD_JSON_BLOCK = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
PRODUCT_DETAILS_BLOCK = re.compile(
    r"PRODUCT\s+DETAILS\s*</[^>]+>\s*<[^>]+>(.*?)</(?:p|div|section)>",
    re.I | re.S,
)
DESIGN_DETAILS_BLOCK = re.compile(
    r"DESIGN\s+DETAILS\s*</[^>]+>\s*<[^>]+>(.*?)</(?:p|div|section)>",
    re.I | re.S,
)
OG_TITLE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
OG_IMAGE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
OG_DESC = re.compile(
    r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.DOTALL)
BY_ARTIST = re.compile(
    r'(?:by|artist)\s*[:\s]*<[^>]*>([^<]+)</|<a[^>]+href=["\']/profiles/[^"\']+["\'][^>]*>([^<]+)<',
    re.I,
)
IMG_SRC = re.compile(
    r'(?:src|data-src)=["\'](https?://(?:render\.)?fineartamerica\.com/[^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)["\']',
    re.I,
)
DESC_BLOCK = re.compile(
    r"(?:Description|Artist.?s Description)\s*</[^>]+>\s*<[^>]+>(.*?)</(?:p|div|section)>",
    re.I | re.DOTALL,
)

POPULAR_SITEMAP = "/sitemap-popular-products.xml"
MAIN_SITEMAP = "/sitemap.xml"
# Popular index ~284 file × ~50k URL. Mỗi SP = 1 request HTML → không được
# mặc định lấy hết 2 file (~100k). Shop homepage không set limit → cap này.
DEFAULT_SHOP_PRODUCT_LIMIT = 3000
DEFAULT_SHOP_CHILD_SITEMAPS = 1
FETCH_WAVE_SIZE = 64
MAX_WORKERS = 500


class FineArtAmericaCrawler(BaseCrawler):
    """Crawl FAA / Pixels: popular sitemap (trang chủ) hoặc listing → /featured/*.html."""

    source = "fineartamerica"

    def __init__(
        self,
        site_url: str = "https://fineartamerica.com",
        *,
        proxy_url: str | None = None,
        request_delay: float = 0.2,
    ):
        self.site_url = self._normalize_site_url(site_url)
        self.request_delay = request_delay
        self.proxy_rotator = ProxyRotator(proxy_url)
        self.session = create_http_session(self.proxy_rotator.current())
        self.session.headers.update(
            {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self._thread_local = threading.local()
        self.last_products: list[Product] = []
        self.partial_callback: PartialProductsCallback | None = None

    @staticmethod
    def _normalize_site_url(site_url: str) -> str:
        url = site_url.strip().rstrip("/")
        if not url.startswith("http"):
            url = f"https://{url}"
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if "pixels.com" in host:
            return "https://pixels.com"
        return "https://fineartamerica.com"

    @staticmethod
    def _product_query_slug(url: str) -> str | None:
        """FAA product pages use /featured/....html?product=coffee-mug etc."""
        qs = parse_qs(urlparse(url).query)
        vals = qs.get("product") or []
        if not vals:
            return None
        slug = (vals[0] or "").strip().lower()
        return slug or None

    @classmethod
    def _canonical_product_url(cls, url: str) -> str:
        """Keep bare /featured/ URL, or preserve ?product= when present."""
        parsed = urlparse(url)
        path = parsed.path or ""
        match = FEATURED_PATH.search(path)
        if not match:
            return url.split("#")[0].rstrip("?") or url
        base = urlunparse(
            (parsed.scheme or "https", parsed.netloc, f"/featured/{match.group(1)}", "", "", "")
        )
        product = cls._product_query_slug(url)
        if product:
            return f"{base}?product={product}"
        return base

    @classmethod
    def with_product_type(cls, url: str, product_slug: str) -> str:
        """Force /featured/....html?product=<slug> for category crawls."""
        slug = (product_slug or "").strip().lower()
        if not slug:
            return cls._canonical_product_url(url)
        parsed = urlparse(url)
        match = FEATURED_PATH.search(parsed.path or "")
        if not match:
            return url
        base = urlunparse(
            (parsed.scheme or "https", parsed.netloc, f"/featured/{match.group(1)}", "", "", "")
        )
        return f"{base}?product={slug}"

    @classmethod
    def is_known_host(cls, host: str) -> bool:
        h = host.lower().removeprefix("www.")
        return h in {"fineartamerica.com", "pixels.com"} or h.endswith(
            ".fineartamerica.com"
        )

    def refresh_session(self, *, rotate: bool = True) -> None:
        proxy = self.proxy_rotator.rotate() if rotate else self.proxy_rotator.current()
        self.session = reset_session(self.session, proxy)

    def _page_session(self, *, rotate: bool = False) -> requests.Session:
        if rotate or not hasattr(self._thread_local, "session"):
            proxy = (
                self.proxy_rotator.rotate()
                if rotate and self.proxy_rotator.count > 1
                else self.proxy_rotator.current()
            )
            # Gán proxy khác nhau cho từng luồng khi có pool
            if (
                not rotate
                and self.proxy_rotator.count > 1
                and not hasattr(self._thread_local, "session")
            ):
                proxy = self.proxy_rotator.rotate()
            sess = create_http_session(proxy)
            sess.headers.update(
                {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
            self._thread_local.session = sess
        return self._thread_local.session

    def _publish_partial(self, products: list[Product]) -> None:
        self.last_products = list(products)
        if self.partial_callback is not None:
            try:
                self.partial_callback(self.last_products)
            except Exception:
                pass

    def _get(
        self,
        url: str,
        *,
        timeout: float | tuple[float, float] = (15, 60),
        max_retries: int = 4,
        session: requests.Session | None = None,
    ) -> requests.Response:
        sess = session or self.session
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                response = sess.get(url, timeout=timeout)
                if response.status_code == 429 and attempt + 1 < max_retries:
                    retry_after = response.headers.get("Retry-After", "8")
                    try:
                        sleep_s = min(max(float(retry_after), 1.0), 25.0)
                    except (TypeError, ValueError):
                        sleep_s = 8.0
                    if self.proxy_rotator.count > 1:
                        if session is not None:
                            sess = self._page_session(rotate=True)
                        else:
                            self.refresh_session(rotate=True)
                            sess = self.session
                    time.sleep(sleep_s)
                    continue
                response.raise_for_status()
                return response
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                if self.proxy_rotator.count > 1:
                    if session is not None:
                        sess = self._page_session(rotate=True)
                    else:
                        self.refresh_session(rotate=True)
                        sess = self.session
                if attempt + 1 < max_retries:
                    time.sleep(min(2.0 * (attempt + 1), 12.0))
                    continue
                raise
        if last_exc:
            raise last_exc
        raise requests.RequestException(f"Không tải được {url}")

    def list_popular_child_sitemaps(self) -> list[str]:
        """Danh sách file con trong sitemap-popular-products.xml (~284 file)."""
        response = self._get(urljoin(self.site_url, POPULAR_SITEMAP))
        text = response.text or ""
        if "<sitemapindex" not in text.lower():
            return [urljoin(self.site_url, POPULAR_SITEMAP)]
        root = ET.fromstring(response.content)
        return [
            (el.text or "").strip()
            for el in root.findall(".//sm:loc", SITEMAP_NS)
            if el.text and (el.text or "").strip()
        ]

    def crawl_full_popular(
        self,
        *,
        max_workers: int = 8,
        limit: int | None = None,
        start_sitemap_index: int = 0,
        skip_urls: set[str] | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        on_products: Callable[[list[Product]], None] | None = None,
        max_sitemaps: int | None = None,
    ) -> tuple[int, int]:
        """
        Crawl toàn bộ (hoặc một phần) sitemap popular — theo từng file con,
        không giữ hết URL/SP trong RAM.

        Returns: (urls_attempted, products_ok)
        """
        children = self.list_popular_child_sitemaps()
        if max_sitemaps and max_sitemaps > 0:
            children = children[:max_sitemaps]
        skip = skip_urls or set()
        attempted = 0
        matched = 0
        start = max(0, int(start_sitemap_index or 0))

        for sm_i, child in enumerate(children):
            if sm_i < start:
                continue
            try:
                urls = self._urls_from_sitemap(child, max_child_sitemaps=None)
            except requests.RequestException:
                self.refresh_session(rotate=True)
                try:
                    urls = self._urls_from_sitemap(child, max_child_sitemaps=None)
                except requests.RequestException:
                    continue
            if skip:
                urls = [u for u in urls if u not in skip]
            if limit is not None:
                remain = limit - matched
                if remain <= 0:
                    break
                urls = urls[:remain]
            if not urls:
                continue

            batch = self._crawl_product_urls(
                urls,
                max_workers=max_workers,
                progress_callback=(
                    (
                        lambda cur, tot, mat, _a=attempted, _m=matched: progress_callback(
                            _a + cur, max(_a + tot, 1), _m + mat
                        )
                    )
                    if progress_callback
                    else None
                ),
                limit=None,
            )
            attempted += len(urls)
            matched += len(batch)
            if on_products and batch:
                on_products(batch)
            if limit is not None and matched >= limit:
                break
        return attempted, matched

    def crawl(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int = 8,
        progress_callback: CrawlProgressCallback | None = None,
        *,
        full: bool = False,
    ) -> list[Product]:
        """Trang chủ → sitemap popular.

        - Mặc định (UI): 1 file sitemap + tối đa ~3.000 SP (tránh treo).
        - full=True hoặc max_pages rất lớn: lấy nhiều file; vẫn nên dùng
          crawl_full_popular() + ghi CSV streaming cho full shop.
        """
        if full and limit is None and (max_pages is None or max_pages <= 0):
            # Full shop: stream từng sitemap, gom trong RAM chỉ khi caller cần list.
            # Không khuyến nghị cho UI — dùng script checkpoint.
            collected: list[Product] = []

            def _keep(batch: list[Product]) -> None:
                collected.extend(batch)
                self._publish_partial(collected)

            self.crawl_full_popular(
                max_workers=max_workers,
                progress_callback=progress_callback,
                on_products=_keep,
            )
            return collected

        max_sitemaps = (
            max_pages
            if max_pages and max_pages > 0
            else DEFAULT_SHOP_CHILD_SITEMAPS
        )
        effective_limit = (
            limit
            if limit is not None
            else (None if full else DEFAULT_SHOP_PRODUCT_LIMIT)
        )
        if progress_callback:
            progress_callback(0, effective_limit or DEFAULT_SHOP_PRODUCT_LIMIT, 0)
        urls = self._discover_featured_urls(
            prefer_popular=True,
            max_sitemaps=max_sitemaps,
            limit=effective_limit,
        )
        return self._crawl_product_urls(
            urls,
            max_workers=max_workers,
            progress_callback=progress_callback,
            limit=effective_limit,
        )

    def crawl_sitemap(
        self,
        sitemap_url: str,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int = 8,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        urls = self._urls_from_sitemap(sitemap_url, max_child_sitemaps=max_pages)
        if limit is not None:
            urls = urls[:limit]
        return self._crawl_product_urls(
            urls,
            max_workers=max_workers,
            progress_callback=progress_callback,
            limit=limit,
        )

    def crawl_listing(
        self,
        listing_url: str,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int = 8,
        progress_callback: CrawlProgressCallback | None = None,
    ) -> list[Product]:
        urls = self._discover_from_listing_pages(
            listing_url,
            max_pages=max_pages or 20,
            limit=limit,
        )
        return self._crawl_product_urls(
            urls,
            max_workers=max_workers,
            progress_callback=progress_callback,
            limit=limit,
        )

    def crawl_product(self, product_url: str) -> list[Product]:
        product = self._fetch_product(product_url)
        if not product:
            return []
        self._publish_partial([product])
        return [product]

    def count_popular_products(self) -> int | None:
        """Ước lượng nhanh: 1 file con sitemap (không tải 5×50k chỉ để đếm)."""
        try:
            urls = self._urls_from_sitemap(
                urljoin(self.site_url, POPULAR_SITEMAP),
                max_child_sitemaps=1,
            )
            return len(urls) or None
        except requests.RequestException:
            return None

    def _discover_featured_urls(
        self,
        *,
        prefer_popular: bool,
        max_sitemaps: int | None,
        limit: int | None,
    ) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()

        def add_many(batch: list[str]) -> None:
            for u in batch:
                if u not in seen:
                    seen.add(u)
                    urls.append(u)

        if prefer_popular:
            try:
                add_many(
                    self._urls_from_sitemap(
                        urljoin(self.site_url, POPULAR_SITEMAP),
                        max_child_sitemaps=max_sitemaps or 2,
                    )
                )
            except requests.RequestException:
                pass

        # Không tự tải toàn bộ sitemap.xml chính (quá lớn) trừ khi caller chỉ định.
        if not urls and max_sitemaps:
            try:
                add_many(
                    self._urls_from_sitemap(
                        urljoin(self.site_url, MAIN_SITEMAP),
                        max_child_sitemaps=max_sitemaps,
                    )
                )
            except requests.RequestException:
                pass

        if limit is not None:
            return urls[:limit]
        return urls

    def _urls_from_sitemap(
        self,
        sitemap_url: str,
        *,
        max_child_sitemaps: int | None = None,
    ) -> list[str]:
        response = self._get(sitemap_url)
        text = response.text or ""
        urls: list[str] = []
        seen: set[str] = set()

        if "<sitemapindex" in text.lower():
            root = ET.fromstring(response.content)
            child_locs = [
                (el.text or "").strip()
                for el in root.findall(".//sm:loc", SITEMAP_NS)
                if el.text
            ]
            if max_child_sitemaps and max_child_sitemaps > 0:
                child_locs = child_locs[:max_child_sitemaps]
            for child in child_locs:
                for u in self._urls_from_sitemap(child, max_child_sitemaps=None):
                    if u not in seen:
                        seen.add(u)
                        urls.append(u)
            return urls

        root = ET.fromstring(response.content)
        for el in root.findall(".//sm:loc", SITEMAP_NS):
            loc = (el.text or "").strip()
            if not loc or "/featured/" not in loc.lower():
                continue
            if not loc.lower().endswith(".html") and ".html?" not in loc.lower():
                # vẫn chấp nhận featured không .html nếu có
                if "/featured/" not in loc.lower():
                    continue
            if loc not in seen:
                seen.add(loc)
                urls.append(loc)
        # HTML fallback nếu XML parse trống nhưng có link featured
        if not urls:
            for match in FEATURED_PATH.finditer(text):
                loc = urljoin(self.site_url, f"/featured/{match.group(1)}")
                if loc not in seen:
                    seen.add(loc)
                    urls.append(loc)
        return urls

    def _discover_from_listing_pages(
        self,
        listing_url: str,
        *,
        max_pages: int,
        limit: int | None,
    ) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        base = listing_url.split("?")[0].rstrip("/")

        for page in range(1, max_pages + 1):
            page_url = base if page == 1 else f"{base}?page={page}"
            try:
                html = self._get(page_url).text
            except requests.RequestException:
                break
            found = 0
            for match in FEATURED_PATH.finditer(html):
                loc = urljoin(self.site_url, f"/featured/{match.group(1)}")
                if loc in seen:
                    continue
                seen.add(loc)
                urls.append(loc)
                found += 1
                if limit is not None and len(urls) >= limit:
                    return urls
            if found == 0:
                break
            if self.request_delay:
                time.sleep(self.request_delay)
        return urls

    def _crawl_product_urls(
        self,
        urls: list[str],
        *,
        max_workers: int,
        progress_callback: CrawlProgressCallback | None,
        limit: int | None,
        on_wave: Callable[[list[Product]], None] | None = None,
    ) -> list[Product]:
        if limit is not None:
            urls = urls[:limit]
        total = len(urls)
        if not total:
            return []
        if progress_callback:
            progress_callback(0, total, 0)

        workers = max(1, min(int(max_workers or 1), MAX_WORKERS))
        # Wave đủ lớn để tận dụng nhiều luồng (tránh 200 workers nhưng chỉ submit 64 URL/lần).
        wave_size = max(FETCH_WAVE_SIZE, workers * 2)
        products: list[Product] = []
        done = 0
        lock = threading.Lock()

        def one(url: str) -> Product | None:
            product = self._fetch_product(url, session=self._page_session())
            if self.request_delay:
                time.sleep(self.request_delay)
            return product

        def emit_wave(wave_products: list[Product]) -> None:
            if not wave_products:
                return
            if on_wave is not None:
                try:
                    on_wave(wave_products)
                except Exception:
                    pass

        if workers <= 1:
            wave_buf: list[Product] = []
            for url in urls:
                product = one(url)
                done += 1
                if product:
                    products.append(product)
                    wave_buf.append(product)
                self._publish_partial(products)
                if progress_callback:
                    progress_callback(done, total, len(products))
                if len(wave_buf) >= 50:
                    emit_wave(wave_buf)
                    wave_buf = []
            emit_wave(wave_buf)
        else:
            # Wave theo lô — tránh submit 50k–100k futures một lúc (treo UI/RAM).
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for wave_start in range(0, total, wave_size):
                    wave = urls[wave_start : wave_start + wave_size]
                    futures = {executor.submit(one, url): url for url in wave}
                    wave_ok: list[Product] = []
                    for future in as_completed(futures):
                        try:
                            product = future.result(timeout=90)
                        except Exception:
                            product = None
                        with lock:
                            done += 1
                            if product:
                                products.append(product)
                                wave_ok.append(product)
                            self._publish_partial(products)
                            if progress_callback:
                                progress_callback(done, total, len(products))
                    emit_wave(wave_ok)

        self._publish_partial(products)
        return products

    def _fetch_product(
        self,
        product_url: str,
        *,
        session: requests.Session | None = None,
    ) -> Product | None:
        try:
            response = self._get(product_url, session=session)
        except requests.RequestException:
            return None
        return self._parse_product_html(product_url, response.text)

    def _parse_product_html(self, url: str, page_html: str) -> Product | None:
        if not page_html or len(page_html) < 200:
            return None

        ld = self._extract_ld_product(page_html)

        title = ""
        if ld and ld.get("name"):
            title = str(ld["name"]).strip()
        if not title:
            og_title = OG_TITLE.search(page_html)
            if og_title:
                title = html_lib.unescape(og_title.group(1)).strip()
                title = re.sub(
                    r"\s*[-|].*Fine Art America.*$", "", title, flags=re.I
                ).strip()
                title = re.sub(
                    r"\s+(Photograph|Painting|Drawing|Digital Art|Mixed Media)\s+by\s+.+$",
                    "",
                    title,
                    flags=re.I,
                ).strip()
        if not title:
            h1 = H1_RE.search(page_html)
            if h1:
                title = re.sub(r"<[^>]+>", "", h1.group(1)).strip()
                title = html_lib.unescape(title)
        if not title:
            match = FEATURED_PATH.search(url)
            if match:
                title = unquote(match.group(1).replace(".html", "")).replace("-", " ")
        if not title:
            return None

        artist = ""
        if ld:
            creator = ld.get("creator")
            if isinstance(creator, dict) and creator.get("name"):
                artist = str(creator["name"]).strip()
            elif isinstance(creator, list) and creator:
                first = creator[0]
                if isinstance(first, dict) and first.get("name"):
                    artist = str(first["name"]).strip()
        if not artist:
            by_match = BY_ARTIST.search(page_html)
            if by_match:
                artist = (by_match.group(1) or by_match.group(2) or "").strip()
        artist = html_lib.unescape(artist)

        price = self._extract_price(
            page_html,
            ld,
            product_slug=self._product_query_slug(url),
        )

        images: list[str] = []
        if ld and ld.get("image"):
            img = ld["image"]
            if isinstance(img, str):
                images.append(img)
            elif isinstance(img, list):
                images.extend(str(x) for x in img if x)
            elif isinstance(img, dict) and img.get("url"):
                images.append(str(img["url"]))
        for match in OG_IMAGE.finditer(page_html):
            src = html_lib.unescape(match.group(1).strip())
            if src and src not in images:
                images.append(src)
        for match in IMG_SRC.finditer(page_html):
            src = match.group(1).split("?")[0]
            if src and src not in images:
                images.append(src)
        images = images[:12]

        description = self._extract_description(page_html, ld, title=title)

        handle_match = FEATURED_PATH.search(url)
        product_id = (
            handle_match.group(1) if handle_match else url.rstrip("/").split("/")[-1]
        )

        product_slug = self._product_query_slug(url)
        return Product(
            source=self.source,
            id=product_id,
            title=title if not artist else (
                title if artist.lower() in title.lower() else f"{title} by {artist}"
            ),
            description=description,
            price=price,
            currency="USD",
            url=self._canonical_product_url(url),
            image_urls=images,
            vendor=artist or None,
            sku=None,
            tags=[],
            categories=[artist] if artist else [],
            product_type=product_slug or "artwork",
            status="instock",
            variants=[],
        )

    def _extract_ld_product(self, page_html: str) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        for match in LD_JSON_BLOCK.finditer(page_html):
            raw = (match.group(1) or "").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            nodes: list[Any]
            if isinstance(data, list):
                nodes = data
            elif isinstance(data, dict) and "@graph" in data:
                graph = data.get("@graph")
                nodes = graph if isinstance(graph, list) else [data]
            else:
                nodes = [data]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                ntype = node.get("@type")
                types = (
                    [str(t).lower() for t in ntype]
                    if isinstance(ntype, list)
                    else [str(ntype or "").lower()]
                )
                if any(
                    t in {
                        "photograph",
                        "painting",
                        "drawing",
                        "visualartwork",
                        "creativework",
                        "product",
                        "imageobject",
                    }
                    for t in types
                ):
                    return node
                if node.get("description") and best is None:
                    best = node
        return best

    def _extract_price(
        self,
        page_html: str,
        ld: dict[str, Any] | None,
        *,
        product_slug: str | None = None,
    ) -> float | None:
        # 1) Meta của trang product đã chọn (ổn định nhất khi có)
        for pat in (META_PRODUCT_PRICE, META_PRODUCT_PRICE_ALT):
            meta = pat.search(page_html)
            if meta:
                val = self._safe_float(meta.group(1))
                if val is not None:
                    return val

        # 2) Offer JSON theo đúng loại SP (?product=iphone-case → "... iPhone Case")
        labels = PRODUCT_SLUG_LABELS.get((product_slug or "").lower(), ())
        if labels:
            for match in NAMED_OFFER_PRICE.finditer(page_html):
                name = (match.group(1) or "").lower()
                if any(label in name for label in labels):
                    val = self._safe_float(match.group(2))
                    if val is not None:
                        return val

        prices: list[float] = []

        if ld:
            offers = ld.get("offers")
            offer_nodes: list[Any]
            if isinstance(offers, list):
                offer_nodes = offers
            elif isinstance(offers, dict):
                offer_nodes = [offers]
            else:
                offer_nodes = []
            for offer in offer_nodes:
                if not isinstance(offer, dict):
                    continue
                offer_name = str(offer.get("name") or "").lower()
                if labels and offer_name and any(label in offer_name for label in labels):
                    for key in ("price", "lowPrice", "highPrice"):
                        val = self._safe_float(offer.get(key))
                        if val is not None:
                            return val
                for key in ("price", "lowPrice", "highPrice"):
                    val = self._safe_float(offer.get(key))
                    if val is not None:
                        prices.append(val)

        for match in BUTTON_PRICE.finditer(page_html):
            val = self._safe_float(match.group(1))
            if val is not None:
                prices.append(val)

        for match in PRICE_LINE.finditer(page_html):
            label = (match.group(1) or "").lower().replace("-", " ")
            val = self._safe_float(match.group(2))
            if val is None:
                continue
            if labels and any(l.replace("-", " ") in label for l in labels):
                return val
            prices.append(val)

        if not prices:
            low = LOW_PRICE.search(page_html)
            if low:
                val = self._safe_float(low.group(1))
                if val is not None:
                    prices.append(val)
        if not prices:
            for match in ANY_JSON_PRICE.finditer(page_html):
                val = self._safe_float(match.group(1))
                if val is not None and 1 <= val <= 50000:
                    prices.append(val)

        # Khi không chọn loại SP: lấy giá thấp nhất (artwork listing).
        # Khi đã chọn loại SP mà không match được: tránh lấy lowPrice sticker —
        # ưu tiên giá nằm giữa (median-ish via sorted mid) nếu có nhiều giá.
        if not prices:
            return None
        if product_slug and len(prices) >= 3:
            prices_sorted = sorted(prices)
            return prices_sorted[len(prices_sorted) // 2]
        return min(prices)

    def _extract_description(
        self,
        page_html: str,
        ld: dict[str, Any] | None,
        *,
        title: str,
    ) -> str | None:
        """Trả về mô tả dạng HTML (<p>…</p>)."""
        parts: list[str] = []

        design = DESIGN_DETAILS_BLOCK.search(page_html)
        if design:
            html_part = self._fragment_to_html(design.group(1))
            if html_part:
                parts.append(html_part)

        product_details = PRODUCT_DETAILS_BLOCK.search(page_html)
        if product_details:
            html_part = self._fragment_to_html(product_details.group(1))
            if html_part:
                parts.append(html_part)

        if ld and ld.get("description"):
            html_part = self._fragment_to_html(str(ld["description"]))
            if html_part:
                # Ưu tiên mô tả artwork (LD) trước boilerplate.
                plain = self._clean_html_text(html_part)
                if plain and not any(
                    self._clean_html_text(p).lower() == plain.lower() for p in parts
                ):
                    parts.insert(0, html_part)

        if not parts:
            block = DESC_BLOCK.search(page_html)
            if block:
                html_part = self._fragment_to_html(block.group(1))
                if html_part:
                    parts.append(html_part)

        if not parts:
            og_desc = OG_DESC.search(page_html)
            if og_desc:
                text = html_lib.unescape(og_desc.group(1)).strip()
                if text and not self._is_weak_og_description(text, title):
                    html_part = self._fragment_to_html(text)
                    if html_part:
                        parts.append(html_part)

        if not parts:
            return None

        # Gộp HTML, bỏ trùng theo text thuần
        seen: set[str] = set()
        ordered: list[str] = []
        for part in parts:
            key = self._clean_html_text(part).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(part)
        return "".join(ordered) if ordered else None

    @classmethod
    def _fragment_to_html(cls, value: str) -> str | None:
        raw = (value or "").strip()
        if not raw:
            return None
        # Đã có thẻ block HTML → giữ markup
        if re.search(r"(?i)</?(?:p|div|ul|ol|li|br|h[1-6])\b", raw):
            return normalize_description_html(raw) or None
        # Text thuần / LD JSON → bọc <p>
        return format_description_output(cls._clean_html_text(raw), "html")

    @staticmethod
    def _clean_html_text(value: str) -> str:
        text = re.sub(r"<[^>]+>", " ", value or "")
        text = html_lib.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _is_weak_og_description(text: str, title: str) -> bool:
        t = text.strip().lower()
        title_l = (title or "").strip().lower()
        if not t:
            return True
        if title_l and t.startswith(title_l):
            return True
        if re.match(
            r"^.+\s+(photograph|painting|drawing|digital art|mixed media)\s+by\s+.+$",
            t,
            re.I,
        ):
            return True
        return len(t) < 80

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            return float(str(value).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError):
            return None
