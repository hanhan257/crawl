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


def parse_proxy_list(proxy_input: str | None) -> list[str]:
    """Parse nhiều proxy — mỗi dòng một proxy host:port:user:pass."""
    if not proxy_input or not str(proxy_input).strip():
        return []
    proxies: list[str] = []
    for line in str(proxy_input).splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = parse_proxy_input(line)
        if parsed:
            proxies.append(parsed)
    return proxies


class ProxyRotator:
    """Luân phiên proxy — đổi sang proxy kế khi gọi rotate()."""

    def __init__(self, proxy_input: str | None = None):
        self._proxies = parse_proxy_list(proxy_input)
        self._index = 0

    @property
    def count(self) -> int:
        return len(self._proxies)

    @property
    def has_proxy(self) -> bool:
        return bool(self._proxies)

    def current(self) -> str | None:
        if not self._proxies:
            return None
        return self._proxies[self._index % len(self._proxies)]

    def rotate(self) -> str | None:
        if not self._proxies:
            return None
        if len(self._proxies) > 1:
            self._index = (self._index + 1) % len(self._proxies)
        return self.current()


def normalize_proxy_url(proxy_url: str | None) -> str | None:
    proxies = parse_proxy_list(proxy_url)
    if proxies:
        return proxies[0]
    return parse_proxy_input(proxy_url)


def mask_proxy_url(proxy_url: str | None) -> str:
    proxies = parse_proxy_list(proxy_url)
    if len(proxies) > 1:
        first = re.sub(r":([^:@/]+)@", ":***@", proxies[0])
        return f"{first} (+{len(proxies) - 1} proxy)"
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
    proxies = parse_proxy_list(proxy_url)
    if not proxies:
        single = parse_proxy_input(proxy_url)
        if not single:
            return False, "Chưa nhập proxy"
        proxies = [single]

    errors: list[str] = []
    test_limit = min(len(proxies), 3)
    for index, proxy in enumerate(proxies[:test_limit], start=1):
        session = create_http_session(proxy, for_redbubble=True)
        try:
            response = session.get("https://www.redbubble.com/", timeout=25)
            if response.status_code == 200:
                masked = re.sub(r":([^:@/]+)@", ":***@", proxy)
                if len(proxies) > 1:
                    return (
                        True,
                        f"OK proxy #{index}/{len(proxies)} · {masked} · pool **{len(proxies)}** proxy",
                    )
                return True, f"Kết nối OK qua {masked}"
            errors.append(f"#{index} HTTP {response.status_code}")
        except requests.RequestException as exc:
            errors.append(f"#{index} {exc}")
        finally:
            session.close()

    summary = "; ".join(errors[:2])
    return False, f"Thử {test_limit}/{len(proxies)} proxy đều lỗi: {summary}"
