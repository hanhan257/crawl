"""Crawl shop Laravel dạng campaign (vd. featherslife.com).

Nhận diện bằng GET /api/home có `numberOfCampaignInCategory`.
Danh mục lá: /api/shop-category/{slug}?page=N
Sản phẩm: /campaign/{slug} và /api/campaign-detail/{slug}
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from typing import Any
from urllib.parse import quote, unquote, urlparse

import requests

from crawlers.base import BaseCrawler
from models.product import Product
from utils.http_session import create_http_session

CrawlProgressCallback = Callable[[int, int, int], None]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
API_HEADERS = {
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}
MAX_LIST_WORKERS = 8
IMAGE_CAP = 8

_ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
_CLASS_RE = re.compile(r"""class=["']([^"']+)["']""", re.I)
_CATEGORY_HREF_RE = re.compile(r"/category/([^/?#'\"]+)", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def parse_category_menu(html: str) -> list[dict[str, str]]:
    """Danh mục lá trên menu. Mục cha (dropdown) bỏ qua vì chỉ gộp các mục con."""
    items: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for match in _ANCHOR_RE.finditer(html or ""):
        attrs = match.group(1)
        href_match = _HREF_RE.search(attrs)
        if not href_match:
            continue
        slug_match = _CATEGORY_HREF_RE.search(href_match.group(1))
        if not slug_match:
            continue
        slug = unquote(slug_match.group(1)).strip().strip("/")
        if not slug:
            continue
        class_match = _CLASS_RE.search(attrs)
        classes = (class_match.group(1) if class_match else "").lower()
        name = _TAG_RE.sub(" ", match.group(2))
        name = _SPACE_RE.sub(" ", unescape(name)).strip() or slug.replace("-", " ").title()
        is_parent = "dropdown-toggle" in classes
        if slug not in items:
            items[slug] = {"slug": slug, "name": name, "is_parent": is_parent}
            order.append(slug)
            continue
        if is_parent:
            items[slug]["is_parent"] = True
        if items[slug]["name"] == slug and name:
            items[slug]["name"] = name

    if not items:
        for slug in dict.fromkeys(_CATEGORY_HREF_RE.findall(html or "")):
            cleaned = unquote(slug).strip().strip("/")
            if not cleaned or cleaned in items:
                continue
            items[cleaned] = {
                "slug": cleaned,
                "name": cleaned.replace("-", " ").title(),
                "is_parent": False,
            }
            order.append(cleaned)

    leaves = [
        {"slug": items[slug]["slug"], "name": items[slug]["name"]}
        for slug in order
        if not items[slug]["is_parent"]
    ]
    if leaves:
        return leaves
    return [{"slug": items[slug]["slug"], "name": items[slug]["name"]} for slug in order]


def _score_product(category_title: str, product_title: str) -> int:
    category = re.sub(r"[^a-z0-9]+", " ", (category_title or "").lower()).strip()
    product = re.sub(r"[^a-z0-9]+", " ", (product_title or "").lower()).strip()
    if not product:
        return 0
    score = 0
    for word in category.split():
        if len(word) >= 3 and word in product:
            score += 10 + len(word)
    if product == category:
        score += 40
    return score


def _price_value(item: dict[str, Any]) -> float | None:
    raw = item.get("price")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _match_score(campaign: dict[str, Any], category_title: str) -> int:
    scores = []
    for item in _enabled_products(campaign):
        product = item.get("product") if isinstance(item.get("product"), dict) else {}
        scores.append(_score_product(category_title, str(product.get("title") or "")))
    return max(scores) if scores else 0


def _enabled_products(campaign: dict[str, Any]) -> list[dict[str, Any]]:
    items = [
        item
        for item in (campaign.get("campaign_products") or [])
        if isinstance(item, dict)
    ]
    enabled = [item for item in items if item.get("enable") in (1, True, "1")]
    return enabled or items


def pick_price(campaign: dict[str, Any], category_title: str) -> float | None:
    pool = _enabled_products(campaign)
    if not pool:
        return None
    best = None
    best_score = 0
    for item in pool:
        product = item.get("product") if isinstance(item.get("product"), dict) else {}
        score = _score_product(category_title, str(product.get("title") or ""))
        if score > best_score:
            best_score = score
            best = item
    if best is None:
        priced = [item for item in pool if _price_value(item) is not None]
        if not priced:
            return None
        best = min(priced, key=lambda item: _price_value(item) or 0)
    return _price_value(best)


def collect_images(campaign: dict[str, Any], *, limit: int = IMAGE_CAP) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for item in campaign.get("campaign_products") or []:
        if not isinstance(item, dict):
            continue
        raw = item.get("mockups")
        mockups: Any = []
        if isinstance(raw, str) and raw.strip():
            try:
                mockups = json.loads(raw)
            except json.JSONDecodeError:
                mockups = []
        elif isinstance(raw, list):
            mockups = raw
        elif isinstance(raw, dict):
            mockups = [raw]
        if isinstance(mockups, dict):
            mockups = [mockups]
        if not isinstance(mockups, list):
            continue
        for mock in mockups:
            if not isinstance(mock, dict):
                continue
            for key in ("front", "back"):
                url = str(mock.get(key) or "").strip()
                if not url.startswith("http") or url in seen:
                    continue
                seen.add(url)
                urls.append(url)
                if len(urls) >= limit:
                    return urls
    return urls


def category_names_from_campaign(campaign: dict[str, Any]) -> list[str]:
    names: list[str] = []
    raw_list = campaign.get("categories_list")
    if isinstance(raw_list, list):
        for item in raw_list:
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
            elif isinstance(item, dict):
                value = item.get("title") or item.get("value") or item.get("name")
                if value:
                    names.append(str(value).strip())
    if names:
        return names
    raw = campaign.get("categories")
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("value"):
                names.append(str(item["value"]).strip())
            elif isinstance(item, str) and item.strip():
                names.append(item.strip())
    return names


class CampaignShopCrawler(BaseCrawler):
    source = "campaign"

    def __init__(
        self,
        site_url: str,
        *,
        proxy_url: str | None = None,
        request_delay: float = 0.0,
    ):
        self.site_url = self._normalize_site_url(site_url)
        self.proxy_url = proxy_url
        self.request_delay = request_delay
        self._local = threading.local()

    @staticmethod
    def _normalize_site_url(site_url: str) -> str:
        url = (site_url or "").strip().rstrip("/")
        if not url.startswith("http"):
            url = f"https://{url}"
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @classmethod
    def site_supports(
        cls,
        site_url: str,
        *,
        session: requests.Session | None = None,
    ) -> bool:
        base = cls._normalize_site_url(site_url)
        http = session or requests.Session()
        if not http.headers.get("User-Agent"):
            http.headers["User-Agent"] = DEFAULT_USER_AGENT
        try:
            response = http.get(
                f"{base}/api/home",
                headers=API_HEADERS,
                timeout=8,
            )
        except requests.RequestException:
            return False
        if response.status_code != 200:
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        return isinstance(payload, dict) and "numberOfCampaignInCategory" in payload

    def _thread_session(self, *, proxy: bool) -> requests.Session:
        attr = "proxy_session" if proxy else "session"
        current = getattr(self._local, attr, None)
        if current is not None:
            return current
        created = create_http_session(self.proxy_url if proxy else None)
        created.headers["User-Agent"] = DEFAULT_USER_AGENT
        setattr(self._local, attr, created)
        return created

    def _api_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.site_url}{path}"
        sessions = [self._thread_session(proxy=False)]
        if self.proxy_url:
            sessions.append(self._thread_session(proxy=True))
        last_error: Exception | None = None
        for http in sessions:
            for attempt in range(2):
                try:
                    response = http.get(
                        url,
                        params=params or None,
                        headers=API_HEADERS,
                        timeout=45,
                    )
                except requests.RequestException as exc:
                    last_error = exc
                    break
                if response.status_code in {429, 503} and attempt == 0:
                    continue
                if response.status_code != 200:
                    last_error = requests.HTTPError(
                        f"HTTP {response.status_code} {path}"
                    )
                    break
                try:
                    payload = response.json()
                except ValueError as exc:
                    last_error = exc
                    break
                if isinstance(payload, dict):
                    return payload
                last_error = ValueError(f"API {path} không trả object JSON.")
                break
        if last_error:
            raise last_error
        raise requests.HTTPError(path)

    def _html(self, path: str) -> str:
        url = f"{self.site_url}{path}"
        sessions = [self._thread_session(proxy=False)]
        if self.proxy_url:
            sessions.append(self._thread_session(proxy=True))
        last_error: Exception | None = None
        for http in sessions:
            try:
                response = http.get(url, timeout=30)
            except requests.RequestException as exc:
                last_error = exc
                continue
            if response.status_code == 200 and response.text:
                return response.text
            last_error = requests.HTTPError(f"HTTP {response.status_code} {path}")
        if last_error:
            raise last_error
        raise requests.HTTPError(path)

    def menu_leaves(self) -> list[dict[str, str]]:
        return parse_category_menu(self._html("/"))

    def _category_page(self, slug: str, page: int) -> dict[str, Any] | None:
        encoded = quote(slug.strip().strip("/"), safe="")
        try:
            payload = self._api_get(f"/api/shop-category/{encoded}", {"page": page})
        except (requests.RequestException, ValueError):
            return None
        campaigns = payload.get("campaigns")
        if not isinstance(campaigns, dict) or not isinstance(campaigns.get("data"), list):
            return None
        return payload

    @staticmethod
    def _category_title(payload: dict[str, Any], fallback: str) -> str:
        category = payload.get("category") if isinstance(payload.get("category"), dict) else {}
        title = str(category.get("title") or "").strip()
        return title or fallback

    def list_categories(self, *, max_workers: int = MAX_LIST_WORKERS) -> list[dict[str, Any]]:
        leaves = self.menu_leaves()
        if not leaves:
            return []
        workers = max(1, min(int(max_workers or MAX_LIST_WORKERS), MAX_LIST_WORKERS))

        def load(item: dict[str, str]) -> dict[str, Any]:
            payload = self._category_page(item["slug"], 1)
            if not payload:
                return {
                    "slug": item["slug"],
                    "name": item["name"],
                    "count": None,
                }
            campaigns = payload.get("campaigns") or {}
            try:
                total = int(campaigns.get("total") or 0)
            except (TypeError, ValueError):
                total = None
            return {
                "slug": item["slug"],
                "name": self._category_title(payload, item["name"]),
                "count": total,
            }

        rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(load, item) for item in leaves]
            for future in futures:
                rows.append(future.result())
        return rows

    def category_info(self, slug: str) -> dict[str, Any] | None:
        cleaned = (slug or "").strip().strip("/")
        if not cleaned:
            return None
        payload = self._category_page(cleaned, 1)
        if not payload:
            return None
        campaigns = payload.get("campaigns") or {}
        try:
            total = int(campaigns.get("total") or 0)
        except (TypeError, ValueError):
            total = None
        return {
            "slug": cleaned,
            "name": self._category_title(payload, cleaned.replace("-", " ").title()),
            "count": total,
        }

    def crawl(
        self,
        limit: int | None = None,
        max_pages: int | None = None,
        category_slug: str | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = MAX_LIST_WORKERS,
    ) -> list[Product]:
        slug = (category_slug or "").strip().strip("/")
        if slug and slug.lower() not in {"tất cả", "tat ca", "all"}:
            return self.crawl_category(
                slug,
                limit=limit,
                max_pages=max_pages,
                progress_callback=progress_callback,
                max_workers=max_workers,
            )
        leaves = self.menu_leaves()
        if not leaves:
            raise ValueError("Không đọc được danh mục trên trang chủ.")
        return self._crawl_leaves(
            leaves,
            limit=limit,
            max_pages=max_pages,
            progress_callback=progress_callback,
            max_workers=max_workers,
        )

    def crawl_category(
        self,
        handle: str,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
        progress_callback: CrawlProgressCallback | None = None,
        max_workers: int = MAX_LIST_WORKERS,
    ) -> list[Product]:
        slug = (handle or "").strip().strip("/")
        if not slug:
            return []
        first = self._category_page(slug, 1)
        if not first:
            raise ValueError(f"Không tải được danh mục {slug}.")
        name = self._category_title(first, slug.replace("-", " ").title())
        campaigns = first.get("campaigns") or {}
        try:
            total = int(campaigns.get("total") or 0)
            last_page = int(campaigns.get("last_page") or 1)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Danh mục {name} không có phân trang hợp lệ.") from exc
        if total <= 0:
            raise ValueError(f"Danh mục {name} không có sản phẩm.")
        if max_pages is not None and max_pages > 0:
            last_page = min(last_page, max_pages)
        bucket: dict[str, Product] = {}
        self._absorb(bucket, first, name)
        if progress_callback:
            progress_callback(1, last_page, len(bucket))
        if limit is not None and len(bucket) >= limit:
            return self._limited(bucket, limit)
        jobs = [(slug, name, page) for page in range(2, last_page + 1)]
        self._run_jobs(
            jobs,
            bucket,
            progress_callback,
            done=1,
            total_pages=last_page,
            max_workers=max_workers,
            limit=limit,
        )
        return self._limited(bucket, limit)

    def crawl_product(self, slug: str) -> Product | None:
        cleaned = (slug or "").strip().strip("/")
        if not cleaned:
            return None
        encoded = quote(cleaned, safe="")
        payload = self._api_get(f"/api/campaign-detail/{encoded}")
        campaign = payload.get("campaignDetail") or payload.get("campaign")
        if not isinstance(campaign, dict):
            return None
        names = category_names_from_campaign(campaign)
        category_name = names[0] if names else ""
        product = self._to_product(campaign, category_name)
        if product is None:
            return None
        if names:
            product.categories = names
        return product

    def _crawl_leaves(
        self,
        leaves: list[dict[str, str]],
        *,
        limit: int | None,
        max_pages: int | None,
        progress_callback: CrawlProgressCallback | None,
        max_workers: int,
    ) -> list[Product]:
        workers = max(1, min(int(max_workers or MAX_LIST_WORKERS), MAX_LIST_WORKERS))
        loaded: list[tuple[str, str, dict[str, Any], int]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._category_page, item["slug"], 1): item for item in leaves
            }
            for future in as_completed(futures):
                item = futures[future]
                payload = future.result()
                if not payload:
                    continue
                campaigns = payload.get("campaigns") or {}
                try:
                    total = int(campaigns.get("total") or 0)
                    last_page = int(campaigns.get("last_page") or 1)
                except (TypeError, ValueError):
                    continue
                if total <= 0:
                    continue
                if max_pages is not None and max_pages > 0:
                    last_page = min(last_page, max_pages)
                name = self._category_title(payload, item["name"])
                loaded.append((item["slug"], name, payload, last_page))

        if not loaded:
            raise ValueError("Không tải được danh mục nào của shop.")

        bucket: dict[str, Product] = {}
        jobs: list[tuple[str, str, int]] = []
        for slug, name, payload, last_page in loaded:
            self._absorb(bucket, payload, name)
            jobs.extend((slug, name, page) for page in range(2, last_page + 1))
        page_one_count = len(loaded)
        total_pages = page_one_count + len(jobs)
        if progress_callback:
            progress_callback(page_one_count, total_pages, len(bucket))
        if limit is not None and len(bucket) >= limit:
            return self._limited(bucket, limit)
        self._run_jobs(
            jobs,
            bucket,
            progress_callback,
            done=page_one_count,
            total_pages=total_pages,
            max_workers=workers,
            limit=limit,
        )
        return self._limited(bucket, limit)

    def _run_jobs(
        self,
        jobs: list[tuple[str, str, int]],
        bucket: dict[str, Product],
        progress_callback: CrawlProgressCallback | None,
        *,
        done: int,
        total_pages: int,
        max_workers: int,
        limit: int | None,
    ) -> None:
        if not jobs:
            return
        workers = max(1, min(int(max_workers or MAX_LIST_WORKERS), MAX_LIST_WORKERS))
        state = {"done": done, "stop": False}
        wave_size = max(workers, 1)

        def fetch(job: tuple[str, str, int]) -> tuple[str, dict[str, Any] | None]:
            slug, name, page = job
            return name, self._category_page(slug, page)

        for offset in range(0, len(jobs), wave_size):
            if state["stop"]:
                break
            wave = jobs[offset : offset + wave_size]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(fetch, job) for job in wave]
                for future in as_completed(futures):
                    name, payload = future.result()
                    if payload and not state["stop"]:
                        self._absorb(bucket, payload, name)
                    state["done"] += 1
                    if limit is not None and len(bucket) >= limit:
                        state["stop"] = True
                    if progress_callback:
                        progress_callback(state["done"], total_pages, len(bucket))

    def _absorb(self, bucket: dict[str, Product], payload: dict[str, Any], category_name: str) -> None:
        campaigns = payload.get("campaigns") or {}
        for entry in campaigns.get("data") or []:
            if not isinstance(entry, dict):
                continue
            campaign = entry.get("campaign") if isinstance(entry.get("campaign"), dict) else entry
            if not isinstance(campaign, dict):
                continue
            if not campaign.get("campaign_products"):
                continue
            product_id = str(campaign.get("id") or campaign.get("slug") or "").strip()
            if not product_id:
                continue
            existing = bucket.get(product_id)
            if existing is not None:
                if category_name and category_name not in existing.categories:
                    existing.categories.append(category_name)
                new_score = _match_score(campaign, category_name)
                old_score = int(getattr(existing, "_match_score", 0) or 0)
                if existing.price is None or new_score > old_score:
                    price = pick_price(campaign, category_name)
                    if price is not None:
                        existing.price = price
                        existing._match_score = new_score
                if not existing.image_urls:
                    existing.image_urls = collect_images(campaign)
                continue
            product = self._to_product(campaign, category_name)
            if product is not None:
                bucket[product_id] = product

    def _to_product(self, campaign: dict[str, Any], category_name: str) -> Product | None:
        title = _SPACE_RE.sub(" ", str(campaign.get("title") or "")).strip()
        slug = str(campaign.get("slug") or "").strip()
        if not title or not slug:
            return None
        description = campaign.get("description")
        if description is not None:
            description = str(description).strip() or None
        product = Product(
            source=self.source,
            id=str(campaign.get("id") or slug),
            title=title,
            description=description,
            price=pick_price(campaign, category_name),
            currency="USD",
            url=f"{self.site_url}/campaign/{slug}",
            image_urls=collect_images(campaign),
            categories=[category_name] if category_name else [],
        )
        product._match_score = _match_score(campaign, category_name)
        return product

    @staticmethod
    def _limited(bucket: dict[str, Product], limit: int | None) -> list[Product]:
        products = sorted(bucket.values(), key=lambda product: (product.title.lower(), product.url or ""))
        if limit is not None and limit > 0:
            return products[:limit]
        return products
