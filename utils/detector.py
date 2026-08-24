from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, unquote_plus, urlparse

import requests

from crawlers.generic_web import GenericWebCrawler
from crawlers.magento_graphql import MagentoGraphqlCrawler

SHOPIFY_PRODUCT = re.compile(r"/products/([^/?#]+)")
SHOPIFY_COLLECTION = re.compile(r"/collections/([^/?#]+)")
WOOCOMMERCE_PRODUCT = re.compile(r"/product/([^/?#]+)")
WOOCOMMERCE_CATEGORY = re.compile(
    r"/product-category/(.+?)(?:/page/\d+)?/?$",
    re.I,
)
# GullPrint / Flatsome đôi khi dùng /home-living/mug/ thay vì /product-category/...
WOOCOMMERCE_CUSTOM_CATEGORY = re.compile(
    r"^/(?!product/|shop/?$|cart/?$|checkout/?$|my-account|wp-|blog|about|contact|page/)"
    r"([a-z0-9\-]+(?:/[a-z0-9\-]+)*)/?$",
    re.I,
)
REDBUBBLE_PRODUCT = re.compile(r"/i/[^/]+/[^/]+/\d+")
REDBUBBLE_SHOP = re.compile(r"/shop/([^/?#]+)")
GENERIC_WEB_PRODUCT = re.compile(r"/product/([^/?#]+)")
GENERIC_WEB_SHOP_CATEGORY = re.compile(r"/shop/([^/?#]+)")
GENERIC_WEB_CATEGORY = re.compile(r"/category/([^/?#]+)")
TEEMILL_COLLECTION = re.compile(r"/collection/([^/?#]+)", re.I)
TEEMILL_PRODUCT = re.compile(r"/product/([^/?#]+)", re.I)
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

    if host in {"fineartamerica.com", "pixels.com"} or host.endswith(
        ".fineartamerica.com"
    ):
        return _detect_fineartamerica(normalized, path)

    if host.endswith(".myshopify.com"):
        return _detect_shopify(normalized, host, path)

    if host.endswith("trendsi.com"):
        return _detect_trendsi(normalized, path, parsed)

    if MagentoGraphqlCrawler.is_known_host(host):
        return _detect_magento(normalized, path, parsed)

    raise UnsupportedUrlError(
        f"Không nhận diện được nền tảng từ link: {normalized}",
        platform="unknown",
    )


def probe_platform(url: str, session: requests.Session | None = None) -> str | None:
    parsed = urlparse(normalize_url(url))
    host = (parsed.netloc or "").lower().removeprefix("www.")
    if host.endswith("trendsi.com"):
        return "trendsi"
    if MagentoGraphqlCrawler.is_known_host(host):
        return "magento"
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
        session = fresh_session()
        # meta.json có myshopify_domain → Shopify thật (kể cả khi /products.json bị 429).
        try:
            meta_resp = session.get(f"{base}/meta.json", timeout=10)
            if meta_resp.status_code == 200:
                meta = meta_resp.json()
                if isinstance(meta, dict) and meta.get("myshopify_domain"):
                    return "shopify"
        except (requests.RequestException, ValueError):
            pass
        try:
            shopify_resp = session.get(
                f"{base}/products.json", params={"limit": 1}, timeout=10
            )
            if shopify_resp.status_code == 200:
                data = shopify_resp.json()
                if isinstance(data, dict) and isinstance(data.get("products"), list):
                    return "shopify"
        except (requests.RequestException, ValueError):
            pass
        # cart.js thường vẫn 200 khi homepage/meta/products.json bị 429 (vd. printyourwear).
        try:
            cart_resp = session.get(f"{base}/cart.js", timeout=10)
            if cart_resp.status_code == 200:
                data = cart_resp.json()
                if (
                    isinstance(data, dict)
                    and "token" in data
                    and isinstance(data.get("items"), list)
                ):
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
        try:
            from utils.curl_http import curl_get_json_list

            data, status, _headers = curl_get_json_list(
                f"{base}/wp-json/wc/store/v1/products",
                params={"per_page": 1},
                timeout=15,
            )
            if status == 200 and isinstance(data, list):
                return "woocommerce"
        except Exception:
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
            # Shopify thật (kể cả rate-limit products.json) → không phải ShopBase.
            try:
                meta_resp = session.get(f"{base}/meta.json", timeout=8)
                if meta_resp.status_code == 200:
                    meta = meta_resp.json()
                    if isinstance(meta, dict) and meta.get("myshopify_domain"):
                        return None
            except (requests.RequestException, ValueError):
                pass
            products_json = session.get(
                f"{base}/products.json",
                params={"limit": 1},
                timeout=8,
            )
            if products_json.status_code == 200:
                return None
            # Chỉ nhận ShopBase khi có catalog API thật — không dùng sitemap_products
            # (Shopify cũng dùng tên sitemap_products_*.xml → dễ nhận nhầm).
            catalog = session.get(
                f"{base}/api/catalog/next/products.json",
                params={"limit": 1, "page": 1},
                timeout=8,
            )
            if catalog.status_code == 200:
                payload = catalog.json()
                if isinstance(payload.get("result"), dict):
                    return "shopbase"
            page = session.get(base, timeout=8)
            if page.status_code == 200 and "shopbase" in page.text.lower():
                return "shopbase"
        except (requests.RequestException, ValueError):
            pass
        return None

    def check_teemill() -> str | None:
        try:
            from crawlers.teemill import TeemillCrawler

            if TeemillCrawler.site_supports(base, session=fresh_session()):
                return "teemill"
            if "/collection/" in normalized or "/product/" in normalized:
                if TeemillCrawler.site_supports(normalized, session=fresh_session()):
                    return "teemill"
        except Exception:
            pass
        return None

    def check_generic() -> str | None:
        try:
            if GenericWebCrawler.site_supports(base, session=fresh_session()):
                return "generic-web"
        except Exception:
            pass
        return None

    def check_magento() -> str | None:
        try:
            if MagentoGraphqlCrawler.site_supports(base, session=fresh_session()):
                return "magento"
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
        check_teemill,
        check_magento,
        check_generic,
        check_html_signals,
    )
    with ThreadPoolExecutor(max_workers=len(checks)) as executor:
        futures = [executor.submit(fn) for fn in checks]
        for future in as_completed(futures):
            platform = future.result()
            if platform:
                return platform
    # Proxy hay bị shop chặn → thử nhận diện direct.
    if session is not None and session.proxies:
        direct = probe_platform(url, session=None)
        if direct:
            return direct

    # Cloudflare chặn requests — thử Woo Store API qua curl_cffi (direct).
    try:
        from utils.curl_http import curl_get_json_list

        data, status, _headers = curl_get_json_list(
            f"{base}/wp-json/wc/store/v1/products",
            params={"per_page": 1},
            timeout=20,
        )
        if status == 200 and isinstance(data, list):
            return "woocommerce"
    except Exception:
        pass
    return None


def resolve_url(url: str, session: requests.Session | None = None) -> DetectedTarget:
    normalized = normalize_url(url)

    try:
        target = detect_url(normalized)
        if target.crawl_type != "unknown":
            if target.platform == "generic-web":
                target = _refine_generic_web_target(target, session=session)
            if target.platform == "magento":
                target = _refine_magento_target(target, session=session)
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

    if platform == "teemill":
        teemill_target = _detect_teemill(normalized, host, path)
        if teemill_target.crawl_type != "unknown":
            return teemill_target
        return DetectedTarget(
            platform="teemill",
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

    if platform == "trendsi":
        trendsi_target = _detect_trendsi(normalized, path, parsed)
        if trendsi_target.crawl_type != "unknown":
            return trendsi_target
        return DetectedTarget(
            platform="trendsi",
            crawl_type="shop",
            url=normalized,
            base_url=f"{parsed.scheme}://{parsed.netloc}",
        )

    if platform == "magento":
        magento_target = _detect_magento(normalized, path, parsed)
        magento_target = _refine_magento_target(magento_target, session=session)
        if magento_target.crawl_type != "unknown":
            return magento_target
        return DetectedTarget(
            platform="magento",
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
        "Hiện hỗ trợ: Shopify, ShopBase, WooCommerce, Teemill, Trendsi, Magento/Jomashop, "
        "Redbubble, Fine Art America, Web thuần (Next.js / HTML / sitemap).\n"
        "Ví dụ: jomashop.com/jewelry.html · thewildlifetrustsshop.com/collection/... · keeptee.com",
        platform="unknown",
    )


def _detect_trendsi(url: str, path: str, parsed) -> DetectedTarget:
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    query = parse_qs(parsed.query, keep_blank_values=True)

    if path.rstrip("/") == "/products/detail":
        product_id = (query.get("id") or [None])[0]
        if product_id:
            return DetectedTarget(
                platform="trendsi",
                crawl_type="product",
                url=url,
                base_url=base_url,
                handle=str(product_id),
            )

    if path.rstrip("/") == "/brand":
        brand_id = (query.get("id") or [None])[0]
        brand_name = (query.get("nameEn") or [""])[0]
        category_id = (query.get("backendCategoryId") or [None])[0]
        if brand_id:
            return DetectedTarget(
                platform="trendsi",
                crawl_type="brand",
                url=url,
                base_url=base_url,
                handle=str(brand_id),
                category_slug=str(category_id) if category_id else None,
                query=str(brand_name).strip() or None,
            )

    # /classify/Category/Kids/Dresses  hoặc  /classify/Product/Jewelry & Beauty
    if path.startswith("/classify/Category/") or path.startswith("/classify/Product/"):
        parts = [unquote(segment) for segment in path.strip("/").split("/")[2:] if segment]
        if parts:
            category_path = "/".join(parts)
            return DetectedTarget(
                platform="trendsi",
                crawl_type="category",
                url=url,
                base_url=base_url,
                handle=category_path,
                category_slug=category_path,
                query=" / ".join(parts),
            )

    if path in ("", "/"):
        return DetectedTarget(
            platform="trendsi",
            crawl_type="shop",
            url=url,
            base_url=base_url,
            message="Trendsi catalog — crawl toàn bộ qua API (không cần đăng nhập).",
        )

    return DetectedTarget(
        platform="trendsi",
        crawl_type="unknown",
        url=url,
        base_url=base_url,
        message=(
            "Trendsi: dán link **/brand?...** · **/classify/Category/...** · "
            "**/classify/Product/...** · **/products/detail?id=...**."
        ),
    )


def _detect_magento(url: str, path: str, parsed) -> DetectedTarget:
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    url_key = MagentoGraphqlCrawler.path_to_url_key(path)

    if path in ("", "/"):
        return DetectedTarget(
            platform="magento",
            crawl_type="shop",
            url=url,
            base_url=base_url,
            message="Magento/Jomashop — crawl các danh mục gốc qua GraphQL.",
        )

    if url_key:
        # Phân loại category/product khi refine (gọi GraphQL).
        return DetectedTarget(
            platform="magento",
            crawl_type="category",
            url=url,
            base_url=base_url,
            handle=url_key,
            category_slug=url_key,
            query=url_key.replace("-", " ").title(),
        )

    return DetectedTarget(
        platform="magento",
        crawl_type="unknown",
        url=url,
        base_url=base_url,
        message="Magento: dán link category/product dạng **/jewelry.html**.",
    )


def _refine_magento_target(
    target: DetectedTarget,
    session: requests.Session | None = None,
) -> DetectedTarget:
    if target.platform != "magento":
        return target
    if target.crawl_type in {"shop", "product"} and target.handle:
        return target
    if target.crawl_type == "shop":
        return target

    url_key = MagentoGraphqlCrawler.path_to_url_key(
        target.handle or target.category_slug or urlparse(target.url).path
    )
    if not url_key:
        return DetectedTarget(
            platform="magento",
            crawl_type="shop",
            url=target.url,
            base_url=target.base_url,
        )

    crawler = MagentoGraphqlCrawler(target.base_url or target.url)
    if session is not None:
        crawler.session.headers.update(session.headers)
        if session.proxies:
            crawler.session.proxies.update(session.proxies)

    try:
        crawl_type, resolved_key = crawler.classify_path(url_key)
    except requests.RequestException:
        return target

    if crawl_type == "category":
        category = crawler.fetch_category(resolved_key) or {}
        name = str(category.get("name") or resolved_key.replace("-", " ").title())
        return DetectedTarget(
            platform="magento",
            crawl_type="category",
            url=target.url,
            base_url=target.base_url,
            handle=resolved_key,
            category_slug=resolved_key,
            query=name,
        )

    if crawl_type == "product":
        return DetectedTarget(
            platform="magento",
            crawl_type="product",
            url=target.url,
            base_url=target.base_url,
            handle=resolved_key,
            query=resolved_key,
        )

    return DetectedTarget(
        platform="magento",
        crawl_type="unknown",
        url=target.url,
        base_url=target.base_url,
        handle=url_key,
        message=(
            f"Magento: không tìm thấy category/product cho `{url_key}`. "
            "Thử link dạng `/jewelry.html` hoặc `/sku-slug.html`."
        ),
    )


def _detect_fineartamerica(url: str, path: str) -> DetectedTarget:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    base_url = (
        "https://pixels.com"
        if "pixels.com" in host
        else "https://fineartamerica.com"
    )
    path_lower = (path or "").lower()

    if "/featured/" in path_lower and path_lower.rstrip("/").endswith(".html"):
        return DetectedTarget(
            platform="fineartamerica",
            crawl_type="product",
            url=url,
            base_url=base_url,
        )

    if "sitemap" in path_lower:
        return DetectedTarget(
            platform="fineartamerica",
            crawl_type="sitemap",
            url=url,
            base_url=base_url,
            message="Crawl theo sitemap Fine Art America",
        )

    if path in ("", "/"):
        return DetectedTarget(
            platform="fineartamerica",
            crawl_type="shop",
            url=url,
            base_url=base_url,
            message="Trang chủ → sitemap popular products (không phải toàn bộ marketplace)",
        )

    # artist / art / shop / collections
    if any(
        part in path_lower
        for part in ("/profiles/", "/art/", "/shop/", "/collections/", "/subjects/")
    ):
        return DetectedTarget(
            platform="fineartamerica",
            crawl_type="category",
            url=url,
            base_url=base_url,
            category_slug=path.strip("/").split("/")[-1] or None,
        )

    return DetectedTarget(
        platform="fineartamerica",
        crawl_type="category",
        url=url,
        base_url=base_url,
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
    path_lower = (path or "").lower()

    # sitemap_products_1.xml?... hoặc /sitemap.xml → crawl theo sitemap
    if "sitemap" in path_lower and (
        "product" in path_lower or path_lower.rstrip("/").endswith("sitemap.xml")
    ):
        return DetectedTarget(
            platform="shopify",
            crawl_type="sitemap",
            url=url,
            base_url=base_url,
            message="Crawl theo sitemap sản phẩm",
        )

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
            category_slug=category_match.group(1).strip("/"),
        )

    if path.rstrip("/") in ("/shop", "/products"):
        return DetectedTarget(
            platform="woocommerce",
            crawl_type="shop",
            url=url,
            base_url=base_url,
        )

    custom = WOOCOMMERCE_CUSTOM_CATEGORY.match(path.rstrip("/") + "/")
    if custom:
        return DetectedTarget(
            platform="woocommerce",
            crawl_type="category",
            url=url,
            base_url=base_url,
            category_slug=custom.group(1).strip("/"),
            handle=custom.group(1).strip("/"),
        )

    return DetectedTarget(
        platform="woocommerce",
        crawl_type="unknown",
        url=url,
        base_url=base_url,
    )


def _detect_teemill(url: str, host: str, path: str) -> DetectedTarget:
    base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    product_match = TEEMILL_PRODUCT.search(path)
    if product_match:
        handle = product_match.group(1)
        return DetectedTarget(
            platform="teemill",
            crawl_type="product",
            url=url,
            base_url=base_url,
            handle=handle,
        )

    collection_match = TEEMILL_COLLECTION.search(path)
    if collection_match:
        handle = collection_match.group(1)
        if handle.lower() in {"all-products", "all"}:
            return DetectedTarget(
                platform="teemill",
                crawl_type="shop",
                url=url,
                base_url=base_url,
            )
        return DetectedTarget(
            platform="teemill",
            crawl_type="category",
            url=url,
            base_url=base_url,
            handle=handle,
            category_slug=handle,
        )

    if path.rstrip("/") in ("", "/"):
        return DetectedTarget(
            platform="teemill",
            crawl_type="shop",
            url=url,
            base_url=base_url,
        )

    return DetectedTarget(
        platform="teemill",
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
