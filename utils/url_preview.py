from __future__ import annotations

from utils.detector import DetectedTarget, UnsupportedUrlError, resolve_url

PLATFORM_LABELS = {
    "shopify": "Shopify",
    "shopbase": "ShopBase",
    "woocommerce": "WooCommerce",
    "trendsi": "Trendsi",
    "redbubble": "Redbubble",
    "generic-web": "Web thuần",
}

CRAWL_TYPE_LABELS = {
    "shop": "Crawl toàn bộ shop",
    "category": "Crawl category / collection",
    "brand": "Crawl brand Trendsi",
    "search": "Crawl kết quả tìm kiếm",
    "product": "Crawl 1 sản phẩm",
    "unknown": "Chưa xác định loại link",
}

CRAWL_HINTS = {
    ("shopify", "shop"): "Link trang chủ shop → lấy tất cả sản phẩm qua /products.json",
    ("shopify", "category"): "Link collection → lấy SP trong collection đó",
    ("shopify", "search"): "Link /search?q=từ-khóa → lấy SP khớp tìm kiếm (HTML storefront)",
    ("shopify", "product"): "Link sản phẩm → lấy 1 SP",
    ("shopbase", "shop"): "Link trang chủ ShopBase (vd. nebsport.com) → crawl API catalog (~10k SP)",
    ("shopbase", "category"): "Link /collections/... → crawl collection (Football, Baseball…)",
    ("shopbase", "product"): "Link /products/... → lấy 1 SP + mô tả HTML",
    ("woocommerce", "shop"): "Link /shop → lấy toàn bộ sản phẩm",
    ("woocommerce", "category"): "Link product-category → lấy SP trong danh mục",
    ("woocommerce", "search"): "Link ?s=từ-khóa → lấy SP khớp tìm kiếm (Store API)",
    ("woocommerce", "product"): "Link sản phẩm → lấy 1 SP",
    ("trendsi", "brand"): "Link /brand?nameEn=...&id=... → crawl white-label brand (vd. CozyNest)",
    ("trendsi", "category"): "Link /classify/Category/Kids/Dresses → crawl theo danh mục Trendsi",
    ("trendsi", "shop"): "Trang chủ Trendsi → crawl catalog qua API",
    ("trendsi", "product"): "Link /products/detail?id=... → lấy 1 SP + biến thể Size/Color",
    ("redbubble", "category"): "Link /shop/... → crawl kết quả tìm kiếm / category",
    ("redbubble", "product"): "Link /i/... → crawl 1 sản phẩm",
    ("redbubble", "unknown"): "Trang chủ — dán link /shop/từ-khóa hoặc /i/.../sản-phẩm",
    ("generic-web", "shop"): "Trang chủ → crawl qua sitemap, API /shop hoặc danh mục HTML",
    ("generic-web", "category"): "Link danh mục → đếm SP trên link đó và crawl (.html, /shop/..., /category/...)",
    ("generic-web", "search"): "Link tìm kiếm (vd. /catalogsearch/result/?q=...)",
    ("generic-web", "product"): "Link sản phẩm (/product/... hoặc .html)",
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
    elif target.crawl_type == "search":
        detail = target.query or ""
    elif target.crawl_type == "product":
        detail = target.handle or ""
    elif target.crawl_type == "brand":
        detail = target.query or target.handle or ""

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
