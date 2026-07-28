from __future__ import annotations



from collections.abc import Callable

from urllib.parse import urlparse



import requests



CrawlProgressCallback = Callable[[int, int, int], None]
PartialProductsCallback = Callable[[list], None]



from utils.credentials import CrawlCredentials

from utils.detector import DetectedTarget, UnsupportedUrlError, resolve_url

from utils.http_session import normalize_proxy_url





class CrawlError(Exception):
    """Lỗi crawl — có thể kèm SP đã lấy được để UI vẫn cho xuất file."""

    def __init__(self, message: str, partial_products: list | None = None):
        super().__init__(message)
        self.partial_products = list(partial_products or [])


def _apply_crawl_description_format(products: list, description_format: str) -> list:
    """Áp dụng HTML / text theo lựa chọn lúc crawl."""
    fmt = (description_format or "html").strip().lower()
    if not products:
        return products
    from utils.description_html import format_description_output

    for product in products:
        product.description = format_description_output(product.description, fmt)
    return products


def run_auto_crawl(

    url: str,

    credentials: CrawlCredentials | None = None,

    limit: int | None = None,

    max_pages: int | None = None,

    proxy_url: str | None = None,

    category_slug: str | None = None,

    progress_callback: CrawlProgressCallback | None = None,

    max_workers: int = 1,

    description_format: str = "html",

    partial_callback: PartialProductsCallback | None = None,

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

    workers = max(1, min(int(max_workers or 1), 500))



    if target.platform == "fineartamerica":
        from crawlers.fineartamerica import FineArtAmericaCrawler

        crawler = FineArtAmericaCrawler(
            site_url=target.base_url or target.url,
            proxy_url=raw_proxy,
        )
        if partial_callback is not None:
            crawler.partial_callback = partial_callback

        def _faa_partial() -> list:
            return list(getattr(crawler, "last_products", None) or [])

        try:
            if target.crawl_type == "product":
                products = crawler.crawl_product(target.url)
            elif target.crawl_type == "sitemap":
                products = crawler.crawl_sitemap(
                    target.url,
                    limit=limit,
                    max_pages=max_pages,
                    max_workers=workers,
                    progress_callback=progress_callback,
                )
            elif target.crawl_type == "shop":
                products = crawler.crawl(
                    limit=limit,
                    max_pages=max_pages,
                    max_workers=workers,
                    progress_callback=progress_callback,
                )
            elif target.crawl_type == "category":
                products = crawler.crawl_listing(
                    target.url,
                    limit=limit,
                    max_pages=max_pages,
                    max_workers=workers,
                    progress_callback=progress_callback,
                )
            else:
                raise CrawlError(
                    f"Fine Art America: chưa hỗ trợ loại link '{target.crawl_type}'"
                )
        except CrawlError:
            raise
        except Exception as exc:
            partial = _faa_partial()
            if partial:
                raise CrawlError(
                    f"Fine Art America: dừng sớm ({exc}). Vẫn giữ {len(partial)} SP.",
                    partial_products=partial,
                ) from exc
            raise CrawlError(f"Fine Art America: {exc}") from exc

        if not products:
            partial = _faa_partial()
            if partial:
                products = partial
            else:
                raise CrawlError(
                    "Fine Art America: không lấy được SP. "
                    "Site hay chặn IP (429) — bật **Proxy**, luồng **4–8**, "
                    "hoặc dán link artist/collection cụ thể."
                )
        return _apply_crawl_description_format(products, description_format), target

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

        return _apply_crawl_description_format(products, description_format), target



    if target.platform == "shopify":

        host = urlparse(target.base_url or target.url).netloc

        token = credentials.shopify_token_for(host)

        crawler = ShopifyCrawler(

            shop_url=target.base_url or target.url,

            access_token=token,

            use_public_api=not token,

            proxy_url=raw_proxy,

        )
        if partial_callback is not None:
            crawler.partial_callback = partial_callback

        def _shopify_partial() -> list:
            return list(getattr(crawler, "last_products", None) or [])

        try:

            if target.crawl_type == "product" and target.handle:

                products = crawler.crawl_product(target.handle)

            elif target.crawl_type == "category" and target.handle:

                products = crawler.crawl_category(

                    target.handle,

                    limit=limit,

                    max_pages=max_pages,

                    max_workers=workers,

                    progress_callback=progress_callback,

                )

            elif target.crawl_type == "shop":

                products = crawler.crawl(

                    limit=limit,

                    max_pages=max_pages,

                    max_workers=workers,

                    progress_callback=progress_callback,

                )

            elif target.crawl_type == "sitemap":

                products = crawler.crawl_sitemap(

                    sitemap_url=target.url,

                    limit=limit,

                    max_pages=max_pages,

                    max_workers=workers,

                    progress_callback=progress_callback,

                )

            elif target.crawl_type == "search" and target.query:

                products = crawler.crawl_search(

                    target.query,

                    limit=limit,

                    max_pages=max_pages,

                    max_workers=workers,

                    progress_callback=progress_callback,

                )

            else:

                raise CrawlError(f"Shopify: chưa hỗ trợ loại link '{target.crawl_type}'")

        except CrawlError:
            raise
        except requests.HTTPError as exc:
            partial = _shopify_partial()
            if exc.response is not None and exc.response.status_code in (401, 403):
                raise CrawlError(
                    "Shopify: store yêu cầu token. Thêm token vào config.json hoặc --token.",
                    partial_products=partial,
                ) from exc
            raise CrawlError(
                f"Shopify: lỗi HTTP {exc}",
                partial_products=partial,
            ) from exc
        except Exception as exc:
            partial = _shopify_partial()
            if partial:
                raise CrawlError(
                    f"Shopify: crawl dừng sớm ({exc}). Vẫn giữ được {len(partial)} SP.",
                    partial_products=partial,
                ) from exc
            raise



        if not products:
            partial = _shopify_partial()
            if partial:
                products = partial
            else:
                raise CrawlError(
                    "Shopify: không crawl được sản phẩm nào. Store có thể tắt public API."
                )

        return _apply_crawl_description_format(products, description_format), target



    if target.platform == "shopbase":

        from crawlers.shopbase import ShopBaseCrawler

        crawler = ShopBaseCrawler(

            site_url=target.base_url or target.url,

            proxy_url=effective_proxy,

        )

        try:

            if target.crawl_type == "product" and target.handle:

                product = crawler.crawl_product(target.handle)

                products = [product] if product else []

            elif target.crawl_type == "category" and target.handle:

                products = crawler.crawl_collection(

                    target.handle,

                    limit=limit,

                    max_pages=max_pages,

                    progress_callback=progress_callback,

                    max_workers=workers,

                )

            elif target.crawl_type == "shop":

                products = crawler.crawl(

                    limit=limit,

                    max_pages=max_pages,

                    progress_callback=progress_callback,

                    max_workers=workers,

                )

            else:

                raise CrawlError(f"ShopBase: chưa hỗ trợ loại link '{target.crawl_type}'")

        except requests.RequestException as exc:

            raise CrawlError(f"ShopBase: lỗi HTTP {exc}") from exc



        if not products:

            if target.crawl_type == "category" and target.handle:
                meta = crawler._collection_meta(target.handle)
                if meta is not None:
                    try:
                        count = int(meta.get("product_count") or 0)
                    except (TypeError, ValueError):
                        count = -1
                    if count == 0:
                        title = str(meta.get("title") or target.handle)
                        raise CrawlError(
                            f"ShopBase: collection **{title}** không có sản phẩm (0 SP). "
                            "Chọn **Tất cả** hoặc danh mục khác."
                        )
            raise CrawlError("ShopBase: không crawl được sản phẩm nào từ link này.")

        return _apply_crawl_description_format(products, description_format), target



    if target.platform == "trendsi":

        from crawlers.trendsi import TrendsiCrawler

        crawler = TrendsiCrawler(proxy_url=effective_proxy)

        try:

            if target.crawl_type == "product" and target.handle:

                product = crawler.crawl_product(

                    target.handle,

                    brand_name=str(target.query or ""),

                )

                products = [product] if product else []

            elif target.crawl_type == "brand":

                products = crawler.crawl_brand(

                    target.url,

                    limit=limit,

                    max_pages=max_pages,

                    progress_callback=progress_callback,

                    max_workers=workers,

                )

            elif target.crawl_type == "category":

                products = crawler.crawl_category(

                    target.url,

                    limit=limit,

                    max_pages=max_pages,

                    progress_callback=progress_callback,

                    max_workers=workers,

                )

            elif target.crawl_type == "shop":

                products = crawler.crawl(

                    limit=limit,

                    max_pages=max_pages,

                    progress_callback=progress_callback,

                    max_workers=workers,

                )

            else:

                raise CrawlError(f"Trendsi: chưa hỗ trợ loại link '{target.crawl_type}'")

        except requests.RequestException as exc:

            raise CrawlError(f"Trendsi: lỗi HTTP {exc}") from exc



        if not products:
            raise CrawlError(
                "Trendsi: không crawl được sản phẩm nào. "
                "Thử **Luồng crawl = 8**, tắt **Proxy**, hoặc crawl lại sau vài phút."
            )

        return _apply_crawl_description_format(products, description_format), target



    if target.platform == "magento":

        from crawlers.magento_graphql import MagentoGraphqlCrawler

        crawler = MagentoGraphqlCrawler(
            target.base_url or target.url,
            proxy_url=effective_proxy,
            description_format=description_format or "html",
        )

        try:

            if target.crawl_type == "product" and target.handle:

                product = crawler.crawl_product(target.handle)

                products = [product] if product else []

            elif target.crawl_type == "category":

                products = crawler.crawl_category(

                    target.category_slug or target.handle or target.url,

                    limit=limit,

                    max_pages=max_pages,

                    progress_callback=progress_callback,

                    max_workers=workers,

                )

            elif target.crawl_type == "shop":

                products = crawler.crawl(

                    limit=limit,

                    max_pages=max_pages,

                    progress_callback=progress_callback,

                    max_workers=workers,

                )

            else:

                raise CrawlError(
                    f"Magento: chưa hỗ trợ loại link '{target.crawl_type}'. "
                    "Dùng `/jewelry.html` hoặc link sản phẩm `.html`."
                )

        except requests.RequestException as exc:

            raise CrawlError(f"Magento: lỗi GraphQL/HTTP {exc}") from exc



        if not products:

            raise CrawlError(
                "Magento: không crawl được sản phẩm nào. "
                "Thử category khác hoặc giảm **Luồng crawl** / tắt Proxy."
            )

        return _apply_crawl_description_format(products, description_format), target



    if target.platform == "woocommerce":

        host = urlparse(target.base_url or target.url).netloc

        key, secret = credentials.woocommerce_for(host)

        crawler = WooCommerceCrawler(

            site_url=target.base_url or target.url,

            consumer_key=key or "",

            consumer_secret=secret or "",

            use_public_api=not (key and secret),

            proxy_url=raw_proxy,

        )



        try:

            if target.crawl_type == "product" and target.handle:

                products = crawler.crawl_product(target.handle)

            elif target.crawl_type == "category" and target.category_slug:

                products = crawler.crawl_category(

                    target.category_slug,

                    limit=limit,

                    max_pages=max_pages,

                    max_workers=workers,

                    progress_callback=progress_callback,

                )

            elif target.crawl_type == "shop":

                products = crawler.crawl(

                    limit=limit,

                    max_pages=max_pages,

                    max_workers=workers,

                    progress_callback=progress_callback,

                )

            elif target.crawl_type == "search" and target.query:

                products = crawler.crawl_search(

                    target.query,

                    limit=limit,

                    max_pages=max_pages,

                    max_workers=workers,

                    progress_callback=progress_callback,

                )

            else:

                raise CrawlError(f"WooCommerce: chưa hỗ trợ loại link '{target.crawl_type}'")

        except requests.HTTPError as exc:

            if exc.response is not None and exc.response.status_code in (401, 403):

                raise CrawlError(

                    "WooCommerce: API bị khóa (401/403) và không crawl được qua HTML/sitemap. "
                    "Thử tắt Proxy, tăng Luồng crawl, hoặc thêm API key vào config.json."

                ) from exc

            raise CrawlError(f"WooCommerce: lỗi HTTP {exc}") from exc

        except ValueError as exc:

            raise CrawlError(str(exc)) from exc



        if not products:

            raise CrawlError("WooCommerce: không crawl được sản phẩm nào từ link này.")

        return _apply_crawl_description_format(products, description_format), target



    if target.platform == "generic-web":

        crawler = GenericWebCrawler(

            site_url=target.base_url or target.url,

            proxy_url=effective_proxy,

        )

        try:

            if target.crawl_type == "product" and target.handle:

                if crawler._uses_html_catalog():

                    products = crawler.crawl_product(target.handle)

                    products = [products] if products else []

                else:

                    product = crawler.crawl_product(target.handle)

                    products = [product] if product else []

            elif target.crawl_type == "category":

                if crawler._uses_html_catalog() and target.handle:

                    products = crawler.crawl_category_path(

                        f"{target.handle}.html",

                        limit=limit,

                        max_pages=max_pages,

                        progress_callback=progress_callback,

                        max_workers=workers,

                    )

                else:

                    effective_category = category_slug or target.category_slug

                    products = crawler.crawl(

                        limit=limit,

                        max_pages=max_pages,

                        category_slug=effective_category,

                        progress_callback=progress_callback,

                        max_workers=workers,

                    )

            elif target.crawl_type == "search" and target.query:

                products = crawler.crawl_search(

                    target.query,

                    limit=limit,

                    max_pages=max_pages,

                    progress_callback=progress_callback,

                    max_workers=workers,

                )

            elif target.crawl_type == "shop":

                products = crawler.crawl(

                    limit=limit,

                    max_pages=max_pages,

                    category_slug=category_slug,

                    progress_callback=progress_callback,

                    max_workers=workers,

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

                "Thử link danh mục .html, /product/..., /shop/... hoặc sitemap-products."

            )

        return _apply_crawl_description_format(products, description_format), target



    raise CrawlError(

        f"Không crawl được link này. Nền tảng: {target.platform}, loại: {target.crawl_type}"

    )


