"""Crawl wearandhome.com (ShopBase catalog API) -> CSV."""
from __future__ import annotations

import csv
import html
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.shopbase import ShopBaseCrawler  # noqa: E402

SHOP = "https://wearandhome.com"
OUT = ROOT / "output" / "wearandhome_products.csv"
WORKERS = 64


def main() -> None:
    print(f"[start] {SHOP} workers={WORKERS}", flush=True)
    t0 = time.time()

    crawler = ShopBaseCrawler(site_url=SHOP, proxy_url=None)

    def progress(current: int, total: int, matched: int) -> None:
        if current == 1 or current == total or current % 20 == 0:
            print(f"[list] page {current}/{total} items={matched}", flush=True)

    items = crawler._list_catalog_items(
        max_pages=None,
        limit=None,
        max_workers=WORKERS,
        progress_callback=progress,
    )
    print(f"[list] raw_items={len(items)} in {time.time() - t0:.1f}s", flush=True)

    by_handle: dict[str, object] = {}
    for item in items:
        product = crawler._map_catalog_item(item, skip_page_fallback=True)
        if not product or not product.url:
            continue
        handle = product.url.rstrip("/").rsplit("/", 1)[-1]
        by_handle[handle] = product

    products = list(by_handle.values())
    print(f"[map] unique={len(products)}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "price", "link", "images", "cate"]
        )
        writer.writeheader()
        for product in products:
            cats = [html.unescape(c) for c in (product.categories or []) if c]
            if not cats and product.product_type:
                cats = [html.unescape(str(product.product_type))]
            writer.writerow(
                {
                    "title": html.unescape(product.title or ""),
                    "price": f"{product.price:.2f}" if product.price is not None else "",
                    "link": product.url or "",
                    "images": " | ".join(product.image_urls or []),
                    "cate": ", ".join(cats),
                }
            )

    print(
        f"[done] rows={len(products):,} elapsed={time.time() - t0:.1f}s file={OUT}",
        flush=True,
    )


if __name__ == "__main__":
    main()
