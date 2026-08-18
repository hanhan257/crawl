"""Crawl nhanh nebgift.com — title, ảnh, giá, link. 500 luồng + xoay proxy."""
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

from crawlers.shopbase import (  # noqa: E402
    CATALOG_LIST_API,
    CATALOG_PAGE_SIZE,
    ShopBaseCrawler,
)
from utils.http_session import (  # noqa: E402
    DEFAULT_USER_AGENT,
    create_http_session,
    parse_proxy_list,
)

SHOP = "https://nebgift.com"
OUT = ROOT / "output" / "nebgift_products.csv"
WORKERS = 500


def fetch_page(page: int, proxy: str | None) -> tuple[int, list[dict], int]:
    """Trả (page, items, reported_count)."""
    for attempt in range(4):
        try:
            sess = create_http_session(proxy)
            sess.headers["User-Agent"] = DEFAULT_USER_AGENT
            resp = sess.get(
                f"{SHOP}{CATALOG_LIST_API}",
                params={"limit": CATALOG_PAGE_SIZE, "page": page},
                timeout=40,
            )
            if resp.status_code in (429, 503):
                time.sleep(1.0 * (attempt + 1))
                continue
            if resp.status_code != 200:
                time.sleep(0.4 * (attempt + 1))
                continue
            result = resp.json().get("result") or {}
            items = [i for i in (result.get("items") or []) if isinstance(i, dict)]
            try:
                count = int(result.get("count") or 0)
            except (TypeError, ValueError):
                count = len(items)
            return page, items, count
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return page, [], 0


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    proxy = ""
    if isinstance(cfg.get("proxy"), dict):
        proxy = str(cfg["proxy"].get("url") or "")
    else:
        proxy = str(cfg.get("proxy") or "")
    proxies = parse_proxy_list(proxy)
    print(f"[start] {SHOP} workers={WORKERS} proxies={len(proxies)}", flush=True)
    t0 = time.time()

    _, first_items, reported = fetch_page(1, proxies[0] if proxies else None)
    if not first_items:
        print("[done] không lấy được trang 1", flush=True)
        return

    total = reported or len(first_items)
    total_pages = max(1, (total + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)
    # ShopBase hay cap 10000 → 200 trang
    total_pages = min(total_pages, 400)
    print(
        f"[page1] items={len(first_items)} count={reported} pages={total_pages}",
        flush=True,
    )

    page_items: dict[int, list[dict]] = {1: first_items}

    def one(page: int) -> tuple[int, list[dict]]:
        pxy = proxies[page % len(proxies)] if proxies else None
        p, items, _ = fetch_page(page, pxy)
        return p, items

    pages = list(range(2, total_pages + 1))
    with ThreadPoolExecutor(max_workers=min(WORKERS, max(1, len(pages)))) as ex:
        futs = [ex.submit(one, p) for p in pages]
        done = 1
        for fut in as_completed(futs):
            page, items = fut.result()
            page_items[page] = items
            done += 1
            if done % 20 == 0 or done == total_pages:
                loaded = sum(len(v) for v in page_items.values())
                print(
                    f"[list] {done}/{total_pages} loaded={loaded}",
                    flush=True,
                )

    # Retry holes
    holes = [p for p in range(1, total_pages + 1) if not page_items.get(p)]
    if holes:
        print(f"[retry] holes={len(holes)}", flush=True)
        with ThreadPoolExecutor(max_workers=min(64, len(holes))) as ex:
            futs = []
            for page in holes:
                pxy = proxies[page % len(proxies)] if proxies else None
                futs.append(ex.submit(fetch_page, page, pxy))
            for fut in as_completed(futs):
                page, items, _ = fut.result()
                if items:
                    page_items[page] = items

    crawler = ShopBaseCrawler(SHOP, proxy_url=proxy or None)
    by_handle: dict[str, object] = {}
    for page in sorted(page_items):
        for item in page_items[page] or []:
            product = crawler._map_catalog_item(item, skip_page_fallback=True)
            if not product or not product.url:
                continue
            handle = product.url.rstrip("/").rsplit("/", 1)[-1]
            by_handle[handle] = product

    products = list(by_handle.values())
    print(f"[map] unique={len(products)} in {time.time()-t0:.1f}s", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "price", "link", "images"]
        )
        writer.writeheader()
        for p in products:
            writer.writerow(
                {
                    "title": html.unescape(p.title or ""),
                    "price": f"{p.price:.2f}" if p.price is not None else "",
                    "link": p.url or "",
                    "images": ",".join(p.image_urls or []),
                }
            )
    print(f"[done] {len(products):,} rows -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
