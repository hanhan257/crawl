from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from utils.credentials import CrawlCredentials
from utils.detector import DetectedTarget, UnsupportedUrlError, resolve_url


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
        return f"{base}/product-category/{category_slug}/"
    if platform == "shopify":
        return f"{base}/collections/{category_slug}"
    if platform == "generic-web":
        return base_url
    return base_url


def _probe_generic_web(
    target: DetectedTarget,
    base_url: str,
    url_category: str | None,
) -> ShopProbeResult:
    from crawlers.generic_web import GenericWebCrawler

    crawler = GenericWebCrawler(base_url)

    if target.crawl_type == "product":
        return ShopProbeResult(
            ok=True,
            platform="generic-web",
            crawl_type="product",
            base_url=base_url,
            message="**1** SP · link sản phẩm /product/...",
            total_products=1,
            categories=[],
            url_category_slug=url_category or target.handle,
        )

    uses_shop_catalog = crawler._uses_shop_catalog()
    total = crawler.count_sitemap_products()
    if total <= 0 and not uses_shop_catalog:
        return ShopProbeResult(
            ok=True,
            platform="generic-web",
            crawl_type=target.crawl_type,
            base_url=base_url,
            message=(
                "Không đếm được SP — site thiếu **sitemap-products**. "
                "Chỉ crawl được link **/product/...** trực tiếp."
            ),
            total_products=None,
            categories=[],
            url_category_slug=url_category,
        )

    raw_categories, category_note = crawler.probe_categories(sample_size=24)
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
    if target.platform == "redbubble":
        return _probe_redbubble(target, credentials, base_url, url_category)

    if target.platform == "generic-web":
        return _probe_generic_web(target, base_url, url_category)

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
    )

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
    total = _woo_product_count(crawler)

    if total is None and categories:
        total = sum(c.count or 0 for c in categories) or None

    return ShopProbeResult(
        ok=True,
        platform="woocommerce",
        crawl_type=target.crawl_type,
        base_url=crawler.site_url,
        message=_stats_message(total, len(categories)),
        total_products=total,
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
    )

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

    categories = _shopify_list_collections(crawler)
    total = _shopify_product_count(crawler)

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
    while page <= 20:
        try:
            if crawler.use_public_api:
                response = crawler.session.get(
                    f"{crawler.site_url}/wp-json/wc/store/v1/products/categories",
                    params={"per_page": 100, "page": page},
                    timeout=30,
                )
            else:
                response = crawler.session.get(
                    f"{crawler.site_url}/wp-json/wc/v3/products/categories",
                    params={"per_page": 100, "page": page},
                    auth=(crawler.consumer_key, crawler.consumer_secret),
                    timeout=30,
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
                timeout=30,
            )
            if response.status_code == 200:
                total = response.headers.get("X-WP-Total")
                if total is not None:
                    return int(total)

        response = crawler.session.get(
            f"{crawler.site_url}/wp-json/wc/store/v1/products",
            params={"per_page": 1},
            timeout=30,
        )
        if response.status_code == 200:
            total = response.headers.get("X-WP-Total") or response.headers.get("x-wp-total")
            if total is not None:
                return int(total)
    except (requests.RequestException, ValueError, TypeError):
        pass
    return None


def _shopify_list_collections(crawler) -> list[ShopCategoryInfo]:
    categories: list[ShopCategoryInfo] = []
    page = 1

    while page <= 10:
        try:
            response = crawler.session.get(
                f"{crawler.shop_url}/collections.json",
                params={"limit": 250, "page": page},
                timeout=30,
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


def _shopify_product_count(crawler) -> int | None:
    try:
        if not crawler.use_public_api and crawler.access_token:
            response = crawler.session.get(
                f"{crawler.shop_url}/admin/api/{crawler.api_version}/products/count.json",
                headers={"X-Shopify-Access-Token": crawler.access_token},
                timeout=30,
            )
            if response.status_code == 200:
                return int(response.json().get("count", 0))

        response = crawler.session.get(
            f"{crawler.shop_url}/products.json",
            params={"limit": 250, "page": 1},
            timeout=30,
        )
        if response.status_code != 200:
            return None
        batch = response.json().get("products", [])
        if len(batch) < 250:
            return len(batch)
        return 250
    except (requests.RequestException, ValueError, TypeError):
        pass
    return None
