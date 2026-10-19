from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote, unquote, urlparse

import requests

from utils.credentials import CrawlCredentials
from utils.detector import DetectedTarget, UnsupportedUrlError, resolve_url

_GENERIC_HOST_LABELS = frozenset({"www", "shop", "store", "m", "mobile", "app"})
_COMMON_TLDS = frozenset({"com", "net", "org", "io", "co", "uk", "shop", "store", "vn"})


def shop_brand_from_url(url: str) -> str:
    """Suy ra tên brand từ domain shop — vd. dawelli.com → Dawelli."""
    text = (url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    host = urlparse(text).netloc.lower().removeprefix("www.")
    if not host or host in {"localhost", "127.0.0.1"}:
        return ""

    labels = [part for part in host.split(".") if part]
    if not labels:
        return ""

    if len(labels) >= 2 and labels[-1] in _COMMON_TLDS:
        candidate = labels[-2]
        if candidate in _GENERIC_HOST_LABELS and len(labels) >= 3:
            candidate = labels[-3]
    else:
        candidate = labels[0]
        if candidate in _GENERIC_HOST_LABELS and len(labels) > 1:
            candidate = labels[1]

    words = re.split(r"[-_]+", candidate)
    return " ".join(word.capitalize() for word in words if word)


@dataclass
class ShopCategoryInfo:
    slug: str
    name: str
    count: int | None = None


@dataclass
class ShopProbeResult:
    ok: bool
    platform: str
    crawl_type: str
    base_url: str
    message: str
    total_products: int | None
    categories: list[ShopCategoryInfo]
    url_category_slug: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["categories"] = [asdict(cat) for cat in self.categories]
        return data


def build_category_crawl_url(base_url: str, platform: str, category_slug: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base.startswith("http"):
        base = f"https://{base}"
    if category_slug in {"", "Tất cả"}:
        return f"{base}/"
    if platform == "woocommerce":
        slug = category_slug.strip().strip("/")
        # GullPrint dùng /home-living/mug/ — ưu tiên path trực tiếp.
        if "/" in slug or slug in {
            "apparel",
            "home-living",
            "holidays",
            "trending",
        }:
            return f"{base}/{slug}/"
        return f"{base}/product-category/{slug}/"
    if platform == "shopify":
        return f"{base}/collections/{category_slug}"
    if platform == "shopbase":
        return f"{base}/collections/{category_slug}"
    if platform == "campaign":
        slug = category_slug.strip().strip("/")
        if slug in {"", "Tất cả"}:
            return f"{base}/"
        return f"{base}/category/{slug}"
    if platform == "teemill":
        slug = category_slug.strip().strip("/")
        if slug in {"", "all-products", "all"}:
            return f"{base}/"
        return f"{base}/collection/{slug}/"
    if platform == "trendsi":
        # Không được trả về homepage — sẽ rơi vào crawl catalog mặc định (quần áo).
        slug = category_slug.strip().strip("/")
        if not slug:
            return f"{base}/"
        lower = slug.lower()
        if lower.startswith("classify/"):
            path = "/".join(
                quote(unquote(part), safe="") for part in slug.split("/") if part
            )
            return f"{base}/{path}"
        parts = [p for p in slug.split("/") if p]
        encoded = "/".join(quote(unquote(part), safe="") for part in parts)
        # /classify/Product/Swimwear  vs  /classify/Category/Kids/Dresses
        kind = "Category" if len(parts) > 1 else "Product"
        return f"{base}/classify/{kind}/{encoded}"
    if platform == "magento":
        slug = category_slug.strip().strip("/")
        if not slug.endswith(".html"):
            slug = f"{slug}.html"
        return f"{base}/{slug}"
    if platform == "generic-web":
        from crawlers.generic_html import GenericHtmlCatalog

        path = GenericHtmlCatalog.path_from_category_slug(category_slug)
        return f"{base}/{path}" if path else base_url
    return base_url


def _probe_generic_web(
    target: DetectedTarget,
    base_url: str,
    url_category: str | None,
    *,
    max_workers: int = 12,
) -> ShopProbeResult:
    from crawlers.generic_web import GenericWebCrawler

    crawler = GenericWebCrawler(base_url)
    uses_html = crawler._uses_html_catalog()

    if target.crawl_type == "product":
        return ShopProbeResult(
            ok=True,
            platform="generic-web",
            crawl_type="product",
            base_url=base_url,
            message="**1** SP · link sản phẩm (/product/... hoặc .html)",
            total_products=1,
            categories=[],
            url_category_slug=url_category or target.handle,
        )

    if target.crawl_type == "search" and target.query and uses_html:
        html = crawler._html_catalog()
        total = html.count_search_results(target.query)
        total_text = f"**{total}** SP" if total is not None else "**?** SP"
        return ShopProbeResult(
            ok=True,
            platform="generic-web",
            crawl_type="search",
            base_url=base_url,
            message=f"{total_text} · tìm kiếm `{target.query}`",
            total_products=total,
            categories=[],
            url_category_slug=target.query,
        )

    if target.crawl_type == "category" and target.handle and uses_html:
        html = crawler._html_catalog()
        path = f"{target.handle}.html"
        total, raw_subs = html.probe_listing_page(
            path,
            include_subcategories=True,
            max_subcategories=24,
            max_workers=max_workers,
        )
        slug = target.category_slug or url_category or html.category_slug(target.handle)
        name = target.handle.split("/")[-1].replace("-", " ").title()
        categories = [
            ShopCategoryInfo(slug=slug, name=f"{name} (link này)", count=total),
        ]
        for item in raw_subs:
            categories.append(
                ShopCategoryInfo(
                    slug=str(item["slug"]),
                    name=str(item["name"]),
                    count=int(item["count"]) if item.get("count") is not None else None,
                )
            )
        message = f"**{total}** SP trên link này"
        if raw_subs:
            message = f"{message} · **{len(raw_subs)}** link con trong menu"
        return ShopProbeResult(
            ok=True,
            platform="generic-web",
            crawl_type="category",
            base_url=base_url,
            message=message,
            total_products=total,
            categories=categories,
            url_category_slug=url_category or slug,
        )

    uses_shop_catalog = crawler._uses_shop_catalog()
    total = crawler.count_sitemap_products()
    if total <= 0 and not uses_shop_catalog and not uses_html:
        return ShopProbeResult(
            ok=True,
            platform="generic-web",
            crawl_type=target.crawl_type,
            base_url=base_url,
            message=(
                "Không đếm được SP — thử link danh mục **.html**, **/product/...** "
                "hoặc site có **sitemap-products**."
            ),
            total_products=None,
            categories=[],
            url_category_slug=url_category,
        )

    raw_categories, category_note = crawler.probe_categories(
        sample_size=24,
        max_workers=max_workers,
    )
    categories = [
        ShopCategoryInfo(
            slug=str(item["slug"]),
            name=str(item["name"]),
            count=int(item["count"]) if item.get("count") is not None else None,
        )
        for item in raw_categories
    ]

    message = _stats_message(total, len(categories))
    if category_note:
        message = f"{message} · {category_note}"

    return ShopProbeResult(
        ok=True,
        platform="generic-web",
        crawl_type=target.crawl_type,
        base_url=base_url,
        message=message,
        total_products=total,
        categories=categories,
        url_category_slug=url_category,
    )


def probe_shop_stats(
    url: str,
    credentials: CrawlCredentials | None = None,
    *,
    max_workers: int = 12,
) -> ShopProbeResult:
    credentials = credentials or CrawlCredentials.load()
    cleaned = url.strip()
    if not cleaned:
        return ShopProbeResult(
            ok=False,
            platform="",
            crawl_type="",
            base_url="",
            message="Chưa có link shop.",
            total_products=None,
            categories=[],
        )

    try:
        target = resolve_url(cleaned)
    except UnsupportedUrlError as exc:
        if getattr(exc, "platform", None) == "redbubble":
            return ShopProbeResult(
                ok=True,
                platform="redbubble",
                crawl_type="unknown",
                base_url="https://www.redbubble.com",
                message=str(exc),
                total_products=None,
                categories=[],
            )
        return ShopProbeResult(
            ok=False,
            platform=getattr(exc, "platform", "") or "",
            crawl_type="",
            base_url="",
            message=str(exc),
            total_products=None,
            categories=[],
        )

    base_url = target.base_url or cleaned
    url_category = target.category_slug or (
        target.handle if target.crawl_type == "category" else None
    )

    if target.platform == "woocommerce":
        return _probe_woocommerce(target, credentials, base_url, url_category)
    if target.platform == "shopify":
        return _probe_shopify(target, credentials, base_url, url_category)
    if target.platform == "shopbase":
        return _probe_shopbase(target, base_url, url_category)
    if target.platform == "campaign":
        return _probe_campaign(target, base_url, url_category, max_workers=max_workers)
    if target.platform == "teemill":
        return _probe_teemill(target, base_url, url_category)
    if target.platform == "trendsi":
        return _probe_trendsi(target, base_url, url_category)
    if target.platform == "magento":
        return _probe_magento(target, base_url, url_category)
    if target.platform == "redbubble":
        return _probe_redbubble(target, credentials, base_url, url_category)
    if target.platform == "fineartamerica":
        return _probe_fineartamerica(target, base_url, url_category)

    if target.platform == "generic-web":
        return _probe_generic_web(target, base_url, url_category, max_workers=max_workers)

    return ShopProbeResult(
        ok=False,
        platform=target.platform,
        crawl_type=target.crawl_type,
        base_url=base_url,
        message=f"Chưa hỗ trợ thống kê shop cho {target.platform}.",
        total_products=None,
        categories=[],
        url_category_slug=url_category,
    )


def _probe_trendsi(
    target: DetectedTarget,
    base_url: str,
    url_category: str | None,
) -> ShopProbeResult:
    from crawlers.trendsi import TrendsiCrawler

    if target.crawl_type == "product":
        return ShopProbeResult(
            ok=True,
            platform="trendsi",
            crawl_type="product",
            base_url=base_url,
            message="**1** SP · link /products/detail?id=...",
            total_products=1,
            categories=[],
            url_category_slug=url_category or target.handle,
        )

    probe_url = target.url if target.crawl_type in {"brand", "category"} else base_url
    crawler = TrendsiCrawler()
    try:
        response = crawler.session.get(probe_url, timeout=30)
        response.raise_for_status()
        html = response.text
    except Exception as exc:
        return ShopProbeResult(
            ok=True,
            platform="trendsi",
            crawl_type=target.crawl_type,
            base_url=base_url,
            message=f"Trendsi — không đếm được SP ({exc}). Vẫn có thể bấm crawl.",
            total_products=None,
            categories=[],
            url_category_slug=url_category,
        )

    total = TrendsiCrawler.parse_listing_item_count(html)
    stubs = crawler._parse_listing_page(html)
    if total is None and stubs:
        total = len(stubs)

    label = target.query or target.category_slug or target.handle or "Trendsi"
    if target.crawl_type == "brand":
        msg = f"Brand **{label}**"
    elif target.crawl_type == "category":
        msg = f"Category **{label}**"
    else:
        msg = "Trendsi catalog"

    if total is not None:
        msg += f" · ~**{total:,}** SP".replace(",", ".")
    elif stubs:
        msg += f" · ~**{len(stubs)}** SP/trang"
    else:
        msg += " · bấm **Crawl dữ liệu** để lấy SP"

    return ShopProbeResult(
        ok=True,
        platform="trendsi",
        crawl_type=target.crawl_type,
        base_url=base_url,
        message=msg,
        total_products=total,
        categories=[],
        url_category_slug=url_category,
    )


def _probe_magento(
    target: DetectedTarget,
    base_url: str,
    url_category: str | None,
) -> ShopProbeResult:
    from crawlers.magento_graphql import MagentoGraphqlCrawler

    if target.crawl_type == "product":
        return ShopProbeResult(
            ok=True,
            platform="magento",
            crawl_type="product",
            base_url=base_url,
            message="**1** SP · link Magento `.html`",
            total_products=1,
            categories=[],
            url_category_slug=url_category or target.handle,
        )

    crawler = MagentoGraphqlCrawler(base_url)
    label = target.query or target.category_slug or target.handle or "Magento"
    total: int | None = None
    categories: list[ShopCategoryInfo] = []

    try:
        if target.crawl_type == "category":
            total = crawler.count_category_products(
                target.category_slug or target.handle or target.url
            )
        else:
            for child in crawler.list_root_categories():
                slug = str(child.get("url_path") or "").strip()
                name = str(child.get("name") or slug).strip()
                if not slug:
                    continue
                categories.append(ShopCategoryInfo(slug=slug, name=name, count=None))
    except Exception as exc:
        return ShopProbeResult(
            ok=True,
            platform="magento",
            crawl_type=target.crawl_type,
            base_url=base_url,
            message=f"Magento — không đếm được SP ({exc}). Vẫn có thể bấm crawl.",
            total_products=None,
            categories=[],
            url_category_slug=url_category,
        )

    if target.crawl_type == "category":
        msg = f"Category **{label}**"
    else:
        msg = "Magento / Jomashop"

    if total is not None:
        msg += f" · ~**{total:,}** SP".replace(",", ".")
    elif categories:
        msg += f" · **{len(categories)}** danh mục gốc"
    else:
        msg += " · bấm **Crawl dữ liệu** để lấy SP"

    return ShopProbeResult(
        ok=True,
        platform="magento",
        crawl_type=target.crawl_type,
        base_url=base_url,
        message=msg,
        total_products=total,
        categories=categories,
        url_category_slug=url_category,
    )


def _probe_fineartamerica(
    target: DetectedTarget,
    base_url: str,
    url_category: str | None,
) -> ShopProbeResult:
    from crawlers.fineartamerica import FineArtAmericaCrawler

    if target.crawl_type == "product":
        return ShopProbeResult(
            ok=True,
            platform="fineartamerica",
            crawl_type="product",
            base_url=base_url,
            message="**1** artwork · /featured/...",
            total_products=1,
            categories=[],
            url_category_slug=url_category,
        )

    crawler = FineArtAmericaCrawler(site_url=base_url)
    total = None
    note = ""
    if target.crawl_type == "shop":
        total = crawler.count_popular_products()
        note = "sitemap popular (không phải toàn marketplace)"
    elif target.crawl_type == "sitemap":
        note = "crawl theo sitemap đã dán"
    else:
        note = "listing artist/collection/art/shop"

    if total is not None:
        message = (
            f"**~{total:,}** URL/file sitemap · {note} "
            f"(trang chủ mặc định lấy tối đa **3.000** SP)"
        ).replace(",", ".")
    else:
        message = (
            f"Fine Art America · {note}. "
            "Nếu không đếm được: hay bị **429** — bật **Proxy**, luồng **4–8**. "
            "Trang chủ mặc định ~**3.000** SP."
        )

    return ShopProbeResult(
        ok=True,
        platform="fineartamerica",
        crawl_type=target.crawl_type,
        base_url=base_url,
        message=message,
        total_products=total,
        categories=[],
        url_category_slug=url_category,
    )


def _probe_redbubble(
    target: DetectedTarget,
    credentials: CrawlCredentials,
    base_url: str,
    url_category: str | None,
) -> ShopProbeResult:
    from crawlers.redbubble import RedbubbleCrawler

    if target.crawl_type == "product":
        return ShopProbeResult(
            ok=True,
            platform="redbubble",
            crawl_type="product",
            base_url=base_url,
            message="**1** SP · link sản phẩm /i/...",
            total_products=1,
            categories=[],
            url_category_slug=url_category or target.handle,
        )

    if target.crawl_type != "category":
        return ShopProbeResult(
            ok=True,
            platform="redbubble",
            crawl_type=target.crawl_type,
            base_url=base_url,
            message="Redbubble — dán link **/shop/từ-khóa** hoặc **/i/...** để crawl.",
            total_products=None,
            categories=[],
            url_category_slug=url_category,
        )

    crawler = RedbubbleCrawler(proxy_url=credentials.proxy_url or None)
    try:
        stats = crawler.probe_category(target.url)
    except Exception as exc:
        return ShopProbeResult(
            ok=True,
            platform="redbubble",
            crawl_type="category",
            base_url=base_url,
            message=(
                f"Redbubble — không đếm được SP ({exc}). "
                "Thử thêm **Proxy** (Redbubble hay chặn IP)."
            ),
            total_products=None,
            categories=[],
            url_category_slug=url_category,
        )

    total = stats.get("total")
    slug = target.category_slug or url_category or "shop"
    name = str(stats.get("category_name") or slug).strip()
    categories = [
        ShopCategoryInfo(
            slug=slug,
            name=name,
            count=int(total) if total is not None else None,
        )
    ]
    message = _stats_message(total, len(categories))
    per_page = stats.get("per_page")
    if per_page and total:
        message = f"{message} · ~{per_page} SP/trang"
    return ShopProbeResult(
        ok=True,
        platform="redbubble",
        crawl_type="category",
        base_url=base_url,
        message=message,
        total_products=total,
        categories=categories,
        url_category_slug=url_category or slug,
    )


def _probe_woocommerce(
    target: DetectedTarget,
    credentials: CrawlCredentials,
    base_url: str,
    url_category: str | None,
) -> ShopProbeResult:
    from crawlers.woocommerce import WooCommerceCrawler

    host = urlparse(base_url).netloc
    key, secret = credentials.woocommerce_for(host)
    crawler = WooCommerceCrawler(
        site_url=base_url,
        consumer_key=key or "",
        consumer_secret=secret or "",
        use_public_api=not (key and secret),
        proxy_url=credentials.proxy_url or None,
    )
    _use_direct_session(crawler)

    if crawler.use_public_api and not (key and secret):
        crawler._store_api_probe_ok()

    if target.crawl_type == "search" and target.query:
        total = _woo_search_count(crawler, target.query)
        total_text = f"**{total}** SP" if total is not None else "**?** SP"
        return ShopProbeResult(
            ok=True,
            platform="woocommerce",
            crawl_type="search",
            base_url=crawler.site_url,
            message=f"{total_text} · tìm kiếm `{target.query}`",
            total_products=total,
            categories=[],
            url_category_slug=target.query,
        )

    categories = _woo_list_categories(crawler)

    # Link danh mục không được lấy tổng cả shop khi không khớp slug.
    if target.crawl_type == "category" and url_category:
        total, cat_name = _woo_category_total(crawler, categories, url_category)
        count_text = f"**{total}**" if total is not None else "**?**"
        return ShopProbeResult(
            ok=True,
            platform="woocommerce",
            crawl_type="category",
            base_url=crawler.site_url,
            message=f"{count_text} SP · danh mục **{cat_name}**",
            total_products=total,
            categories=categories,
            url_category_slug=url_category,
        )

    total = _woo_product_count(crawler)

    if total is None and categories:
        total = sum(c.count or 0 for c in categories) or None

    message = _stats_message(total, len(categories))

    return ShopProbeResult(
        ok=True,
        platform="woocommerce",
        crawl_type=target.crawl_type,
        base_url=crawler.site_url,
        message=message,
        total_products=total,
        categories=categories,
        url_category_slug=url_category,
    )


def _probe_campaign(
    target: DetectedTarget,
    base_url: str,
    url_category: str | None,
    *,
    max_workers: int = 8,
) -> ShopProbeResult:
    from crawlers.campaign_shop import CampaignShopCrawler

    crawler = CampaignShopCrawler(base_url)
    if target.crawl_type == "product":
        return ShopProbeResult(
            ok=True,
            platform="campaign",
            crawl_type="product",
            base_url=base_url,
            message="**1** SP · link /campaign/...",
            total_products=1,
            categories=[],
            url_category_slug=url_category or target.handle,
        )

    try:
        raw_categories = crawler.list_categories(max_workers=max_workers)
    except Exception as exc:
        return ShopProbeResult(
            ok=True,
            platform="campaign",
            crawl_type=target.crawl_type,
            base_url=base_url,
            message=f"Campaign shop — không đọc được danh mục ({exc}).",
            total_products=None,
            categories=[],
            url_category_slug=url_category,
        )

    categories = [
        ShopCategoryInfo(
            slug=str(item.get("slug") or ""),
            name=str(item.get("name") or item.get("slug") or ""),
            count=item.get("count"),
        )
        for item in raw_categories
        if item.get("slug")
    ]
    slug = (url_category or target.category_slug or target.handle or "").strip().strip("/")
    total: int | None = None
    if target.crawl_type == "category" and slug:
        match = next((cat for cat in categories if cat.slug == slug), None)
        if match is None:
            try:
                info = crawler.category_info(slug)
            except Exception:
                info = None
            if info and info.get("slug"):
                match = ShopCategoryInfo(
                    slug=str(info["slug"]),
                    name=str(info.get("name") or info["slug"]),
                    count=info.get("count"),
                )
                categories.insert(0, match)
        if match is not None and match.count is not None:
            total = int(match.count)
        if match is not None and total is not None:
            message = f"**{total}** SP · {match.name}"
        elif match is not None:
            message = f"Danh mục {match.name}"
        else:
            message = _stats_message(total, len(categories))
    else:
        message = f"**{len(categories)}** danh mục · Campaign shop"

    return ShopProbeResult(
        ok=True,
        platform="campaign",
        crawl_type=target.crawl_type,
        base_url=base_url,
        message=message,
        total_products=total,
        categories=categories,
        url_category_slug=slug or url_category,
    )


def _probe_teemill(
    target: DetectedTarget,
    base_url: str,
    url_category: str | None,
) -> ShopProbeResult:
    from crawlers.teemill import TeemillCrawler

    crawler = TeemillCrawler(base_url)

    if target.crawl_type == "product":
        return ShopProbeResult(
            ok=True,
            platform="teemill",
            crawl_type="product",
            base_url=base_url,
            message="**1** SP · link /product/...",
            total_products=1,
            categories=[],
            url_category_slug=url_category or target.handle,
        )

    collection_slug = url_category or target.category_slug or target.handle
    try:
        collections_raw = crawler.list_collections()
    except Exception as exc:
        return ShopProbeResult(
            ok=True,
            platform="teemill",
            crawl_type=target.crawl_type,
            base_url=base_url,
            message=f"Teemill — không đọc được collection ({exc}). Vẫn có thể bấm crawl.",
            total_products=None,
            categories=[],
            url_category_slug=collection_slug,
        )

    categories = [
        ShopCategoryInfo(
            slug=str(item.get("slug") or ""),
            name=str(item.get("name") or item.get("slug") or ""),
            count=item.get("count"),
        )
        for item in collections_raw
        if item.get("slug")
    ]

    total: int | None = None
    if target.crawl_type == "category" and collection_slug:
        try:
            total = crawler.count_products(collection_slug)
        except Exception:
            total = None
    elif target.crawl_type == "shop":
        try:
            total = crawler.count_products()
        except Exception:
            total = None

    return ShopProbeResult(
        ok=True,
        platform="teemill",
        crawl_type=target.crawl_type,
        base_url=base_url,
        message=_stats_message(total, len(categories)),
        total_products=total,
        categories=categories,
        url_category_slug=collection_slug,
    )


def _probe_shopbase(
    target: DetectedTarget,
    base_url: str,
    url_category: str | None,
) -> ShopProbeResult:
    from concurrent.futures import ThreadPoolExecutor

    from crawlers.shopbase import ShopBaseCrawler

    crawler = ShopBaseCrawler(base_url)

    if target.crawl_type == "product":
        return ShopProbeResult(
            ok=True,
            platform="shopbase",
            crawl_type="product",
            base_url=base_url,
            message="**1** SP · link /products/...",
            total_products=1,
            categories=[],
            url_category_slug=url_category or target.handle,
        )

    from utils.sitemap_mode import product_sitemap_filename

    sitemap_file = product_sitemap_filename(target.url or "")
    if target.crawl_type == "sitemap" and sitemap_file:
        total = _shopify_one_sitemap_product_count(crawler, target.url)
        count_text = f"**{total}**" if total is not None else "**?**"
        return ShopProbeResult(
            ok=True,
            platform="shopbase",
            crawl_type="sitemap",
            base_url=base_url,
            message=f"{count_text} SP · file `{sitemap_file}`",
            total_products=total,
            categories=[],
            url_category_slug=sitemap_file,
        )

    if target.crawl_type == "search" and target.query:
        total = crawler.count_search_results(target.query)
        return ShopProbeResult(
            ok=True,
            platform="shopbase",
            crawl_type="search",
            base_url=base_url,
            message=f"**{total}** SP · tìm kiếm `{target.query}`",
            total_products=total,
            categories=[],
            url_category_slug=target.query,
        )

    if target.crawl_type == "category" and target.handle:
        with ThreadPoolExecutor(max_workers=2) as executor:
            total_future = executor.submit(
                crawler.count_sitemap_products,
                collection_handle=target.handle,
            )
            categories_future = executor.submit(crawler.probe_categories)
            total = total_future.result()
            categories_raw, _ = categories_future.result()
        categories = [
            ShopCategoryInfo(
                slug=str(item.get("slug") or ""),
                name=str(item.get("name") or item.get("slug") or ""),
                count=item.get("count"),
            )
            for item in categories_raw
            if item.get("slug")
        ]
        message = _stats_message(total, 1) if total else f"Collection **{target.handle}**"
        return ShopProbeResult(
            ok=True,
            platform="shopbase",
            crawl_type="category",
            base_url=base_url,
            message=message,
            total_products=total,
            categories=categories,
            url_category_slug=url_category or target.handle,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        total_future = executor.submit(crawler.count_sitemap_products)
        categories_future = executor.submit(crawler.probe_categories)
        total = total_future.result()
        categories_raw, _ = categories_future.result()

    categories = [
        ShopCategoryInfo(
            slug=str(item.get("slug") or ""),
            name=str(item.get("name") or item.get("slug") or ""),
            count=item.get("count"),
        )
        for item in categories_raw
        if item.get("slug")
    ]

    return ShopProbeResult(
        ok=True,
        platform="shopbase",
        crawl_type=target.crawl_type,
        base_url=base_url,
        message=_stats_message(total, len(categories)) + " · ShopBase",
        total_products=total or None,
        categories=categories,
        url_category_slug=url_category,
    )


def _probe_shopify(
    target: DetectedTarget,
    credentials: CrawlCredentials,
    base_url: str,
    url_category: str | None,
) -> ShopProbeResult:
    from crawlers.shopify import ShopifyCrawler

    host = urlparse(base_url).netloc
    token = credentials.shopify_token_for(host)

    crawler = ShopifyCrawler(
        shop_url=base_url,
        access_token=token,
        use_public_api=not token,
        proxy_url=credentials.proxy_url or None,
    )
    _use_direct_session(crawler)

    if target.crawl_type == "search" and target.query:
        total = crawler.count_search_results(target.query)
        total_text = f"**{total}** SP" if total is not None else "**?** SP"
        return ShopProbeResult(
            ok=True,
            platform="shopify",
            crawl_type="search",
            base_url=crawler.shop_url,
            message=f"{total_text} · tìm kiếm `{target.query}`",
            total_products=total,
            categories=[],
            url_category_slug=target.query,
        )

    if target.crawl_type == "product":
        return ShopProbeResult(
            ok=True,
            platform="shopify",
            crawl_type="product",
            base_url=crawler.shop_url,
            message="**1** SP · link sản phẩm",
            total_products=1,
            categories=[],
            url_category_slug=url_category or target.handle,
        )

    # sitemap_products_1.xml?from=&to= là 1 file, không phải index cả shop.
    from utils.sitemap_mode import product_sitemap_filename

    sitemap_file = product_sitemap_filename(target.url or "")
    if target.crawl_type == "sitemap" and sitemap_file:
        total = _shopify_one_sitemap_product_count(crawler, target.url)
        count_text = f"**{total}**" if total is not None else "**?**"
        return ShopProbeResult(
            ok=True,
            platform="shopify",
            crawl_type="sitemap",
            base_url=crawler.shop_url,
            message=f"{count_text} SP · file `{sitemap_file}`",
            total_products=total,
            categories=[],
            url_category_slug=sitemap_file,
        )

    # Đếm collection trước khi liệt kê cả shop — list collections dễ 429
    # và làm mất số SP của đúng collection user dán.
    from concurrent.futures import ThreadPoolExecutor

    if target.crawl_type == "category" and url_category and url_category != "all":
        with ThreadPoolExecutor(max_workers=2) as executor:
            count_future = executor.submit(
                _shopify_collection_product_count, crawler, url_category
            )
            list_future = executor.submit(_shopify_list_collections, crawler)
            total, title = count_future.result()
            categories = list_future.result()
        if total is None and not categories and _use_one_proxy(crawler):
            with ThreadPoolExecutor(max_workers=2) as executor:
                count_future = executor.submit(
                    _shopify_collection_product_count, crawler, url_category
                )
                list_future = executor.submit(_shopify_list_collections, crawler)
                total, title = count_future.result()
                categories = list_future.result()
        _upsert_shopify_collection(categories, url_category, title, total)
        count_text = f"**{total}**" if total is not None else "**?**"
        return ShopProbeResult(
            ok=True,
            platform="shopify",
            crawl_type="category",
            base_url=crawler.shop_url,
            message=f"{count_text} SP · danh mục **{title}**",
            total_products=total,
            categories=categories,
            url_category_slug=url_category,
        )

    total = _shopify_product_count(crawler)
    categories = _shopify_list_collections(crawler)

    if total is None and not categories and _use_one_proxy(crawler):
        total = _shopify_product_count(crawler)
        categories = _shopify_list_collections(crawler)

    if total is None and categories:
        total = sum(c.count or 0 for c in categories) or None

    return ShopProbeResult(
        ok=True,
        platform="shopify",
        crawl_type=target.crawl_type,
        base_url=crawler.shop_url,
        message=_stats_message(total, len(categories)),
        total_products=total,
        categories=categories,
        url_category_slug=url_category,
    )


def _stats_message(total: int | None, category_count: int) -> str:
    total_text = f"**{total}** SP" if total is not None else "**?** SP"
    if category_count:
        return f"{total_text} · **{category_count}** danh mục"
    return total_text


def _woo_list_categories(crawler) -> list[ShopCategoryInfo]:
    categories: list[ShopCategoryInfo] = []
    seen: set[str] = set()

    def add_item(slug: str, name: str, count: int | None) -> None:
        slug = slug.strip()
        if not slug or slug in seen:
            return
        seen.add(slug)
        display = name.strip() or slug
        if slug == "uncategorized":
            display = "Chưa phân loại"
        categories.append(ShopCategoryInfo(slug=slug, name=display, count=count))

    page = 1
    while page <= 4:
        try:
            if crawler.use_public_api:
                response = crawler.session.get(
                    f"{crawler.site_url}/wp-json/wc/store/v1/products/categories",
                    params={"per_page": 100, "page": page},
                    timeout=8,
                )
            else:
                response = crawler.session.get(
                    f"{crawler.site_url}/wp-json/wc/v3/products/categories",
                    params={"per_page": 100, "page": page},
                    auth=(crawler.consumer_key, crawler.consumer_secret),
                    timeout=8,
                )
            if response.status_code != 200:
                break
            batch = response.json()
            if not isinstance(batch, list) or not batch:
                break

            for item in batch:
                slug = str(item.get("slug") or "").strip()
                name = str(item.get("name") or slug).strip()
                count_raw = item.get("count")
                count = int(count_raw) if count_raw is not None else None
                add_item(slug, name, count)

            if len(batch) < 100:
                break
            page += 1
        except requests.RequestException:
            break

    if not categories:
        try:
            response = crawler.session.get(
                f"{crawler.site_url}/wp-json/wp/v2/product_cat",
                params={"per_page": 100},
                timeout=30,
            )
            if response.status_code == 200:
                for item in response.json():
                    slug = str(item.get("slug") or "").strip()
                    name = str(item.get("name") or slug).strip()
                    count_raw = item.get("count")
                    count = int(count_raw) if count_raw is not None else None
                    add_item(slug, name, count)
        except requests.RequestException:
            pass

    categories.sort(key=lambda item: (-(item.count or 0), item.name.lower()))
    return categories


def _woo_category_total(crawler, categories: list[ShopCategoryInfo], url_category: str) -> tuple[int | None, str]:
    """Số SP của đúng danh mục. Không trả về tổng cả shop."""
    want = url_category.strip().strip("/").split("/")[-1].lower()
    name = want or url_category
    matched = next((c for c in categories if c.slug.lower() == want), None)
    if matched is not None:
        return matched.count, matched.name or name

    category_id = None
    try:
        category_id = crawler._resolve_category_id(url_category)
    except (requests.RequestException, ValueError, TypeError):
        category_id = None
    if category_id is None:
        return None, name

    try:
        if crawler.use_public_api:
            response = crawler.session.get(
                f"{crawler.site_url}/wp-json/wc/store/v1/products",
                params={"category": category_id, "per_page": 1},
                timeout=30,
            )
        else:
            response = crawler.session.get(
                f"{crawler.site_url}/wp-json/wc/v3/products",
                params={"category": category_id, "per_page": 1, "status": "publish"},
                auth=(crawler.consumer_key, crawler.consumer_secret),
                timeout=30,
            )
        if response.status_code == 200:
            raw = response.headers.get("X-WP-Total") or response.headers.get("x-wp-total")
            if raw is not None:
                return int(raw), name
    except (requests.RequestException, ValueError, TypeError):
        pass
    return None, name


def _woo_search_count(crawler, query: str) -> int | None:
    total = _woo_search_count_from_html(crawler, query)
    if total is not None:
        return total

    try:
        params: dict[str, Any] = {"search": query, "per_page": 1}
        if crawler.use_public_api:
            response = crawler.session.get(
                f"{crawler.site_url}/wp-json/wc/store/v1/products",
                params=params,
                timeout=30,
            )
        else:
            response = crawler.session.get(
                f"{crawler.site_url}/wp-json/wc/v3/products",
                params={**params, "status": "publish"},
                auth=(crawler.consumer_key, crawler.consumer_secret),
                timeout=30,
            )
        if response.status_code == 200:
            total_header = response.headers.get("X-WP-Total") or response.headers.get("x-wp-total")
            if total_header is not None:
                return int(total_header)
    except (requests.RequestException, ValueError, TypeError):
        pass
    return None


def _woo_search_count_from_html(crawler, query: str) -> int | None:
    try:
        response = crawler.session.get(
            crawler.site_url.rstrip("/") + "/",
            params={"s": query, "post_type": "product"},
            timeout=30,
        )
        if response.status_code != 200:
            return None
        match = re.search(r"([\d][\d\.,]*)\s+result", response.text, re.I)
        if match:
            return int(match.group(1).replace(".", "").replace(",", ""))
    except (requests.RequestException, ValueError, TypeError):
        pass
    return None


def _woo_product_count(crawler) -> int | None:
    try:
        if not crawler.use_public_api:
            response = crawler.session.get(
                f"{crawler.site_url}/wp-json/wc/v3/products",
                params={"per_page": 1, "status": "publish"},
                auth=(crawler.consumer_key, crawler.consumer_secret),
                timeout=8,
            )
            if response.status_code == 200:
                total = response.headers.get("X-WP-Total")
                if total is not None:
                    return int(total)
        else:
            if hasattr(crawler, "_store_api_probe_ok"):
                crawler._store_api_probe_ok()
            if getattr(crawler, "_use_curl_cffi", False):
                from utils.curl_http import curl_get

                response = curl_get(
                    f"{crawler.site_url}/wp-json/wc/store/v1/products",
                    params={"per_page": 1},
                    timeout=8,
                )
                if response is not None and response.status_code == 200:
                    total = response.headers.get("X-WP-Total") or response.headers.get(
                        "x-wp-total"
                    )
                    if total is not None:
                        return int(total)
            else:
                response = crawler.session.get(
                    f"{crawler.site_url}/wp-json/wc/store/v1/products",
                    params={"per_page": 1},
                    timeout=8,
                )
                if response.status_code == 200:
                    total = response.headers.get("X-WP-Total") or response.headers.get(
                        "x-wp-total"
                    )
                    if total is not None:
                        return int(total)
                if response.status_code == 403 and hasattr(
                    crawler, "_curl_store_probe"
                ):
                    if crawler._curl_store_probe() and crawler._switch_to_curl_cffi():
                        from utils.curl_http import curl_get

                        curl_resp = curl_get(
                            f"{crawler.site_url}/wp-json/wc/store/v1/products",
                            params={"per_page": 1},
                            timeout=30,
                        )
                        if curl_resp is not None and curl_resp.status_code == 200:
                            total = curl_resp.headers.get(
                                "X-WP-Total"
                            ) or curl_resp.headers.get("x-wp-total")
                            if total is not None:
                                return int(total)
    except (requests.RequestException, ValueError, TypeError):
        pass

    # Store API khóa (GullPrint…) — đếm từ /shop/ hoặc sitemap.
    html_total = _woo_count_from_shop_html(crawler)
    if html_total is not None:
        return html_total
    try:
        urls = crawler._discover_product_urls_sitemap(max_sitemaps=None)
        return len(urls) or None
    except Exception:
        return None


def _woo_count_from_shop_html(crawler) -> int | None:
    import re

    try:
        response = crawler.session.get(f"{crawler.site_url}/shop/", timeout=30)
        if response.status_code != 200:
            return None
        match = re.search(
            r"of\s+([\d,]+)\s+results|([\d,]+)\s+results?",
            response.text,
            re.I,
        )
        if not match:
            return None
        raw = match.group(1) or match.group(2)
        return int(str(raw).replace(",", ""))
    except (requests.RequestException, ValueError, TypeError):
        return None


def _use_direct_session(crawler) -> None:
    """Nhận diện đi thẳng. Proxy giữ trong rotator để thử một lần nếu direct fail."""
    from utils.http_session import create_http_session

    user_agent = ""
    session = getattr(crawler, "session", None)
    if session is not None:
        user_agent = session.headers.get("User-Agent") or ""
    crawler.session = create_http_session(None)
    if user_agent:
        crawler.session.headers["User-Agent"] = user_agent


def _use_one_proxy(crawler) -> bool:
    from utils.http_session import create_http_session

    rotator = getattr(crawler, "proxy_rotator", None)
    if rotator is None or not rotator.has_proxy:
        return False
    user_agent = crawler.session.headers.get("User-Agent") or ""
    crawler.session = create_http_session(rotator.current())
    if user_agent:
        crawler.session.headers["User-Agent"] = user_agent
    return True


def _shopify_list_collections(crawler) -> list[ShopCategoryInfo]:
    categories: list[ShopCategoryInfo] = []
    page = 1

    while page <= 2:
        try:
            response = crawler.session.get(
                f"{crawler.shop_url}/collections.json",
                params={"limit": 250, "page": page},
                timeout=(3, 6),
            )
            if response.status_code != 200:
                break
            batch = response.json().get("collections", [])
            if not batch:
                break

            for item in batch:
                handle = str(item.get("handle") or "").strip()
                name = str(item.get("title") or handle).strip()
                if not handle:
                    continue
                count_raw = item.get("products_count")
                count = int(count_raw) if count_raw is not None else None
                categories.append(ShopCategoryInfo(slug=handle, name=name, count=count))

            if len(batch) < 250:
                break
            page += 1
        except (requests.RequestException, ValueError, TypeError):
            break

    categories.sort(key=lambda item: (-(item.count or 0), item.name.lower()))
    return categories


def _upsert_shopify_collection(
    categories: list[ShopCategoryInfo],
    handle: str,
    title: str,
    count: int | None,
) -> None:
    """Đưa collection của link user dán lên đầu, count = SP storefront."""
    for item in categories:
        if item.slug == handle:
            item.name = title or item.name
            item.count = count
            categories.remove(item)
            categories.insert(0, item)
            return
    categories.insert(
        0,
        ShopCategoryInfo(slug=handle, name=title or handle, count=count),
    )


def _shopify_collection_product_count(crawler, handle: str) -> tuple[int | None, str]:
    """Đếm SP storefront của 1 collection. Sitemap cả shop không được dùng."""
    from utils.http_session import create_http_session

    user_agent = crawler.session.headers.get("User-Agent") or ""
    direct = create_http_session(None)
    if user_agent:
        direct.headers["User-Agent"] = user_agent
    sessions = [direct]
    rotator = getattr(crawler, "proxy_rotator", None)
    if rotator is not None and rotator.has_proxy:
        proxied = create_http_session(rotator.current())
        if user_agent:
            proxied.headers["User-Agent"] = user_agent
        sessions.append(proxied)

    title = handle
    admin_count: int | None = None

    for session in sessions:
        gql_count, gql_title = _shopify_collection_graphql_count(
            session, crawler.shop_url, handle
        )
        if gql_title:
            title = gql_title
        if gql_count is not None:
            return gql_count, title

        try:
            meta = session.get(
                f"{crawler.shop_url}/collections/{handle}.json",
                timeout=8,
            )
            if meta.status_code == 200:
                collection = meta.json().get("collection") or {}
                title = str(collection.get("title") or title).strip() or title
                raw_count = collection.get("products_count")
                if raw_count is not None:
                    admin_count = int(raw_count)
        except (requests.RequestException, ValueError, TypeError):
            pass

        html_count = _shopify_collection_html_count(session, crawler.shop_url, handle)
        if html_count is not None:
            return html_count, title
        if admin_count is not None:
            return admin_count, title

    return None, title


def _shopify_collection_graphql_count(
    session,
    shop_url: str,
    handle: str,
) -> tuple[int | None, str | None]:
    """Đếm id trong collection. Payload nhỏ, cùng cửa với /products.json."""
    query = """
    query($handle: String!, $cursor: String) {
      collection(handle: $handle) {
        title
        products(first: 250, after: $cursor) {
          pageInfo { hasNextPage endCursor }
          edges { node { id } }
        }
      }
    }
    """
    cursor = None
    total = 0
    title: str | None = None
    try:
        for _ in range(4):
            response = session.post(
                f"{shop_url.rstrip('/')}/api/2024-10/graphql.json",
                json={
                    "query": query,
                    "variables": {"handle": handle, "cursor": cursor},
                },
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=8,
            )
            if response.status_code != 200:
                return None, title
            collection = ((response.json().get("data") or {}).get("collection")) or {}
            if not collection:
                return None, None
            found = str(collection.get("title") or "").strip()
            if found:
                title = found
            products = collection.get("products") or {}
            edges = products.get("edges") or []
            if not isinstance(edges, list):
                return None, title
            total += len(edges)
            page_info = products.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return total, title
            cursor = page_info.get("endCursor")
            if not cursor:
                return total, title
    except (requests.RequestException, ValueError, TypeError):
        return None, title
    return None, title


def _shopify_collection_html_count(session, shop_url: str, handle: str) -> int | None:
    """Số trên trang collection, vd. '186 products'."""
    try:
        response = session.get(
            f"{shop_url.rstrip('/')}/collections/{handle}",
            timeout=8,
        )
        if response.status_code != 200 or not response.text:
            return None
        counts = [
            int(raw.replace(",", ""))
            for raw in re.findall(r"(\d[\d,]*)\s+products\b", response.text, re.I)
        ]
        return max(counts) if counts else None
    except (requests.RequestException, ValueError, TypeError):
        return None


def _shopify_product_count(crawler) -> int | None:
    """Đếm bằng sitemap (file nhỏ). Không tải /products.json?limit=250 — body quá nặng, nhận diện bị treo."""
    try:
        if not crawler.use_public_api and crawler.access_token:
            response = crawler.session.get(
                f"{crawler.shop_url}/admin/api/{crawler.api_version}/products/count.json",
                headers={"X-Shopify-Access-Token": crawler.access_token},
                timeout=(3, 6),
            )
            if response.status_code == 200:
                return int(response.json().get("count", 0))

        sitemap_total = _shopify_sitemap_product_count(crawler.shop_url, crawler.session)
        if sitemap_total is not None:
            return sitemap_total

        response = crawler.session.get(
            f"{crawler.shop_url}/products.json",
            params={"limit": 30, "page": 1},
            timeout=(3, 5),
        )
        if response.status_code == 200:
            batch = response.json().get("products", [])
            if isinstance(batch, list) and len(batch) < 30:
                return len(batch)
    except (requests.RequestException, ValueError, TypeError):
        pass
    return None


def _count_product_handles_in_sitemap(text: str) -> int | None:
    """Đếm handle trong XML sitemap. None nếu body không phải sitemap."""
    if not text or "<loc" not in text.lower():
        return None
    seen: set[str] = set()
    for raw in re.findall(r"/products/([^<\s\"'#?]+)", text, flags=re.I):
        handle = unquote(raw).strip().strip("/")
        if not handle or handle.lower() in {"json"} or handle.lower().endswith(".xml"):
            continue
        seen.add(handle)
    return len(seen)


def _fetch_sitemap_text(crawler, sitemap_url: str) -> str | None:
    """Lấy đúng URL sitemap (giữ ?from=&to=). Direct, rồi curl, rồi 1 proxy."""
    from utils.http_session import create_http_session

    def from_response(response) -> str | None:
        if response is None or getattr(response, "status_code", 0) != 200:
            return None
        text = getattr(response, "text", "") or ""
        if "<loc" in text.lower():
            return text
        content = getattr(response, "content", b"") or b""
        if content[:2] == b"\x1f\x8b":
            import gzip

            try:
                content = gzip.decompress(content)
            except OSError:
                return None
        if not content:
            return None
        decoded = content.decode("utf-8", errors="replace")
        return decoded if "<loc" in decoded.lower() else None

    user_agent = ""
    session = getattr(crawler, "session", None)
    if session is not None:
        user_agent = session.headers.get("User-Agent") or ""
    direct = create_http_session(None)
    if user_agent:
        direct.headers["User-Agent"] = user_agent
    try:
        text = from_response(direct.get(sitemap_url, timeout=(5, 20)))
        if text:
            return text
    except requests.RequestException:
        pass

    try:
        from utils.curl_http import curl_get

        text = from_response(curl_get(sitemap_url, timeout=20))
        if text:
            return text
    except Exception:
        pass

    rotator = getattr(crawler, "proxy_rotator", None)
    if rotator is not None and rotator.has_proxy:
        proxied = create_http_session(rotator.current())
        if user_agent:
            proxied.headers["User-Agent"] = user_agent
        try:
            return from_response(proxied.get(sitemap_url, timeout=(5, 20)))
        except requests.RequestException:
            return None
    return None


def _shopify_one_sitemap_product_count(crawler, sitemap_url: str) -> int | None:
    """Đếm <loc>/products/...</loc> trong đúng URL đã dán, giữ nguyên query ?from=&to=."""
    text = _fetch_sitemap_text(crawler, sitemap_url)
    if not text:
        return None
    return _count_product_handles_in_sitemap(text)


def _shopify_sitemap_product_count(shop_url: str, session: requests.Session) -> int | None:
    """Đếm URL /products/... trong sitemap_products_*.xml."""
    from xml.etree import ElementTree as ET

    try:
        index = session.get(f"{shop_url.rstrip('/')}/sitemap.xml", timeout=(3, 8))
        if index.status_code != 200:
            return None

        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        root = ET.fromstring(index.content)
        smaps = [
            (el.text or "").strip()
            for el in root.findall(".//sm:loc", ns)
            if el.text and "sitemap_products" in el.text.lower()
        ]
        if not smaps:
            return None

        def _count_file(smap_url: str) -> int:
            local = requests.Session()
            local.headers.update(session.headers)
            local.proxies.update(session.proxies)
            try:
                resp = local.get(smap_url, timeout=(3, 8))
                if resp.status_code != 200:
                    return 0
                sm_root = ET.fromstring(resp.content)
            except (requests.RequestException, ET.ParseError):
                return 0
            finally:
                local.close()
            return sum(
                1
                for el in sm_root.findall(".//sm:loc", ns)
                if el.text and "/products/" in el.text
            )

        from concurrent.futures import ThreadPoolExecutor

        workers = min(8, len(smaps))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            total = sum(executor.map(_count_file, smaps))
        return total or None
    except (requests.RequestException, ET.ParseError, ValueError):
        return None


def _shopify_graphql_product_count(crawler, max_pages: int = 80) -> int | None:
    """Đếm SP qua Storefront GraphQL (fallback nếu sitemap lỗi)."""
    query = """
    query($cursor: String) {
      products(first: 250, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        edges { node { handle } }
      }
    }
    """
    cursor = None
    total = 0
    try:
        for _ in range(max_pages):
            payload = crawler._graphql_request(query, {"cursor": cursor})
            connection = (payload.get("data") or {}).get("products") or {}
            edges = connection.get("edges") or []
            total += len(edges)
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return total
            cursor = page_info.get("endCursor")
            if not cursor:
                return total
        return total
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return total or None
