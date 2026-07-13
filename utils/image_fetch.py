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

    lower = url.lower()
    session = create_http_session(proxy_url, for_redbubble="redbubble.net" in lower)
    headers: dict[str, str] = {
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    elif "redbubble.net" in lower:
        headers["Referer"] = "https://www.redbubble.com/"
    elif "silverbene.com" in lower:
        headers["Referer"] = "https://silverbene.com/"
    elif "jomashop.com" in lower:
        headers["Referer"] = "https://www.jomashop.com/"

    try:
        response = session.get(url, headers=headers, timeout=25)
        if response.status_code != 200:
            return None
        content_type = (response.headers.get("content-type") or "").lower()
        if "image" not in content_type and len(response.content) < 500:
            return None
        # CDN đôi khi trả HTML/JS lỗi thay vì ảnh.
        if "javascript" in content_type or "text/html" in content_type:
            return None
        if response.content[:20].lstrip().startswith((b"<", b"{", b"function")):
            return None
        return response.content
    except requests.RequestException:
        return None
