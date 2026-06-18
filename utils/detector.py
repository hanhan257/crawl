from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

SHOPIFY_PRODUCT = re.compile(r"/products/([^/?#]+)")
SHOPIFY_COLLECTION = re.compile(r"/collections/([^/?#]+)")
WOOCOMMERCE_PRODUCT = re.compile(r"/product/([^/?#]+)")
WOOCOMMERCE_CATEGORY = re.compile(r"/product-category/([^/?#]+)")
REDBUBBLE_PRODUCT = re.compile(r"/i/[^/]+/[^/]+/\d+")
REDBUBBLE_SHOP = re.compile(r"/shop/([^/?#]+)")
GENERIC_WEB_PRODUCT = re.compile(r"/product/([^/?#]+)")

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
    session = session or requests.Session()
    session.headers["User-Agent"] = DEFAULT_USER_AGENT
    parsed = urlparse(normalize_url(url))
    base = f"{parsed.scheme}://{parsed.netloc}"

    try:
        shopify_resp = session.get(f"{base}/products.json", params={"limit": 1}, timeout=15)
        if shopify_resp.status_code == 200:
            data = shopify_resp.json()
            if isinstance(data, dict) and isinstance(data.get("products"), list):
                return "shopify"
    except (requests.RequestException, ValueError):
        pass

    try:
        woo_store = session.get(
            f"{base}/wp-json/wc/store/v1/products",
            params={"per_page": 1},
            timeout=15,
        )
        if woo_store.status_code == 200:
            data = woo_store.json()
            if isinstance(data, list):
                return "woocommerce"
    except (requests.RequestException, ValueError):
        pass

    try:
        wp_json = session.get(f"{base}/wp-json/", timeout=15)
        if wp_json.status_code == 200:
            data = wp_json.json()
            routes = str(data.get("routes", {}))
            if "wc/v3" in routes or "wc/store" in routes:
                return "woocommerce"
    except (requests.RequestException, ValueError):
        pass

    try:
        page = session.get(normalize_url(url), timeout=15)
        if page.status_code == 200:
            text = page.text.lower()
            if "cdn.shopify.com" in text or "shopify.shop" in text:
                return "shopify"
            if "woocommerce" in text or "wc-block" in text:
                return "woocommerce"
    except requests.RequestException:
        pass

    try:
        from crawlers.generic_web import GenericWebCrawler

        if GenericWebCrawler.site_supports(base, session=session):
            return "generic-web"
    except Exception:
        pass

    return None


def resolve_url(url: str, session: requests.Session | None = None) -> DetectedTarget:
    normalized = normalize_url(url)

    try:
        target = detect_url(normalized)
        if target.crawl_type != "unknown":
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
        if generic_target.crawl_type != "unknown":
            return generic_target
        return DetectedTarget(
            platform="generic-web",
            crawl_type="shop",
            url=normalized,
            base_url=f"{parsed.scheme}://{parsed.netloc}",
        )

    if target is not None:
        raise UnsupportedUrlError(
            f"Nhận diện được {target.platform} nhưng chưa hỗ trợ loại link: {path}",
            platform=target.platform,
        )

    raise UnsupportedUrlError(
        f"Không nhận diện được nền tảng từ link: {normalized}\n"
        "Hiện hỗ trợ: Shopify, WooCommerce, Redbubble, Web thuần (Next.js/sitemap).\n"
        "Ví dụ web thuần: keeptee.com",
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

    raise UnsupportedUrlError(
        f"Link Redbubble không hợp lệ hoặc chưa hỗ trợ: {url}",
        platform="redbubble",
    )


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


def _detect_woocommerce(url: str, host: str, path: str) -> DetectedTarget:
    base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

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
    base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    product_match = GENERIC_WEB_PRODUCT.search(path)
    if product_match:
        return DetectedTarget(
            platform="generic-web",
            crawl_type="product",
            url=url,
            base_url=base_url,
            handle=product_match.group(1),
        )

    if path in ("", "/", "/products", "/shop", "/new-arrivals"):
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
