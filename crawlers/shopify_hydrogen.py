"""Shopify Hydrogen / Oxygen (Remix) storefront crawler."""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

import requests

from crawlers.base import BaseCrawler
from crawlers.shopify import ShopifyCrawler
from models.product import Product
from utils.http_session import ProxyRotator, create_http_session
from utils.remix_context import DEFAULT_USER_AGENT, extract_remix_context, fetch_page_text

CrawlProgressCallback = Callable[[int, int, int], None]
GIFT_CARD_HANDLE = "influxer-gift-card"


class ShopifyHydrogenCrawler(BaseCrawler):
    source = "shopify-hydrogen"

    def __init__(
        self,
        shop_url: str,
        *,
        proxy_url: str | None = None,
        max_workers: int = 32,
    ) -> None:
        self.shop_url = shop_url.strip().rstrip("/")
        if not self.shop_url.startswith("http"):
            self.shop_url = f"https://{self.shop_url}"
        self.max_workers = max(1, min(max_workers, 64))
        self.proxy_rotator = ProxyRotator(proxy_url)
        self.session = create_http_session(self.proxy_rotator.current())
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        self.partial_callback: Callable[[list[Product]], None] | None = None
        self.last_products: list[Product] = []

    @classmethod
    def is_hydrogen_store(
        cls,
        shop_url: str,
        session: requests.Session | None = None,
    ) -> bool:
        base = shop_url.strip().rstrip("/")
        if not base.startswith("http"):
            base = f"https://{base}"
        sess = session or requests.Session()
        sess.headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
        try:
            response = sess.get(
                f"{base}/products.json",
                params={"limit": 1},
                timeout=12,
            )
            if response.status_code == 200:
                content_type = (response.headers.get("content-type") or "").lower()
                if "json" in content_type:
                    payload = response.json()
                    if isinstance(payload, dict) and isinstance(
                        payload.get("products"), list
                    ):
                        return False
        except (requests.RequestException, ValueError):
            pass
        try:
            home = sess.get(base, timeout=15)
            if home.status_code != 200:
                return False
            text = home.text
            return (
                "cdn.shopify.com" in text
                and (
                    "__remixContext" in text
                    or "oxygen-v2" in text
                    or "shopifyhydrogen" in text.lower()
                )
            )
        except requests.RequestException:
            return False

    @classmethod
    def myshopify_domain(
        cls,
        shop_url: str,
        session: requests.Session | None = None,
    ) -> str | None:
        base = shop_url.strip().rstrip("/")
        if not base.startswith("http"):
            base = f"https://{base}"
        sess = session or requests.Session()
        sess.headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
        text = ""
        try:
            text = fetch_page_text(sess, base, timeout=20, max_retries=2)
            ctx = extract_remix_context(text)
            domain = (ctx.get("state", {}).get("loaderData", {}).get("root") or {}).get(
                "publicStoreDomain"
            )
            if isinstance(domain, str) and domain.strip():
                return domain.strip()
        except (requests.RequestException, ValueError, KeyError):
            pass
        match = re.search(r"([a-z0-9-]+\.myshopify\.com)", text)
        return match.group(1) if match else None

    @staticmethod
    def _walk_products(obj, found: list[dict]) -> None:
        if isinstance(obj, dict):
            if (
                "handle" in obj
                and "priceRange" in obj
                and "title" in obj
                and obj.get("handle") != GIFT_CARD_HANDLE
            ):
                found.append(obj)
            for value in obj.values():
                ShopifyHydrogenCrawler._walk_products(value, found)
        elif isinstance(obj, list):
            for item in obj:
                ShopifyHydrogenCrawler._walk_products(item, found)

    @staticmethod
    def _find_athletes_index(loader_data: dict) -> dict | None:
        for value in loader_data.values():
            if not isinstance(value, dict):
                continue
            athletes = value.get("athletes")
            if isinstance(athletes, dict) and isinstance(athletes.get("nodes"), list):
                return value
        return None

    @staticmethod
    def _find_athlete_detail(loader_data: dict) -> dict | None:
        for key, value in loader_data.items():
            if key == "root" or not isinstance(value, dict):
                continue
            if "athlete" in value or "productFacets" in value:
                return value
            products: list[dict] = []
            ShopifyHydrogenCrawler._walk_products(value, products)
            if products:
                return value
        return None

    def _map_product(self, node: dict) -> Product:
        handle = str(node.get("handle") or "").strip()
        price_range = node.get("priceRange") or {}
        min_price = price_range.get("minVariantPrice") or {}
        amount = min_price.get("amount")
        price = float(amount) if amount is not None else None
        images: list[str] = []
        featured = node.get("featuredImage") or {}
        if isinstance(featured, dict) and featured.get("url"):
            images.append(featured["url"])
        image_block = node.get("images")
        if isinstance(image_block, dict):
            for edge in image_block.get("nodes") or []:
                if isinstance(edge, dict) and edge.get("url"):
                    images.append(edge["url"])
        categories: list[str] = []
        if node.get("productType"):
            categories.append(str(node["productType"]))
        college = node.get("college")
        if isinstance(college, dict) and college.get("value"):
            categories.append(str(college["value"]))
        sport = node.get("sport")
        if isinstance(sport, dict) and sport.get("value"):
            categories.append(str(sport["value"]))
        tags = node.get("tags") or []
        if isinstance(tags, list):
            categories.extend(str(tag) for tag in tags[:3] if tag)
        seen: set[str] = set()
        categories = [c for c in categories if c and (c not in seen and not seen.add(c))]

        product_id = str(node.get("id") or handle)
        return Product(
            source=self.source,
            id=product_id,
            title=str(node.get("title") or ""),
            price=price,
            currency=min_price.get("currencyCode"),
            url=f"{self.shop_url}/products/{handle}" if handle else self.shop_url,
            image_urls=images,
            vendor=node.get("vendor"),
            tags=list(tags) if isinstance(tags, list) else [],
            categories=categories,
            product_type=node.get("productType"),
        )

    def _publish_partial(self, products: list[Product]) -> None:
        self.last_products = list(products)
        if self.partial_callback is not None:
            try:
                self.partial_callback(self.last_products)
            except Exception:
                pass

    def _crawl_via_myshopify(
        self,
        *,
        limit: int | None,
        max_pages: int | None,
        max_workers: int,
        progress_callback: CrawlProgressCallback | None,
    ) -> list[Product] | None:
        domain = self.myshopify_domain(self.shop_url, session=self.session)
        if not domain:
            return None
        backend = ShopifyCrawler(
            shop_url=f"https://{domain}",
            use_public_api=True,
            proxy_url=self.proxy_rotator.current(),
        )
        if not backend._public_products_json_ok() and not backend._graphql_available():
            return None
        products = backend.crawl(
            limit=limit,
            max_pages=max_pages,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )
        backend_base = f"https://{domain}"
        for product in products:
            if product.url and product.url.startswith(backend_base):
                product.url = product.url.replace(backend_base, self.shop_url, 1)
            product.source = self.source
        return products

    def _iter_athlete_handles(self):
        url = f"{self.shop_url}/browse/athletes"
        pages = 0
        while url:
            text = fetch_page_text(self.session, url)
            ctx = extract_remix_context(text)
            route = self._find_athletes_index(ctx.get("state", {}).get("loaderData", {}))
            if not route:
                break
            athletes = route.get("athletes") or {}
            nodes = athletes.get("nodes") or []
            page_info = athletes.get("pageInfo") or {}
            for node in nodes:
                if isinstance(node, dict):
                    handle = (node.get("handle") or "").strip()
                    if handle:
                        yield handle
            pages += 1
            if not page_info.get("hasNextPage"):
                break
            url = (
                f"{self.shop_url}/browse/athletes?"
                + urllib.parse.urlencode({"cursor": page_info["endCursor"]})
            )

    def _fetch_athlete_products(self, athlete_handle: str) -> list[dict]:
        url = f"{self.shop_url}/athletes/{athlete_handle}"
        session = create_http_session(self.proxy_rotator.current())
        session.headers["User-Agent"] = DEFAULT_USER_AGENT
        try:
            text = fetch_page_text(session, url, timeout=90, max_retries=4)
            ctx = extract_remix_context(text)
            route = self._find_athlete_detail(
                ctx.get("state", {}).get("loaderData", {})
            )
            if not route:
                return []
            products: list[dict] = []
            self._walk_products(route, products)
            return products
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            return []

    def _crawl_athlete_catalog(
        self,
        *,
        limit: int | None,
        progress_callback: CrawlProgressCallback | None,
    ) -> list[Product]:
        by_handle: dict[str, Product] = {}
        lock = threading.Lock()
        done = 0
        pending_handles: list[str] = []

        def ingest(raw_products: list[dict]) -> None:
            nonlocal done
            with lock:
                done += 1
                for raw in raw_products:
                    handle = str(raw.get("handle") or "").strip()
                    if not handle:
                        continue
                    by_handle[handle] = self._map_product(raw)
                    if limit is not None and len(by_handle) >= limit:
                        break
                self._publish_partial(list(by_handle.values()))
                if progress_callback:
                    total = max(len(pending_handles), done)
                    progress_callback(done, total, len(by_handle))

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures: dict = {}
            try:
                for handle in self._iter_athlete_handles():
                    pending_handles.append(handle)
                    if limit is not None and len(by_handle) >= limit:
                        break
                    futures[executor.submit(self._fetch_athlete_products, handle)] = handle
                    if len(futures) >= self.max_workers * 4:
                        completed = wait(
                            futures.keys(),
                            return_when=FIRST_COMPLETED,
                        ).done
                        for future in completed:
                            futures.pop(future)
                            ingest(future.result())
                            if limit is not None and len(by_handle) >= limit:
                                break
                while futures:
                    completed = wait(futures.keys(), return_when=FIRST_COMPLETED).done
                    for future in completed:
                        futures.pop(future)
                        ingest(future.result())
                        if limit is not None and len(by_handle) >= limit:
                            futures.clear()
                            break
            except Exception:
                while futures:
                    completed = wait(futures.keys(), return_when=FIRST_COMPLETED).done
                    for future in completed:
                        futures.pop(future)
                        ingest(future.result())

        products = list(by_handle.values())
        if limit is not None:
            products = products[:limit]
        return products

    def crawl(
        self,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        max_workers: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        **_kwargs,
    ) -> list[Product]:
        workers = max_workers or self.max_workers
        backend_products = self._crawl_via_myshopify(
            limit=limit,
            max_pages=max_pages,
            max_workers=workers,
            progress_callback=progress_callback,
        )
        if backend_products:
            return backend_products

        probe = self.session.get(f"{self.shop_url}/browse/athletes", timeout=20)
        if probe.status_code == 200 and "/browse/athletes" in probe.url:
            return self._crawl_athlete_catalog(
                limit=limit,
                progress_callback=progress_callback,
            )

        raise requests.RequestException(
            "Shopify Hydrogen: không lấy được catalog (thử bật Proxy hoặc giảm luồng)."
        )

    @classmethod
    def estimate_athlete_count(cls, shop_url: str) -> int | None:
        base = shop_url.strip().rstrip("/")
        if not base.startswith("http"):
            base = f"https://{base}"
        session = requests.Session()
        session.headers["User-Agent"] = DEFAULT_USER_AGENT
        try:
            text = fetch_page_text(
                session, f"{base}/browse/athletes", timeout=30, max_retries=2
            )
            ctx = extract_remix_context(text)
            route = cls._find_athletes_index(ctx.get("state", {}).get("loaderData", {}))
            if not route:
                return None
            athletes = route.get("athletes") or {}
            nodes = athletes.get("nodes") or []
            page_info = athletes.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return len(nodes)
            return None
        except (requests.RequestException, ValueError):
            return None
