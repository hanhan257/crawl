from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse

import requests

CrawlProgressCallback = Callable[[int, int, int], None]

from utils.credentials import CrawlCredentials
from utils.detector import DetectedTarget, UnsupportedUrlError, resolve_url
from utils.http_session import normalize_proxy_url


class CrawlError(Exception):
    pass


def run_auto_crawl(
    url: str,
    credentials: CrawlCredentials | None = None,
    limit: int | None = None,
    max_pages: int | None = None,
    proxy_url: str | None = None,
    category_slug: str | None = None,
    progress_callback: CrawlProgressCallback | None = None,
) -> tuple[list, DetectedTarget]:
    from crawlers.generic_web import GenericWebCrawler
    from crawlers.redbubble import RedbubbleCrawler
    from crawlers.shopify import ShopifyCrawler
    from crawlers.woocommerce import WooCommerceCrawler

    credentials = credentials or CrawlCredentials.load()
    raw_proxy = (
        str(proxy_url).strip() if proxy_url is not None else credentials.proxy_url
    )
    raw_proxy = raw_proxy.strip() if raw_proxy else None
    effective_proxy = normalize_proxy_url(raw_proxy)
    target = resolve_url(url)

    if target.platform == "redbubble":
        crawler = RedbubbleCrawler(proxy_url=raw_proxy)
        if target.crawl_type == "product":
            products = crawler.crawl(url=target.url, limit=limit)
        elif target.crawl_type == "category":
            products = crawler.crawl_category(
                category_url=target.url,
                limit=limit,
                max_pages=max_pages or 1,
            )
        else:
            raise CrawlError(f"Redbubble: chưa hỗ trợ loại link '{target.crawl_type}'")

        if not products:
            raise CrawlError("Redbubble: không crawl được sản phẩm nào từ link này.")
        return products, target

    if target.platform == "shopify":
        host = urlparse(target.base_url or target.url).netloc
        token = credentials.shopify_token_for(host)
        crawler = ShopifyCrawler(
            shop_url=target.base_url or target.url,
            access_token=token,
            use_public_api=not token,
        )

        try:
            if target.crawl_type == "product" and target.handle:
                products = crawler.crawl_product(target.handle)
            elif target.crawl_type == "category" and target.handle:
                products = crawler.crawl_category(
                    target.handle,
                    limit=limit,
                    max_pages=max_pages,
                )
            elif target.crawl_type == "shop":
                products = crawler.crawl(limit=limit, max_pages=max_pages)
            else:
                raise CrawlError(f"Shopify: chưa hỗ trợ loại link '{target.crawl_type}'")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (401, 403):
                raise CrawlError(
                    "Shopify: store yêu cầu token. Thêm token vào config.json hoặc --token."
                ) from exc
            raise CrawlError(f"Shopify: lỗi HTTP {exc}") from exc

        if not products:
            raise CrawlError("Shopify: không crawl được sản phẩm nào. Store có thể tắt public API.")
        return products, target

    if target.platform == "woocommerce":
        host = urlparse(target.base_url or target.url).netloc
        key, secret = credentials.woocommerce_for(host)
        crawler = WooCommerceCrawler(
            site_url=target.base_url or target.url,
            consumer_key=key or "",
            consumer_secret=secret or "",
            use_public_api=not (key and secret),
        )

        try:
            if target.crawl_type == "product" and target.handle:
                products = crawler.crawl_product(target.handle)
            elif target.crawl_type == "category" and target.category_slug:
                products = crawler.crawl_category(
                    target.category_slug,
                    limit=limit,
                    max_pages=max_pages,
                )
            elif target.crawl_type == "shop":
                products = crawler.crawl(limit=limit, max_pages=max_pages)
            else:
                raise CrawlError(f"WooCommerce: chưa hỗ trợ loại link '{target.crawl_type}'")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (401, 403):
                raise CrawlError(
                    "WooCommerce: cần API key/secret. Thêm vào config.json hoặc --key/--secret."
                ) from exc
            raise CrawlError(f"WooCommerce: lỗi HTTP {exc}") from exc
        except ValueError as exc:
            raise CrawlError(str(exc)) from exc

        if not products:
            raise CrawlError("WooCommerce: không crawl được sản phẩm nào từ link này.")
        return products, target

    if target.platform == "generic-web":
        crawler = GenericWebCrawler(
            site_url=target.base_url or target.url,
            proxy_url=effective_proxy,
        )
        try:
            if target.crawl_type == "product" and target.handle:
                product = crawler.crawl_product(target.handle)
                products = [product] if product else []
            elif target.crawl_type in ("shop", "category"):
                effective_category = category_slug
                if target.crawl_type == "category" and not effective_category:
                    effective_category = target.category_slug
                products = crawler.crawl(
                    limit=limit,
                    max_pages=max_pages,
                    category_slug=effective_category,
                    progress_callback=progress_callback,
                )
            else:
                raise CrawlError(f"Web thuần: chưa hỗ trợ loại link '{target.crawl_type}'")
        except requests.RequestException as exc:
            raise CrawlError(f"Web thuần: lỗi HTTP {exc}") from exc
        except ValueError as exc:
            raise CrawlError(str(exc)) from exc

        if not products:
            raise CrawlError(
                "Web thuần: không crawl được sản phẩm nào. "
                "Site cần sitemap-products, trang /shop/... hoặc link /product/..."
            )
        return products, target

    raise CrawlError(
        f"Không crawl được link này. Nền tảng: {target.platform}, loại: {target.crawl_type}"
    )
