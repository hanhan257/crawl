"""Crawl Trendsi — brand / category pages (SSR HTML) + catalog API."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape, unescape
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

from crawlers.base import BaseCrawler
from models.product import Product
from utils.http_session import create_http_session

CrawlProgressCallback = Callable[[int, int, int], None]

TRENDSI_BASE = "https://www.trendsi.com"
PRODUCT_LIST_API = f"{TRENDSI_BASE}/api/product/list"
PRODUCT_INFO_API = f"{TRENDSI_BASE}/api/product/info"
DEFAULT_PAGE_SIZE = 90
DETAIL_MAX_WORKERS = 24
BRAND_CARD_RE = re.compile(
    r'data-product-id="(?P<id>\d+)".*?'
    r'<div class="shop-brand-and-name">\s*(?P<title>.*?)\s*</div>',
    re.S,
)
BRAND_PRICE_RE = re.compile(
    r'class="cost-price">\$?(?P<price>[\d.,]+)</span>',
    re.S,
)
LISTING_ITEM_COUNT_RE = re.compile(r"\((\d+)\s+Items?\)", re.I)


class TrendsiCrawler(BaseCrawler):
    source = "trendsi"

    def __init__(self, *, proxy_url: str | None = None, request_delay: float = 0.0):
        self.request_delay = request_delay
        self._proxy_url = proxy_url
        self.session = self._build_session()
        self._thread_local = threading.local()

    def crawl(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        return self._crawl_catalog(
            limit=limit,
            max_pages=max_pages,
            progress_callback=progress_callback,
            max_workers=max_workers,
        )

    def crawl_brand(
        self,
        brand_url: str,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        parsed = urlparse(brand_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        brand_name = (query.get("nameEn") or [""])[0].strip()
        return self.crawl_listing(
            brand_url,
            vendor_override=brand_name,
            limit=limit,
            max_pages=max_pages,
            progress_callback=progress_callback,
            max_workers=max_workers,
        )

    def crawl_category(
        self,
        category_url: str,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        return self.crawl_listing(
            category_url,
            vendor_override="",
            limit=limit,
            max_pages=max_pages,
            progress_callback=progress_callback,
            max_workers=max_workers,
        )

    def _build_session(self) -> requests.Session:
        session = create_http_session(self._proxy_url)
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Origin": TRENDSI_BASE,
                "Referer": f"{TRENDSI_BASE}/",
            }
        )
        return session

    def _session_for_thread(self, referer: str | None = None) -> requests.Session:
        if not hasattr(self._thread_local, "session"):
            self._thread_local.session = self._build_session()
        session = self._thread_local.session
        if referer:
            session.headers["Referer"] = referer
        return session

    def crawl_listing(
        self,
        listing_url: str,
        *,
        vendor_override: str = "",
        limit: int | None = None,
        max_pages: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = 1,
    ) -> list[Product]:
        stubs = self._collect_listing_stubs(listing_url, limit=limit, max_pages=max_pages)
        if not stubs:
            return []
        return self._products_from_stubs(
            stubs,
            brand_name=vendor_override,
            limit=limit,
            progress_callback=progress_callback,
            max_workers=max_workers,
        )

    def crawl_product(self, product_id: str, *, brand_name: str = "") -> Product | None:
        detail = self._fetch_product_detail(
            str(product_id).strip(),
            session=self.session,
        )
        if not detail:
            return None
        return self._map_detail_product(detail, brand_name=brand_name)

    def _crawl_catalog(
        self,
        *,
        limit: int | None,
        max_pages: int | None,
        progress_callback: CrawlProgressCallback | None,
        max_workers: int,
    ) -> list[Product]:
        stubs: list[dict[str, str]] = []
        page = 1
        while True:
            if max_pages and page > max_pages:
                break
            payload = self._fetch_list_page(page)
            items = payload.get("result") or []
            if not items:
                break
            for item in items:
                product_id = str(item.get("id") or "").strip()
                if not product_id:
                    continue
                stubs.append(
                    {
                        "id": product_id,
                        "title": str(item.get("title") or "").strip(),
                        "price": str(item.get("minPriceB") or item.get("minPrice") or ""),
                    }
                )
                if limit and len(stubs) >= limit:
                    break
            if progress_callback:
                progress_callback(len(stubs), len(stubs), page)
            if limit and len(stubs) >= limit:
                break
            pages_total = int((payload.get("page") or {}).get("pages") or 0)
            if pages_total and page >= pages_total:
                break
            page += 1

        if limit:
            stubs = stubs[:limit]
        return self._products_from_stubs(
            stubs,
            brand_name="",
            limit=limit,
            progress_callback=progress_callback,
            max_workers=max_workers,
        )

    @staticmethod
    def parse_listing_item_count(html: str) -> int | None:
        match = LISTING_ITEM_COUNT_RE.search(html)
        if not match:
            return None
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None

    def _collect_listing_stubs(
        self,
        listing_url: str,
        *,
        limit: int | None,
        max_pages: int | None,
    ) -> list[dict[str, str]]:
        stubs: list[dict[str, str]] = []
        seen: set[str] = set()
        page = 1
        while True:
            if max_pages and page > max_pages:
                break
            page_url = self._listing_page_url(listing_url, page)
            self.session.headers["Referer"] = page_url
            try:
                response = self.session.get(page_url, timeout=45)
                response.raise_for_status()
            except requests.RequestException as exc:
                if stubs:
                    break
                raise requests.RequestException(
                    f"Không tải được trang Trendsi (trang {page}): {exc}"
                ) from exc
            page_stubs = self._parse_listing_page(response.text)
            if not page_stubs:
                break
            for stub in page_stubs:
                product_id = stub["id"]
                if product_id in seen:
                    continue
                seen.add(product_id)
                stubs.append(stub)
                if limit and len(stubs) >= limit:
                    return stubs
            page += 1
        return stubs

    @staticmethod
    def _listing_page_url(listing_url: str, page: int) -> str:
        parsed = urlparse(listing_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["curPage"] = [str(page)]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    @staticmethod
    def _parse_listing_page(html: str) -> list[dict[str, str]]:
        products: list[dict[str, str]] = []
        for match in BRAND_CARD_RE.finditer(html):
            block = match.group(0)
            price_match = BRAND_PRICE_RE.search(block)
            products.append(
                {
                    "id": match.group("id"),
                    "title": unescape(match.group("title").strip()),
                    "price": price_match.group("price") if price_match else "",
                }
            )
        return products

    def _fetch_list_page(self, cur_page: int, page_size: int = DEFAULT_PAGE_SIZE) -> dict[str, Any]:
        try:
            response = self.session.post(
                PRODUCT_LIST_API,
                json={"curPage": cur_page, "pageSize": page_size},
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise requests.RequestException(
                f"Trendsi API /api/product/list lỗi (trang {cur_page}): {exc}"
            ) from exc
        if not payload.get("success"):
            return {"result": [], "page": {}}
        return payload

    def _fetch_product_detail(
        self,
        product_id: str,
        *,
        session: requests.Session | None = None,
    ) -> dict[str, Any] | None:
        http = session or self.session
        try:
            response = http.post(
                PRODUCT_INFO_API,
                json={"productId": int(product_id)},
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError, TypeError):
            return None
        if not payload.get("success"):
            return None
        result = payload.get("result")
        return result if isinstance(result, dict) else None

    def _products_from_stubs(
        self,
        stubs: list[dict[str, str]],
        *,
        brand_name: str,
        limit: int | None,
        progress_callback: CrawlProgressCallback | None,
        max_workers: int,
    ) -> list[Product]:
        if limit:
            stubs = stubs[:limit]
        total = len(stubs)
        if not total:
            return []

        workers = max(1, min(int(max_workers or 1), DETAIL_MAX_WORKERS, total))
        products: list[Product | None] = [None] * total
        referer = str(self.session.headers.get("Referer") or f"{TRENDSI_BASE}/")

        def fetch_one(index: int, stub: dict[str, str]) -> None:
            try:
                session = self._session_for_thread(referer)
                detail = self._fetch_product_detail(stub["id"], session=session)
                product = (
                    self._map_detail_product(detail, brand_name=brand_name, list_stub=stub)
                    if detail
                    else None
                )
            except Exception:
                product = None
            products[index] = product

        if workers > 1 and total > 1:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(fetch_one, index, stub)
                    for index, stub in enumerate(stubs)
                ]
                completed = 0
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        pass
                    completed += 1
                    if progress_callback and (
                        completed == total or completed % 2 == 0
                    ):
                        matched = sum(1 for product in products if product is not None)
                        progress_callback(completed, total, matched)
        else:
            for index, stub in enumerate(stubs):
                fetch_one(index, stub)
                if progress_callback:
                    matched = sum(1 for product in products if product is not None)
                    progress_callback(index + 1, total, matched)

        if progress_callback and total:
            matched = sum(1 for product in products if product is not None)
            progress_callback(total, total, matched)

        return [product for product in products if product is not None]

    def _map_detail_product(
        self,
        item: dict[str, Any],
        *,
        brand_name: str = "",
        list_stub: dict[str, str] | None = None,
    ) -> Product:
        product_id = str(item.get("id") or (list_stub or {}).get("id") or "")
        title = str(item.get("title") or (list_stub or {}).get("title") or "").strip()
        description = self._build_description(item)
        vendor = brand_name.strip() or str(item.get("brand") or "").strip() or "Trendsi"
        categories = self._parse_back_cates(item.get("backCates"))
        images = self._collect_images(item)
        color_swatches = self._map_color_swatches(item, product_id, title)
        variants = self._map_variants(item) + color_swatches
        price = self._resolve_price(item, list_stub, variants)
        sku = str(item.get("spuCode") or product_id)
        status = "instock" if int(item.get("stock") or 0) > 0 else "outofstock"
        colors = [str(s.get("color") or "").strip() for s in color_swatches if s.get("color")]
        gmc_color = ", ".join(colors) if colors else None

        return Product(
            source=self.source,
            id=product_id,
            title=title,
            description=description,
            price=price,
            currency="USD",
            url=self._product_url(product_id, title),
            image_urls=images,
            vendor=vendor,
            sku=sku,
            tags=["Trendsi"],
            categories=categories,
            product_type=categories[-1] if categories else None,
            status=status,
            variants=variants,
            gmc_color=gmc_color,
        )

    @classmethod
    def _build_description(cls, item: dict[str, Any]) -> str | None:
        """Gộp Size & Fit (HTML table) + Product Details vào đầu description."""
        base = str(item.get("description") or "").strip()
        if not base:
            text = str(item.get("text") or "").strip()
            base = f"<p>{escape(text)}</p>" if text else ""
        size_html = cls._size_fit_html(item)
        parts = [part for part in (size_html, base) if part]
        return "".join(parts) or None

    @classmethod
    def _size_fit_html(cls, item: dict[str, Any]) -> str:
        if item.get("sizeInfoEnable") is False:
            return ""
        size_info = item.get("sizeInfo") or []
        if not isinstance(size_info, list) or not size_info:
            return ""

        blocks: list[str] = []
        for block in size_info:
            if not isinstance(block, dict):
                continue
            table = cls._size_info_table_html(block)
            if table:
                blocks.append(table)
        if not blocks:
            return ""

        fit_type = str(item.get("fitType") or "").strip()
        fit_note = (
            f"<p><strong>Fit:</strong> {escape(fit_type)}</p>" if fit_type else ""
        )
        heading = "<h3>Size &amp; Fit</h3>"
        return (
            f'<div class="trendsi-size-fit">{heading}{fit_note}'
            f'{"".join(blocks)}</div>'
        )

    @staticmethod
    def _size_info_table_html(block: dict[str, Any]) -> str:
        sizes = [
            part.strip()
            for part in str(block.get("sizeTemplate") or "").split(",")
            if part.strip()
        ]
        attrs = block.get("sizeAttrList") or []
        if not sizes or not isinstance(attrs, list) or not attrs:
            return ""

        columns: list[tuple[str, list[str]]] = []
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            name = str(attr.get("nameEn") or attr.get("name") or "").strip()
            values = [
                part.strip()
                for part in str(attr.get("sizeProps") or "").split(",")
            ]
            if not name or not values:
                continue
            columns.append((name, values))
        if not columns:
            return ""

        unit = str(block.get("unit") or "IN").strip().upper() or "IN"
        unit_label = "inches" if unit == "IN" else unit.lower()
        caption = (
            f"Product Measurements (Measurements by {escape(unit_label)}) "
            f"&amp; Size Conversion"
        )
        header_cells = "".join(
            f"<th>{escape(name)}</th>" for name, _ in columns
        )
        rows: list[str] = []
        for index, size in enumerate(sizes):
            cells = [f"<td>{escape(size)}</td>"]
            for _, values in columns:
                value = values[index] if index < len(values) else ""
                cells.append(f"<td>{escape(value)}</td>")
            rows.append(f"<tr>{''.join(cells)}</tr>")

        disclaimer = (
            f"<p><em>*This data was obtained from manually measuring the product, "
            f"it may be off by 0.4 ~ 1.2 {escape(unit_label)}.</em></p>"
        )
        return (
            f"<p><strong>{caption}</strong> - Unit: {escape(unit)}</p>"
            f"<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\">"
            f"<thead><tr><th>Size</th>{header_cells}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
            f"{disclaimer}"
        )

    @staticmethod
    def _product_url(product_id: str, title: str = "", *, sku_id: str | None = None) -> str:
        params: dict[str, str] = {"id": product_id, "name": title}
        if sku_id:
            params["skuId"] = str(sku_id)
        query = urlencode(params)
        return f"{TRENDSI_BASE}/products/detail?{query}"

    @classmethod
    def _map_color_swatches(
        cls,
        item: dict[str, Any],
        product_id: str,
        title: str,
    ) -> list[dict[str, Any]]:
        """Mỗi màu: tên, ảnh, giá, link có skuId (vd. ...&skuId=2586142)."""
        swatches: list[dict[str, Any]] = []
        for entry in item.get("colorList") or []:
            if not isinstance(entry, dict):
                continue
            color = str(entry.get("color") or "").strip()
            if not color:
                continue
            sku_id = str(entry.get("firstSkuId") or "").strip()
            image = cls._normalize_image_url(
                str(entry.get("image") or entry.get("iconImage") or "")
            )
            price = cls._safe_float(entry.get("dpMinSkCPrice")) or cls._safe_float(
                entry.get("dpMaxSkCPrice")
            )
            swatches.append(
                {
                    "_type": "color_swatch",
                    "color": color,
                    "sku_id": sku_id,
                    "price": price,
                    "image_url": image,
                    "url": cls._product_url(product_id, title, sku_id=sku_id or None),
                }
            )
        return swatches

    @staticmethod
    def _parse_back_cates(raw: object) -> list[str]:
        text = str(raw or "")
        if not text:
            return []
        names = re.findall(r':\s*"([^"]+)"', text)
        return [name.strip() for name in names if name.strip()]

    @staticmethod
    def _collect_images(item: dict[str, Any]) -> list[str]:
        # originImages (.jpg) và headImages (.webp) cùng nội dung — chỉ lấy 1 nguồn.
        preferred = item.get("originImages") or item.get("headImages") or []
        images: list[str] = []
        seen_keys: set[str] = set()

        def add(url: object) -> None:
            normalized = TrendsiCrawler._normalize_image_url(str(url or ""))
            if not normalized:
                return
            key = TrendsiCrawler._image_dedupe_key(normalized)
            if key in seen_keys:
                return
            seen_keys.add(key)
            images.append(normalized)

        for url in preferred:
            add(url)
        add(item.get("mainImage"))
        return images

    @staticmethod
    def _image_dedupe_key(url: str) -> str:
        """Khử trùng .jpg/.webp cùng path (originImages vs headImages)."""
        cleaned = url.strip().split("?", 1)[0].lower()
        for suffix in (".webp", ".jpeg", ".jpg", ".png", ".gif"):
            if cleaned.endswith(suffix):
                return cleaned[: -len(suffix)]
        return cleaned

    @staticmethod
    def _normalize_image_url(url: str) -> str:
        cleaned = url.strip()
        if not cleaned or cleaned.startswith("https://shobby-s"):
            return ""
        return cleaned.replace("-Min.", "-Max.").replace("-Min-", "-Max-")

    @staticmethod
    def _map_variants(item: dict[str, Any]) -> list[dict[str, Any]]:
        dimensions = item.get("dimensions") or []
        attr_defs: list[dict[str, Any]] = []
        option_names: list[str] = []
        for dim in dimensions[:3]:
            name = str(dim.get("name") or "").strip()
            values = [str(value).strip() for value in (dim.get("values") or []) if str(value).strip()]
            if not name or not values:
                continue
            option_names.append(name)
            attr_defs.append(
                {
                    "name": name,
                    "value": ", ".join(values),
                    "options": values,
                }
            )

        priced: list[dict[str, Any]] = []
        for sku in item.get("skuList") or []:
            if not isinstance(sku, dict):
                continue
            dim_value = sku.get("dimensionValue") or {}
            option_map: dict[str, str] = {}
            if option_names:
                for index, name in enumerate(option_names[:3], start=1):
                    option_map[f"option{index}"] = str(dim_value.get(name) or "").strip()
            else:
                for index, key in enumerate(list(dim_value.keys())[:3], start=1):
                    option_map[f"option{index}"] = str(dim_value.get(key) or "").strip()
            labels = [value for value in option_map.values() if value]
            price = TrendsiCrawler._safe_float(sku.get("priceB")) or TrendsiCrawler._safe_float(
                sku.get("price")
            )
            stock = int(sku.get("dropShipStock") or sku.get("stock") or 0)
            priced.append(
                {
                    "id": str(sku.get("id") or ""),
                    "sku": str(sku.get("skuCode") or sku.get("id") or ""),
                    "title": " / ".join(labels),
                    "price": price,
                    "regular_price": TrendsiCrawler._safe_float(sku.get("price")),
                    "available": stock > 0,
                    "image_url": TrendsiCrawler._normalize_image_url(str(sku.get("image") or "")),
                    **option_map,
                    "_shopify_option_names": option_names,
                }
            )
        return attr_defs + priced

    @staticmethod
    def _resolve_price(
        item: dict[str, Any],
        list_stub: dict[str, str] | None,
        variants: list[dict[str, Any]],
    ) -> float | None:
        sku_prices: list[float] = []
        for sku in item.get("skuList") or []:
            if not isinstance(sku, dict):
                continue
            value = TrendsiCrawler._safe_float(sku.get("priceB")) or TrendsiCrawler._safe_float(
                sku.get("price")
            )
            if value is not None:
                sku_prices.append(value)
        if sku_prices:
            return min(sku_prices)

        for key in ("minPriceB", "priceB", "minPrice", "priceC"):
            value = TrendsiCrawler._safe_float(item.get(key))
            if value is not None:
                return value
        if list_stub:
            value = TrendsiCrawler._safe_float(list_stub.get("price"))
            if value is not None:
                return value
        for variant in variants:
            if variant.get("price") not in (None, ""):
                value = TrendsiCrawler._safe_float(variant.get("price"))
                if value is not None:
                    return value
        return None

    @staticmethod
    def _safe_float(value: object) -> float | None:
        if value in (None, ""):
            return None
        try:
            return round(float(str(value).replace(",", "")), 2)
        except (TypeError, ValueError):
            return None
