from __future__ import annotations

from utils.detector import DetectedTarget, UnsupportedUrlError, resolve_url

PLATFORM_LABELS = {
    "shopify": "Shopify",
    "shopbase": "ShopBase",
    "woocommerce": "WooCommerce",
    "teemill": "Teemill",
    "trendsi": "Trendsi",
    "magento": "Magento / Jomashop",
    "redbubble": "Redbubble",
    "fineartamerica": "Fine Art America",
    "generic-web": "Web thuần",
}

CRAWL_TYPE_LABELS = {
    "shop": "Crawl toàn bộ shop",
    "category": "Crawl category / collection",
    "brand": "Crawl brand Trendsi",
    "search": "Crawl kết quả tìm kiếm",
    "product": "Crawl 1 sản phẩm",
    "sitemap": "Crawl theo sitemap",
    "unknown": "Chưa xác định loại link",
}

CRAWL_HINTS = {
    ("shopify", "shop"): (
        "Link trang chủ → /products.json (tối đa ~25k SP). "
        "Shop lớn / 429: dùng chế độ **Crawl theo sitemap** hoặc dán /sitemap.xml. "
        "**Shopify Hydrogen (Remix):** crawl catalog athlete (vd. influxermerch.com)."
    ),
    ("shopify", "sitemap"): (
        "Dán `/sitemap.xml` → lấy toàn bộ SP. "
        "Dán `sitemap_products_1.xml?from=&to=` → chỉ file đó, giữ nguyên dấu `?`."
    ),
    ("shopify", "category"): "Link collection → lấy SP trong collection đó",
    ("shopify", "search"): "Link /search?q=từ-khóa → lấy SP khớp tìm kiếm (HTML storefront)",
    ("shopify", "product"): "Link sản phẩm → lấy 1 SP",
    ("fineartamerica", "shop"): (
        "Trang chủ → sitemap popular (~10 triệu URL). "
        "UI mặc định ~3.000 SP. Full shop: chạy `python crawl_faa_full.py`."
    ),
    ("fineartamerica", "sitemap"): "Link sitemap → lấy /featured/*.html trong sitemap",
    ("fineartamerica", "category"): (
        "Link artist/collection/art/shop → quét listing rồi crawl SP (đa luồng)"
    ),
    ("fineartamerica", "product"): "Link /featured/...html → lấy 1 artwork",
    ("shopbase", "shop"): "Link trang chủ ShopBase (vd. nebsport.com) → crawl API catalog (~10k SP)",
    ("shopbase", "category"): "Link /collections/... → crawl collection (Football, Baseball…)",
    ("shopbase", "search"): "Link /search?q=từ-khóa → crawl SP khớp tìm kiếm qua catalog API",
    ("shopbase", "product"): "Link /products/... → lấy 1 SP + mô tả HTML",
    ("woocommerce", "shop"): (
        "Trang chủ hoặc /shop → Store API (tự giới hạn ~24 luồng). "
        "Shop lớn (>10k SP): để **Luồng crawl** mặc định hoặc 16–24; tắt Proxy nếu lỗi."
    ),
    ("woocommerce", "category"): "Link danh mục (product-category hoặc ?filter_category=) → chỉ lấy SP trong danh mục đó",
    ("woocommerce", "search"): "Link ?s=từ-khóa → lấy SP khớp tìm kiếm",
    ("woocommerce", "product"): "Link sản phẩm → lấy 1 SP",
    ("teemill", "shop"): "Trang chủ Teemill → crawl toàn bộ shop qua Omnis API",
    ("teemill", "category"): "Link /collection/... → lấy SP trong collection đó",
    ("teemill", "product"): "Link /product/... → lấy 1 SP",
    ("trendsi", "brand"): "Link /brand?nameEn=...&id=... → crawl white-label brand (vd. CozyNest)",
    ("trendsi", "category"): (
        "Link /classify/... (Women, Category, Product, …) "
        "→ crawl đúng danh mục Trendsi (vd. Denim Shorts ~346 SP)"
    ),
    ("trendsi", "shop"): "Trang chủ Trendsi → crawl catalog qua API",
    ("trendsi", "product"): "Link /products/detail?id=... → lấy 1 SP + biến thể Size/Color",
    ("magento", "shop"): "Trang chủ Magento/Jomashop → crawl các danh mục gốc qua GraphQL",
    ("magento", "category"): "Link /jewelry.html → crawl category qua GraphQL (vd. ~9k SP Jewelry)",
    ("magento", "product"): "Link /sku-slug.html → lấy 1 SP Magento",
    ("redbubble", "category"): "Link /shop/... → crawl kết quả tìm kiếm / category",
    ("redbubble", "product"): "Link /i/... → crawl 1 sản phẩm",
    ("redbubble", "unknown"): "Trang chủ — dán link /shop/từ-khóa hoặc /i/.../sản-phẩm",
    ("generic-web", "shop"): "Trang chủ → crawl qua sitemap, API /shop hoặc danh mục HTML",
    ("generic-web", "category"): "Link danh mục → đếm SP trên link đó và crawl (.html, /shop/..., /category/...)",
    ("generic-web", "search"): "Link tìm kiếm (vd. /catalogsearch/result/?q=...)",
    ("generic-web", "product"): "Link sản phẩm (/product/... hoặc .html)",
}


def describe_url(url: str, proxy_url: str | None = None) -> dict:
    if not url.strip():
        return {"ok": False, "message": "Nhập link để nhận diện nền tảng.", "crawl_ready": False}

    cleaned_proxy = (proxy_url or "").strip() or None
    if cleaned_proxy is None:
        try:
            from utils.credentials import CrawlCredentials

            cleaned_proxy = CrawlCredentials.load().proxy_url or None
        except Exception:
            cleaned_proxy = None

    target: DetectedTarget | None = None
    last_exc: UnsupportedUrlError | None = None
    # Direct trước. List proxy (hàng chục IP) không được chặn bước nhận diện.
    try:
        target = resolve_url(url.strip(), session=None)
    except UnsupportedUrlError as exc:
        last_exc = exc
        if cleaned_proxy:
            try:
                from utils.http_session import create_http_session

                target = resolve_url(url.strip(), session=create_http_session(cleaned_proxy))
                last_exc = None
            except UnsupportedUrlError as exc2:
                last_exc = exc2

    if target is None and last_exc is not None:
        # Cloudflare Woo — curl_cffi direct khi requests + proxy đều fail.
        try:
            from urllib.parse import urlparse

            from utils.curl_http import curl_get_json_list
            from utils.detector import normalize_url

            normalized = normalize_url(url.strip())
            parsed = urlparse(normalized)
            base = f"{parsed.scheme}://{parsed.netloc}"
            data, status, _headers = curl_get_json_list(
                f"{base}/wp-json/wc/store/v1/products",
                params={"per_page": 1},
                timeout=20,
            )
            if status == 200 and isinstance(data, list):
                target = DetectedTarget(
                    platform="woocommerce",
                    crawl_type="shop",
                    url=normalized,
                    base_url=base,
                )
                last_exc = None
        except Exception:
            pass

    if target is None and last_exc is not None:
        if getattr(last_exc, "platform", None) == "redbubble":
            target = DetectedTarget(
                platform="redbubble",
                crawl_type="unknown",
                url=url.strip(),
                base_url="https://www.redbubble.com",
                message=str(last_exc),
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
                "notice": str(last_exc),
            }
        return {"ok": False, "message": str(last_exc), "crawl_ready": False}

    assert target is not None

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
    elif target.crawl_type == "sitemap":
        from utils.sitemap_mode import product_sitemap_filename

        sitemap_file = product_sitemap_filename(target.url or url)
        if sitemap_file:
            detail = sitemap_file
            hint = (
                f"Chỉ file `{sitemap_file}` — giữ nguyên `?from=&to=`. "
                "Không lấy các file sitemap khác của shop."
            )
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
