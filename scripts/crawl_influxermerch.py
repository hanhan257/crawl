"""Crawl influxermerch.com (Shopify Hydrogen) -> CSV."""
from __future__ import annotations

import csv
import html
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.shopify_hydrogen import ShopifyHydrogenCrawler  # noqa: E402

SHOP = "https://www.influxermerch.com"
OUT = ROOT / "output" / "influxermerch_products.csv"


def main() -> None:
    print(f"[start] {SHOP}", flush=True)
    t0 = time.time()

    crawler = ShopifyHydrogenCrawler(shop_url=SHOP, max_workers=32)

    def progress(current: int, total: int, matched: int) -> None:
        if current == 1 or current == total or current % 250 == 0:
            print(f"[crawl] {current}/{total} products={matched}", flush=True)

    products = crawler.crawl(max_workers=32, progress_callback=progress)
    print(f"[map] products={len(products)} in {time.time() - t0:.1f}s", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "price", "link", "images", "cate"]
        )
        writer.writeheader()
        for product in products:
            cats = [html.unescape(c) for c in (product.categories or []) if c]
            writer.writerow(
                {
                    "title": html.unescape(product.title or ""),
                    "price": f"{product.price:.2f}" if product.price is not None else "",
                    "link": product.url or "",
                    "images": " | ".join(product.image_urls or []),
                    "cate": ", ".join(cats),
                }
            )

    elapsed = time.time() - t0
    print(f"[done] rows={len(products):,} elapsed={elapsed:.1f}s file={OUT}", flush=True)


if __name__ == "__main__":
    main()
