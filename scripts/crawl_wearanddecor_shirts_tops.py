"""Crawl collection shirts-tops wearanddecor — API count ao, paginate den khi het."""
from __future__ import annotations

import csv
import html
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.shopbase import CATALOG_LIST_API, CATALOG_PAGE_SIZE, ShopBaseCrawler  # noqa: E402
from utils.http_session import DEFAULT_USER_AGENT, create_http_session, parse_proxy_list  # noqa: E402

SHOP = "https://wearanddecor.com"
COLLECTION = "shirts-tops"
OUT = ROOT / "output" / "wearanddecor_shirts_tops.csv"
WORKERS = 24
MAX_PAGE_CAP = 800  # an toan; API bao 5882 nhung thuong timeout


def fetch_page(session, page: int) -> list[dict]:
    for attempt in range(4):
        try:
            resp = session.get(
                f"{SHOP}{CATALOG_LIST_API}",
                params={
                    "limit": CATALOG_PAGE_SIZE,
                    "page": page,
                    "collection_handle": COLLECTION,
                },
                timeout=35,
            )
            if resp.status_code in (429, 503):
                time.sleep(1.2 * (attempt + 1))
                continue
            if resp.status_code != 200:
                time.sleep(0.5 * (attempt + 1))
                continue
            result = resp.json().get("result") or {}
            items = [i for i in (result.get("items") or []) if isinstance(i, dict)]
            return items
        except Exception:
            time.sleep(0.6 * (attempt + 1))
    return []


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    proxy = ""
    if isinstance(cfg.get("proxy"), dict):
        proxy = str(cfg["proxy"].get("url") or "")
    else:
        proxy = str(cfg.get("proxy") or "")
    proxies = parse_proxy_list(proxy)

    crawler = ShopBaseCrawler(SHOP, proxy_url=proxy or None)
    seed = create_http_session(proxies[0] if proxies else None)
    seed.headers["User-Agent"] = DEFAULT_USER_AGENT

    first = fetch_page(seed, 1)
    print(f"[start] collection={COLLECTION} first={len(first)}", flush=True)
    if not first:
        print("[done] khong lay duoc trang 1", flush=True)
        return

    page_items: dict[int, list[dict]] = {1: first}
    # Probe theo song: tang dan den khi gap nhieu trang trong
    next_page = 2
    empty_streak = 0
    t0 = time.time()

    while next_page <= MAX_PAGE_CAP and empty_streak < 8:
        wave = list(range(next_page, min(next_page + WORKERS, MAX_PAGE_CAP + 1)))
        if not wave:
            break

        def one(page: int) -> tuple[int, list[dict]]:
            sess = create_http_session(proxies[page % len(proxies)] if proxies else None)
            sess.headers["User-Agent"] = DEFAULT_USER_AGENT
            return page, fetch_page(sess, page)

        with ThreadPoolExecutor(max_workers=len(wave)) as ex:
            futs = [ex.submit(one, p) for p in wave]
            for fut in as_completed(futs):
                page, items = fut.result()
                page_items[page] = items

        # dem empty trong wave theo thu tu
        for page in wave:
            items = page_items.get(page) or []
            if not items:
                empty_streak += 1
            else:
                empty_streak = 0
        loaded = sum(len(v) for v in page_items.values())
        print(
            f"[wave] pages {wave[0]}-{wave[-1]} loaded={loaded} empty_streak={empty_streak}",
            flush=True,
        )
        next_page = wave[-1] + 1
        if empty_streak >= 8:
            break

    # Retry holes truoc trang cuoi co data
    max_filled = max((p for p, items in page_items.items() if items), default=1)
    holes = [p for p in range(1, max_filled + 1) if not page_items.get(p)]
    if holes:
        print(f"[retry] holes={len(holes)} up_to={max_filled}", flush=True)
        with ThreadPoolExecutor(max_workers=min(16, len(holes))) as ex:
            futs = []
            for page in holes:
                def one(page: int = page) -> tuple[int, list[dict]]:
                    sess = create_http_session(
                        proxies[page % len(proxies)] if proxies else None
                    )
                    sess.headers["User-Agent"] = DEFAULT_USER_AGENT
                    return page, fetch_page(sess, page)

                futs.append(ex.submit(one))
            for fut in as_completed(futs):
                page, items = fut.result()
                if items:
                    page_items[page] = items

    by_handle: dict[str, object] = {}
    for page in sorted(page_items):
        for item in page_items[page] or []:
            product = crawler._map_catalog_item(
                item,
                default_category="Shirts & Tops",
                skip_page_fallback=True,
            )
            if not product or not product.url:
                continue
            handle = product.url.rstrip("/").rsplit("/", 1)[-1]
            by_handle[handle] = product

    products = list(by_handle.values())
    print(f"[map] unique={len(products)} in {time.time()-t0:.1f}s", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "price", "cate", "link", "images"]
        )
        writer.writeheader()
        for p in products:
            cats = [html.unescape(c) for c in (p.categories or []) if c]
            if not cats and p.product_type:
                cats = [html.unescape(str(p.product_type))]
            writer.writerow(
                {
                    "title": html.unescape(p.title or ""),
                    "price": f"{p.price:.2f}" if p.price is not None else "",
                    "cate": ", ".join(cats),
                    "link": p.url or "",
                    "images": ",".join(p.image_urls or []),
                }
            )
    print(f"[done] {len(products):,} rows -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
