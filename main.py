#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

from crawlers.redbubble import RedbubbleCrawler
from crawlers.shopify import ShopifyCrawler
from crawlers.woocommerce import WooCommerceCrawler
from exporters.export import default_output_path, export_products
from utils.credentials import CrawlCredentials
from utils.crawl_runner import CrawlError, run_auto_crawl
from utils.detector import UnsupportedUrlError, resolve_url


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, help="Max number of products")
    parser.add_argument("--max-pages", type=int, help="Max pages to crawl")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument(
        "--format",
        choices=["csv", "json", "shopify-csv", "woocommerce-csv"],
        default="csv",
        help=(
            "csv/json = dữ liệu crawl thô; "
            "shopify-csv = CSV import Shopify; "
            "woocommerce-csv = CSV import WooCommerce"
        ),
    )
    parser.add_argument("--config", help="Path to config.json for API credentials")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crawl products from Shopify, WooCommerce, and Redbubble.",
    )
    subparsers = parser.add_subparsers(dest="platform", required=True)

    auto = subparsers.add_parser(
        "auto",
        help="Nhập link -> tự nhận diện nền tảng và crawl",
    )
    auto.add_argument("--url", required=True, help="Product/category/shop URL")
    auto.add_argument("--pages", type=int, default=1, help="Pages for category crawl")
    auto.add_argument("--token", help="Shopify token override")
    auto.add_argument("--key", help="WooCommerce key override")
    auto.add_argument("--secret", help="WooCommerce secret override")
    add_common_args(auto)

    shopify = subparsers.add_parser("shopify", help="Crawl Shopify store")
    shopify.add_argument("--shop", required=True, help="Shop URL, e.g. mystore.myshopify.com")
    shopify.add_argument("--token", help="Shopify Admin API access token")
    shopify.add_argument("--public", action="store_true", help="Use public /products.json endpoint")
    shopify.add_argument("--category", help="Collection handle, e.g. t-shirts")
    shopify.add_argument("--product", help="Product handle")
    shopify.add_argument("--api-version", default="2024-10")
    add_common_args(shopify)

    woo = subparsers.add_parser("woocommerce", help="Crawl WooCommerce store")
    woo.add_argument("--url", required=True, help="Site URL, e.g. https://example.com")
    woo.add_argument("--key", help="WooCommerce consumer key")
    woo.add_argument("--secret", help="WooCommerce consumer secret")
    woo.add_argument("--category", help="Category slug")
    woo.add_argument("--product", help="Product slug")
    woo.add_argument("--insecure", action="store_true", help="Disable SSL verification")
    add_common_args(woo)

    redbubble = subparsers.add_parser("redbubble", help="Crawl Redbubble search or product pages")
    redbubble.add_argument("--query", help="Search query, e.g. t-shirt")
    redbubble.add_argument("--category-url", help="Category/search page URL")
    redbubble.add_argument("--url", help="Single product URL")
    redbubble.add_argument("--urls", nargs="+", help="Multiple product URLs")
    redbubble.add_argument("--pages", type=int, default=1, help="Number of search result pages")
    add_common_args(redbubble)

    return parser


def load_credentials(args: argparse.Namespace) -> CrawlCredentials:
    creds = CrawlCredentials.load(getattr(args, "config", None))
    if getattr(args, "token", None):
        creds.shopify_token = args.token
    if getattr(args, "key", None):
        creds.woocommerce_key = args.key
    if getattr(args, "secret", None):
        creds.woocommerce_secret = args.secret
    return creds


def output_name_from_target(target) -> str:
    return (
        target.category_slug
        or target.handle
        or target.query
        or urlparse(target.url).path.strip("/").replace("/", "_")
        or "products"
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.platform == "auto":
            creds = load_credentials(args)
            try:
                preview = resolve_url(args.url)
            except UnsupportedUrlError as exc:
                print(f"Không crawl được: {exc}", file=sys.stderr)
                return 1

            print(
                f"Nền tảng: {preview.platform} | "
                f"Loại: {preview.crawl_type} | "
                f"Link: {preview.url}"
            )

            products, target = run_auto_crawl(
                url=args.url,
                credentials=creds,
                limit=args.limit,
                max_pages=args.pages or args.max_pages,
            )
            output = args.output or default_output_path(
                target.platform,
                output_name_from_target(target),
                args.format,
            )

        elif args.platform == "shopify":
            crawler = ShopifyCrawler(
                shop_url=args.shop,
                access_token=args.token,
                api_version=args.api_version,
                use_public_api=args.public or not args.token,
            )
            if args.product:
                products = crawler.crawl_product(args.product)
            elif args.category:
                products = crawler.crawl_category(
                    args.category,
                    limit=args.limit,
                    max_pages=args.max_pages,
                )
            else:
                products = crawler.crawl(limit=args.limit, max_pages=args.max_pages)

            name = args.product or args.category or urlparse(args.shop).netloc
            output = args.output or default_output_path("shopify", name, args.format)

        elif args.platform == "woocommerce":
            crawler = WooCommerceCrawler(
                site_url=args.url,
                consumer_key=args.key or "",
                consumer_secret=args.secret or "",
                verify_ssl=not args.insecure,
                use_public_api=not (args.key and args.secret),
            )
            if args.product:
                products = crawler.crawl_product(args.product)
            elif args.category:
                products = crawler.crawl_category(
                    args.category,
                    limit=args.limit,
                    max_pages=args.max_pages,
                )
            else:
                products = crawler.crawl(limit=args.limit, max_pages=args.max_pages)

            name = args.product or args.category or urlparse(args.url).netloc
            output = args.output or default_output_path("woocommerce", name, args.format)

        elif args.platform == "redbubble":
            if not args.query and not args.url and not args.urls and not args.category_url:
                parser.error("redbubble requires --query, --category-url, --url, or --urls")

            crawler = RedbubbleCrawler()
            if args.category_url:
                products = crawler.crawl_category(
                    category_url=args.category_url,
                    limit=args.limit,
                    max_pages=args.pages,
                )
                name = args.category_url
            else:
                products = crawler.crawl(
                    query=args.query,
                    url=args.url,
                    urls=args.urls,
                    limit=args.limit,
                    max_pages=args.pages,
                )
                name = args.query or args.url or "products"

            output = args.output or default_output_path("redbubble", name, args.format)
        else:
            parser.error(f"Unknown platform: {args.platform}")
            return 1

        if not products:
            print("Không crawl được sản phẩm nào.", file=sys.stderr)
            return 1

        output_path = export_products(products, output, fmt=args.format)
        format_hint = {
            "shopify-csv": " (Shopify import CSV)",
            "woocommerce-csv": " (WooCommerce import CSV)",
        }.get(args.format, "")
        print(f"Crawled {len(products)} products -> {output_path}{format_hint}")
        if args.format == "woocommerce-csv":
            extras = Path(output_path).with_suffix("")
            if extras.is_dir():
                print(f"Extras (anh, huong dan, PHP fix) -> {extras}/")
        return 0

    except (CrawlError, UnsupportedUrlError, ValueError) as exc:
        print(f"Không crawl được: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
