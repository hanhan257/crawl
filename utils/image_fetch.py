from __future__ import annotations

import requests

from utils.http_session import create_http_session


def fetch_image_bytes(
    url: str,
    proxy_url: str | None = None,
    referer: str | None = None,
) -> bytes | None:
    if not url or not str(url).strip():
        return None

    session = create_http_session(proxy_url, for_redbubble="redbubble.net" in url.lower())
    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    elif "redbubble.net" in url.lower():
        headers["Referer"] = "https://www.redbubble.com/"
        headers["Accept"] = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"

    try:
        response = session.get(url, headers=headers, timeout=25)
        if response.status_code != 200:
            return None
        content_type = (response.headers.get("content-type") or "").lower()
        if "image" not in content_type and len(response.content) < 500:
            return None
        return response.content
    except requests.RequestException:
        return None
