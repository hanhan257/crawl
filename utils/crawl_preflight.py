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


def check_shop_reachable(url: str) -> str | None:
    """Return error message if shop cannot be reached, else None."""
    cleaned = url.strip()
    if not cleaned:
        return "Chưa có link shop."

    normalized = normalize_url(cleaned)
    host = urlparse(normalized).netloc

    try:
        target = resolve_url(cleaned)
    except UnsupportedUrlError as exc:
        session = requests.Session()
        session.headers["User-Agent"] = DEFAULT_USER_AGENT
        try:
            session.get(normalized, timeout=PREFLIGHT_TIMEOUT)
        except requests.ConnectionError as conn_exc:
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
    session = requests.Session()
    session.headers["User-Agent"] = DEFAULT_USER_AGENT

    try:
        if target.platform == "woocommerce":
            return _check_woocommerce(session, base, host, target)
        if target.platform == "shopify":
            return _check_shopify(session, base, host, target)
        if target.platform == "generic-web":
            return _check_generic(session, base, host)
        if target.platform == "redbubble":
            response = session.get(
                "https://www.redbubble.com/shop/t-shirts",
                timeout=PREFLIGHT_TIMEOUT,
            )
            if response.status_code >= 500:
                return "Redbubble đang lỗi — thử lại sau hoặc bật Proxy."
            return None
        if target.platform == "trendsi":
            response = session.get(normalized, timeout=PREFLIGHT_TIMEOUT)
            if response.status_code == 200:
                return None
            return (
                f"Trendsi trả HTTP {response.status_code}. "
                "Thử tắt **Proxy** hoặc giảm **Luồng crawl**."
            )
    except requests.ConnectionError as exc:
        return _connection_error_message(cleaned, exc)
    except requests.Timeout:
        return (
            f"Shop **{host}** phản hồi quá chậm. "
            "Thử **Luồng crawl = 4–8** rồi bấm crawl lại."
        )
    except requests.RequestException as exc:
        return f"Không kiểm tra được shop: {exc}"

    return None


def _check_woocommerce(
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
        return (
            f"WooCommerce **{host}** chặn Store API ({response.status_code}). "
            "Thêm **Woo key/secret** ở expander API."
        )

    return (
        f"Store API **{host}** trả HTTP {response.status_code}. "
        "Link có thể sai domain hoặc shop không phải WooCommerce."
    )


def _check_shopify(
    session: requests.Session,
    base: str,
    host: str,
    target: DetectedTarget | None = None,
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

    response = session.get(
        f"{base}/products.json",
        params={"limit": 1},
        timeout=PREFLIGHT_TIMEOUT,
    )
    if response.status_code == 200:
        return None
    if response.status_code in {401, 403}:
        return f"Shopify **{host}** cần access token."
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
