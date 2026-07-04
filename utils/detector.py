from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, unquote_plus, urlparse

import requests

from crawlers.generic_web import GenericWebCrawler

SHOPIFY_PRODUCT = re.compile(r"/products/([^/?#]+)")
SHOPIFY_COLLECTION = re.compile(r"/collections/([^/?#]+)")
WOOCOMMERCE_PRODUCT = re.compile(r"/product/([^/?#]+)")
WOOCOMMERCE_CATEGORY = re.compile(r"/product-category/([^/?#]+)")
REDBUBBLE_PRODUCT = re.compile(r"/i/[^/]+/[^/]+/\d+")
REDBUBBLE_SHOP = re.compile(r"/shop/([^/?#]+)")
GENERIC_WEB_PRODUCT = re.compile(r"/product/([^/?#]+)")
GENERIC_WEB_SHOP_CATEGORY = re.compile(r"/shop/([^/?#]+)")
GENERIC_WEB_CATEGORY = re.compile(r"/category/([^/?#]+)")
MAGENTO_SEARCH = re.compile(r"/catalogsearch/result", re.I)
HTML_CATALOG_SEARCH = MAGENTO_SEARCH

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class DetectedTarget:
    platform: str
    crawl_type: str
    url: str
    base_url: str | None = None
    handle: str | None = None
    category_slug: str | None = None
    query: str | None = None
    message: str | None = None


class UnsupportedUrlError(Exception):
    def __init__(self, message: str, platform: str | None = None):
        super().__init__(message)
        self.platform = platform


def normalize_url(url: str) -> str:
    normalized = url.strip()
    if not normalized.startswith("http"):
        normalized = f"https://{normalized}"
    return normalized


def detect_url(url: str) -> DetectedTarget:
    normalized = normalize_url(url)
    parsed = urlparse(normalized)
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = parsed.path or "/"

    if host in {"redbubble.com"} or host.endswith(".redbubble.com"):
        return _detect_redbubble(normalized, path)

    if host.endswith(".myshopify.com"):
        return _detect_shopify(normalized, host, path)

    raise UnsupportedUrlError(
        f"Không nhận diện được nền tảng từ link: {normalized}",
        platform="unknown",
    )


def probe_platform(url: str, session: requests.Session | None = None) -> str | None:
    parsed = urlparse(normalize_url(url))
    base = f"{parsed.scheme}://{parsed.netloc}"
    normalized = normalize_url(url)

    def fresh_session() -> requests.Session:
        s = requests.Session()
        s.headers["User-Agent"] = DEFAULT_USER_AGENT
        if session is not None:
            s.headers.update(session.headers)
            if session.proxies:
                s.proxies.update(session.proxies)
        return s

    def check_shopify() -> str | None:
        try:
            shopify_resp = fresh_session().get(
                f"{base}/products.json", params={"limit": 1}, timeout=10
            )
            if shopify_resp.status_code == 200:
                data = shopify_resp.json()
                if isinstance(data, dict) and isinstance(data.get("products"), list):
                    return "shopify"
        except (requests.RequestException, ValueError):
            pass
        return None

    def check_woo_store() -> str | None:
        try:
            woo_store = fresh_session().get(
                f"{base}/wp-json/wc/store/v1/products",
                params={"per_page": 1},
                timeout=10,
            )
            if woo_store.status_code == 200:
                data = woo_store.json()
                if isinstance(data, list):
                    return "woocommerce"
        except (requests.RequestException, ValueError):
            pass
        return None

    def check_wp_json() -> str | None:
        try:
            wp_json = fresh_session().get(f"{base}/wp-json/", timeout=10)
            if wp_json.status_code == 200:
                data = wp_json.json()
                routes = str(data.get("routes", {}))
                if "wc/v3" in routes or "wc/store" in routes:
                    return "woocommerce"
        except (requests.RequestException, ValueError):
            pass
        return None

    def check_shopbase() -> str | None:
        try:
            session = fresh_session()
            products_json = session.get(
                f"{base}/products.json",
                params={"limit": 1},
                timeout=8,
            )
            if products_json.status_code == 200:
                return None
            catalog = session.get(
                f"{base}/api/catalog/next/products.json",
                params={"limit": 1, "page": 1},
                timeout=8,
            )
            if catalog.status_code == 200:
                payload = catalog.json()
                if isinstance(payload.get("result"), dict):
                    return "shopbase"
            sitemap = session.get(f"{base}/sitemap.xml", timeout=8)
            if sitemap.status_code == 200 and "sitemap_products" in sitemap.text.lower():
                return "shopbase"
            page = session.get(base, timeout=8)
            if page.status_code == 200 and "shopbase" in page.text.lower():
                return "shopbase"
        except (requests.RequestException, ValueError):
            pass
        return None

    def check_generic() -> str | None:
        try:
            if GenericWebCrawler.site_supports(base, session=fresh_session()):
                return "generic-web"
        except Exception:
            pass
        return None

    def check_html_signals() -> str | None:
        try:
            page = fresh_session().get(normalized, timeout=10)
            if page.status_code == 200:
                text = page.text.lower()
                if "cdn.shopify.com" in text or "shopify.shop" in text:
                    return "shopify"
                if "woocommerce" in text or "wc-block" in text:
                    return "woocommerce"
        except requests.RequestException:
            pass
        return None

    checks = (
        check_shopify,
        check_shopbase,
        check_woo_store,
        check_wp_json,
        check_generic,
        check_html_signals,
    )
    with ThreadPoolExecutor(max_workers=len(checks)) as executor:
        futures = [executor.submit(fn) for fn in checks]
        for future in as_completed(futures):
            platform = future.result()
            if platform:
                return platform
    return None


def resolve_url(url: str, session: requests.Session | None = None) -> DetectedTarget:
    normalized = normalize_url(url)

    try:
        target = detect_url(normalized)
        if target.crawl_type != "unknown":
            if target.platform == "generic-web":
                target = _refine_generic_web_target(target, session=session)
            return target
    except UnsupportedUrlError:
        target = None

    platform = probe_platform(normalized, session=session)
    parsed = urlparse(normalized)
    host = parsed.netloc.lower()
    path = parsed.path or "/"

    if platform == "shopify":
        shopify_target = _detect_shopify(normalized, host, path)
        if shopify_target.crawl_type != "unknown":
            return shopify_target
        return DetectedTarget(
            platform="shopify",
            crawl_type="shop",
            url=normalized,
            base_url=f"{parsed.scheme}://{parsed.netloc}",
        )

    if platform == "shopbase":
        shopbase_target = _detect_shopbase(normalized, host, path)
        if shopbase_target.crawl_type != "unknown":
            return shopbase_target
        return DetectedTarget(
            platform="shopbase",
            crawl_type="shop",
            url=normalized,
            base_url=f"{parsed.scheme}://{parsed.netloc}",
        )

    if platform == "woocommerce":
        woo_target = _detect_woocommerce(normalized, host, path)
        if woo_target.crawl_type != "unknown":
            return woo_target
        return DetectedTarget(
            platform="woocommerce",
            crawl_type="shop",
            url=normalized,
            base_url=f"{parsed.scheme}://{parsed.netloc}",
        )

    if platform == "generic-web":
        generic_target = _detect_generic_web(normalized, path)
        generic_target = _refine_generic_web_target(generic_target, session=session)
        if generic_target.crawl_type != "unknown":
            return generic_target
        return DetectedTarget(
            platform="generic-web",
            crawl_type="shop",
            url=normalized,
            base_url=f"{parsed.scheme}://{parsed.netloc}",
        )

    if target is not None:
        if target.platform == "redbubble" and (
            target.crawl_type == "unknown" or path in ("", "/")
        ):
            raise UnsupportedUrlError(
                target.message
                or (
                    "Nhận diện: **Redbubble** · Trang chủ không crawl được.\n"
                    "Dán link **/shop/từ-khóa** (vd. `redbubble.com/shop/t-shirts`) "
                    "hoặc link sản phẩm **/i/...**."
                ),
                platform="redbubble",
            )
        raise UnsupportedUrlError(
            f"Nhận diện được {target.platform} nhưng chưa hỗ trợ loại link: {path}",
            platform=target.platform,
        )

    raise UnsupportedUrlError(
        f"Không nhận diện được nền tảng từ link: {normalized}\n"
        "Hiện hỗ trợ: Shopify, ShopBase, WooCommerce, Redbubble, Web thuần (Next.js / HTML / sitemap).\n"
        "Ví dụ: keeptee.com · wc.luminaaura.us · silverbene.com/earrings.html",
        platform="unknown",
    )


def _detect_redbubble(url: str, path: str) -> DetectedTarget:
    if REDBUBBLE_PRODUCT.search(path):
        return DetectedTarget(
            platform="redbubble",
            crawl_type="product",
            url=url,
            base_url="https://www.redbubble.com",
        )

    shop_match = REDBUBBLE_SHOP.search(path)
    if shop_match:
        return DetectedTarget(
            platform="redbubble",
            crawl_type="category",
            url=url,
            base_url="https://www.redbubble.com",
            query=shop_match.group(1),
            category_slug=shop_match.group(1),
        )

    if "/people/" in path and "/shop" in path:
        return DetectedTarget(
            platform="redbubble",
            crawl_type="category",
            url=url,
            base_url="https://www.redbubble.com",
        )

    if path not in ("", "/"):
        return DetectedTarget(
            platform="redbubble",
            crawl_type="category",
            url=url,
            base_url="https://www.redbubble.com",
        )

    return DetectedTarget(
        platform="redbubble",
        crawl_type="unknown",
        url=url,
        base_url="https://www.redbubble.com",
        message=(
            "Trang chủ Redbubble — dán link **/shop/từ-khóa** "
            "(vd. `/shop/t-shirts`) hoặc link sản phẩm **/i/...**."
        ),
    )


def _extract_search_query(url: str) -> str | None:
    parsed = urlparse(url)
    raw = parse_qs(parsed.query).get("s", [None])[0]
    if not raw:
        return None
    query = unquote_plus(str(raw)).strip()
    return query or None


def _extract_shopify_search_query(url: str) -> str | None:
    parsed = urlparse(url)
    if (parsed.path or "").rstrip("/") != "/search":
        return None
    raw = parse_qs(parsed.query).get("q", [None])[0]
    if not raw:
        return None
    query = unquote_plus(str(raw)).strip()
    return query or None


def _detect_shopify(url: str, host: str, path: str) -> DetectedTarget:
    base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    product_match = SHOPIFY_PRODUCT.search(path)
    if product_match and product_match.group(1) != "json":
        return DetectedTarget(
            platform="shopify",
            crawl_type="product",
            url=url,
            base_url=base_url,
            handle=product_match.group(1),
        )

    search_query = _extract_shopify_search_query(url)
    if search_query:
        return DetectedTarget(
            platform="shopify",
            crawl_type="search",
            url=url,
            base_url=base_url,
            query=search_query,
        )

    collection_match = SHOPIFY_COLLECTION.search(path)
    if collection_match:
        handle = collection_match.group(1)
        crawl_type = "shop" if handle == "all" else "category"
        return DetectedTarget(
            platform="shopify",
            crawl_type=crawl_type,
            url=url,
            base_url=base_url,
            handle=handle,
            category_slug=handle,
        )

    if path in ("", "/"):
        return DetectedTarget(
            platform="shopify",
            crawl_type="shop",
            url=url,
            base_url=base_url,
        )

    return DetectedTarget(
        platform="shopify",
        crawl_type="unknown",
        url=url,
        base_url=base_url,
    )


def _detect_shopbase(url: str, host: str, path: str) -> DetectedTarget:
    target = _detect_shopify(url, host, path)
    target.platform = "shopbase"
    return target


def _detect_woocommerce(url: str, host: str, path: str) -> DetectedTarget:
    base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    search_query = _extract_search_query(url)
    if search_query:
        return DetectedTarget(
            platform="woocommerce",
            crawl_type="search",
            url=url,
            base_url=base_url,
            query=search_query,
        )

    product_match = WOOCOMMERCE_PRODUCT.search(path)
    if product_match:
        return DetectedTarget(
            platform="woocommerce",
            crawl_type="product",
            url=url,
            base_url=base_url,
            handle=product_match.group(1),
        )

    category_match = WOOCOMMERCE_CATEGORY.search(path)
    if category_match:
        return DetectedTarget(
            platform="woocommerce",
            crawl_type="category",
            url=url,
            base_url=base_url,
            category_slug=category_match.group(1),
        )

    if path.rstrip("/") in ("/shop", "/products"):
        return DetectedTarget(
            platform="woocommerce",
            crawl_type="shop",
            url=url,
            base_url=base_url,
        )

    return DetectedTarget(
        platform="woocommerce",
        crawl_type="unknown",
        url=url,
        base_url=base_url,
    )


def _detect_generic_web(url: str, path: str) -> DetectedTarget:
    from crawlers.generic_html import GenericHtmlCatalog

    base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    search_query = _extract_html_catalog_search_query(url)
    if search_query:
        return DetectedTarget(
            platform="generic-web",
            crawl_type="search",
            url=url,
            base_url=base_url,
            query=search_query,
        )

    if path.lower().endswith(".html"):
        slug_path = path.strip("/").removesuffix(".html")
        return DetectedTarget(
            platform="generic-web",
            crawl_type="category",
            url=url,
            base_url=base_url,
            handle=slug_path,
            category_slug=GenericHtmlCatalog.category_slug(slug_path),
        )

    product_match = GENERIC_WEB_PRODUCT.search(path)
    if product_match:
        return DetectedTarget(
            platform="generic-web",
            crawl_type="product",
            url=url,
            base_url=base_url,
            handle=product_match.group(1),
        )

    shop_match = GENERIC_WEB_SHOP_CATEGORY.search(path)
    if shop_match:
        category_name = unquote(shop_match.group(1)).replace("+", " ").strip()
        return DetectedTarget(
            platform="generic-web",
            crawl_type="category",
            url=url,
            base_url=base_url,
            handle=category_name,
            category_slug=GenericWebCrawler.category_slug(category_name),
        )

    category_match = GENERIC_WEB_CATEGORY.search(path)
    if category_match:
        category_name = unquote(category_match.group(1)).replace("+", " ").strip()
        return DetectedTarget(
            platform="generic-web",
            crawl_type="category",
            url=url,
            base_url=base_url,
            handle=category_name,
            category_slug=GenericWebCrawler.category_slug(category_name),
        )

    if path.rstrip("/") in ("", "/products", "/shop", "/category", "/new-arrivals"):
        return DetectedTarget(
            platform="generic-web",
            crawl_type="shop",
            url=url,
            base_url=base_url,
        )

    return DetectedTarget(
        platform="generic-web",
        crawl_type="unknown",
        url=url,
        base_url=base_url,
    )


def _extract_html_catalog_search_query(url: str) -> str | None:
    parsed = urlparse(url)
    if not HTML_CATALOG_SEARCH.search(parsed.path or ""):
        return None
    raw = parse_qs(parsed.query).get("q", [None])[0]
    if not raw:
        return None
    query = unquote_plus(str(raw)).strip()
    return query or None


def _refine_generic_web_target(
    target: DetectedTarget,
    session: requests.Session | None = None,
) -> DetectedTarget:
    if target.platform != "generic-web" or target.crawl_type != "category":
        return target
    if not (target.url or "").lower().endswith(".html"):
        return target

    session = session or requests.Session()
    session.headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
    try:
        response = session.get(target.url, timeout=20)
        if response.status_code != 200:
            return target
        from crawlers.generic_html import GenericHtmlCatalog

        if GenericHtmlCatalog.is_product_page_html(response.text):
            handle = urlparse(target.url).path.strip("/").removesuffix(".html")
            return DetectedTarget(
                platform="generic-web",
                crawl_type="product",
                url=target.url,
                base_url=target.base_url,
                handle=handle,
            )
    except requests.RequestException:
        pass
    return target
