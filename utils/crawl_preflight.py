from __future__ import annotations

from urllib.parse import urlparse

import requests

from utils.detector import DetectedTarget, UnsupportedUrlError, normalize_url, resolve_url

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
PREFLIGHT_TIMEOUT = (10, 25)


def _connection_error_message(url: str, exc: Exception) -> str:
    host = urlparse(normalize_url(url)).netloc or url
    text = str(exc).lower()
    if "getaddrinfo failed" in text or "failed to resolve" in text or "name or service not known" in text:
        return (
            f"Domain **{host}** không tồn tại hoặc sai chính tả. "
            "Shop WooCommerce đúng là **`nederlandsjersey.com`** (không phải domain khác)."
        )
    return (
        f"Không kết nối được **{host}** — kiểm tra lại link hoặc mạng/proxy."
    )


def check_shop_reachable(url: str, proxy_url: str | None = None) -> str | None:
    """Return error message if shop cannot be reached, else None."""
    cleaned = url.strip()
    if not cleaned:
        return "Chưa có link shop."

    normalized = normalize_url(cleaned)
    host = urlparse(normalized).netloc

    try:
        target = resolve_url(cleaned)
    except UnsupportedUrlError as exc:
        session = _preflight_session(proxy_url)
        try:
            session.get(normalized, timeout=PREFLIGHT_TIMEOUT)
        except requests.ConnectionError as conn_exc:
            # Proxy hỏng → thử direct trước khi báo lỗi nhận diện.
            if _used_or_saved_proxy(proxy_url):
                try:
                    _preflight_session(None, allow_saved_proxy=False).get(
                        normalized, timeout=PREFLIGHT_TIMEOUT
                    )
                except requests.RequestException:
                    return _connection_error_message(cleaned, conn_exc)
                return str(exc)
            return _connection_error_message(cleaned, conn_exc)
        except requests.Timeout:
            return (
                f"Shop **{host}** phản hồi quá chậm. "
                "Thử **Luồng crawl = 4–8** rồi bấm crawl lại."
            )
        except requests.RequestException:
            pass
        return str(exc)

    base = (target.base_url or cleaned).rstrip("/")
    session = _preflight_session(proxy_url)

    try:
        if target.platform == "woocommerce":
            return _check_woocommerce(session, base, host, target)
        if target.platform == "shopify":
            return _check_shopify(session, base, host, target, proxy_url=proxy_url)
        if target.platform == "generic-web":
            return _check_generic(session, base, host)
        if target.platform == "fineartamerica":
            # FAA hay 429 IP local — không chặn preflight; crawl dùng proxy/retry.
            try:
                response = session.get(
                    f"{base}/robots.txt",
                    timeout=PREFLIGHT_TIMEOUT,
                )
                if response.status_code == 200:
                    return None
                if response.status_code == 429:
                    return None
                if response.status_code >= 500:
                    return "Fine Art America đang lỗi server — thử lại sau."
            except requests.RequestException:
                return None
            return None
        if target.platform == "redbubble":
            response = session.get(
                "https://www.redbubble.com/shop/t-shirts",
                timeout=PREFLIGHT_TIMEOUT,
            )
            if response.status_code >= 500:
                return "Redbubble đang lỗi — thử lại sau hoặc bật Proxy."
            return None
        if target.platform == "trendsi":
            return _check_trendsi(session, normalized, proxy_url=proxy_url)
        if target.platform == "magento":
            from crawlers.magento_graphql import MagentoGraphqlCrawler

            if MagentoGraphqlCrawler.site_supports(base, session=session):
                return None
            # Proxy có thể chặn GraphQL — thử direct.
            if _used_or_saved_proxy(proxy_url):
                direct = _preflight_session(None, allow_saved_proxy=False)
                if MagentoGraphqlCrawler.site_supports(base, session=direct):
                    return None
            return (
                f"Magento **{host}** không mở được GraphQL. "
                "Thử tắt **Proxy** hoặc kiểm tra lại domain."
            )
    except requests.ConnectionError as exc:
        # Nhiều shop (Trendsi, …) chặn proxy — retry không proxy trước khi fail.
        if _used_or_saved_proxy(proxy_url):
            try:
                return _check_reachable_direct(cleaned, target, normalized, base, host)
            except requests.RequestException:
                pass
        return _connection_error_message(cleaned, exc)
    except requests.Timeout:
        if _used_or_saved_proxy(proxy_url):
            try:
                return _check_reachable_direct(cleaned, target, normalized, base, host)
            except requests.RequestException:
                pass
        return (
            f"Shop **{host}** phản hồi quá chậm. "
            "Thử **Luồng crawl = 4–8** rồi bấm crawl lại."
        )
    except requests.RequestException as exc:
        return f"Không kiểm tra được shop: {exc}"

    return None


def _used_or_saved_proxy(proxy_url: str | None) -> bool:
    if (proxy_url or "").strip():
        return True
    try:
        from utils.credentials import CrawlCredentials

        return bool((CrawlCredentials.load().proxy_url or "").strip())
    except Exception:
        return False


def _check_reachable_direct(
    cleaned: str,
    target: DetectedTarget,
    normalized: str,
    base: str,
    host: str,
) -> str | None:
    """Preflight lại không dùng proxy (proxy list trong config hay chết)."""
    session = _preflight_session(None, allow_saved_proxy=False)
    if target.platform == "woocommerce":
        return _check_woocommerce(session, base, host, target)
    if target.platform == "shopify":
        return _check_shopify(session, base, host, target, proxy_url=None)
    if target.platform == "generic-web":
        return _check_generic(session, base, host)
    if target.platform == "trendsi":
        return _check_trendsi(session, normalized, proxy_url=None, allow_saved=False)
    if target.platform == "magento":
        from crawlers.magento_graphql import MagentoGraphqlCrawler

        if MagentoGraphqlCrawler.site_supports(base, session=session):
            return None
        return (
            f"Magento **{host}** không mở được GraphQL. "
            "Thử tắt **Proxy** hoặc kiểm tra lại domain."
        )
    if target.platform in {"fineartamerica", "redbubble"}:
        return None
    response = session.get(normalized, timeout=PREFLIGHT_TIMEOUT)
    if response.status_code == 200:
        return None
    return f"Shop **{host}** trả HTTP {response.status_code}."


def _check_trendsi(
    session: requests.Session,
    normalized: str,
    *,
    proxy_url: str | None = None,
    allow_saved: bool = True,
) -> str | None:
    """Trendsi thường chặn proxy — thử session hiện tại rồi direct; không chặn crawl cứng."""
    try:
        response = session.get(normalized, timeout=PREFLIGHT_TIMEOUT)
        if response.status_code == 200:
            return None
        status = response.status_code
    except requests.RequestException:
        status = None

    if allow_saved and _used_or_saved_proxy(proxy_url):
        try:
            direct = _preflight_session(None, allow_saved_proxy=False)
            response = direct.get(normalized, timeout=PREFLIGHT_TIMEOUT)
            if response.status_code == 200:
                return None
            status = response.status_code
        except requests.RequestException:
            pass

    # Nhận diện đã OK — cho crawl chạy (TrendsiCrawler dùng direct khi proxy fail).
    if status is None or status in {403, 429, 503}:
        return None
    if status == 200:
        return None
    return (
        f"Trendsi trả HTTP {status}. "
        "Thử tắt **Proxy** hoặc giảm **Luồng crawl**."
    )


def _preflight_session(
    proxy_url: str | None = None,
    *,
    allow_saved_proxy: bool = True,
) -> requests.Session:
    cleaned = (proxy_url or "").strip() or None
    if cleaned is None and allow_saved_proxy:
        try:
            from utils.credentials import CrawlCredentials

            cleaned = CrawlCredentials.load().proxy_url or None
        except Exception:
            cleaned = None
    if cleaned:
        try:
            from utils.http_session import create_http_session

            session = create_http_session(cleaned)
            session.headers["User-Agent"] = DEFAULT_USER_AGENT
            return session
        except Exception:
            pass
    session = requests.Session()
    session.headers["User-Agent"] = DEFAULT_USER_AGENT
    return session


def _check_woocommerce(
    session: requests.Session,
    base: str,
    host: str,
    target: DetectedTarget,
) -> str | None:
    err = _check_woocommerce_once(session, base, host, target)
    if err is None:
        return None
    if session.proxies or _used_or_saved_proxy(None):
        direct = _preflight_session(None, allow_saved_proxy=False)
        if direct is not session:
            direct_err = _check_woocommerce_once(direct, base, host, target)
            if direct_err is None:
                return None
    return err


def _check_woocommerce_once(
    session: requests.Session,
    base: str,
    host: str,
    target: DetectedTarget,
) -> str | None:
    params: dict[str, str | int] = {"per_page": 1}
    if target.crawl_type == "search" and target.query:
        params["search"] = target.query

    response = session.get(
        f"{base}/wp-json/wc/store/v1/products",
        params=params,
        timeout=PREFLIGHT_TIMEOUT,
    )
    if response.status_code == 200:
        data = response.json()
        if isinstance(data, list):
            return None
        return f"Store API **{host}** trả dữ liệu không hợp lệ."

    if response.status_code in {401, 403}:
        # Cloudflare — Store API vẫn mở qua curl_cffi (vd. usa.hobbypew.com).
        try:
            from utils.curl_http import curl_get_json_list

            data, curl_status, _headers = curl_get_json_list(
                f"{base}/wp-json/wc/store/v1/products",
                params=params,
                timeout=20,
            )
            if curl_status == 200 and isinstance(data, list):
                return None
        except Exception:
            pass
        # Store API khóa — vẫn crawl được qua sitemap / HTML (vd. gullprint.com).
        if _woocommerce_html_fallback_ok(session, base, target):
            return None
        return (
            f"WooCommerce **{host}** chặn Store API ({response.status_code}) "
            "và không mở được sitemap/HTML. "
            "Thêm **Woo key/secret** ở expander API, hoặc thử tắt Proxy."
        )

    return (
        f"Store API **{host}** trả HTTP {response.status_code}. "
        "Link có thể sai domain hoặc shop không phải WooCommerce."
    )


def _woocommerce_html_fallback_ok(
    session: requests.Session,
    base: str,
    target: DetectedTarget,
) -> bool:
    candidates: list[str] = []
    if target.crawl_type == "product" and target.handle:
        candidates.append(f"{base}/product/{target.handle}/")
    elif target.crawl_type == "category" and target.category_slug:
        candidates.append(f"{base}/product-category/{target.category_slug.strip('/')}/")
    elif target.crawl_type == "search" and target.query:
        from urllib.parse import quote

        candidates.append(f"{base}/?s={quote(target.query)}&post_type=product")
    else:
        candidates.extend(
            [
                f"{base}/sitemap_index.xml",
                f"{base}/product-sitemap.xml",
                f"{base}/product-sitemap1.xml",
                f"{base}/shop/",
            ]
        )

    for url in candidates:
        try:
            response = session.get(url, timeout=PREFLIGHT_TIMEOUT)
            if response.status_code != 200:
                continue
            text = response.text or ""
            lower = text.lower()
            if "product-sitemap" in lower or "/product/" in lower:
                return True
            if "single-product" in lower or "product_title" in lower:
                return True
            if "<urlset" in lower or "<sitemapindex" in lower:
                return True
        except requests.RequestException:
            continue
    return False


def _check_shopify(
    session: requests.Session,
    base: str,
    host: str,
    target: DetectedTarget | None = None,
    proxy_url: str | None = None,
) -> str | None:
    if target and target.crawl_type == "search" and target.query:
        response = session.get(
            f"{base}/search",
            params={"q": target.query, "type": "product"},
            timeout=PREFLIGHT_TIMEOUT,
        )
        if response.status_code == 200:
            return None
        if response.status_code in {401, 403}:
            return f"Shopify **{host}** chặn trang tìm kiếm ({response.status_code})."
        return f"Shopify search **{host}** trả HTTP {response.status_code}."

    if target and target.crawl_type == "sitemap":
        sitemap_url = target.url or f"{base}/sitemap.xml"
        try:
            sm = session.get(sitemap_url, timeout=PREFLIGHT_TIMEOUT)
            if sm.status_code == 200 and (
                "<urlset" in sm.text or "<sitemapindex" in sm.text or "/products/" in sm.text
            ):
                return None
            return f"Shopify sitemap **{host}** trả HTTP {sm.status_code}."
        except requests.RequestException as exc:
            return f"Không tải được sitemap Shopify **{host}**: {exc}"

    response = session.get(
        f"{base}/products.json",
        params={"limit": 1},
        timeout=PREFLIGHT_TIMEOUT,
    )
    if response.status_code == 200:
        return None
    if response.status_code in {401, 403}:
        return f"Shopify **{host}** cần access token."

    # Một số shop (vd. PopFanPrints / PrintYourWear) chặn /products.json (429)
    # nhưng GraphQL / cart.js / proxy vẫn dùng được.
    if response.status_code == 429:
        try:
            gql = session.post(
                f"{base}/api/2024-10/graphql.json",
                json={
                    "query": "{ products(first: 1) { edges { node { handle } } } }"
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=PREFLIGHT_TIMEOUT,
            )
            if gql.status_code == 200:
                payload = gql.json()
                if isinstance(payload, dict) and payload.get("data", {}).get("products"):
                    return None
        except (requests.RequestException, ValueError):
            pass
        try:
            meta = session.get(f"{base}/meta.json", timeout=PREFLIGHT_TIMEOUT)
            if meta.status_code == 200 and meta.json().get("myshopify_domain"):
                return None
        except (requests.RequestException, ValueError):
            pass
        try:
            cart = session.get(f"{base}/cart.js", timeout=PREFLIGHT_TIMEOUT)
            if cart.status_code == 200:
                data = cart.json()
                if (
                    isinstance(data, dict)
                    and "token" in data
                    and isinstance(data.get("items"), list)
                ):
                    # Shop sống — crawl qua proxy / sitemap+GraphQL.
                    return None
        except (requests.RequestException, ValueError):
            pass
        # Thử lại một lần với proxy từ config nếu session hiện tại chưa có proxy.
        if not (proxy_url or "").strip():
            try:
                from utils.credentials import CrawlCredentials

                saved = CrawlCredentials.load().proxy_url
            except Exception:
                saved = None
            if saved:
                retry = _preflight_session(saved)
                try:
                    again = retry.get(
                        f"{base}/products.json",
                        params={"limit": 1},
                        timeout=PREFLIGHT_TIMEOUT,
                    )
                    if again.status_code == 200:
                        return None
                except requests.RequestException:
                    pass
                return None  # Có proxy — cho crawl chạy, không chặn UI.
        elif (proxy_url or "").strip():
            # Đã dùng proxy mà vẫn 429 — vẫn cho crawl thử (đổi proxy/rotate).
            return None

    return f"Shopify **{host}** trả HTTP {response.status_code}."


def _check_generic(session: requests.Session, base: str, host: str) -> str | None:
    for path in ("/sitemap-products.xml", "/sitemap.xml", "/"):
        try:
            response = session.get(f"{base}{path}", timeout=PREFLIGHT_TIMEOUT)
            if response.status_code == 200:
                return None
        except requests.RequestException:
            continue
    return f"Không truy cập được web **{host}** — kiểm tra link."
