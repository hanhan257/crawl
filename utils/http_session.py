from __future__ import annotations

import re
from urllib.parse import quote

import requests

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

REDBUBBLE_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def parse_proxy_input(proxy_url: str | None) -> str | None:
    """
    Hỗ trợ các định dạng:
    - host:port:user:pass          → http://user:pass@host:port
    - user:pass@host:port
    - host:port
    - http://user:pass@host:port
    - socks5://user:pass@host:port
    """
    if not proxy_url or not str(proxy_url).strip():
        return None

    cleaned = str(proxy_url).strip()

    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", cleaned):
        return cleaned

    if "@" in cleaned:
        return f"http://{cleaned}"

    parts = cleaned.split(":")
    if len(parts) == 4 and parts[1].isdigit():
        host, port, user, password = parts
        user_q = quote(user, safe="")
        pass_q = quote(password, safe="")
        return f"http://{user_q}:{pass_q}@{host}:{port}"

    if len(parts) == 2 and parts[1].isdigit():
        return f"http://{cleaned}"

    return f"http://{cleaned}"


def normalize_proxy_url(proxy_url: str | None) -> str | None:
    return parse_proxy_input(proxy_url)


def mask_proxy_url(proxy_url: str | None) -> str:
    parsed = parse_proxy_input(proxy_url)
    if not parsed:
        return ""
    return re.sub(r":([^:@/]+)@", ":***@", parsed)


def create_http_session(
    proxy_url: str | None = None,
    *,
    for_redbubble: bool = False,
) -> requests.Session:
    session = requests.Session()
    headers = dict(REDBUBBLE_HEADERS if for_redbubble else {"User-Agent": DEFAULT_USER_AGENT})
    session.headers.update(headers)

    proxy = normalize_proxy_url(proxy_url)
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
        if proxy.lower().startswith("socks"):
            try:
                import socks  # noqa: F401
            except ImportError as exc:
                raise ImportError(
                    "Proxy SOCKS cần cài PySocks: pip install PySocks"
                ) from exc
    return session


def reset_session(
    session: requests.Session,
    proxy_url: str | None = None,
    *,
    for_redbubble: bool = False,
) -> requests.Session:
    session.close()
    return create_http_session(proxy_url, for_redbubble=for_redbubble)


def test_proxy_connection(proxy_url: str | None) -> tuple[bool, str]:
    proxy = normalize_proxy_url(proxy_url)
    if not proxy:
        return False, "Chưa nhập proxy"

    session = create_http_session(proxy_url, for_redbubble=True)
    try:
        response = session.get("https://www.redbubble.com/", timeout=25)
        if response.status_code == 200:
            return True, f"Kết nối OK qua {mask_proxy_url(proxy_url)}"
        return False, f"Proxy phản hồi HTTP {response.status_code}"
    except requests.RequestException as exc:
        return False, f"Lỗi: {exc}"
