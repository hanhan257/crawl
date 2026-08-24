"""HTTP qua curl_cffi — vượt Cloudflare khi requests bị 403."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

try:
    from curl_cffi import requests as curl_requests

    CURL_CFFI_AVAILABLE = True
except ImportError:
    curl_requests = None  # type: ignore[assignment,misc]
    CURL_CFFI_AVAILABLE = False

DEFAULT_IMPERSONATE = "chrome120"
_CLOUDFLARE_MARKERS = (
    "cloudflare",
    "cf-browser-verification",
    "just a moment",
    "security check",
)


class CurlResponse:
    """Tối thiểu tương thích requests.Response cho Store API."""

    __slots__ = ("status_code", "headers", "text", "_json")

    def __init__(
        self,
        status_code: int,
        headers: dict[str, str],
        text: str,
        *,
        parsed_json: Any | None = None,
    ):
        self.status_code = status_code
        self.headers = headers
        self.text = text
        self._json = parsed_json

    def json(self) -> Any:
        if self._json is None:
            self._json = json.loads(self.text)
        return self._json


def curl_cffi_available() -> bool:
    return CURL_CFFI_AVAILABLE


def looks_like_cloudflare_block(status_code: int, text: str = "") -> bool:
    if status_code not in (403, 503):
        return False
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _CLOUDFLARE_MARKERS)


def curl_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float | tuple[float, float] = 60,
    proxy: str | None = None,
    impersonate: str = DEFAULT_IMPERSONATE,
) -> CurlResponse | None:
    if not CURL_CFFI_AVAILABLE or curl_requests is None:
        return None

    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urlencode(params)}"

    kwargs: dict[str, Any] = {"timeout": timeout, "impersonate": impersonate}
    if proxy:
        kwargs["proxy"] = proxy

    try:
        response = curl_requests.get(url, **kwargs)
    except Exception:
        return None

    headers = {str(k): str(v) for k, v in (response.headers or {}).items()}
    text = response.text or ""
    parsed_json: Any | None = None
    if response.status_code == 200 and text.lstrip().startswith(("[", "{")):
        try:
            parsed_json = response.json()
        except (json.JSONDecodeError, ValueError, TypeError):
            parsed_json = None

    return CurlResponse(
        response.status_code,
        headers,
        text,
        parsed_json=parsed_json,
    )


def curl_get_json_list(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float | tuple[float, float] = 30,
    proxy: str | None = None,
    impersonate: str = DEFAULT_IMPERSONATE,
) -> tuple[list[Any] | None, int, dict[str, str]]:
    """GET JSON array — dùng cho Woo Store API probe."""
    response = curl_get(
        url,
        params=params,
        timeout=timeout,
        proxy=proxy,
        impersonate=impersonate,
    )
    if response is None:
        return None, 0, {}
    if response.status_code != 200:
        return None, response.status_code, response.headers
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError, TypeError):
        return None, response.status_code, response.headers
    if isinstance(data, list):
        return data, response.status_code, response.headers
    return None, response.status_code, response.headers
