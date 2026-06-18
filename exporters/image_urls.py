from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

DEFAULT_MIN_IMAGE_COLUMNS = 5
MAX_IMAGE_COLUMNS_CAP = 50

IMAGE_EXPORT_MODES = {
    "comma": "Một cột Images — nhiều URL cách nhau dấu phẩy (chuẩn WooCommerce)",
    "split_columns": "Tách cột — Images + Image 2…N (Trendsi/TikTok, Woo chỉ đọc cột đầu)",
    "first_only": "Chỉ ảnh đầu — Images (1 URL)",
}


def _fix_single_slash_scheme(url: str) -> str:
    url = re.sub(r"^(https?):/([^/])", r"\1://\2", url, flags=re.IGNORECASE)
    return url


def _strip_doubled_site_prefix(url: str) -> str:
    patterns = [
        r"^https?://[^/]+/(https?://.+)$",
        r"^https?://[^/]+/(https?:/.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, url, re.IGNORECASE)
        if match:
            return _fix_single_slash_scheme(match.group(1))
    return url


def is_exportable_image_url(url: str) -> bool:
    cleaned = normalize_image_url(url)
    if not cleaned or not cleaned.lower().startswith("http"):
        return False
    lower = cleaned.lower()

    # Chặn URL lỗi sau import Woo (placeholder, site đích bị ghép sai)
    if "woocommerce-placeholder" in lower:
        return False
    if "veexovar.com" in lower:
        return False
    if "placeholder" in lower and "wp-content" in lower:
        return False
    if re.search(r"https?://[^/]+/https?:", lower):
        return False

    return True


def normalize_image_url(url: str) -> str:
    cleaned = str(url or "").strip()
    if not cleaned:
        return ""

    cleaned = _fix_single_slash_scheme(cleaned)
    cleaned = _strip_doubled_site_prefix(cleaned)

    if cleaned.startswith("//"):
        cleaned = f"https:{cleaned}"
    if cleaned.startswith("/"):
        return ""
    if cleaned.lower().startswith(("wp-content/", "uploads/")):
        return ""
    if not re.match(r"^https?://", cleaned, re.IGNORECASE):
        return ""

    parsed = urlparse(cleaned)
    return urlunparse(("https", parsed.netloc, parsed.path or "", parsed.params, parsed.query, parsed.fragment))


def collect_product_image_urls(product, max_images: int | None = None) -> list[str]:
    """max_images=None hoặc 0 → lấy tất cả ảnh (tối đa MAX_IMAGE_COLUMNS_CAP)."""
    limit = MAX_IMAGE_COLUMNS_CAP
    if max_images and max_images > 0:
        limit = min(max_images, MAX_IMAGE_COLUMNS_CAP)

    urls: list[str] = []
    for url in product.image_urls or []:
        if not url or not str(url).strip():
            continue
        normalized = normalize_image_url(url)
        if not normalized or not is_exportable_image_url(normalized):
            continue
        if normalized not in urls:
            urls.append(normalized)
        if len(urls) >= limit:
            break
    return urls


def max_exportable_images_in_products(products, cap: int | None = None) -> int:
    """Số ảnh tối đa trong batch — quyết định số cột Image 2…N."""
    hard_cap = cap if cap and cap > 0 else MAX_IMAGE_COLUMNS_CAP
    max_count = 0
    for product in products:
        count = len(collect_product_image_urls(product, max_images=hard_cap))
        max_count = max(max_count, count)
    return max(max_count, 1)


def resolve_image_column_count(
    products,
    image_mode: str,
    max_images_per_product: int | None = None,
) -> int:
    if image_mode in {"comma", "pipe", "first_only"}:
        return 1
    return max_exportable_images_in_products(products, cap=max_images_per_product)


def format_woocommerce_images(product, max_images: int | None = None) -> str:
    return ",".join(collect_product_image_urls(product, max_images=max_images))


def format_woocommerce_images_fields(
    product,
    mode: str = "comma",
    max_images: int | None = None,
) -> dict[str, str]:
    urls = collect_product_image_urls(product, max_images=max_images)
    fields: dict[str, str] = {}

    if mode in {"comma", "pipe"}:
        fields["Images"] = ",".join(urls)
        return fields

    if mode == "first_only":
        fields["Images"] = urls[0] if urls else ""
        return fields

    column_count = max_images if max_images and max_images > 0 else max(len(urls), 1)
    fields["Images"] = urls[0] if urls else ""
    if column_count <= 1:
        return fields
    for index in range(2, column_count + 1):
        fields[f"Image {index}"] = urls[index - 1] if len(urls) >= index else ""
    return fields


def woocommerce_image_column_names(
    mode: str = "split_columns",
    max_images: int = DEFAULT_MIN_IMAGE_COLUMNS,
) -> list[str]:
    if mode != "split_columns":
        return ["Images"]
    count = max(max_images, 1)
    if count <= 1:
        return ["Images"]
    return ["Images"] + [f"Image {i}" for i in range(2, count + 1)]
