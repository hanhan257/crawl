from __future__ import annotations

from utils.detector import DetectedTarget, UnsupportedUrlError, resolve_url

PLATFORM_LABELS = {
    "shopify": "Shopify",
    "woocommerce": "WooCommerce",
    "redbubble": "Redbubble",
    "generic-web": "Web thuần (Next.js)",
}

CRAWL_TYPE_LABELS = {
    "shop": "Crawl toàn bộ shop",
    "category": "Crawl category / collection",
    "product": "Crawl 1 sản phẩm",
    "unknown": "Chưa xác định loại link",
}

CRAWL_HINTS = {
    ("shopify", "shop"): "Link trang chủ shop → lấy tất cả sản phẩm qua /products.json",
    ("shopify", "category"): "Link collection → lấy SP trong collection đó",
    ("shopify", "product"): "Link sản phẩm → lấy 1 SP",
    ("woocommerce", "shop"): "Link /shop → lấy toàn bộ sản phẩm",
    ("woocommerce", "category"): "Link product-category → lấy SP trong danh mục",
    ("woocommerce", "product"): "Link sản phẩm → lấy 1 SP",
    ("redbubble", "category"): "Link /shop/... → crawl kết quả tìm kiếm / category",
    ("redbubble", "product"): "Link /i/... → crawl 1 sản phẩm",
    ("redbubble", "unknown"): "Trang chủ — dán link /shop/từ-khóa hoặc /i/.../sản-phẩm",
    ("generic-web", "shop"): "Trang chủ /shop → lấy SP qua sitemap-products hoặc API /shop",
    ("generic-web", "category"): "Link /shop/danh-mục hoặc /category/... → crawl theo danh mục",
    ("generic-web", "product"): "Link /product/... → crawl 1 sản phẩm (JSON Next.js)",
}


def describe_url(url: str) -> dict:
    if not url.strip():
        return {"ok": False, "message": "Nhập link để nhận diện nền tảng.", "crawl_ready": False}

    try:
        target = resolve_url(url.strip())
    except UnsupportedUrlError as exc:
        if getattr(exc, "platform", None) == "redbubble":
            target = DetectedTarget(
                platform="redbubble",
                crawl_type="unknown",
                url=url.strip(),
                base_url="https://www.redbubble.com",
                message=str(exc),
            )
            platform = PLATFORM_LABELS["redbubble"]
            crawl = CRAWL_TYPE_LABELS["unknown"]
            hint = CRAWL_HINTS.get(("redbubble", "unknown"), "")
            return {
                "ok": True,
                "crawl_ready": False,
                "target": target,
                "platform": platform,
                "crawl_type": crawl,
                "detail": "",
                "hint": hint,
                "message": f"{platform} · {crawl}",
                "notice": str(exc),
            }
        return {"ok": False, "message": str(exc), "crawl_ready": False}

    platform = PLATFORM_LABELS.get(target.platform, target.platform)
    crawl = CRAWL_TYPE_LABELS.get(target.crawl_type, target.crawl_type)
    hint = CRAWL_HINTS.get((target.platform, target.crawl_type), "")
    crawl_ready = target.crawl_type != "unknown"

    detail = ""
    if target.crawl_type == "category":
        detail = target.handle or target.category_slug or target.query or ""
    elif target.crawl_type == "product":
        detail = target.handle or ""

    notice = ""
    if not crawl_ready and target.message:
        notice = target.message

    return {
        "ok": True,
        "crawl_ready": crawl_ready,
        "target": target,
        "platform": platform,
        "crawl_type": crawl,
        "detail": detail,
        "hint": hint,
        "message": f"{platform} · {crawl}" + (f" · `{detail}`" if detail else ""),
        "notice": notice,
    }
