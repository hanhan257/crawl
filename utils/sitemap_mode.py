"""Ép crawl theo sitemap — khách dán link shop hoặc link sitemap đều được."""

from __future__ import annotations

from urllib.parse import urlparse

from utils.detector import DetectedTarget, UnsupportedUrlError, resolve_url

# Nền tảng có crawl_type=sitemap riêng trong crawl_runner.
SITEMAP_PLATFORMS = frozenset({"shopify", "fineartamerica"})


def looks_like_sitemap_url(url: str) -> bool:
    path = (urlparse(url.strip()).path or "").lower()
    return "sitemap" in path and (
        "product" in path or path.rstrip("/").endswith("sitemap.xml")
    )


def shop_sitemap_index_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base.startswith("http"):
        base = f"https://{base}"
    return f"{base}/sitemap.xml"


def apply_sitemap_mode(
    url: str,
    *,
    force: bool,
) -> tuple[str, DetectedTarget | None, str]:
    """
    Trả về (url_crawl, target|None, ghi_chú).

    - force=False: giữ nguyên URL.
    - force=True + đã là sitemap: giữ nguyên.
    - force=True + shop/homepage (Shopify / FAA): đổi sang /sitemap.xml.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        return cleaned, None, ""

    try:
        target = resolve_url(cleaned)
    except UnsupportedUrlError:
        return cleaned, None, ""

    if not force:
        return cleaned, target, ""

    if looks_like_sitemap_url(cleaned) or target.crawl_type == "sitemap":
        return cleaned, target, ""

    if target.platform not in SITEMAP_PLATFORMS:
        return (
            cleaned,
            target,
            (
                f"Crawl theo sitemap chỉ áp dụng Shopify / Fine Art America "
                f"(đang nhận diện: {target.platform}). Giữ cách crawl mặc định."
            ),
        )

    # Chỉ ép từ shop / category / search — giữ nguyên 1 SP.
    if target.crawl_type == "product":
        return (
            cleaned,
            target,
            "Link 1 sản phẩm — bỏ qua chế độ sitemap, crawl đúng SP đó.",
        )

    sitemap_url = shop_sitemap_index_url(target.base_url or cleaned)
    try:
        sitemap_target = resolve_url(sitemap_url)
    except UnsupportedUrlError:
        sitemap_target = DetectedTarget(
            platform=target.platform,
            crawl_type="sitemap",
            url=sitemap_url,
            base_url=target.base_url,
            message="Crawl theo sitemap sản phẩm",
        )
    return (
        sitemap_url,
        sitemap_target,
        f"Đã chuyển link shop → `{sitemap_url}` (crawl toàn bộ sitemap sản phẩm).",
    )
