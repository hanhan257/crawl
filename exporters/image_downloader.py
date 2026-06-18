from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def safe_filename(title: str, product_id: str, sku: str | None, index: int) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (title or "product").lower()).strip("-")[:50]
    suffix = re.sub(r"[^a-z0-9]", "", (sku or product_id).lower())[-10:] or str(index)
    return f"{base}-{suffix}-{index}.jpg"


def _guess_extension(content_type: str, url: str) -> str:
    content_type = (content_type or "").lower()
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        if path.endswith(ext):
            return ext if ext != ".jpeg" else ".jpg"
    return ".jpg"


def download_image(
    url: str,
    dest: Path,
    referer: str | None = None,
    session: requests.Session | None = None,
) -> bool:
    session = session or requests.Session()
    headers = dict(DEFAULT_HEADERS)
    headers["Referer"] = referer or "https://www.redbubble.com/"

    try:
        response = session.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            return False
        content_type = response.headers.get("content-type", "")
        if "image" not in content_type and len(response.content) < 1000:
            return False

        ext = _guess_extension(content_type, url)
        if dest.suffix != ext:
            dest = dest.with_suffix(ext)

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        return True
    except requests.RequestException:
        return False


def download_product_images(
    product,
    images_dir: Path,
    session: requests.Session | None = None,
    max_images: int = 5,
) -> list[str]:
    session = session or requests.Session()
    saved_names: list[str] = []
    referer = product.url or "https://www.redbubble.com/"

    for index, image_url in enumerate(product.image_urls[:max_images], start=1):
        filename = safe_filename(product.title, product.id, product.sku, index)
        dest = images_dir / filename
        if download_image(image_url, dest, referer=referer, session=session):
            saved_names.append(dest.name)

    return saved_names
